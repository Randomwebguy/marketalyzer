"""Borsa Istanbul constants."""

from typing import Literal

TIMEZONE = "Europe/Istanbul"
YAHOO_SUFFIX = ".IS"
# Yahoo serves the same chart API from two hosts; a request rejected by one is
# retried on the other.
CHART_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
CHART_URL = "https://{host}/v8/finance/chart/{symbol}"

Interval = Literal["1m", "2m", "5m", "15m", "30m", "1h", "1d", "1W", "1M"]
INTERVALS: list[str] = ["1m", "2m", "5m", "15m", "30m", "1h", "1d", "1W", "1M"]

YAHOO_INTERVALS: dict[str, str] = {
    "1m": "1m",
    "2m": "2m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "1d": "1d",
    "1W": "1wk",
    "1M": "1mo",
}

# How far back Yahoo serves intraday bars, in days.
INTRADAY_LOOKBACK_DAYS: dict[str, int] = {
    "1m": 29,
    "2m": 59,
    "5m": 59,
    "15m": 59,
    "30m": 59,
    "1h": 729,
}
# Calendar days fetched per request. Yahoo rejects 1m requests spanning more
# than seven days, so longer ranges are split into chunks of this size.
INTRADAY_MAX_SPAN_DAYS: dict[str, int] = {"1m": 6}
# The default span when no start date is given for an intraday interval.
INTRADAY_DEFAULT_DAYS = 7

SYMBOL_ALIASES: dict[str, str] = {
    "BIST100": "XU100",
    "BIST50": "XU050",
    "BIST30": "XU030",
    "BISTTUM": "XUTUM",
}

# Main Borsa Istanbul indices. Codes are BIST's own; Yahoo lists them with the
# ".IS" suffix, but coverage of the smaller sector indices is not guaranteed.
BIST_INDICES: dict[str, str] = {
    "XU100": "BIST 100",
    "XU050": "BIST 50",
    "XU030": "BIST 30",
    "XUTUM": "BIST Tüm",
    "XYLDZ": "BIST Yıldız",
    "XKTUM": "BIST Katılım Tüm",
    "XUSRD": "BIST Sürdürülebilirlik",
    "XBANK": "BIST Banka",
    "XUMAL": "BIST Mali",
    "XUSIN": "BIST Sınai",
    "XUHIZ": "BIST Hizmetler",
    "XUTEK": "BIST Teknoloji",
    "XHOLD": "BIST Holding ve Yatırım",
    "XSGRT": "BIST Sigorta",
    "XGMYO": "BIST Gayrimenkul Yatırım Ortaklıkları",
    "XBLSM": "BIST Bilişim",
    "XELKT": "BIST Elektrik",
    "XGIDA": "BIST Gıda, İçecek",
    "XILTM": "BIST İletişim",
    "XINSA": "BIST İnşaat",
    "XKMYA": "BIST Kimya, Petrol, Plastik",
    "XMADN": "BIST Madencilik",
    "XMANA": "BIST Metal Ana",
    "XMESY": "BIST Metal Eşya, Makina",
    "XTCRT": "BIST Ticaret",
    "XTEKS": "BIST Tekstil, Deri",
    "XTRZM": "BIST Turizm",
    "XULAS": "BIST Ulaştırma",
}
