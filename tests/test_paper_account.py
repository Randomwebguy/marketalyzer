from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper.account import OrderRejected, PaperAccount
from marketalyzer.paper.models import Bar

IST = ZoneInfo("Europe/Istanbul")
OPEN = datetime(2026, 10, 1, 10, 0, tzinfo=IST)
FREE = BistCosts(commission_rate=0, bsmv_rate=0)


def bar(minutes, o, h, low, c, day=OPEN):
    start = day + timedelta(minutes=minutes)
    return Bar(start, start + timedelta(minutes=5), o, h, low, c, 1_000)


@pytest.fixture
def make_account(tmp_path):
    accounts = []

    def _make(cash=100_000.0, costs=FREE, slippage=0.0, name="paper.sqlite"):
        account = PaperAccount.create(
            tmp_path / name, cash, costs, slippage, now=OPEN - timedelta(hours=2)
        )
        accounts.append(account)
        return account

    yield _make
    for account in accounts:
        account.close()


class TestCreate:
    def test_new_account(self, make_account):
        account = make_account(cash=50_000)
        assert account.cash == 50_000
        assert account.summary()["equity"] == 50_000
        assert account.positions() == []

    def test_settings_survive_reopening(self, make_account):
        account = make_account(costs=BistCosts(min_commission=5), slippage=0.002)
        reopened = PaperAccount(account.path)
        assert reopened.costs == BistCosts(min_commission=5)
        assert reopened.slippage == 0.002
        reopened.close()

    def test_refuses_to_overwrite(self, make_account):
        account = make_account()
        with pytest.raises(FileExistsError):
            PaperAccount.create(account.path)

    def test_missing_account(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            PaperAccount(tmp_path / "nope.sqlite")

    @pytest.mark.parametrize(
        ("cash", "slippage"), [(0, 0.001), (100, -0.1), (100, 0.5)]
    )
    def test_rejects_bad_settings(self, tmp_path, cash, slippage):
        with pytest.raises(ValueError):
            PaperAccount.create(tmp_path / "x.sqlite", cash, slippage=slippage)


class TestSubmit:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"side": "short"}, "side"),
            ({"type": "iceberg"}, "type"),
            ({"tif": "ioc"}, "tif"),
            ({"qty": 0}, "whole number"),
            ({"qty": 1.5}, "whole number"),
            ({"qty": True}, "whole number"),
            ({"limit_price": 300.0}, "Market orders"),
            ({"type": "limit"}, "positive price"),
            ({"type": "limit", "limit_price": 300.1}, "use 300.0 or 300.25"),
            ({"type": "stop", "stop_price": -1.0}, "positive price"),
        ],
    )
    def test_validation(self, make_account, kwargs, message):
        order = {"symbol": "THYAO", "side": "buy", "qty": 10, **kwargs}
        with pytest.raises(OrderRejected, match=message):
            make_account().submit(**order, now=OPEN)

    def test_normalizes_symbols(self, make_account):
        order = make_account().submit("thyao.is", "buy", 10, now=OPEN)
        assert order.symbol == "THYAO"
        assert order.status == "open"
        assert order.session_date == OPEN.date()

    def test_no_short_selling(self, make_account):
        with pytest.raises(OrderRejected, match="only 0 shares"):
            make_account().submit("THYAO", "sell", 1, now=OPEN)

    def test_sells_cannot_exceed_the_position(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, now=OPEN)
        account.process_bar("THYAO", bar(0, 300, 300, 300, 300))
        account.submit("THYAO", "sell", 6, "limit", limit_price=320.0, now=OPEN)
        with pytest.raises(OrderRejected, match="only 4 shares"):
            account.submit("THYAO", "sell", 5, now=OPEN)

    def test_buying_power_counts_open_orders(self, make_account):
        account = make_account(cash=10_000)
        account.submit("THYAO", "buy", 30, "limit", limit_price=300.0, now=OPEN)
        assert account.buying_power() == pytest.approx(1_000)
        with pytest.raises(OrderRejected, match="Insufficient buying power"):
            account.submit("GARAN", "buy", 10, "limit", limit_price=130.0, now=OPEN)

    def test_day_orders_after_the_close_wait_for_the_next_session(self, make_account):
        evening = OPEN.replace(hour=19)
        order = make_account().submit("THYAO", "buy", 1, now=evening)
        assert order.session_date is None


