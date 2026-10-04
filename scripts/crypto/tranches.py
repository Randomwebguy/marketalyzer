"""Timing luck of the sized quarterly rule, and three staggered tranches.

    uv run python scripts/crypto/tranches.py

Same point-in-time universe as pit_research.py. The quarterly rule (top 5,
supertrend, BTC filter) sized to 40 % volatility is run from all 13 weekly
offsets of its quarter, and as three tranches whose quarters start a month
apart (each holds a third). Splitting cannot raise the expected result; it
should narrow how much the start date matters (Hoffstein et al.).
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, evaluate, neural, strategies, universe

START, SPLIT, END = date(2020, 1, 1), date(2024, 1, 1), date(2026, 10, 3)
RISKY = {"band": 0.005, "relative": 0.25}

pairs = {p.stem: binance.history(p.stem, refresh=False) for p in binance.cache_dir().glob("*.csv")}
candles = universe.coins(pairs)
months = universe.monthly(candles, date(2019, 1, 1), END, size=20)
used = sorted({c for m in months.values() for c in m} | {"BTC"})
candles = {c: candles[c][candles[c].index <= pd.Timestamp(END)] for c in used}
closes = strategies.table(candles)
closes = closes[closes.index >= pd.Timestamp("2018-01-01")]
members = universe.membership(months, closes.index).reindex(columns=closes.columns, fill_value=False)
first = int(np.searchsorted(closes.index, pd.Timestamp(START)))
split = int(np.searchsorted(closes.index, pd.Timestamp(SPLIT)))
btc50 = neural._above(closes["BTC"], 50)
flags = neural._flags(candles, closes, "supertrend", {"factor": 2.0, "atrPeriod": 10})


def held(offset):
    return strategies.rule_held(closes, members, flags, btc50, START, 5, offset_days=offset)


def result(targets, trade):
    book = strategies.run(closes, targets, first, **trade)
    return {p: evaluate.measure(book.values, closes.index, a, b)
            for p, (a, b) in {"2020-23": (first, split), "2024-26": (split, len(closes))}.items()}  # fmt: skip


rows = {}
for week in range(13):
    h = held(7 * week)
    rows[week] = {"tek, eşit 1/5": result(h / 5, {}),
                  "tek, oynaklık %40": result(strategies.risk_weights(h, closes, 0.40), RISKY)}  # fmt: skip
for name in ("tek, eşit 1/5", "tek, oynaklık %40"):
    for period in ("2020-23", "2024-26"):
        money = [rows[w][name][period]["money"] for w in rows]
        sharpe = [rows[w][name][period]["sharpe"] for w in rows]
        print(json.dumps({"yöntem": name, "dönem": period, "para en düşük/ortanca/en yüksek":
                          [min(money), int(np.median(money)), max(money)],
                          "Sharpe en düşük/ortanca/en yüksek": [min(sharpe), float(np.median(sharpe)), max(sharpe)]},
                         ensure_ascii=False))  # fmt: skip

for start_week in range(4):  # three tranches a month apart, from four different starts
    parts = [held(7 * start_week + 30 * k) for k in range(3)]
    equal = sum(p / 5 for p in parts) / 3
    sized = sum(strategies.risk_weights(p, closes, 0.40) for p in parts) / 3
    print(json.dumps({"üç dilim, başlangıç kaydırması (hafta)": start_week,
                      "eşit 1/5": result(equal, {"band": 0.005, "relative": 0.25}),
                      "oynaklık %40": result(sized, RISKY)}, ensure_ascii=False))  # fmt: skip
