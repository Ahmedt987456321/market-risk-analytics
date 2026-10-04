"""The generated Power BI project: every field a visual uses must exist in the model."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_powerbi", ROOT / "tools" / "build_powerbi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fields(obj):
    if isinstance(obj, dict):
        for kind in ("Column", "Measure"):
            if kind in obj and "Property" in obj[kind]:
                yield kind, obj[kind]["Expression"]["SourceRef"]["Entity"], obj[kind]["Property"]
        for v in obj.values():
            yield from _fields(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _fields(v)


def test_visuals_only_use_fields_that_exist(tmp_path, monkeypatch):
    b = _load_builder()
    monkeypatch.setattr(b, "OUT", tmp_path / "powerbi")
    b.main()
    columns = {(t, c) for t, cols in b.TABLES.items() for c, _ in cols}
    measures = {(t, m) for t, m, *_ in b.MEASURES}
    visuals = list((tmp_path / "powerbi").rglob("visual.json"))
    assert len(visuals) >= 15
    for v in visuals:
        for kind, entity, prop in _fields(json.loads(v.read_text(encoding="utf-8"))):
            assert (entity, prop) in (columns if kind == "Column" else measures), (v.parent.name, kind, entity, prop)


def test_measures_only_reference_known_tables(tmp_path):
    b = _load_builder()
    for table, name, dax, *_ in b.MEASURES:
        assert table in b.TABLES
        for t in b.TABLES:
            if f"{t}[" in dax:
                cols = {c for c, _ in b.TABLES[t]}
                import re
                for ref in re.findall(rf"{t}\[([^\]]+)\]", dax):
                    assert ref in cols, (name, t, ref)
