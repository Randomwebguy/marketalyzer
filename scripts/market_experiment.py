"""Run the blind-test benchmark on US stocks or crypto (an experiment, not a feature).

    uv run python scripts/market_experiment.py us|crypto ARM PERIOD [PERIOD ...]

ARM is ``stats_ask`` or ``stats_take`` (statistics filter judging entries too, or
taking every entry), ``sonnet_take`` (Claude Sonnet 5.5 through the Claude Code
command line judging exits; see ``marketalyzer.ai.claude_cli``) or ``rotation``
(6- and 9-month momentum, top 5; daily periods only). PERIOD is A (2024-10 to
2025-10, daily), B (2025-10 to 2026-10, daily) or S (the last three months,
hourly, another basket). Every blind-test arm also reports the signals alone,
hold and the benchmark.

The product is built for Borsa Istanbul. This script only patches, inside its
own process, what is specific to it: Yahoo symbols (no ".IS"), the benchmark
(SPY or BTC-USD), costs (no commission on US stocks; 0.1 % per side on crypto),
bars per year, the reference stocks for breadth and the evidence pool, and the
market named in the decision prompt. The cash is large so whole units of
expensive coins still fit each symbol's share.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date

from marketalyzer import blind, research, rotation
from marketalyzer.ai import claude_cli, context, decide, runs
from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper import account
from openbb_bist.utils import symbols

US_20 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "V", "JNJ",
         "XOM", "PG", "UNH", "HD", "KO", "PEP", "CVX", "WMT", "BAC", "DIS"]  # fmt: skip
US_8 = ["CAT", "NKE", "MCD", "IBM", "ORCL", "CSCO", "GS", "BA"]
CRYPTO_15 = ["BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD",
             "DOGE-USD", "TRX-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD",
             "BCH-USD", "XLM-USD", "ATOM-USD"]  # fmt: skip
CRYPTO_8 = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD",
            "ADA-USD", "LINK-USD"]  # fmt: skip
A = (date(2024, 10, 2), date(2025, 10, 1), 1.0, "1d")
B = (date(2025, 10, 2), date(2026, 10, 2), 1.0, "1d")
S = (date(2026, 7, 3), date(2026, 10, 2), 0.5, "1h")
MARKETS = {
    "us": {
        "name": "ABD hisseleri",
        "benchmark": "SPY",
        "reference": US_20,
        "costs": BistCosts(commission_rate=0.0, bsmv_rate=0.0),
        "slippage": 0.0005,
        "cash": 10_000_000,
        "bars": {"1d": 252, "1h": 252 * 7},
        "cost_pct": 0.05,
        "periods": {"A": (US_20, *A), "B": (US_20, *B), "S": (US_8, *S)},
    },
    "crypto": {
        "name": "kripto paralar",
        "benchmark": "BTC-USD",
        "reference": CRYPTO_15,
        "costs": BistCosts(commission_rate=0.001, bsmv_rate=0.0),
        "slippage": 0.001,
        "cash": 100_000_000,
        "bars": {"1d": 365, "1h": 365 * 24},
        "cost_pct": 0.3,
        "periods": {"A": (CRYPTO_15, *A), "B": (CRYPTO_15, *B), "S": (CRYPTO_8, *S)},
    },
}


def use(market: str) -> dict:
    """Patch this process for ``market``; return its settings."""
    m = MARKETS[market]
    foreign = {m["benchmark"], *m["reference"]} | {
        code for period in m["periods"].values() for code in period[0]
    }
    original = symbols.to_yahoo_symbol

    def to_yahoo(symbol: str) -> str:
        code = symbol.strip().upper()
        return code if code in foreign else original(symbol)

    symbols.to_yahoo_symbol = to_yahoo
    account.to_yahoo_symbol = to_yahoo
    research.BENCHMARK = m["benchmark"]
    research.MAX_SYMBOLS = 30
    research.BARS_PER_YEAR.update(m["bars"])
    blind.LARGE_CAPS = m["reference"]
    context.LARGE_CAPS = m["reference"]
    if market == "crypto":
        rotation.MONTH = 30  # bars in a month: crypto trades every day
    decide.SYSTEM = decide.SYSTEM.replace(
        "Borsa İstanbul hisseleri", m["name"]
    ).replace(f"yaklaşık %{decide.ROUND_TRIP_COST_PCT}", f"yaklaşık %{m['cost_pct']}")
    return m


def blind_config(m: dict, period: str, **extra) -> blind.BlindConfig:
    """Return the blind test of ``period`` with the market's cash and costs."""
    codes, start, end, years, interval = m["periods"][period]
    return blind.BlindConfig(
        symbols=codes, start=start, end=end, years=years, interval=interval,
        script="supertrend_sik", cash=m["cash"], costs=m["costs"],
        slippage=m["slippage"], **extra,
    )  # fmt: skip


def brief(result: dict) -> dict:
    """Return the figures to compare arms by."""
    ai = result.get("ai") or {}
    signals = result["signals"]
    return {
        "ai": ai.get("return_pct"),
        "dd": ai.get("max_drawdown_pct"),
        "sharpe": ai.get("sharpe"),
        "trades": ai.get("trades"),
        "signals": signals["return_pct"],
        "signals_sharpe": signals["sharpe"],
        "hold": result["hold"]["return_pct"],
        "hold_dd": result["hold"]["max_drawdown_pct"],
        "bench": (result.get("benchmark") or {}).get("return_pct"),
        "beats_random": (result.get("comparison") or {}).get("beats_random_pct"),
        "exits": (result.get("comparison") or {}).get("exits"),
    }


def main(argv: list[str]) -> None:
    """Run one arm on one market over the given periods; print one line each."""
    market, arm, periods = argv[0], argv[1], argv[2:]
    m = use(market)
    for period in periods:
        began = time.time()
        if arm == "rotation":
            codes, start, end, _, _ = m["periods"][period]
            out = {}
            for months in (6, 9):
                r = rotation.run_rotation(
                    rotation.RotationConfig(
                        symbols=codes,
                        start=start,
                        end=end,
                        lookback_months=months,
                        top=5,
                        cash=m["cash"],
                        costs=m["costs"],
                        slippage=m["slippage"],
                    )  # fmt: skip
                )
                out[f"{months}m"] = {
                    "ret": r["rotation"]["return_pct"],
                    "dd": r["rotation"]["max_drawdown_pct"],
                    "equal": r["equal"]["return_pct"],
                    "bench": (r.get("benchmark") or {}).get("return_pct"),
                }
        elif arm.startswith("stats_"):
            entries = arm.removeprefix("stats_")
            out = brief(
                blind.run_blind(blind_config(m, period, mode="stats", entries=entries))
            )
        elif arm == "sonnet_take":
            config = blind_config(
                m, period, mode="ai", learning="journal", entries="take"
            )
            result = blind.run_blind(
                config,
                claude_cli.decider("claude-sonnet-5-5"),
                model={"id": "claude-code/claude-sonnet-5-5"},
                coach=claude_cli.coach(),
            )
            result["market"] = market
            runs.save(result)
            out = brief(result)
        else:
            raise SystemExit(f"Bilinmeyen kol: {arm}")
        line = json.dumps(out, ensure_ascii=False)
        print(market, arm, period, f"{time.time() - began:.0f} s", line, flush=True)  # noqa: T201


if __name__ == "__main__":
    main(sys.argv[1:])
