"""Command line interface: ``marketalyzer-backtest THYAO --strategy sma_cross``."""

import argparse
import json
import sys
from typing import Any

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.engine import optimize_backtest, run_backtest
from marketalyzer.backtest.strategies import STRATEGIES

INTERVALS = ["1m", "2m", "5m", "15m", "30m", "1h", "1d", "1W", "1M"]

LABELS = {
    "symbol": "Sembol",
    "strategy": "Strateji",
    "params": "Parametreler",
    "start": "Başlangıç",
    "end": "Bitiş",
    "duration_days": "Süre [gün]",
    "return_pct": "Getiri [%]",
    "buy_hold_return_pct": "Al ve tut getirisi [%]",
    "cagr_pct": "Yıllık bileşik getiri [%]",
    "volatility_ann_pct": "Yıllık oynaklık [%]",
    "sharpe": "Sharpe",
    "sortino": "Sortino",
    "max_drawdown_pct": "En büyük düşüş [%]",
    "trades": "İşlem sayısı",
    "win_rate_pct": "Kazançlı işlem oranı [%]",
    "profit_factor": "Kâr faktörü",
    "exposure_pct": "Pozisyonda kalma [%]",
    "equity_final": "Son bakiye [TL]",
    "commissions": "Toplam komisyon [TL]",
    "benchmark": "Kıyas endeksi",
    "benchmark_return_pct": "Kıyas endeksi getirisi [%]",
    "usdtry_change_pct": "USD/TRY değişimi [%]",
    "return_usd_pct": "USD bazında getiri [%]",
}


def parse_value(text: str) -> int | float | str:
    """Parse a command line value as an int, a float or, failing both, text."""
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            continue
    return text


def parse_pair(text: str) -> tuple[str, str]:
    """Parse NAME=VALUE."""
    name, sep, value = text.partition("=")
    if not sep or not name:
        raise argparse.ArgumentTypeError(f"Expected NAME=VALUE, got {text!r}.")
    return name.strip(), value.strip()


def grid_values(spec: str) -> list:
    """Parse "5:30:5" (inclusive range) or "10,20,30" into a list of values."""
    if ":" in spec:
        parts = [parse_value(part) for part in spec.split(":")]
        if len(parts) != 3 or not all(isinstance(p, (int, float)) for p in parts):
            raise argparse.ArgumentTypeError(f"Expected START:STOP:STEP, got {spec!r}.")
        start, stop, step = parts
        if step <= 0:
            raise argparse.ArgumentTypeError("STEP must be positive.")
        values = []
        value = start
        while value <= stop + 1e-9:
            values.append(value)
            value += step
        return values
    return [parse_value(part) for part in spec.split(",") if part]


def _print_summary(summary: dict[str, Any]) -> None:
    for key, value in summary.items():
        if value is None:
            continue
        text = value
        if isinstance(value, dict):
            text = ", ".join(f"{k}={v}" for k, v in value.items())
        elif isinstance(value, float):
            text = f"{value:,.2f}"
        print(f"  {LABELS.get(key, key):<28} {text}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-backtest",
        description="BIST hissesi üzerinde strateji backtest'i (gerçek para kullanılmaz).",
    )
    parser.add_argument("symbol", help="BIST kodu, ör. THYAO veya XU100")
    parser.add_argument(
        "-s", "--strategy", default="sma_cross", choices=sorted(STRATEGIES)
    )
    parser.add_argument("--start", help="YYYY-AA-GG (varsayılan: 1 yıl önce)")
    parser.add_argument("--end", help="YYYY-AA-GG (varsayılan: bugün)")
    parser.add_argument("--interval", default="1d", choices=INTERVALS)
    parser.add_argument("--cash", type=float, default=100_000, help="Başlangıç TL")
    parser.add_argument(
        "--commission",
        type=float,
        default=0.002,
        help="Komisyon oranı (0.002 = binde 2)",
    )
    parser.add_argument(
        "--bsmv", type=float, default=0.05, help="Komisyon üzerinden BSMV"
    )
    parser.add_argument(
        "--min-commission",
        type=float,
        default=0.0,
        help="Emir başına asgari komisyon TL",
    )
    parser.add_argument(
        "--slippage", type=float, default=0.001, help="Alış-satış makası ve kayma oranı"
    )
    parser.add_argument(
        "--trade-on-close",
        action="store_true",
        help="Emirleri sinyal barının kapanışından doldur",
    )
    parser.add_argument(
        "--risk-free",
        type=float,
        default=0.0,
        help="Yıllık TL risksiz faiz (Sharpe için)",
    )
    parser.add_argument(
        "-p",
        "--param",
        type=parse_pair,
        action="append",
        default=[],
        metavar="AD=DEĞER",
        help="Strateji parametresi, ör. fast=10",
    )
    parser.add_argument(
        "--optimize", action="store_true", help="Parametreleri optimize et"
    )
    parser.add_argument(
        "--grid",
        type=parse_pair,
        action="append",
        default=[],
        metavar="AD=ARALIK",
        help="Arama aralığı, ör. fast=5:30:5 veya slow=50,100",
    )
    parser.add_argument(
        "--maximize", default="Sharpe Ratio", help="Optimize edilecek ölçüt"
    )
    parser.add_argument(
        "--holdout",
        type=float,
        default=0.3,
        help="Optimizasyonda teste ayrılan son dönem oranı (0 = ayırma)",
    )
    parser.add_argument("--benchmark", default="XU100", help="Kıyas endeksi")
    parser.add_argument("--no-benchmark", action="store_true")
    parser.add_argument(
        "--no-usd", action="store_true", help="USD bazında getiri hesaplama"
    )
    parser.add_argument(
        "--plot", metavar="DOSYA.html", help="Etkileşimli grafiği kaydet"
    )
    parser.add_argument("--json", action="store_true", help="Sonucu JSON olarak yaz")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    args = build_parser().parse_args(argv)
    common: dict[str, Any] = {
        "start": args.start,
        "end": args.end,
        "interval": args.interval,
        "cash": args.cash,
        "costs": BistCosts(
            commission_rate=args.commission,
            bsmv_rate=args.bsmv,
            min_commission=args.min_commission,
        ),
        "slippage": args.slippage,
        "trade_on_close": args.trade_on_close,
        "risk_free_rate": args.risk_free,
        "benchmark": None if args.no_benchmark else args.benchmark,
        "usd": not args.no_usd,
    }
    try:
        if args.optimize:
            grid = {name: grid_values(spec) for name, spec in args.grid} or None
            result = optimize_backtest(
                args.symbol,
                args.strategy,
                param_grid=grid,
                maximize=args.maximize,
                holdout=args.holdout or None,
                **common,
            )
            summary = result.summary()
            report = result.out_of_sample or result.in_sample
        else:
            params = {name: parse_value(value) for name, value in args.param}
            report = run_backtest(args.symbol, args.strategy, params=params, **common)
            summary = report.summary()
    except Exception as error:
        print(f"Hata: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.optimize:
        print(
            "En iyi parametreler: "
            + ", ".join(f"{k}={v}" for k, v in summary["best_params"].items())
        )
        print("\nEğitim dönemi (parametreler burada seçildi):")
        _print_summary(summary["in_sample"])
        if summary["out_of_sample"]:
            print("\nTest dönemi (optimizasyonun görmediği veri):")
            _print_summary(summary["out_of_sample"])
    else:
        _print_summary(summary)

    if args.plot:
        path = report.plot(args.plot)
        print(f"\nGrafik: {path}", file=sys.stderr if args.json else sys.stdout)
    return 0
