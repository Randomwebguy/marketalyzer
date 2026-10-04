import pytest

from openbb_bist.utils.symbols import (
    parse_symbols,
    to_bist_symbol,
    to_currency_pair,
    to_yahoo_currency,
    to_yahoo_symbol,
)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("THYAO", "THYAO.IS"),
        ("thyao", "THYAO.IS"),
        (" THYAO.IS ", "THYAO.IS"),
        ("THYAO.E", "THYAO.IS"),
        ("XU100", "XU100.IS"),
        ("^XU100", "XU100.IS"),
        ("BIST100", "XU100.IS"),
        ("bist 30", "XU030.IS"),
        ("USDTRY=X", "USDTRY=X"),
        ("btc-usd", "BTC-USD"),
    ],
)
def test_to_yahoo_symbol(symbol, expected):
    assert to_yahoo_symbol(symbol) == expected


def test_to_yahoo_symbol_rejects_blank():
    with pytest.raises(ValueError):
        to_yahoo_symbol("  ")


def test_to_bist_symbol():
    assert to_bist_symbol("THYAO.IS") == "THYAO"
    assert to_bist_symbol("xu100.is") == "XU100"


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("USDTRY", "USDTRY=X"),
        ("usd/try", "USDTRY=X"),
        ("EUR-TRY", "EURTRY=X"),
        ("EURTRY=X", "EURTRY=X"),
    ],
)
def test_to_yahoo_currency(symbol, expected):
    assert to_yahoo_currency(symbol) == expected


@pytest.mark.parametrize("symbol", ["TRY", "USDTRY1", "USD/TR"])
def test_to_yahoo_currency_rejects_invalid(symbol):
    with pytest.raises(ValueError):
        to_yahoo_currency(symbol)


def test_to_currency_pair():
    assert to_currency_pair("USDTRY=X") == "USDTRY"


def test_parse_symbols_converts_and_deduplicates():
    assert parse_symbols("THYAO, thyao.is,,GARAN", to_yahoo_symbol) == [
        "THYAO.IS",
        "GARAN.IS",
    ]


def test_parse_symbols_requires_one():
    with pytest.raises(ValueError):
        parse_symbols(" , ", to_yahoo_symbol)
