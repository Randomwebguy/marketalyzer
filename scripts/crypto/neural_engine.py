"""Walk the NeuralEngine forward: settings chosen on 2021-23, tested on 2024-26.

    uv run python scripts/crypto/neural_engine.py

The engine runs without a break from 2021-01-01; what it learned in the first
period carries into the second. Its settings (learning rate, floor,
forgetting, market states) are picked by the Sharpe ratio of 2021-23 alone;
2024 onward is reported as is, next to the fixed rule (the paper account),
each expert, the equal mix of the experts, equal-weight hold and BTC.
Amounts are from a 100,000 $ start.
"""

from __future__ import annotations

import itertools
import json
from datetime import date

import numpy as np
import pandas as pd

from marketalyzer import services
from marketalyzer.crypto import momentum, neural

START, SPLIT, END = date(2021, 1, 1), date(2024, 1, 1), date(2026, 10, 3)
MONEY = 100_000

frames = {}
for code in momentum.UNIVERSE:
    frame = services.load_bars(code, date(2018, 1, 1), END, "1d")[0]
    frames[code] = frame[pd.DatetimeIndex(frame.index).normalize() <= pd.Timestamp(END)]
closes, experts = neural.experts_of(frames, START)
first = int(np.searchsorted(closes.index, pd.Timestamp(START)))
split = int(np.searchsorted(closes.index, pd.Timestamp(SPLIT)))
rows = len(closes)
books = {name: neural.simulate(closes, target, first=first) for name, target in experts.items()}
PERIODS = {"2021-23": (first, split), "2024-26": (split, rows), "2021-26": (first, rows)}


def measure(values, a, b):
    """Money, drawdown, Sharpe and yearly returns of rows [a, b), from the close before a."""
    curve = values[a - 1 : b] / values[a - 1]
    logs = np.diff(np.log(curve))
    peak = np.maximum.accumulate(curve)
    days = pd.Series(curve, index=closes.index[a - 1 : b])
    last = days.groupby(days.index.year).last()
    yearly = last / last.shift(1).fillna(1)
    return {"money": round(MONEY * curve[-1]), "dd": round(float((curve / peak - 1).min()) * 100, 1),
            "sharpe": round(float(logs.mean() / logs.std() * np.sqrt(365)), 2) if logs.std() else 0.0,
            "years": {int(y): round((v - 1) * 100, 1) for y, v in yearly.items()}}  # fmt: skip


def hold_values(codes):
    prices = closes[codes].to_numpy(dtype=float)
    return np.nanmean(prices / prices[first - 1], axis=1)


listed = [c for c in closes.columns if not np.isnan(closes[c].iloc[first - 1])]
references = {"al-tut (eşit)": hold_values(listed), "BTC": hold_values([momentum.BTC])}

GRID = [neural.Settings(eta=e, floor=f, forget=g, context=c)
        for e, f, g, c in itertools.product((1.0, 3.0, 10.0, 30.0), (0.02, 0.1),
                                            (0.0, 1 / 60, 1 / 20), neural.CONTEXTS)]  # fmt: skip
EQUAL = neural.Settings(eta=0.0, floor=1.0, context="none")
runs = {}
for settings in [EQUAL, *GRID]:
    book, weights, _ = neural.run(closes, experts, settings, first, books)
    runs[settings] = (book, weights, {p: measure(book.values, a, b) for p, (a, b) in PERIODS.items()})
chosen = max(GRID, key=lambda s: runs[s][2]["2021-23"]["sharpe"])
by_money = max(GRID, key=lambda s: runs[s][2]["2021-23"]["money"])

table = {}
table[f"NeuralEngine (seçilen: {chosen})"] = runs[chosen][2]
table[f"NeuralEngine (2021-23 parası en yüksek: {by_money})"] = runs[by_money][2]
table["uzmanların eşit karışımı"] = runs[EQUAL][2]
for name, book in books.items():
    table[name] = {p: measure(book.values, a, b) for p, (a, b) in PERIODS.items()}
for name, values in references.items():
    table[name] = {p: measure(values, a, b) for p, (a, b) in PERIODS.items()}
for name, result in table.items():
    print(json.dumps({"yöntem": name, **result}, ensure_ascii=False))

fixed = table["en güçlü 5 + supertrend"]["2024-26"]["money"]
test = np.array([runs[s][2]["2024-26"]["money"] for s in GRID])
print(json.dumps({"ayar sayısı": len(GRID), "2024-26 parası: ortanca": int(np.median(test)),
                  "çeyrekler": [int(np.percentile(test, 25)), int(np.percentile(test, 75))],
                  "sabit kuralı geçen ayar oranı": round(float((test > fixed).mean()), 2)},
                 ensure_ascii=False))  # fmt: skip

book, weights, _ = runs[chosen]
states = neural.contexts(closes, chosen.context)
names = list(books)
for state in sorted(set(states[split:])):
    mask = np.zeros(rows, bool)
    mask[split:] = states[split:] == state
    mean = weights[mask].mean(axis=0)
    print(json.dumps({"durum": int(state), "gün": int(mask.sum()),
                      "ortalama ağırlık": {n: round(float(w), 2) for n, w in zip(names, mean, strict=True)}},
                     ensure_ascii=False))  # fmt: skip
print(json.dumps({"son gün": str(closes.index[-1].date()), "durum": int(states[-1]),
                  "ağırlıklar": {n: round(float(w), 2) for n, w in zip(names, weights[-1], strict=True)},
                  "işlem hacmi (başlangıç parasının katı)": round(book.traded, 1)}, ensure_ascii=False))  # fmt: skip
