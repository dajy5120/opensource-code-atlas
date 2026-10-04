from pathlib import Path

from conftest import LIB_V1, commit_all
from osca import docs
from osca.cli import app
from osca.project import load_project

DOC = """---
title: 演示模块
anchors:
  - src/lib.rs#sub
  - src/app.py
---

# 演示模块

`sub` 做减法。
"""


def statuses(study):
    return {d.doc.path: (d.status, d.changed, d.missing) for d in docs.check(load_project(study))}


def write_doc(study, text=DOC):
    p = study / "osca/docs/modules/demo.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_doc_lifecycle(study: Path):
    write_doc(study)
    path = "osca/docs/modules/demo.md"
    assert statuses(study)[path][0] == "new"
    docs.update(load_project(study))
    assert statuses(study)[path][0] == "current"

    (study / "src/lib.rs").write_text(LIB_V1.replace("a - b", "a.wrapping_sub(b)"))
    assert statuses(study)[path][:2] == ("stale", ["src/lib.rs#sub"])
    docs.update(load_project(study))  # stale docs are not refreshed by update
    assert statuses(study)[path][0] == "stale"
    docs.update(load_project(study), approve=[path])
    assert statuses(study)[path][0] == "current"

    (study / "src/app.py").write_text("def greet(name):\n    return name\n")  # whole-file anchor
    assert statuses(study)[path][:2] == ("stale", ["src/app.py"])
    write_doc(study, DOC.replace("做减法", "做回绕减法"))  # editing refreshes
    assert statuses(study)[path][0] == "new"

    write_doc(study, DOC.replace("src/lib.rs#sub", "src/lib.rs#gone"))
    assert statuses(study)[path][0] == "broken"


def test_sync_report_lists_affected_docs(study: Path, upstream: Path, runner):
    from conftest import git
    write_doc(study)
    commit_all(study, "doc")
    (upstream / "src/lib.rs").write_text(LIB_V1.replace("a - b", "a.saturating_sub(b)"))
    commit_all(upstream, "v2")
    git(upstream, "tag", "v1.1.0")
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 0, res.output
    import yaml
    h = yaml.safe_load((study / ".osca/sync.yaml").read_text())["history"][0]
    assert h["docs_stale"] == 1
    assert "受影响的分析文档（1）" in (study / h["report"]).read_text()
    out = runner.invoke(app, ["docs", "list"]).output
    assert "stale" in out and "src/lib.rs#sub" in out
