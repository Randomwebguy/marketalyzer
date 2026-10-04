"""The leveraged long and short trend accounts on live Binance futures prices.

Paper only: nothing is sent to an exchange. The rules are ``swing.Rules``
(4-hour breakouts with the daily trend, 1 % risk per trade, trailing stops),
tested in ``scripts/crypto/swing_test.py``. Meant to run every minute:

- every open position is checked against the finished 1-minute bars since
  the last check (trailing stop, liquidation) and pays or receives funding at
  00/08/16 UTC at the actual rate;
- on each finished 4-hour bar the stops are tightened and the universe's
  breakouts are opened, strongest first, within the limits;
- the universe is rebuilt each month: the top 20 Binance coins by 30-day
  median volume (``live.pick_universe``) that have a USDT perpetual;
- the value is recorded at the latest price.

    marketalyzer-leverage init [--cash 10000]
    marketalyzer-leverage step
    marketalyzer-leverage status
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sqlite3
import sys
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, live, swing
from marketalyzer.paper.cli import default_home

ACCOUNTS = ("long", "short")
CASH = 10_000.0
FUNDING_HOURS = (0, 8, 16)
BAR = timedelta(hours=4)


def folder() -> Path:
    """Return where the experiment's accounts live."""
    return default_home() / "leverage"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(timezone.utc)


def _floor(moment: datetime, step: timedelta) -> datetime:
    seconds = step.total_seconds()
    return datetime.fromtimestamp(
        math.floor(moment.timestamp() / seconds) * seconds, timezone.utc
    )


# --- Storage ------------------------------------------------------------------------


