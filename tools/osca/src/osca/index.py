"""Indexing the study working tree (or upstream blobs), with a content-addressed cache."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

from . import __version__, gitutil
from .project import Project
from .symbols import FileIndex, index_text, supported

CACHE_NAME = "osca-index-cache.pickle"


class IndexCache:
    """path+content hash -> FileIndex, stored under .git so it never reaches commits."""

    def __init__(self, root: Path, salt: str):
        self.path = root / gitutil.git(root, "rev-parse", "--git-path", CACHE_NAME).strip()
        self.salt = salt
        self.dirty = False
        self.touched: set[str] = set()
        try:
            data = pickle.loads(self.path.read_bytes())
            self.entries: dict[str, FileIndex] = data["entries"] if data.get("salt") == salt else {}
        except Exception:  # missing / corrupt / incompatible cache
            self.entries = {}

    def key(self, path: str, text: str) -> str:
        return hashlib.sha1(f"{path}\0{text}".encode()).hexdigest()

    def get(self, path: str, text: str, build) -> FileIndex | None:
        k = self.key(path, text)
        self.touched.add(k)
        if k in self.entries:
            return self.entries[k]
        idx = build()
        if idx is not None:
            self.entries[k] = idx
            self.dirty = True
        return idx

    def save(self) -> None:
        if not self.dirty:
            return
        if len(self.entries) > max(5000, 3 * len(self.touched)):
            self.entries = {k: v for k, v in self.entries.items() if k in self.touched}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(pickle.dumps({"salt": self.salt, "entries": self.entries}))


def _salt(project: Project) -> str:
    return json.dumps([__version__, project.marker, project.symbol_policy], sort_keys=True)


def scope_files(project: Project) -> list[str]:
    files = gitutil.split_z(gitutil.git(project.root, "ls-files", "-z"))
    return [f for f in files if project.in_scope(f) and supported(f)]


def index_worktree(project: Project, paths: list[str] | None = None) -> dict[str, FileIndex]:
    root = project.root
    cache = IndexCache(root, _salt(project))
    out: dict[str, FileIndex] = {}
    for path in paths if paths is not None else scope_files(project):
        full = root / path
        if not full.is_file() or not supported(path):
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        idx = cache.get(path, text, lambda: index_text(path, text, project.marker, project.symbol_policy))
        if idx is not None:
            out[path] = idx
    cache.save()
    return out


def index_rev(project: Project, rev: str, paths: list[str]) -> dict[str, FileIndex]:
    """Index files as they exist at `rev` (e.g. an upstream tag)."""
    root = project.root
    cache = IndexCache(root, _salt(project))
    out: dict[str, FileIndex] = {}
    with gitutil.BlobReader(root) as blobs:
        for path in paths:
            if not supported(path):
                continue
            data = blobs.read(rev, path)
            if data is None:
                continue
            text = data.decode("utf-8", "replace")
            idx = cache.get(path, text, lambda: index_text(path, text, project.marker, project.symbol_policy))
            if idx is not None:
                out[path] = idx
    cache.save()
    return out


