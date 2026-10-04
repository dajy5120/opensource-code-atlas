"""`osca` command-line interface."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import __version__, gitutil, status as status_mod, sync as sync_mod, terms as terms_mod
from . import impact as impact_mod, index as index_mod, resolve as resolve_mod, state as state_mod
from . import atlas as atlas_mod, docs as docs_mod, onboard as onboard_mod, translate as tr_mod
from .markers import comment_tokens, strip_text
from .project import (
    SYNC_FILE,
    ConfigError,
    Project,
    anchor_commit,
    find_project_root,
    load_project,
    write_sync,
)
from .verify import CLAUDE_IMPORT, CLAUDE_MD, verify as run_verify

app = typer.Typer(
    help="OpenSource Code Atlas: upstream tracking and 【zh】 annotation tooling.",
    no_args_is_help=True,
    add_completion=False,
)
terms_app = typer.Typer(help="Terminology utilities.", no_args_is_help=True)
workflows_app = typer.Typer(help="GitHub Actions housekeeping for study repos.", no_args_is_help=True)
review_app = typer.Typer(help="Human review of annotations.", no_args_is_help=True)
docs_app = typer.Typer(help="Analysis documents anchored to source symbols.", no_args_is_help=True)
atlas_app = typer.Typer(help="Index-repository commands (run inside opensource-code-atlas).", no_args_is_help=True)
app.add_typer(atlas_app, name="atlas")
app.add_typer(docs_app, name="docs")
app.add_typer(review_app, name="review")
app.add_typer(terms_app, name="terms")
app.add_typer(workflows_app, name="workflows")


def die(msg: str, code: int = 1) -> None:
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _project(cwd: Path | None = None) -> Project:
    root = find_project_root((cwd or Path.cwd()).resolve())
    if root is None:
        die("not inside an OSCA study repository (.osca/project.yaml not found)")
    try:
        return load_project(root)
    except ConfigError as e:
        die(str(e))
    raise AssertionError  # unreachable


@app.command()
def version() -> None:
    """Print the osca version."""
    typer.echo(__version__)


@app.command()
def new(
    project_id: Annotated[str, typer.Argument(help="Project id, e.g. flowsurface.")],
    upstream: Annotated[str, typer.Option(help="Upstream git URL.")],
    anchor: Annotated[str, typer.Option(help="Upstream tag/commit to start from (tip: the second-newest release).")],
    name: Annotated[Optional[str], typer.Option(help="Display name.")] = None,
    directory: Annotated[Optional[Path], typer.Option(help="Target directory (default ./osca-<id>).")] = None,
    branch: Annotated[str, typer.Option(help="Upstream development branch.")] = "main",
    license: Annotated[str, typer.Option(help="Upstream license (SPDX).")] = "",
    terminology: Annotated[str, typer.Option(help="Comma list of terminology domains.")] = "general",
    include: Annotated[str, typer.Option(help="Comma list of scope globs.")] = "**",
    exclude: Annotated[str, typer.Option(help="Comma list of exclude globs.")] = "**/tests/**,**/test/**,**/benches/**,**/*_test.go,**/*.test.ts,**/*.spec.ts,**/testdata/**",
    template: Annotated[str, typer.Option(help="Copier template source.")] = onboard_mod.TEMPLATE,
    template_ref: Annotated[str, typer.Option(help="Template git ref.")] = "main",
    registry: Annotated[Optional[Path], typer.Option(help="Atlas projects/ dir: also write <id>.yaml there.")] = None,
    categories: Annotated[str, typer.Option(help="Comma list of catalog categories (for --registry).")] = "",
    description: Annotated[str, typer.Option(help="One-line description (for --registry).")] = "",
    languages: Annotated[str, typer.Option(help="Comma list, primary first (for --registry).")] = "rust",
) -> None:
    """Create a local study repository for a new upstream project."""
    split = lambda v: [x.strip() for x in v.split(",") if x.strip()]  # noqa: E731
    spec = onboard_mod.NewProject(
        id=project_id,
        name=name or project_id,
        upstream=upstream,
        anchor=anchor,
        directory=(directory or Path.cwd() / f"osca-{project_id}").resolve(),
        branch=branch,
        license=license,
        terminology=split(terminology),
        include=split(include),
        exclude=split(exclude),
        template=template,
        template_ref=template_ref,
    )
    try:
        d = onboard_mod.create(spec, typer.echo)
    except (onboard_mod.OnboardError, gitutil.GitError) as e:
        die(str(e))
        return
    if registry:
        out = registry / f"{project_id}.yaml"
        out.write_text(onboard_mod.registry_entry(spec, split(categories), description, split(languages)), encoding="utf-8")
        typer.echo(f"registry  {out}")
    typer.echo(f"\nNext: cd {d} && osca status && osca publish --public")


@app.command()
def publish(
    public: Annotated[bool, typer.Option("--public/--private", help="Repository visibility.")] = True,
    repo: Annotated[Optional[str], typer.Option(help="owner/name (default dajy5120/osca-<id>).")] = None,
    description: Annotated[str, typer.Option(help="Repository description.")] = "",
) -> None:
    """Create the study repo on GitHub, push, and leave only OSCA workflows enabled."""
    project = _project()
    repo = repo or f"dajy5120/osca-{project.id}"
    desc = description or f"{project.raw.get('name', project.id)} 中文源码学习版（OSCA）：上游源码 + 【zh】 中文注释，持续跟随官方版本"
    try:
        onboard_mod.publish(project.root, repo, public, desc, typer.echo)
    except onboard_mod.OnboardError as e:
        die(str(e))


@app.command()
def init(
    anchor: Annotated[str, typer.Option(help="Upstream ref (tag/commit) the study branch starts from.")],
    force: Annotated[bool, typer.Option(help="Overwrite an existing sync.yaml.")] = False,
) -> None:
    """Record the initial anchor, create the mirror branch and wire CLAUDE.md."""
    project = _project()
    root = project.root
    if (root / SYNC_FILE).exists() and not force:
        die(f"{SYNC_FILE} already exists (use --force to overwrite)")
    commit = gitutil.try_rev_parse(root, anchor)
    if not commit:
        die(f"cannot resolve {anchor!r}; did you fetch upstream tags?")
    if not gitutil.is_ancestor(root, commit, "HEAD"):
        die(f"{anchor} is not an ancestor of HEAD")
    write_sync(
        root,
        {
            "anchor": {
                "upstream_commit": commit,
                "upstream_ref": anchor,
                "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            "history": [],
        },
    )
    typer.echo(f"anchor     {anchor} ({commit[:12]}) → {SYNC_FILE}")

    if not gitutil.try_rev_parse(root, project.mirror_branch):
        gitutil.git(root, "branch", project.mirror_branch, commit)
        typer.echo(f"branch     {project.mirror_branch} → {commit[:12]}")

    claude = root / CLAUDE_MD
    if not claude.exists():
        claude.write_text(f"{CLAUDE_IMPORT}\n", encoding="utf-8")
        typer.echo(f"created    {CLAUDE_MD} (imports {CLAUDE_IMPORT})")
    else:
        text = claude.read_text(encoding="utf-8")
        if CLAUDE_IMPORT not in text.splitlines():
            sep = "" if text.endswith("\n") else "\n"
            claude.write_text(f"{text}{sep}\n{CLAUDE_IMPORT}\n", encoding="utf-8")
            typer.echo(f"appended   {CLAUDE_IMPORT} to upstream {CLAUDE_MD}")

    ignored = gitutil.git(
        root, "check-ignore", CLAUDE_MD, ".osca/CLAUDE.md", ".claude/settings.json", check=False
    ).split()
    if ignored:
        typer.secho(
            f"note       upstream .gitignore ignores {', '.join(ignored)}; add with `git add -f`",
            fg=typer.colors.YELLOW,
        )


def _hook_files(root: Path) -> list[str] | None:
    """Extract the edited file from a Claude Code PostToolUse payload on stdin."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return None
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(payload.get("cwd") or root) / path
    try:
        return [path.resolve().relative_to(root.resolve()).as_posix()]
    except ValueError:
        return None


