"""Thin wrappers around the git CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def git(root: Path, *args: str, check: bool = True, input: bytes | None = None) -> str:
    """Run git in `root` and return stdout as text."""
    return git_bytes(root, *args, check=check, input=input).decode("utf-8", "replace")


def git_bytes(root: Path, *args: str, check: bool = True, input: bytes | None = None) -> bytes:
    proc = subprocess.run(
        ["git", *args], cwd=root, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def git_ok(root: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode == 0


def repo_root(start: Path) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True, text=True
    )
    if out.returncode != 0:
        raise GitError(f"{start} is not inside a git repository")
    return Path(out.stdout.strip())


def rev_parse(root: Path, rev: str) -> str:
    return git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").strip()


def try_rev_parse(root: Path, rev: str) -> str | None:
    try:
        return rev_parse(root, rev)
    except GitError:
        return None


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return git_ok(root, "merge-base", "--is-ancestor", ancestor, descendant)


def current_branch(root: Path) -> str | None:
    out = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).strip()
    return out or None


def is_clean(root: Path) -> bool:
    return git(root, "status", "--porcelain", "--untracked-files=no").strip() == ""


def split_z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


class BlobReader:
    """Reads many blobs through a single `git cat-file --batch` process."""

    def __init__(self, root: Path):
        self._proc = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, rev: str, path: str) -> bytes | None:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(f"{rev}:{path}\n".encode())
        self._proc.stdin.flush()
        header = self._proc.stdout.readline().decode()
        if header.endswith("missing\n"):
            return None
        size = int(header.split()[2])
        data = self._proc.stdout.read(size)
        self._proc.stdout.read(1)  # trailing LF
        return data

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait()

    def __enter__(self) -> BlobReader:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
