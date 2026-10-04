from osca.markers import comment_tokens, first_divergence, strip_text

RS = comment_tokens("a.rs")
PY = comment_tokens("a.py")


def test_tokens_by_language():
    assert comment_tokens("x/y.pyx") == ("#",)
    assert comment_tokens("Makefile") == ("#",)
    assert comment_tokens("README.md") is None


def test_strip_whole_lines_only():
    src = "/// doc\n/// [zh] 文档\nfn f() {\n    // [zh] 注释\n    //! [zh] inner\n    x // [zh] trailing\n}\n"
    out, n = strip_text(src, RS)
    assert n == 3
    assert out == "/// doc\nfn f() {\n    x // [zh] trailing\n}\n"


def test_hash_token_not_valid_in_rust():
    out, n = strip_text("# [zh] nope\nfn f() {}\n", RS)
    assert n == 0


def test_python():
    out, n = strip_text("# [zh] 问候\ndef greet():\n    #[zh]紧凑\n    pass\n", PY)
    assert (out, n) == ("def greet():\n    pass\n", 2)


def test_no_trailing_newline_preserved():
    assert strip_text("// [zh] a\nfn f() {}", RS)[0] == "fn f() {}"


def test_first_divergence_reports_study_line():
    study = "// [zh] a\nfn f() {\n    y\n}\n"
    up = "fn f() {\n    x\n}\n"
    assert first_divergence(study, up, RS) == (3, "    y\n", "    x\n")