@app.command()
def verify(
    files: Annotated[Optional[list[str]], typer.Argument(help="Limit to these paths.")] = None,
    anchor: Annotated[Optional[str], typer.Option(help="Override the anchor commit.")] = None,
    hook: Annotated[bool, typer.Option(help="Claude Code hook mode: read the edited file from stdin, exit 2 on violation.")] = False,
) -> None:
    """Check the strip invariant: strip_zh(study) == upstream@anchor."""
    if hook:
        root = find_project_root(Path.cwd().resolve())
        if root is None:
            raise typer.Exit(0)
        files = _hook_files(root)
        if not files:
            raise typer.Exit(0)
        project = load_project(root)
    else:
        project = _project()
        root = project.root
        if files:
            files = [
                (Path.cwd() / f).resolve().relative_to(root.resolve()).as_posix() for f in files
            ]
    try:
        ref = anchor or anchor_commit(root)
    except ConfigError as e:
        die(str(e))
    issues = run_verify(root, ref, project.marker, files, project.generated)
    if not issues:
        if not hook:
            scope = f"{len(files)} file(s)" if files else "working tree"
            typer.secho(f"✓ strip invariant holds for {scope} against {ref[:12]}", fg=typer.colors.GREEN)
        raise typer.Exit(0)
    out = "\n".join(i.format() for i in issues)
    if hook:
        print(
            "OSCA verify failed: only whole-line `【zh】` comments may be added to upstream files.\n"
            f"{out}\nRevert the non-annotation change.",
            file=sys.stderr,
        )
        raise typer.Exit(2)
    typer.echo(out)
    typer.secho(f"✗ {len(issues)} violation(s)", fg=typer.colors.RED)
    raise typer.Exit(1)