class TestFills:
    def test_market_order_fills_at_the_next_bar_open(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, now=OPEN + timedelta(minutes=2))
        assert account.process_bar("THYAO", bar(0, 300, 301, 299, 300.5)) == []
        (fill,) = account.process_bar("THYAO", bar(5, 300.75, 302, 300, 301))
        assert (fill.price, fill.qty, fill.time) == (
            300.75,
            10,
            OPEN + timedelta(minutes=5),
        )
        assert account.order(fill.order_id).status == "filled"

    def test_slippage_moves_fills_against_the_order(self, make_account):
        account = make_account(slippage=0.002)
        account.submit("THYAO", "buy", 10, now=OPEN)
        (buy,) = account.process_bar("THYAO", bar(0, 300, 301, 299, 300))
        assert buy.price == 300.5  # 300 * 1.001 = 300.3, rounded up to the tick
        account.submit("THYAO", "sell", 10, now=OPEN + timedelta(minutes=5))
        (sell,) = account.process_bar("THYAO", bar(5, 300, 301, 299, 300))
        assert sell.price == 299.5  # 300 * 0.999 = 299.7, rounded down

    @pytest.mark.parametrize(
        ("low", "open_", "expected"),
        [(299.5, 300.0, None), (298.0, 300.0, 299.0), (297.0, 297.5, 297.5)],
    )
    def test_limit_buy(self, make_account, low, open_, expected):
        account = make_account()
        account.submit("THYAO", "buy", 10, "limit", limit_price=299.0, now=OPEN)
        fills = account.process_bar("THYAO", bar(0, open_, 301, low, 300))
        assert (fills[0].price if fills else None) == expected

    @pytest.mark.parametrize(
        ("high", "open_", "expected"),
        [(309.75, 300.0, None), (311.0, 300.0, 310.0), (313.0, 312.5, 312.5)],
    )
    def test_limit_sell(self, make_account, high, open_, expected):
        account = make_account()
        account.submit("THYAO", "buy", 10, now=OPEN - timedelta(minutes=5))
        account.process_bar("THYAO", bar(-5, 300, 300, 300, 300))
        account.submit("THYAO", "sell", 10, "limit", limit_price=310.0, now=OPEN)
        fills = account.process_bar("THYAO", bar(0, open_, high, 299, 300))
        assert (fills[0].price if fills else None) == expected

    def test_stop_orders(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, "stop", stop_price=305.0, now=OPEN)
        assert account.process_bar("THYAO", bar(0, 300, 304.75, 299, 304)) == []
        (buy,) = account.process_bar("THYAO", bar(5, 304, 306, 303, 305.5))
        assert buy.price == 305.0
        account.submit(
            "THYAO",
            "sell",
            10,
            "stop",
            stop_price=300.0,
            now=OPEN + timedelta(minutes=10),
        )
        (sell,) = account.process_bar("THYAO", bar(10, 298.5, 299, 297, 298))
        assert sell.price == 298.5  # gapped below the stop: filled at the open

    def test_costs_cash_and_pnl(self, make_account):
        account = make_account(costs=BistCosts())
        account.submit("THYAO", "buy", 100, now=OPEN)
        account.process_bar("THYAO", bar(0, 300, 301, 299, 300))
        buy_cost = 100 * 300 * 0.002 * 1.05
        assert account.cash == pytest.approx(100_000 - 30_000 - buy_cost)
        (position,) = account.positions()
        assert position.avg_cost == pytest.approx(300 + buy_cost / 100)

        account.process_bar("THYAO", bar(5, 300, 311, 300, 310))
        assert account.equity() == pytest.approx(account.cash + 100 * 310)
        assert position.qty == 100

        account.submit("THYAO", "sell", 100, now=OPEN + timedelta(minutes=10))
        (sell,) = account.process_bar("THYAO", bar(10, 310, 311, 309, 310))
        sell_cost = 100 * 310 * 0.002 * 1.05
        summary = account.summary()
        assert summary["realized_pnl"] == pytest.approx(
            1_000 - buy_cost - sell_cost, abs=0.01
        )
        assert summary["equity"] == pytest.approx(
            100_000 + summary["realized_pnl"], abs=0.01
        )
        assert summary["positions"] == []
        assert sell.commission == pytest.approx(sell_cost)

    def test_minimum_commission(self, make_account):
        account = make_account(costs=BistCosts(min_commission=5))
        account.submit("THYAO", "buy", 1, now=OPEN)
        (fill,) = account.process_bar("THYAO", bar(0, 300, 300, 300, 300))
        assert fill.commission == pytest.approx(5.25)

    def test_insufficient_cash_at_fill_rejects(self, make_account):
        account = make_account(cash=3_000)
        account.submit("THYAO", "buy", 10, now=OPEN)
        assert account.process_bar("THYAO", bar(0, 310, 311, 309, 310)) == []
        (order,) = account.orders()
        assert order.status == "rejected"
        assert "Insufficient cash" in order.reason

    def test_bars_are_processed_once(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, now=OPEN)
        first = bar(0, 300, 301, 299, 300)
        assert len(account.process_bar("THYAO", first)) == 1
        account.submit("THYAO", "buy", 10, now=OPEN)
        assert account.process_bar("THYAO", first) == []
        assert account.processed_until("THYAO") == first.end
        assert account.mark("THYAO") == 300


