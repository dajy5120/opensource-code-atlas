"""Symbol-level diff between two upstream revisions."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import gitutil
from .index import index_rev
from .project import Project
from .state import KINDS, all_symbols
from .symbols import Symbol, supported


@dataclass
class SymbolDiff:
    files: list[str] = field(default_factory=list)
    added: list[Symbol] = field(default_factory=list)
    removed: list[Symbol] = field(default_factory=list)
    modified: list[tuple[Symbol, Symbol, list[str]]] = field(default_factory=list)
    renamed: list[tuple[Symbol, Symbol]] = field(default_factory=list)


def changed_files(project: Project, old: str, new: str) -> list[str]:
    out = gitutil.git(project.root, "diff", "--name-only", "--no-renames", old, new).splitlines()
    return [f for f in out if project.in_scope(f) and supported(f)]


def diff_revs(project: Project, old: str, new: str) -> SymbolDiff:
    files = changed_files(project, old, new)
    a = all_symbols(index_rev(project, old, files))
    b = all_symbols(index_rev(project, new, files))
    d = SymbolDiff(files=files)
    removed = [a[k] for k in a if k not in b]
    added = [b[k] for k in b if k not in a]
    for k in a.keys() & b.keys():
        changed = [x for x in KINDS if getattr(a[k], x) != getattr(b[k], x)]
        if changed:
            d.modified.append((a[k], b[k], changed))

    # renames / moves: same kind and identical non-trivial body
    by_body: dict[tuple[str, str], list[Symbol]] = {}
    for s in added:
        if s.kind != "module" and s.body_lines > 0:
            by_body.setdefault((s.kind, s.body), []).append(s)
    for s in removed:
        cands = by_body.get((s.kind, s.body))
        if s.kind != "module" and s.body_lines > 0 and cands:
            t = cands.pop(0)
            d.renamed.append((s, t))
            added.remove(t)
        else:
            d.removed.append(s)
    d.added = added
    d.modified.sort(key=lambda m: m[1].id)
    return d