@app.command()
def strip(
    files: Annotated[list[Path], typer.Argument(help="Files to strip.")],
    out: Annotated[Optional[Path], typer.Option(help="Write stripped files under this directory instead of stdout.")] = None,
    marker: Annotated[str, typer.Option()] = "【zh】",
) -> None:
    """Print files with all 【zh】 annotation lines removed."""
    for f in files:
        tokens = comment_tokens(f.as_posix())
        text = f.read_text(encoding="utf-8")
        stripped = strip_text(text, tokens, marker)[0] if tokens else text
        if out:
            dest = out / f
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(stripped, encoding="utf-8")
        else:
            sys.stdout.write(stripped)


@app.command()
def status(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
    write: Annotated[bool, typer.Option(help="Write .osca/status.json.")] = False,
    depth: Annotated[int, typer.Option(help="Directory depth used for grouping.")] = 4,
) -> None:
    """Show translation coverage."""
    project = _project()
    st = status_mod.compute(project)
    if write:
        status_mod.write_json(project, st)
    if as_json:
        typer.echo(json.dumps(status_mod.to_json(project, st), ensure_ascii=False, indent=2))
    else:
        typer.echo(status_mod.render(project, st, depth))


def _rel(project: Project, paths: list[str] | None) -> list[str] | None:
    if not paths:
        return None
    out = []
    for f in paths:
        full = (Path.cwd() / f).resolve()
        rel = full.relative_to(project.root.resolve()).as_posix()
        if full.is_dir():
            prefix = rel.rstrip("/") + "/"
            out += [x for x in index_mod.scope_files(project) if x.startswith(prefix)]
        else:
            out.append(rel)
    return out


def _git_user(root: Path) -> str:
    return gitutil.git(root, "config", "user.name", check=False).strip() or "unknown"


