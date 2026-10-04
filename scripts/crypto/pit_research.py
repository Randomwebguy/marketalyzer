"""The research round on Binance's point-in-time universe (no survivorship bias).

    uv run python scripts/crypto/binance_fetch.py   # once, fills the cache
    uv run python scripts/crypto/pit_research.py

Every month the universe is the top 20 Binance USDT coins by 30-day median
volume, listed for a year, delisted coins included. Candidates were fixed
before running (docs/superpowers/plans/2026-10-04-crypto-research-round.md);
the pick is the best Sharpe of 2020-23 alone, judged on 2024 → 2026-10.
Amounts are from 100,000 $; costs 0.1 % fee + 0.05 % slippage per side, and
twice that as a stress test.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, evaluate, neural, strategies, universe

START, SPLIT, END = date(2020, 1, 1), date(2024, 1, 1), date(2026, 10, 3)
EARLIER_TRIALS = 150  # variants tried in earlier rounds (grids, Hedge, adaptive, ...)
RISKY = {"band": 0.005, "relative": 0.25}  # rebalance a sized position when 25 % off

pairs = {p.stem: binance.history(p.stem, refresh=False) for p in binance.cache_dir().glob("*.csv")}
candles = universe.coins(pairs)
months = universe.monthly(candles, date(2019, 1, 1), END, size=20)
used = sorted({c for m in months.values() for c in m} | {"BTC"})
candles = {c: candles[c][candles[c].index <= pd.Timestamp(END)] for c in used}
closes = strategies.table(candles)
closes = closes[closes.index >= pd.Timestamp("2018-01-01")]
members = universe.membership(months, closes.index).reindex(columns=closes.columns, fill_value=False)
member = members.to_numpy()
first = int(np.searchsorted(closes.index, pd.Timestamp(START)))
split = int(np.searchsorted(closes.index, pd.Timestamp(SPLIT)))
rows = len(closes)
print(json.dumps({"evrene giren coin sayısı": len(used), "gün": rows,
                  "başlangıç": str(closes.index[0].date()),
                  "universe 2026-10": months[date(2026, 10, 1)]}))  # fmt: skip

btc = closes["BTC"]
btc50 = neural._above(btc, 50)
regime = strategies.btc_regime(btc)
flags = neural._flags(candles, closes, "supertrend", {"factor": 2.0, "atrPeriod": 10})
donchian = np.column_stack([strategies.donchian(closes[c].to_numpy()) for c in closes.columns])
donchian_in = donchian * member


def rule(top, offset=0):
    return strategies.rule_held(closes, members, flags, btc50, START, top, offset_days=offset)


held5, held3, held_all = rule(5), rule(3), rule(None)
sized = lambda signal, vol: strategies.risk_weights(signal, closes, vol)  # noqa: E731
CANDIDATES = {
    "kural en güçlü 5 (sanal hesap)": (held5 / 5, {}),
    "kural en güçlü 3": (held3 / 3, {}),
    "A25: kural 5 + oynaklık %25": (sized(held5, 0.25), RISKY),
    "A40: kural 5 + oynaklık %40": (sized(held5, 0.40), RISKY),
    "B25: sıralamasız supertrend + oynaklık %25": (sized(held_all, 0.25), RISKY),
    "B40: sıralamasız supertrend + oynaklık %40": (sized(held_all, 0.40), RISKY),
    "C25: Donchian topluluğu + oynaklık %25": (sized(donchian_in, 0.25), RISKY),
    "C40: Donchian topluluğu + oynaklık %40": (sized(donchian_in, 0.40), RISKY),
    "E25: Donchian + BTC rejimi + oynaklık %25": (sized(donchian_in, 0.25) * regime[:, None], RISKY),
    "F: haftalık 4 hafta momentum, en güçlü 5": (
        strategies.weekly_momentum(closes, members, btc50, START), {}),
}  # fmt: skip
BENCHMARKS = {
    "evren eşit ağırlık (aylık)": (np.nan_to_num(member / member.sum(axis=1, keepdims=True)),
                                   {"band": 0.0, "relative": 0.5}),
    "BTC al-tut": (np.tile((closes.columns == "BTC").astype(float), (rows, 1)), {}),
}  # fmt: skip
PERIODS = {"2020-23": (first, split), "2024-26": (split, rows),
           "2021-23": (int(np.searchsorted(closes.index, pd.Timestamp("2021-01-01"))), split),
           "2020-26": (first, rows)}  # fmt: skip

books, report = {}, {}
for name, (targets, trade) in {**CANDIDATES, **BENCHMARKS}.items():
    book = strategies.run(closes, targets, first, **trade)
    stress = strategies.run(closes, targets, first, cost=2.0, **trade)
    books[name] = book
    exposure = book.weights[first:].sum(axis=1)
    report[name] = {p: evaluate.measure(book.values, closes.index, a, b) for p, (a, b) in PERIODS.items()}
    report[name]["2024-26 maliyet x2"] = evaluate.measure(stress.values, closes.index, split, rows)
    report[name]["ortalama brüt pozisyon"] = round(float(exposure.mean()), 2)
    report[name]["işlem hacmi (başl. katı)"] = round(book.traded, 1)
    print(json.dumps({"yöntem": name, **report[name]}, ensure_ascii=False), flush=True)

names = list(CANDIDATES)
chosen = max(names, key=lambda n: report[n]["2020-23"]["sharpe"])
daily = np.column_stack([books[n].returns()[first:] for n in names])
test_returns = books[chosen].returns()[split:]
trial_sharpes = np.array([evaluate.sharpe(books[n].returns()[split:]) for n in names])
spread = np.r_[trial_sharpes, np.random.default_rng(0).normal(
    trial_sharpes.mean(), trial_sharpes.std(ddof=1), EARLIER_TRIALS)]  # fmt: skip
base = "kural en güçlü 5 (sanal hesap)"
print(json.dumps({
    "seçilen (2020-23 Sharpe)": chosen,
    "test Sharpe seçilen / sanal hesap": [report[chosen]["2024-26"]["sharpe"], report[base]["2024-26"]["sharpe"]],
    "test düşüşü seçilen / sanal hesap": [report[chosen]["2024-26"]["drawdown"], report[base]["2024-26"]["drawdown"]],
    "DSR (yalnız bu turun adayları)": round(evaluate.deflated_sharpe(test_returns, trial_sharpes), 3),
    "DSR (önceki denemeler dahil)": round(evaluate.deflated_sharpe(test_returns, spread), 3),
    "PBO (CSCV, 16 blok, 2020-26)": round(evaluate.overfit_probability(daily), 3),
}, ensure_ascii=False))  # fmt: skip

funding = pd.DataFrame({c: binance.funding(c.split("#")[0] + "USDT", END + timedelta(days=1))
                        for c in closes.columns}).reindex(columns=closes.columns)  # fmt: skip
crowd = strategies.crowded(funding, closes.index)
targets, trade = CANDIDATES[chosen]
overlay = strategies.run(closes, targets * np.where(crowd, 0.5, 1.0), first, **trade)
with_overlay = {p: evaluate.measure(overlay.values, closes.index, a, b) for p, (a, b) in PERIODS.items()}
print(json.dumps({"G: seçilene fonlama kalabalığı katmanı (yarım boyut)": with_overlay,
                  "kalabalık gün payı": round(float(crowd[first:].mean()), 3),
                  "benimsenir mi (2020-23 Sharpe artarsa)":
                      with_overlay["2020-23"]["sharpe"] > report[chosen]["2020-23"]["sharpe"]},
                 ensure_ascii=False))  # fmt: skip

luck = []
for week in range(13):
    book = strategies.run(closes, rule(5, 7 * week) / 5, first)
    luck.append({"kaydırma (hafta)": week,
                 **{p: evaluate.measure(book.values, closes.index, a, b)["money"]
                    for p, (a, b) in PERIODS.items() if p in ("2024-26", "2020-26")}})  # fmt: skip
test_money = [row["2024-26"] for row in luck]
print(json.dumps({"zamanlama şansı, en güçlü 5, 2024-26 parası": {
    "en düşük": min(test_money), "ortanca": int(np.median(test_money)), "en yüksek": max(test_money)},
    "satırlar": luck}, ensure_ascii=False))  # fmt: skip
