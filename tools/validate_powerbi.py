"""Validate every JSON file in powerbi/ against the schema named in its own $schema field.

Schemas are downloaded from developer.microsoft.com (cached under data/schemas/).
Files without a $schema (.pbip, definition.pbism) are checked against the schema
for their file type.

    python tools/validate_powerbi.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
from jsonschema import Draft7Validator, Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "schemas"
# Official report theme schema, from github.com/microsoft/powerbi-desktop-samples ("Report Theme JSON Schema").
THEME_SCHEMA_URL = ("https://raw.githubusercontent.com/microsoft/powerbi-desktop-samples/main/"
                    "Report%20Theme%20JSON%20Schema/reportThemeSchema-2.157.json")
THEME_SCHEMA = CACHE / "reportThemeSchema-2.157.json"
NO_SCHEMA = {
    ".pbip": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
    "definition.pbism": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
}


def fetch(uri: str) -> dict:
    path = CACHE / (uri.split("://", 1)[1].replace("/", "_"))
    if not path.exists():
        r = requests.get(uri, timeout=60)
        r.raise_for_status()
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def retrieve(uri: str) -> Resource:
    return Resource.from_contents(fetch(uri))


def main() -> int:
    if not THEME_SCHEMA.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        THEME_SCHEMA.write_bytes(requests.get(THEME_SCHEMA_URL, timeout=60).content)
    registry = Registry(retrieve=retrieve)
    files = [p for p in (ROOT / "powerbi").rglob("*") if p.is_file() and p.suffix in (".json", ".pbir", ".pbip", ".pbism")
             and ("StaticResources" not in p.parts or "RegisteredResources" in p.parts)]
    failures = 0
    for f in sorted(files):
        doc = json.loads(f.read_text(encoding="utf-8"))
        if "RegisteredResources" in f.parts:              # custom report theme
            schema = json.loads(THEME_SCHEMA.read_text(encoding="utf-8-sig"))
        else:
            uri = doc.get("$schema") or NO_SCHEMA.get(f.suffix) or NO_SCHEMA.get(f.name)
            if uri is None:
                print(f"SKIP  {f.relative_to(ROOT)} (no schema)")
                continue
            schema = fetch(uri)
        cls = Draft202012Validator if "2020-12" in schema.get("$schema", "") else Draft7Validator
        errors = sorted(cls(schema, registry=registry).iter_errors(doc), key=lambda e: list(e.path))
        if errors:
            failures += 1
            print(f"FAIL  {f.relative_to(ROOT)}")
            for e in errors[:5]:
                print(f"      at {'/'.join(map(str, e.path)) or '(root)'}: {e.message[:200]}")
        else:
            print(f"ok    {f.relative_to(ROOT)}")
    print(f"\n{len(files) - failures} of {len(files)} files valid")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
