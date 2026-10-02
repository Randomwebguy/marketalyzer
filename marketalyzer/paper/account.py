"""A virtual BIST brokerage account stored in SQLite."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from marketalyzer.backtest.costs import BistCosts, round_to_tick, tick_size
from marketalyzer.paper.models import (
    IST,
    Bar,
    Fill,
    Order,
    Position,
    as_istanbul,
)
from openbb_bist.utils.symbols import to_bist_symbol, to_yahoo_symbol

# End of the BIST equity closing session. A day order entered before this time
# belongs to that day's session; one entered later waits for the next session.
SESSION_CLOSE = time(18, 10)

SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    type TEXT NOT NULL,
    tif TEXT NOT NULL,
    status TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    limit_price REAL,
    stop_price REAL,
    session_date TEXT,
    filled_at TEXT,
    fill_price REAL,
    commission REAL,
    reason TEXT,
    tag TEXT
);
CREATE INDEX orders_open ON orders (symbol, status);
CREATE TABLE fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders (id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price REAL NOT NULL,
    commission REAL NOT NULL,
    time TEXT NOT NULL
);
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    qty INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    realized_pnl REAL NOT NULL
);
CREATE TABLE marks (
    symbol TEXT PRIMARY KEY,
    price REAL NOT NULL,
    time TEXT NOT NULL
);
CREATE TABLE equity (time TEXT PRIMARY KEY, cash REAL NOT NULL, equity REAL NOT NULL);
"""


class OrderRejected(ValueError):
    """The broker refused an order."""


def normalize_symbol(symbol: str) -> str:
    """Return the plain BIST code: thyao, THYAO.IS and THYAO.E become THYAO."""
    return to_bist_symbol(to_yahoo_symbol(symbol))


