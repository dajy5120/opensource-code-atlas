"""Translation status, derived from the symbol index and state records (ADR 0003).

Files whose language has no symbol extractor fall back to file granularity
(annotated -> translated).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitutil, state
from .index import index_worktree
from .markers import comment_tokens, count_annotations
from .project import STATUS_FILE, Project, load_sync
from .symbols import Symbol, supported

ORDER = ("reviewed", "translated", "stale", "pending")


@dataclass
class Row:
    sym: Symbol
    status: str
    changed: list[str]
    priority: str


@dataclass
class Status:
    rows: list[Row] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    file_status: dict[str, str] = field(default_factory=dict)
    fallback: dict[str, int] = field(default_factory=dict)  # unsupported annotated files -> zh lines
    zh_lines: int = 0
    docs: list = field(default_factory=list)  # docs.DocStatus

    def counted(self) -> list[Row]:
        """Rows in the coverage denominator: translatable symbols plus anything annotated."""
        return [r for r in self.rows if r.sym.translatable or r.status != "pending"]

    def counts(self) -> Counter:
        return Counter(r.status for r in self.counted())

    def stale_kinds(self) -> Counter:
        return Counter(k for r in self.rows if r.status == "stale" for k in r.changed)


def group_of(path: str, depth: int) -> str:
    parts = path.split("/")
    return "/".join(parts[: min(depth, len(parts) - 1)]) or "."


def compute(project: Project, paths: list[str] | None = None) -> Status:
    root = project.root
    indexes = index_worktree(project, paths)
    records = state.load(root)
    symbols = state.all_symbols(indexes)
    st = Status()
    for path, idx in indexes.items():
        st.zh_lines += len(idx.zh_rows)
        statuses = []
        for sym in idx.symbols.values():
            d = state.derive(sym, records.get(sym.id))
            st.rows.append(Row(sym, d.status, d.changed, d.priority))
            if sym.translatable or d.status != "pending":
                statuses.append(d.status)
        st.file_status[path] = (
            "stale" if "stale" in statuses else "translated" if any(s != "pending" for s in statuses) else "pending"
        )
    st.orphaned = state.orphans(records, symbols, set(indexes) if paths is not None else None)

    if paths is None:
        from .docs import check as check_docs

        st.docs = check_docs(project)
        for path in gitutil.split_z(gitutil.git(root, "ls-files", "-z")):
            tokens = comment_tokens(path)
            if tokens is None or supported(path) or not project.in_scope(path):
                continue
            full = root / path
            try:
                n = count_annotations(full.read_text(encoding="utf-8"), tokens, project.marker)
            except (UnicodeDecodeError, OSError):
                continue
            st.file_status[path] = "translated" if n else "pending"
            if n:
                st.fallback[path] = n
                st.zh_lines += n
    return st


def _docs_summary(st: Status) -> dict[str, int]:
    from .docs import summary

    return summary(st.docs)


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


def to_json(project: Project, st: Status) -> dict[str, Any]:
    anchor = load_sync(project.root)["anchor"]
    c = st.counts()
    total = sum(c.values())
    annotated = total - c["pending"]
    fc = Counter(st.file_status.values())
    return {
        "schema": "osca-status/v2",
        "project": project.id,
        "granularity": "symbol",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor": {"commit": anchor["upstream_commit"], "ref": anchor.get("upstream_ref")},
        "files": {
            "in_scope": len(st.file_status),
            "translated": fc["translated"],
            "stale": fc["stale"],
            "pending": fc["pending"],
        },
        "symbols": {
            "in_scope": total,
            **{k: c[k] for k in ORDER},
            "stale_by_kind": dict(st.stale_kinds()),
            "orphaned": len(st.orphaned),
        },
        "zh_lines": st.zh_lines,
        "docs": _docs_summary(st),
        "coverage": _ratio(annotated, total),
        "fresh": _ratio(c["translated"] + c["reviewed"], total),
        "reviewed": _ratio(c["reviewed"], total),
    }


def write_json(project: Project, st: Status) -> Path:
    path = project.root / STATUS_FILE
    path.write_text(json.dumps(to_json(project, st), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def render(project: Project, st: Status, depth: int = 3) -> str:
    data = to_json(project, st)
    f, s = data["files"], data["symbols"]
    k = s["stale_by_kind"]
    lines = [
        f"{project.id}  @ {data['anchor']['ref']} ({data['anchor']['commit'][:9]})",
        "━" * 52,
        f"{'':<18}{'Files':>8}{'Symbols':>12}",
        f"{'In scope':<18}{f['in_scope']:>8,}{s['in_scope']:>12,}",
        f"{'Reviewed':<18}{'':>8}{s['reviewed']:>12,}",
        f"{'Translated':<18}{f['translated']:>8,}{s['translated']:>12,}",
        f"{'Stale':<18}{f['stale']:>8,}{s['stale']:>12,}"
        + (f"   (sig {k.get('sig', 0)} · doc {k.get('doc', 0)} · body {k.get('body', 0)})" if s["stale"] else ""),
        f"{'Pending':<18}{f['pending']:>8,}{s['pending']:>12,}",
        f"{'Orphaned':<18}{'':>8}{s['orphaned']:>12,}",
        "",
        f"Coverage {data['coverage']:.2%}   Fresh {data['fresh']:.2%}   Reviewed {data['reviewed']:.2%}   ({st.zh_lines} zh lines)",
    ]
    d = data["docs"]
    if st.docs:
        lines.append(
            f"Analysis docs {len(st.docs)}: current {d['current'] + d['new']} · stale {d['stale']}"
            f" · broken {d['broken']} · unanchored {d['unanchored']}"
        )
    groups: dict[str, Counter] = defaultdict(Counter)
    for r in st.counted():
        groups[group_of(r.sym.path, depth)][r.status] += 1
    touched = {g: c for g, c in groups.items() if sum(c.values()) - c["pending"]}
    if touched:
        lines += ["", f"{'Annotated groups':<40}{'done':>6}{'stale':>6}{'all':>7}"]
        for g, c in sorted(touched.items()):
            total = sum(c.values())
            lines.append(f"  {g:<38}{total - c['pending'] - c['stale']:>6}{c['stale']:>6}{total:>7}")
    return "\n".join(lines)


def queue(st: Status, which: str = "stale", prefix: str | None = None) -> list[Row]:
    rows = [
        r
        for r in st.rows
        if r.status == which and (r.sym.translatable or which != "pending") and (not prefix or r.sym.id.startswith(prefix))
    ]
    key = (lambda r: (r.priority, r.sym.id)) if which == "stale" else (lambda r: r.sym.id)
    return sorted(rows, key=key)
