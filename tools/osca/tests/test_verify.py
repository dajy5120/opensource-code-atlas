import json
from pathlib import Path

from conftest import LIB_V1, commit_all
from osca.cli import app
from osca.verify import verify


def anchor(st: Path) -> str:
    import yaml
    return yaml.safe_load((st / ".osca/sync.yaml").read_text())["anchor"]["upstream_commit"]


def test_init_appends_claude_import(study: Path):
    assert (study / "CLAUDE.md").read_text() == "# upstream rules\n\n@.osca/CLAUDE.md\n"
    assert verify(study, anchor(study)) == []


def test_annotations_pass(study: Path, runner):
    (study / "src/lib.rs").write_text("/// [zh] 加法\n" + LIB_V1.replace("    a + b\n", "    // [zh] 直接相加\n    a + b\n"))
    (study / "osca/docs").mkdir(parents=True)
    (study / "osca/docs/x.md").write_text("notes")
    assert verify(study, anchor(study)) == []
    assert runner.invoke(app, ["verify"]).exit_code == 0


def test_code_change_detected(study: Path, runner):
    (study / "src/lib.rs").write_text("// [zh] 加法\n" + LIB_V1.replace("a + b", "a + b + 1"))
    issues = verify(study, anchor(study))
    assert [(i.path, i.code, i.line) for i in issues] == [("src/lib.rs", "code-modified", 4)]
    res = runner.invoke(app, ["verify"])
    assert res.exit_code == 1 and "code-modified" in res.output


def test_whitespace_change_detected(study: Path):
    (study / "src/app.py").write_text((study / "src/app.py").read_text() + "\n")
    assert [i.code for i in verify(study, anchor(study))] == ["code-modified"]


def test_trailing_annotation_rejected(study: Path):
    (study / "src/lib.rs").write_text(LIB_V1.replace("a + b\n", "a + b // [zh] 相加\n"))
    assert [i.code for i in verify(study, anchor(study))] == ["code-modified"]


def test_new_deleted_unsupported(study: Path):
    (study / "src/new.rs").write_text("fn x() {}\n")
    (study / "src/app.py").unlink()
    (study / "README.md").write_text("# demo\n<!-- [zh] -->\n")
    codes = sorted(i.code for i in verify(study, anchor(study)))
    assert codes == ["deleted", "new-file", "unsupported"]


def test_claude_md_other_edit_rejected(study: Path):
    (study / "CLAUDE.md").write_text("# changed\n\n@.osca/CLAUDE.md\n")
    assert [i.code for i in verify(study, anchor(study))] == ["claude-md"]


def test_committed_changes_checked(study: Path):
    (study / "src/lib.rs").write_text(LIB_V1.replace("a + b", "b + a"))
    commit_all(study, "oops")
    assert [i.code for i in verify(study, anchor(study))] == ["code-modified"]


def test_hook_mode(study: Path, runner):
    (study / "src/lib.rs").write_text(LIB_V1.replace("a + b", "b + a"))
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(study / "src/lib.rs")}, "cwd": str(study)}
    res = runner.invoke(app, ["verify", "--hook"], input=json.dumps(payload))
    assert res.exit_code == 2
    assert "code-modified" in res.stderr
    ok = {"tool_name": "Edit", "tool_input": {"file_path": str(study / "src/app.py")}}
    assert runner.invoke(app, ["verify", "--hook"], input=json.dumps(ok)).exit_code == 0
