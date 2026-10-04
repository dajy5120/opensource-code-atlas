"""Spec-driven tree-sitter symbol extraction for brace languages (Go, TS/JS, C/C++).

Each language declares which node types are symbols, how to name them, which
ones contain further symbols, and which wrappers (TS `export`, C++ `template`)
to look through. Leading comments are adjacent comment siblings of the
outermost node, as in the Rust extractor.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache

from tree_sitter import Language, Node, Parser, Tree

from .common import Extracted, RawSymbol, byte_lines, span_parts


@dataclass
class Spec:
    language: Callable[[], object]
    items: dict[str, str]  # node type -> kind
    containers: frozenset[str] = frozenset()  # item types whose body holds more items
    passthrough: frozenset[str] = frozenset()  # non-item nodes whose body holds items (extern "C", namespaces w/o name)
    unwrap: dict[str, str] = field(default_factory=dict)  # wrapper type -> field ("" = last named child)
    name: Callable[[Node], str | None] | None = None
    body: Callable[[Node], Node | None] | None = None
    comments: frozenset[str] = frozenset({"comment"})
    strings: frozenset[str] = frozenset()
    sep: str = "."


def _text(n: Node | None) -> str:
    return " ".join((n.text or b"").decode("utf-8", "replace").split()) if n is not None else ""


_NAME = re.compile(r"^[A-Za-z_~(][\w:<>,~ *&=+\-!/%^|()\[\].]*$")
_KEYWORDS = {"namespace", "class", "struct", "template", "typedef", "return", "if", "for", "while", "switch"}


def _plausible(name: str | None) -> bool:
    return bool(name) and len(name) <= 120 and name not in _KEYWORDS and bool(_NAME.match(name))


def default_name(n: Node) -> str | None:
    return _text(n.child_by_field_name("name")) or None


def default_body(n: Node) -> Node | None:
    return n.child_by_field_name("body")


@cache
def _parser(language: Callable[[], object]) -> Parser:
    return Parser(Language(language()))


def parse(spec: Spec, src: bytes) -> Tree:
    return _parser(spec.language).parse(src)


def _end_row(n: Node) -> int:
    return n.end_point.row if n.end_point.column > 0 or n.end_point.row == n.start_point.row else n.end_point.row - 1


def _leading(spec: Spec, n: Node) -> list[Node]:
    out: list[Node] = []
    cur, prev = n, n.prev_sibling
    while prev is not None and prev.type in spec.comments:
        if _end_row(prev) < cur.start_point.row - 1:
            break
        out.insert(0, prev)
        cur, prev = prev, prev.prev_sibling
    return out


def _inner(spec: Spec, n: Node) -> Node:
    """Look through wrapper nodes (export_statement, template_declaration, …)."""
    while n.type in spec.unwrap:
        f = spec.unwrap[n.type]
        nxt = n.child_by_field_name(f) if f else (n.named_children[-1] if n.named_children else None)
        if nxt is None:
            break
        n = nxt
    return n


def extract(spec: Spec, src: bytes, tree: Tree | None = None) -> Extracted:
    tree = tree or parse(spec, src)
    lines = byte_lines(src)
    out: list[RawSymbol] = []
    name_of = spec.name or default_name
    body_of = spec.body or default_body

    def walk(container: Node, prefix: str, parent: int | None) -> None:
        for outer in container.named_children:
            n = _inner(spec, outer)
            if n.type in spec.passthrough:
                b = body_of(n)
                if b is not None:
                    walk(b, prefix, parent)
                continue
            if n.type not in spec.items:
                continue
            name = name_of(n)
            if n.has_error or not _plausible(name):
                # parse error (typically an unexpanded macro): keep the contents, drop the item
                b = body_of(n) if n.type in spec.containers else None
                if b is not None:
                    walk(b, prefix, parent)
                continue
            lead = _leading(spec, outer)
            body = body_of(n)
            qual = f"{prefix}{spec.sep}{name}" if prefix else name
            out.append(
                RawSymbol(
                    kind=spec.items[n.type],
                    qualname=qual,
                    start=lead[0].start_point.row if lead else outer.start_point.row,
                    item_row=outer.start_point.row,
                    end=outer.end_point.row,
                    sig=span_parts(lines, outer.start_point, body.start_point if body is not None else outer.end_point),
                    doc=[p for c in lead for p in span_parts(lines, c.start_point, c.end_point)],
                    body=span_parts(lines, body.start_point, body.end_point) if body is not None else [],
                    parent=parent,
                    container=n.type in spec.containers,
                )
            )
            if n.type in spec.containers and body is not None:
                walk(body, qual, len(out) - 1)

    walk(tree.root_node, "", None)
    # file header comments directly at the top serve as module documentation
    module_doc = []
    for c in tree.root_node.named_children:
        if c.type not in spec.comments:
            break
        module_doc += span_parts(lines, c.start_point, c.end_point)
    return Extracted(out, module_doc)


def lint_rows(spec: Spec, src: bytes, rows: list[int], tree: Tree | None = None) -> list[tuple[int, str, str]]:
    tree = tree or parse(spec, src)
    lines = byte_lines(src)
    issues = []
    for r in rows:
        line = lines[r]
        col = len(line) - len(line.lstrip(b" \t"))
        anc = tree.root_node.descendant_for_point_range((r, col), (r, col))
        while anc is not None:
            if anc.type in spec.strings:
                issues.append((r, "zh-in-string", "annotation inside a string literal changes its value"))
                break
            anc = anc.parent
    return issues


class LanguageModule:
    """Adapter exposing the extract / lint_rows / parse interface used by symbols.py and verify.py."""

    def __init__(self, spec: Spec):
        self.spec = spec

    def parse(self, src: bytes) -> Tree:
        return parse(self.spec, src)

    def extract(self, src: bytes, tree: Tree | None = None) -> Extracted:
        return extract(self.spec, src, tree)

    def lint_rows(self, src: bytes, rows: list[int], tree: Tree | None = None) -> list[tuple[int, str, str]]:
        return lint_rows(self.spec, src, rows, tree)
