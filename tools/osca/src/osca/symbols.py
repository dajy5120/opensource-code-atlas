"""Symbol index: identity, content hashes and annotation attachment.

For every symbol we hash three normalised texts with all `【zh】` rows removed:

- sig:  attributes / decorators + declaration header (API surface)
- doc:  leading comments, doc comments, docstrings (the English original)
- body: implementation; for containers (impl / trait / mod / class) the body
        with nested symbols replaced by placeholders, so a change inside one
        method does not make the whole class stale

Because annotation rows are dropped and whitespace is normalised, a study file
and its upstream original hash identically, and pure reformatting is ignored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from . import lang
from .lang.common import Part
from .markers import DEFAULT_MARKER, comment_tokens, is_annotation

MODULE = "<module>"
DEFAULT_POLICY = {"min_body_lines": 5, "exclude_kinds": ["impl"]}


def digest(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:12]


@dataclass
class Symbol:
    id: str
    path: str
    kind: str
    qualname: str
    start: int  # 0-based rows in the indexed text
    item_row: int
    end: int
    sig: str
    doc: str
    body: str
    body_lines: int
    translatable: bool
    parent: str | None = None
    zh: str | None = None  # hash of attached annotation text
    zh_rows: list[int] = field(default_factory=list)

    @property
    def hashes(self) -> dict[str, str]:
        return {"sig": self.sig, "doc": self.doc, "body": self.body}

    @property
    def line(self) -> int:
        return self.item_row + 1


@dataclass
class FileIndex:
    path: str
    symbols: dict[str, Symbol]
    zh_rows: list[int]

    @property
    def annotated(self) -> list[Symbol]:
        return [s for s in self.symbols.values() if s.zh]


def supported(path: str) -> bool:
    return lang.for_path(path) is not None


def annotation_rows(path: str, text: str, marker: str = DEFAULT_MARKER) -> list[int]:
    tokens = comment_tokens(path)
    if tokens is None:
        return []
    return [i for i, line in enumerate(text.split("\n")) if is_annotation(line, tokens, marker)]


def _join(parts: list[Part], skip: set[int]) -> str:
    return "\n".join(t for r, t in parts if r not in skip)


def index_text(
    path: str, text: str, marker: str = DEFAULT_MARKER, policy: dict | None = None
) -> FileIndex | None:
    mod = lang.for_path(path)
    if mod is None:
        return None
    policy = {**DEFAULT_POLICY, **(policy or {})}
    src = text.encode("utf-8")
    zh_rows = annotation_rows(path, text, marker)
    zh_set = set(zh_rows)
    ex = mod.extract(src)
    raws = ex.symbols
    lines = text.split("\n")

    children: dict[int, list[int]] = {}
    for i, r in enumerate(raws):
        if r.parent is not None:
            children.setdefault(r.parent, []).append(i)

    # stable ids, disambiguating duplicates (e.g. #[cfg] variants) with ~N
    ids: list[str] = []
    seen: dict[str, int] = {}
    for r in raws:
        base = f"{path}#{r.qualname}"
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}~{seen[base]}")

    symbols: dict[str, Symbol] = {}
    for i, r in enumerate(raws):
        skip = set(zh_set)
        body_parts = r.body
        if r.container:
            body_parts = list(body_parts)
            for c in children.get(i, []):
                cr = raws[c]
                rows = set(range(cr.start, cr.end + 1))
                body_parts = [p for p in body_parts if p[0] not in rows or p[0] == cr.item_row]
                body_parts = [(p[0], f"⟨{cr.kind} {cr.qualname}⟩") if p[0] == cr.item_row else p for p in body_parts]
        body_text = _join(body_parts, skip)
        doc_text = _join(r.doc, skip)
        body_lines = sum(1 for row, t in r.body if row not in skip and t.strip())
        kind_ok = r.kind not in policy["exclude_kinds"]
        translatable = kind_ok and (
            r.kind in ("struct", "enum", "union", "trait", "class", "mod")
            or bool(doc_text.strip())
            or body_lines >= policy["min_body_lines"]
        )
        symbols[ids[i]] = Symbol(
            id=ids[i],
            path=path,
            kind=r.kind,
            qualname=r.qualname,
            start=r.start,
            item_row=r.item_row,
            end=r.end,
            sig=digest(_join(r.sig, skip)),
            doc=digest(doc_text),
            body=digest(body_text),
            body_lines=body_lines,
            translatable=translatable,
            parent=ids[r.parent] if r.parent is not None else None,
        )

    top = [f"{raws[i].kind} {raws[i].qualname}" for i in range(len(raws)) if raws[i].parent is None]
    module = Symbol(
        id=f"{path}#{MODULE}",
        path=path,
        kind="module",
        qualname=MODULE,
        start=0,
        item_row=0,
        end=max(len(lines) - 1, 0),
        sig=digest(""),
        doc=digest(_join(ex.module_doc, zh_set)),
        body=digest("\n".join(top)),
        body_lines=0,
        translatable=True,  # every file deserves an overview
    )
    symbols = {module.id: module, **symbols}

    _attach(symbols, zh_rows, lines, marker, path)
    return FileIndex(path, symbols, zh_rows)


def _blocks(rows: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for r in rows:
        if out and out[-1][-1] == r - 1:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def annotation_body(line: str, marker: str) -> str:
    return line.split(marker, 1)[1].strip() if marker in line else line.strip()


def owner(symbols: dict[str, Symbol], row: int) -> Symbol:
    """Innermost symbol whose extent contains `row` (the module if none)."""
    best: Symbol | None = None
    for s in symbols.values():
        if s.kind == "module" or not (s.start <= row <= s.end):
            continue
        if best is None or (s.end - s.start) < (best.end - best.start):
            best = s
    return best or next(iter(symbols.values()))


def _attach(symbols: dict[str, Symbol], zh_rows: list[int], lines: list[str], marker: str, path: str) -> None:
    texts: dict[str, list[str]] = {}
    for block in _blocks(zh_rows):
        sym = owner(symbols, block[0])
        sym.zh_rows.extend(block)
        texts.setdefault(sym.id, []).extend(annotation_body(lines[r], marker) for r in block)
    for sid, parts in texts.items():
        symbols[sid].zh = digest("\n".join(parts))
