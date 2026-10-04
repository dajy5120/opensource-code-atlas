from osca.lang import brace
from osca.markers import comment_tokens, strip_text
from osca.symbols import MODULE, annotation_rows, index_text

GO = '''// Copyright 2026.

// Package book keeps an order book.
package book

// 【zh】 订单簿
// Book is a book.
type Book struct {
\tBids []int
}

// Add adds.
func (b *Book) Add(x int) int {
\t// 【zh】 直接返回
\ts := "a"
\treturn x
}

func Free() {}
'''

TS = """/** A widget. */
// 【zh】 控件
export class Widget extends Base {
  /** render it */
  render(x: number): string {
    return "a";
  }
}
export interface Props { a: number }
export const make = (a: number) => {
  return a;
};
const limit = 3;
type Alias = string;
"""

CPP = """namespace ns {
/// A thing.
class Thing : public Base {
 public:
  int get() const { return 1; }
};
template <typename T>
T add(T a, T b) { return a + b; }
}
int Thing2::method(int x) { return x; }
struct S { int a; };
struct S *p;
extern "C" {
void c_api(void) {}
}
"""


def names(path, text):
    return {s.qualname: s for s in index_text(path, text).symbols.values()}


def test_go():
    s = names("book/book.go", GO)
    assert set(s) == {MODULE, "Book", "Book.Add", "Free"}
    assert s["Book"].kind == "type" and len(s["Book"].zh_rows) == 1
    assert len(s["Book.Add"].zh_rows) == 1
    stripped = strip_text(GO, comment_tokens("x.go"))[0]
    t = names("book/book.go", stripped)
    assert all(t[k].hashes == s[k].hashes for k in s)


def test_typescript():
    s = names("src/widget.ts", TS)
    assert set(s) == {MODULE, "Widget", "Widget.render", "Props", "make", "Alias"}  # `limit` is not a function
    assert s["Widget"].kind == "class" and len(s["Widget"].zh_rows) == 1
    assert s["Widget.render"].kind == "method"


def test_cpp():
    s = names("src/thing.cpp", CPP)
    assert set(s) == {MODULE, "ns", "ns::Thing", "ns::Thing::get", "ns::add", "Thing2::method", "S", "c_api"}


def test_string_lint_go_ts():
    go = 'package x\n\nvar s = `\n// 【zh】 in raw string\n`\n'
    assert [c for _, c, _ in brace.go.lint_rows(go.encode(), annotation_rows("a.go", go))] == ["zh-in-string"]
    ts = "const s = `\n// 【zh】 in template\n`;\n"
    assert [c for _, c, _ in brace.typescript.lint_rows(ts.encode(), annotation_rows("a.ts", ts))] == ["zh-in-string"]


def test_translate_go_placement(tmp_path):
    from osca import translate
    from osca.symbols import index_text as it

    text = GO.replace("// 【zh】 订单簿\n", "").replace("\t// 【zh】 直接返回\n", "")
    idx = it("b.go", text)
    zh: set[int] = set()
    lines = text.split("\n")
    row, tok, ind = translate._doc_slot("b.go", lines, idx.symbols["b.go#Book.Add"], zh)
    assert (lines[row], tok) == ("func (b *Book) Add(x int) int {", "//")
    row, tok, _ = translate._doc_slot("b.go", lines, idx.symbols["b.go#<module>"], zh)
    assert lines[row - 1] == "// Copyright 2026." and lines[row] == "" and tok == "//"
