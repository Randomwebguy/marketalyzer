"""Blind backtest: trade bar by bar, deciding only from what was known then.

Between ``start`` and ``end`` the test walks forward one bar at a time. On a
bar where the strategy script signals (or a periodic review is due), the
decision maker gets the bars up to and including that bar, cut from the data,
and nothing later. Its order fills at the next bar's open. The script's signal
is recomputed on the cut data before every decision, so a script that looked
ahead would be caught; the language model also never sees the ticker, the
dates or the price level, so it cannot recall what happened next.

Each symbol trades its own equal share of the cash, long only. The same
signals are also traded without the AI ("signals") and held from the first
bar ("hold"), to show what the AI added.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer import research
from marketalyzer.ai import author, learn
from marketalyzer.ai.decide import (
    Decider,
    position_view,
    script_view,
    snapshot,
)
from marketalyzer.ai.openrouter import OpenRouterError
from marketalyzer.backtest.costs import BistCosts, tick_size
from marketalyzer.scripting import Script, ScriptError, compile_script
from marketalyzer.services import (
    _parse_date,
    _script,
    number,
    stamp,
    thin,
    today,
)

MAX_DECISIONS = 800
REVIEW_CHOICES = (0, 5, 10, 20)
BLIND_INTERVALS = ("1d", "1W")
MODES = ("ai", "signals")
# off: decide alone; journal: learn from resolved outcomes; rounds: also revise
# the script between walk-forward windows.
LEARNING = ("off", "journal", "rounds")
MAX_ROUNDS = 4
EVENTS = {"entry": "giriş", "exit": "çıkış", "review": "gözden geçirme"}
END = "dönem sonu (açık)"  # a position still open when the test ends
ROUND_END = "tur sonu"  # a position closed because its window ended
_DATE = re.compile(r"\b(19|20)\d\d-\d\d-\d\d\b")


class Cancelled(Exception):
    """The person stopped the test."""


@dataclass
class BlindConfig:
    """What to test: symbols, period, strategy script and who decides."""

    symbols: list[str]
    start: date
    end: date
    script: str | None = None
    source: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    mode: str = "ai"
    review_every: int = 0
    years: float = 2.0
    interval: str = "1d"
    cash: float = 100_000.0
    costs: BistCosts = field(default_factory=BistCosts)
    slippage: float = 0.001
    stop_loss_pct: float | None = None
    learning: str = "off"
    rounds: int = 1

    def check(self) -> None:
        """Raise ValueError (in Turkish) for settings that cannot run."""
        if self.mode not in MODES:
            raise ValueError("Karar modu 'ai' ya da 'signals' olmalı.")
        if self.learning not in LEARNING:
            raise ValueError("Öğrenme 'off', 'journal' ya da 'rounds' olmalı.")
        if not 1 <= self.rounds <= MAX_ROUNDS:
            raise ValueError(f"Tur sayısı 1 ile {MAX_ROUNDS} arasında olmalı.")
        if self.rounds > 1 and self.learning != "rounds":
            raise ValueError("Turlar için öğrenme 'rounds' (günlük + turlar) olmalı.")
        if self.interval not in BLIND_INTERVALS:
            raise ValueError(f"Kör test zaman dilimi: {', '.join(BLIND_INTERVALS)}.")
        if self.review_every not in REVIEW_CHOICES:
            raise ValueError("Gözden geçirme aralığı 0, 5, 10 ya da 20 bar olmalı.")
        if self.start >= self.end:
            raise ValueError("Test başlangıcı bitişten önce olmalı.")
        if self.end > today():
            raise ValueError("Test bitişi bugünden sonra olamaz.")
        if self.stop_loss_pct is not None and not 0 < self.stop_loss_pct < 50:
            raise ValueError("Zarar durdur yüzdesi 0 ile 50 arasında olmalı.")
        if not self.script and not self.source:
            raise ValueError("Bir strateji scripti seçin.")


@dataclass
class Data:
    """One symbol's bars (warm-up, training and test) and its signals."""

    code: str
    frame: pd.DataFrame
    first: int  # first test bar
    train_first: int  # first training bar
    result: Any  # the script's run over all bars
    training: dict[str, dict[str, Any]]  # catalog signal stats before ``first``

    @property
    def test_bars(self) -> int:
        """Return how many bars the test covers."""
        return len(self.frame) - self.first


def _edges(mask: np.ndarray | None, n: int) -> np.ndarray:
    if mask is None:
        return np.zeros(n, dtype=bool)
    mask = np.asarray(mask, dtype=bool)
    return mask & ~np.r_[False, mask[:-1]]


def load(config: BlindConfig, script: Script) -> tuple[list[Data], pd.DataFrame | None]:
    """Load every symbol up to ``end`` (never later) and run the script once.

    The script's full run only marks the bars where something may happen; the
    decision itself is made on data cut at that bar.
    """
    codes = research._codes(config.symbols)
    train_start = config.start - timedelta(days=round(config.years * 365.25))
    frames, benchmark = research.load_study_frames(
        codes, train_start, config.end, config.interval
    )
    data = []
    for code in codes:
        frame, shown = frames[code]
        index = pd.DatetimeIndex(frame.index)
        first = int(np.searchsorted(index, pd.Timestamp(config.start, tz=index.tz)))
        if first >= len(frame) - 1:
            raise ValueError(f"{code}: test döneminde yeterli bar yok.")
        train_first = int(np.searchsorted(index, shown)) if shown is not None else 0
        if first - train_first < research.MIN_BARS:
            raise ValueError(
                f"{code}: test başlangıcından önce en az {research.MIN_BARS} bar"
                " gerekli; eğitim süresini uzatın ya da başlangıcı ileri alın."
            )
        result = script.run(frame, config.inputs, symbol=code, interval=config.interval)
        if result.entries is None:
            raise ValueError(
                "Seçilen script bir strateji değil; strategy() ile bildirilmiş ve"
                " strategy.entry kullanan bir script seçin."
            )
        training_frame = frame.iloc[:first]
        stats = research.signal_stats(research.indicators(training_frame), train_first)
        data.append(
            Data(code, frame, first, train_first, result, {s["key"]: s for s in stats})
        )
    return data, benchmark


@dataclass
class _Account:
    cash: float
    qty: int = 0
    entry_price: float = 0.0
    entry_time: Any = None
    entry_bar: int = 0
    peak: float = 0.0
    stop: float = math.nan
    limit: float = math.nan
    entry_note: dict[str, Any] = field(default_factory=dict)
    fees: float = 0.0


