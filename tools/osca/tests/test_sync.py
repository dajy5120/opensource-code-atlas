from pathlib import Path

import yaml

from conftest import LIB_V1, LIB_V2, commit_all, git
from osca.cli import app


def release_v2(upstream: Path, lib: str = LIB_V2) -> None:
    (upstream / "src/lib.rs").write_text(lib)
    (upstream / "src/extra.rs").write_text("pub fn extra() {}\n")
    commit_all(upstream, "v2")
    git(upstream, "tag", "v1.1.0")


def annotate(study: Path) -> None:
    (study / "src/lib.rs").write_text("/// [zh] 加法\n" + LIB_V1)
    commit_all(study, "zh: lib")


def test_sync_clean_merge(study: Path, upstream: Path, runner):
    annotate(study)
    release_v2(upstream)
    dry = runner.invoke(app, ["sync", "--dry-run"])
    assert dry.exit_code == 0 and "v1.0.0 → v1.1.0" in dry.output

    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 0, res.output
    assert "strip invariant holds" in res.output
    assert git(study, "branch", "--show-current").strip() == "sync/v1.1.0"
    assert git(study, "rev-parse", "mirror/main") == git(study, "rev-parse", "v1.1.0")

    data = yaml.safe_load((study / ".osca/sync.yaml").read_text())
    assert data["anchor"]["upstream_ref"] == "v1.1.0"
    h = data["history"][0]
    assert (h["from"], h["to"], h["files_changed"], h["zh_files_affected"]) == ("v1.0.0", "v1.1.0", 2, 1)
    report = (study / h["report"]).read_text()
    assert "`src/lib.rs`" in report and "需要复核中文注释的文件：1" in report
    # upstream ancestry is preserved: the merge has the upstream tag as a parent
    assert git(study, "merge-base", "--is-ancestor", "v1.1.0", "HEAD") == ""
    assert runner.invoke(app, ["verify"]).exit_code == 0
    assert runner.invoke(app, ["sync", "--no-fetch"]).output.startswith("Already")


def test_sync_conflict_then_continue(study: Path, upstream: Path, runner):
    # annotation sits right where upstream changes -> conflict
    (study / "src/lib.rs").write_text(LIB_V1.replace("    a - b\n", "    // [zh] 相减\n    a - b\n"))
    commit_all(study, "zh")
    release_v2(upstream)
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 1 and "src/lib.rs" in res.output
    assert runner.invoke(app, ["sync"]).exit_code == 1  # already in progress

    (study / "src/lib.rs").write_text(LIB_V2.replace("    a.saturating", "    // [zh] 饱和减法\n    a.saturating"))
    git(study, "add", "src/lib.rs")
    res = runner.invoke(app, ["sync", "--continue"])
    assert res.exit_code == 0, res.output
    assert "strip invariant holds" in res.output
    assert not (study / ".git/osca-sync.json").exists()


def test_sync_refuses_dirty_tree(study: Path, upstream: Path, runner):
    release_v2(upstream)
    (study / "src/lib.rs").write_text("// [zh] x\n" + LIB_V1)
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 1 and "uncommitted" in res.output


def test_status(study: Path, runner):
    annotate(study)
    res = runner.invoke(app, ["status", "--json", "--write"])
    assert res.exit_code == 0, res.output
    import json
    data = json.loads(res.output)
    assert data["files"] == {"in_scope": 2, "translated": 1, "pending": 1}
    assert data["zh_lines"] == 1
    assert (study / ".osca/status.json").exists()
