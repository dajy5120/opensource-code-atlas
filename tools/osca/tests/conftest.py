from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from osca.cli import app

PROJECT_YAML = """\
schema: osca/v1
project: demo
upstream:
  repository: https://example.invalid/demo
  branch: main
  track: tags
  tag_pattern: '^v\\d+\\.\\d+\\.\\d+$'
study:
  branch: study/zh-CN
scope:
  include: ["src/**"]
  exclude: ["**/tests/**"]
"""

LIB_V1 = """\
/// Adds two numbers.
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}

pub fn sub(a: i32, b: i32) -> i32 {
    a - b
}
"""

LIB_V2 = LIB_V1.replace("    a - b\n", "    a.saturating_sub(b)\n")

PY_V1 = """\
def greet(name):
    return f"hi {name}"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def commit_all(cwd: Path, msg: str) -> None:
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", msg)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    for k, v in {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null",
    }.items():
        monkeypatch.setenv(k, v)
    return CliRunner()


@pytest.fixture
def upstream(tmp_path: Path, runner: CliRunner) -> Path:
    up = tmp_path / "upstream"
    (up / "src").mkdir(parents=True)
    git(up, "init", "-q", "-b", "main")
    (up / "src/lib.rs").write_text(LIB_V1)
    (up / "src/app.py").write_text(PY_V1)
    (up / "README.md").write_text("# demo\n")
    (up / "CLAUDE.md").write_text("# upstream rules\n")
    commit_all(up, "v1")
    git(up, "tag", "v1.0.0")
    return up


@pytest.fixture
def study(tmp_path: Path, upstream: Path, runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> Path:
    st = tmp_path / "study"
    git(tmp_path, "clone", "-q", "-o", "upstream", str(upstream), str(st))
    git(st, "switch", "-q", "-c", "study/zh-CN", "v1.0.0")
    (st / ".osca").mkdir()
    (st / ".osca/project.yaml").write_text(PROJECT_YAML)
    monkeypatch.chdir(st)
    res = runner.invoke(app, ["init", "--anchor", "v1.0.0"])
    assert res.exit_code == 0, res.output
    commit_all(st, "osca: init")
    return st
