"""Terminology files: schema validation and cross-domain consistency."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import jsonschema
import yaml


def load_schema() -> dict:
    return json.loads(resources.files("osca").joinpath("terms.schema.json").read_text(encoding="utf-8"))


def validate_dir(directory: Path) -> tuple[list[str], list[str], int]:
    """Return (errors, warnings, term_count) for all *.yml/*.yaml in `directory`."""
    validator = jsonschema.Draft202012Validator(load_schema())
    errors: list[str] = []
    warnings: list[str] = []
    seen: dict[str, tuple[str, str]] = {}  # key -> (file, zh)
    count = 0
    files = sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])
    if not files:
        errors.append(f"no terminology files in {directory}")
    for f in files:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            errors.append(f"{f.name}: invalid YAML: {e}")
            continue
        for err in validator.iter_errors(data):
            loc = "/".join(str(p) for p in err.absolute_path) or "<root>"
            errors.append(f"{f.name}: {loc}: {err.message}")
        terms = (data or {}).get("terms") or {}
        if not isinstance(terms, dict):
            continue
        for key, t in terms.items():
            if not isinstance(t, dict):
                continue
            count += 1
            zh = str(t.get("zh", ""))
            for bad in t.get("avoid", []) or []:
                if bad == zh:
                    errors.append(f"{f.name}: {key}: `avoid` contains the preferred translation {zh!r}")
            if key in seen and seen[key][1] != zh:
                warnings.append(
                    f"{key}: {seen[key][0]} says {seen[key][1]!r} but {f.name} says {zh!r} "
                    "(domain override — make sure it is intended)"
                )
            seen.setdefault(key, (f.name, zh))
    return errors, warnings, count


# --- loading terminology for a study repo -------------------------------------------------

ATLAS_RAW = "https://raw.githubusercontent.com/{repo}/{ref}/terminology/{name}.yml"


def _fetch(root: Path, repo: str, ref: str, name: str) -> str:
    """Download a terminology file from the atlas repo, cached under .git."""
    import subprocess
    import urllib.request

    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-path", "osca-terms"], cwd=root, capture_output=True, text=True
    ).stdout.strip()
    cache = root / git_dir / f"{name}.yml"
    import time

    fresh = cache.is_file() and time.time() - cache.stat().st_mtime < 86400
    if fresh:
        return cache.read_text(encoding="utf-8")
    try:
        with urllib.request.urlopen(ATLAS_RAW.format(repo=repo, ref=ref, name=name), timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except OSError:
        if cache.is_file():  # offline: a stale glossary beats none
            return cache.read_text(encoding="utf-8")
        raise
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def load_project_terms(project, terms_dir: Path | None = None) -> dict[str, dict]:
    """Merge the project's terminology domains (later domains and .osca/terminology.yaml win).

    Sources, in order: `terms_dir` / $OSCA_TERMINOLOGY (a local atlas `terminology/` dir),
    else the atlas repo on GitHub (cached).
    """
    import os

    raw = project.raw
    names = raw.get("terminology") or ["general"]
    atlas = raw.get("atlas", {})
    repo, ref = atlas.get("repo", "dajy5120/opensource-code-atlas"), atlas.get("ref", "main")
    local = terms_dir or (Path(os.environ["OSCA_TERMINOLOGY"]) if os.environ.get("OSCA_TERMINOLOGY") else None)
    merged: dict[str, dict] = {}
    for name in names:
        if local is not None:
            text = (local / f"{name}.yml").read_text(encoding="utf-8")
        else:
            text = _fetch(project.root, repo, ref, name)
        merged.update((yaml.safe_load(text) or {}).get("terms") or {})
    override = project.root / ".osca" / "terminology.yaml"
    if override.is_file():
        merged.update((yaml.safe_load(override.read_text(encoding="utf-8")) or {}).get("terms") or {})
    return merged


def relevant(terms: dict[str, dict], text: str) -> dict[str, dict]:
    """Terms whose English form (or an alias) occurs in `text` (case-insensitive)."""
    low = text.lower()
    out = {}
    for key, t in terms.items():
        if _mentions(_forms(key, t), low):
            out[key] = t
    return out


def _forms(key: str, t: dict) -> list[str]:
    return [f for f in (t.get("en", ""), key.replace("_", " "), key.replace("_", ""), *(t.get("aliases") or [])) if f]


def _mentions(forms: list[str], text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(f.lower())}(?![a-z])", low) for f in forms)


def lint_text(terms: dict[str, dict], lines: list[tuple[str, int, str, str]]) -> list[str]:
    """`avoid` violations: (path, line_no, annotation text, code context) -> messages.

    An avoided word only counts when the English term occurs in the code the
    annotation belongs to (or in the annotation itself): "填充" is a wrong
    translation of *fill*, but a fine word for padding.
    """
    problems = []
    for path, n, text, context in lines:
        for key, t in terms.items():
            bad = [b for b in t.get("avoid") or [] if b and b in text]
            if bad and _mentions(_forms(key, t), context + "\n" + text):
                problems += [f"{path}:{n}: 「{b}」应译为「{t['zh']}」（{t['en']}）" for b in bad]
    return problems
