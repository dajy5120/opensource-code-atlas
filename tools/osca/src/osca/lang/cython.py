"""Cython (.pyx / .pxd) symbols by indentation, since tree-sitter-python cannot parse cdef syntax."""

from __future__ import annotations

import re

from .common import Extracted, RawSymbol, byte_lines, indent_of, row_parts
from .python import leading_comment_rows

HEADER = re.compile(rb"^[ \t]*(?:async[ \t]+)?(def|cpdef|cdef|class)\b(.*)$")
CLASS = re.compile(rb"^(?:[ \t]+(?:public|api))*[ \t]+class[ \t]+(\w+)")
STRUCT = re.compile(rb"^[ \t]+(struct|enum|union)[ \t]+(\w+)")
FUNC = re.compile(rb"(\w+)[ \t]*\(")
TRIPLE = re.compile(rb'"""|\'\'\'')


def string_rows(lines: list[bytes]) -> set[int]:
    """Rows that start inside a triple-quoted string (naive but adequate)."""
    rows: set[int] = set()
    open_q: bytes | None = None
    for r, line in enumerate(lines):
        if open_q:
            rows.add(r)
        for m in TRIPLE.finditer(line):
            q = m.group(0)
            if open_q is None:
                open_q = q
            elif q == open_q:
                open_q = None
    return rows


def _classify(kw: bytes, rest: bytes) -> tuple[str, str] | None:
    if kw == b"class":
        m = re.match(rb"[ \t]+(\w+)", rest)
        return ("class", m.group(1).decode()) if m else None
    if kw == b"cdef":
        if m := CLASS.match(rest):
            return "class", m.group(1).decode()
        if m := STRUCT.match(rest):
            return m.group(1).decode(), m.group(2).decode()
    if b"(" not in rest:
        return None
    m = FUNC.search(rest)
    return ("fn", m.group(1).decode()) if m else None


def _header_end(lines: list[bytes], row: int) -> tuple[int, bool]:
    """Last row of a (possibly multi-line) header and whether it opens a block."""
    depth = 0
    r = row
    while r < len(lines):
        code = lines[r].split(b"#", 1)[0]
        depth += code.count(b"(") + code.count(b"[") - code.count(b")") - code.count(b"]")
        if depth <= 0:
            return r, code.rstrip().endswith(b":")
        r += 1
    return len(lines) - 1, False


def extract(src: bytes, tree: object = None) -> Extracted:
    lines = byte_lines(src)
    in_str = string_rows(lines)
    out: list[RawSymbol] = []
    stack: list[tuple[int, int, int]] = []  # (indent, end_row, index) of open classes

    r = 0
    while r < len(lines):
        line = lines[r]
        m = HEADER.match(line) if r not in in_str else None
        kind_name = _classify(m.group(1), m.group(2)) if m else None
        if not kind_name:
            r += 1
            continue
        kind, name = kind_name
        ind = indent_of(line)
        while stack and (ind <= stack[-1][0] or r > stack[-1][1]):
            stack.pop()
        hdr_end, block = _header_end(lines, r)
        end = hdr_end
        if block:
            k = hdr_end + 1
            while k < len(lines):
                s = lines[k].strip()
                if s and not s.startswith(b"#") and k not in in_str and indent_of(lines[k]) <= ind:
                    break
                if s and not s.startswith(b"#"):
                    end = k
                k += 1
        start = r
        while start > 0 and lines[start - 1].lstrip().startswith(b"@") and indent_of(lines[start - 1]) == ind:
            start -= 1
        lead = leading_comment_rows(lines, start)
        doc_rows: list[int] = []
        k = hdr_end + 1
        while k <= end and not lines[k].strip():
            k += 1
        if block and k <= end and re.match(rb'[ \t]*[rRuUbB]?("""|\'\'\')', lines[k]):
            doc_rows.append(k)
            q = re.match(rb'[ \t]*[rRuUbB]?("""|\'\'\')', lines[k]).group(1)
            if lines[k].count(q) < 2:
                k += 1
                while k <= end and q not in lines[k]:
                    doc_rows.append(k)
                    k += 1
                doc_rows.append(k)
        parent = stack[-1][2] if stack else None
        prefix = out[parent].qualname if parent is not None else ""
        if parent is not None and kind == "fn":
            kind = "method"
        out.append(
            RawSymbol(
                kind=kind,
                qualname=f"{prefix}.{name}" if prefix else name,
                start=lead[0] if lead else start,
                item_row=start,
                end=end,
                sig=row_parts(lines, range(start, hdr_end + 1)),
                doc=row_parts(lines, lead) + row_parts(lines, doc_rows),
                body=[p for p in row_parts(lines, range(hdr_end + 1, end + 1)) if p[0] not in set(doc_rows)],
                parent=parent,
                container=kind == "class",
            )
        )
        if kind == "class" and block:
            stack.append((ind, end, len(out) - 1))
            r = hdr_end + 1
        else:
            r = end + 1
    return Extracted(out, [])


def lint_rows(src: bytes, rows: list[int], tree: object = None) -> list[tuple[int, str, str]]:
    in_str = string_rows(byte_lines(src))
    return [
        (r, "zh-in-string", "annotation inside a triple-quoted string changes its value")
        for r in rows
        if r in in_str
    ]
