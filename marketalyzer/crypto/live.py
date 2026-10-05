"""Run a volatility-sized strategy on a paper account with Binance data.

Called every hour by ``runner.step_all``. On the first finished day of a
month the universe is rebuilt from every listed USDT pair (top ``size`` by
30-day median volume, listed for a year). On each new finished day the
strategy's target weights for that day's close are computed from up to
``HISTORY`` days of candles and the account trades toward them at the latest
prices, only where a position is more than the band away from its target.
Positions are keyed by pair ("BTCUSDT").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests

from marketalyzer.crypto import binance, neural, strategies, universe
from marketalyzer.crypto.ledger import CryptoLedger

HISTORY = 800  # days of candles behind each decision
TRANCHE_DAYS = 30  # tranche k's quarters start k months later
EXCHANGE_INFO = "https://data-api.binance.vision/api/v3/exchangeInfo"
TICKER = "https://data-api.binance.vision/api/v3/ticker/price"

Fetch = Callable[[str, date, date], pd.DataFrame]


@dataclass(frozen=True)
class Plan:
    """What the account trades and how it sizes positions."""

    signal: str = "supertrend"  # "supertrend" (BTC 50-day filter) or "donchian"
    target_vol: float = 0.25
    regime: bool = False  # scale exposure by BTC's 20/50/100/200-day trend
    size: int = 20
    band: float = 0.005
    relative: float = 0.25
    top: int | None = None  # supertrend only: the quarter's strongest coins
    tranches: int = 1  # with ``top``: quarters starting a month apart

    def short(self) -> str:
        """Return a very short name for the account switcher."""
        if self.signal == "donchian":
            return f"Donchian %{self.target_vol * 100:g}"
        return f"Dilimli en güçlü {self.top}" if self.top else "Supertrend"

    def label(self) -> str:
        """Return a short Turkish name for the plan."""
        if self.signal == "supertrend" and self.top:
            name = f"En güçlü {self.top}"
            if self.tranches > 1:
                name += f" · {self.tranches} dilim"
        else:
            name = "Supertrend" if self.signal == "supertrend" else "Donchian topluluğu"
        extra = " + BTC rejimi" if self.regime else ""
        return f"{name}{extra} · oynaklık %{self.target_vol * 100:g}"


def plan_of(ledger: CryptoLedger) -> Plan | None:
    """Return the account's plan, or None for a quarterly-rule account."""
    stored = ledger.get("plan")
    return Plan(**stored) if stored else None


def open_account(
    account_path, plan: Plan, cash: float = 100_000.0, now=None
) -> CryptoLedger:
    """Create an account that trades ``plan``."""
    ledger = CryptoLedger.create(account_path, cash, now=now)
    ledger.put("plan", asdict(plan))
    return ledger


# --- Data ---------------------------------------------------------------------------


