"""Deterministic conflict resolution for sync merges.

By the strip invariant our side of any upstream file is `upstream_old + 【zh】`.
So the merged code is always *their* side (upstream_new); only annotations need
placing. Each annotation block is re-attached to the same symbol in the new
upstream text, right before the code line it originally preceded. Blocks whose
anchor line vanished are relocated to the symbol head; blocks whose symbol
vanished are orphaned (reported, not inserted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitutil
from .markers import comment_tokens
from .symbols import MODULE, _blocks, index_text, owner


@dataclass
class Outcome:
    path: str
    reattached: int = 0
    relocated: list[str] = field(default_factory=list)  # symbol ids
    orphaned: list[dict] = field(default_factory=list)  # {"symbol", "lines"}
    deleted_upstream: bool = False


def _stage(root: Path, n: int, path: str) -> str | None:
    data = gitutil.git_bytes(root, "show", f":{n}:{path}", check=False)
    if not data and not gitutil.git_ok(root, "cat-file", "-e", f":{n}:{path}"):
        return None
    return data.decode("utf-8")


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _next_code(lines: list[str], zh: set[int], row: int, step: int) -> str | None:
    r = row + step
    while 0 <= r < len(lines):
        if r not in zh:
            return lines[r]
        r += step
    return None


def place(path: str, ours: str, theirs: str, marker: str) -> tuple[str, Outcome]:
    """Return their text with our annotation blocks re-attached."""
    out = Outcome(path)
    o_idx = index_text(path, ours, marker)
    t_idx = index_text(path, theirs, marker)
    o_lines, t_lines = ours.split("\n"), theirs.split("\n")
    zh = set(o_idx.zh_rows)
    inserts: list[tuple[int, list[str]]] = []

    for block in _blocks(o_idx.zh_rows):
        sym = owner(o_idx.symbols, block[0])
        raw = [o_lines[r].lstrip(" \t") for r in block]
        target = t_idx.symbols.get(sym.id)
        if target is None:
            out.orphaned.append({"symbol": sym.id, "lines": raw})
            continue
        nxt = _next_code(o_lines, zh, block[-1], 1)
        prv = _next_code(o_lines, zh, block[0], -1)
        offset = sum(1 for r in range(sym.start, block[0]) if r not in zh)
        lo, hi = (0, len(t_lines) - 1) if sym.qualname == MODULE else (target.start, target.end)
        best: int | None = None
        if nxt is not None and nxt.strip():
            cands = [r for r in range(lo, hi + 1) if t_lines[r].strip() == nxt.strip()]
            if cands:
                exact = [r for r in cands if prv is not None and r > 0 and t_lines[r - 1].strip() == prv.strip()]
                pool = exact or cands
                best = min(pool, key=lambda r: abs((r - target.start) - offset))
        if best is None and prv is not None and prv.strip():
            # following line vanished or is blank: attach right after the preceding line
            cands = [r + 1 for r in range(lo, hi + 1) if t_lines[r].strip() == prv.strip()]
            if cands:
                best = min(cands, key=lambda r: abs((r - target.start) - offset))
        if best is not None:
            out.reattached += 1
            ind = _indent(t_lines[best]) if best < len(t_lines) and t_lines[best].strip() else _indent(nxt or "")
            inserts.append((best, [ind + l for l in raw]))
            continue
        # anchor line vanished: put the block at the head of its symbol
        out.relocated.append(sym.id)
        head = target.item_row if sym.qualname != MODULE else 0
        inserts.append((head, [_indent(t_lines[head]) + l for l in raw]))

    # bottom-up; for equal positions insert later blocks first so the original order is kept
    for _, at, new in sorted(((i, at, new) for i, (at, new) in enumerate(inserts)), key=lambda x: (x[1], x[0]), reverse=True):
        t_lines[at:at] = new
    return "\n".join(t_lines), out


def resolve_file(root: Path, path: str, marker: str) -> Outcome | None:
    """Resolve one conflicted file in place. None if it cannot be handled."""
    ours, theirs = _stage(root, 2, path), _stage(root, 3, path)
    if ours is None:
        return None
    if theirs is None:  # upstream deleted the file: annotations are orphaned
        out = Outcome(path, deleted_upstream=True)
        gitutil.git(root, "rm", "-q", "--", path)
        return out
    if comment_tokens(path) is None or index_text(path, ours, marker) is None:
        base = _stage(root, 1, path)
        if base is not None and base != ours:
            return None  # unsupported file we changed: needs a human
        (root / path).write_text(theirs, encoding="utf-8")
        gitutil.git(root, "add", "--", path)
        return Outcome(path)
    merged, out = place(path, ours, theirs, marker)
    (root / path).write_text(merged, encoding="utf-8")
    gitutil.git(root, "add", "--", path)
    return out
