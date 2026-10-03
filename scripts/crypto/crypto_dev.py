"""Develop crypto strategies without AI: choose on 2021-2023, test untouched on 2024-2026.

    python crypto_dev.py regime SCRIPT [SCRIPT ...]
    python crypto_dev.py params SCRIPT

regime: entries only while BTC is above its 50- or 200-day average, exits by the
signal or by the exit evidence. params: a small input grid, chosen by the median
yearly return on 2021-2023 with its neighbours, then run on 2024-2026.
"""

import itertools
import json
import statistics
import sys

import crypto_lab as lab
from marketalyzer import blind
from marketalyzer.ai import decide

TRAIN = ["Y2021", "Y2022", "Y2023"]
TEST = ["Y2024", "Y2025", "Y2026"]


class RuleDecider(decide.EvidenceDecider):
    """Entries only in BTC's uptrend (``regime``); exits by signal or evidence."""

    model = "kural"
    regime: str | None = None  # "sma50", "sma200" or None
    exit_evidence = False

    def decide(self, view):
        holding = (view.get("pozisyon") or {}).get("durum") == "var"
        if holding:
            if self.exit_evidence:
                return super().decide(view)
            event = (view.get("strateji") or {}).get("olay")
            if event == "çıkış":
                return decide.Decision("sell", "SAT", reason="sinyal", model=self.model)
            return decide.Decision("hold", "TUT", reason="sinyal yok", model=self.model)
        market = view.get("piyasa") or {}
        if self.regime == "sma50":
            up = (market.get("xu100_sma50_uzaklik_%") or 0) > 0
        elif self.regime == "sma200":
            up = bool(market.get("xu100_sma200_ustunde"))
        else:
            up = True
        if up:
            return decide.Decision("buy", "AL", reason="BTC yukarı trendde", model=self.model)
        return decide.Decision("hold", "BEKLE", reason="BTC aşağı trendde", model=self.model)


def run_rule(script, window, regime, exit_evidence, inputs=None):
    RuleDecider.regime, RuleDecider.exit_evidence = regime, exit_evidence
    blind.EvidenceDecider = RuleDecider
    start, end = lab.YEARS[window]
    codes = lab.eligible(lab.mx.CRYPTO_15, start, 1.0, "1d")
    row = lab.run(script, codes, start, end, 1.0, "1d", "stats", "ask", inputs=inputs)
    return {"window": window, "regime": regime, "exit_evidence": exit_evidence, **row}


def signals(script, window, inputs):
    start, end = lab.YEARS[window]
    codes = lab.eligible(lab.mx.CRYPTO_15, start, 1.0, "1d")
    return lab.run(script, codes, start, end, 1.0, "1d", inputs=inputs)["return"]


GRIDS = {
    "supertrend": {"factor": [2.0, 2.5, 3.0, 3.5, 4.0], "atrPeriod": [7, 14, 21]},
    "supertrend_sik": {"factor": [1.0, 1.5, 2.0, 2.5, 3.0], "atrPeriod": [7, 14, 21]},
    "sma_cross": {"fastLength": [10, 20, 30], "slowLength": [60, 100, 140]},
    "donchian_breakout": {"entryLength": [20, 40, 60], "exitLength": [10, 20, 30]},
    "macd_trend": {"trendLength": [50, 100, 150, 200]},
    "coklu_trend": {"short": [10, 20, 30], "medium": [40, 60, 100]},
}


def main():
    kind, scripts = sys.argv[1], sys.argv[2:]
    out = open(f"crypto_dev_{kind}.jsonl", "a", encoding="utf-8")
    if kind == "regime":
        for script in scripts:
            for regime, exit_evidence in itertools.product((None, "sma50", "sma200"), (False, True)):
                for window in TRAIN + TEST:
                    row = run_rule(script, window, regime, exit_evidence)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{script:18} rejim={regime!s:6} çıkış_kanıtı={exit_evidence!s:5} {window} %{row['return']:>8}"
                          f" → {row['money_end']:>10,} $ · düşüş %{row['dd']}", flush=True)
    else:
        script = scripts[0]
        grid = GRIDS[script]
        keys = list(grid)
        scores = {}
        for values in itertools.product(*grid.values()):
            inputs = dict(zip(keys, values, strict=True))
            yearly = [signals(script, w, inputs) for w in TRAIN]
            scores[values] = statistics.median(yearly)
            print(script, inputs, "eğitim yılları", yearly, flush=True)
        # Neighbourhood score: the median of a point and its grid neighbours.
        def neighbours(values):
            idx = [grid[k].index(v) for k, v in zip(keys, values, strict=True)]
            near = []
            for delta in itertools.product((-1, 0, 1), repeat=len(keys)):
                j = [i + d for i, d in zip(idx, delta, strict=True)]
                if all(0 <= x < len(grid[k]) for x, k in zip(j, keys, strict=True)):
                    near.append(scores[tuple(grid[k][x] for x, k in zip(j, keys, strict=True))])
            return statistics.median(near)
        best = max(scores, key=neighbours)
        default = None
        inputs = dict(zip(keys, best, strict=True))
        print("seçilen (eğitim, komşularıyla):", inputs, "eğitim medyanı", scores[best])
        for window in TEST:
            tuned = signals(script, window, inputs)
            plain = signals(script, window, default or {})
            row = {"script": script, "window": window, "inputs": inputs, "tuned": tuned, "default": plain}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  {window}: ayarlı %{tuned} → {lab.money(tuned):,} $ · varsayılan %{plain} → {lab.money(plain):,} $", flush=True)


if __name__ == "__main__":
    main()
