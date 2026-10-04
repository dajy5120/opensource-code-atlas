"""The strip invariant: strip_zh(study) == upstream@anchor, byte for byte.

Every path that differs between the anchor commit and the working tree must be
either an OSCA overlay path, or a file whose only additions are `【zh】` lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pathspec

from . import gitutil, lang
from .markers import DEFAULT_MARKER, comment_tokens, first_divergence, strip_text
from .symbols import annotation_rows

# Paths OSCA owns outright; anything goes.
OVERLAY = pathspec.GitIgnoreSpec.from_lines(
    [
        "/.osca/**",
        "/osca/**",
        "/.github/workflows/osca-*.yml",
        "/.github/workflows/osca-*.yaml",
    ]
)

# Paths OSCA may create, but only if upstream does not have them.
NEW_ONLY = pathspec.GitIgnoreSpec.from_lines(["/.claude/**", "/CLAUDE.md"])

# If upstream already has CLAUDE.md, the only allowed change is appending this line.
CLAUDE_MD = "CLAUDE.md"
CLAUDE_IMPORT = "@.osca/CLAUDE.md"


@dataclass(frozen=True)
class Issue:
    path: str
    code: str
    message: str
    line: int | None = None

    def format(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}: [{self.code}] {self.message}"


def is_overlay(path: str) -> bool:
    return OVERLAY.match_file(path)


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_worktree(root: Path, path: str) -> bytes | None:
    full = root / path
    if full.is_symlink():
        return os.readlink(full).encode()
    if not full.is_file():
        return None
    return full.read_bytes()


def _check_claude_md(path: str, study: bytes, upstream: bytes) -> Issue | None:
    s, u = _decode(study), _decode(upstream)
    if s is None or u is None:
        return Issue(path, "binary", "CLAUDE.md is not valid UTF-8")
    lines = s.rstrip("\n").split("\n")
    if lines and lines[-1].strip() == CLAUDE_IMPORT:
        lines.pop()
        while lines and lines[-1].strip() == "":
            lines.pop()
    if "\n".join(lines) == u.rstrip("\n"):
        return None
    return Issue(
        path,
        "claude-md",
        f"upstream CLAUDE.md may only gain a trailing `{CLAUDE_IMPORT}` line",
    )


def _check_modified(path: str, study: bytes, upstream: bytes, marker: str) -> Issue | None:
    if study == upstream:
        return None
    tokens = comment_tokens(path)
    if tokens is None:
        return Issue(path, "unsupported", "file modified, but annotations are not supported for this file type")
    s, u = _decode(study), _decode(upstream)
    if s is None or u is None:
        return Issue(path, "binary", "binary or non-UTF-8 file differs from upstream")
    stripped, _ = strip_text(s, tokens, marker)
    if stripped == u:
        return _lint(path, s, marker)
    div = first_divergence(s, u, tokens, marker)
    if div is None:  # pragma: no cover - strip mismatch always has a divergence
        return Issue(path, "code-modified", "content differs from upstream after stripping annotations")
    n, got, want = div
    return Issue(
        path,
        "code-modified",
        f"non-annotation line differs from upstream\n    study:    {got.rstrip()!r}\n    upstream: {want.rstrip()!r}",
        line=n,
    )


def _lint(path: str, text: str, marker: str) -> Issue | None:
    """AST placement checks: the blind spots of the strip invariant (ADR 0001)."""
    mod = lang.for_path(path)
    if mod is None:
        return None
    rows = annotation_rows(path, text, marker)
    if not rows:
        return None
    problems = mod.lint_rows(text.encode("utf-8"), rows)
    if not problems:
        return None
    row, code, msg = problems[0]
    more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
    return Issue(path, code, msg + more, line=row + 1)


def changed_paths(root: Path, anchor: str) -> dict[str, str]:
    """Map path -> status letter for the working tree vs `anchor` (incl. untracked)."""
    out = gitutil.split_z(gitutil.git(root, "diff", "--name-status", "--no-renames", "-z", anchor, "--"))
    changes: dict[str, str] = {}
    for status, path in zip(out[::2], out[1::2]):
        changes[path] = status[0]
    for path in gitutil.split_z(gitutil.git(root, "ls-files", "-z", "--others", "--exclude-standard")):
        changes[path] = "?"
    return changes


def verify(
    root: Path,
    anchor: str,
    marker: str = DEFAULT_MARKER,
    files: list[str] | None = None,
    generated: list[str] | None = None,
) -> list[Issue]:
    """Check the strip invariant.

    `generated` lists gitignore-style patterns of build-generated files (e.g. cbindgen
    headers rendered from doc comments): they must equal upstream exactly, because a
    local build copies annotations into them.
    """
    gen_spec = pathspec.GitIgnoreSpec.from_lines(generated or [])
    issues: list[Issue] = []
    if not gitutil.try_rev_parse(root, anchor):
        return [Issue(".osca/sync.yaml", "anchor", f"anchor commit {anchor} not found; fetch upstream history")]
    # During an in-progress sync merge the new anchor is reachable from MERGE_HEAD only.
    merging = gitutil.try_rev_parse(root, "MERGE_HEAD")
    if not gitutil.is_ancestor(root, anchor, "HEAD") and not (
        merging and gitutil.is_ancestor(root, anchor, merging)
    ):
        issues.append(
            Issue(".osca/sync.yaml", "anchor", f"anchor {anchor[:12]} is not an ancestor of HEAD")
        )

    if files is None:
        # OSCA files that upstream's .gitignore hides would silently never be committed
        hidden = gitutil.split_z(
            gitutil.git(root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--", ".osca", "osca", ".claude", CLAUDE_MD)
        )
        for path in hidden:
            if path.endswith("settings.local.json"):  # personal Claude Code settings
                continue
            issues.append(Issue(path, "ignored", f"OSCA file is hidden by .gitignore; track it with `git add -f {path}`"))

    changes = changed_paths(root, anchor)
    if files is not None:
        wanted = set(files)
        changes = {p: s for p, s in changes.items() if p in wanted}

    with gitutil.BlobReader(root) as blobs:
        for path, status in sorted(changes.items()):
            if is_overlay(path):
                continue
            upstream = blobs.read(anchor, path)
            if upstream is None:
                if NEW_ONLY.match_file(path):
                    continue
                issues.append(Issue(path, "new-file", "new file outside the OSCA overlay (.osca/, osca/)"))
                continue
            study = _read_worktree(root, path)
            if study is None:
                issues.append(Issue(path, "deleted", "upstream file was deleted"))
                continue
            if gen_spec.match_file(path):
                issue = None if study == upstream else Issue(
                    path,
                    "generated",
                    "build-generated file differs from upstream (a local build copied annotations "
                    f"into it); restore with `git checkout {anchor[:12]} -- {path}`",
                )
            elif path == CLAUDE_MD:
                issue = _check_claude_md(path, study, upstream)
            else:
                issue = _check_modified(path, study, upstream, marker)
            if issue:
                issues.append(issue)
    return issues
