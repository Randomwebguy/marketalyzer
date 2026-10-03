"""Ensemble: hold each coin in proportion to how many of seven strategies are long.

    uv run python scripts/crypto/ensemble.py

Nothing is fitted. Each strategy's position on each coin comes from its own
signals; the coin's weight is the share of strategies in a position, decided
at a day's close and applied from the next day's close (a day later than the
blind test fills, to stay on the safe side). Weight changes pay 0.1 % plus
0.05 % slippage. The portfolio splits its value equally between the coins
each quarter, like adaptive.py. Amounts are from a 100,000 $ start.
"""

from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd

import crypto_lab as lab
from marketalyzer import blind, services
from marketalyzer.scripting import ta

START, END = date(2022, 1, 1), date(2026, 10, 3)
COST = lab.M["costs"].effective_rate + lab.M["slippage"] / 2
MEMBERS = [
    ("supertrend", {"factor": 3.0, "atrPeriod": 10}), ("supertrend", {"factor": 2.0, "atrPeriod": 10}),
    ("sma_cross", {"fastLength": 10, "slowLength": 50}), ("sma_cross", {"fastLength": 20, "slowLength": 100}),
    ("donchian_breakout", {}), ("coklu_trend", {}), ("macd_trend", {}),
]  # fmt: skip
config = blind.BlindConfig(symbols=["BTC-USD"], start=START, end=END, script="supertrend",
                           mode="signals", costs=lab.M["costs"], slippage=lab.M["slippage"], cash=lab.CASH)


def positions(code, frame):
    """Return, per bar, the share of member strategies holding ``code`` at the close."""
    held = np.zeros(len(frame))
    for name, inputs in MEMBERS:
        result = blind._script(name, None).run(frame, inputs, symbol=code, interval="1d")
        sim = blind.Simulation(blind.Data(code, frame, 1, 1, result, {}), config, 1e9).run()
        on = np.zeros(len(frame))
        for entry, exit_bar in sim.trade_bars:
            on[entry:exit_bar] = 1.0  # bought at the open of ``entry``, sold at the open of ``exit_bar``
        if sim.account.qty:
            on[sim.account.entry_bar:] = 1.0
        held += on
    return held / len(MEMBERS)


frames, weights = {}, {}
for code in lab.mx.CRYPTO_15:
    frame, _ = services.load_bars(code, date(2019, 1, 1), END, "1d")
    frames[code] = frame
    weights[code] = pd.Series(positions(code, frame), index=frame.index)
btc = frames["BTC-USD"]["Close"]
btc_up = pd.Series(btc.to_numpy() > ta.sma(btc.to_numpy(float), 50), index=btc.index)


def quarter_return(code, a, b, filtered):
    close = frames[code]["Close"]
    w = weights[code].copy()
    if filtered:
        w = w.where(btc_up.reindex(w.index).fillna(False), 0.0)
    w = w.shift(1).fillna(0.0)  # decided at a close, held from the next close on
    days = pd.DatetimeIndex(close.index).normalize()
    inside = (days >= pd.Timestamp(a)) & (days <= pd.Timestamp(b))
    if inside.sum() < 2:
        return 0.0
    r = close.pct_change().fillna(0.0)
    held = w.shift(1).fillna(0.0)  # the weight held over each day's move
    step = held * r - w.diff().abs().fillna(0.0) * COST
    first = np.flatnonzero(inside)[0]
    step.iloc[first] -= w.iloc[first] * COST  # (re)entering at the quarter start
    return float((1 + step[inside]).prod() - 1) * 100


rows = []
for a in pd.date_range(START, END, freq="QS"):
    b = min((a + pd.offsets.QuarterEnd(0)).date(), END)
    coins = [c for c in frames if frames[c].index[0] <= a - pd.Timedelta(days=365)]
    row = {"çeyrek": f"{a:%Y-%m}"}
    for label, filtered in (("Topluluk", False), ("Topluluk + BTC 50g", True)):
        row[label] = round(float(np.mean([quarter_return(c, a.date(), b, filtered) for c in coins])), 2)
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
table = pd.DataFrame(rows).set_index("çeyrek")
for label in ("Topluluk", "Topluluk + BTC 50g"):
    money = 100_000 * float(np.prod(1 + table[label] / 100))
    yearly = table[label].groupby(table.index.str[:4]).apply(lambda r: (np.prod(1 + r / 100) - 1) * 100).round(1)
    print(f"{label:22} {money:>12,.0f} $ | son çeyrek {100_000 * (1 + table[label].iloc[-1] / 100):>9,.0f} $ | yıllar "
          + " ".join(f"{y}:{v}" for y, v in yearly.items()))
table.to_csv("ensemble_quarters.csv")
