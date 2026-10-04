"""Per-language symbol extractors."""

from __future__ import annotations

from pathlib import PurePosixPath
from types import ModuleType

from . import cython, python, rust

BY_SUFFIX: dict[str, ModuleType] = {
    ".rs": rust,
    ".py": python,
    ".pyi": python,
    ".pyx": cython,
    ".pxd": cython,
    ".pxi": cython,
}


def for_path(path: str) -> ModuleType | None:
    return BY_SUFFIX.get(PurePosixPath(path).suffix.lower())