class Store:
    """One experiment account in SQLite: rules, account, trades and value curve."""

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Deney hesabı yok: {path}")
        self.path = path
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None)

    @classmethod
    def create(cls, path: Path, rules: swing.Rules, cash: float = CASH,
               now: datetime | None = None) -> Store:  # fmt: skip
        """Create an account with ``cash`` USD."""
        if path.exists():
            raise FileExistsError(f"Deney hesabı zaten var: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE trades (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE equity (time TEXT PRIMARY KEY, value REAL NOT NULL);
        """)  # fmt: skip
        db.close()
        store = cls(path)
        store.put("rules", asdict(rules))
        store.put("account", swing.Account(cash, cash).to_dict())
        store.put("created", _iso(now or datetime.now(timezone.utc)))
        return store

    def close(self) -> None:
        """Close the database."""
        self._db.close()

    def __enter__(self) -> Store:
        """Return the store for a with block."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the store at the end of a with block."""
        self.close()

    def get(self, key: str, default: Any = None) -> Any:
        """Return a stored value."""
        row = self._db.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        """Store a value as JSON."""
        self._db.execute(
            "INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value))
        )

    @property
    def rules(self) -> swing.Rules:
        """Return the account's rules."""
        return swing.Rules(**self.get("rules"))

    def account(self) -> swing.Account:
        """Return the account."""
        return swing.Account.from_dict(self.get("account"))

    def save(self, account: swing.Account) -> None:
        """Store the account."""
        self.put("account", account.to_dict())

    def add_trade(self, trade: dict) -> None:
        """Store a closed trade."""
        self._db.execute("INSERT INTO trades (data) VALUES (?)", (json.dumps(trade),))

    def trades(self, limit: int = 50) -> list[dict]:
        """Return the latest closed trades, newest first."""
        rows = self._db.execute(
            "SELECT data FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [json.loads(r[0]) for r in rows]

    def record(self, moment: datetime, value: float) -> None:
        """Store the account's value at ``moment``."""
        self._db.execute(
            "INSERT OR REPLACE INTO equity VALUES (?, ?)", (_iso(moment), value)
        )

    def curve(self, points: int = 600) -> list[dict]:
        """Return the value curve, thinned to at most ``points`` points."""
        rows = self._db.execute(
            "SELECT time, value FROM equity ORDER BY time"
        ).fetchall()
        if len(rows) > points:
            keep = np.unique(np.linspace(0, len(rows) - 1, points).astype(int))
            rows = [rows[k] for k in keep]
        return [{"t": t, "v": round(v, 2)} for t, v in rows]


def store_path(name: str) -> Path:
    """Return an account's file."""
    return folder() / f"{name}.sqlite"


def existing() -> list[str]:
    """Return the accounts that exist."""
    return [name for name in ACCOUNTS if store_path(name).exists()]


# --- Market data --------------------------------------------------------------------


class Market:
    """Live Binance data; tests replace it with a fake."""

    def bars(self, symbol, interval, start=None, limit=1000, now=None) -> pd.DataFrame:
        """Return a perpetual's finished bars."""
        return binance.futures_bars(symbol, interval, start, limit, now)

    def prices(self) -> dict[str, float]:
        """Return every perpetual's latest price."""
        return binance.futures_prices()

    def funding(self, symbol: str) -> float:
        """Return a perpetual's latest funding rate (per eight hours)."""
        return binance.futures_funding_rate(symbol)

    def universe(self, month: date) -> list[str]:
        """Return the month's top-20 spot pairs by volume (point in time)."""
        fetch = live.memo(live.fetch_candles)
        return live.pick_universe(live.listed_pairs(), month, fetch, 20)


def perpetual(pair: str, perps: set[str]) -> str | None:
    """Return the USDT perpetual of a spot pair ("PEPEUSDT" -> "1000PEPEUSDT")."""
    base = pair.removesuffix("USDT")
    for prefix in ("", "1000", "1000000"):
        if f"{prefix}{base}USDT" in perps:
            return f"{prefix}{base}USDT"
    return None


# --- Live step ----------------------------------------------------------------------


def step(now: datetime | None = None, market: Market | None = None) -> dict[str, Any]:
    """Run one minute of the experiment for every account."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    market = market or Market()
    minute = _floor(now, timedelta(minutes=1))
    bar_end = _floor(now, BAR)
    report: dict[str, Any] = {"time": _iso(now)}
    stores = {name: Store(store_path(name)) for name in existing()}
    try:
        accounts = {name: s.account() for name, s in stores.items()}
        for name, store in stores.items():
            report[name] = {"closed": _watch(store, accounts[name], minute, market)}
        due = [n for n, s in stores.items() if (s.get("decided") or "") < _iso(bar_end)]
        prices = market.prices()
        if due:
            members = _members(stores, bar_end, prices, market)
            held = {s for n in due for s in accounts[n].positions}
            frames = _frames(sorted(set(members) | held), bar_end, market)
            for name in due:
                store, account = stores[name], accounts[name]
                report[name].update(
                    _decide(store, account, frames, members, bar_end, prices)
                )
                store.put("decided", _iso(bar_end))
        for name, store in stores.items():
            account, rules = accounts[name], store.rules
            value = swing.value(account, prices, rules)
            store.save(account)
            store.put(
                "prices", {s: prices[s] for s in account.positions if s in prices}
            )
            store.record(now, value)
            report[name]["value"] = round(value, 2)
    finally:
        for store in stores.values():
            store.close()
    return report


def _watch(
    store: Store, account: swing.Account, minute: datetime, market: Market
) -> list:
    """Check every open position against the 1-minute bars since the last check."""
    rules = store.rules
    checked = store.get("checked", {})
    closed = []
    for symbol, p in list(account.positions.items()):
        since = _parse(checked.get(symbol) or p.opened)
        if since >= minute:
            continue
        bars = market.bars(symbol, "1m", start=since, limit=1000, now=minute)
        opened = pd.Timestamp(_parse(p.opened).replace(tzinfo=None))
        last = since
        for start, row in bars.iterrows():
            if start < pd.Timestamp(since.replace(tzinfo=None)):
                continue
            if start.minute == 0 and start.hour in FUNDING_HOURS and start > opened:
                with contextlib.suppress(
                    Exception
                ):  # no rate available: skip this funding
                    swing.fund(
                        account,
                        rules,
                        symbol,
                        market.funding(symbol),
                        float(row["Open"]),
                    )
            last = (
                (start + pd.Timedelta(minutes=1))
                .to_pydatetime()
                .replace(tzinfo=timezone.utc)
            )
            trade = swing.check(account, rules, symbol, float(row["Open"]), float(row["High"]),
                                float(row["Low"]), _iso(last))  # fmt: skip
            if trade:
                store.add_trade(trade)
                closed.append(trade)
                break
        checked[symbol] = _iso(last)
    store.put("checked", {s: checked[s] for s in account.positions if s in checked})
    return closed


def _members(
    stores: dict[str, Store], bar_end: datetime, prices: dict, market: Market
) -> list[str]:
    """Return this month's universe as perpetuals, rebuilt once a month."""
    first = next(iter(stores.values()))
    month = date(bar_end.year, bar_end.month, 1)
    saved = first.get("universe")
    if saved and saved["month"] == month.isoformat():
        return saved["perps"]
    pairs = market.universe(month)
    perps = [p for p in (perpetual(pair, set(prices)) for pair in pairs) if p]
    for store in stores.values():
        store.put(
            "universe", {"month": month.isoformat(), "pairs": pairs, "perps": perps}
        )
    return perps


def _frames(symbols: list[str], bar_end: datetime, market: Market) -> dict[str, tuple]:
    """Return each perpetual's finished 4-hour bars and daily closes."""
    out = {}
    for symbol in symbols:
        try:
            four = market.bars(symbol, "4h", limit=200, now=bar_end)
            day = market.bars(symbol, "1d", limit=120, now=bar_end)
        except Exception:  # noqa: BLE001, S112 - a coin without data sits this bar out
            continue
        if len(four) and len(day):
            out[symbol] = (four, day["Close"])
    return out


def _decide(store: Store, account: swing.Account, frames: dict, members: list[str],
            bar_end: datetime, prices: dict) -> dict:  # fmt: skip
    """Tighten stops and open breakouts on a finished 4-hour bar."""
    rules = store.rules
    start = pd.Timestamp((bar_end - BAR).replace(tzinfo=None))
    board, candidates = {}, []
    for symbol, (four, daily) in frames.items():
        sig = swing.signals(four, daily, rules)
        if start not in sig.index:
            continue
        row = sig.loc[start]
        close = float(four.at[start, "Close"])
        if symbol in account.positions:
            swing.trail(account, rules, symbol, float(row["trail"]))
            account.positions[symbol].bars += 1
        level = four["Close"].iloc[-rules.entry_bars - 1 : -1]
        edge = level.max() if rules.sign > 0 else level.min()
        board[symbol] = {
            "close": close, "enter": bool(row["enter"]),
            "to_breakout_pct": round(rules.sign * (edge / close - 1) * 100, 2),
            "trend": bool(_trend_ok(daily, rules)), "atr_pct": round(float(row["atr"]) / close * 100, 2),
        }  # fmt: skip
        if symbol in members and row["enter"] and symbol not in account.positions:
            candidates.append({"symbol": symbol, "close": close, "atr": float(row["atr"]),
                               "strength": float(row["strength"])})  # fmt: skip
    opened = swing.open_positions(account, rules, candidates,
                                  {**prices, **{c["symbol"]: c["close"] for c in candidates}},
                                  _iso(bar_end))  # fmt: skip
    checked = store.get("checked", {})
    store.put("checked", {**checked, **{s: _iso(bar_end) for s in opened}})
    store.put("board", board)
    return {"opened": opened}


def _trend_ok(daily: pd.Series, rules: swing.Rules) -> bool:
    values = daily.to_numpy(dtype=float)
    if len(values) < rules.trend_days:
        return False
    return rules.sign * (values[-1] - values[-rules.trend_days :].mean()) > 0


# --- Status -------------------------------------------------------------------------


def status(name: str, prices: dict[str, float] | None = None) -> dict[str, Any]:
    """Return an account for the web page, valued at ``prices`` when given."""
    with Store(store_path(name)) as store:
        rules, account = store.rules, store.account()
        prices = {**store.get("prices", {}), **(prices or {})}
        positions = []
        for symbol, p in account.positions.items():
            price = prices.get(symbol, p.entry)
            gain = max(rules.sign * p.units * (price - p.entry), -p.margin)
            positions.append({
                **asdict(p), "price": price, "pnl": round(gain, 2),
                "notional": round(p.units * price, 2),
                "r_now": round((gain - p.fees - p.funding) / p.risk_usd, 2) if p.risk_usd else 0,
                "move_pct": round(rules.sign * (price / p.entry - 1) * 100, 2),
                "to_stop_pct": round(abs(price / p.stop - 1) * 100, 2),
                "to_liquidation_pct": round(abs(price / p.liquidation - 1) * 100, 1),
            })  # fmt: skip
        value = swing.value(account, prices, rules)
        exposure = swing.gross(account, prices)
        return {
            "account": name, "side": rules.side, "rules": asdict(rules),
            "initial": account.initial, "wallet": round(account.wallet, 2),
            "value": round(value, 2), "return_pct": round((value / account.initial - 1) * 100, 2),
            "exposure": round(exposure / value, 2) if value > 0 else 0.0,
            "stats": account.stats, "positions": positions, "trades": store.trades(30),
            "board": store.get("board", {}), "universe": store.get("universe", {}),
            "decided": store.get("decided"), "created": store.get("created"),
            "equity": store.curve(),
        }  # fmt: skip


# --- CLI ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Create, step or show the experiment."""
    parser = argparse.ArgumentParser(prog="marketalyzer-leverage",
                                     description="Kaldıraçlı long/short trend deneyi (sanal).")  # fmt: skip
    parser.add_argument("command", choices=("init", "step", "status"))
    parser.add_argument("--cash", type=float, default=CASH)
    args = parser.parse_args(argv)
    if args.command == "init":
        for side in ACCOUNTS:
            if not store_path(side).exists():
                Store.create(
                    store_path(side), swing.Rules(side=side), args.cash
                ).close()
        print(json.dumps({"accounts": existing(), "folder": str(folder())}))
        return 0
    if not existing():
        print("Deney hesabı yok; önce 'marketalyzer-leverage init'.", file=sys.stderr)
        return 1
    out = step() if args.command == "step" else {n: status(n) for n in existing()}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
