"""Language-neutral symbol extraction primitives.

Extractors return `RawSymbol`s whose text is given as (row, text) parts. The
indexer later drops annotation rows from those parts before hashing, so a study
file and its upstream original produce identical hashes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

Part = tuple[int, str]


@dataclass
class RawSymbol:
    kind: str
    qualname: str
    start: int  # first row of the extent, including leading docs / attributes / decorators
    item_row: int  # first row of the item itself
    end: int  # last row, inclusive
    sig: list[Part] = field(default_factory=list)
    doc: list[Part] = field(default_factory=list)
    body: list[Part] = field(default_factory=list)
    parent: int | None = None  # index into the extractor's result list
    container: bool = False  # body hash replaces child extents by placeholders


@dataclass
class Extracted:
    symbols: list[RawSymbol]
    module_doc: list[Part]


def byte_lines(src: bytes) -> list[bytes]:
    return src.split(b"\n")


def span_parts(lines: list[bytes], start: tuple[int, int], end: tuple[int, int]) -> list[Part]:
    """Text between two (row, byte-column) points, as (row, text) parts."""
    (r0, c0), (r1, c1) = start, end
    parts: list[Part] = []
    for r in range(r0, min(r1, len(lines) - 1) + 1):
        line = lines[r]
        a = c0 if r == r0 else 0
        b = c1 if r == r1 else len(line)
        parts.append((r, line[a:b].decode("utf-8", "replace")))
    return parts


def row_parts(lines: list[bytes], rows: range | list[int]) -> list[Part]:
    return [(r, lines[r].decode("utf-8", "replace")) for r in rows if 0 <= r < len(lines)]


def indent_of(line: bytes) -> int:
    return len(line) - len(line.lstrip(b" \t"))
