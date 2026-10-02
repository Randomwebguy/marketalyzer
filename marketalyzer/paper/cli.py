"""Command line interface: ``marketalyzer-paper``."""

import argparse
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from marketalyzer.backtest.cli import parse_pair, parse_value
from marketalyzer.backtest.costs import BistCosts
from marketalyzer.backtest.data import load_ohlcv
from marketalyzer.backtest.strategies import STRATEGIES
from marketalyzer.paper.account import PaperAccount, normalize_symbol
from marketalyzer.paper.feed import INTERVALS, FrameFeed, ProviderFeed
from marketalyzer.paper.models import Order
from marketalyzer.paper.trader import PaperTrader, StepReport, replay

SIDES = {"buy": "AL", "sell": "SAT"}
STATUSES = {
    "open": "açık",
    "filled": "gerçekleşti",
    "cancelled": "iptal",
    "expired": "süresi doldu",
    "rejected": "reddedildi",
}


def default_home() -> Path:
    """Return MARKETALYZER_HOME, or ~/.local/share/marketalyzer."""
    configured = os.environ.get("MARKETALYZER_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "share" / "marketalyzer"


def account_path(name: str) -> Path:
    """Return the file of a named paper account."""
    return default_home() / "paper" / f"{name}.sqlite"


def _money(value: float | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def _order_line(order: Order) -> str:
    price = order.limit_price or order.stop_price
    kind = order.type if price is None else f"{order.type} {price:g}"
    line = (
        f"  #{order.id:<4} {order.symbol:<7} {SIDES[order.side]:<4} {order.qty:>7}"
        f"  {kind:<14} {order.tif:<4} {STATUSES[order.status]:<13}"
        f" {order.submitted_at:%Y-%m-%d %H:%M}"
    )
    if order.fill_price is not None:
        line += f"  @ {order.fill_price:g}"
    if order.reason and order.status != "filled":
        line += f"  ({order.reason})"
    return line


def _status_text(account: PaperAccount) -> str:
    summary = account.summary()
    lines = [
        f"Hesap: {account.path}",
        f"  {'Nakit':<22} {_money(summary['cash'])} TL",
        f"  {'Özsermaye':<22} {_money(summary['equity'])} TL",
        f"  {'Getiri [%]':<22} {summary['return_pct']:.2f}",
        f"  {'Gerçekleşen K/Z':<22} {_money(summary['realized_pnl'])} TL",
        f"  {'Gerçekleşmemiş K/Z':<22} {_money(summary['unrealized_pnl'])} TL",
    ]
    positions = account.positions()
    if positions:
        lines += [
            "",
            "Pozisyonlar:",
            f"  {'Sembol':<7} {'Adet':>7} {'Ort. maliyet':>13} {'Son fiyat':>10}"
            f" {'Değer':>13} {'K/Z':>11}",
        ]
        lines += [
            f"  {p.symbol:<7} {p.qty:>7} {_money(p.avg_cost):>13}"
            f" {_money(p.last_price):>10} {_money(p.market_value):>13}"
            f" {_money(p.unrealized_pnl):>11}"
            for p in positions
        ]
    orders = account.orders(status="open")
    if orders:
        lines += ["", "Açık emirler:", *(_order_line(order) for order in orders)]
    return "\n".join(lines)


def _print_step(report: StepReport, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False), flush=True)
        return
    parts = [f"{report.time:%Y-%m-%d %H:%M}", f"{report.bars} yeni bar"]
    if report.signals:
        wants = ", ".join(
            f"{symbol}={'AL' if want else 'NAKİT'}"
            for symbol, want in report.signals.items()
        )
        parts.append(f"sinyal: {wants}")
    print(" | ".join(parts), flush=True)
    for fill in report.fills:
        print(
            f"  gerçekleşti: {SIDES[fill.side]} {fill.qty} {fill.symbol}"
            f" @ {fill.price:g} (maliyet {fill.commission:,.2f} TL)"
        )
    for order in report.orders:
        print(f"  emir: {SIDES[order.side]} {order.qty} {order.symbol} (#{order.id})")
    for order in report.expired:
        print(f"  süresi doldu: #{order.id} {order.symbol}")
    for symbol, error in report.errors.items():
        print(f"  hata ({symbol}): {error}", file=sys.stderr)


def _costs(args: argparse.Namespace) -> BistCosts:
    return BistCosts(
        commission_rate=args.commission,
        bsmv_rate=args.bsmv,
        min_commission=args.min_commission,
    )


def _add_account_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cash", type=float, default=100_000, help="Başlangıç TL")
    parser.add_argument("--commission", type=float, default=0.002)
    parser.add_argument("--bsmv", type=float, default=0.05)
    parser.add_argument("--min-commission", type=float, default=0.0)
    parser.add_argument(
        "--slippage",
        type=float,
        default=0.001,
        help="Gidiş-dönüş makas ve kayma; her işlemde yarısı uygulanır",
    )


def _add_strategy_options(parser: argparse.ArgumentParser, required: bool) -> None:
    parser.add_argument("symbols", nargs="+", metavar="SEMBOL")
    parser.add_argument(
        "-s", "--strategy", choices=sorted(STRATEGIES), required=required
    )
    parser.add_argument(
        "-p",
        "--param",
        type=parse_pair,
        action="append",
        default=[],
        metavar="AD=DEĞER",
    )
    parser.add_argument("--interval", default="5m", choices=list(INTERVALS))


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="marketalyzer-paper",
        description="BIST için sanal hesapla işlem simülasyonu (gerçek para kullanılmaz).",
    )
    parser.add_argument("--account", default="default", help="Hesap adı")
    parser.add_argument("--db", type=Path, help="Hesap dosyası (--account yerine)")
    parser.add_argument("--json", action="store_true", help="Çıktıyı JSON yaz")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Yeni sanal hesap aç")
    _add_account_options(init)
    commands.add_parser("status", help="Nakit, pozisyonlar ve açık emirler")

    for side, label in (("buy", "Alış"), ("sell", "Satış")):
        order = commands.add_parser(side, help=f"{label} emri ver")
        order.add_argument("symbol", metavar="SEMBOL")
        order.add_argument("qty", type=int, metavar="ADET")
        prices = order.add_mutually_exclusive_group()
        prices.add_argument("--limit", type=float, help="Limit fiyat")
        prices.add_argument("--stop", type=float, help="Stop fiyatı")
        order.add_argument(
            "--gtc", action="store_true", help="İptal edilene kadar geçerli"
        )

    cancel = commands.add_parser("cancel", help="Açık emri iptal et")
    cancel.add_argument("order_id", type=int, metavar="EMİR_NO")

    orders = commands.add_parser("orders", help="Emirleri listele")
    orders.add_argument("--all", action="store_true", help="Kapanmış emirler dahil")
    commands.add_parser("fills", help="Gerçekleşen işlemler")

    run = commands.add_parser("run", help="Canlı (gecikmeli) veriyle çalıştır")
    _add_strategy_options(run, required=False)
    run.add_argument("--poll", type=float, default=60, help="Adım aralığı [sn]")
    run.add_argument("--once", action="store_true", help="Tek adım çalıştır ve çık")
    run.add_argument(
        "--delay", type=float, default=15, help="Veri gecikmesi [dk] (Yahoo: 15)"
    )

    rerun = commands.add_parser("replay", help="Geçmiş günleri canlıymış gibi oynat")
    _add_strategy_options(rerun, required=True)
    rerun.add_argument("--start", required=True, help="YYYY-AA-GG")
    rerun.add_argument("--end", help="YYYY-AA-GG (varsayılan: bugün)")
    rerun.add_argument("--save", metavar="AD", help="Replay hesabını bu adla sakla")
    rerun.add_argument("--verbose", action="store_true", help="Her adımı yazdır")
    _add_account_options(rerun)
    return parser


