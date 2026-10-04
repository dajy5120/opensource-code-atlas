"""Rust symbols via tree-sitter."""

from __future__ import annotations

from functools import cache

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Tree

from .common import Extracted, RawSymbol, byte_lines, span_parts

ITEMS = {
    "function_item": "fn",
    "function_signature_item": "fn",
    "struct_item": "struct",
    "enum_item": "enum",
    "union_item": "union",
    "trait_item": "trait",
    "impl_item": "impl",
    "mod_item": "mod",
    "type_item": "type",
    "const_item": "const",
    "static_item": "static",
    "macro_definition": "macro",
}
CONTAINERS = {"impl_item", "trait_item", "mod_item"}
LEADING = {"line_comment", "block_comment", "attribute_item"}
STRING_NODES = {"string_literal", "raw_string_literal", "string_content", "char_literal"}


@cache
def parser() -> Parser:
    return Parser(Language(tree_sitter_rust.language()))


def parse(src: bytes) -> Tree:
    return parser().parse(src)


def _end_row(n: Node) -> int:
    # Line comments include their newline and end at column 0 of the next row.
    return n.end_point.row if n.end_point.column > 0 or n.end_point.row == n.start_point.row else n.end_point.row - 1


def _is_inner_doc(n: Node) -> bool:
    return n.type == "line_comment" and n.text is not None and n.text.startswith(b"//!")


def _leading(n: Node) -> list[Node]:
    out: list[Node] = []
    cur, prev = n, n.prev_sibling
    while prev is not None and prev.type in LEADING and not _is_inner_doc(prev):
        if _end_row(prev) < cur.start_point.row - 1:
            break
        out.insert(0, prev)
        cur, prev = prev, prev.prev_sibling
    return out


def _text(n: Node | None) -> str:
    return " ".join((n.text or b"").decode("utf-8", "replace").split()) if n is not None else ""


def _name(n: Node) -> str | None:
    if n.type == "impl_item":
        ty, tr = _text(n.child_by_field_name("type")), _text(n.child_by_field_name("trait"))
        return f"impl {tr} for {ty}" if tr else f"impl {ty}"
    name = n.child_by_field_name("name")
    return _text(name) or None


def _is_test(n: Node, lead: list[Node]) -> bool:
    if n.type == "mod_item" and _name(n) == "tests":
        return True
    return any(a.type == "attribute_item" and b"cfg(test)" in (a.text or b"").replace(b" ", b"") for a in lead)


def extract(src: bytes, tree: Tree | None = None) -> Extracted:
    tree = tree or parse(src)
    lines = byte_lines(src)
    out: list[RawSymbol] = []

    def walk(container: Node, prefix: str, parent: int | None) -> None:
        for n in container.named_children:
            if n.type not in ITEMS:
                continue
            body = n.child_by_field_name("body")
            if n.type == "mod_item" and body is None:
                continue  # `mod foo;` declarations
            lead = _leading(n)
            if _is_test(n, lead):
                continue
            name = _name(n)
            if not name:
                continue
            qual = f"{prefix}::{name}" if prefix else name
            sig_end = body.start_point if body is not None else n.end_point
            sym = RawSymbol(
                kind=ITEMS[n.type],
                qualname=qual,
                start=lead[0].start_point.row if lead else n.start_point.row,
                item_row=n.start_point.row,
                end=n.end_point.row,
                sig=[p for a in lead if a.type == "attribute_item" for p in span_parts(lines, a.start_point, a.end_point)]
                + span_parts(lines, n.start_point, sig_end),
                doc=[p for c in lead if c.type != "attribute_item" for p in span_parts(lines, c.start_point, c.end_point)],
                body=span_parts(lines, body.start_point, body.end_point) if body is not None else [],
                parent=parent,
                container=n.type in CONTAINERS,
            )
            out.append(sym)
            if n.type in CONTAINERS and body is not None:
                walk(body, qual, len(out) - 1)

    walk(tree.root_node, "", None)
    module_doc = [
        p
        for c in tree.root_node.named_children
        if _is_inner_doc(c)
        for p in span_parts(lines, c.start_point, c.end_point)
    ]
    return Extracted(out, module_doc)


def lint_rows(src: bytes, rows: list[int], tree: Tree | None = None) -> list[tuple[int, str, str]]:
    """Placement problems for annotation rows: (row, code, message)."""
    tree = tree or parse(src)
    lines = byte_lines(src)
    issues: list[tuple[int, str, str]] = []
    for r in rows:
        line = lines[r]
        col = len(line) - len(line.lstrip(b" \t"))
        node = tree.root_node.descendant_for_point_range((r, col), (r, col))
        anc = node
        while anc is not None:
            if anc.type in STRING_NODES:
                issues.append((r, "zh-in-string", "annotation inside a string literal changes its value"))
                break
            anc = anc.parent
        else:
            stripped = line.lstrip()
            if stripped.startswith(b"///") or stripped.startswith(b"//!"):
                comment = node
                while comment is not None and comment.type != "line_comment":
                    comment = comment.parent
                if comment is not None and comment.parent is not None and comment.parent.type == "block":
                    issues.append((r, "doc-in-body", "doc comment inside a function body; use `// 【zh】`"))
                if _in_fence(lines, r):
                    issues.append((r, "zh-in-code-block", "annotation inside a rustdoc code block changes a doctest"))
    return issues


def _in_fence(lines: list[bytes], row: int) -> bool:
    """True if `row` lies inside a ``` fence of its contiguous doc-comment block."""
    token = lines[row].lstrip()[:3]
    r = row - 1
    fences = 0
    while r >= 0 and lines[r].lstrip().startswith(token):
        if lines[r].lstrip()[3:].strip().startswith(b"```"):
            fences += 1
        r -= 1
    return fences % 2 == 1


def test_ranges(src: bytes) -> list[tuple[int, int]]:
    """Row ranges (inclusive) of `#[cfg(test)]` items and `mod tests` blocks, with their attributes."""
    tree = parse(src)
    out = []
    for n in tree.root_node.named_children:
        if n.type not in ITEMS:
            continue
        lead = _leading(n)
        if _is_test(n, lead):
            out.append((lead[0].start_point.row if lead else n.start_point.row, n.end_point.row))
    return out