def listed_pairs() -> list[str]:
    """Return the USDT spot pairs trading now, without stable or leveraged coins."""
    info = binance._get(EXCHANGE_INFO).json()
    pairs = [s["symbol"] for s in info["symbols"]
             if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"]  # fmt: skip
    return universe.tradable(sorted(pairs))


def fetch_candles(symbol: str, start: date, today: date) -> pd.DataFrame:
    """Return ``symbol``'s finished daily candles from ``start``."""
    return binance.recent_candles(symbol, start, today)


def memo(fetch: Fetch) -> Fetch:
    """Return ``fetch`` remembering its answers for one run."""
    seen: dict[tuple, Any] = {}

    def wrapper(symbol: str, start: date, today: date) -> pd.DataFrame:
        key = (symbol, start, today)
        if key not in seen:
            try:
                seen[key] = fetch(symbol, start, today)
            except requests.RequestException as error:
                seen[key] = error
        if isinstance(seen[key], Exception):
            raise seen[key]
        return seen[key]

    return wrapper


def latest_prices(symbols: list[str]) -> dict[str, float]:
    """Return the last traded price of each pair (pairs no longer listed are left out)."""
    if not symbols:
        return {}
    try:
        batch = json.dumps(symbols, separators=(",", ":"))
        rows = binance._get(TICKER, symbols=batch).json()
    except requests.RequestException:  # one delisted pair fails the whole batch
        rows = []
        for symbol in symbols:
            try:
                rows.append(binance._get(TICKER, symbol=symbol).json())
            except requests.RequestException:
                continue
    return {row["symbol"]: float(row["price"]) for row in rows}


def pick_universe(pairs: list[str], month: date, fetch: Fetch, size: int) -> list[str]:
    """Return ``month``'s universe as pairs, strongest volume first."""
    start = month - timedelta(days=universe.MIN_DAYS + 60)

    def one(pair):
        try:
            return pair, fetch(pair, start, month)
        except requests.RequestException:
            return pair, None

    with ThreadPoolExecutor(8) as pool:
        frames = {p: f for p, f in pool.map(one, pairs) if f is not None and len(f)}
    coins = {}
    for pair, frame in frames.items():
        parts = universe.segments(pair, frame)
        if parts:  # the live coin is the latest segment
            coins[pair] = list(parts.values())[-1]
    return universe.monthly(coins, month, month, size)[month]


# --- Targets ------------------------------------------------------------------------


def targets(
    candles: dict[str, pd.DataFrame], members: list[str], plan: Plan
) -> dict[str, float]:
    """Return the plan's weights after the last day's close."""
    closes = strategies.table(candles)
    index = closes.index
    in_universe = pd.DataFrame(False, index=index, columns=closes.columns)
    in_universe[[m for m in members if m in closes.columns]] = True
    btc = closes["BTCUSDT"]
    if plan.signal == "donchian":
        signal = np.column_stack(
            [strategies.donchian(closes[c].to_numpy()) for c in closes.columns]
        )
        signal = signal * in_universe.to_numpy()
        weights = strategies.risk_weights(signal, closes, plan.target_vol)[-1]
    else:
        flags = neural._flags(
            candles, closes, "supertrend", {"factor": 2.0, "atrPeriod": 10}
        )
        start = index[min(len(index) - 1, 400)].date()
        up = neural._above(btc, 50)
        parts = [  # tranches whose quarters start a month apart, each a share
            strategies.rule_held(
                closes,
                in_universe,
                flags,
                up,
                start,
                plan.top,
                offset_days=TRANCHE_DAYS * k,
            )  # fmt: skip
            for k in range(plan.tranches if plan.top else 1)
        ]
        weights = sum(
            strategies.risk_weights(held, closes, plan.target_vol)[-1] for held in parts
        ) / len(parts)
    if plan.regime:
        weights = weights * strategies.btc_regime(btc)[-1]
    return {c: float(w) for c, w in zip(closes.columns, weights, strict=True) if w > 0}


# --- Step ---------------------------------------------------------------------------


def step(ledger: CryptoLedger, now: datetime | None = None, fetch: Fetch | None = None,
         pairs: Callable[[], list[str]] | None = None,
         prices: Callable[[list[str]], dict[str, float]] | None = None) -> dict[str, Any]:  # fmt: skip
    """Trade the newest finished day if it is new; record the value either way."""
    plan = plan_of(ledger)
    fetch, pairs, prices = (
        fetch or fetch_candles,
        pairs or listed_pairs,
        prices or latest_prices,
    )
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date()
    day = today - timedelta(days=1)
    report: dict[str, Any] = {
        "time": now.isoformat(timespec="seconds"),
        "days": [],
        "fills": [],
    }
    month = date(day.year, day.month, 1)
    if ledger.get("universe_month") != month.isoformat():
        chosen = pick_universe(pairs(), month, fetch, plan.size)
        ledger.put("universe_month", month.isoformat())
        ledger.put("selection", chosen)
        report["selected"] = chosen
    members = ledger.get("selection", [])
    held = list(ledger.positions())
    quotes = prices(sorted(set(members) | set(held) | {"BTCUSDT"}))
    if ledger.get("last_day") != day.isoformat():
        start = day - timedelta(days=HISTORY)
        candles = {}
        for pair in sorted(set(members) | set(held) | {"BTCUSDT"}):
            try:
                frame = fetch(pair, start, today)
            except requests.RequestException:
                continue
            if len(frame):
                candles[pair] = frame
        if "BTCUSDT" in candles:
            goal = targets(candles, members, plan)
            ledger.put("targets", goal)
            _rebalance(ledger, goal, quotes, plan, now, report, day)
            ledger.put("last_day", day.isoformat())
            report["days"].append(day.isoformat())
    ledger.put("prices", quotes)
    report["value"] = round(ledger.record(quotes, now), 2)
    report["selection"] = members
    return report


def _rebalance(ledger, goal, quotes, plan, now, report, day) -> None:
    value = ledger.value(quotes)
    positions = ledger.positions()
    for pair, position in positions.items():  # sells first
        price = quotes.get(pair)
        if price is None:
            continue
        held = position.units * price / value
        want = goal.get(pair, 0.0)
        if want == 0:
            fill = ledger.sell(pair, price, now=now, reason=f"hedef sıfır ({day})")
        elif held - want > plan.band + plan.relative * want:
            units = (held - want) * value / price
            fill = ledger.sell(
                pair, price, units=units, now=now, reason=f"hedefe indir ({day})"
            )
        else:
            continue
        if fill:
            report["fills"].append(fill.to_dict())
    value = ledger.value(quotes)
    positions = ledger.positions()
    for pair, want in sorted(goal.items(), key=lambda kv: -kv[1]):
        price = quotes.get(pair)
        if price is None:
            continue
        held = positions[pair].units * price / value if pair in positions else 0.0
        if pair not in positions or want - held > plan.band + plan.relative * want:
            fill = ledger.buy(pair, (want - held) * value, price, now=now,
                              reason=f"hedefe çık ({day})")  # fmt: skip
            if fill:
                report["fills"].append(fill.to_dict())
