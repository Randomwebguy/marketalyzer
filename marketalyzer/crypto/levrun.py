"""The leveraged long and short experiment accounts on live Binance futures prices.

Paper only: nothing is sent to an exchange. Meant to run every minute:

- the open position is checked against every finished 1-minute bar since the
  last check (liquidation, stop, target, funding at 00/08/16 UTC);
- on each finished 15-minute bar the watchlist (the most active USDT
  perpetuals) is scored for longs and shorts; the account closes a position
  whose score faded or that is too old, and opens the best-scoring coin when
  its score is high enough;
- the value is recorded at the latest price.

``replay`` runs the same rules over past 15-minute bars.

    marketalyzer-leverage init [--cash 10000]
    marketalyzer-leverage step
    marketalyzer-leverage status
    marketalyzer-leverage replay [--days 60]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer.crypto import binance, confidence, martingale, universe
from marketalyzer.paper.cli import default_home

ACCOUNTS = ("long", "short")
WATCH = 10
CASH = 10_000.0
FUNDING_HOURS = (0, 8, 16)
BAR = timedelta(minutes=15)
REPLAY_FUNDING = 0.0001  # per eight hours, the exchanges' default rate


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
    """One experiment account in SQLite: config, book, trades and value curve."""

    def __init__(self, path: Path):
        if not path.exists():
            raise FileNotFoundError(f"Deney hesabı yok: {path}")
        self.path = path
        self._db = sqlite3.connect(path, timeout=30, isolation_level=None)

    @classmethod
    def create(cls, path: Path, config: martingale.Config, cash: float = CASH,
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
        store.put("config", asdict(config))
        store.put("book", martingale.Book(cash, cash).to_dict())
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
    def config(self) -> martingale.Config:
        """Return the account's settings."""
        data = self.get("config")
        return martingale.Config(**{**data, "ladder": tuple(data["ladder"])})

    def book(self) -> martingale.Book:
        """Return the account's book."""
        return martingale.Book.from_dict(self.get("book"))

    def save(self, book: martingale.Book) -> None:
        """Store the book."""
        self.put("book", book.to_dict())

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
    """Live Binance futures data; tests replace it with a fake."""

    def bars(self, symbol, interval, start=None, limit=1000, now=None) -> pd.DataFrame:
        """Return finished bars."""
        return binance.futures_bars(symbol, interval, start, limit, now)

    def active(self) -> list[str]:
        """Return the most active perpetuals, without stablecoins."""
        return binance.futures_active(WATCH, universe.STABLE)

    def prices(self) -> dict[str, float]:
        """Return the latest prices."""
        return binance.futures_prices()

    def funding(self, symbol: str) -> float:
        """Return the latest funding rate."""
        return binance.futures_funding_rate(symbol)


def board(frames: dict[str, pd.DataFrame], start: pd.Timestamp) -> dict[str, dict]:
    """Return each coin's scores, close and ATR for the 15-minute bar at ``start``."""
    out = {}
    for symbol, frame in frames.items():
        if start not in frame.index:
            continue
        table = confidence.scores(frame)
        row = table.loc[start]
        if np.isnan(row["long"]) or np.isnan(row["atr"]):
            continue
        out[symbol] = {
            "long": round(float(row["long"]), 1), "short": round(float(row["short"]), 1),
            "close": float(frame.loc[start, "Close"]), "atr": float(row["atr"]),
            "parts": {side: {k: round(float(row[f"{side}_{k}"]), 2) for k in confidence.WEIGHTS}
                      for side in ("long", "short")},
        }  # fmt: skip
    return out


# --- Decisions ----------------------------------------------------------------------


