"""The 4-hour trend accounts (no martingale) on the point-in-time universe.

    uv run python scripts/crypto/binance_fetch.py      # daily candles
    uv run python scripts/crypto/binance_fetch.py 4h   # 4-hour candles
    uv run python scripts/crypto/swing_test.py

Rules fixed before testing (marketalyzer/crypto/swing.py). Each month the
universe is the top 20 Binance coins by volume (delisted coins included); a
coin can be traded only once its perpetual existed (its funding history has
started), and pays or receives the actual funding. Spot 4-hour candles stand
in for the perpetual's. 2020-23 is the training period, 2024 on the test.
The daily trend filter is the recommendation; the runs without it show what
the filter does. Amounts from 10,000 $.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, evaluate, strategies, swing, universe

START, SPLIT, END = date(2020, 1, 1), date(2024, 1, 1), date(2026, 10, 3)
CASH = 10_000.0

daily_pairs = {
    p.stem: binance.history(p.stem, refresh=False)
    for p in binance.cache_dir().glob("*.csv")
}
daily = universe.coins(daily_pairs)
months = universe.monthly(daily, date(2019, 1, 1), END, size=20)
names = sorted({c for m in months.values() for c in m})
bars4 = {}
for symbol in {n.split("#")[0] + "USDT" for n in names}:
    path = binance.cache_dir("4h") / f"{symbol}.csv"
    if path.exists():
        bars4.update(
            universe.segments(
                symbol, binance.history(symbol, refresh=False, interval="4h")
            )
        )
names = [n for n in names if n in bars4 and n in daily]
index = sorted(set().union(*(bars4[n].index for n in names)))
index = pd.DatetimeIndex([t for t in index if pd.Timestamp(START) - pd.Timedelta(days=60) <= t
                          and t < pd.Timestamp(END + timedelta(days=1))])  # fmt: skip
member = universe.membership(months, index).reindex(columns=names, fill_value=False)
funding = {}
for n in names:
    rate = binance.funding(n.split("#")[0] + "USDT", END + timedelta(days=1))
    funding[n] = rate
first_funding = {n: (f.index[0] if len(f) else None) for n, f in funding.items()}
print(json.dumps({"coins": len(names), "bars": len(index),
                  "with perpetual history": sum(v is not None for v in first_funding.values())}))  # fmt: skip


def run(rules: swing.Rules) -> dict:
    sig = {n: swing.signals(bars4[n], daily[n]["Close"], rules) for n in names}
    last_bar = {n: bars4[n].index[-1] for n in names}
    account = swing.Account(CASH, CASH)
    trades, curve = [], []
    for t in index:
        stamp = (t + pd.Timedelta(hours=4)).isoformat()
        closes = {}
        for n in list(account.positions):
            frame = bars4[n]
            if t not in frame.index:
                continue
            bar = frame.loc[t]
            closes[n] = float(bar["Close"])
            done = swing.check(account, rules, n, float(bar["Open"]), float(bar["High"]),
                               float(bar["Low"]), stamp)  # fmt: skip
            if done:
                trades.append(done)
                continue
            day = t.normalize()
            rate = funding[n].get(day, 0.0003) / swing.BARS_PER_DAY
            swing.fund(account, rules, n, float(rate), float(bar["Close"]))
            swing.trail(account, rules, n, float(sig[n].at[t, "trail"]))
            account.positions[n].bars += 1
            if t == last_bar[n]:  # no more candles: sold at the last close
                trades.append(
                    swing.close(
                        account, rules, n, float(bar["Close"]), "listeden çıktı", stamp
                    )
                )
        if t >= pd.Timestamp(START):
            candidates = []
            for n in np.array(names)[member.loc[t].to_numpy()]:
                s = sig[n]
                if t not in s.index or not s.at[t, "enter"] or n in account.positions:
                    continue
                if first_funding[n] is None or first_funding[n] > t:
                    continue
                close_price = float(bars4[n].at[t, "Close"])
                closes[n] = close_price
                candidates.append({"symbol": n, "close": close_price, "atr": float(s.at[t, "atr"]),
                                   "strength": float(s.at[t, "strength"])})  # fmt: skip
            swing.open_positions(account, rules, candidates, closes, stamp)
        prices = {n: float(bars4[n]["Close"].asof(t)) for n in account.positions}
        curve.append(swing.value(account, prices, rules))
    values = pd.Series(curve, index=index + pd.Timedelta(hours=4))
    return {"account": account, "trades": trades, "values": values}


def summary(result: dict) -> dict:
    values = result["values"]
    days = values.groupby(values.index.normalize()).last()
    days = days[days.index >= pd.Timestamp(START)]
    base = pd.Series([CASH], index=[pd.Timestamp(START) - pd.Timedelta(days=1)])
    days = pd.concat([base, days])
    split = int(np.searchsorted(days.index, pd.Timestamp(SPLIT)))
    out = {}
    for label, a, b in (("2020-23", 1, split), ("2024-26", split, len(days))):
        m = evaluate.measure(days.to_numpy(), days.index, a, b)
        trades = [x for x in result["trades"]
                  if (x["closed"] < SPLIT.isoformat()) == (label == "2020-23")]  # fmt: skip
        r = np.array([x["r"] for x in trades]) if trades else np.array([0.0])
        wins = r[r > 0]
        losses = r[r <= 0]
        out[label] = {
            "para (10 bin $'dan)": round(m["money"] / 10), "düşüş %": m["drawdown"],
            "sharpe": m["sharpe"], "yıllar": m["years"], "işlem": len(trades),
            "isabet %": round(len(wins) / max(len(r), 1) * 100, 1),
            "ort. kazanç R": round(float(wins.mean()), 2) if len(wins) else 0,
            "ort. kayıp R": round(float(losses.mean()), 2) if len(losses) else 0,
            "işlem başı R": round(float(r.mean()), 3),
            "komisyon $": round(sum(x["fees"] for x in trades)),
            "fonlama $": round(sum(x["funding"] for x in trades)),
            "tasfiye": sum(x["reason"] == "tasfiye" for x in trades),
        }  # fmt: skip
    return out


for side in ("long", "short"):
    for daily_filter in (True, False):
        rules = swing.Rules(side=side, daily_filter=daily_filter)
        result = run(rules)
        print(json.dumps({"hesap": side, "günlük trend filtresi": daily_filter,
                          **summary(result)}, ensure_ascii=False), flush=True)  # fmt: skip

btc = bars4["BTC"]["Close"]
for label, a, b in (("2020-23", START, SPLIT), ("2024-26", SPLIT, END)):
    seg = btc[(btc.index >= pd.Timestamp(a)) & (btc.index < pd.Timestamp(b))]
    print(
        json.dumps(
            {
                "BTC al-tut": label,
                "para": round(CASH * float(seg.iloc[-1] / seg.iloc[0])),
            }
        )
    )
