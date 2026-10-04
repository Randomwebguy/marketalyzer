"""Download every tradable Binance USDT pair's daily candles into the cache.

    uv run python scripts/crypto/binance_fetch.py

Delisted pairs included; later runs only fetch what is new.
"""

from __future__ import annotations

import time

from marketalyzer.crypto import binance, universe

start = time.time()
symbols = universe.tradable(binance.usdt_symbols())
frames = binance.download(symbols, workers=24)
print(f"{len(frames)}/{len(symbols)} pairs in {time.time() - start:.0f} s -> {binance.cache_dir()}")
missing = sorted(set(symbols) - set(frames))
if missing:
    print("missing:", " ".join(missing))
