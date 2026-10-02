"""Symbol conversion between BIST codes and Yahoo Finance tickers."""

from collections.abc import Callable

from openbb_bist.utils.constants import SYMBOL_ALIASES, YAHOO_SUFFIX


def to_yahoo_symbol(symbol: str) -> str:
    """Convert a BIST code such as THYAO, THYAO.E or BIST100 to a Yahoo ticker.

    Tickers that already carry a Yahoo suffix (".IS", "=X", ...) pass through.
    """
    code = symbol.strip().upper().removeprefix("^").replace(" ", "")
    if not code:
        raise ValueError("Symbol must not be empty.")
    code = SYMBOL_ALIASES.get(code, code)
    if code.endswith(".E"):
        code = code.removesuffix(".E")
    if code.endswith(YAHOO_SUFFIX) or "." in code or "=" in code:
        return code
    return code + YAHOO_SUFFIX


def to_bist_symbol(yahoo_symbol: str) -> str:
    """Convert a Yahoo ticker back to its BIST code."""
    return yahoo_symbol.upper().removesuffix(YAHOO_SUFFIX)


def to_yahoo_currency(symbol: str) -> str:
    """Convert a currency pair such as USDTRY or USD/TRY to a Yahoo ticker."""
    pair = symbol.strip().upper().replace("/", "").replace("-", "").replace(" ", "")
    if pair.endswith("=X"):
        return pair
    if len(pair) == 6 and pair.isalpha():
        return pair + "=X"
    raise ValueError(
        f"Invalid currency pair: {symbol!r}. Use a six-letter pair such as USDTRY."
    )


def to_currency_pair(yahoo_symbol: str) -> str:
    """Convert a Yahoo currency ticker back to a plain pair."""
    return yahoo_symbol.upper().removesuffix("=X")


def parse_symbols(symbol: str, convert: Callable[[str], str]) -> list[str]:
    """Split a comma-separated string, convert each item and drop duplicates."""
    converted = [convert(item) for item in symbol.split(",") if item.strip()]
    if not converted:
        raise ValueError("At least one symbol is required.")
    return list(dict.fromkeys(converted))
