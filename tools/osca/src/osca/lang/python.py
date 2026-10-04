"""Python symbols via tree-sitter.

Extents are trimmed line-wise (leading comments above, trailing comments below)
because tree-sitter-python attaches comments that precede a dedent to the
previous block.
"""

from __future__ import annotations

from functools import cache

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

from .common import Extracted, RawSymbol, byte_lines, indent_of, row_parts, span_parts

STRING_NODES = {"string", "string_content", "concatenated_string"}


@cache
def parser() -> Parser:
    return Parser(Language(tree_sitter_python.language()))


def parse(src: bytes) -> Tree:
    return parser().parse(src)


def _is_comment(line: bytes) -> bool:
    return line.lstrip().startswith(b"#")


def leading_comment_rows(lines: list[bytes], row: int) -> list[int]:
    """Contiguous comment rows directly above `row` at the same indentation."""
    ind = indent_of(lines[row])
    rows: list[int] = []
    r = row - 1
    while r >= 0 and _is_comment(lines[r]) and indent_of(lines[r]) == ind:
        rows.insert(0, r)
        r -= 1
    return rows


def trim_end(lines: list[bytes], start: int, end: int) -> int:
    while end > start and (not lines[end].strip() or _is_comment(lines[end])):
        end -= 1
    return end


def _docstring(block: Node | None) -> Node | None:
    if block is None or not block.named_children:
        return None
    first = block.named_children[0]
    if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
        return first
    return None


def extract(src: bytes, tree: Tree | None = None) -> Extracted:
    tree = tree or parse(src)
    lines = byte_lines(src)
    out: list[RawSymbol] = []

    def walk(block: Node, prefix: str, parent: int | None, in_class: bool) -> None:
        for n in block.named_children:
            defn = n.child_by_field_name("definition") if n.type == "decorated_definition" else n
            if defn is None or defn.type not in ("function_definition", "class_definition"):
                continue
            name_node = defn.child_by_field_name("name")
            if name_node is None:
                continue
            name = (name_node.text or b"").decode()
            is_class = defn.type == "class_definition"
            qual = f"{prefix}.{name}" if prefix else name
            body = defn.child_by_field_name("body")
            lead = leading_comment_rows(lines, n.start_point.row)
            doc_node = _docstring(body)
            doc_rows = set(range(doc_node.start_point.row, doc_node.end_point.row + 1)) if doc_node else set()
            body_parts = span_parts(lines, body.start_point, body.end_point) if body is not None else []
            sym = RawSymbol(
                kind="class" if is_class else ("method" if in_class else "fn"),
                qualname=qual,
                start=lead[0] if lead else n.start_point.row,
                item_row=n.start_point.row,
                end=trim_end(lines, n.start_point.row, n.end_point.row),
                sig=span_parts(lines, n.start_point, body.start_point if body is not None else n.end_point),
                doc=row_parts(lines, lead)
                + (span_parts(lines, doc_node.start_point, doc_node.end_point) if doc_node else []),
                body=[p for p in body_parts if p[0] not in doc_rows],
                parent=parent,
                container=is_class,
            )
            out.append(sym)
            if is_class and body is not None:
                walk(body, qual, len(out) - 1, True)

    walk(tree.root_node, "", None, False)
    doc = _docstring(tree.root_node)
    module_doc = span_parts(lines, doc.start_point, doc.end_point) if doc else []
    return Extracted(out, module_doc)


def lint_rows(src: bytes, rows: list[int], tree: Tree | None = None) -> list[tuple[int, str, str]]:
    tree = tree or parse(src)
    lines = byte_lines(src)
    issues: list[tuple[int, str, str]] = []
    for r in rows:
        col = indent_of(lines[r])
        anc = tree.root_node.descendant_for_point_range((r, col), (r, col))
        while anc is not None:
            if anc.type in STRING_NODES:
                issues.append((r, "zh-in-string", "annotation inside a string / docstring changes its value"))
                break
            anc = anc.parent
    return issues
