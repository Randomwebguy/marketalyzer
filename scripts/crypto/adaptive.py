"""Adapt the strategy to each coin, walking forward one quarter at a time.

    uv run python scripts/crypto/adaptive.py

At the start of every quarter from 2022, for every coin, each candidate (a
strategy with its inputs, or holding the coin) is scored on that coin's last
12 months only; the best one trades the coin in the next quarter. Nothing of
a quarter is seen before it is traded. The portfolio splits its value equally
between the coins each quarter. Fixed strategies, holding and BTC are run the
same way for comparison. Amounts are from a 100,000 $ start.
"""

from __future__ import annotations

import itertools
import json
import types
from collections import Counter
from datetime import date, timedelta

import numpy as np
import pandas as pd

import crypto_lab as lab
from marketalyzer import blind, services
from marketalyzer.scripting import ta

COSTS, SLIP = lab.M["costs"], lab.M["slippage"]
START, END = date(2022, 1, 1), date(2026, 10, 3)
CANDIDATES = (
    [("supertrend", {"factor": f, "atrPeriod": a}) for f, a in
     itertools.product((1.5, 2.0, 2.5, 3.0, 3.5, 4.0), (7, 10, 14, 21))]
    + [("sma_cross", {"fastLength": f, "slowLength": s}) for f, s in
       itertools.product((10, 20, 30), (50, 100, 150))]
    + [("donchian_breakout", {"entryLength": e, "exitLength": x}) for e, x in
       itertools.product((20, 40, 60), (10, 20))]
    + [("macd_trend", {"trendLength": t}) for t in (50, 100, 200)]
    + [("coklu_trend", {}), ("hold", {})]
)  # fmt: skip
FIXED = ("supertrend", {"factor": 2.0, "atrPeriod": 10})  # supertrend_sik


def quarters():
    starts = pd.date_range(START, END, freq="QS")
    return [(s.date(), min((s + pd.offsets.QuarterEnd(0)).date(), END)) for s in starts]


frames = {}
for code in lab.mx.CRYPTO_15:
    frame, _ = services.load_bars(code, date(2019, 1, 1), END, "1d")
    frames[code] = frame
btc = frames["BTC-USD"]["Close"]
btc_up = (btc > pd.Series(ta.sma(btc.to_numpy(float), 50), index=btc.index))
config = blind.BlindConfig(symbols=["BTC-USD"], start=START, end=END, script="supertrend",
                           mode="signals", costs=COSTS, slippage=SLIP, cash=lab.CASH)
scripts = {name: blind._script(name, None) for name in {c[0] for c in CANDIDATES} - {"hold"}}
results = {}


def result(code, name, inputs, filtered):
    key = (code, name, json.dumps(inputs, sort_keys=True), filtered)
    if key not in results:
        raw_key = key[:3] + (False,)
        if raw_key not in results:
            results[raw_key] = scripts[name].run(frames[code], inputs, symbol=code, interval="1d")
        raw = results[raw_key]
        if filtered:
            up = btc_up.reindex(frames[code].index).fillna(False).to_numpy()
            raw = types.SimpleNamespace(
                entries=np.asarray(raw.entries, dtype=bool) & up, exits=raw.exits,
                stops=raw.stops, limits=raw.limits, loss_ticks=raw.loss_ticks,
                profit_ticks=raw.profit_ticks)
        results[key] = raw
    return results[key]


def window(code, a, b):
    """Return the frame cut at ``b`` and the index of the first bar from ``a``."""
    frame = frames[code]
    days = pd.DatetimeIndex(frame.index).normalize()
    cut = frame[days <= pd.Timestamp(b)]
    first = int(np.searchsorted(pd.DatetimeIndex(cut.index), pd.Timestamp(a)))
    return cut, first


