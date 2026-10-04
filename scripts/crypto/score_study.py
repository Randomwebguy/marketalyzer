"""What in the 15-minute confidence score predicts a winning trade?

    uv run python scripts/crypto/score_study.py

For every 15-minute close of the ten most active Binance perpetuals over
the last 180 days, the experiment's trade is simulated for both sides
(stop 1 ATR, target 1.5 ATR, at most 16 bars, stop first when both are in
one bar, 0.14 % round-trip cost). Then:

1. win rate and net return by score bucket;
2. the rank correlation of each score part, and of a few candidate signals,
   with the net trade return;
3. a learned score: logistic regression on the parts and candidates, fitted
   on the first 120 days and judged on the last 60 (never seen), against
   the hand-made score at the same number of trades.

The coins are today's most active ones, so the set carries some hindsight.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, confidence, universe

DAYS, TEST_DAYS = 180, 60
COST = 2 * (0.0005 + 0.0002)
STOP, TAKE, HOLD = 1.0, 1.5, 16
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


def raw_bars(symbol: str) -> pd.DataFrame:
    """Return 15-minute bars with taker-buy volume, finished bars only."""
    rows, start = [], NOW - timedelta(days=DAYS + 5)
    while start < NOW:
        batch = binance._get(f"{binance.FUTURES}/klines", symbol=symbol, interval="15m",
                             startTime=int(start.timestamp() * 1000), limit=1000).json()  # fmt: skip
        if not batch:
            break
        rows += batch
        start = datetime.fromtimestamp(batch[-1][0] / 1000 + 900, timezone.utc)
    frame = pd.DataFrame(rows).drop_duplicates(0)
    out = frame[[1, 2, 3, 4, 5, 9]].astype(float)
    out.columns = ["Open", "High", "Low", "Close", "Volume", "TakerBuy"]
    out.index = pd.to_datetime(frame[0].astype("int64"), unit="ms")
    ends = pd.to_datetime(frame[6].astype("int64") + 1, unit="ms")
    return out[ends.to_numpy() <= pd.Timestamp(NOW.replace(tzinfo=None))]


def outcomes(frame: pd.DataFrame, atr: np.ndarray, sign: int) -> np.ndarray:
    """Return each bar's net trade return (fraction), entering at its close."""
    o, h, low, c = (frame[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    out = np.full(len(c), np.nan)
    for t in range(len(c) - HOLD - 1):
        if np.isnan(atr[t]):
            continue
        entry = c[t]
        stop, take = entry - sign * STOP * atr[t], entry + sign * TAKE * atr[t]
        result = None
        for u in range(t + 1, t + 1 + HOLD):
            worst, best = (low[u], h[u]) if sign > 0 else (h[u], low[u])
            if sign * (worst - stop) <= 0:
                fill = o[u] if sign * (o[u] - stop) <= 0 else stop
                result = sign * (fill / entry - 1)
                break
            if sign * (best - take) >= 0:
                fill = o[u] if sign * (o[u] - take) >= 0 else take
                result = sign * (fill / entry - 1)
                break
        if result is None:
            result = sign * (c[t + HOLD] / entry - 1)
        out[t] = result - COST
    return out


def candidates(frame: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """Signals the score does not use yet, each read at the bar's close."""
    c = frame["Close"]
    logs = np.log(c).diff()
    flow = (2 * frame["TakerBuy"] - frame["Volume"]) / frame["Volume"].replace(0, np.nan)
    btc_close = btc["Close"].reindex(frame.index).ffill()
    vol = logs.rolling(96).std()
    return pd.DataFrame({
        "ret_1h": c.pct_change(4), "ret_4h": c.pct_change(16), "ret_24h": c.pct_change(96),
        "flow_1h": flow.rolling(4).mean(), "flow_4h": flow.rolling(16).mean(),
        "btc_ret_1h": btc_close.pct_change(4), "btc_ret_4h": btc_close.pct_change(16),
        "vol_24h": vol, "vol_ratio": logs.rolling(16).std() / vol,
        "hour": frame.index.hour.to_numpy(),
    }, index=frame.index)  # fmt: skip


def spearman(x: pd.Series, y: pd.Series) -> float:
    ok = x.notna() & y.notna()
    return float(x[ok].rank().corr(y[ok].rank()))


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0) -> np.ndarray:
    """L2-regularized logistic regression by Newton steps (intercept first)."""
    x = np.c_[np.ones(len(x)), x]
    w = np.zeros(x.shape[1])
    for _ in range(25):
        p = 1 / (1 + np.exp(-x @ w))
        gradient = x.T @ (p - y) + l2 * np.r_[0, w[1:]]
        hessian = (x * (p * (1 - p))[:, None]).T @ x + l2 * np.diag(np.r_[0, np.ones(len(w) - 1)])
        w -= np.linalg.solve(hessian, gradient)
    return w


watch = binance.futures_active(10, universe.STABLE)
frames = {s: raw_bars(s) for s in sorted(set(watch) | {"BTCUSDT"})}
rows = []
for symbol in watch:
    frame = frames[symbol]
    table = confidence.scores(frame[["Open", "High", "Low", "Close", "Volume"]])
    extra = candidates(frame, frames["BTCUSDT"])
    for side, sign in (("long", 1), ("short", -1)):
        part = pd.DataFrame({"symbol": symbol, "side": side, "score": table[side]}, index=frame.index)
        for k in confidence.WEIGHTS:
            part[k] = table[f"{side}_{k}"]
        for k in extra:  # signed so that "higher = better for this side"
            part[k] = extra[k] if k in ("vol_24h", "vol_ratio", "hour") else sign * extra[k]
        part["net"] = outcomes(frame, table["atr"].to_numpy(), sign)
        rows.append(part)
data = pd.concat(rows).dropna(subset=["score", "net"])
data = data[data.index >= pd.Timestamp((NOW - timedelta(days=DAYS)).replace(tzinfo=None))]
data["win"] = (data["net"] > 0).astype(float)
split = pd.Timestamp((NOW - timedelta(days=TEST_DAYS)).replace(tzinfo=None))
print(json.dumps({"coins": watch, "rows": len(data), "from": str(data.index.min()),
                  "test from": str(split)}))  # fmt: skip

buckets = pd.cut(data["score"], [0, 30, 50, 60, 70, 80, 100])
by = data.groupby([data["side"], buckets], observed=True)["net"]
print("\n1) Score bucket -> trades, win rate, mean net return per trade (%)")
print((pd.DataFrame({"trades": by.size(), "win %": by.apply(lambda r: (r > 0).mean() * 100),
                     "net %": by.mean() * 100}).round(2)).to_string())  # fmt: skip

features = list(confidence.WEIGHTS) + [c for c in data.columns if c.startswith(("ret_", "flow_", "btc_", "vol_"))]
print("\n2) Rank correlation with the net trade return (all 180 days; + = helps)")
corr = {k: {side: round(spearman(g[k], g["net"]), 4) for side, g in data.groupby("side")} for k in features + ["score"]}
print(pd.DataFrame(corr).T.to_string())

print("\n3) Learned score vs the hand-made score on the last 60 days (never fitted)")
for side, g in data.groupby("side"):
    g = g.dropna(subset=features)
    train, test = g[g.index < split], g[g.index >= split]
    mean, std = train[features].mean(), train[features].std().replace(0, 1)
    w = fit_logistic(((train[features] - mean) / std).to_numpy(), train["win"].to_numpy())
    p = 1 / (1 + np.exp(-(np.c_[np.ones(len(test)), ((test[features] - mean) / std).to_numpy()] @ w)))
    test = test.assign(p=p)
    for share in (0.01, 0.05, 0.20):
        n = max(1, int(len(test) * share))
        hand = test.nlargest(n, "score")
        learned = test.nlargest(n, "p")
        print(f"  {side:5} best {share:4.0%} of bars ({n:5}): hand-made win {hand['win'].mean():.1%} "
              f"net {hand['net'].mean() * 100:+.3f}% | learned win {learned['win'].mean():.1%} "
              f"net {learned['net'].mean() * 100:+.3f}%")
    top = sorted(zip(features, w[1:], strict=True), key=lambda kv: -abs(kv[1]))[:6]
    print(f"  {side} strongest learned weights: " + ", ".join(f"{k} {v:+.2f}" for k, v in top))
print(f"\nBreak-even win rate for 1.5:1 with costs ≈ {(1 + COST / 0.006) / 2.5:.0%} (stop ~0.6 %)")
