"""Crypto lab: every non-AI strategy, yearly windows, hourly windows and scalping.

    uv run python scripts/crypto/crypto_lab.py daily|hourly|scalp|rotation [OUT.jsonl]

Prices are fetched once per symbol and interval and sliced per request. Results
are reported from a 100,000 $ start (the engine runs with large cash so whole
coin units fit; returns are the same).
"""

import importlib.util
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("market_experiment", ROOT / "scripts" / "market_experiment.py")
mx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mx)

from marketalyzer import blind, research, rotation, services  # noqa: E402
from marketalyzer.ai import learn  # noqa: E402
from marketalyzer.backtest.costs import BistCosts  # noqa: E402

M = mx.use("crypto")
START_MONEY = 100_000
CASH = 100_000_000
STRATEGIES = ["supertrend", "supertrend_sik", "sma_cross", "macd_trend", "coklu_trend",
              "donchian_breakout", "rsi_reversion", "bollinger_reversion"]
blind.BLIND_INTERVALS = ("1d", "1W", "1h", "15m", "5m")
research.BARS_PER_YEAR.update({"15m": 365 * 96, "5m": 365 * 288})
learn.HORIZONS.update({"15m": 16, "5m": 24})

_frames: dict = {}
_original = services.load_ohlcv


def _memo(code, first, end, interval, adjust="splits_only", cache=False):
    key = (code, interval)
    if key not in _frames:
        if interval == "1d":
            wide = _original(code, date(2018, 1, 1), date(2026, 10, 2), interval, adjust, cache=False)
        else:
            limit = services.INTRADAY_LOOKBACK_DAYS.get(interval, 729)
            wide = _original(code, services.today() - timedelta(days=limit), date(2026, 10, 2),
                             interval, adjust, cache=False)
        _frames[key] = wide
    frame = _frames[key]
    days = pd.DatetimeIndex(frame.index).normalize()
    return frame[(days >= pd.Timestamp(first)) & (days <= pd.Timestamp(end))].copy()


services.load_ohlcv = _memo


def eligible(codes, start, years, interval):
    """Keep the coins with enough history before ``start`` for training."""
    keep = []
    need = start - timedelta(days=round(years * 365.25 * 0.8))
    for code in codes:
        try:
            frame, _ = services.load_bars(code, need - timedelta(days=5), start, interval)
        except Exception:  # noqa: BLE001
            continue
        if len(frame) and pd.Timestamp(frame.index[0]).date() <= need + timedelta(days=3):
            keep.append(code)
    return keep


def money(pct):
    return None if pct is None else round(START_MONEY * (1 + pct / 100))


def run(script, codes, start, end, years, interval, mode="signals", entries="ask",
        costs=None, slippage=0.001, inputs=None, source=None):
    config = blind.BlindConfig(
        symbols=codes, start=start, end=end, years=years, interval=interval,
        script=None if source else script, source=source, inputs=inputs or {}, mode=mode,
        entries=entries, cash=CASH, costs=costs or M["costs"], slippage=slippage)
    r = blind.run_blind(config)
    main = r.get("ai") or r["signals"]
    return {
        "script": script, "mode": mode if mode == "signals" else f"{mode}_{entries}",
        "interval": interval, "start": str(start), "end": str(end), "coins": len(codes),
        "return": main["return_pct"], "money_end": money(main["return_pct"]),
        "dd": main["max_drawdown_pct"], "sharpe": main["sharpe"], "trades": main["trades"],
        "win": main["win_rate_pct"], "exposure": main["exposure_pct"],
        "signals": r["signals"]["return_pct"], "hold": r["hold"]["return_pct"],
        "hold_money": money(r["hold"]["return_pct"]), "hold_dd": r["hold"]["max_drawdown_pct"],
        "btc": (r.get("benchmark") or {}).get("return_pct"),
    }


def write(out, row):
    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    out.flush()
    print(f"{row.get('window', ''):6} {row['script']:20} {row['mode']:12} %{row['return']:>8} → {row['money_end']:>9,} $"
          f" · düşüş %{row['dd']} · {row['trades']} işlem | al-tut %{row['hold']} · BTC %{row['btc']}", flush=True)


