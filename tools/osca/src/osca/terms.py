"""Terminology files: schema validation and cross-domain consistency."""

from __future__ import annotations

import json
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
