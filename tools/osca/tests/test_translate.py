from pathlib import Path

from conftest import LIB_V1, commit_all
from osca import state, translate
from osca.project import load_project
from osca.verify import verify

TERMS = {"fill": {"en": "Fill", "zh": "成交", "avoid": ["填充"]}, "spread": {"en": "Spread", "zh": "价差"}}


def fake(responses):
    calls = []

    def call(params):
        calls.append(params)
        return responses.pop(0), "", {"input_tokens": 10, "output_tokens": 5}

    call.calls = calls
    return call


def anchor(study):
    import yaml
    return yaml.safe_load((study / ".osca/sync.yaml").read_text())["anchor"]["upstream_commit"]


def test_plan_and_request(study: Path):
    project = load_project(study)
    jobs = translate.plan(project, ["src/lib.rs"], all_symbols=True)
    assert [t.sym.qualname for t in jobs[0].targets] == ["<module>", "add", "sub"]
    params = translate.request_params(project, jobs[0], TERMS)
    assert params["model"] == "claude-sonnet-5-5"
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["output_config"]["format"]["type"] == "json_schema"
    file_block, targets_block = params["messages"][0]["content"]
    assert "    3|     a + b" in file_block["text"]
    assert "Fill" not in file_block["text"]  # irrelevant terms are not injected
    assert "src/lib.rs#sub" in targets_block["text"]
    batch = translate.request_params(project, jobs[0], TERMS, batch=True)
    assert "fallbacks" not in batch and "betas" not in batch


def test_apply_doc_and_line_annotations(study: Path):
    project = load_project(study)
    jobs = translate.plan(project, ["src/lib.rs"], all_symbols=True)
    call = fake([{
        "annotations": [
            {"symbol": "src/lib.rs#add", "placement": "doc", "line": 0, "text": ["加法：返回两数之和。"]},
            {"symbol": "src/lib.rs#sub", "placement": "doc", "line": 0, "text": ["// 【zh】 减法"]},
            {"symbol": "src/lib.rs#sub", "placement": "line", "line": 7, "text": ["直接相减，可能溢出。"]},
            {"symbol": "src/lib.rs#<module>", "placement": "doc", "line": 0, "text": ["演示模块。"]},
        ],
        "unchanged": [],
        "notes": "",
    }])
    [res] = translate.run(project, jobs, TERMS, call)
    assert res.error == "" and sorted(res.applied) == ["src/lib.rs#<module>", "src/lib.rs#add", "src/lib.rs#sub"]
    text = (study / "src/lib.rs").read_text()
    assert text.startswith("//! 【zh】 演示模块。\n/// Adds two numbers.\n/// 【zh】 加法：返回两数之和。\npub fn add")
    assert "// 【zh】 减法\npub fn sub(a: i32, b: i32) -> i32 {\n    // 【zh】 直接相减，可能溢出。\n    a - b\n" in text
    assert verify(study, anchor(study)) == []
    recs = state.load(study)
    assert recs["src/lib.rs#add"]["by"] == "ai:claude-sonnet-5-5" and recs["src/lib.rs#add"]["review"] is None


def test_replaces_existing_annotation_and_reports_unchanged(study: Path):
    (study / "src/lib.rs").write_text(LIB_V1.replace("pub fn sub", "// 【zh】 旧说明\npub fn sub").replace("pub fn add", "// 【zh】 加\npub fn add"))
    commit_all(study, "zh")
    project = load_project(study)
    jobs = translate.plan(project, ["src/lib.rs"], which=("translated",))
    call = fake([{
        "annotations": [{"symbol": "src/lib.rs#sub", "placement": "doc", "line": 0, "text": ["新说明"]}],
        "unchanged": ["src/lib.rs#add"],
        "notes": "ok",
    }])
    [res] = translate.run(project, jobs, TERMS, call)
    text = (study / "src/lib.rs").read_text()
    assert "旧说明" not in text and "// 【zh】 新说明\npub fn sub" in text
    assert res.unchanged == ["src/lib.rs#add"] and "// 【zh】 加\n" in text


def test_rejects_bad_lines_and_rolls_back_invalid_files(study: Path):
    (study / "src/app.py").write_text('def greet(name):\n    s = """\nhello\n"""\n    return s\n')
    commit_all(study, "edit (test only)")
    # make the edited file the "upstream" so verify compares against it
    import subprocess, yaml
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=study, capture_output=True, text=True).stdout.strip()
    data = yaml.safe_load((study / ".osca/sync.yaml").read_text())
    data["anchor"]["upstream_commit"] = head
    (study / ".osca/sync.yaml").write_text(yaml.safe_dump(data))
    commit_all(study, "anchor")
    project = load_project(study)
    jobs = translate.plan(project, ["src/app.py"], all_symbols=True)
    original = (study / "src/app.py").read_text()
    call = fake([{
        "annotations": [
            {"symbol": "src/app.py#greet", "placement": "line", "line": 99, "text": ["越界"]},
        ],
        "unchanged": [], "notes": "",
    }, {
        "annotations": [
            {"symbol": "src/app.py#greet", "placement": "line", "line": 3, "text": ["插进字符串里"]},
        ],
        "unchanged": [], "notes": "",
    }])
    [r1] = translate.run(project, jobs, TERMS, call)
    assert r1.applied == [] and "line 99" in r1.rejected[0]
    [r2] = translate.run(project, translate.plan(project, ["src/app.py"], all_symbols=True), TERMS, call)
    assert r2.applied == [] and "zh-in-string" in r2.error
    assert (study / "src/app.py").read_text() == original


def test_parse_message_handles_refusal_and_json():
    class B:  # minimal stand-ins for SDK objects
        def __init__(self, **kw):
            self.__dict__.update(kw)

    refusal = B(stop_reason="refusal", stop_details=B(category="cyber"), content=[])
    assert translate.parse_message(refusal) == (None, "refused (category: cyber)")
    ok = B(stop_reason="end_turn", content=[B(type="thinking"), B(type="text", text='{"annotations": [], "unchanged": [], "notes": ""}')])
    assert translate.parse_message(ok)[0]["notes"] == ""


def test_cross_file_type_context(study: Path):
    (study / "src/types.rs").write_text("/// A price.\npub struct Price {\n    raw: i64,\n}\n")
    (study / "src/lib.rs").write_text("pub fn mid(a: Price, b: Price) -> Price {\n    todo!()\n}\n")
    commit_all(study, "types (test only)")
    import subprocess, yaml
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=study, capture_output=True, text=True).stdout.strip()
    data = yaml.safe_load((study / ".osca/sync.yaml").read_text())
    data["anchor"]["upstream_commit"] = head
    (study / ".osca/sync.yaml").write_text(yaml.safe_dump(data))
    project = load_project(study)
    [job] = [j for j in translate.plan(project, ["src/lib.rs"], all_symbols=True)]
    assert "// src/types.rs:2" in job.context and "pub struct Price" in job.context
    assert "相关类型定义" in translate.render_file(job, {})
