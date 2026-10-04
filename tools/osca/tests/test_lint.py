from osca.lang import cython, python, rust
from osca.symbols import annotation_rows


def lint(mod, path, text):
    return [c for _, c, _ in mod.lint_rows(text.encode(), annotation_rows(path, text))]


def test_rust_string_doc_in_body_and_fence():
    src = '''/// Example:
/// ```
/// 【zh】 inside fence
/// let x = 1;
/// ```
/// 【zh】 after fence is fine
fn f() {
    let s = "a
// 【zh】 in string
";
    /// 【zh】 doc in body
    let y = 1;
}
'''
    assert sorted(lint(rust, "a.rs", src)) == ["doc-in-body", "zh-in-code-block", "zh-in-string"]


def test_python_and_cython_strings():
    py = 'def f():\n    s = """\n# 【zh】 inside\n"""\n    # 【zh】 fine\n    return s\n'
    assert lint(python, "a.py", py) == ["zh-in-string"]
    assert lint(cython, "a.pyx", py) == ["zh-in-string"]
