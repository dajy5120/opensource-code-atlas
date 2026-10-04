from pathlib import Path

from conftest import LIB_V1, commit_all
from osca import docs
from osca.project import load_project
from osca.site import _paragraphs, build


def test_build_site(study: Path, tmp_path: Path):
    (study / "src/lib.rs").write_text(
        LIB_V1.replace("pub fn sub", "// 【zh】 减法：`a - b`，\n// 【zh】 可能溢出。\npub fn sub")
    )
    doc = study / "osca/docs/modules/demo.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\ntitle: 演示\nanchors:\n  - src/lib.rs#sub\n---\n\n# 演示\n\n```mermaid\ngraph TD; A-->B\n```\n")
    commit_all(study, "zh + doc")
    project = load_project(study)
    docs.update(project)
    out = tmp_path / "site"
    assert build(project, out) == {"files": 1, "docs": 1}
    page = (out / "src/src/lib.rs.html").read_text()
    assert '<div class="note"><p>减法：<code>a - b</code>，可能溢出。</p></div>' in page
    assert 'id="L3"' in page and "【zh】" not in page
    assert '<pre class="mermaid">' in (out / "docs/modules/demo.html").read_text()
    assert "src/src/lib.rs.html" in (out / "index.html").read_text()


def test_paragraphs():
    assert _paragraphs(["处理流程：", "1. 先查缓存；", "2. 再分派。", "", "注意：", "跨行 `a`", "`b` 合并"]) == [
        "处理流程：", "1. 先查缓存；", "2. 再分派。", "注意：跨行 `a` `b` 合并",
    ]
