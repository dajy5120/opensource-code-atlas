"""Language specs for Go, TypeScript / JavaScript and C / C++."""

from __future__ import annotations

import tree_sitter_c
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Node

from .generic import LanguageModule, Spec, _text, default_body, default_name

# --- Go -------------------------------------------------------------------------------------


def _go_name(n: Node) -> str | None:
    if n.type == "type_declaration":
        spec = next((c for c in n.named_children if c.type in ("type_spec", "type_alias")), None)
        return _text(spec.child_by_field_name("name")) if spec else None
    if n.type == "method_declaration":
        recv = n.child_by_field_name("receiver")
        types = [c for c in _walk(recv) if c.type == "type_identifier"] if recv else []
        base = (types[-1].text or b"").decode() if types else "?"
        return f"{base}.{_text(n.child_by_field_name('name'))}"
    return default_name(n)


def _go_body(n: Node) -> Node | None:
    if n.type == "type_declaration":
        spec = next((c for c in n.named_children if c.type in ("type_spec", "type_alias")), None)
        return spec.child_by_field_name("type") if spec else None
    return default_body(n)


def _walk(n: Node):
    yield n
    for c in n.named_children:
        yield from _walk(c)


GO = Spec(
    language=tree_sitter_go.language,
    items={"function_declaration": "fn", "method_declaration": "fn", "type_declaration": "type"},
    name=_go_name,
    body=_go_body,
    strings=frozenset({"interpreted_string_literal", "raw_string_literal"}),
)


# --- TypeScript / JavaScript ------------------------------------------------------------------


def _ts_name(n: Node) -> str | None:
    if n.type == "lexical_declaration":
        decl = next((c for c in n.named_children if c.type == "variable_declarator"), None)
        if decl is None:
            return None
        value = decl.child_by_field_name("value")
        if value is None or value.type not in ("arrow_function", "function_expression", "function", "class"):
            return None  # only `const f = () => …` style definitions are symbols
        return _text(decl.child_by_field_name("name"))
    return default_name(n)


def _ts_body(n: Node) -> Node | None:
    if n.type == "lexical_declaration":
        decl = next((c for c in n.named_children if c.type == "variable_declarator"), None)
        value = decl.child_by_field_name("value") if decl else None
        return value.child_by_field_name("body") if value is not None else None
    return default_body(n)


_TS_ITEMS = {
    "function_declaration": "fn",
    "generator_function_declaration": "fn",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "method_definition": "method",
    "lexical_declaration": "fn",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "internal_module": "mod",
}
_TS_COMMON = dict(
    containers=frozenset({"class_declaration", "abstract_class_declaration", "internal_module"}),
    unwrap={"export_statement": "declaration", "expression_statement": ""},
    name=_ts_name,
    body=_ts_body,
    strings=frozenset({"string", "template_string", "string_fragment"}),
)
TYPESCRIPT = Spec(language=tree_sitter_typescript.language_typescript, items=_TS_ITEMS, **_TS_COMMON)
TSX = Spec(language=tree_sitter_typescript.language_tsx, items=_TS_ITEMS, **_TS_COMMON)
JAVASCRIPT = Spec(
    language=tree_sitter_javascript.language,
    items={k: v for k, v in _TS_ITEMS.items() if k in ("function_declaration", "generator_function_declaration", "class_declaration", "method_definition", "lexical_declaration")},
    **{**_TS_COMMON, "containers": frozenset({"class_declaration"})},
)


# --- C / C++ ----------------------------------------------------------------------------------


def _c_name(n: Node) -> str | None:
    if n.type == "function_definition":
        d = n.child_by_field_name("declarator")
        while d is not None and d.type not in (
            "identifier", "field_identifier", "qualified_identifier", "destructor_name", "operator_name", "template_function",
        ):
            d = d.child_by_field_name("declarator")
        return _text(d) if d is not None else None
    if n.type == "namespace_definition":
        return _text(n.child_by_field_name("name")) or "(anonymous)"
    if n.type.endswith("_specifier") and n.child_by_field_name("body") is None:
        return None  # `struct Foo *p;` mentions a type, it does not define it
    return default_name(n)


_C_ITEMS = {
    "function_definition": "fn",
    "struct_specifier": "struct",
    "union_specifier": "union",
    "enum_specifier": "enum",
}
C = Spec(
    language=tree_sitter_c.language,
    items=_C_ITEMS,
    passthrough=frozenset({"linkage_specification"}),
    unwrap={"declaration": "type", "type_definition": "type"},
    name=_c_name,
    strings=frozenset({"string_literal", "char_literal"}),
    sep="::",
)
CPP = Spec(
    language=tree_sitter_cpp.language,
    items={**_C_ITEMS, "class_specifier": "class", "namespace_definition": "mod"},
    containers=frozenset({"class_specifier", "struct_specifier", "namespace_definition"}),
    passthrough=frozenset({"linkage_specification"}),
    unwrap={"template_declaration": "", "declaration": "type", "type_definition": "type"},
    name=_c_name,
    strings=frozenset({"string_literal", "raw_string_literal", "char_literal"}),
    sep="::",
)

go = LanguageModule(GO)
typescript = LanguageModule(TYPESCRIPT)
tsx = LanguageModule(TSX)
javascript = LanguageModule(JAVASCRIPT)
c = LanguageModule(C)
cpp = LanguageModule(CPP)
