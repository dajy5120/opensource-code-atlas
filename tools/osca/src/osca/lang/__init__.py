"""Per-language symbol extractors."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from . import brace, cython, python, rust

BY_SUFFIX: dict[str, Any] = {
    ".rs": rust,
    ".py": python,
    ".pyi": python,
    ".pyx": cython,
    ".pxd": cython,
    ".pxi": cython,
    ".go": brace.go,
    ".ts": brace.typescript,
    ".mts": brace.typescript,
    ".cts": brace.typescript,
    ".tsx": brace.tsx,
    ".js": brace.javascript,
    ".jsx": brace.javascript,
    ".mjs": brace.javascript,
    ".cjs": brace.javascript,
    ".c": brace.c,
    ".h": brace.cpp,  # C++ grammar also parses C headers
    ".cc": brace.cpp,
    ".cpp": brace.cpp,
    ".cxx": brace.cpp,
    ".hh": brace.cpp,
    ".hpp": brace.cpp,
    ".hxx": brace.cpp,
}


def for_path(path: str) -> Any | None:
    return BY_SUFFIX.get(PurePosixPath(path).suffix.lower())