def simulate(code, name, inputs, a, b, filtered=False):
    """Return (return %, max drawdown %) of a candidate on ``code`` from ``a`` to ``b``."""
    cut, first = window(code, a, b)
    if first >= len(cut) - 1:
        return None
    if name == "hold":
        buy = float(cut["Open"].iloc[first]) * (1 + SLIP / 2) * (1 + COSTS.effective_rate)
        closes = cut["Close"].iloc[first:].to_numpy(float) * (1 - SLIP / 2) * (1 - COSTS.effective_rate)
        curve = closes / buy
    else:
        data = blind.Data(code, cut, first, first, result(code, name, inputs, filtered), {})
        sim = blind.Simulation(data, config, 1_000_000).run()
        curve = np.array([v for _, v in sim.equity]) / 1_000_000
    if not len(curve):
        return None
    drawdown = float((curve / np.maximum.accumulate(curve) - 1).min()) * 100
    return (float(curve[-1]) - 1) * 100, drawdown


def eligible(a):
    need = pd.Timestamp(a - timedelta(days=365))
    return [c for c in frames if pd.Timestamp(frames[c].index[0]) <= need]


def choose(code, a, score, with_hold, filtered):
    """Pick the candidate that did best on ``code`` in the 12 months before ``a``."""
    train = (a - timedelta(days=365), a - timedelta(days=1))
    best, best_score = None, -np.inf
    for name, inputs in CANDIDATES:
        if name == "hold" and not with_hold:
            continue
        found = simulate(code, name, inputs, *train, filtered=filtered and name != "hold")
        if found is None:
            continue
        ret, dd = found
        value = ret if score == "getiri" else ret / max(abs(dd), 5.0)
        if value > best_score:
            best, best_score = (name, inputs), value
    return best


VARIANTS = {
    "Al-tut (eşit ağırlık)": ("fixed", ("hold", {}), False),
    "Sabit supertrend_sik": ("fixed", FIXED, False),
    "Sabit supertrend_sik + BTC 50g": ("fixed", FIXED, True),
    "Adaptif, getiriye göre": ("adapt", ("getiri", True), False),
    "Adaptif, getiriye göre + BTC 50g": ("adapt", ("getiri", True), True),
    "Adaptif, getiri/düşüşe göre": ("adapt", ("oran", True), False),
    "Adaptif, getiri/düşüşe göre + BTC 50g": ("adapt", ("oran", True), True),
    "Adaptif, elde tutmasız + BTC 50g": ("adapt", ("getiri", False), True),
}
rows, picks = [], {v: Counter() for v in VARIANTS}
for a, b in quarters():
    coins = eligible(a)
    row = {"çeyrek": f"{a:%Y-%m}", "coin": len(coins)}
    for variant, (kind, spec, filtered) in VARIANTS.items():
        rets = []
        for code in coins:
            if kind == "fixed":
                name, inputs = spec
            else:
                name, inputs = choose(code, a, spec[0], spec[1], filtered)
                picks[variant][name] += 1
            found = simulate(code, name, inputs, a, b, filtered=filtered and name != "hold")
            rets.append(found[0] if found else 0.0)
        row[variant] = round(float(np.mean(rets)), 2)
    btc_cut, first = window("BTC-USD", a, b)
    row["BTC"] = round((float(btc_cut["Close"].iloc[-1]) / float(btc_cut["Open"].iloc[first]) - 1) * 100, 2)
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)

table = pd.DataFrame(rows).set_index("çeyrek")
names = list(VARIANTS) + ["BTC"]
money = {n: 100_000 * float(np.prod(1 + table[n] / 100)) for n in names}
last = {n: 100_000 * (1 + table[n].iloc[-1] / 100) for n in names}
year = table.index.str[:4]
yearly = {n: table[n].groupby(year).apply(lambda r: (np.prod(1 + r / 100) - 1) * 100).round(1) for n in names}
print("\n100.000 $ ile, 2022 başı → 2026 Ekim (çeyreklik yeniden dağıtım):")
for n in names:
    print(f"  {n:42} {money[n]:>12,.0f} $ | son çeyrek {last[n]:>9,.0f} $ | yıllar "
          + " ".join(f"{y}:{v:>7}" for y, v in yearly[n].items()))
print("\nAdaptif seçimlerin dağılımı:")
for variant, counter in picks.items():
    if counter:
        print(f"  {variant}: {dict(counter.most_common())}")
table.to_csv("adaptive_quarters.csv")
