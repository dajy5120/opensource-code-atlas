"""Cross-file context for translation: definitions of types a file uses but does not define.

Translators kept noting "X is defined elsewhere, so this is inferred". We index
type-like symbols across the translation scope and attach the (truncated)
definitions referenced by the target symbols.
"""

from __future__ import annotations

import re
from pathlib import Path

from .index import index_worktree
from .project import Project
from .symbols import FileIndex, Symbol

TYPE_KINDS = {"struct", "enum", "union", "trait", "type", "class", "interface"}
IDENT = re.compile(r"\b[A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*\b")  # CamelCase, not ALL_CAPS consts
MAX_DEFS = 12
MAX_LINES = 30


def type_index(project: Project) -> dict[str, list[Symbol]]:
    """Short type name -> symbols defining it, across the whole translation scope."""
    out: dict[str, list[Symbol]] = {}
    for idx in index_worktree(project).values():
        for s in idx.symbols.values():
            if s.kind in TYPE_KINDS:
                out.setdefault(s.qualname.rsplit("::", 1)[-1].rsplit(".", 1)[-1], []).append(s)
    return out


def _definition(root: Path, sym: Symbol, marker: str) -> str:
    lines = (root / sym.path).read_text(encoding="utf-8").split("\n")
    body = [lines[r] for r in range(sym.start, sym.end + 1) if marker not in lines[r]]
    if len(body) > MAX_LINES:
        body = body[:MAX_LINES] + ["    … (truncated)"]
    return "\n".join(body)


def related_definitions(
    project: Project, idx: FileIndex, text: str, targets: list[Symbol], types: dict[str, list[Symbol]]
) -> str:
    lines = text.split("\n")
    used: dict[str, int] = {}
    for s in targets:
        for r in range(s.start, s.end + 1):
            for name in IDENT.findall(lines[r]):
                used[name] = used.get(name, 0) + 1
    local = {s.qualname.rsplit("::", 1)[-1].rsplit(".", 1)[-1] for s in idx.symbols.values()}
    picked: list[Symbol] = []
    for name, _ in sorted(used.items(), key=lambda kv: -kv[1]):
        if name in local or name not in types:
            continue
        cands = [s for s in types[name] if s.path != idx.path]
        if len(cands) == 1 or (cands and len({c.path for c in cands}) == 1):  # skip ambiguous names
            picked.append(cands[0])
        if len(picked) >= MAX_DEFS:
            break
    blocks = [f"// {s.path}:{s.line}\n{_definition(project.root, s, project.marker)}" for s in picked]
    return "\n\n".join(blocks)
