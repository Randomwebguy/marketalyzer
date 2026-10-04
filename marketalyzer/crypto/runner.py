"""Run the crypto rule on the paper account; meant to be called every hour.

A crypto daily bar is a UTC day, final once that day has ended. Each step
processes the finished days not processed yet, in order: at a new quarter it
picks the coins and sells those that dropped out, then it sells on a
supertrend exit and buys on a supertrend entry while BTC is in its uptrend.
Orders fill at the latest price (the last hourly close) with fee and
slippage, and the account's value is recorded at those prices.

    marketalyzer-crypto init [--cash 100000]
    marketalyzer-crypto step
    marketalyzer-crypto status
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from marketalyzer import services
from marketalyzer.crypto import momentum
from marketalyzer.crypto.ledger import CryptoLedger
from marketalyzer.paper.cli import default_home

HISTORY_DAYS = 600  # enough for a year of history plus the averages
MAX_CATCH_UP = 14  # finished days processed in one step at most

Loader = Callable[..., tuple[pd.DataFrame, Any]]


def ledger_path() -> Path:
    """Return the crypto paper account's file."""
    return default_home() / "crypto" / "paper.sqlite"


def _day(index: Any) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    return (idx.tz_localize(None) if idx.tz is not None else idx).normalize()


def load_market(today: date, load: Loader | None = None) -> dict[str, pd.DataFrame]:
    """Return each coin's finished daily bars (UTC days before ``today``)."""
    load = load or services.load_bars
    frames = {}
    for code in momentum.UNIVERSE:
        try:
            frame, _ = load(code, today - timedelta(days=HISTORY_DAYS), today, "1d")
        except Exception:  # noqa: BLE001, S112 - a coin without data waits for the next step
            continue
        frames[code] = frame[_day(frame.index) < pd.Timestamp(today)]
    return frames


def latest_prices(
    codes: list[str],
    frames: dict[str, pd.DataFrame],
    today: date,
    load: Loader | None = None,
) -> dict[str, float]:
    """Return each coin's last hourly close, or its last daily close."""
    load = load or services.load_bars
    prices = {}
    for code in codes:
        try:
            hourly, _ = load(code, today - timedelta(days=2), today, "1h")
            prices[code] = float(hourly["Close"].iloc[-1])
        except Exception:  # noqa: BLE001
            if code in frames and len(frames[code]):
                prices[code] = float(frames[code]["Close"].iloc[-1])
    return prices


def step(
    ledger: CryptoLedger,
    now: datetime | None = None,
    load: Loader | None = None,
    rule: momentum.Rule = momentum.Rule(),
) -> dict[str, Any]:
    """Process the finished days since the last step; return what was done."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today = now.date()
    finished = today - timedelta(days=1)
    last = date.fromisoformat(
        ledger.get("last_day") or (finished - timedelta(days=1)).isoformat()
    )
    days = [last + timedelta(days=k) for k in range(1, (finished - last).days + 1)][
        -MAX_CATCH_UP:
    ]
    report: dict[str, Any] = {
        "time": now.isoformat(timespec="seconds"),
        "days": [],
        "fills": [],
    }
    frames = load_market(today, load) if days else {}
    held = list(ledger.positions())
    prices = latest_prices(
        sorted(set(held) | set(frames) if days else set(held)), frames, today, load
    )
    if days and momentum.BTC in frames:
        up = momentum.btc_up(frames[momentum.BTC]["Close"], rule)
        flags = {code: momentum.signals(frame, rule) for code, frame in frames.items()}
        for day in days:
            _process(ledger, day, frames, flags, up, prices, now, rule, report)
            ledger.put("last_day", day.isoformat())
            report["days"].append(day.isoformat())
    ledger.put("prices", prices)
    report["value"] = round(ledger.record(prices, now), 2)
    report["selection"] = ledger.get("selection", [])
    return report


def _process(ledger, day, frames, flags, up, prices, now, rule, report) -> None:
    quarter = momentum.quarter_start(day)
    if ledger.get("quarter") != quarter.isoformat():
        closes = {code: frame["Close"] for code, frame in frames.items()}
        chosen = momentum.select(closes, quarter, rule)
        ledger.put("quarter", quarter.isoformat())
        ledger.put("selection", chosen)
        report["selected"] = chosen
        for code in list(ledger.positions()):
            if code not in chosen and code in prices:
                _record(
                    report,
                    ledger.sell(code, prices[code], now=now, reason="seçimden düştü"),
                )
    btc_day = up[_day(up.index) == pd.Timestamp(day)]
    btc_ok = bool(btc_day.iloc[-1]) if len(btc_day) else False
    for code in ledger.get("selection", []):
        frame = frames.get(code)
        if frame is None or code not in prices:
            continue
        at = (_day(frame.index) == pd.Timestamp(day)).nonzero()[0]
        if not len(at):
            continue
        entries, exits = flags[code]
        i = int(at[-1])
        held = code in ledger.positions()
        if held and exits[i]:
            _record(
                report,
                ledger.sell(
                    code, prices[code], now=now, reason=f"supertrend aşağı ({day})"
                ),
            )
        elif not held and entries[i] and btc_ok:
            budget = ledger.value(prices) / rule.top
            _record(report, ledger.buy(code, budget, prices[code], now=now,
                                       reason=f"supertrend yukarı, BTC trendde ({day})"))  # fmt: skip


def _record(report: dict[str, Any], fill: Any) -> None:
    if fill is not None:
        report["fills"].append(fill.to_dict())


def status(ledger: CryptoLedger) -> dict[str, Any]:
    """Return the account for the web page: value, positions, fills, curve."""
    prices = ledger.get("prices", {})
    value = ledger.value(prices)
    positions = []
    for code, p in ledger.positions().items():
        price = prices.get(code, p.avg_cost)
        positions.append({
            "symbol": code, "units": p.units, "avg_cost": p.avg_cost, "price": price,
            "value": round(p.units * price, 2), "pnl_pct": round((price / p.avg_cost - 1) * 100, 2),
            "opened": p.opened,
        })  # fmt: skip
    curve = ledger.equity_curve()
    return {
        "exists": True,
        "initial": ledger.initial_cash,
        "cash": round(ledger.cash, 2),
        "value": round(value, 2),
        "return_pct": round((value / ledger.initial_cash - 1) * 100, 2),
        "positions": positions,
        "quarter": ledger.get("quarter"),
        "selection": ledger.get("selection", []),
        "last_day": ledger.get("last_day"),
        "created": ledger.created,
        "fills": [f.to_dict() for f in ledger.fills(limit=50)],
        "equity": [{"t": t, "v": round(v, 2)} for t, v in curve[-2000:]],
        "rule": momentum.Rule().__dict__,
        "fee": ledger.fee,
        "slippage": ledger.slippage,
    }


def main(argv: list[str] | None = None) -> int:
    """Create, step or show the crypto paper account."""
    parser = argparse.ArgumentParser(prog="marketalyzer-crypto",
                                     description="Kripto sanal hesabı (7/24, USD).")  # fmt: skip
    parser.add_argument("command", choices=("init", "step", "status"))
    parser.add_argument("--cash", type=float, default=100_000.0)
    args = parser.parse_args(argv)
    path = ledger_path()
    if args.command == "init":
        CryptoLedger.create(path, args.cash).close()
        print(json.dumps({"created": str(path), "cash": args.cash}))
        return 0
    if not path.exists():
        print(
            "Kripto sanal hesabı yok; önce 'marketalyzer-crypto init'.", file=sys.stderr
        )
        return 1
    with CryptoLedger(path) as ledger:
        out = step(ledger) if args.command == "step" else status(ledger)
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