class Simulation:
    """Trade one symbol over its test bars.

    ``decide`` is None for the signals-only run; otherwise it receives the
    snapshot of a cut frame and returns a decision dict.
    """

    def __init__(
        self,
        data: Data,
        config: BlindConfig,
        allocation: float,
        benchmark: pd.DataFrame | None = None,
        decide: Callable[[dict[str, Any]], Any] | None = None,
        on_decision: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        script: Script | None = None,
        strategy: dict[str, Any] | None = None,
        codes: list[str] | None = None,
        on_close: Callable[[Simulation, dict, dict], None] | None = None,
        round_no: int = 1,
    ):
        self.data = data
        self.config = config
        self.benchmark = benchmark
        self.decide = decide
        self.on_decision = on_decision
        self.cancelled = cancelled
        self.script = script
        self.strategy = strategy
        # Every ticker of the test: none may appear in a view.
        self.codes = codes or [data.code]
        # Called with (simulation, trade, entry decision) when a trade closes.
        self.on_close = on_close
        self.round_no = round_no
        self.account = _Account(cash=allocation)
        self.allocation = allocation
        self.trades: list[dict[str, Any]] = []
        self.trade_bars: list[tuple[int, int]] = []  # (entry bar, exit bar)
        self.decisions: list[dict[str, Any]] = []
        self.equity: list[tuple[Any, float]] = []
        self.exposure = 0
        self.audit = {"decisions": 0, "rechecks": 0, "mismatches": 0, "leaks": 0}

    # Orders -----------------------------------------------------------------------

    def _buy(self, i: int, note: dict[str, Any]) -> None:
        frame, account, config = self.data.frame, self.account, self.config
        price = float(frame["Open"].iloc[i]) * (1 + config.slippage / 2)
        per_share = price * (1 + config.costs.effective_rate)
        qty = int(account.cash // per_share)
        if qty <= 0:
            return
        fee = config.costs(qty, price)
        account.cash -= qty * price + fee
        account.qty = qty
        account.entry_price = price
        account.entry_time = frame.index[i]
        account.entry_bar = i
        account.peak = price
        account.fees = fee
        account.entry_note = note
        self._set_levels(i - 1, fresh=True)

    def _sell(self, i: int, price: float, reason: str) -> None:
        frame, account, config = self.data.frame, self.account, self.config
        price *= 1 - config.slippage / 2
        fee = config.costs(account.qty, price)
        account.cash += account.qty * price - fee
        cost = account.qty * account.entry_price
        pnl = account.qty * (price - account.entry_price) - account.fees - fee
        self.trades.append(
            {
                "symbol": self.data.code,
                "entry_time": stamp(account.entry_time),
                "exit_time": stamp(frame.index[i]),
                "qty": account.qty,
                "entry_price": number(account.entry_price),
                "exit_price": number(price),
                "pnl": number(pnl, 2),
                "return_pct": number(pnl / cost * 100, 2) if cost else None,
                "bars": i - account.entry_bar,
                "exit_reason": reason,
                "confidence": account.entry_note.get("confidence"),
            }
        )
        self.trade_bars.append((account.entry_bar, i))
        if self.on_close:
            self.on_close(self, self.trades[-1], account.entry_note)
        account.qty = 0
        account.stop = account.limit = math.nan

    def _set_levels(self, i: int, fresh: bool = False) -> None:
        """Carry the script's stop and target levels known at bar ``i``'s close."""
        result, account = self.data.result, self.account
        if fresh:
            account.stop = account.limit = math.nan
        if i < 0:
            return
        arrays = (result.stops, result.limits, result.loss_ticks, result.profit_ticks)
        stop, limit, loss, profit = (
            float(values[i]) if values is not None else math.nan for values in arrays
        )
        if not math.isfinite(stop) and math.isfinite(loss) and account.entry_price:
            stop = account.entry_price - loss * tick_size(account.entry_price)
        if not math.isfinite(limit) and math.isfinite(profit) and account.entry_price:
            limit = account.entry_price + profit * tick_size(account.entry_price)
        if math.isfinite(stop):
            account.stop = stop
        if math.isfinite(limit):
            account.limit = limit
        if self.config.stop_loss_pct and account.entry_price:
            floor = account.entry_price * (1 - self.config.stop_loss_pct / 100)
            account.stop = (
                max(account.stop, floor) if math.isfinite(account.stop) else floor
            )

    def _exits_inside_bar(self, i: int) -> None:
        """Fill the stop or the target if this bar reached it (stop first)."""
        account = self.account
        frame = self.data.frame
        o, h, low = (float(frame[k].iloc[i]) for k in ("Open", "High", "Low"))
        if math.isfinite(account.stop):
            if o <= account.stop:
                self._sell(i, o, "zarar durdur (boşluk)")
                return
            if low <= account.stop:
                self._sell(i, account.stop, "zarar durdur")
                return
        if math.isfinite(account.limit):
            if o >= account.limit:
                self._sell(i, o, "kâr al (boşluk)")
            elif h >= account.limit:
                self._sell(i, account.limit, "kâr al")

    # Decisions --------------------------------------------------------------------

    def _view(
        self, i: int, event: str, journal: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], Any]:
        """Build the decision snapshot from the bars up to ``i`` only."""
        frame = self.data.frame
        cut = frame.iloc[: i + 1]
        assert cut.index[-1] == frame.index[i]  # noqa: S101 - the blindness rule
        result = self.script.run(
            cut,
            self.config.inputs,
            symbol=self.data.code,
            interval=self.config.interval,
        )
        self.audit["rechecks"] += 1
        benchmark = None
        if self.benchmark is not None:
            benchmark = self.benchmark[self.benchmark.index <= frame.index[i]]
        close = float(cut["Close"].iloc[-1])
        account = self.account
        position = None
        if account.qty:
            position = position_view(
                account.entry_price, close, i - account.entry_bar, account.peak
            )
        view = snapshot(
            cut,
            benchmark=benchmark,
            script=script_view(result, close, EVENTS[event]),
            position=position,
            training=self.data.training,
            strategy=self.strategy,
            journal=journal,
        )
        return view, result

    def _leaks(self, view: dict[str, Any]) -> bool:
        """Check that the snapshot names no stock of the test and no date."""
        text = json.dumps(view, ensure_ascii=False)
        tickers = "|".join(re.escape(code) for code in self.codes)
        return bool(re.search(rf"\b(?:{tickers})\b", text) or _DATE.search(text))

    def _event(self, i: int, entries: np.ndarray, exits: np.ndarray) -> str | None:
        account = self.account
        if not account.qty and entries[i]:
            return "entry"
        if account.qty and exits[i]:
            return "exit"
        every = self.config.review_every
        if self.decide is None or not every:
            return None
        since = i - (
            self.decisions[-1]["bar"] if self.decisions else self.data.first - 1
        )
        raw = self.data.result.entries
        if since >= every and (account.qty or (raw is not None and raw[i])):
            return "review"
        return None

    def prepare(
        self, i: int, event: str, journal: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Return the view for a decision on bar ``i``, or None if none is due.

        None means the signal was different on the cut data (a script that
        looked ahead) or the view named a stock or a date.
        """
        view, cut_result = self._view(i, event, journal)
        if event in ("entry", "exit"):
            flags = cut_result.entries if event == "entry" else cut_result.exits
            full = (
                self.data.result.entries if event == "entry" else self.data.result.exits
            )
            if bool(flags[-1]) != bool(full[i]):
                self.audit["mismatches"] += 1
                return None
        if self._leaks(view):
            self.audit["leaks"] += 1
            return None
        return view

    def record(
        self, i: int, event: str, view: dict[str, Any], decision: Any
    ) -> str | None:
        """Keep the decision; return "buy", "sell" or None."""
        self.audit["decisions"] += 1
        record = {
            "bar": i,
            "time": stamp(self.data.frame.index[i]),
            "symbol": self.data.code,
            "event": EVENTS[event],
            "signals": [item["aciklama"] for item in view["aktif_sinyaller"]],
            "close": number(self.data.frame["Close"].iloc[i]),
            "data_end": stamp(self.data.frame.index[i]),
            "round": self.round_no,
            **decision.to_dict(),
        }
        self.decisions.append(record)
        if self.on_decision:
            self.on_decision(record)
        if decision.action in ("buy", "sell"):
            return decision.action
        return None

    # Run --------------------------------------------------------------------------

    def begin(self) -> None:
        """Prepare the walk: the signal edges and the bar of each test date."""
        frame = self.data.frame
        n = len(frame)
        self._entries = _edges(self.data.result.entries, n)
        self._exits = _edges(self.data.result.exits, n)
        self._close = frame["Close"].to_numpy(dtype=float)
        self._high = frame["High"].to_numpy(dtype=float)
        self.dates = {frame.index[i]: i for i in range(self.data.first, n)}
        self.pending = None

    def step(self, i: int) -> str | None:
        """Trade bar ``i`` up to its close; return the event to decide on, if any.

        The order queued at the previous close fills at this open, then the
        stop and the target are checked inside the bar.
        """
        frame, account = self.data.frame, self.account
        if self.pending:
            action, note = self.pending
            if action == "buy" and not account.qty:
                self._buy(i, note)
            elif action == "sell" and account.qty:
                self._sell(i, float(frame["Open"].iloc[i]), "sinyal")
            self.pending = None
        if account.qty:
            self._exits_inside_bar(i)
        if account.qty:
            account.peak = max(account.peak, self._high[i])
            self.exposure += 1
            self._set_levels(i)
        self.equity.append(
            (frame.index[i], account.cash + account.qty * self._close[i])
        )
        if i >= len(frame) - 1:  # A decision on the last bar could not be filled.
            return None
        event = self._event(i, self._entries, self._exits)
        if event and len(self.decisions) < MAX_DECISIONS:
            return event
        return None

    def queue(self, action: str | None, note: dict[str, Any]) -> None:
        """Fill ``action`` ("buy" or "sell") at the next bar's open."""
        if action:
            self.pending = (action, note)

    def finish(self, reason: str = END) -> Simulation:
        """Close a position still open at the last bar's close.

        At the end of the test the trade is marked open; at the end of a
        window (``ROUND_END``) it is an ordinary close.
        """
        n = len(self.data.frame)
        if self.account.qty:
            self._sell(n - 1, self._close[n - 1], reason)
            if reason == END:
                self.trades[-1]["open"] = True
        return self

    def run(self) -> Simulation:
        """Walk the test bars alone; return self with trades, decisions, equity."""
        _walk([self], cancelled=self.cancelled)
        return self


class Learner:
    """Keep the journal of an AI walk: schedule and attach every outcome.

    A buy's outcome is its trade, attached when the trade closes. A rejected
    entry's outcome is the trade the signals-only run opened at the same
    next open (what was missed or avoided), known when that trade closed;
    without one, and for sells and holds, it is the move from the next open
    over ``learn.HORIZON`` bars, known on that bar.
    """

    def __init__(
        self,
        journal: learn.Journal,
        signal_sims: list[Simulation],
        *,
        coach: learn.Coach | None = None,
        strategy: dict[str, Any] | None = None,
        emit: Callable[[dict[str, Any]], None] = lambda event: None,
    ):
        self.journal = journal
        self.signals = {sim.data.code: sim for sim in signal_sims}
        self.coach = coach
        self.strategy = strategy
        self.emit = emit
        self._open: dict[int, learn.Entry] = {}

    def day(self, when: Any) -> dict[str, Any] | None:
        """Return what the decisions on ``when`` may know.

        Once ``learn.REFLECT_EVERY`` new outcomes are known, the coach first
        rewrites the lessons from them.
        """
        if self.coach and self.journal.fresh(when) >= learn.REFLECT_EVERY:
            self.reflect(when)
        return self.journal.view(when)

    def reflect(self, when: Any, **note: Any) -> None:
        """Have the coach rewrite the lessons from what is known on ``when``."""
        if not self.coach or not self.journal.fresh(when):
            return
        lessons = learn.reflect(
            self.journal,
            when,
            self.coach,
            strategy=self.strategy,
            codes=list(self.journal.letters),
        )
        self.journal.mark(when)
        if lessons:
            resolved = len(self.journal.known(when))
            self.journal.lessons = lessons
            self.journal.history.append(
                {"resolved": resolved, **note, "lessons": lessons}
            )
            self.emit(
                {"type": "lesson", "lessons": lessons, "resolved": resolved, **note}
            )

    def add(self, sim: Simulation, i: int, view: dict, record: dict) -> None:
        """Keep a decision just made on bar ``i`` and schedule its outcome."""
        holding = (view.get("pozisyon") or {}).get("durum") == "var"
        entry = self.journal.add(record, holding=holding, features=learn.features(view))
        if record["action"] == "buy":
            self._open[id(record)] = entry
            return
        outcome = None if holding else self._missed(sim, i)
        outcome = outcome or self._forward(sim, i)
        if outcome:
            self.journal.resolve(entry, *outcome)

    def closed(self, sim: Simulation, trade: dict, note: dict) -> None:
        """Attach a closed trade to the buy decision that opened it."""
        entry = self._open.pop(id(note), None)
        if entry is not None:
            exit_bar = sim.trade_bars[-1][1]
            self.journal.resolve(
                entry,
                trade["return_pct"] or 0.0,
                "işlem",
                sim.data.frame.index[exit_bar],
            )

    def _missed(self, sim: Simulation, i: int) -> tuple | None:
        signals = self.signals.get(sim.data.code)
        if signals is None:
            return None
        for (entry_bar, exit_bar), trade in zip(signals.trade_bars, signals.trades):
            if entry_bar == i + 1:
                frame = signals.data.frame
                return (
                    trade["return_pct"] or 0.0,
                    "sinyal işlemi",
                    frame.index[exit_bar],
                )
        return None

    @staticmethod
    def _forward(sim: Simulation, i: int) -> tuple | None:
        frame = sim.data.frame
        if i + 1 >= len(frame):
            return None
        j = min(i + 1 + learn.HORIZON, len(frame) - 1)
        start = float(frame["Open"].iloc[i + 1])
        if not start:
            return None
        pct = (float(frame["Close"].iloc[j]) / start - 1) * 100
        return pct, f"{j - i - 1} bar sonra", frame.index[j]


def _walk(
    sims: list[Simulation],
    *,
    cancelled: Callable[[], bool] = lambda: False,
    learner: Learner | None = None,
    reason: str = END,
) -> None:
    """Walk the simulations together, one date at a time.

    On each date every symbol trading that day steps to its close; then the
    decisions due that day are asked in parallel (each on its own cut data,
    with what the ``learner`` knows by that day) and their orders queued for
    the next open. Signals-only simulations follow the signals. Positions
    still open at the end are closed with ``reason``.
    """
    for sim in sims:
        sim.begin()
    calendar = sorted(set().union(*(sim.dates for sim in sims)))
    asking = any(sim.decide for sim in sims)
    pool = ThreadPoolExecutor(max_workers=min(4, len(sims))) if asking else None
    try:
        for when in calendar:
            if cancelled():
                raise Cancelled
            due = _step_day(sims, when)
            if due:
                _ask_day(pool, due, learner, when)
    finally:
        if pool:
            pool.shutdown()
    for sim in sims:
        sim.finish(reason)


def _step_day(sims: list[Simulation], when: Any) -> list[tuple[Simulation, int, str]]:
    """Step every symbol trading on ``when``; return the decisions due.

    Signals-only simulations follow their signal at once.
    """
    due = []
    for sim in sims:
        i = sim.dates.get(when)
        if i is None:
            continue
        event = sim.step(i)
        if event is None:
            continue
        if sim.decide is None:
            sim.queue({"entry": "buy", "exit": "sell"}.get(event), {})
        else:
            due.append((sim, i, event))
    return due


def _ask_day(
    pool: ThreadPoolExecutor,
    due: list[tuple[Simulation, int, str]],
    learner: Learner | None,
    when: Any,
) -> None:
    """Ask the day's decisions in parallel; record them and queue the orders."""
    journal = learner.day(when) if learner else None

    def ask(item: tuple[Simulation, int, str]) -> tuple[Any, Any]:
        sim, i, event = item
        view = sim.prepare(i, event, journal)
        return view, (sim.decide(view) if view is not None else None)

    for (sim, i, event), (view, decision) in zip(due, pool.map(ask, due)):
        if view is None:
            continue
        action = sim.record(i, event, view, decision)
        sim.queue(action, sim.decisions[-1])
        if learner:
            learner.add(sim, i, view, sim.decisions[-1])


# --- Results ----------------------------------------------------------------------


def metrics(
    curve: pd.Series,
    trades: list[dict[str, Any]],
    per_year: int,
    exposure: float,
    initial: float,
) -> dict[str, Any]:
    """Summary statistics of an equity curve that started at ``initial``."""
    values = pd.concat([pd.Series([initial]), curve.reset_index(drop=True)])
    final = float(values.iloc[-1])
    returns = values.pct_change().dropna()
    sharpe = None
    if len(returns) > 2 and returns.std() > 0:
        sharpe = number(returns.mean() / returns.std() * math.sqrt(per_year), 2)
    wins = [t for t in trades if (t["pnl"] or 0) > 0]
    gains = sum(t["pnl"] for t in wins)
    losses = -sum(t["pnl"] for t in trades if (t["pnl"] or 0) < 0)
    return {
        "return_pct": number((final / initial - 1) * 100, 2),
        "equity_final": number(final, 2),
        "max_drawdown_pct": number(
            float((values / values.cummax() - 1).min()) * 100, 2
        ),
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate_pct": number(len(wins) / len(trades) * 100, 1) if trades else None,
        "profit_factor": number(gains / losses, 2) if losses else None,
        "avg_trade_pct": number(
            sum(t["return_pct"] or 0 for t in trades) / len(trades), 2
        )
        if trades
        else None,
        "exposure_pct": number(exposure * 100, 1),
    }


def _portfolio(sims: list[Simulation], cash: float) -> pd.Series:
    curves = [
        pd.Series(dict(sim.equity)).rename(sim.data.code) for sim in sims if sim.equity
    ]
    joined = pd.concat(curves, axis=1).sort_index().ffill()
    for sim in sims:
        joined[sim.data.code] = joined[sim.data.code].fillna(sim.allocation)
    return joined.sum(axis=1)


def _hold_curve(data: Data, config: BlindConfig, allocation: float) -> pd.Series:
    frame = data.frame.iloc[data.first :]
    price = float(frame["Open"].iloc[0]) * (1 + config.slippage / 2)
    qty = int(allocation // (price * (1 + config.costs.effective_rate)))
    rest = allocation - qty * price - (config.costs(qty, price) if qty else 0)
    return (rest + qty * frame["Close"]).rename(data.code)


def _points(curve: pd.Series) -> list[dict[str, Any]]:
    return thin([{"t": stamp(t), "v": number(v, 2)} for t, v in curve.items()])


def _exposure(sims: list[Simulation]) -> float:
    bars = sum(sim.data.test_bars for sim in sims)
    return sum(sim.exposure for sim in sims) / bars if bars else 0.0


def summarize(
    config: BlindConfig,
    data: list[Data],
    benchmark: pd.DataFrame | None,
    signal_sims: list[Simulation],
    ai_sims: list[Simulation] | None,
    model: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the result: AI, signals-only and hold, overall and per symbol."""
    per_year = research.BARS_PER_YEAR.get(config.interval, 252)
    allocation = config.cash / len(data)
    hold_curves = [_hold_curve(d, config, allocation) for d in data]
    hold = (
        pd.concat(hold_curves, axis=1)
        .sort_index()
        .ffill()
        .fillna(allocation)
        .sum(axis=1)
    )
    signals_curve = _portfolio(signal_sims, config.cash)
    signal_trades = [t for sim in signal_sims for t in sim.trades]
    result: dict[str, Any] = {
        "period": {
            "train_start": stamp(data[0].frame.index[data[0].train_first]),
            "start": stamp(min(d.frame.index[d.first] for d in data)),
            "end": stamp(max(d.frame.index[-1] for d in data)),
            "bars": max(d.test_bars for d in data),
        },
        "config": {
            "symbols": [d.code for d in data],
            "script": config.script or "editör scripti",
            "mode": config.mode,
            "review_every": config.review_every,
            "interval": config.interval,
            "cash": config.cash,
            "stop_loss_pct": config.stop_loss_pct,
            "inputs": config.inputs,
        },
        "signals": {
            **metrics(
                signals_curve,
                signal_trades,
                per_year,
                _exposure(signal_sims),
                config.cash,
            ),
            "equity": _points(signals_curve),
        },
        "hold": {
            **metrics(hold, [], per_year, 1.0, config.cash),
            "equity": _points(hold),
        },
        "signal_trades": signal_trades,
    }
    if benchmark is not None and not benchmark.empty:
        start = min(d.frame.index[d.first] for d in data)
        index_close = benchmark["Close"][benchmark.index >= start]
        # On the portfolio's dates, so every curve shares one time axis.
        index_close = index_close.reindex(hold.index, method="ffill").dropna()
        if len(index_close) > 1:
            scaled = index_close / index_close.iloc[0] * config.cash
            result["benchmark"] = {
                "symbol": research.BENCHMARK,
                "return_pct": number(
                    (index_close.iloc[-1] / index_close.iloc[0] - 1) * 100, 2
                ),
                "equity": _points(scaled),
            }
    symbols = []
    for k, d in enumerate(data):
        row = {
            "symbol": d.code,
            "hold_return_pct": number(
                (hold_curves[k].iloc[-1] / allocation - 1) * 100, 2
            ),
            "signals_return_pct": number(
                (signal_sims[k].equity[-1][1] / allocation - 1) * 100, 2
            ),
            "signals_trades": len(signal_sims[k].trades),
        }
        if ai_sims:
            row["ai_return_pct"] = number(
                (ai_sims[k].equity[-1][1] / allocation - 1) * 100, 2
            )
            row["ai_trades"] = len(ai_sims[k].trades)
        symbols.append(row)
    result["symbols"] = symbols
    if ai_sims:
        curve = _portfolio(ai_sims, config.cash)
        trades = [t for sim in ai_sims for t in sim.trades]
        decisions = [d for sim in ai_sims for d in sim.decisions]
        decisions.sort(key=lambda d: (d["time"], d["symbol"]))
        live = [d for d in decisions if not d["cached"] and not d["error"]]
        audit = {
            key: sum(sim.audit[key] for sim in ai_sims)
            for key in ("decisions", "rechecks", "mismatches", "leaks")
        }
        result["ai"] = {
            **metrics(curve, trades, per_year, _exposure(ai_sims), config.cash),
            "equity": _points(curve),
            "decisions": len(decisions),
            "buys": sum(d["action"] == "buy" for d in decisions),
            "sells": sum(d["action"] == "sell" for d in decisions),
            "holds": sum(d["action"] == "hold" for d in decisions),
            "errors": sum(bool(d["error"]) for d in decisions),
            "cached": sum(d["cached"] for d in decisions),
            "avg_latency_ms": round(sum(d["latency_ms"] for d in live) / len(live))
            if live
            else None,
            "cost_usd": number(sum(d["cost"] for d in decisions), 4),
            "model": model,
        }
        result["trades"] = trades
        for decision in decisions:
            decision.pop("bar", None)
        result["decisions"] = decisions
        result["audit"] = {
            **audit,
            "blind": audit["mismatches"] == 0 and audit["leaks"] == 0,
            "notes": [
                "Her kararda model yalnızca karar barına kadar kesilmiş veriyi gördü;"
                " emir bir sonraki barın açılışında gerçekleşti.",
                "Strateji sinyali her kararda kesilmiş veri üzerinde yeniden"
                f" hesaplandı ({audit['rechecks']} kez); tam veriyle farklı çıkan"
                f" sinyal sayısı: {audit['mismatches']}.",
                "Modele hisse adı, tarih ve fiyat seviyesi gönderilmedi (fiyatlar"
                f" yüzde ve 100 tabanlı); kontrolde yakalanan sızıntı: {audit['leaks']}.",
                "Eğitim istatistikleri yalnızca test başlangıcından önceki barlardan"
                " hesaplandı.",
            ],
        }
    return result


def estimate(config: BlindConfig, data: list[Data]) -> dict[str, Any]:
    """Count the signal events in the test period: the decisions to expect.

    Entry signals while already holding and exit signals while flat need no
    decision, so the count of entries plus exits is an upper bound.
    """
    events = 0
    rows = []
    for d in data:
        n = len(d.frame)
        entries = _edges(d.result.entries, n)[d.first : n - 1]
        exits = _edges(d.result.exits, n)[d.first : n - 1]
        reviews = d.test_bars // config.review_every if config.review_every else 0
        count = int(entries.sum() + exits.sum()) + reviews
        events += count
        rows.append(
            {
                "symbol": d.code,
                "entries": int(entries.sum()),
                "exits": int(exits.sum()),
                "reviews": reviews,
                "bars": d.test_bars,
            }
        )
    return {"decisions": min(events, MAX_DECISIONS * len(data)), "symbols": rows}


def training_trades(script: Script, data: Data, config: BlindConfig) -> list[dict]:
    """Trade the script's signals, without the AI, on the bars before the test."""
    if data.first - data.train_first < 2:
        return []
    cut = data.frame.iloc[: data.first]
    result = script.run(cut, config.inputs, symbol=data.code, interval=config.interval)
    past = Data(data.code, cut, data.train_first, data.train_first, result, {})
    return Simulation(past, config, config.cash, script=script).run().trades


def strategy_context(
    script: Script, data: list[Data], config: BlindConfig, decider: Decider
) -> dict[str, Any]:
    """Return the strategy's summary and its record before the test, for views."""
    trades = [t for d in data for t in training_trades(script, d, config)]
    return {
        "strateji_ozeti": learn.strategy_brief(decider, script.source),
        "strateji_gecmisi": learn.track_record(trades),
    }


# --- Walk-forward windows ---------------------------------------------------------


def windows(data: list[Data], rounds: int) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Split the test dates into ``rounds`` consecutive, nearly equal windows."""
    dates = sorted(set().union(*(set(d.frame.index[d.first :]) for d in data)))
    rounds = max(1, min(rounds, len(dates)))
    bounds = [round(k * len(dates) / rounds) for k in range(rounds + 1)]
    return [(dates[a], dates[b - 1]) for a, b in zip(bounds, bounds[1:])]


def _window_data(
    d: Data, start: pd.Timestamp, end: pd.Timestamp, script: Script, config: BlindConfig
) -> Data:
    """Cut a symbol at a window's end and run the window's script on it."""
    frame = d.frame[d.frame.index <= end]
    first = int(np.searchsorted(pd.DatetimeIndex(frame.index), start))
    result = script.run(frame, config.inputs, symbol=d.code, interval=config.interval)
    stats = research.signal_stats(
        research.indicators(frame.iloc[:first]), d.train_first
    )
    return Data(
        d.code, frame, first, d.train_first, result, {s["key"]: s for s in stats}
    )


@dataclass
class _Joined:
    """One symbol's windows put back together, for ``summarize``."""

    data: Data
    allocation: float
    equity: list[tuple[Any, float]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    exposure: int = 0
    audit: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("decisions", "rechecks", "mismatches", "leaks"), 0
        )
    )

    def add(self, sim: Simulation) -> None:
        """Append a window's simulation."""
        self.equity += sim.equity
        self.trades += sim.trades
        self.decisions += sim.decisions
        self.exposure += sim.exposure
        for key in self.audit:
            self.audit[key] += sim.audit[key]


def _window_metrics(
    sims: list[Simulation], config: BlindConfig
) -> dict[str, Any] | None:
    if not sims:
        return None
    per_year = research.BARS_PER_YEAR.get(config.interval, 252)
    initial = sum(sim.allocation for sim in sims)
    trades = [t for sim in sims for t in sim.trades]
    found = metrics(
        _portfolio(sims, initial), trades, per_year, _exposure(sims), initial
    )
    keep = ("return_pct", "trades", "win_rate_pct", "avg_trade_pct")
    return {key: found[key] for key in (*keep, "max_drawdown_pct", "exposure_pct")}


def _move_pct(frame: pd.DataFrame, first: int) -> float | None:
    start = float(frame["Open"].iloc[first])
    return (float(frame["Close"].iloc[-1]) / start - 1) * 100 if start else None


def _round_row(
    round_no: int,
    window: tuple[pd.Timestamp, pd.Timestamp],
    script: Script,
    wdata: list[Data],
    signals: list[Simulation],
    ai: list[Simulation] | None,
    benchmark: pd.DataFrame | None,
    config: BlindConfig,
) -> dict[str, Any]:
    """Describe one window: its dates, script and results."""
    start, end = window
    moves = [
        m
        for d in wdata
        if d.first < len(d.frame) and (m := _move_pct(d.frame, d.first)) is not None
    ]
    row: dict[str, Any] = {
        "round": round_no,
        "start": stamp(start),
        "end": stamp(end),
        "bars": max(d.test_bars for d in wdata),
        "script": script.name or "editör",
        "ai": _window_metrics(ai or [], config),
        "signals": _window_metrics(signals, config),
        "hold_return_pct": number(sum(moves) / len(moves), 2) if moves else None,
        "benchmark_return_pct": None,
        "symbols": [],
        "revision": None,
    }
    if benchmark is not None and not benchmark.empty:
        index = benchmark["Close"][
            (benchmark.index >= start) & (benchmark.index <= end)
        ]
        if len(index) > 1:
            row["benchmark_return_pct"] = number(
                (index.iloc[-1] / index.iloc[0] - 1) * 100, 2
            )

    def change(sim: Simulation) -> float | None:
        if not sim.equity or not sim.allocation:
            return None
        return number((sim.equity[-1][1] / sim.allocation - 1) * 100, 2)

    for k, d in enumerate(wdata):
        item = {
            "symbol": d.code,
            "signals_trades": len(signals[k].trades),
            "signals_return_pct": change(signals[k]),
            "hold_return_pct": number(_move_pct(d.frame, d.first), 2)
            if d.first < len(d.frame)
            else None,
        }
        if ai:
            item["ai_trades"] = len(ai[k].trades)
            item["ai_return_pct"] = change(ai[k])
        row["symbols"].append(item)
    return row


def _round_report(
    row: dict[str, Any], letters: dict[str, str], learner: Learner | None, end: Any
) -> dict[str, Any]:
    """Return a window's results for the script author: letters, no dates."""
    report = {
        key: row[key]
        for key in ("round", "bars", "ai", "signals", "hold_return_pct")
        if row.get(key) is not None
    }
    report["benchmark_return_pct"] = row.get("benchmark_return_pct")
    report["symbols"] = [
        {
            **{k: v for k, v in item.items() if k != "symbol"},
            "hisse": letters[item["symbol"]],
        }
        for item in row["symbols"]
    ]
    if learner:
        journal = learner.journal.view(end) or {}
        report["karar_gunlugu"] = journal.get("ozet")
        report["dersler"] = list(learner.journal.lessons)
    return report


def _pct(value: Any) -> str:
    return "—" if value is None else f"%{value}"


def report_text(report: dict[str, Any]) -> str:
    """Write a window's anonymous report as text for the script author."""
    lines = [f"Pencere {report['round']}: {report.get('bars')} bar."]
    for key, label in (("ai", "Yapay zeka ile"), ("signals", "Sadece sinyaller")):
        found = report.get(key)
        if found:
            lines.append(
                f"{label}: getiri {_pct(found.get('return_pct'))},"
                f" {found.get('trades')} işlem,"
                f" kazançlı {_pct(found.get('win_rate_pct'))},"
                f" en büyük düşüş {_pct(found.get('max_drawdown_pct'))},"
                f" piyasada kalma {_pct(found.get('exposure_pct'))}."
            )
    lines.append(
        f"Al-tut (hisse ortalaması): {_pct(report.get('hold_return_pct'))}"
        f" · XU100: {_pct(report.get('benchmark_return_pct'))}."
    )
    lines.append("Hisseler:")
    for item in report.get("symbols") or []:
        ai = (
            f"yapay zeka {_pct(item.get('ai_return_pct'))}"
            f" ({item.get('ai_trades')} işlem), "
            if "ai_trades" in item
            else ""
        )
        lines.append(
            f"- {item['hisse']}: {ai}sinyaller {_pct(item.get('signals_return_pct'))}"
            f" ({item.get('signals_trades')} işlem),"
            f" al-tut {_pct(item.get('hold_return_pct'))}"
        )
    if report.get("karar_gunlugu"):
        summary = json.dumps(report["karar_gunlugu"], ensure_ascii=False)
        lines.append(f"Karar günlüğü özeti: {summary}")
    if report.get("dersler"):
        lines.append("Karar katmanının ders notları:")
        lines += [f"- {lesson}" for lesson in report["dersler"]]
    return "\n".join(lines)


def revised_name(base: str | None, round_no: int) -> str:
    """Name the script revised for window ``round_no``: ``<base>_t<round_no>``."""
    base = "editor" if base in (None, "", "editör") else base
    stem = re.sub(r"_t\d+$", "", base).lower()
    stem = re.sub(r"[^a-z0-9_-]", "_", stem).strip("_-") or "script"
    suffix = f"_t{round_no}"
    return stem[: 48 - len(suffix)] + suffix


def reviser(
    config: BlindConfig,
    coach: learn.Coach,
    *,
    emit: Callable[[dict[str, Any]], None] = lambda event: None,
    cancelled: Callable[[], bool] = lambda: False,
) -> Callable[..., tuple[Script | None, dict[str, Any]]]:
    """Return a ``revise`` hook for ``run_blind`` that asks the script author."""

    def revise(round_no: int, script: Script, report: dict, cutoff: date):
        name = revised_name(script.name, round_no + 1)

        def forward(event: dict[str, Any]) -> None:
            if event.get("type") == "review" and event.get("problem"):
                emit({"type": "revision_review", "round": round_no,
                      "attempt": event["attempt"], "problem": event["problem"]})  # fmt: skip

        try:
            written = author.revise_script(
                script.source,
                report_text(report),
                config.symbols,
                cutoff=cutoff.isoformat(),
                years=config.years,
                api_key=coach.api_key,
                model=coach.model,
                emit=forward,
                name=name,
                cancelled=cancelled,
                interval=config.interval,
                stream=coach.stream,
            )
        except author.Cancelled:
            raise Cancelled from None
        except (ValueError, OpenRouterError, ScriptError) as error:
            return None, {"problem": str(error)}
        info = {
            "explanation": written["explanation"],
            "attempts": written["attempts"],
            "cost_usd": number((written["usage"] or {}).get("cost", 0.0), 4),
        }
        return compile_script(written["source"], name), info

    return revise


def run_blind(
    config: BlindConfig,
    decider: Decider | None = None,
    *,
    emit: Callable[[dict[str, Any]], None] = lambda event: None,
    cancelled: Callable[[], bool] = lambda: False,
    model: dict[str, Any] | None = None,
    coach: learn.Coach | None = None,
    revise: Callable[..., tuple[Script, dict[str, Any]] | None] | None = None,
) -> dict[str, Any]:
    """Run the blind test and return the results (see ``summarize``).

    With learning on, ``coach`` writes the lessons; without one the journal
    alone is shown to the decisions. With ``config.rounds`` above 1 the test
    runs window by window; after each window but the last,
    ``revise(round_no, script, report, cutoff)`` may return a new script (and
    a note about it), which trades from the next window on. It sees the
    window's report and data up to ``cutoff``, the window's last day.
    """
    config.check()
    if config.mode == "ai" and decider is None:
        raise ValueError("Yapay zeka modu için karar modeli gerekli.")
    script = _script(config.script, config.source)
    emit({"type": "stage", "stage": "loading", "message": "Veriler yükleniyor"})
    data, benchmark = load(config, script)
    allocation = config.cash / len(data)
    parts = windows(data, config.rounds)
    walk = _Rounds(config, data, benchmark, decider, emit, cancelled)
    if config.mode == "ai":
        plan = estimate(config, data)
        emit({"type": "plan", **plan})
        if config.learning != "off":
            letters = {d.code: research.letter(k) for k, d in enumerate(data)}
            walk.learner = Learner(learn.Journal(letters), [], coach=coach, emit=emit)
    rows = walk.run(script, parts, allocation, revise)
    emit({"type": "stage", "stage": "summary", "message": "Sonuçlar hesaplanıyor"})
    signal_sims = list(walk.signals.values())
    ai_sims = list(walk.ai.values()) if config.mode == "ai" else None
    result = summarize(config, data, benchmark, signal_sims, ai_sims, model)
    if walk.strategy:
        result["ai"]["strategy"] = walk.strategy
    if len(parts) > 1:
        result["rounds"] = rows
        first = [Simulation(d, config, allocation, script=script).run() for d in data]
        result["initial_signals"] = _window_metrics(first, config)
    if len(parts) > 1 and "audit" in result:
        result["audit"]["notes"].append(
            "Script revizyonları yalnızca pencere sonuna kadarki veriyi gördü ve"
            " yalnızca sonraki pencerede kullanıldı; pencere sonunda açık"
            " pozisyonlar kapatıldı."
        )
    if walk.learner:
        _report_learning(result, config, walk.learner.journal)
    return result


class _Rounds:
    """Run the windows of a blind test, carrying cash, journal and script."""

    def __init__(self, config, data, benchmark, decider, emit, cancelled):
        self.config = config
        self.data = data
        self.benchmark = benchmark
        self.decider = decider
        self.emit = emit
        self.cancelled = cancelled
        self.learner: Learner | None = None
        self.strategy: dict[str, Any] | None = None
        self.signals = {d.code: _Joined(d, config.cash / len(data)) for d in data}
        self.ai = {d.code: _Joined(d, config.cash / len(data)) for d in data}

    def _on_decision(self, record: dict[str, Any]) -> None:
        self.emit(
            {
                "type": "decision",
                "decision": {k: v for k, v in record.items() if k != "bar"},
            }
        )

    def run(self, script, parts, allocation, revise) -> list[dict[str, Any]]:
        """Walk every window; return a row per window."""
        config, codes = self.config, [d.code for d in self.data]
        cash = {"signals": dict.fromkeys(codes, allocation)}
        cash["ai"] = dict(cash["signals"])
        letters = {d.code: research.letter(k) for k, d in enumerate(self.data)}
        rows = []
        for k, window in enumerate(parts, 1):
            last = k == len(parts)
            if len(parts) > 1:
                self.emit({"type": "round", "round": k, "rounds": len(parts),
                           "start": stamp(window[0]), "end": stamp(window[1]),
                           "script": script.name or "editör"})  # fmt: skip
                wdata = [_window_data(d, *window, script, config) for d in self.data]
            else:
                wdata = self.data
            reason = END if last else ROUND_END
            signals = [
                Simulation(
                    w,
                    config,
                    cash["signals"][w.code],
                    self.benchmark,
                    script=script,
                    round_no=k,
                )  # fmt: skip
                for w in wdata
            ]
            _walk(signals, cancelled=self.cancelled, reason=reason)
            ai = self._ai_window(script, wdata, cash["ai"], k, signals, reason)
            for name, sims in (("signals", signals), ("ai", ai or [])):
                for sim in sims:
                    if (
                        sim.equity
                    ):  # A symbol without bars in the window keeps its cash.
                        cash[name][sim.data.code] = sim.equity[-1][1]
                    getattr(self, name)[sim.data.code].add(sim)
            if len(parts) == 1:
                return rows
            row = _round_row(
                k, window, script, wdata, signals, ai, self.benchmark, config
            )
            if not last:
                script = self._revise(script, row, letters, window[1], revise)
            rows.append(row)
            self.emit({"type": "round_end", "row": row})
        return rows

    def _ai_window(self, script, wdata, cash, k, signals, reason):
        if self.config.mode != "ai":
            return None
        self.emit(
            {"type": "stage", "stage": "strategy", "message": "Strateji özetleniyor"}
        )
        self.strategy = strategy_context(script, wdata, self.config, self.decider)
        self.emit({"type": "strategy", **self.strategy})
        self.emit(
            {"type": "stage", "stage": "deciding", "message": "Model karar veriyor"}
        )
        learner = self.learner
        if learner:
            learner.signals = {sim.data.code: sim for sim in signals}
            learner.strategy = self.strategy
        codes = [d.code for d in self.data]
        ai = [
            Simulation(
                w,
                self.config,
                cash[w.code],
                self.benchmark,
                decide=self.decider.decide,
                on_decision=self._on_decision,
                cancelled=self.cancelled,
                script=script,
                strategy=self.strategy,
                codes=codes,
                on_close=learner.closed if learner else None,
                round_no=k,
            )
            for w in wdata
        ]
        _walk(ai, cancelled=self.cancelled, learner=learner, reason=reason)
        return ai

    def _revise(self, script, row, letters, end, revise):
        if self.learner:
            self.learner.reflect(end, round=row["round"])
        if revise is None:
            return script
        self.emit(
            {"type": "stage", "stage": "revising", "message": "Script geliştiriliyor"}
        )
        report = _round_report(row, letters, self.learner, end)
        new, info = revise(row["round"], script, report, end.date()) or (None, {})
        if new is None:
            row["revision"] = {"ok": False, **info, "name": script.name or "editör"}
        else:
            script = new
            row["revision"] = {"ok": True, **info, "name": script.name or "editör"}
        self.emit({"type": "revision", "round": row["round"], **row["revision"]})
        return script


def _report_learning(
    result: dict[str, Any], config: BlindConfig, journal: learn.Journal
) -> None:
    """Add the journal's summary and its audit note to the result."""
    resolved = sum(entry.at is not None for entry in journal.entries)
    result["learning"] = {
        "mode": config.learning,
        "decisions": len(journal.entries),
        "resolved": resolved,
        "lessons": list(journal.lessons),
        "history": list(journal.history),
        "reflections": journal.reflections,
        "unusable_reflections": journal.unusable,
        "cost_usd": number(journal.cost, 4),
    }
    result["audit"]["notes"].append(
        "Karar günlüğündeki her sonuç yalnızca gerçekleştiği bardan sonraki"
        f" kararlara gösterildi ({resolved} sonuç); hisseler harfle anıldı."
    )


def config_from(body: dict[str, Any]) -> BlindConfig:
    """Build a config from request fields (dates as ISO text)."""
    start = _parse_date(body.get("start"))
    end = _parse_date(body.get("end")) or today()
    if start is None:
        raise ValueError("Test başlangıç tarihi gerekli.")
    costs = body.get("costs") or BistCosts()
    return BlindConfig(
        symbols=list(body.get("symbols") or []),
        start=start,
        end=end,
        script=body.get("script"),
        source=body.get("source"),
        inputs=dict(body.get("inputs") or {}),
        mode=body.get("mode") or "ai",
        review_every=int(body.get("review_every") or 0),
        years=float(body.get("years") or 2),
        interval=body.get("interval") or "1d",
        cash=float(body.get("cash") or 100_000),
        costs=costs,
        slippage=float(body.get("slippage", 0.001)),
        stop_loss_pct=body.get("stop_loss_pct") or None,
        learning=body.get("learning") or "off",
        rounds=int(body.get("rounds") or 1),
    )


def decide_now(
    symbols: list[str],
    script: Script,
    decider: Decider,
    *,
    inputs: dict[str, Any] | None = None,
    holdings: dict[str, dict[str, Any]] | None = None,
    years: float = 2.0,
    interval: str = "1d",
    lessons: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Ask for a decision on each symbol's latest bar, for paper trading.

    ``holdings`` maps a symbol to ``{"qty", "avg_cost", "since"}`` from the
    paper account; a held symbol gets "SAT/TUT", the others "AL/BEKLE".
    ``lessons`` are the latest blind test's lessons for this script; the
    views also carry the strategy's summary and its record over the period.
    """
    codes = research._codes(symbols)
    end = today()
    start = end - timedelta(days=round(years * 365.25))
    frames, benchmark = research.load_study_frames(codes, start, end, interval)
    holdings = holdings or {}
    past = []
    for code in codes:
        frame, shown = frames[code]
        first = (
            int(np.searchsorted(pd.DatetimeIndex(frame.index), shown))
            if shown is not None
            else 0
        )
        result = script.run(frame, inputs, symbol=code, interval=interval)
        past.append(Data(code, frame, len(frame), first, result, {}))
    config = BlindConfig(
        symbols=codes, start=start, end=end, inputs=dict(inputs or {}),
        source=script.source, interval=interval,
    )  # fmt: skip
    strategy = strategy_context(script, past, config, decider)
    journal = {"dersler": list(lessons)} if lessons else None

    def one(code: str) -> dict[str, Any]:
        frame, shown = frames[code]
        index = pd.DatetimeIndex(frame.index)
        first = int(np.searchsorted(index, shown)) if shown is not None else 0
        result = script.run(frame, inputs, symbol=code, interval=interval)
        if result.entries is None:
            raise ValueError("Seçilen script bir strateji değil.")
        stats = research.signal_stats(research.indicators(frame), first)
        n = len(frame)
        held = holdings.get(code)
        close = float(frame["Close"].iloc[-1])
        if held and _edges(result.exits, n)[-1]:
            event = "exit"
        elif not held and _edges(result.entries, n)[-1]:
            event = "entry"
        else:
            event = "review"
        position = None
        if held:
            since = held.get("since")
            later = frame.iloc[-1:]
            if since is not None:
                day = pd.Timestamp(since).date()
                mask = np.array([t.date() >= day for t in index])
                later = frame[mask] if mask.any() else later
            position = position_view(
                float(held["avg_cost"]),
                close,
                len(later),
                float(later["High"].max()),
            )
        view = snapshot(
            frame,
            benchmark=benchmark,
            script=script_view(result, close, EVENTS[event]),
            position=position,
            training={s["key"]: s for s in stats},
            strategy=strategy,
            journal=journal,
        )
        decision = decider.decide(view)
        return {
            "symbol": code,
            "time": stamp(frame.index[-1]),
            "close": number(close),
            "event": EVENTS[event],
            "held": int(held["qty"]) if held else 0,
            "signals": [item["aciklama"] for item in view["aktif_sinyaller"]],
            **decision.to_dict(),
        }

    with ThreadPoolExecutor(max_workers=min(4, len(codes))) as pool:
        return list(pool.map(one, codes))
