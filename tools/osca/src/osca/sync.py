"""Upstream sync: fetch -> fast-forward mirror -> merge into a sync branch -> report.

Before merging, annotation state is recorded so that annotations become *stale*
when upstream changes the code they describe. Conflicts are resolved
deterministically (upstream code wins, annotations are re-attached to their
symbols, see resolve.py). Anything that still needs a human stops the sync;
`osca sync --continue` finishes it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import gitutil, state, status
from .impact import SymbolDiff, diff_revs
from .index import index_worktree
from .markers import comment_tokens, count_annotations
from .project import REPORTS_DIR, STATE_FILE, STATUS_FILE, SYNC_FILE, Project, load_sync, write_sync
from .resolve import Outcome, resolve_file
from .verify import CLAUDE_IMPORT, CLAUDE_MD, verify

DEP_FILES = re.compile(
    r"(^|/)(Cargo\.toml|Cargo\.lock|pyproject\.toml|uv\.lock|poetry\.lock|requirements[^/]*\.txt|go\.mod|go\.sum|package\.json)$"
)


class SyncError(RuntimeError):
    pass


@dataclass
class Plan:
    old_commit: str
    old_ref: str | None
    new_commit: str
    new_ref: str
    study_tip: str
    branch: str

    @property
    def label(self) -> str:
        return f"{self.old_ref or self.old_commit[:9]} → {self.new_ref}"


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def resolve_target(project: Project, to: str | None) -> tuple[str, str]:
    """Return (ref_name, commit) of the upstream revision to sync to."""
    root = project.root
    if to:
        commit = gitutil.try_rev_parse(root, to)
        if not commit:
            raise SyncError(f"cannot resolve {to!r}")
        return to, commit
    if project.track == "tags":
        pat = re.compile(project.tag_pattern)
        tags = [t for t in gitutil.git(root, "tag", "--list").split() if pat.search(t)]
        if not tags:
            raise SyncError(f"no tags match {project.tag_pattern!r}")
        tag = max(tags, key=_version_key)
        return tag, gitutil.rev_parse(root, tag)
    ref = f"{project.upstream_remote}/{project.upstream_branch}"
    return ref, gitutil.rev_parse(root, ref)


def _state_path(root: Path) -> Path:
    return root / gitutil.git(root, "rev-parse", "--git-path", "osca-sync.json").strip()


def _sanitize(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", ref.split("/")[-1])


def plan(project: Project, to: str | None = None, fetch: bool = True) -> Plan | None:
    root = project.root
    if fetch:
        gitutil.git(root, "fetch", project.upstream_remote, "--tags", "--prune", "--force")
    anchor = load_sync(root)["anchor"]
    old = anchor["upstream_commit"]
    new_ref, new = resolve_target(project, to)
    if new == old:
        return None
    if not gitutil.is_ancestor(root, old, new):
        raise SyncError(
            f"{new_ref} ({new[:9]}) does not descend from the anchor {old[:9]}: "
            "upstream history was rewritten or the target is older. Resolve manually."
        )
    mirror = gitutil.try_rev_parse(root, project.mirror_branch)
    if mirror and not gitutil.is_ancestor(root, mirror, new):
        raise SyncError(f"{project.mirror_branch} ({mirror[:9]}) cannot fast-forward to {new_ref}")
    study_tip = gitutil.rev_parse(root, project.study_branch)
    return Plan(old, anchor.get("upstream_ref"), new, new_ref, study_tip, f"sync/{_sanitize(new_ref)}")


def describe(project: Project, p: Plan) -> str:
    root = project.root
    commits = gitutil.git(root, "rev-list", "--count", "--no-merges", f"{p.old_commit}..{p.new_commit}").strip()
    files = gitutil.git(root, "diff", "--name-only", "--no-renames", p.old_commit, p.new_commit).splitlines()
    return (
        f"Sync {project.id}: {p.label}\n"
        f"  upstream  {p.old_commit[:12]} → {p.new_commit[:12]}  ({commits} commits, {len(files)} files)\n"
        f"  mirror    {project.mirror_branch} fast-forward\n"
        f"  branch    {p.branch}  (from {project.study_branch} @ {p.study_tip[:9]})"
    )


def _record_state(project: Project, by: str) -> list[str]:
    """Bring .osca/state up to date with the annotations on the current branch."""
    root = project.root
    records = state.load(root)
    symbols = state.all_symbols(index_worktree(project))
    changes = state.update(records, symbols, by)
    if any(changes.values()):
        state.save(root, records)
    return [k for k, v in changes.items() if v]


def start(project: Project, p: Plan) -> bool:
    """Create the sync branch and merge. Returns True if the merge completed."""
    root = project.root
    if not gitutil.is_clean(root):
        raise SyncError("working tree has uncommitted changes")
    if gitutil.current_branch(root) == project.mirror_branch:
        raise SyncError(f"currently on {project.mirror_branch}; switch to {project.study_branch} first")
    if gitutil.try_rev_parse(root, p.branch):
        raise SyncError(f"branch {p.branch} already exists")
    gitutil.git(root, "branch", "-f", project.mirror_branch, p.new_commit)
    gitutil.git(root, "switch", "-c", p.branch, project.study_branch)
    from .docs import DOCS_STATE, update as update_docs

    recorded = _record_state(project, "osca")
    docs_changed = any(update_docs(project).values())
    if recorded or docs_changed:
        gitutil.git(root, "add", *[f for f in (STATE_FILE, DOCS_STATE) if (root / f).exists()])
        gitutil.git(root, "commit", "-m", "osca: record annotation and analysis-doc state before sync")
    _state_path(root).write_text(json.dumps(asdict(p)), encoding="utf-8")
    msg = f"sync: upstream {p.label}\n\nupstream-commit: {p.new_commit}"
    if gitutil.git_ok(root, "merge", "--no-ff", "--no-edit", "-m", msg, p.new_commit):
        return True
    outcomes: list[Outcome] = []
    for path in conflicts(root):
        if path == CLAUDE_MD:
            _resolve_claude_md(root, p.new_commit)
            continue
        out = resolve_file(root, path, project.marker)
        if out is not None:
            outcomes.append(out)
    _resolutions_path(root).write_text(json.dumps([asdict(o) for o in outcomes], ensure_ascii=False), encoding="utf-8")
    return not conflicts(root)


def _resolutions_path(root: Path) -> Path:
    return root / gitutil.git(root, "rev-parse", "--git-path", "osca-resolve.json").strip()


def load_resolutions(root: Path) -> list[Outcome]:
    path = _resolutions_path(root)
    if not path.is_file():
        return []
    return [Outcome(**o) for o in json.loads(path.read_text(encoding="utf-8"))]


def _resolve_claude_md(root: Path, upstream: str) -> None:
    """Upstream CLAUDE.md wins; re-append the OSCA import line."""
    blob = gitutil.git_bytes(root, "cat-file", "-p", f"{upstream}:{CLAUDE_MD}", check=False)
    text = blob.decode("utf-8")
    if text and not text.endswith("\n"):
        text += "\n"
    text = f"{text}\n{CLAUDE_IMPORT}\n" if text else f"{CLAUDE_IMPORT}\n"
    (root / CLAUDE_MD).write_text(text, encoding="utf-8")
    gitutil.git(root, "add", CLAUDE_MD)


def conflicts(root: Path) -> list[str]:
    return gitutil.git(root, "diff", "--name-only", "--diff-filter=U").splitlines()


def load_pending(root: Path) -> Plan | None:
    path = _state_path(root)
    if not path.is_file():
        return None
    return Plan(**json.loads(path.read_text(encoding="utf-8")))


def finish(project: Project, p: Plan) -> tuple[Path, list[str]]:
    """Commit the merge, update state / anchor / report / status, verify, commit."""
    root = project.root
    if conflicts(root):
        raise SyncError("unresolved conflicts remain:\n  " + "\n  ".join(conflicts(root)))
    if gitutil.git_ok(root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"):
        gitutil.git(root, "commit", "--no-edit")

    records = state.load(root)
    symbols = state.all_symbols(index_worktree(project))
    changes = state.update(records, symbols, "osca")
    state.save(root, records)
    st = status.compute(project)
    diff = diff_revs(project, p.old_commit, p.new_commit)
    resolutions = load_resolutions(root)

    report_rel = write_report(project, p, st, diff, changes, resolutions)
    data = load_sync(root)
    data["anchor"] = {
        "upstream_commit": p.new_commit,
        "upstream_ref": p.new_ref,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    stale = [r for r in st.rows if r.status == "stale"]
    docs_stale = [d for d in st.docs if d.status in ("stale", "broken")]
    data.setdefault("history", []).append(
        {
            "from": p.old_ref,
            "to": p.new_ref,
            "from_commit": p.old_commit,
            "to_commit": p.new_commit,
            **_diff_stats(project, p),
            "symbols": {
                "added": len(diff.added),
                "removed": len(diff.removed),
                "modified": len(diff.modified),
                "renamed": len(diff.renamed),
            },
            "zh_stale": len(stale),
            "docs_stale": len(docs_stale),
            "zh_orphaned": len(changes["dropped"]) + sum(len(o.orphaned) for o in resolutions),
            "report": report_rel,
        }
    )
    write_sync(root, data)
    status.write_json(project, st)

    issues = verify(root, p.new_commit, project.marker, generated=project.generated)
    from .docs import DOCS_STATE

    gitutil.git(root, "add", SYNC_FILE, STATUS_FILE, STATE_FILE, report_rel,
                *([DOCS_STATE] if (root / DOCS_STATE).exists() else []))
    gitutil.git(root, "commit", "-m", f"osca: anchor → {p.new_ref}, sync report")
    _state_path(root).unlink(missing_ok=True)
    _resolutions_path(root).unlink(missing_ok=True)
    return root / report_rel, [i.format() for i in issues]


def _diff_stats(project: Project, p: Plan) -> dict[str, int]:
    root = project.root
    files = gitutil.git(root, "diff", "--name-only", "--no-renames", p.old_commit, p.new_commit).splitlines()
    return {
        "commits": int(gitutil.git(root, "rev-list", "--count", "--no-merges", f"{p.old_commit}..{p.new_commit}")),
        "files_changed": len(files),
        "files_in_scope": sum(1 for f in files if project.in_scope(f)),
    }


def _numstat(root: Path, a: str, b: str) -> dict[str, tuple[str, str]]:
    out = gitutil.split_z(gitutil.git(root, "diff", "--numstat", "--no-renames", "-z", a, b))
    res: dict[str, tuple[str, str]] = {}
    for rec in out:
        add, dele, path = rec.split("\t", 2)
        res[path] = (add, dele)
    return res


def _unsupported_affected(project: Project, p: Plan, paths: list[str]) -> dict[str, int]:
    """Files without a symbol extractor that carry annotations: file-level fallback."""
    from .symbols import supported

    res: dict[str, int] = {}
    with gitutil.BlobReader(project.root) as blobs:
        for path in paths:
            tokens = comment_tokens(path)
            if tokens is None or supported(path):
                continue
            data = blobs.read(p.study_tip, path)
            if data is None:
                continue
            n = count_annotations(data.decode("utf-8", "replace"), tokens, project.marker)
            if n:
                res[path] = n
    return res


CHANGE_ZH = {"sig": "签名", "doc": "英文文档", "body": "实现"}


def _sym_link(sym) -> str:
    return f"`{sym.id.split('#', 1)[1]}` ({sym.path}:{sym.line})"


def write_report(
    project: Project,
    p: Plan,
    st: status.Status,
    diff: SymbolDiff,
    changes: dict[str, list[str]],
    resolutions: list[Outcome],
) -> str:
    root = project.root
    numstat = _numstat(root, p.old_commit, p.new_commit)
    in_scope = [f for f in numstat if project.in_scope(f)]
    deps = [f for f in numstat if DEP_FILES.search(f)]
    fallback = _unsupported_affected(project, p, list(numstat))
    log = gitutil.git(
        root, "log", "--no-merges", "--format=%h%x09%as%x09%s", f"{p.old_commit}..{p.new_commit}"
    ).splitlines()
    dates = gitutil.git(root, "log", "-1", "--format=%as", p.new_commit).strip()
    stale = sorted((r for r in st.rows if r.status == "stale"), key=lambda r: (r.priority, r.sym.id))
    orphaned = [*changes["dropped"]] + [o["symbol"] for res in resolutions for o in res.orphaned]
    relocated = [sid for res in resolutions for sid in res.relocated]
    conflicted = [res for res in resolutions if res.reattached or res.relocated or res.orphaned]

    out = [
        f"# Upstream Changes: {p.label}",
        "",
        f"- 上游区间：`{p.old_commit[:12]}..{p.new_commit[:12]}`（截至 {dates}），{len(log)} 个提交（不含 merge）",
        f"- 变更文件：{len(numstat)}，其中翻译范围内 {len(in_scope)}",
        f"- 符号变化（翻译范围内）：新增 {len(diff.added)} · 删除 {len(diff.removed)} · 修改 {len(diff.modified)} · 重命名/移动 {len(diff.renamed)}",
        f"- **需要复核的中文注释：{len(stale)} 个符号**；孤儿注释 {len(orphaned)}；冲突自动解决 {len(conflicted)} 个文件"
        f"；受影响的分析文档 {sum(1 for d in st.docs if d.status in ('stale', 'broken'))}",
        "",
        "复核方式：注释仍然正确 → `osca review approve <符号或文件>`；需要修改 → 直接编辑 【zh】 行（编辑即视为重新翻译）。",
        "",
        f"## 需要复核的中文注释（{len(stale)}）",
        "",
        "优先级：P0 签名变化（API 变了）· P1 英文文档变化（原文变了）· P2 实现变化（讲解可能过时）。",
        "",
    ]
    if stale:
        out += ["| 优先级 | 符号 | 变化 |", "|---|---|---|"]
        out += [f"| {r.priority} | {_sym_link(r.sym)} | {' / '.join(CHANGE_ZH[c] for c in r.changed)} |" for r in stale]
    else:
        out.append("无。")
    if orphaned:
        out += ["", f"## 孤儿注释（{len(orphaned)}）", "", "以下符号已被上游删除，其中文注释未能重新挂载："]
        out += [f"- `{sid}`" for sid in orphaned]
        lost = [o for res in resolutions for o in res.orphaned]
        if lost:
            out += ["", "<details><summary>注释原文</summary>", ""]
            for o in lost:
                out += [f"`{o['symbol']}`", "", "```", *o["lines"], "```", ""]
            out += ["</details>"]
    if conflicted:
        out += ["", f"## 自动解决的冲突（{len(conflicted)}）", "", "上游代码优先；中文注释按符号重新挂载。", "",
                "| 文件 | 重新挂载 | 挂到符号开头 | 孤儿 |", "|---|---|---|---|"]
        out += [f"| `{r.path}` | {r.reattached} | {len(r.relocated)} | {len(r.orphaned)} |" for r in conflicted]
        if relocated:
            out += ["", "挂到符号开头的注释（原锚点行已被上游改写，请确认位置）：", *[f"- `{s}`" for s in relocated]]
    docs_hit = [d for d in st.docs if d.status in ("stale", "broken")]
    if docs_hit:
        out += ["", f"## 受影响的分析文档（{len(docs_hit)}）", "",
                "文档锚定的源码发生变化：更新文档（编辑即刷新），或确认仍正确后 `osca docs approve <文档>`。", "",
                "| 文档 | 状态 | 变化的锚点 |", "|---|---|---|"]
        out += [f"| `{d.doc.path}` | {d.status} | {', '.join(f'`{a}`' for a in (d.changed or d.missing))} |" for d in docs_hit]
    if fallback:
        out += ["", f"## 文件级复核（无符号解析器，{len(fallback)}）", "", "| 文件 | 【zh】 行数 |", "|---|---|"]
        out += [f"| `{f}` | {n} |" for f, n in sorted(fallback.items())]
    if changes["migrated"]:
        out += ["", "## 随重命名迁移的注释", "", *[f"- {m}" for m in changes["migrated"]]]

    def sym_rows(title: str, items: list[str]) -> list[str]:
        return ["", f"<details><summary>{title}（{len(items)}）</summary>", "", *items, "", "</details>"]

    out += ["", "## 上游符号变化（翻译范围内）"]
    out += sym_rows("修改", [f"- {_sym_link(n)}：{' / '.join(CHANGE_ZH[c] for c in ch)}" for _, n, ch in diff.modified])
    out += sym_rows("新增（可翻译）", [f"- {_sym_link(s)}" for s in diff.added if s.translatable])
    out += sym_rows("删除", [f"- `{s.id}`" for s in diff.removed])
    out += sym_rows("重命名 / 移动", [f"- `{a.id}` → `{b.id}`" for a, b in diff.renamed])
    out += ["", f"## 依赖文件变化（{len(deps)}）", ""]
    out += [f"- `{f}`" for f in sorted(deps)] or ["无。"]
    commits = [f"- `{h}` {d} {s}" for h, d, s in ((line.split("\t", 2) + ["", ""])[:3] for line in log[:500])]
    if len(log) > 500:
        commits.append(f"- …以及另外 {len(log) - 500} 个提交")
    out += sym_rows("上游提交", commits)
    out.append("")

    day = datetime.now().strftime("%Y-%m-%d")
    rel = f"{REPORTS_DIR}/sync-{day}-{_sanitize(p.old_ref or p.old_commit[:9])}..{_sanitize(p.new_ref)}.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return rel