def _time(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _order(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"],
        symbol=row["symbol"],
        side=row["side"],
        qty=row["qty"],
        type=row["type"],
        tif=row["tif"],
        status=row["status"],
        submitted_at=datetime.fromisoformat(row["submitted_at"]),
        limit_price=row["limit_price"],
        stop_price=row["stop_price"],
        session_date=date.fromisoformat(row["session_date"])
        if row["session_date"]
        else None,
        filled_at=_time(row["filled_at"]),
        fill_price=row["fill_price"],
        commission=row["commission"],
        reason=row["reason"],
        tag=row["tag"],
    )


def _check_price(name: str, price: float | None) -> None:
    if price is None or price <= 0:
        raise OrderRejected(f"{name} must be a positive price.")
    if abs(round_to_tick(price) - price) > 1e-9:
        raise OrderRejected(
            f"{name} {price} is not on the BIST tick grid (tick {tick_size(price)});"
            f" use {round_to_tick(price, 'down')} or {round_to_tick(price, 'up')}."
        )


def _check_order(
    side: str,
    qty: Any,
    type: str,
    limit_price: float | None,
    stop_price: float | None,
    tif: str,
) -> int:
    """Validate an order's fields and return the share count."""
    if side not in ("buy", "sell"):
        raise OrderRejected("side must be 'buy' or 'sell'.")
    if type not in ("market", "limit", "stop"):
        raise OrderRejected("type must be 'market', 'limit' or 'stop'.")
    if tif not in ("day", "gtc"):
        raise OrderRejected("tif must be 'day' or 'gtc'.")
    if isinstance(qty, bool) or int(qty) != qty or qty <= 0:
        raise OrderRejected("qty must be a positive whole number of shares.")
    prices = {"limit": limit_price, "stop": stop_price}
    if type == "market":
        if limit_price is not None or stop_price is not None:
            raise OrderRejected("Market orders take no limit or stop price.")
    else:
        _check_price(f"{type}_price", prices.pop(type))
        if prices.popitem()[1] is not None:
            raise OrderRejected(f"A {type} order takes only a {type}_price.")
    return int(qty)


class PaperAccount:
    """A paper trading account: cash, positions, orders and fills in one file.

    Each call runs in its own SQLite transaction, so a trading loop and another
    process placing orders (the CLI or an AI agent) can share an account. Only
    long positions are supported, as short selling on BIST is restricted.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"No paper account at {self.path}.")
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        settings = {
            row["key"]: row["value"]
            for row in self._db.execute("SELECT key, value FROM settings")
        }
        self.costs = BistCosts(**json.loads(settings["costs"]))
        self.slippage = float(settings["slippage"])
        self.initial_cash = float(settings["initial_cash"])
        self.created_at = datetime.fromisoformat(settings["created_at"])

    @classmethod
    def create(
        cls,
        path: str | Path,
        cash: float = 100_000.0,
        costs: BistCosts | None = None,
        slippage: float = 0.001,
        now: datetime | None = None,
    ) -> "PaperAccount":
        """Create a new account file and open it.

        ``slippage`` is the round-trip spread and slippage; half of it is charged
        on each market or stop fill.
        """
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"A paper account already exists at {path}.")
        if cash <= 0:
            raise ValueError("Starting cash must be positive.")
        if not 0 <= slippage < 0.1:
            raise ValueError("Slippage must be between 0 and 0.1.")
        path.parent.mkdir(parents=True, exist_ok=True)
        created = as_istanbul(now or datetime.now(IST))
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [
                    ("initial_cash", str(float(cash))),
                    ("cash", str(float(cash))),
                    ("costs", json.dumps(asdict(costs or BistCosts()))),
                    ("slippage", str(float(slippage))),
                    ("created_at", created.isoformat()),
                ],
            )
            db.commit()
        finally:
            db.close()
        return cls(path)

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> "PaperAccount":
        """Use the account as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the account."""
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    # Queries

    @property
    def cash(self) -> float:
        """Cash balance in TRY."""
        row = self._db.execute("SELECT value FROM settings WHERE key = 'cash'")
        return float(row.fetchone()[0])

    def order(self, order_id: int) -> Order:
        """Return one order."""
        row = self._db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        found = row.fetchone()
        if found is None:
            raise KeyError(f"No order with id {order_id}.")
        return _order(found)

    def orders(
        self, status: str | None = None, symbol: str | None = None
    ) -> list[Order]:
        """Return orders, oldest first, optionally filtered."""
        query, args = "SELECT * FROM orders WHERE 1 = 1", []
        if status:
            query += " AND status = ?"
            args.append(status)
        if symbol:
            query += " AND symbol = ?"
            args.append(normalize_symbol(symbol))
        return [_order(row) for row in self._db.execute(query + " ORDER BY id", args)]

    def fills(self, symbol: str | None = None) -> list[Fill]:
        """Return fills, oldest first."""
        query, args = "SELECT * FROM fills", []
        if symbol:
            query += " WHERE symbol = ?"
            args.append(normalize_symbol(symbol))
        return [
            Fill(
                id=row["id"],
                order_id=row["order_id"],
                symbol=row["symbol"],
                side=row["side"],
                qty=row["qty"],
                price=row["price"],
                commission=row["commission"],
                time=datetime.fromisoformat(row["time"]),
            )
            for row in self._db.execute(query + " ORDER BY id", args)
        ]

    def positions(self, include_closed: bool = False) -> list[Position]:
        """Return positions with their last known price."""
        query = (
            "SELECT p.*, m.price AS last_price, m.time AS last_time FROM positions p"
            " LEFT JOIN marks m ON m.symbol = p.symbol"
        )
        if not include_closed:
            query += " WHERE p.qty > 0"
        return [
            Position(
                symbol=row["symbol"],
                qty=row["qty"],
                avg_cost=row["avg_cost"],
                realized_pnl=row["realized_pnl"],
                last_price=row["last_price"],
                last_time=_time(row["last_time"]),
            )
            for row in self._db.execute(query + " ORDER BY p.symbol")
        ]

    def held(self, symbol: str) -> int:
        """Return the number of shares held."""
        row = self._db.execute(
            "SELECT qty FROM positions WHERE symbol = ?", (normalize_symbol(symbol),)
        ).fetchone()
        return row["qty"] if row else 0

    def mark(self, symbol: str) -> float | None:
        """Return the last processed close price for a symbol."""
        row = self._db.execute(
            "SELECT price FROM marks WHERE symbol = ?", (normalize_symbol(symbol),)
        ).fetchone()
        return row["price"] if row else None

    def processed_until(self, symbol: str) -> datetime | None:
        """Return the end of the last bar processed for a symbol."""
        row = self._db.execute(
            "SELECT time FROM marks WHERE symbol = ?", (normalize_symbol(symbol),)
        ).fetchone()
        return _time(row["time"]) if row else None

    def equity(self) -> float:
        """Cash plus open positions at their last price (or cost, if unpriced)."""
        value = sum(
            p.market_value if p.market_value is not None else p.qty * p.avg_cost
            for p in self.positions()
        )
        return self.cash + value

    def buying_power(self) -> float:
        """Cash minus what open buy orders are expected to cost."""
        reserved = 0.0
        for order in self._open_orders():
            if order.side != "buy":
                continue
            reference = order.limit_price or order.stop_price or self.mark(order.symbol)
            if reference is not None:
                reserved += self._buy_cost(order.qty, reference, order.type)
        return self.cash - reserved

    def equity_curve(self) -> list[dict[str, Any]]:
        """Return the recorded equity snapshots, oldest first."""
        return [
            {"time": row["time"], "cash": row["cash"], "equity": row["equity"]}
            for row in self._db.execute("SELECT * FROM equity ORDER BY time")
        ]

    def summary(self) -> dict[str, Any]:
        """Account state as a JSON-friendly dict; amounts are in TRY."""
        positions = self.positions(include_closed=True)
        open_positions = [p for p in positions if p.qty > 0]
        equity = self.equity()
        return {
            "cash": round(self.cash, 2),
            "equity": round(equity, 2),
            "initial_cash": self.initial_cash,
            "return_pct": round((equity / self.initial_cash - 1) * 100, 4),
            "realized_pnl": round(sum(p.realized_pnl for p in positions), 2),
            "unrealized_pnl": round(
                sum(p.unrealized_pnl or 0.0 for p in open_positions), 2
            ),
            "positions": [p.to_dict() for p in open_positions],
            "open_orders": [o.to_dict() for o in self.orders(status="open")],
        }

    # Orders

    def submit(
        self,
        symbol: str,
        side: str,
        qty: int,
        type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        tif: str = "day",
        tag: str | None = None,
        now: datetime | None = None,
    ) -> Order:
        """Place an order. It can fill from the first bar starting at ``now`` or later.

        Market orders fill at the bar's open; limit orders when the bar trades at
        or through the limit; stop orders become market orders once the bar
        trades at or through the stop. Day orders expire after their session.
        """
        symbol = normalize_symbol(symbol)
        now = as_istanbul(now or datetime.now(IST))
        qty = _check_order(side, qty, type, limit_price, stop_price, tif)
        session_date = None
        if tif == "day" and now.weekday() < 5 and now.time() < SESSION_CLOSE:
            session_date = now.date().isoformat()

        with self._transaction():
            if side == "sell":
                available = self.held(symbol) - self._open_qty(symbol, "sell")
                if qty > available:
                    raise OrderRejected(
                        f"{symbol}: only {available} shares are available to sell;"
                        " short selling is not supported."
                    )
            else:
                reference = limit_price or stop_price or self.mark(symbol)
                if reference is not None:
                    needed = self._buy_cost(qty, reference, type)
                    power = self.buying_power()
                    if needed > power:
                        raise OrderRejected(
                            f"Insufficient buying power: the order needs about"
                            f" {needed:,.2f} TRY and {power:,.2f} TRY is available."
                        )
            cursor = self._db.execute(
                "INSERT INTO orders (symbol, side, qty, type, tif, status,"
                " submitted_at, limit_price, stop_price, session_date, tag)"
                " VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)",
                (
                    symbol,
                    side,
                    qty,
                    type,
                    tif,
                    now.isoformat(),
                    limit_price,
                    stop_price,
                    session_date,
                    tag,
                ),
            )
        return self.order(cursor.lastrowid)  # type: ignore[arg-type]

    def cancel(self, order_id: int) -> Order:
        """Cancel an open order."""
        with self._transaction():
            order = self.order(order_id)
            if order.status != "open":
                raise OrderRejected(f"Order {order_id} is already {order.status}.")
            self._close_order(order_id, "cancelled", "Cancelled by the user.")
        return self.order(order_id)

    def expire_day_orders(self, now: datetime | None = None) -> list[Order]:
        """Expire day orders whose session was on an earlier date.

        A day order is not expired on its own date even after the close: with
        delayed data, that session's last bars may not have been processed yet.
        """
        now = as_istanbul(now or datetime.now(IST))
        expired = []
        with self._transaction():
            for order in self._open_orders():
                if order.tif != "day" or order.session_date is None:
                    continue
                if order.session_date < now.date():
                    self._close_order(order.id, "expired", "Day order expired.")
                    expired.append(order.id)
        return [self.order(order_id) for order_id in expired]

    # Market data

    def process_bar(self, symbol: str, bar: Bar) -> list[Fill]:
        """Match open orders against a completed bar and mark the symbol's price.

        Bars that end at or before the last processed bar are ignored, so feeding
        the same bar twice is harmless.
        """
        symbol = normalize_symbol(symbol)
        start, end = as_istanbul(bar.start), as_istanbul(bar.end)
        fills = []
        with self._transaction():
            done = self.processed_until(symbol)
            if done is not None and end <= done:
                return []
            for order in self._open_orders(symbol):
                if order.submitted_at > start:
                    continue
                if order.tif == "day":
                    if order.session_date is None:
                        order.session_date = start.date()
                        self._db.execute(
                            "UPDATE orders SET session_date = ? WHERE id = ?",
                            (order.session_date.isoformat(), order.id),
                        )
                    elif start.date() > order.session_date:
                        self._close_order(order.id, "expired", "Day order expired.")
                        continue
                price = self._match(order, bar)
                if price is not None:
                    fill = self._execute(order, price, start)
                    if fill:
                        fills.append(fill)
            self._db.execute(
                "INSERT INTO marks (symbol, price, time) VALUES (?, ?, ?)"
                " ON CONFLICT (symbol) DO UPDATE SET price = excluded.price,"
                " time = excluded.time",
                (symbol, bar.close, end.isoformat()),
            )
        return fills

    def record_equity(self, now: datetime | None = None) -> float:
        """Store an equity snapshot and return the equity."""
        now = as_istanbul(now or datetime.now(IST))
        equity = self.equity()
        with self._transaction():
            self._db.execute(
                "INSERT OR REPLACE INTO equity (time, cash, equity) VALUES (?, ?, ?)",
                (now.isoformat(), self.cash, equity),
            )
        return equity

    # Internals

    def _open_orders(self, symbol: str | None = None) -> list[Order]:
        query, args = "SELECT * FROM orders WHERE status = 'open'", []
        if symbol:
            query += " AND symbol = ?"
            args.append(symbol)
        return [_order(row) for row in self._db.execute(query + " ORDER BY id", args)]

    def _open_qty(self, symbol: str, side: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(SUM(qty), 0) FROM orders"
            " WHERE status = 'open' AND symbol = ? AND side = ?",
            (symbol, side),
        ).fetchone()
        return int(row[0])

    def _buy_cost(self, qty: int, price: float, type: str) -> float:
        if type != "limit":
            price *= 1 + self.slippage / 2
        return qty * price + self.costs(qty, price)

    def _close_order(self, order_id: int, status: str, reason: str) -> None:
        self._db.execute(
            "UPDATE orders SET status = ?, reason = ? WHERE id = ?",
            (status, reason, order_id),
        )

    def _set_cash(self, cash: float) -> None:
        self._db.execute(
            "UPDATE settings SET value = ? WHERE key = 'cash'", (str(cash),)
        )

    def _match(self, order: Order, bar: Bar) -> float | None:
        """Return the fill price of an order in a bar, or None if it does not fill."""
        buy = order.side == "buy"
        if order.type == "limit":
            limit = order.limit_price
            touched = bar.low <= limit if buy else bar.high >= limit
            if not touched:
                return None
            # A bar that opens through the limit fills at the better open price.
            return round_to_tick(min(bar.open, limit) if buy else max(bar.open, limit))
        base = bar.open
        if order.type == "stop":
            stop = order.stop_price
            if not (bar.high >= stop if buy else bar.low <= stop):
                return None
            base = max(bar.open, stop) if buy else min(bar.open, stop)
        return self._slipped(base, buy)

    def _slipped(self, price: float, buy: bool) -> float:
        """Move a market fill against the order by half the slippage, on a tick."""
        if not self.slippage:
            return round_to_tick(price)
        if buy:
            return round_to_tick(price * (1 + self.slippage / 2), "up")
        return round_to_tick(price * (1 - self.slippage / 2), "down")

    def _execute(self, order: Order, price: float, when: datetime) -> Fill | None:
        commission = self.costs(order.qty, price)
        value = order.qty * price
        cash = self.cash
        row = self._db.execute(
            "SELECT qty, avg_cost, realized_pnl FROM positions WHERE symbol = ?",
            (order.symbol,),
        ).fetchone()
        held, avg_cost, realized = (row[0], row[1], row[2]) if row else (0, 0.0, 0.0)

        if order.side == "buy":
            total = value + commission
            if total > cash + 1e-6:
                self._close_order(
                    order.id,
                    "rejected",
                    f"Insufficient cash at fill: needed {total:,.2f} TRY,"
                    f" had {cash:,.2f} TRY.",
                )
                return None
            avg_cost = (held * avg_cost + total) / (held + order.qty)
            held += order.qty
            cash -= total
        else:
            if order.qty > held:
                self._close_order(order.id, "rejected", "Not enough shares at fill.")
                return None
            proceeds = value - commission
            realized += proceeds - order.qty * avg_cost
            held -= order.qty
            cash += proceeds
            if held == 0:
                avg_cost = 0.0

        self._db.execute(
            "INSERT INTO positions (symbol, qty, avg_cost, realized_pnl)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (symbol) DO UPDATE SET"
            " qty = excluded.qty, avg_cost = excluded.avg_cost,"
            " realized_pnl = excluded.realized_pnl",
            (order.symbol, held, avg_cost, realized),
        )
        self._set_cash(cash)
        self._db.execute(
            "UPDATE orders SET status = 'filled', filled_at = ?, fill_price = ?,"
            " commission = ? WHERE id = ?",
            (when.isoformat(), price, commission, order.id),
        )
        cursor = self._db.execute(
            "INSERT INTO fills (order_id, symbol, side, qty, price, commission, time)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                order.id,
                order.symbol,
                order.side,
                order.qty,
                price,
                commission,
                when.isoformat(),
            ),
        )
        return Fill(
            id=cursor.lastrowid,  # type: ignore[arg-type]
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            price=price,
            commission=commission,
            time=when,
        )