@app.command()
def index(
    paths: Annotated[Optional[list[str]], typer.Argument(help="Files or directories (default: whole scope).")] = None,
    write: Annotated[bool, typer.Option(help="Update .osca/state/symbols.jsonl from the current annotations.")] = False,
    show: Annotated[bool, typer.Option(help="List symbols of the given files.")] = False,
) -> None:
    """Build the symbol index; with --write, record annotation state."""
    project = _project()
    rel = _rel(project, paths)
    indexes = index_mod.index_worktree(project, rel)
    if show:
        records = state_mod.load(project.root)
        for path, idx in indexes.items():
            typer.secho(path, bold=True)
            for sym in idx.symbols.values():
                d = state_mod.derive(sym, records.get(sym.id))
                mark = "" if sym.translatable else " (not counted)"
                extra = f" [{'/'.join(d.changed)}]" if d.changed else ""
                typer.echo(f"  L{sym.line:<6}{sym.kind:<8}{d.status:<11}{sym.qualname}{extra}{mark}")
    if write:
        records = state_mod.load(project.root)
        changes = state_mod.update(
            records, state_mod.all_symbols(indexes), _git_user(project.root), set(indexes) if rel else None
        )
        state_mod.save(project.root, records)
        _write_status(project)
        typer.echo("state: " + ", ".join(f"{k} {len(v)}" for k, v in changes.items()))
    if not show and not write:
        n = sum(len(i.symbols) for i in indexes.values())
        typer.echo(f"{len(indexes)} files, {n} symbols")


@app.command()
def queue(
    prefix: Annotated[Optional[str], typer.Argument(help="Path prefix filter.")] = None,
    which: Annotated[str, typer.Option("--state", help="stale | pending | translated | reviewed")] = "stale",
    limit: Annotated[int, typer.Option(help="Maximum rows (0 = all).")] = 50,
) -> None:
    """List symbols waiting for work (stale annotations first by priority)."""
    project = _project()
    rows = status_mod.queue(status_mod.compute(project), which, prefix)
    for r in rows[: limit or None]:
        extra = f"{r.priority} {'/'.join(r.changed):<9}" if which == "stale" else ""
        typer.echo(f"{extra}{r.sym.path}:{r.sym.line}  {r.sym.qualname}")
    if limit and len(rows) > limit:
        typer.echo(f"… {len(rows) - limit} more")
    typer.echo(f"{len(rows)} {which}")


@review_app.command("approve")
def review_approve(
    targets: Annotated[list[str], typer.Argument(help="Symbol ids (path#name), files or directories.")],
    by: Annotated[Optional[str], typer.Option(help="Reviewer name (default: git user.name).")] = None,
) -> None:
    """Confirm annotations are correct for the current code (clears stale, marks reviewed)."""
    project = _project()
    ids = [t for t in targets if "#" in t]
    files = _rel(project, [t for t in targets if "#" not in t]) or []
    paths = sorted({i.split("#", 1)[0] for i in ids} | set(files))
    syms = state_mod.all_symbols(index_mod.index_worktree(project, paths))
    chosen = [s for s in syms.values() if s.zh and (s.id in ids or s.path in files)]
    missing = [i for i in ids if i not in syms]
    for i in missing:
        typer.secho(f"not found: {i}", fg=typer.colors.YELLOW)
    records = state_mod.load(project.root)
    done = state_mod.approve(records, chosen, by or _git_user(project.root))
    state_mod.save(project.root, records)
    _write_status(project)
    typer.echo(f"approved {len(done)} annotated symbol(s)")


def _load_terms(project: Project, terms_dir: Path | None) -> dict:
    try:
        return terms_mod.load_project_terms(project, terms_dir)
    except Exception as e:  # network / missing file: translation still works without a glossary
        typer.secho(f"warning: terminology not loaded ({e}); pass --terms-dir", fg=typer.colors.YELLOW, err=True)
        return {}


