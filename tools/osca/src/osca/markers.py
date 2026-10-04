"""Recognising and stripping `【zh】` annotation lines.

An annotation line is a *whole line* consisting of optional indentation, a
line-comment token valid for the file's language, optional spaces, and the
marker (default `【zh】`). Trailing comments are deliberately not recognised:
they cannot be stripped back to the exact upstream bytes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache
from pathlib import PurePosixPath

DEFAULT_MARKER = "【zh】"

_SLASH = ("///", "//!", "//")
_HASH = ("#",)
_DASH = ("--",)

COMMENT_TOKENS_BY_SUFFIX: dict[str, tuple[str, ...]] = {
    **dict.fromkeys(
        [".rs", ".go", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".java", ".kt",
         ".scala", ".swift", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".cs", ".zig", ".hxx",
         ".proto", ".dart", ".fbs", ".capnp"],
        _SLASH,
    ),
    **dict.fromkeys(
        [".py", ".pyi", ".pyx", ".pxd", ".pxi", ".sh", ".bash", ".zsh", ".rb", ".pl", ".r",
         ".toml", ".yaml", ".yml", ".mk", ".cmake", ".nix", ".tf"],
        _HASH,
    ),
    **dict.fromkeys([".sql", ".lua", ".hs"], _DASH),
}

COMMENT_TOKENS_BY_NAME: dict[str, tuple[str, ...]] = {
    "Makefile": _HASH,
    "Dockerfile": _HASH,
    "CMakeLists.txt": _HASH,
    "Justfile": _HASH,
}


def comment_tokens(path: str) -> tuple[str, ...] | None:
    """Line-comment tokens allowed for `path`, or None if annotations are unsupported."""
    p = PurePosixPath(path)
    if p.name in COMMENT_TOKENS_BY_NAME:
        return COMMENT_TOKENS_BY_NAME[p.name]
    return COMMENT_TOKENS_BY_SUFFIX.get(p.suffix.lower())


@cache
def _pattern(tokens: tuple[str, ...], marker: str) -> re.Pattern[str]:
    alts = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    return re.compile(rf"[ \t]*(?:{alts})[ \t]*{re.escape(marker)}")


def iter_lines(text: str) -> Iterator[str]:
    """Split on '\\n' only, keeping line endings (unlike str.splitlines)."""
    start = 0
    while True:
        i = text.find("\n", start)
        if i == -1:
            if start < len(text):
                yield text[start:]
            return
        yield text[start : i + 1]
        start = i + 1


def is_annotation(line: str, tokens: tuple[str, ...], marker: str = DEFAULT_MARKER) -> bool:
    return _pattern(tokens, marker).match(line) is not None


def strip_text(text: str, tokens: tuple[str, ...], marker: str = DEFAULT_MARKER) -> tuple[str, int]:
    """Remove annotation lines. Returns (stripped_text, removed_line_count)."""
    pat = _pattern(tokens, marker)
    kept: list[str] = []
    removed = 0
    for line in iter_lines(text):
        if pat.match(line):
            removed += 1
        else:
            kept.append(line)
    return "".join(kept), removed


def count_annotations(text: str, tokens: tuple[str, ...], marker: str = DEFAULT_MARKER) -> int:
    pat = _pattern(tokens, marker)
    return sum(1 for line in iter_lines(text) if pat.match(line))


def first_divergence(
    study: str, upstream: str, tokens: tuple[str, ...], marker: str = DEFAULT_MARKER
) -> tuple[int, str, str] | None:
    """Locate the first non-annotation difference.

    Returns (study_line_number, study_line, upstream_line) using 1-based study
    line numbers, or None if the texts are equivalent modulo annotations.
    """
    pat = _pattern(tokens, marker)
    up_lines = list(iter_lines(upstream))
    j = 0
    n = 0
    for n, line in enumerate(iter_lines(study), start=1):
        if pat.match(line):
            continue
        if j >= len(up_lines) or line != up_lines[j]:
            return n, line, up_lines[j] if j < len(up_lines) else "<EOF>"
        j += 1
    if j < len(up_lines):
        return n + 1, "<EOF>", up_lines[j]
    return None
