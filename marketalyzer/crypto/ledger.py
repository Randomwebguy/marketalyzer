"""A crypto paper account: USD cash, fractional units, fees and slippage.

Everything lives in one SQLite file, so the hourly runner and the web server
can share it. Buys and sells fill at the given price moved by half the
round-trip slippage, and pay the fee on the traded value.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE positions (
    symbol TEXT PRIMARY KEY,
    units REAL NOT NULL,
    avg_cost REAL NOT NULL,
    opened TEXT NOT NULL
);
CREATE TABLE fills (
    id INTEGER PRIMARY KEY,
    time TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    units REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    pnl REAL,
    reason TEXT
);
CREATE TABLE equity (time TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass
class Position:
    """Units held of one coin and their average cost per unit, fees included."""

    symbol: str
    units: float
    avg_cost: float
    opened: str


@dataclass
class Fill:
    """A buy or a sell that happened."""

    time: str
    symbol: str
    side: str
    units: float
    price: float
    fee: float
    pnl: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fill as a JSON-friendly dict."""
        return asdict(self)


def _now(now: datetime | None) -> str:
    return (
        (now or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat(timespec="seconds")
    )


class CryptoLedger:
    """Open an existing crypto paper account (``create`` makes a new one)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"Kripto sanal hesabı yok: {self.path}")
        self._db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        settings = dict(self._db.execute("SELECT key, value FROM settings").fetchall())
        self.initial_cash = float(settings["initial_cash"])
        self.fee = float(settings["fee"])
        self.slippage = float(settings["slippage"])
        self.created = settings["created"]

    @classmethod
    def create(
        cls,
        path: str | Path,
        cash: float = 100_000.0,
        fee: float = 0.001,
        slippage: float = 0.001,
        now: datetime | None = None,
    ) -> CryptoLedger:
        """Create an account file with ``cash`` USD; ``slippage`` is round trip."""
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"Kripto sanal hesabı zaten var: {path}")
        if cash <= 0 or not 0 <= fee < 0.05 or not 0 <= slippage < 0.05:
            raise ValueError("Geçersiz sermaye, komisyon ya da kayma.")
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.executemany(
                "INSERT INTO settings VALUES (?, ?)",
                [("initial_cash", str(float(cash))), ("cash", str(float(cash))),
                 ("fee", str(fee)), ("slippage", str(slippage)), ("created", _now(now))],
            )  # fmt: skip
            db.commit()
        finally:
            db.close()
        return cls(path)

    def close(self) -> None:
        """Close the database connection."""
        self._db.close()

    def __enter__(self) -> CryptoLedger:
        """Return the ledger for a with block."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the ledger at the end of a with block."""
        self.close()

    # State -----------------------------------------------------------------------

    @property
    def cash(self) -> float:
        """Return the USD cash."""
        row = self._db.execute(
            "SELECT value FROM settings WHERE key = 'cash'"
        ).fetchone()
        return float(row[0])

    def positions(self) -> dict[str, Position]:
        """Return the open positions by symbol."""
        rows = self._db.execute("SELECT * FROM positions ORDER BY symbol").fetchall()
        return {r["symbol"]: Position(**dict(r)) for r in rows}

    def get(self, key: str, default: Any = None) -> Any:
        """Return a stored state value (JSON), or ``default``."""
        row = self._db.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        """Store a state value as JSON."""
        self._db.execute(
            "INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value))
        )

    # Trading ---------------------------------------------------------------------

    def buy(
        self, symbol: str, usd: float, price: float, *, now: datetime | None = None,
        reason: str = "",
    ) -> Fill | None:  # fmt: skip
        """Spend up to ``usd`` (fee included, at most the cash) on ``symbol``.

        Returns None when nothing can be bought.
        """
        if price <= 0:
            raise ValueError("Fiyat pozitif olmalı.")
        filled = price * (1 + self.slippage / 2)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            cash = self.cash
            spend = min(usd, cash)
            units = spend / (filled * (1 + self.fee))
            if units <= 0 or spend < 1e-6:
                self._db.execute("ROLLBACK")
                return None
            fee = units * filled * self.fee
            held = self.positions().get(symbol)
            total_units = units + (held.units if held else 0.0)
            cost = units * filled + fee + (held.units * held.avg_cost if held else 0.0)
            opened = held.opened if held else _now(now)
            self._db.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?)",
                (symbol, total_units, cost / total_units, opened),
            )
            self._set_cash(cash - units * filled - fee)
            fill = Fill(_now(now), symbol, "buy", units, filled, fee, None, reason)
            self._insert(fill)
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return fill

    def sell(
        self,
        symbol: str,
        price: float,
        *,
        units: float | None = None,
        now: datetime | None = None,
        reason: str = "",
    ) -> Fill | None:
        """Sell ``units`` of ``symbol`` (all of it by default); None when none is held."""
        if price <= 0:
            raise ValueError("Fiyat pozitif olmalı.")
        filled = price * (1 - self.slippage / 2)
        self._db.execute("BEGIN IMMEDIATE")
        try:
            held = self.positions().get(symbol)
            if held is None or (units is not None and units <= 0):
                self._db.execute("ROLLBACK")
                return None
            sold = (
                held.units
                if units is None or units >= held.units * (1 - 1e-9)
                else units
            )
            fee = sold * filled * self.fee
            proceeds = sold * filled - fee
            pnl = proceeds - sold * held.avg_cost
            if sold == held.units:
                self._db.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
            else:
                self._db.execute(
                    "UPDATE positions SET units = ? WHERE symbol = ?",
                    (held.units - sold, symbol),
                )
            self._set_cash(self.cash + proceeds)
            fill = Fill(_now(now), symbol, "sell", sold, filled, fee, pnl, reason)
            self._insert(fill)
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        return fill

    def _set_cash(self, value: float) -> None:
        self._db.execute(
            "UPDATE settings SET value = ? WHERE key = 'cash'", (str(value),)
        )

    def _insert(self, fill: Fill) -> None:
        self._db.execute(
            "INSERT INTO fills (time, symbol, side, units, price, fee, pnl, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (fill.time, fill.symbol, fill.side, fill.units, fill.price, fill.fee,
             fill.pnl, fill.reason),
        )  # fmt: skip

    # Value -----------------------------------------------------------------------

    def value(self, prices: dict[str, float]) -> float:
        """Return cash plus the positions at ``prices`` (their cost if unpriced)."""
        return self.cash + sum(
            p.units * prices.get(s, p.avg_cost) for s, p in self.positions().items()
        )

    def record(self, prices: dict[str, float], now: datetime | None = None) -> float:
        """Store the account's value at ``prices`` for the equity curve."""
        value = self.value(prices)
        self._db.execute(
            "INSERT OR REPLACE INTO equity VALUES (?, ?)", (_now(now), value)
        )
        return value

    def fills(self, limit: int | None = None) -> list[Fill]:
        """Return the fills, newest first."""
        rows = self._db.execute(
            "SELECT * FROM fills ORDER BY id DESC LIMIT ?",
            (int(limit) if limit else -1,),
        )
        return [Fill(**{k: r[k] for k in Fill.__dataclass_fields__}) for r in rows]

    def equity_curve(self) -> list[tuple[str, float]]:
        """Return the recorded values, oldest first."""
        return [
            (r[0], r[1]) for r in self._db.execute("SELECT * FROM equity ORDER BY time")
        ]
