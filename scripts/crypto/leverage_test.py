"""The paper account's rule with leverage, 2021 → 2026-10, from 100,000 $.

    uv run python scripts/crypto/leverage_test.py

Same entries and exits as the paper accounts (top 5 and top 3); positions are
isolated-margin perpetuals: 0.05 % taker fee, 0.1 % round-trip slippage,
funding paid by longs (10 % a year, and 20 % as a stress case), liquidation at
1 % maintenance margin checked against each day's low. "spot" is the paper
account itself (0.1 % fee, no funding, no leverage).
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

from marketalyzer import services
from marketalyzer.crypto import leverage, momentum, neural

START, SPLIT, END = date(2021, 1, 1), date(2024, 1, 1), date(2026, 10, 3)
MONEY = 100_000

frames = {}
for code in momentum.UNIVERSE:
    frame = services.load_bars(code, date(2018, 1, 1), END, "1d")[0]
    frames[code] = frame[pd.DatetimeIndex(frame.index).normalize() <= pd.Timestamp(END)]
closes, experts = neural.experts_of(frames, START)
lows = leverage.lows_of(frames)
first = int(np.searchsorted(closes.index, pd.Timestamp(START)))
split = int(np.searchsorted(closes.index, pd.Timestamp(SPLIT)))
y2022 = int(np.searchsorted(closes.index, pd.Timestamp("2022-01-01")))
PERIODS = {"2021-23": (first, split), "2024-26": (split, len(closes)),
           "2022-26": (y2022, len(closes)), "2021-26": (first, len(closes))}  # fmt: skip


def measure(values, a, b):
    curve = values[a - 1 : b] / values[a - 1]
    peak = np.maximum.accumulate(curve)
    logs = np.diff(np.log(np.maximum(curve, 1e-12)))
    return {"para": round(MONEY * curve[-1]), "düşüş": round(float((curve / peak - 1).min()) * 100, 1),
            "sharpe": round(float(logs.mean() / logs.std() * np.sqrt(365)), 2)}  # fmt: skip


CASES = [("spot", leverage.Terms(1.0, fee=0.001, slippage=0.001, funding=0.0))]
CASES += [(f"{x:g}x, fonlama %{f * 100:g}", leverage.Terms(x, funding=f))
          for f in (0.10, 0.20) for x in (1.0, 1.5, 2.0, 3.0, 5.0)]  # fmt: skip
for top in (5, 3):
    targets = experts[f"en güçlü {top} + supertrend"]
    for name, terms in CASES:
        out = leverage.simulate(closes, lows, targets, top, terms, first)
        row = {"kural": f"en güçlü {top}", "kaldıraç": name, "tasfiye": len(out.liquidations),
               "işlem": out.trades, "fonlama ödendi (başl. katı)": round(out.funding, 2),
               **{p: measure(out.values, a, b) for p, (a, b) in PERIODS.items()}}  # fmt: skip
        if out.liquidations:
            row["tasfiyeler"] = out.liquidations[:8]
        print(json.dumps(row, ensure_ascii=False), flush=True)
