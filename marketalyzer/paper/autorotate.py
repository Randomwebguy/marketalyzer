"""Run the monthly momentum rotation on a paper account: ``marketalyzer-rotation``.

On the first step of each month the stocks are ranked on the last close of
the previous month (``marketalyzer.rotation``); the stocks that left the top
are sold first and, once those sells have filled, the new ones are bought, all
with market orders that fill at the open of the next bar. With an hourly feed
that is the next hourly bar. Turning the plan on ranks on the latest close and
buys right away. The rotation only ever sells what it bought, so other
positions in the account are left alone.

The same ``RotationTrader`` runs live (``ProviderFeed``, called every few
minutes by a timer) and over history (``replay`` with a ``FrameFeed``). The
plan and its history are kept in ``<home>/rotation/<account>.json``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd

from marketalyzer import rotation
from marketalyzer.ai.settings import write_json
from marketalyzer.paper.account import OrderRejected, PaperAccount
from marketalyzer.paper.cli import account_path, default_home
from marketalyzer.paper.feed import Feed, FrameFeed, ProviderFeed
from marketalyzer.paper.models import IST, as_istanbul
from marketalyzer.paper.trader import PaperTrader
from marketalyzer.services import number, stamp, today

TAG = "rotation"
BENCHMARK = "XU100"
BUFFER = 0.01  # cash kept back from each buy for price moves until the fill


@dataclass
class Plan:
    """What the rotation trades and where it stands."""

    symbols: list[str] = field(default_factory=lambda: list(rotation.LARGE_CAPS))
    lookback_months: int = 6
    skip_months: int = 1
    top: int = 5
    absolute: bool = False
    market: bool = False
    enabled: bool = True
    owned: list[str] = field(default_factory=list)  # what the rotation bought
    month: str | None = None  # "YYYY-MM" of the last decision
    phase: str = "idle"  # idle, selling or buying
    picks: list[dict[str, Any]] = field(default_factory=list)
    to_buy: list[str] = field(default_factory=list)
    slot: float = 0.0  # TRY per pick in the current rebalance
    history: list[dict[str, Any]] = field(default_factory=list)

    def config(self) -> rotation.RotationConfig:
        """Return the ranking settings as a rotation config."""
        return rotation.RotationConfig(
            symbols=self.symbols,
            start=today() - timedelta(days=365),
            end=today(),
            lookback_months=self.lookback_months,
            skip_months=self.skip_months,
            top=self.top,
            absolute=self.absolute,
            market=self.market,
        )


def plan_path(account: str):
    """Return the file that keeps an account's rotation plan."""
    return default_home() / "rotation" / f"{account}.json"


