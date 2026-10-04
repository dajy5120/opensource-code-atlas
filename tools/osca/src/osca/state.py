"""Symbol-level translation state (ADR 0003).

`.osca/state/symbols.jsonl` holds one record per *annotated* symbol:

    {"id", "at": {sig, doc, body}, "zh", "by", "review": {by, at, hashes} | null}

`at` are the code hashes the annotation was written / last confirmed against.
Statuses are never stored; they are derived by comparing records with the
current index:

    pending     no annotation
    translated  annotation is new or was edited, or code unchanged since `at`
    stale       code hashes differ from `at` (which of sig / doc / body is reported)
    reviewed    a human approved exactly the current code + annotation
    orphaned    a record whose symbol no longer exists
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .project import STATE_FILE
from .symbols import FileIndex, Symbol

KINDS = ("sig", "doc", "body")
PRIORITY = {"sig": "P0", "doc": "P1", "body": "P2"}

Record = dict[str, Any]


def load(root: Path) -> dict[str, Record]:
    path = root / STATE_FILE
    if not path.is_file():
        return {}
    records = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            records[rec["id"]] = rec
    return records


def save(root: Path, records: dict[str, Record]) -> Path:
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(records[k], ensure_ascii=False, sort_keys=True) for k in sorted(records)]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


@dataclass
class Derived:
    status: str
    changed: list[str] = field(default_factory=list)

    @property
    def priority(self) -> str:
        return min((PRIORITY[c] for c in self.changed), default="")


def derive(sym: Symbol, rec: Record | None) -> Derived:
    if not sym.zh:
        return Derived("pending")
    if rec is None or rec.get("zh") != sym.zh:
        return Derived("translated")
    changed = [k for k in KINDS if rec["at"].get(k) != getattr(sym, k)]
    if changed:
        return Derived("stale", changed)
    review = rec.get("review")
    if review and review.get("hashes") == {**sym.hashes, "zh": sym.zh}:
        return Derived("reviewed")
    return Derived("translated")


def all_symbols(indexes: dict[str, FileIndex]) -> dict[str, Symbol]:
    return {sid: s for idx in indexes.values() for sid, s in idx.symbols.items()}


def orphans(records: dict[str, Record], symbols: dict[str, Symbol], paths: set[str] | None = None) -> list[str]:
    """Record ids whose symbol vanished (limited to `paths` when given)."""
    return sorted(
        rid
        for rid in records
        if rid not in symbols and (paths is None or rid.split("#", 1)[0] in paths)
    )


def update(
    records: dict[str, Record], symbols: dict[str, Symbol], by: str, paths: set[str] | None = None
) -> dict[str, list[str]]:
    """Sync records with the current annotations (in place). Returns what changed.

    - new annotation              -> record created at current hashes
    - edited annotation           -> record refreshed (editing == re-translating), review dropped
    - unchanged annotation        -> record untouched (so stale stays stale)
    - symbol renamed / moved      -> record migrated when an orphan carries the same annotation
    - annotation / symbol removed -> record dropped
    """
    changes: dict[str, list[str]] = {"created": [], "refreshed": [], "migrated": [], "dropped": []}
    gone = orphans(records, symbols, paths)
    gone_by_zh = {records[r]["zh"]: r for r in gone}

    for sid, sym in symbols.items():
        if paths is not None and sym.path not in paths:
            continue
        rec = records.get(sid)
        if not sym.zh:
            if rec is not None:
                del records[sid]
                changes["dropped"].append(sid)
            continue
        if rec is None and sym.zh in gone_by_zh:
            old = gone_by_zh.pop(sym.zh)
            rec = records.pop(old)
            rec["id"] = sid
            records[sid] = rec
            gone.remove(old)
            changes["migrated"].append(f"{old} -> {sid}")
            continue
        if rec is None:
            records[sid] = {"id": sid, "at": dict(sym.hashes), "zh": sym.zh, "by": by, "review": None}
            changes["created"].append(sid)
        elif rec.get("zh") != sym.zh:
            rec.update(at=dict(sym.hashes), zh=sym.zh, by=by, review=None)
            changes["refreshed"].append(sid)

    for rid in gone:
        del records[rid]
        changes["dropped"].append(rid)
    return changes


def approve(records: dict[str, Record], syms: list[Symbol], by: str) -> list[str]:
    """Human confirmation: the annotation is correct for the current code."""
    done = []
    for sym in syms:
        if not sym.zh:
            continue
        records[sym.id] = {
            "id": sym.id,
            "at": dict(sym.hashes),
            "zh": sym.zh,
            "by": records.get(sym.id, {}).get("by", by),
            "review": {"by": by, "at": date.today().isoformat(), "hashes": {**sym.hashes, "zh": sym.zh}},
        }
        done.append(sym.id)
    return done
