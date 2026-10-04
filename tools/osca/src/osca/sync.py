"""Upstream sync: fetch -> fast-forward mirror -> merge into a sync branch -> report.

Phase 1 handles the no-conflict path automatically. On conflicts it stops,
records its state in .git/osca-sync.json, and `osca sync --continue` finishes
the job once the conflicts are resolved by hand (upstream code always wins;
re-attach the 【zh】 lines).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import gitutil, status
from .markers import comment_tokens, count_annotations
from .project import REPORTS_DIR, SYNC_FILE, STATUS_FILE, Project, load_sync, write_sync
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
    _state_path(root).write_text(json.dumps(asdict(p)), encoding="utf-8")
    msg = f"sync: upstream {p.label}\n\nupstream-commit: {p.new_commit}"
    if gitutil.git_ok(root, "merge", "--no-ff", "--no-edit", "-m", msg, p.new_commit):
        return True
    if CLAUDE_MD in conflicts(root):
        _resolve_claude_md(root, p.new_commit)
    return not conflicts(root)


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
    """Commit a resolved merge (if needed), write report/anchor/status, verify, commit."""
    root = project.root
    if conflicts(root):
        raise SyncError("unresolved conflicts remain:\n  " + "\n  ".join(conflicts(root)))
    if gitutil.git_ok(root, "rev-parse", "--verify", "--quiet", "MERGE_HEAD"):
        gitutil.git(root, "commit", "--no-edit")

    report_rel = write_report(project, p)
    data = load_sync(root)
    stats = _diff_stats(project, p)
    data["anchor"] = {
        "upstream_commit": p.new_commit,
        "upstream_ref": p.new_ref,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    data.setdefault("history", []).append(
        {
            "from": p.old_ref,
            "to": p.new_ref,
            "from_commit": p.old_commit,
            "to_commit": p.new_commit,
            **stats,
            "report": report_rel,
        }
    )
    write_sync(root, data)
    status.write_json(project, status.compute(project))

    issues = verify(root, p.new_commit, project.marker, generated=project.generated)
    gitutil.git(root, "add", SYNC_FILE, STATUS_FILE, report_rel)
    gitutil.git(root, "commit", "-m", f"osca: anchor → {p.new_ref}, sync report")
    _state_path(root).unlink(missing_ok=True)
    return root / report_rel, [i.format() for i in issues]


def _diff_stats(project: Project, p: Plan) -> dict[str, int]:
    root = project.root
    files = gitutil.git(root, "diff", "--name-only", "--no-renames", p.old_commit, p.new_commit).splitlines()
    return {
        "commits": int(gitutil.git(root, "rev-list", "--count", "--no-merges", f"{p.old_commit}..{p.new_commit}")),
        "files_changed": len(files),
        "files_in_scope": sum(1 for f in files if project.in_scope(f)),
        "zh_files_affected": len(_affected(project, p)),
    }


def _numstat(root: Path, a: str, b: str) -> dict[str, tuple[str, str]]:
    out = gitutil.split_z(gitutil.git(root, "diff", "--numstat", "--no-renames", "-z", a, b))
    res: dict[str, tuple[str, str]] = {}
    for rec in out:
        add, dele, path = rec.split("\t", 2)
        res[path] = (add, dele)
    return res


def _affected(project: Project, p: Plan) -> dict[str, int]:
    """Upstream-changed files that carry 【zh】 lines on the study branch -> line count."""
    root = project.root
    changed = _numstat(root, p.old_commit, p.new_commit)
    res: dict[str, int] = {}
    with gitutil.BlobReader(root) as blobs:
        for path in changed:
            tokens = comment_tokens(path)
            if tokens is None:
                continue
            data = blobs.read(p.study_tip, path)
            if data is None:
                continue
            n = count_annotations(data.decode("utf-8", "replace"), tokens, project.marker)
            if n:
                res[path] = n
    return res


def write_report(project: Project, p: Plan) -> str:
    root = project.root
    numstat = _numstat(root, p.old_commit, p.new_commit)
    affected = _affected(project, p)
    ns = gitutil.split_z(
        gitutil.git(root, "diff", "--name-status", "--no-renames", "-z", p.old_commit, p.new_commit)
    )
    status_by_path = {path: st[0] for st, path in zip(ns[::2], ns[1::2])}
    in_scope = [f for f in numstat if project.in_scope(f)]
    deps = [f for f in numstat if DEP_FILES.search(f)]
    log = gitutil.git(
        root, "log", "--no-merges", "--format=%h%x09%as%x09%s", f"{p.old_commit}..{p.new_commit}"
    ).splitlines()
    dates = gitutil.git(root, "log", "-1", "--format=%as", p.new_commit).strip()

    def row(f: str) -> str:
        a, d = numstat[f]
        return f"| `{f}` | {status_by_path.get(f, '?')} | +{a} / −{d} |"

    out = [
        f"# Upstream Changes: {p.label}",
        "",
        f"- 上游区间：`{p.old_commit[:12]}..{p.new_commit[:12]}`（截至 {dates}）",
        f"- 提交数：{len(log)}（不含 merge）",
        f"- 变更文件：{len(numstat)}，其中翻译范围内 {len(in_scope)}",
        f"- **需要复核中文注释的文件：{len(affected)}**",
        "",
        "> Phase 1 报告为文件级。符号级（函数/结构体）变化检测将在 Phase 2 由 Tree-sitter 索引提供。",
        "",
        f"## 需要复核中文注释的文件（{len(affected)}）",
        "",
    ]
    if affected:
        out += ["| 文件 | 状态 | 上游变化 | 【zh】 行数 |", "|---|---|---|---|"]
        for f in sorted(affected):
            a, d = numstat[f]
            out.append(f"| `{f}` | {status_by_path.get(f, '?')} | +{a} / −{d} | {affected[f]} |")
    else:
        out.append("无。")
    out += ["", f"## 依赖文件变化（{len(deps)}）", ""]
    out += [f"- `{f}`" for f in sorted(deps)] or ["无。"]
    out += [
        "",
        f"## 翻译范围内的上游变化（{len(in_scope)}）",
        "",
        "<details><summary>展开</summary>",
        "",
        "| 文件 | 状态 | 变化 |",
        "|---|---|---|",
        *[row(f) for f in sorted(in_scope)],
        "",
        "</details>",
        "",
        f"## 上游提交（{len(log)}）",
        "",
        "<details><summary>展开</summary>",
        "",
    ]
    for line in log[:500]:
        h, d, s = (line.split("\t", 2) + ["", ""])[:3]
        out.append(f"- `{h}` {d} {s}")
    if len(log) > 500:
        out.append(f"- …以及另外 {len(log) - 500} 个提交")
    out += ["", "</details>", ""]

    day = datetime.now().strftime("%Y-%m-%d")
    rel = f"{REPORTS_DIR}/sync-{day}-{_sanitize(p.old_ref or p.old_commit[:9])}..{_sanitize(p.new_ref)}.md"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out), encoding="utf-8")
    return rel