def _open(args: argparse.Namespace) -> PaperAccount:
    path = args.db or account_path(args.account)
    if not path.exists():
        raise FileNotFoundError(
            f"No paper account at {path}. Create one with 'marketalyzer-paper init'."
        )
    return PaperAccount(path)


def _replay(args: argparse.Namespace) -> dict[str, Any]:
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    symbols = [normalize_symbol(s) for s in args.symbols]
    intraday = {
        s: load_ohlcv(s, start, end, args.interval, "splits_only") for s in symbols
    }
    daily = {s: load_ohlcv(s, start - timedelta(days=400), end) for s in symbols}
    feed = FrameFeed(intraday, daily, args.interval)

    with tempfile.TemporaryDirectory() as tmp:
        path = account_path(args.save) if args.save else Path(tmp) / "replay.sqlite"
        first = min(frame.index[0] for frame in intraday.values()).to_pydatetime()
        with PaperAccount.create(
            path, args.cash, _costs(args), args.slippage, now=first
        ) as account:
            params = {name: parse_value(value) for name, value in args.param}
            reports = replay(
                account,
                feed,
                symbols,
                args.strategy,
                params,
                on_step=(lambda r: _print_step(r, args.json)) if args.verbose else None,
            )
            hold = [
                (frame["Close"].iloc[-1] / frame["Open"].iloc[0] - 1) * 100
                for frame in intraday.values()
            ]
            return {
                "account": str(path) if args.save else None,
                "steps": len(reports),
                "fills": [fill.to_dict() for fill in account.fills()],
                "buy_hold_return_pct": round(sum(hold) / len(hold), 4),
                **account.summary(),
            }