def _print_result(res) -> None:
    u = res.usage
    cost = f", ≈${u['cost_micro_usd'] / 1e6:.3f} list price" if u.get("cost_micro_usd") else ""
    tok = f"  [out {u.get('output_tokens', 0)} tokens{cost}]" if u else ""
    if res.error:
        typer.secho(f"✗ {res.path}: {res.error}{tok}", fg=typer.colors.RED)
    else:
        typer.secho(f"✓ {res.path}: {len(res.applied)} annotated, {len(res.unchanged)} judged still correct{tok}", fg=typer.colors.GREEN)
    for r in res.rejected:
        typer.echo(f"    rejected {r}")
    for sid in res.unchanged:
        typer.echo(f"    unchanged {sid}  (needs `osca review approve`)")
    if res.notes:
        typer.echo(f"    notes: {res.notes}")


@app.command()
def translate(
    paths: Annotated[Optional[list[str]], typer.Argument(help="Files or directories (default: whole scope).")] = None,
    which: Annotated[str, typer.Option("--state", help="Comma list of states to work on.")] = "pending,stale",
    max_symbols: Annotated[Optional[int], typer.Option(help="Symbols per request.")] = None,
    limit_files: Annotated[Optional[int], typer.Option(help="Only the first N files.")] = None,
    all_symbols: Annotated[bool, typer.Option(help="Also annotate symbols that do not count towards coverage.")] = False,
    dry_run: Annotated[bool, typer.Option(help="Show the plan and a token estimate; no API calls.")] = False,
    batch: Annotated[bool, typer.Option(help="Submit through the Message Batches API (async, half price).")] = False,
    collect: Annotated[Optional[str], typer.Option(help="Apply the results of a finished batch.")] = None,
    terms_dir: Annotated[Optional[Path], typer.Option(help="Local atlas terminology/ directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
    backend: Annotated[Optional[str], typer.Option(help="claude-code (subscription, default) | api (API key).")] = None,
    model: Annotated[Optional[str], typer.Option(help="Override ai.model, e.g. claude-haiku-4-5.")] = None,
) -> None:
    """Annotate pending / stale symbols with Claude (structured patches; code is never edited by the model)."""
    project = _project()
    ai = project.raw.setdefault("ai", {})
    if backend:
        ai["backend"] = backend
    if model:
        ai["model"] = model
    cfg = tr_mod.ai_config(project)
    if batch and cfg["backend"] != "api":
        die("--batch needs the API backend (--backend api with ANTHROPIC_API_KEY)")
    if collect:
        status_, results = tr_mod.collect_batch(project, collect)
        if status_ != "ended":
            typer.echo(f"batch {collect}: {status_}")
            raise typer.Exit(0)
        for r in results:
            _print_result(r)
        raise typer.Exit(0)

    if not gitutil.is_clean(project.root):
        die("working tree has uncommitted changes; commit or stash first so AI edits are reviewable")
    jobs = tr_mod.plan(project, _rel(project, paths), tuple(which.split(",")), max_symbols, limit_files, all_symbols)
    if not jobs:
        typer.echo("Nothing to translate.")
        raise typer.Exit(0)
    terms = _load_terms(project, terms_dir)
    n_sym = sum(len(j.targets) for j in jobs)
    fresh = cached = 0
    seen: set[str] = set()
    for j in jobs:
        file_tok = len(tr_mod.render_file(j, terms).encode()) // 3
        fresh += len(tr_mod.render_targets(j).encode()) // 3 + 3000
        if j.path in seen and not batch:
            cached += file_tok  # later chunks of a file re-read the cached file block
        else:
            fresh += file_tok
            seen.add(j.path)
    typer.echo(f"{len(jobs)} request(s), {len(seen)} file(s), {n_sym} symbol(s) → {cfg['model']} via {cfg['backend']} (effort {cfg['effort']})")
    typer.echo(
        f"estimated input ≈ {fresh:,} tokens + {cached:,} cache-read tokens (rough: bytes/3)"
        + (" — batch: 50% price" if batch else "")
    )
    for j in jobs:
        kinds = ", ".join(f"{k} {sum(t.status == k for t in j.targets)}" for k in ("pending", "stale") if any(t.status == k for t in j.targets))
        typer.echo(f"  {j.path}  ({kinds})")
    if dry_run:
        raise typer.Exit(0)
    if not yes and not typer.confirm(f"Run {len(jobs)} request(s) via {cfg['backend']} now?"):
        raise typer.Exit(1)
    if batch:
        bid = tr_mod.submit_batch(project, jobs, terms)
        typer.echo(f"submitted batch {bid}; apply later with `osca translate --collect {bid}`")
        raise typer.Exit(0)
    results = tr_mod.run(project, jobs, terms, tr_mod.caller_for(project), lambda j, r: _print_result(r))
    _write_status(project)
    done = sum(len(r.applied) for r in results)
    typer.echo(f"\n{done} symbol(s) annotated. Review with `git diff`, then `osca verify` and commit (state already recorded).")


def _write_status(project: Project) -> None:
    """Keep .osca/status.json current for the atlas aggregation."""
    status_mod.write_json(project, status_mod.compute(project))


@docs_app.command("list")
def docs_list() -> None:
    """Show each analysis document and whether its anchors changed."""
    project = _project()
    rows = docs_mod.check(project)
    colors = {"current": typer.colors.GREEN, "new": typer.colors.GREEN, "stale": typer.colors.YELLOW, "broken": typer.colors.RED}
    for r in rows:
        typer.secho(f"{r.status:<11}{r.doc.path}  ({r.doc.title})", fg=colors.get(r.status))
        for a in r.changed:
            typer.echo(f"    changed  {a}")
        for a in r.missing:
            typer.echo(f"    missing  {a}")
    typer.echo(f"{len(rows)} document(s): " + ", ".join(f"{k} {v}" for k, v in docs_mod.summary(rows).items() if v))


@docs_app.command("update")
def docs_update() -> None:
    """Record new or edited documents (stale ones stay stale until edited or approved)."""
    project = _project()
    ch = docs_mod.update(project)
    _write_status(project)
    typer.echo("docs: " + ", ".join(f"{k} {len(v)}" for k, v in ch.items()))


@docs_app.command("approve")
def docs_approve(paths: Annotated[list[str], typer.Argument(help="Document paths.")]) -> None:
    """Confirm documents are still correct for the current code."""
    project = _project()
    rel = [(Path.cwd() / p).resolve().relative_to(project.root.resolve()).as_posix() for p in paths]
    ch = docs_mod.update(project, approve=rel)
    _write_status(project)
    typer.echo(f"approved {len(ch['approved'])}, recorded {len(ch['recorded'])}")


def _atlas(root: Path | None):
    r = (root or atlas_mod.find_root(Path.cwd().resolve()))
    if r is None:
        die("not inside the opensource-code-atlas repository (use --root)")
    return atlas_mod.load(r)


@atlas_app.command("status")
def atlas_status(
    write_readme: Annotated[bool, typer.Option(help="Rewrite the README progress table.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
    offline: Annotated[bool, typer.Option(help="Skip querying upstream tags.")] = False,
    root: Annotated[Optional[Path], typer.Option(help="Atlas repository root.")] = None,
) -> None:
    """Aggregate every registered study repo (status.json + upstream tags)."""
    atlas = _atlas(root)
    rows = atlas_mod.collect(atlas, offline)
    if as_json:
        typer.echo(json.dumps([{"id": r.id, "status": r.status, "behind": r.behind, "latest": r.latest, "error": r.error} for r in rows], ensure_ascii=False, indent=2))
        return
    body = atlas_mod.table(atlas, rows)
    if write_readme:
        changed = atlas_mod.write_readme(atlas, body)
        typer.echo("README.md updated" if changed else "README.md already up to date")
    else:
        typer.echo(body)


@app.command("list")
def list_projects(
    offline: Annotated[bool, typer.Option(help="Skip querying upstream tags.")] = False,
    root: Annotated[Optional[Path], typer.Option(help="Atlas repository root.")] = None,
) -> None:
    """Print the atlas as a category tree."""
    atlas = _atlas(root)
    typer.echo(atlas_mod.tree(atlas, atlas_mod.collect(atlas, offline)))


@app.command()
def impact(
    old: Annotated[str, typer.Argument(help="Old upstream revision.")],
    new: Annotated[str, typer.Argument(help="New upstream revision.")],
) -> None:
    """Symbol-level diff between two upstream revisions (translation scope only)."""
    project = _project()
    d = impact_mod.diff_revs(project, old, new)
    typer.echo(f"{len(d.files)} files · added {len(d.added)} · removed {len(d.removed)} · "
               f"modified {len(d.modified)} · renamed {len(d.renamed)}")
    for a, b, ch in d.modified:
        typer.echo(f"  M {'/'.join(ch):<13}{b.id}")
    for a, b in d.renamed:
        typer.echo(f"  R {'':<13}{a.id} -> {b.id}")
    for x in d.removed:
        typer.echo(f"  D {'':<13}{x.id}")
    for x in d.added:
        if x.translatable:
            typer.echo(f"  A {'':<13}{x.id}")


@app.command()
def resolve() -> None:
    """Resolve merge conflicts: upstream code wins, annotations are re-attached."""
    project = _project()
    root = project.root
    for path in sync_mod.conflicts(root):
        if path == CLAUDE_MD:
            continue
        out = resolve_mod.resolve_file(root, path, project.marker)
        if out is None:
            typer.secho(f"manual  {path}", fg=typer.colors.YELLOW)
        else:
            typer.echo(f"ok      {path}  reattached {out.reattached}, relocated {len(out.relocated)}, orphaned {len(out.orphaned)}")


@app.command()
def sync(
    to: Annotated[Optional[str], typer.Option(help="Upstream ref to sync to (default: newest matching tag).")] = None,
    dry_run: Annotated[bool, typer.Option(help="Only show what would happen.")] = False,
    fetch: Annotated[bool, typer.Option(help="Fetch upstream first.")] = True,
    cont: Annotated[bool, typer.Option("--continue", help="Finish a sync after resolving conflicts.")] = False,
    push: Annotated[bool, typer.Option(help="Push mirror + sync branch to origin.")] = False,
    pr: Annotated[bool, typer.Option(help="Open a PR (implies --push).")] = False,
) -> None:
    """Merge a newer upstream revision into a sync/* branch and write a change report."""
    project = _project()
    root = project.root
    try:
        if cont:
            p = sync_mod.load_pending(root)
            if p is None:
                die("no sync in progress")
        else:
            if sync_mod.load_pending(root):
                die("a sync is already in progress; finish it with `osca sync --continue`")
            p = sync_mod.plan(project, to, fetch)
            if p is None:
                typer.echo("Already at the target upstream revision.")
                raise typer.Exit(0)
            typer.echo(sync_mod.describe(project, p))
            if dry_run:
                raise typer.Exit(0)
            if not sync_mod.start(project, p):
                files = sync_mod.conflicts(root)
                typer.secho(f"\nMerge stopped with {len(files)} conflict(s):", fg=typer.colors.YELLOW)
                for f in files:
                    typer.echo(f"  {f}")
                typer.echo(
                    "\nResolve them with upstream code winning, keep/re-attach the 【zh】 lines,\n"
                    "`git add` the files, then run `osca sync --continue`."
                )
                raise typer.Exit(1)
        assert p is not None
        report, issues = sync_mod.finish(project, p)
    except (sync_mod.SyncError, gitutil.GitError, ConfigError) as e:
        die(str(e))
        return

    typer.secho(f"\n✓ merged {p.label} on {p.branch}", fg=typer.colors.GREEN)
    typer.echo(f"  report: {report.relative_to(root)}")
    if issues:
        typer.secho(f"  verify: {len(issues)} violation(s)", fg=typer.colors.RED)
        for i in issues:
            typer.echo(f"    {i}")
    else:
        typer.echo("  verify: ✓ strip invariant holds")

    if push or pr:
        gitutil.git(root, "push", "origin", project.mirror_branch, p.branch)
        typer.echo(f"  pushed {project.mirror_branch}, {p.branch}")
    if pr:
        url = subprocess.run(
            [
                "gh", "pr", "create", "--repo", _origin_repo(root),
                "--base", project.study_branch, "--head", p.branch,
                "--title", f"sync: upstream {p.label}",
                "--body-file", str(report),
            ],
            cwd=root, capture_output=True, text=True,
        )
        typer.echo(f"  PR: {url.stdout.strip() or url.stderr.strip()}")
        typer.secho("  ⚠ merge this PR with “Create a merge commit” — never squash/rebase.", fg=typer.colors.YELLOW)


def _origin_repo(root: Path) -> str:
    url = gitutil.git(root, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        die(f"origin is not a GitHub repository: {url}")
    return m.group(1)  # type: ignore[union-attr]


@workflows_app.command("disable")
def workflows_disable(
    repo: Annotated[Optional[str], typer.Option(help="owner/name (default: origin).")] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
) -> None:
    """Disable every GitHub workflow that is not an OSCA workflow (osca-*.yml)."""
    project = _project()
    repo = repo or _origin_repo(project.root)
    res = subprocess.run(
        ["gh", "workflow", "list", "--all", "--repo", repo, "--json", "id,name,path,state"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        die(res.stderr.strip())
    disabled = 0
    for wf in json.loads(res.stdout):
        name = Path(wf["path"]).name
        if name.startswith("osca-") or wf["state"] != "active":
            continue
        typer.echo(f"disable  {wf['path']}  ({wf['name']})")
        if not dry_run:
            subprocess.run(["gh", "workflow", "disable", str(wf["id"]), "--repo", repo], check=True)
        disabled += 1
    typer.echo(f"{disabled} upstream workflow(s) {'would be ' if dry_run else ''}disabled")


@terms_app.command("lint")
def terms_lint(
    paths: Annotated[Optional[list[str]], typer.Argument(help="Files or directories (default: whole scope).")] = None,
    terms_dir: Annotated[Optional[Path], typer.Option(help="Local atlas terminology/ directory.")] = None,
) -> None:
    """Check 【zh】 lines for translations the terminology marks as `avoid`."""
    project = _project()
    terms = terms_mod.load_project_terms(project, terms_dir)
    lines = []
    for path, idx in index_mod.index_worktree(project, _rel(project, paths)).items():
        text = (project.root / path).read_text(encoding="utf-8").split("\n")
        zh = set(idx.zh_rows)
        for sym in idx.annotated:
            context = "\n".join(text[r] for r in range(sym.start, sym.end + 1) if r not in zh)
            if sym.kind == "module":  # module notes talk about the whole file
                context = "\n".join(t for r, t in enumerate(text) if r not in zh)
            lines += [(path, r + 1, text[r], context) for r in sym.zh_rows]
    problems = terms_mod.lint_text(terms, lines)
    for p in problems:
        typer.secho(p, fg=typer.colors.YELLOW)
    if problems:
        raise typer.Exit(1)
    typer.secho(f"✓ {len(lines)} annotation lines, no terminology violations", fg=typer.colors.GREEN)


@terms_app.command("validate")
def terms_validate(
    directory: Annotated[Path, typer.Argument(help="Terminology directory.")] = Path("terminology"),
) -> None:
    """Validate terminology files against the schema."""
    errors, warnings, count = terms_mod.validate_dir(directory)
    for w in warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW)
    for e in errors:
        typer.secho(f"error: {e}", fg=typer.colors.RED)
    if errors:
        raise typer.Exit(1)
    typer.secho(f"✓ {count} terms valid", fg=typer.colors.GREEN)


if __name__ == "__main__":  # pragma: no cover
    app()
