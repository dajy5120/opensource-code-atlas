"""Analysis documents (osca/docs/**.md) anchored to source symbols.

Front matter:

    ---
    title: 订单簿模块
    anchors:
      - crates/model/src/orderbook/book.rs#OrderBook        # one symbol
      - crates/model/src/orderbook/ladder.rs                # a whole file
    ---

`.osca/state/docs.jsonl` records, per document, the content hash and the code
hash of every anchor when the document was written or last confirmed. Same
rules as annotations (ADR 0003): editing the document refreshes the record;
code changes under an anchor make the document stale; `osca docs approve`
confirms a document is still correct.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .index import index_worktree
from .project import Project, load_sync
from .symbols import Symbol

DOCS_DIR = "osca/docs"
DOCS_STATE = ".osca/state/docs.jsonl"
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@dataclass
class Doc:
    path: str
    title: str
    anchors: list[str]
    body_sha: str
    meta: dict[str, Any]


@dataclass
class DocStatus:
    doc: Doc
    status: str  # current | stale | broken | unanchored | new
    changed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def parse(root: Path, rel: str) -> Doc:
    text = (root / rel).read_text(encoding="utf-8")
    m = FRONT.match(text)
    meta = (yaml.safe_load(m.group(1)) or {}) if m else {}
    body = text[m.end():] if m else text
    anchors = [str(a) for a in meta.get("anchors") or []]
    return Doc(rel, str(meta.get("title") or Path(rel).stem), anchors, _sha(body), meta)


def discover(root: Path) -> list[Doc]:
    base = root / DOCS_DIR
    if not base.is_dir():
        return []
    return [
        parse(root, p.relative_to(root).as_posix())
        for p in sorted(base.rglob("*.md"))
        if p.name.lower() != "readme.md"
    ]


def _sym_hash(s: Symbol) -> str:
    return _sha(f"{s.sig}|{s.doc}|{s.body}")


def anchor_hashes(project: Project, anchors: list[str]) -> dict[str, str | None]:
    """Current code hash per anchor (None if it no longer exists)."""
    paths = sorted({a.split("#", 1)[0] for a in anchors})
    indexes = index_worktree(project, [p for p in paths if (project.root / p).is_file()])
    out: dict[str, str | None] = {}
    for a in anchors:
        path, _, sym = a.partition("#")
        idx = indexes.get(path)
        if idx is None:
            out[a] = None
        elif sym:
            s = idx.symbols.get(a)
            out[a] = _sym_hash(s) if s else None
        else:  # whole file: any symbol change counts
            out[a] = _sha("\n".join(f"{k}:{_sym_hash(s)}" for k, s in sorted(idx.symbols.items())))
    return out


def load_state(root: Path) -> dict[str, dict]:
    p = root / DOCS_STATE
    if not p.is_file():
        return {}
    return {r["path"]: r for r in (json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())}


def save_state(root: Path, records: dict[str, dict]) -> None:
    p = root / DOCS_STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(records[k], ensure_ascii=False, sort_keys=True) for k in sorted(records)]
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def derive(doc: Doc, rec: dict | None, current: dict[str, str | None]) -> DocStatus:
    if not doc.anchors:
        return DocStatus(doc, "unanchored")
    missing = [a for a in doc.anchors if current.get(a) is None]
    if missing:
        return DocStatus(doc, "broken", missing=missing)
    if rec is None or rec.get("sha") != doc.body_sha or set(rec.get("anchors", {})) != set(doc.anchors):
        return DocStatus(doc, "new")  # written / edited since last record: fresh once recorded
    changed = [a for a in doc.anchors if rec["anchors"].get(a) != current.get(a)]
    return DocStatus(doc, "stale", changed) if changed else DocStatus(doc, "current")


def check(project: Project) -> list[DocStatus]:
    docs = discover(project.root)
    records = load_state(project.root)
    current = anchor_hashes(project, sorted({a for d in docs for a in d.anchors}))
    return [derive(d, records.get(d.path), current) for d in docs]


def _record(doc: Doc, current: dict[str, str | None]) -> dict:
    return {"path": doc.path, "sha": doc.body_sha, "anchors": {a: current[a] for a in doc.anchors}}


def update(project: Project, approve: list[str] | None = None) -> dict[str, list[str]]:
    """Record new / edited docs (and `approve`d ones); drop records of deleted docs.

    Stale documents are left stale unless edited or explicitly approved.
    """
    root = project.root
    docs = discover(root)
    records = load_state(root)
    current = anchor_hashes(project, sorted({a for d in docs for a in d.anchors}))
    anchor = load_sync(root)["anchor"]
    changes: dict[str, list[str]] = {"recorded": [], "approved": [], "dropped": []}
    for d in docs:
        st = derive(d, records.get(d.path), current)
        if st.status in ("unanchored", "broken"):
            continue
        if st.status == "new" or (approve and d.path in approve):
            records[d.path] = {
                **_record(d, current),
                "upstream_commit": anchor["upstream_commit"],
                "recorded": date.today().isoformat(),
            }
            changes["approved" if st.status != "new" else "recorded"].append(d.path)
    for p in [p for p in records if p not in {d.path for d in docs}]:
        del records[p]
        changes["dropped"].append(p)
    save_state(root, records)
    return changes


def summary(statuses: list[DocStatus]) -> dict[str, int]:
    out = {k: 0 for k in ("current", "new", "stale", "broken", "unanchored")}
    for s in statuses:
        out[s.status] += 1
    return out