YEARS = {f"Y{y}": (date(y, 1, 1), date(y, 12, 31) if y < 2026 else date(2026, 10, 2)) for y in range(2021, 2027)}
HOURS = {"H1": (date(2025, 4, 6), date(2025, 7, 5)), "H2": (date(2025, 7, 6), date(2025, 10, 5)),
         "H3": (date(2025, 10, 6), date(2026, 1, 5)), "H4": (date(2026, 1, 6), date(2026, 4, 5)),
         "H5": (date(2026, 4, 6), date(2026, 7, 5)), "H6": (date(2026, 7, 6), date(2026, 10, 2))}


def main():
    kind = sys.argv[1]
    out = open(sys.argv[2] if len(sys.argv) > 2 else f"crypto_{kind}.jsonl", "a", encoding="utf-8")
    began = time.time()
    if kind == "daily":
        for window, (start, end) in YEARS.items():
            codes = eligible(mx.CRYPTO_15, start, 1.0, "1d")
            for script in STRATEGIES:
                for mode, entries in (("signals", "ask"), ("stats", "take"), ("stats", "ask")):
                    try:
                        row = run(script, codes, start, end, 1.0, "1d", mode, entries)
                    except Exception as error:  # noqa: BLE001
                        print(window, script, mode, "HATA", error, flush=True)
                        continue
                    write(out, {"window": window, **row})
    elif kind == "hourly":
        for window, (start, end) in HOURS.items():
            codes = eligible(mx.CRYPTO_8, start, 0.5, "1h")
            for script in STRATEGIES:
                for mode, entries in (("signals", "ask"),):
                    try:
                        row = run(script, codes, start, end, 0.5, "1h", mode, entries)
                    except Exception as error:  # noqa: BLE001
                        print(window, script, mode, "HATA", error, flush=True)
                        continue
                    write(out, {"window": window, **row})
    elif kind == "scalp":
        coins = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "DOGE-USD"]
        start, end, years = date(2026, 8, 25), date(2026, 10, 2), 0.05
        here = Path(__file__).parent
        scripts = [(s, None) for s in STRATEGIES] + [
            (name, (here / f"{name}.pine").read_text(encoding="utf-8")) for name in ("scalp_rsi2", "scalp_ema")]
        fees = {"spot %0,1": (0.001, 0.0002), "düşük %0,02": (0.0002, 0.0002), "sıfır": (0.0, 0.0)}
        for interval in ("15m", "5m"):
            for fee, (rate, slip) in fees.items():
                for name, source in scripts:
                    try:
                        row = run(name, coins, start, end, years, interval, costs=BistCosts(rate, 0.0),
                                  slippage=slip, source=source)
                    except Exception as error:  # noqa: BLE001
                        print(interval, fee, name, "HATA", error, flush=True)
                        continue
                    write(out, {"window": interval, "fee": fee, **row, "mode": fee})
    elif kind == "rotation":
        for window, (start, end) in YEARS.items():
            codes = eligible(mx.CRYPTO_15, start, 1.0, "1d")
            for months in (3, 6, 9):
                for top in (3, 5):
                    r = rotation.run_rotation(rotation.RotationConfig(
                        symbols=codes, start=start, end=end, lookback_months=months, top=top,
                        cash=CASH, costs=M["costs"], slippage=M["slippage"]))
                    rot = r["rotation"]
                    row = {"window": window, "script": f"rotasyon {months}a/{top}", "mode": "rotation",
                           "return": rot["return_pct"], "money_end": money(rot["return_pct"]),
                           "dd": rot["max_drawdown_pct"], "trades": rot.get("trades"),
                           "hold": r["equal"]["return_pct"], "btc": (r.get("benchmark") or {}).get("return_pct")}
                    write(out, row)
    print(f"bitti: {time.time() - began:.0f} sn")


if __name__ == "__main__":
    main()
