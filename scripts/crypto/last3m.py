"""The last three months on crypto, every non-AI strategy, from a 100,000 $ start.

    uv run python scripts/crypto/last3m.py [END]

Daily bars on 15 coins (signals; trend strategies also with the BTC 50-day
entry filter; rotation with and without the BTC 200-day cash filter), hourly
bars on 8 coins, and scalping on the scalping window Yahoo allows (59 days).
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import crypto_dev
import crypto_lab as lab
from marketalyzer import blind, rotation
from marketalyzer.backtest.costs import BistCosts

END = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2026, 10, 3)
START = END - timedelta(days=91)
HERE = Path(__file__).parent
rows = []


def add(group, name, r):
    row = {"grup": group, "strateji": name, "getiri_%": r["return"], "bitis_$": r["money_end"],
           "dusus_%": r.get("dd"), "islem": r.get("trades"), "al_tut_$": r.get("hold_money"),
           "btc_%": r.get("btc")}
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


daily = lab.eligible(lab.mx.CRYPTO_15, START, 1.0, "1d")
for script in lab.STRATEGIES:
    add("günlük", script, lab.run(script, daily, START, END, 1.0, "1d"))
for script in ("supertrend_sik", "donchian_breakout", "sma_cross"):
    crypto_dev.RuleDecider.regime, crypto_dev.RuleDecider.exit_evidence = "sma50", False
    blind.EvidenceDecider = crypto_dev.RuleDecider
    add("günlük", f"{script} + BTC 50g filtresi",
        lab.run(script, daily, START, END, 1.0, "1d", "stats", "ask"))
for months, top, market in ((3, 3, False), (3, 3, True), (6, 5, False), (6, 5, True)):
    r = rotation.run_rotation(rotation.RotationConfig(
        symbols=daily, start=START, end=END, lookback_months=months, top=top, market=market,
        cash=lab.CASH, costs=lab.M["costs"], slippage=lab.M["slippage"]))
    rot = r["rotation"]
    add("günlük", f"rotasyon {months} ay / en iyi {top}" + (" + BTC 200g filtresi" if market else ""),
        {"return": rot["return_pct"], "money_end": lab.money(rot["return_pct"]), "dd": rot["max_drawdown_pct"],
         "trades": rot.get("trades"), "hold_money": lab.money(r["equal"]["return_pct"]),
         "btc": (r.get("benchmark") or {}).get("return_pct")})

hourly = lab.eligible(lab.mx.CRYPTO_8, START, 0.5, "1h")
for script in lab.STRATEGIES:
    add("saatlik", script, lab.run(script, hourly, START, END, 0.5, "1h"))

coins = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD"]
s_start = END - timedelta(days=40)
rsi2 = (HERE / "scalp_rsi2.pine").read_text(encoding="utf-8")
cases = [("rsi_reversion 15dk (ayarlı)", "rsi_reversion", None, "15m", {"length": 21, "lower": 30, "upper": 70}),
         ("rsi_reversion 15dk", "rsi_reversion", None, "15m", {}),
         ("bollinger_reversion 5dk (ayarlı)", "bollinger_reversion", None, "5m", {"length": 20, "mult": 3.0, "stopPct": 4}),
         ("bollinger_reversion 5dk", "bollinger_reversion", None, "5m", {}),
         ("scalp_rsi2 15dk (ayarlı)", "scalp_rsi2", rsi2, "15m", {"entryLevel": 5, "exitLength": 3, "stopAtr": 2.0})]
for label, script, source, interval, inputs in cases:
    for fee, rate in (("spot %0,1", 0.001), ("düşük %0,02", 0.0002)):
        add(f"scalping ({fee})", label, lab.run(script, coins, s_start, END, 0.05, interval,
            costs=BistCosts(rate, 0.0), slippage=0.0002, inputs=inputs, source=source))
json.dump(rows, open("last3m.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