def load_plan(account: str) -> Plan | None:
    """Return the account's plan, or None."""
    try:
        data = json.loads(plan_path(account).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Plan(**data)


def save_plan(account: str, plan: Plan) -> Plan:
    """Check and store a plan.

    Raises
    ------
    ValueError
        For settings that cannot run (Turkish message).
    """
    plan.symbols = plan.config().codes()
    plan.config().check()
    write_json(plan_path(account), asdict(plan))
    return plan


def live_feed() -> Feed:
    """Return the delayed live feed the timer trades on: hourly bars."""
    return ProviderFeed("1h")


def _month(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


class RotationTrader:
    """Step a plan on an account: fill orders, then rebalance when a month starts."""

    def __init__(self, account: PaperAccount, feed: Feed, plan: Plan):
        self.account = account
        self.feed = feed
        self.plan = plan

    def step(self, now: datetime | None = None) -> dict[str, Any]:
        """Process new bars, move the rebalance on and decide when one is due."""
        now = as_istanbul(now or datetime.now(IST))
        plan = self.plan
        report: dict[str, Any] = {"time": now.isoformat(), "fills": [], "orders": [],
                                  "decision": None, "errors": {}}  # fmt: skip
        self._process(self._watched(), now, report)
        if plan.phase == "selling" and not self._open("sell"):
            plan.phase = "buying"
            plan.slot = self._budget() / plan.top
        if plan.phase == "buying":
            self._buy(now, report)
        if plan.enabled and plan.phase == "idle" and plan.month != _month(now):
            self._decide(now, report)
        report["phase"] = plan.phase
        return report

    # Orders and fills -------------------------------------------------------------

    def _watched(self) -> list[str]:
        plan = self.plan
        open_orders = {
            o.symbol for o in self.account.orders(status="open") if o.tag == TAG
        }
        picks = {p["symbol"] for p in plan.picks}
        return sorted(set(plan.owned) | picks | set(plan.to_buy) | open_orders)

    def _process(self, symbols: list[str], now: datetime, report: dict) -> None:
        if not symbols:
            return
        done = PaperTrader(self.account, symbols, self.feed).step(now)
        report["errors"].update(done.errors)
        for fill in done.fills:
            report["fills"].append(fill.to_dict())
            if self.account.order(fill.order_id).tag != TAG:
                continue
            row = self.plan.history[-1] if self.plan.history else None
            if fill.side == "buy":
                if fill.symbol not in self.plan.owned:
                    self.plan.owned.append(fill.symbol)
                if row is not None:
                    row["bought"].append(fill.symbol)
            else:
                if fill.symbol in self.plan.owned:
                    self.plan.owned.remove(fill.symbol)
                if row is not None:
                    row["sold"].append(fill.symbol)

    def _open(self, side: str) -> bool:
        return any(
            o.tag == TAG and o.side == side for o in self.account.orders(status="open")
        )

    def _budget(self) -> float:
        """Return the cash plus what the rotation holds, at the last prices."""
        held = sum(
            self.account.held(code) * (self.account.mark(code) or 0.0)
            for code in self.plan.owned
        )
        return self.account.cash + held

    def _buy(self, now: datetime, report: dict) -> None:
        plan, account = self.plan, self.account
        waiting = []
        for code in plan.to_buy:
            if account.held(code) or any(
                o.side == "buy" for o in account.orders(status="open", symbol=code)
            ):
                continue
            price = account.mark(code)
            if price is None:
                waiting.append(code)  # No bar yet: buy once a price is known.
                continue
            unit = (
                price * (1 + account.slippage / 2) * (1 + account.costs.effective_rate)
            )
            budget = min(plan.slot, account.buying_power()) * (1 - BUFFER)
            qty = int(budget // unit)
            if qty < 1:
                plan.history[-1]["errors"].append(f"{code}: yetersiz nakit")
                continue
            try:
                order = account.submit(code, "buy", qty, tag=TAG, now=now)
            except OrderRejected as error:
                plan.history[-1]["errors"].append(f"{code}: {error}")
                continue
            report["orders"].append(order.to_dict())
        plan.to_buy = waiting
        if not waiting and not self._open("buy"):
            plan.phase = "idle"

    # Decisions --------------------------------------------------------------------

    def _closes(self, now: datetime, cutoff: date | None) -> rotation.Prices:
        """Daily closes known at ``now``, before ``cutoff`` if given."""
        frames = {}
        for code in self.plan.config().codes():
            frame = self.feed.daily(code, now)
            if cutoff is not None:
                frame = frame[frame.index < pd.Timestamp(cutoff)]
            frames[code] = frame["Close"]
        closes = pd.concat(frames, axis=1, sort=True).ffill()
        index_closes = None
        try:
            bench = self.feed.daily(BENCHMARK, now)["Close"]
            index_closes = bench.reindex(closes.index).ffill()
        except (KeyError, ValueError, OSError):
            pass
        return rotation.Prices(closes, closes * float("nan"), index_closes)

    def _decide(self, now: datetime, report: dict) -> None:
        plan, account = self.plan, self.account
        first = plan.month is None
        cutoff = None if first else now.date().replace(day=1)
        plan.month = _month(now)
        try:
            prices = self._closes(now, cutoff)
        except Exception as error:  # A failed download: try again next month.
            report["errors"]["karar"] = f"{type(error).__name__}: {error}"
            return
        i = len(prices.closes) - 1
        if i < plan.lookback_months * rotation.MONTH:
            report["errors"]["karar"] = "Momentum için yeterli geçmiş yok."
            return
        chosen = rotation._choose(prices, i, plan.config())
        plan.picks = chosen["picks"]
        wanted = [p["symbol"] for p in plan.picks]
        row = {"at": now.isoformat(), "decided_on": stamp(prices.closes.index[i]),
               "first": first, "picks": plan.picks, "market_up": chosen["market_up"],
               "sold": [], "bought": [], "errors": []}  # fmt: skip
        plan.history.append(row)
        report["decision"] = row
        sells = 0
        for code in list(plan.owned):
            if code in wanted:
                continue
            qty = account.held(code)
            if qty <= 0:
                plan.owned.remove(code)
                continue
            try:
                order = account.submit(code, "sell", qty, tag=TAG, now=now)
            except OrderRejected as error:
                row["errors"].append(f"{code}: {error}")
                continue
            report["orders"].append(order.to_dict())
            sells += 1
        plan.to_buy = [code for code in wanted if code not in plan.owned]
        if sells:
            plan.phase = "selling"
            return
        plan.phase = "buying"
        plan.slot = self._budget() / plan.top
        # Price the new picks before buying them.
        self._process(plan.to_buy, now, report)
        self._buy(now, report)


# --- Replay ---------------------------------------------------------------------------


def _change(frame: pd.DataFrame, start: date, end: date) -> float | None:
    closes = frame["Close"]
    closes = closes[
        (closes.index >= pd.Timestamp(start)) & (closes.index <= pd.Timestamp(end))
    ]
    if len(closes) < 2:
        return None
    return (float(closes.iloc[-1]) / float(closes.iloc[0]) - 1) * 100


def replay(
    account: PaperAccount, feed: FrameFeed, plan: Plan, start: date, end: date
) -> dict[str, Any]:
    """Run a plan over history as if live: the account trades on the feed's bars.

    The plan is turned on at ``start`` (buying the picks of the latest close)
    and rebalances at the start of every later month until ``end``.
    """
    trader = RotationTrader(account, feed, plan)
    moments = feed.times(
        include_closes=True,
        start=datetime.combine(start, time(0), IST),
        end=datetime.combine(end, time(23, 59), IST),
    )
    for moment in moments:
        trader.step(moment)
    summary = account.summary()
    changes = [_change(feed.daily_frames[code], start, end) for code in plan.symbols]
    changes = [c for c in changes if c is not None]
    bench = None
    if BENCHMARK in feed.daily_frames:
        bench = _change(feed.daily_frames[BENCHMARK], start, end)
    return {
        "plan": asdict(plan),
        "history": plan.history,
        "summary": summary,
        "equity": account.equity_curve(),
        "fills": [fill.to_dict() for fill in account.fills()],
        "equal": {
            "return_pct": number(sum(changes) / len(changes), 2) if changes else None
        },
        "benchmark": {
            "symbol": BENCHMARK,
            "return_pct": number(bench, 2) if bench is not None else None,
        },
    }


# --- Command line ---------------------------------------------------------------------


def step_account(account_name: str, feed: Feed | None = None) -> dict[str, Any]:
    """Step the account's plan once, if it is on; store the plan."""
    plan = load_plan(account_name)
    if plan is None or not plan.enabled:
        return {"skipped": True, "reason": "Otomatik rotasyon kapalı."}
    path = account_path(account_name)
    if not path.exists():
        return {"skipped": True, "reason": "Sanal hesap yok."}
    with PaperAccount(path) as account:
        report = RotationTrader(account, feed or live_feed(), plan).step()
    write_json(plan_path(account_name), asdict(plan))
    return {**report, "skipped": False}


def main(argv: list[str] | None = None) -> int:
    """Step or show the paper account's automatic rotation."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-rotation",
        description="Momentum rotasyonunu sanal hesapta her ay otomatik uygular.",
    )
    parser.add_argument("command", choices=("step", "status"))
    parser.add_argument("--account", default="web", help="Sanal hesap adı (web)")
    args = parser.parse_args(argv)
    if args.command == "status":
        plan = load_plan(args.account)
        print(json.dumps(asdict(plan) if plan else None, ensure_ascii=False, indent=1))
        return 0
    report = step_account(args.account)
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0
