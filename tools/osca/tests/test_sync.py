import json
from pathlib import Path

import yaml

from conftest import LIB_V1, LIB_V2, commit_all, git
from osca.cli import app


def release_v2(upstream: Path, lib: str = LIB_V2) -> None:
    (upstream / "src/lib.rs").write_text(lib)
    (upstream / "src/extra.rs").write_text("pub fn extra() {}\n")
    commit_all(upstream, "v2")
    git(upstream, "tag", "v1.1.0")


def annotate(study: Path, lib: str | None = None) -> None:
    (study / "src/lib.rs").write_text(
        lib
        or LIB_V1.replace("/// Adds two numbers.\n", "/// Adds two numbers.\n/// 【zh】 加法\n").replace(
            "pub fn sub", "// 【zh】 减法\npub fn sub"
        )
    )
    commit_all(study, "zh: lib")


def test_sync_clean_merge_marks_changed_symbol_stale(study: Path, upstream: Path, runner):
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
    assert (h["from"], h["to"], h["files_changed"], h["zh_stale"]) == ("v1.0.0", "v1.1.0", 2, 1)
    assert h["symbols"]["modified"] == 1  # only `sub`; src/extra.rs is new
    report = (study / h["report"]).read_text()
    assert "需要复核的中文注释：1 个符号" in report
    assert "`sub`" in report and "| P2 |" in report
    # upstream ancestry preserved
    assert git(study, "merge-base", "--is-ancestor", "v1.1.0", "HEAD") == ""
    # `add` is untouched upstream -> still translated; `sub` is stale
    q = runner.invoke(app, ["queue"])
    assert "sub" in q.output and "add" not in q.output.split("\n")[0]
    assert runner.invoke(app, ["sync", "--no-fetch"]).output.startswith("Already")


def test_sync_conflict_is_resolved_automatically(study: Path, upstream: Path, runner):
    # annotation sits right where upstream changes -> textual conflict
    (study / "src/lib.rs").write_text(LIB_V1.replace("    a - b\n", "    // 【zh】 相减\n    a - b\n"))
    commit_all(study, "zh")
    release_v2(upstream)
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 0, res.output
    text = (study / "src/lib.rs").read_text()
    assert "    // 【zh】 相减\n    a.saturating_sub(b)\n" in text
    assert "<<<<<<<" not in text
    report = (study / yaml.safe_load((study / ".osca/sync.yaml").read_text())["history"][0]["report"]).read_text()
    assert "自动解决的冲突（1）" in report
    assert not (study / ".git/osca-sync.json").exists()


def test_sync_refuses_dirty_tree(study: Path, upstream: Path, runner):
    release_v2(upstream)
    (study / "src/lib.rs").write_text("// 【zh】 x\n" + LIB_V1)
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 1 and "uncommitted" in res.output


def test_sync_auto_resolves_claude_md(study: Path, upstream: Path, runner):
    (upstream / "CLAUDE.md").write_text("# upstream rules v2\n")
    release_v2(upstream)
    res = runner.invoke(app, ["sync"])
    assert res.exit_code == 0, res.output
    assert (study / "CLAUDE.md").read_text() == "# upstream rules v2\n\n@.osca/CLAUDE.md\n"


def test_status_and_review(study: Path, runner):
    annotate(study)
    res = runner.invoke(app, ["status", "--json", "--write"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["granularity"] == "symbol"
    assert data["files"]["translated"] == 1
    assert data["symbols"]["translated"] == 2 and data["symbols"]["reviewed"] == 0
    assert runner.invoke(app, ["review", "approve", "src/lib.rs"]).exit_code == 0
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["symbols"]["reviewed"] == 2