def decide(book: martingale.Book, config: martingale.Config, scored: dict[str, dict],
           time: str) -> list[dict]:  # fmt: skip
    """Act on a finished 15-minute bar: close a fading position, open the best coin."""
    done = []
    p = book.position
    if p is not None and p.symbol in scored:
        p.bars += 1
        score = scored[p.symbol][config.side]
        if score < config.leave or p.bars >= config.max_bars:
            reason = (
                "süre doldu"
                if p.bars >= config.max_bars
                else f"güven düştü ({score:g})"
            )
            done.append(
                martingale.close(book, config, scored[p.symbol]["close"], reason, time)
            )
    if martingale.can_open(book, config) and scored:
        symbol = max(scored, key=lambda s: scored[s][config.side])
        row = scored[symbol]
        if row[config.side] >= config.enter:
            martingale.open_position(book, config, symbol, row["close"], row["atr"],
                                     row[config.side], time)  # fmt: skip
    return done


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
        books = {name: s.book() for name, s in stores.items()}
        for name, store in stores.items():
            report[name] = {"closed": _watch(store, books[name], minute, market)}
        due = [n for n, s in stores.items() if (s.get("decided") or "") < _iso(bar_end)]
        if due:
            watch = _watchlist(stores, now, market)
            start = pd.Timestamp((bar_end - BAR).replace(tzinfo=None))
            frames = {}
            for symbol in watch:
                try:
                    frames[symbol] = market.bars(symbol, "15m", limit=300, now=bar_end)
                except Exception:  # noqa: BLE001, S112 - a coin without data sits this bar out
                    continue
            scored = board(frames, start)
            if scored:
                for name in due:
                    store, book = stores[name], books[name]
                    closed = decide(book, store.config, scored, _iso(bar_end))
                    for trade in closed:
                        store.add_trade(trade)
                    if book.position is not None and book.position.opened == _iso(
                        bar_end
                    ):
                        store.put("checked", _iso(bar_end))
                    report[name]["closed"] += closed
                    report[name]["opened"] = (
                        book.position.symbol if book.position else None
                    )
                    store.put("decided", _iso(bar_end))
                    store.put("scores", scored)
                    store.put("watch", watch)
        held = [b.position.symbol for b in books.values() if b.position]
        prices = market.prices() if held else {}
        for name, store in stores.items():
            book, config = books[name], store.config
            price = prices.get(book.position.symbol) if book.position else None
            value = martingale.equity(book, price, config.sign)
            store.save(book)
            store.put("prices", {s: prices[s] for s in held if s in prices})
            store.record(now, value)
            report[name]["value"] = round(value, 2)
    finally:
        for store in stores.values():
            store.close()
    return report


def _watch(
    store: Store, book: martingale.Book, minute: datetime, market: Market
) -> list:
    """Check the open position against the finished 1-minute bars since the last check."""
    p = book.position
    if p is None:
        return []
    config = store.config
    since = _parse(store.get("checked") or p.opened)
    if since >= minute:
        return []
    bars = market.bars(p.symbol, "1m", start=since, limit=1000, now=minute)
    opened = pd.Timestamp(_parse(p.opened).replace(tzinfo=None))
    last = since
    for start, row in bars.iterrows():
        if start < pd.Timestamp(since.replace(tzinfo=None)):
            continue
        if start.minute == 0 and start.hour in FUNDING_HOURS and start > opened:
            with contextlib.suppress(Exception):  # no rate available: skip this funding
                martingale.fund(
                    book, config, market.funding(p.symbol), float(row["Open"])
                )
        end = (
            (start + pd.Timedelta(minutes=1))
            .to_pydatetime()
            .replace(tzinfo=timezone.utc)
        )
        last = end
        trade = martingale.check(book, config, float(row["Open"]), float(row["High"]),
                                 float(row["Low"]), _iso(end))  # fmt: skip
        if trade:
            store.add_trade(trade)
            store.put("checked", _iso(end))
            return [trade]
    store.put("checked", _iso(last))
    return []


def _watchlist(stores: dict[str, Store], now: datetime, market: Market) -> list[str]:
    """Return the watchlist, refreshed every hour."""
    saved = next(iter(stores.values())).get("watchlist")
    if saved and _parse(saved["time"]) > now - timedelta(hours=1):
        return saved["symbols"]
    symbols = market.active()
    for store in stores.values():
        store.put("watchlist", {"time": _iso(now), "symbols": symbols})
    return symbols


# --- Status -------------------------------------------------------------------------


def status(name: str, prices: dict[str, float] | None = None) -> dict[str, Any]:
    """Return an account for the web page, valued at ``prices`` when given."""
    with Store(store_path(name)) as store:
        config, book = store.config, store.book()
        prices = {**store.get("prices", {}), **(prices or {})}
        p = book.position
        position = None
        if p is not None:
            price = prices.get(p.symbol, p.entry)
            gain = martingale.unrealized(p, price, config.sign)
            position = {
                **asdict(p), "price": price, "pnl": round(gain, 2),
                "roe_pct": round(gain / (p.margin + p.fees) * 100, 2),
                "move_pct": round(config.sign * (price / p.entry - 1) * 100, 3),
                "to_stop_pct": round(abs(price / p.stop - 1) * 100, 3),
                "to_take_pct": round(abs(p.take / price - 1) * 100, 3),
                "to_liquidation_pct": round(abs(price / p.liquidation - 1) * 100, 2),
            }  # fmt: skip
        value = martingale.equity(
            book, position["price"] if position else None, config.sign
        )
        return {
            "account": name, "side": config.side, "config": asdict(config),
            "initial": book.initial, "wallet": round(book.wallet, 2), "value": round(value, 2),
            "return_pct": round((value / book.initial - 1) * 100, 2),
            "step": book.step, "leverage": config.ladder[book.step],
            "cycle_pnl": round(book.cycle_pnl, 2), "cycle_margin": book.cycle_margin,
            "stats": book.stats, "position": position, "trades": store.trades(30),
            "scores": store.get("scores", {}), "watch": store.get("watch", []),
            "decided": store.get("decided"), "checked": store.get("checked"),
            "created": store.get("created"), "equity": store.curve(),
            "stopped": not martingale.can_open(book, config) and book.position is None,
        }  # fmt: skip


