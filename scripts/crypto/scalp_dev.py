"""Scalping development: choose inputs on 25 Aug - 13 Sep, test untouched on 14 Sep - 2 Oct."""

import itertools
import json
from datetime import date
from pathlib import Path

import crypto_lab as lab
from marketalyzer.backtest.costs import BistCosts

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD"]
TRAIN = (date(2026, 8, 25), date(2026, 9, 13))
TEST = (date(2026, 9, 14), date(2026, 10, 2))
LOW, SPOT = (0.0002, 0.0002), (0.001, 0.0002)
HERE = Path(__file__).parent
CASES = [
    ("rsi_reversion", None, "15m", {"length": [7, 14, 21], "lower": [20, 25, 30], "upper": [60, 70, 80]}),
    ("bollinger_reversion", None, "15m", {"length": [20, 30, 40], "mult": [2.0, 2.5, 3.0], "stopPct": [2, 4, 6]}),
    ("bollinger_reversion", None, "5m", {"length": [20, 30, 40], "mult": [2.0, 2.5, 3.0], "stopPct": [2, 4, 6]}),
    ("scalp_rsi2", (HERE / "scalp_rsi2.pine").read_text(encoding="utf-8"), "15m",
     {"entryLevel": [5, 10, 15], "exitLength": [3, 5, 8], "stopAtr": [1.0, 1.5, 2.0]}),
]


def run(name, source, interval, window, fee, inputs):
    rate, slip = fee
    return lab.run(name, COINS, window[0], window[1], 0.05, interval, costs=BistCosts(rate, 0.0),
                   slippage=slip, inputs=inputs, source=source)


out = open("scalp_dev.jsonl", "a", encoding="utf-8")
for name, source, interval, grid in CASES:
    keys = list(grid)
    scored = []
    for values in itertools.product(*grid.values()):
        inputs = dict(zip(keys, values, strict=True))
        r = run(name, source, interval, TRAIN, LOW, inputs)
        scored.append((r["return"], r["trades"], inputs))
    scored.sort(key=lambda x: -x[0])
    best_ret, best_trades, best = scored[0]
    default_test = {f: run(name, source, interval, TEST, fee, {}) for f, fee in (("düşük", LOW), ("spot", SPOT))}
    tuned_test = {f: run(name, source, interval, TEST, fee, best) for f, fee in (("düşük", LOW), ("spot", SPOT))}
    row = {"script": name, "interval": interval, "chosen": best, "train_return": best_ret, "train_trades": best_trades,
           "test": {f: {"tuned": tuned_test[f]["return"], "tuned_money": tuned_test[f]["money_end"], "tuned_trades": tuned_test[f]["trades"],
                        "default": default_test[f]["return"], "default_money": default_test[f]["money_end"]} for f in tuned_test},
           "hold": tuned_test["düşük"]["hold"], "hold_money": tuned_test["düşük"]["hold_money"]}
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.flush()
    print(json.dumps(row, ensure_ascii=False), flush=True)
