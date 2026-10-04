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
