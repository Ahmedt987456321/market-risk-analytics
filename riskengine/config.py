from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
CONFIG_FILES = ("universe.yaml", "book.yaml", "controls.yaml", "risk.yaml", "stress.yaml")


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    asset_class: str
    desk: str
    source: str
    ticker: str
    quote: str
    currency: str
    price_scale: float = 1.0
    tenor_years: float | None = None
    returns_proxy: str | None = None   # Yahoo ticker whose returns replace this instrument's in risk scenarios
    stress_proxy: str | None = None    # "source:ticker" used instead in historical stress windows


@dataclass
class Config:
    instruments: list[Instrument]
    cross_checks: list[dict]
    fx_map: dict[str, str]
    history_start: str
    book: dict
    controls: dict
    risk: dict
    stress: dict
    sha256: str

    def instrument(self, instrument_id: str) -> Instrument:
        for inst in self.instruments:
            if inst.id == instrument_id:
                return inst
        raise KeyError(instrument_id)

    def tickers(self, source: str) -> list[str]:
        """All tickers requested from a source, including cross-check tickers."""
        own = [i.ticker for i in self.instruments if i.source == source]
        extra = [c["ticker"] for c in self.cross_checks if c["source"] == source]
        if source == "yahoo":
            extra += [i.returns_proxy for i in self.instruments if i.returns_proxy]
        extra += [i.stress_proxy.split(":", 1)[1] for i in self.instruments
                  if i.stress_proxy and i.stress_proxy.split(":", 1)[0] == source]
        return own + [t for t in extra if t not in own]


def load_config(config_dir: Path = CONFIG_DIR) -> Config:
    texts = {name: (config_dir / name).read_text(encoding="utf-8") for name in CONFIG_FILES}
    sha = hashlib.sha256("".join(texts[n] for n in CONFIG_FILES).encode()).hexdigest()
    universe = yaml.safe_load(texts["universe.yaml"])
    return Config(
        instruments=[Instrument(**row) for row in universe["instruments"]],
        cross_checks=universe.get("cross_checks", []),
        fx_map=universe["fx_map"],
        history_start=str(universe["history_start"]),
        book=yaml.safe_load(texts["book.yaml"]),
        controls=yaml.safe_load(texts["controls.yaml"]),
        risk=yaml.safe_load(texts["risk.yaml"]),
        stress=yaml.safe_load(texts["stress.yaml"]),
        sha256=sha,
    )
