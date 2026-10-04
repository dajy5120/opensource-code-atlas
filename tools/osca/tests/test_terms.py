from pathlib import Path

from osca.terms import validate_dir


def test_valid_and_invalid(tmp_path: Path):
    (tmp_path / "a.yml").write_text(
        "schema: osca-terms/v1\ndomain: a\nterms:\n  fill: {en: Fill, zh: 成交, avoid: [填充]}\n"
    )
    (tmp_path / "b.yml").write_text(
        "schema: osca-terms/v1\ndomain: b\nterms:\n  fill: {en: Fill, zh: 填充}\n  Bad-Key: {en: x, zh: y}\n"
    )
    errors, warnings, count = validate_dir(tmp_path)
    assert count == 3
    assert any("Bad-Key" in e for e in errors)
    assert any("fill" in w for w in warnings)


def test_repo_terminology_is_valid():
    repo_terms = Path(__file__).resolve().parents[3] / "terminology"
    errors, _, count = validate_dir(repo_terms)
    assert errors == [] and count > 100


def test_lint_is_context_sensitive():
    from osca.terms import lint_text

    terms = {"fill": {"en": "Fill", "zh": "成交", "avoid": ["填充"]}}
    padding = ("a.rs", 1, "// 【zh】 不足 10 档时用零数量填充", "// Skip padding entries\nfor order in depth.bids {}")
    fills = ("a.rs", 2, "// 【zh】 处理填充事件", "fn on_fill(&mut self, fill: OrderFilled) {}")
    assert lint_text(terms, [padding]) == []
    assert lint_text(terms, [fills]) == ["a.rs:2: 「填充」应译为「成交」（Fill）"]
