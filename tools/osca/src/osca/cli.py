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

    ignored = gitutil.git(root, "check-ignore", CLAUDE_MD, ".claude/settings.json", check=False).split()
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
    typer.echo(f"approved {len(done)} annotated symbol(s)")


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
