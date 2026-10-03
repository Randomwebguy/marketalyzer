"""Evidence for the AI's decisions: how the strategy's similar signals ended.

Every trade the strategy's own signals made (before the test and during it,
on the tested stocks and on the large caps) is an entry sample: the readings
on the bar where its entry signal came and the trade's return after costs.
Every exit signal is an exit sample: the readings on its bar and what
holding on for a few more bars would have brought. A sample counts for a
decision only once its outcome was known (``known``), so nothing from the
future leaks in.

A decision gets the outcome of the most similar known samples next to the
outcome of all of them. The coach proposes rules over the same readings; a
rule is kept only if the known samples it picks did what it claims.
"""

from __future__ import annotations

import bisect
import json
import math
import operator
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from marketalyzer.services import number

# Readings a sample and a decision share; rules may use these names.
FEATURES: dict[str, str] = {
    "rsi": "RSI",
    "bollinger_b": "Bollinger %b",
    "adx": "ADX",
    "getiri_5": "5 bar getiri %",
    "getiri_20": "20 bar getiri %",
    "sma50_uzaklik": "SMA50'ye uzaklık %",
    "sma200_uzaklik": "SMA200'e uzaklık %",
    "hacim_orani": "hacim / 20 bar ortalaması",
    "atr": "ATR %",
    "supertrend_genel": "genel Supertrend (1 yukarı, -1 aşağı)",
    "goreli_guc": "XU100'e göre 20 bar göreli güç",
    "gunluk_getiri_20": "günlük barlarda 20 gün getiri %",
    "gunluk_sma50": "günlük SMA50'ye uzaklık %",
    "piyasa_getiri_20": "XU100 20 gün getiri %",
    "piyasa_genislik": "50 günlük ortalamasının üstündeki büyük hisse oranı %",
}
NEIGHBOURS = 25
MIN_SAMPLES = 15  # fewer known samples: no evidence
MIN_SUPPORT = 10  # a rule needs this many matching samples
MIN_T = 1.5  # how far a rule's samples must sit from all samples, in standard errors
MAX_RULES = 5
MAX_CONDITIONS = 3
# Entry rules pass on or take a buy signal; exit rules hold on or sell.
ENTRY_ACTIONS = ("reddet", "uygula")
EXIT_ACTIONS = ("tut", "sat")
ACTIONS = ENTRY_ACTIONS + EXIT_ACTIONS
# The actions that claim the picked samples end below the rest.
DOWN = ("reddet", "sat")
OPS: dict[str, Callable[[float, float], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


# --- Readings ---------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def readings(
    ind: dict[str, np.ndarray],
    i: int,
    *,
    index_change: float | None = None,
    daily: dict[str, Any] | None = None,
    market: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    """Return the readings on bar ``i`` of causal indicators (see ``research``).

    ``index_change`` is XU100's 20-bar change on the same bar; ``daily`` and
    ``market`` are the context's sections for the decision.
    """
    close = ind["close"]
    last = float(close[i])
    n = i % len(close) if i < 0 else i

    def change(bars: int) -> float | None:
        if n < bars or not close[n - bars]:
            return None
        return (last / float(close[n - bars]) - 1) * 100

    def distance(key: str) -> float | None:
        level = _finite(ind[key][i])
        return (last / level - 1) * 100 if level else None

    upper, lower = _finite(ind["bb_upper"][i]), _finite(ind["bb_lower"][i])
    width = upper - lower if upper is not None and lower is not None else None
    volume20 = _finite(ind["volume20"][i])
    atr = _finite(ind["atr"][i])
    twenty = change(20)
    daily, market = daily or {}, market or {}
    values = {
        "rsi": _finite(ind["rsi"][i]),
        "bollinger_b": (last - lower) / width if width else None,
        "adx": _finite(ind["adx"][i]),
        "getiri_5": change(5),
        "getiri_20": twenty,
        "sma50_uzaklik": distance("sma50"),
        "sma200_uzaklik": distance("sma200"),
        "hacim_orani": float(ind["volume"][i]) / volume20 if volume20 else None,
        "atr": atr / last * 100 if atr is not None and last else None,
        "supertrend_genel": 1.0 if ind["st_dir"][i] < 0 else -1.0,
        "goreli_guc": twenty - index_change
        if twenty is not None and index_change is not None
        else None,
        "gunluk_getiri_20": _finite(daily.get("getiri_20_gun_%")),
        "gunluk_sma50": _finite(daily.get("sma50_uzaklik_%")),
        "piyasa_getiri_20": _finite(market.get("xu100_20_gun_%")),
        "piyasa_genislik": _finite(market.get("genislik_sma50_ustu_%")),
    }
    return {key: (number(v, 3) if v is not None and math.isfinite(v) else None)
            for key, v in values.items()}  # fmt: skip


# --- Samples ----------------------------------------------------------------------


@dataclass(frozen=True)
class Sample:
    """A past signal trade: the readings at its signal and how it ended."""

    readings: dict[str, float | None]
    pct: float  # the trade's return after costs
    known: Any  # the bar where the trade closed
    code: str


def stats(samples: list[Sample]) -> dict[str, Any]:
    """Count, share of winners and average return of ``samples``."""
    if not samples:
        return {"adet": 0}
    returns = [s.pct for s in samples]
    return {
        "adet": len(samples),
        "kazancli_%": number(sum(r > 0 for r in returns) / len(returns) * 100, 1),
        "ort_getiri_%": number(sum(returns) / len(returns), 2),
    }


class Pool:
    """The strategy's signal trades, each counted once it has closed."""

    def __init__(self, samples: list[Sample]):
        self.samples = sorted(samples, key=lambda s: s.known)
        self._known = [s.known for s in self.samples]

    def known(self, now: Any) -> list[Sample]:
        """Return the samples whose trade closed by ``now``."""
        return self.samples[: bisect.bisect_right(self._known, now)]

    def similar(
        self, values: dict[str, float | None], now: Any
    ) -> dict[str, Any] | None:
        """Return the outcome of the samples most like ``values`` and of all of them.

        Readings are compared in units of their spread; a reading a sample
        lacks counts as one unit apart. Too few known samples give None.
        """
        known = self.known(now)
        if len(known) < MIN_SAMPLES:
            return None
        keys = [k for k in FEATURES if values.get(k) is not None]
        if not keys:
            return None
        matrix = np.array(
            [
                [np.nan if s.readings.get(k) is None else s.readings[k] for k in keys]
                for s in known
            ]  # fmt: skip
        )
        spread = np.nanstd(matrix, axis=0)
        spread[~np.isfinite(spread) | (spread == 0)] = 1.0
        target = np.array([values[k] for k in keys])
        gaps = np.abs(matrix - target) / spread
        gaps = np.where(np.isnan(gaps), 1.0, np.minimum(gaps, 4.0))
        distance = np.sqrt((gaps**2).mean(axis=1))
        count = min(NEIGHBOURS, max(MIN_SAMPLES // 2, len(known) // 3))
        nearest = [known[j] for j in np.argsort(distance, kind="stable")[:count]]
        return {"benzer": stats(nearest), "tumu": stats(known)}


# --- Rules ------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    """A condition on the readings of a signal and what to do then.

    Entry rules ("reddet", "uygula") are checked on entry samples, exit rules
    ("tut", "sat") on exit samples.
    """

    conditions: tuple[tuple[str, str, float], ...]
    action: str  # reddet, uygula, tut or sat

    @property
    def exit(self) -> bool:
        """Return whether the rule is about an exit signal."""
        return self.action in EXIT_ACTIONS

    def matches(self, values: dict[str, float | None]) -> bool:
        """Return whether every condition holds for ``values``."""
        for key, op, level in self.conditions:
            value = values.get(key)
            if value is None or not OPS[op](value, level):
                return False
        return True

    def text(self) -> str:
        """Say the rule in Turkish, e.g. ``RSI < 45 ve ADX > 30 ise reddet``."""
        parts = [
            f"{FEATURES[key]} {op} {_plain(level)}"
            for key, op, level in self.conditions
        ]
        where = "çıkış sinyalinde " if self.exit else ""
        return f"{where}{' ve '.join(parts)} ise {self.action}"

    def to_dict(self) -> dict[str, Any]:
        """Return the rule as JSON-friendly data."""
        return {"kosullar": [list(c) for c in self.conditions], "eylem": self.action}


def _plain(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _rule(item: Any) -> Rule | None:
    if not isinstance(item, dict):
        return None
    action = str(item.get("eylem") or "").strip().lower()
    raw = item.get("kosullar")
    if action not in ACTIONS or not isinstance(raw, list):
        return None
    conditions = []
    for raw_part in raw[: MAX_CONDITIONS + 1]:
        part = raw_part
        if isinstance(part, dict):
            part = [part.get("ozellik"), part.get("islem"), part.get("deger")]
        if not isinstance(part, (list, tuple)) or len(part) != 3:
            return None
        key, op, level = str(part[0]).strip(), str(part[1]).strip(), _finite(part[2])
        if key not in FEATURES or op not in OPS or level is None:
            return None
        conditions.append((key, op, level))
    if not 1 <= len(conditions) <= MAX_CONDITIONS:
        return None
    return Rule(tuple(conditions), action)


def parse_rules(text: str) -> list[Rule]:
    """Read ``{"kurallar": [{"kosullar": [[ad, işlem, değer]], "eylem": ...}]}``.

    Rules naming unknown readings or operators are left out.
    """
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    items = data.get("kurallar") if isinstance(data, dict) else None
    rules = [_rule(item) for item in items or []] if isinstance(items, list) else []
    return [rule for rule in rules if rule is not None]


def verify(rule: Rule, samples: list[Sample]) -> dict[str, Any]:
    """Test a rule on known samples of its kind; ``kabul`` says whether it holds.

    A "reddet" or "sat" rule must pick at least ``MIN_SUPPORT`` samples whose
    average is below zero and clearly below all samples' (by ``MIN_T``
    standard errors); an "uygula" or "tut" rule the other way round.
    """
    picked = [s for s in samples if rule.matches(s.readings)]
    found = stats(picked)
    base = stats(samples)
    row = {
        "kural": rule.text(),
        **rule.to_dict(),
        **found,
        "tumu": base,
        "kabul": False,
    }
    if len(picked) < MIN_SUPPORT or len(samples) < 2:
        row["neden"] = f"az örnek ({len(picked)})"
        return row
    returns = np.array([s.pct for s in samples])
    mean = float(np.mean([s.pct for s in picked]))
    error = float(np.std(returns)) / math.sqrt(len(picked)) or 1.0
    t = (mean - float(returns.mean())) / error
    row["t"] = number(t, 2)
    if rule.action in DOWN:
        row["kabul"] = mean < 0 and t <= -MIN_T
    else:
        row["kabul"] = mean > 0 and t >= MIN_T
    if not row["kabul"]:
        row["neden"] = "geçmiş sinyallerde tutmadı"
    return row


def describe(row: dict[str, Any]) -> str:
    """Write a verified rule with its record, for the decision view."""
    if row.get("eylem") in EXIT_ACTIONS:
        return (
            f"{row['kural']} ({row['adet']} geçmiş çıkış: tutmak %{row['kazancli_%']}"
            f" oranında kazandırırdı, ort. %{row['ort_getiri_%']}; tüm çıkışlar"
            f" %{row['tumu'].get('kazancli_%')}, ort. %{row['tumu'].get('ort_getiri_%')})"
        )
    return (
        f"{row['kural']} ({row['adet']} geçmiş sinyal: %{row['kazancli_%']} kazançlı,"
        f" ort. %{row['ort_getiri_%']}; tüm sinyaller %{row['tumu'].get('kazancli_%')},"
        f" ort. %{row['tumu'].get('ort_getiri_%')})"
    )


def slices(samples: list[Sample]) -> dict[str, list[dict[str, Any]]]:
    """Split known samples into thirds by each reading, for the coach."""
    out = {}
    for key in FEATURES:
        valued = sorted(
            (s for s in samples if s.readings.get(key) is not None),
            key=lambda s: s.readings[key],
        )
        if len(valued) < 3 * MIN_SUPPORT // 2:
            continue
        third = len(valued) // 3
        parts = [valued[:third], valued[third : 2 * third], valued[2 * third :]]
        out[key] = [
            {"aralik": [part[0].readings[key], part[-1].readings[key]], **stats(part)}
            for part in parts
        ]
    return out
