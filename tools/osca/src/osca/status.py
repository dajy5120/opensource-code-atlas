"""Translation status (Phase 1: file granularity).

A file in scope is `translated` when it contains at least one annotation line,
otherwise `pending`. Symbol-level states (stale / reviewed / orphaned) arrive
with the Tree-sitter index in Phase 2.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import gitutil
from .markers import comment_tokens, count_annotations
from .project import STATUS_FILE, Project, load_sync


@dataclass
class Bucket:
    files: int = 0
    translated: int = 0
    zh_lines: int = 0

    @property
    def coverage(self) -> float:
        return self.translated / self.files if self.files else 0.0


@dataclass
class Status:
    total: Bucket = field(default_factory=Bucket)
    groups: dict[str, Bucket] = field(default_factory=lambda: defaultdict(Bucket))
    translated_files: list[str] = field(default_factory=list)


def group_of(path: str, depth: int = 2) -> str:
    parts = path.split("/")
    return "/".join(parts[: min(depth, len(parts) - 1)]) or "."


def compute(project: Project, depth: int = 2) -> Status:
    root = project.root
    st = Status()
    for path in gitutil.split_z(gitutil.git(root, "ls-files", "-z")):
        tokens = comment_tokens(path)
        if tokens is None or not project.in_scope(path):
            continue
        full = root / path
        if not full.is_file() or full.is_symlink():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        n = count_annotations(text, tokens, project.marker)
        for b in (st.total, st.groups[group_of(path, depth)]):
            b.files += 1
            b.zh_lines += n
            b.translated += n > 0
        if n:
            st.translated_files.append(path)
    return st


def to_json(project: Project, st: Status) -> dict[str, Any]:
    anchor = load_sync(project.root)["anchor"]
    return {
        "schema": "osca-status/v1",
        "project": project.id,
        "granularity": "file",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "anchor": {"commit": anchor["upstream_commit"], "ref": anchor.get("upstream_ref")},
        "files": {
            "in_scope": st.total.files,
            "translated": st.total.translated,
            "pending": st.total.files - st.total.translated,
        },
        "zh_lines": st.total.zh_lines,
        "coverage": round(st.total.coverage, 4),
        "groups": {
            g: {"files": b.files, "translated": b.translated, "zh_lines": b.zh_lines}
            for g, b in sorted(st.groups.items())
        },
    }


def write_json(project: Project, st: Status) -> Path:
    path = project.root / STATUS_FILE
    path.write_text(json.dumps(to_json(project, st), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def render(project: Project, st: Status, show_groups: bool = True) -> str:
    anchor = load_sync(project.root)["anchor"]
    ref = anchor.get("upstream_ref") or ""
    t = st.total
    lines = [
        f"{project.id}  @ {ref} ({anchor['upstream_commit'][:9]})",
        "━" * 52,
        f"{'Files in scope':<18}{t.files:>8,}",
        f"{'Translated':<18}{t.translated:>8,}",
        f"{'Pending':<18}{t.files - t.translated:>8,}",
        f"{'zh lines':<18}{t.zh_lines:>8,}",
        "",
        f"Coverage  {t.coverage:.2%}  (by file; symbol-level status arrives in Phase 2)",
    ]
    if show_groups:
        touched = {g: b for g, b in st.groups.items() if b.translated}
        if touched:
            lines += ["", "Translated groups:"]
            for g, b in sorted(touched.items()):
                lines.append(f"  {g:<40}{b.translated:>5}/{b.files:<5} {b.coverage:>7.1%}")
    return "\n".join(lines)