# --- Replay -------------------------------------------------------------------------


def history(symbol: str, days: int, now: datetime, market: Market) -> pd.DataFrame:
    """Return ``days`` (plus a warm-up) of finished 15-minute bars."""
    start = _floor(now, BAR) - timedelta(days=days + 4)
    parts = []
    while start < now:
        part = market.bars(symbol, "15m", start=start, limit=1000, now=now)
        if part.empty:
            break
        parts.append(part)
        start = (
            (part.index[-1] + pd.Timedelta(minutes=15))
            .to_pydatetime()
            .replace(tzinfo=timezone.utc)
        )
    frame = pd.concat(parts) if parts else pd.DataFrame()
    return frame[~frame.index.duplicated()]


def replay(days: int = 60, cash: float = CASH, now: datetime | None = None,
           market: Market | None = None, watch: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Run both accounts over the past ``days`` of 15-minute bars.

    Positions are checked against each bar's high and low (stop first) and
    funding is charged at the default 0.01 % every eight hours.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    market = market or Market()
    watch = watch or market.active()
    frames = {s: history(s, days, now, market) for s in watch}
    tables = {s: confidence.scores(f) for s, f in frames.items() if len(f)}
    first = pd.Timestamp((now - timedelta(days=days)).replace(tzinfo=None))
    times = sorted(set().union(*(f.index for f in frames.values())))
    times = [t for t in times if t >= first]
    out = {}
    for side in ACCOUNTS:
        config = martingale.Config(side=side)
        book = martingale.Book(cash, cash)
        trades, curve = [], []
        for t in times:
            stamp = _iso(
                (t + pd.Timedelta(minutes=15))
                .to_pydatetime()
                .replace(tzinfo=timezone.utc)
            )
            p = book.position
            if p is not None and t in frames[p.symbol].index and p.opened < stamp:
                bar = frames[p.symbol].loc[t]
                if t.minute == 0 and t.hour in FUNDING_HOURS:
                    martingale.fund(book, config, REPLAY_FUNDING, float(bar["Open"]))
                trade = martingale.check(book, config, float(bar["Open"]), float(bar["High"]),
                                         float(bar["Low"]), stamp)  # fmt: skip
                if trade:
                    trades.append(trade)
            scored = {}
            for s, table in tables.items():
                if t in table.index and not np.isnan(table.at[t, "long"]):
                    scored[s] = {"long": float(table.at[t, "long"]), "short": float(table.at[t, "short"]),
                                 "close": float(frames[s].at[t, "Close"]), "atr": float(table.at[t, "atr"])}  # fmt: skip
            trades += decide(book, config, scored, stamp)
            price = (
                scored.get(book.position.symbol, {}).get("close")
                if book.position
                else None
            )
            curve.append(martingale.equity(book, price, config.sign))
        values = np.array(curve) if curve else np.array([cash])
        peak = np.maximum.accumulate(values)
        out[side] = {
            "start": cash, "end": round(float(values[-1]), 2),
            "return_pct": round((values[-1] / cash - 1) * 100, 2),
            "max_drawdown_pct": round(float((values / peak - 1).min()) * 100, 2),
            "lowest": round(float(values.min()), 2), "stats": book.stats,
            "trades": trades,
        }  # fmt: skip
    return {"days": days, "watch": watch, "bars": len(times), **out}


# --- CLI ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Create, step, show or replay the experiment."""
    parser = argparse.ArgumentParser(prog="marketalyzer-leverage",
                                     description="Kaldıraçlı long/short deneyi (sanal).")  # fmt: skip
    parser.add_argument("command", choices=("init", "step", "status", "replay"))
    parser.add_argument("--cash", type=float, default=CASH)
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args(argv)
    if args.command == "init":
        for side in ACCOUNTS:
            if not store_path(side).exists():
                Store.create(
                    store_path(side), martingale.Config(side=side), args.cash
                ).close()
        print(json.dumps({"accounts": existing(), "folder": str(folder())}))
        return 0
    if args.command == "replay":
        result = replay(args.days, args.cash)
        for side in ACCOUNTS:
            result[side]["trades"] = len(result[side]["trades"])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if not existing():
        print("Deney hesabı yok; önce 'marketalyzer-leverage init'.", file=sys.stderr)
        return 1
    out = step() if args.command == "step" else {n: status(n) for n in existing()}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
