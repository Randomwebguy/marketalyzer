"""Download Binance USDT pairs' candles into the cache, delisted pairs included.

    uv run python scripts/crypto/binance_fetch.py        # daily, every tradable pair
    uv run python scripts/crypto/binance_fetch.py 4h     # 4-hour, universe coins only

Later runs only fetch what is new. Intraday bars are fetched only for the
coins that were ever in the monthly top-20 universe (built from the daily
cache, so fetch the daily candles first).
"""

from __future__ import annotations

import sys
import time
from datetime import date

from marketalyzer.crypto import binance, universe

interval = sys.argv[1] if len(sys.argv) > 1 else "1d"
start = time.time()
if interval == "1d":
    symbols = universe.tradable(binance.usdt_symbols())
else:
    pairs = {p.stem: binance.history(p.stem, refresh=False) for p in binance.cache_dir().glob("*.csv")}
    months = universe.monthly(universe.coins(pairs), date(2019, 1, 1), date.today(), size=20)
    symbols = sorted({c.split("#")[0] + "USDT" for m in months.values() for c in m})
frames = binance.download(symbols, workers=24, interval=interval)
print(f"{len(frames)}/{len(symbols)} pairs ({interval}) in {time.time() - start:.0f} s -> {binance.cache_dir(interval)}")
missing = sorted(set(symbols) - set(frames))
if missing:
    print("missing:", " ".join(missing))