class TestLifecycle:
    def test_day_orders_expire_after_their_session(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, "limit", limit_price=290.0, now=OPEN)
        account.submit(
            "THYAO", "buy", 10, "limit", limit_price=290.0, tif="gtc", now=OPEN
        )
        account.process_bar("THYAO", bar(0, 300, 301, 299, 300))
        account.process_bar(
            "THYAO", bar(0, 300, 301, 299, 300, day=OPEN + timedelta(days=1))
        )
        day, gtc = account.orders()
        assert day.status == "expired"
        assert gtc.status == "open"

    def test_evening_orders_fill_in_the_next_session(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, now=OPEN.replace(hour=19))
        tomorrow = OPEN + timedelta(days=1)
        (fill,) = account.process_bar("THYAO", bar(0, 302, 303, 301, 302, day=tomorrow))
        assert fill.price == 302
        assert account.order(fill.order_id).session_date == tomorrow.date()

    def test_expire_day_orders_by_date(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 10, "limit", limit_price=290.0, now=OPEN)
        assert account.expire_day_orders(OPEN.replace(hour=19)) == []
        (expired,) = account.expire_day_orders(OPEN + timedelta(days=1))
        assert expired.status == "expired"

    def test_cancel(self, make_account):
        account = make_account()
        order = account.submit("THYAO", "buy", 10, "limit", limit_price=290.0, now=OPEN)
        assert account.cancel(order.id).status == "cancelled"
        assert account.buying_power() == account.cash
        with pytest.raises(OrderRejected, match="already cancelled"):
            account.cancel(order.id)
        with pytest.raises(KeyError):
            account.cancel(999)

    def test_two_processes_share_an_account(self, make_account):
        account = make_account()
        other = PaperAccount(account.path)
        other.submit("THYAO", "buy", 10, now=OPEN)
        (fill,) = account.process_bar("THYAO", bar(0, 300, 301, 299, 300))
        assert other.held("THYAO") == 10
        assert other.fills() == [fill]
        other.close()

    def test_equity_curve(self, make_account):
        account = make_account()
        account.record_equity(OPEN)
        account.record_equity(OPEN + timedelta(minutes=5))
        assert [row["equity"] for row in account.equity_curve()] == [100_000, 100_000]


class TestDividends:
    def test_paid_to_shares_held_before_the_ex_date(self, make_account):
        account = make_account()
        account.submit("THYAO", "buy", 100, now=OPEN)
        account.process_bar("THYAO", bar(0, 300, 300, 300, 300))
        cash = account.cash
        # Bought on the ex-date: this order's shares get no dividend.
        account.submit("THYAO", "buy", 50, now=OPEN + timedelta(days=1))
        ex_date = bar(0, 297, 298, 296, 297, day=OPEN + timedelta(days=1))
        ex_date = Bar(**{**ex_date.__dict__, "dividend": 3.0})
        account.process_bar("THYAO", ex_date)
        paid = 100 * 3.0 * (1 - 0.15)
        assert account.cash == pytest.approx(cash + paid - 50 * 297)
        assert account.dividend_income() == pytest.approx(paid)
        assert account.summary()["dividends"] == pytest.approx(paid, abs=0.01)

    def test_no_position_no_dividend(self, make_account):
        account = make_account()
        account.process_bar(
            "THYAO", Bar(OPEN, OPEN + timedelta(hours=8), 1, 1, 1, 1, 0, 5.0)
        )
        assert account.dividend_income() == 0
        assert account.cash == 100_000

    def test_tax_rate_is_a_setting(self, tmp_path):
        PaperAccount.create(tmp_path / "a.sqlite", dividend_tax=0.1).close()
        with PaperAccount(tmp_path / "a.sqlite") as account:
            assert account.dividend_tax == 0.1
        with pytest.raises(ValueError):
            PaperAccount.create(tmp_path / "b.sqlite", dividend_tax=1.0)