def _emit(args: argparse.Namespace, data: Any, text: str) -> None:
    """Print ``data`` as JSON with --json, otherwise the Turkish ``text``."""
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else text)


def _init(args: argparse.Namespace) -> None:
    path = args.db or account_path(args.account)
    with PaperAccount.create(path, args.cash, _costs(args), args.slippage) as account:
        text = f"Sanal hesap açıldı: {path} ({_money(args.cash)} TL)"
        _emit(args, account.summary(), text)


def _replay_command(args: argparse.Namespace) -> None:
    result = _replay(args)
    costs = sum(fill["commission"] for fill in result["fills"])
    lines = [
        f"Replay: {result['steps']} adım, {len(result['fills'])} işlem",
        f"  {'Son özsermaye':<22} {_money(result['equity'])} TL",
        f"  {'Getiri [%]':<22} {result['return_pct']:.2f}",
        f"  {'Al ve tut getirisi [%]':<22} {result['buy_hold_return_pct']:.2f}",
        f"  {'Toplam maliyet':<22} {_money(costs)} TL",
    ]
    if result["account"]:
        lines.append(f"  Hesap kaydedildi: {result['account']}")
    _emit(args, result, "\n".join(lines))


def _status(args: argparse.Namespace, account: PaperAccount) -> None:
    _emit(args, account.summary(), _status_text(account))


def _order(args: argparse.Namespace, account: PaperAccount) -> None:
    order = account.submit(
        args.symbol,
        args.command,
        args.qty,
        type="limit" if args.limit else "stop" if args.stop else "market",
        limit_price=args.limit,
        stop_price=args.stop,
        tif="gtc" if args.gtc else "day",
        tag="manual",
    )
    _emit(args, order.to_dict(), "Emir alındı:\n" + _order_line(order))


def _cancel(args: argparse.Namespace, account: PaperAccount) -> None:
    order = account.cancel(args.order_id)
    _emit(args, order.to_dict(), "Emir iptal edildi:\n" + _order_line(order))


def _orders(args: argparse.Namespace, account: PaperAccount) -> None:
    orders = account.orders(None if args.all else "open")
    text = "\n".join(_order_line(order) for order in orders) or "Emir yok."
    _emit(args, [order.to_dict() for order in orders], text)


def _fills(args: argparse.Namespace, account: PaperAccount) -> None:
    fills = account.fills()
    text = "\n".join(
        f"  {f.time:%Y-%m-%d %H:%M} {SIDES[f.side]:<4} {f.qty:>7} {f.symbol:<7}"
        f" @ {f.price:g}  maliyet {f.commission:,.2f} TL"
        for f in fills
    )
    _emit(args, [fill.to_dict() for fill in fills], text or "İşlem yok.")


def _run(args: argparse.Namespace, account: PaperAccount) -> None:
    feed = ProviderFeed(args.interval, timedelta(minutes=args.delay))
    params = {name: parse_value(value) for name, value in args.param}
    trader = PaperTrader(account, args.symbols, feed, args.strategy, params)
    trader.run(
        poll=args.poll,
        steps=1 if args.once else None,
        on_step=lambda report: _print_step(report, args.json),
    )


ACCOUNT_COMMANDS = {
    "status": _status,
    "buy": _order,
    "sell": _order,
    "cancel": _cancel,
    "orders": _orders,
    "fills": _fills,
    "run": _run,
}


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            _init(args)
        elif args.command == "replay":
            _replay_command(args)
        else:
            with _open(args) as account:
                ACCOUNT_COMMANDS[args.command](args, account)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"Hata: {error}", file=sys.stderr)
        return 1
    return 0
