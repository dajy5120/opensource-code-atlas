"""Atlas-level view: aggregate every study repo's status into the index repository."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

START, END = "<!-- osca:status:start -->", "<!-- osca:status:end -->"
RAW = "https://raw.githubusercontent.com/{repo}/refs/heads/{branch}/{path}"


@dataclass
class Row:
    entry: dict[str, Any]
    status: dict[str, Any] | None = None
    project: dict[str, Any] | None = None
    behind: int | None = None
    latest: str | None = None
    error: str = ""

    @property
    def id(self) -> str:
        return self.entry["id"]


@dataclass
class Atlas:
    root: Path
    projects: list[dict[str, Any]] = field(default_factory=list)
    categories: dict[str, dict[str, str]] = field(default_factory=dict)


def find_root(start: Path) -> Path | None:
    for d in [start, *start.parents]:
        if (d / "projects").is_dir() and (d / "catalog" / "categories.yaml").is_file():
            return d
    return None


def load(root: Path) -> Atlas:
    cats = yaml.safe_load((root / "catalog" / "categories.yaml").read_text(encoding="utf-8")) or {}
    projects = [
        yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted((root / "projects").glob("*.yaml"))
    ]
    return Atlas(root, [p for p in projects if p], cats.get("categories") or {})


def _repo(url: str) -> str:
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise ValueError(f"not a GitHub URL: {url}")
    return m.group(1)


def _fetch(repo: str, branch: str, path: str) -> str | None:
    try:
        with urllib.request.urlopen(RAW.format(repo=repo, branch=branch, path=path), timeout=30) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # private repos: fall back to the authenticated GitHub CLI
            proc = subprocess.run(
                ["gh", "api", f"repos/{repo}/contents/{path}?ref={branch}", "-H", "Accept: application/vnd.github.raw"],
                capture_output=True, text=True,
            )
            return proc.stdout if proc.returncode == 0 else None
        raise


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def upstream_tags(url: str) -> list[str]:
    out = subprocess.run(["git", "ls-remote", "--tags", "--refs", url], capture_output=True, text=True, timeout=120)
    return [line.split("refs/tags/", 1)[1] for line in out.stdout.splitlines() if "refs/tags/" in line]


def collect(atlas: Atlas, offline: bool = False) -> list[Row]:
    rows = []
    for entry in atlas.projects:
        row = Row(entry)
        try:
            repo = _repo(entry["repository"]["study"])
            branch = entry.get("study_branch", "study/zh-CN")
            raw_status = _fetch(repo, branch, ".osca/status.json")
            raw_project = _fetch(repo, branch, ".osca/project.yaml")
            row.status = json.loads(raw_status) if raw_status else None
            row.project = yaml.safe_load(raw_project) if raw_project else None
            if row.status is None:
                row.error = "no .osca/status.json yet"
            up = (row.project or {}).get("upstream") or {}
            ref = ((row.status or {}).get("anchor") or {}).get("ref")
            if not offline and up.get("track", "tags") == "tags" and ref:
                pat = re.compile(up.get("tag_pattern", r"^v\d+\.\d+\.\d+$"))
                tags = sorted((t for t in upstream_tags(entry["repository"]["upstream"]) if pat.search(t)), key=_version_key)
                row.latest = tags[-1] if tags else None
                row.behind = sum(1 for t in tags if _version_key(t) > _version_key(ref))
        except Exception as e:  # one broken project must not hide the others
            row.error = str(e)[:200]
        rows.append(row)
    return rows


def _pct(x: float | None) -> str:
    return f"{x:.1%}" if isinstance(x, (int, float)) else "—"


def _cat_names(atlas: Atlas, entry: dict) -> str:
    return " · ".join(atlas.categories.get(c, {}).get("name", c) for c in entry.get("categories") or [])


def table(atlas: Atlas, rows: list[Row]) -> str:
    head = [
        "| 分类 | 项目 | 上游 | 学习仓库 | 锚点 | 落后 | 覆盖率 | 已审核 | 注释行 | 分析文档 |",
        "|------|------|------|----------|------|------|--------|--------|--------|----------|",
    ]
    lines = []
    for r in sorted(rows, key=lambda r: (r.entry.get("priority", 99), r.id)):
        e, s = r.entry, r.status or {}
        up, st = e["repository"]["upstream"], e["repository"]["study"]
        anchor = (s.get("anchor") or {}).get("ref") or "—"
        behind = "—" if r.behind is None else ("✓ 最新" if r.behind == 0 else f"{r.behind} 个版本（最新 {r.latest}）")
        docs = s.get("docs") or {}
        n_docs = sum(docs.values()) if docs else 0
        doc_cell = "—" if not n_docs else f"{n_docs}" + (f"（{docs.get('stale', 0)} 过时）" if docs.get("stale") else "")
        lines.append(
            f"| {_cat_names(atlas, e)} | {e['name']} | [{_repo(up)}]({up}) | [{_repo(st).split('/')[1]}]({st})"
            f" · [在线阅读](https://{_repo(st).split('/')[0]}.github.io/{_repo(st).split('/')[1]}/) "
            f"| {anchor} | {behind} | {_pct(s.get('coverage'))} | {_pct(s.get('reviewed'))} "
            f"| {s.get('zh_lines', '—')} | {doc_cell} |"
            + (f" ⚠ {r.error}" if r.error else "")
        )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "\n".join(head + lines) + f"\n\n> 由 `osca atlas status --write-readme` 自动生成于 {stamp}；覆盖率按符号统计。"


def write_readme(atlas: Atlas, body: str) -> bool:
    p = atlas.root / "README.md"
    text = p.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise ValueError(f"README.md lacks {START} / {END} markers")
    pre, rest = text.split(START, 1)
    _, post = rest.split(END, 1)
    new = f"{pre}{START}\n{body}\n{END}{post}"
    old_core = re.sub(r"自动生成于 [^；]+", "", text)
    if re.sub(r"自动生成于 [^；]+", "", new) == old_core:
        return False  # only the timestamp would change
    p.write_text(new, encoding="utf-8")
    return True


def tree(atlas: Atlas, rows: list[Row]) -> str:
    by_cat: dict[str, list[Row]] = {}
    for r in rows:
        for c in r.entry.get("categories") or ["other"]:
            by_cat.setdefault(c, []).append(r)
    out = ["OpenSource Code Atlas", ""]
    order = [c for c in atlas.categories if c in by_cat] + [c for c in by_cat if c not in atlas.categories]
    for c in order:
        meta = atlas.categories.get(c, {})
        out.append(f"{meta.get('name', c)}  {meta.get('zh', '')}".rstrip())
        items = sorted(by_cat[c], key=lambda r: r.id)
        for i, r in enumerate(items):
            s = r.status or {}
            branch = "└──" if i == len(items) - 1 else "├──"
            anchor = (s.get("anchor") or {}).get("ref") or "?"
            behind = "" if r.behind in (None, 0) else f"  ↓{r.behind}"
            out.append(f"{branch} {r.entry['name']:<20}{_pct(s.get('coverage')):>7}  reviewed {_pct(s.get('reviewed')):>6}  @ {anchor}{behind}")
        out.append("")
    return "\n".join(out).rstrip()
