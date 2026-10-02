# Learning Blind Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI blind test learn causally from its own resolved decisions and, across walk-forward windows, revise its strategy script, so returns and trade counts can rise without looking ahead.

**Architecture:** The decision model gets the strategy's summary, its pre-test track record and a "veto" instruction. Symbols walk a shared date calendar so a journal of resolved outcomes (and lessons written from it every 6 outcomes) can feed later decisions across symbols. With rounds, the test period is split into windows; after each window the script author revises the script from that window's report and a study cut at the window end, and the new script trades only the next window. Every AI run is saved.

**Tech Stack:** Python 3.10+, pandas/numpy, FastAPI, the existing Pine-like scripting engine, OpenRouter-compatible chat API (OpenRouter or fal.ai), vanilla JS front end.

**Spec:** `docs/superpowers/specs/2026-10-03-learning-blind-test-design.md`

## Global Constraints

- Blindness: no outcome may be shown to a decision made before the outcome's resolution bar; revisions see data only up to the window end and trade only later windows.
- The model never sees tickers, dates or price levels (letters "Hisse A…", percentages, 100-based series); journal lines and lessons are scanned for every run ticker and ISO dates.
- Learning off must keep the existing blind tests passing unchanged (same trades and decisions for the same model answers).
- Turkish user-facing text; English code comments and docstrings matching the surrounding style.
- `uv run pytest -q` must pass after every task; commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Constants: outcome horizon `HORIZON = 10` bars, reflection every `REFLECT_EVERY = 6` new outcomes, at most `MAX_LESSONS = 5` lessons, `RECENT = 6` recent outcome lines, rounds 2–4.

## File Structure

- Create `marketalyzer/ai/learn.py` — strategy brief (cached model call), track record, `Journal`, `reflect()`.
- Create `marketalyzer/ai/runs.py` — save/list/load runs under `ai/lab/runs/`, latest lessons for a script.
- Modify `marketalyzer/ai/decide.py` — veto `SYSTEM`; `snapshot(..., strategy=None, journal=None)`.
- Modify `marketalyzer/blind.py` — `Simulation` split into `step/prepare/record/finish`; `_walk()` lockstep driver; learning; windows and rounds; `decide_now(..., lessons, brief)`.
- Modify `marketalyzer/ai/author.py` — shared write loop; `revise_script()`.
- Modify `marketalyzer/web/app.py` — request fields, coach model, run saving, `/api/lab/runs`, live lessons.
- Modify `marketalyzer/web/static/js/pages/strategy.js` — learning form, live lesson/round events, rounds table.
- Tests: `tests/test_ai_learn.py` (new), `tests/test_ai_lab.py` (fakes answer brief/reflection prompts).
- Docs: README "AI Strateji" section.

---

### Task 1: Veto prompt, strategy brief and track record

**Files:**
- Create: `marketalyzer/ai/learn.py`
- Modify: `marketalyzer/ai/decide.py` (`SYSTEM`, `snapshot`)
- Modify: `marketalyzer/blind.py` (`Simulation._view`, `run_blind`, `decide_now`)
- Test: `tests/test_ai_learn.py`, `tests/test_ai_lab.py` (`RuleModel`)

**Interfaces:**
- Produces: `learn.track_record(trades: list[dict]) -> dict`; `learn.strategy_brief(decider: Decider, source: str) -> dict` (`{"tur", "ozet"}`), `learn.BRIEF_SYSTEM`; `decide.snapshot(..., strategy: dict | None = None, journal: dict | None = None)` adding `view["strateji_ozeti"]`, `view["strateji_gecmisi"]`, `view["karar_gunlugu"]`; `blind.training_trades(script, data, config) -> list[dict]` (signals-only trades on bars before `data.first`).

- [ ] **Step 1: Failing tests** (`tests/test_ai_learn.py`)

```python
def test_track_record():
    trades = [{"return_pct": 4.0, "bars": 5}, {"return_pct": -2.0, "bars": 3}]
    assert learn.track_record(trades) == {
        "islem": 2, "kazancli_%": 50.0, "ort_getiri_%": 1.0, "ort_bar": 4.0}
    assert learn.track_record([]) == {"islem": 0}

def test_strategy_brief_is_cached_and_falls_back(tmp_path):
    calls = []
    def complete(api_key, model, messages, **kwargs):
        calls.append(messages)
        return answer_text('{"tur": "dönüş", "ozet": "Düşüşte alır."}')
    decider = Decider("k", "m", cache=DecisionCache(tmp_path / "c.sqlite"), complete=complete)
    assert learn.strategy_brief(decider, STRATEGY) == {"tur": "dönüş", "ozet": "Düşüşte alır."}
    assert learn.strategy_brief(decider, STRATEGY)["tur"] == "dönüş"
    assert len(calls) == 1
    broken = Decider("k", "m", complete=lambda *a, **k: answer_text("yok"))
    assert learn.strategy_brief(broken, STRATEGY)["tur"] == "bilinmiyor"

def test_views_carry_strategy_context_from_before_the_test():
    short, long = RuleModel(), RuleModel()
    blind.run_blind(config(symbols=["THYAO"], end=END - timedelta(days=60)), Decider("k", "m", complete=short))
    blind.run_blind(config(symbols=["THYAO"]), Decider("k", "m", complete=long))
    first = json.loads(short.views[0])
    assert first["strateji_ozeti"]["tur"] == "trend"
    assert first["strateji_gecmisi"] == json.loads(long.views[0])["strateji_gecmisi"]
```

`RuleModel` (in both test files) answers brief prompts: if `messages[0]["content"] == learn.BRIEF_SYSTEM` return `{"tur": "trend", "ozet": "Kesişimde alır."}`.

- [ ] **Step 2: Run** `uv run pytest tests/test_ai_learn.py -q` → FAIL (no module `learn`).

- [ ] **Step 3: Implement.** `learn.py`:

```python
BRIEF_SYSTEM = """Bir Pine Script strateji kodunu, bu stratejinin sinyallerini tek tek
onaylayacak hızlı bir karar modeline anlatacaksın. Yalnızca tek satır JSON yaz:
{"tur": "dönüş|trend|kırılım|karma", "ozet": "en fazla 2 cümle: hangi koşulda alır, hangi koşulda satar"}"""

def track_record(trades):
    if not trades:
        return {"islem": 0}
    returns = [t.get("return_pct") or 0.0 for t in trades]
    return {
        "islem": len(trades),
        "kazancli_%": number(sum(r > 0 for r in returns) / len(returns) * 100, 1),
        "ort_getiri_%": number(sum(returns) / len(returns), 2),
        "ort_bar": number(sum(t.get("bars") or 0 for t in trades) / len(trades), 1),
    }

def strategy_brief(decider, source):
    key = hashlib.sha256(f"brief\n{decider.model}\n{BRIEF_SYSTEM}\n{source}".encode()).hexdigest()
    stored = decider.cache.get(key) if decider.cache else None
    text = stored["text"] if stored else None
    if text is None:
        try:
            text = decider.complete(decider.api_key, decider.model,
                [{"role": "system", "content": BRIEF_SYSTEM}, {"role": "user", "content": source}],
                temperature=0.0, max_tokens=200, timeout=decide.TIMEOUT)["text"]
        except OpenRouterError:
            return {"tur": "bilinmiyor", "ozet": ""}
    brief = _parse_brief(text)   # JSON object with "tur" and "ozet", text trimmed to 300; dates removed
    if brief["tur"] != "bilinmiyor" and decider.cache and not stored:
        decider.cache.put(key, {"text": text})
    return brief
```

`decide.snapshot` gains `strategy` (merged as `strateji_ozeti` + `strateji_gecmisi`) and `journal` (as `karar_gunlugu`, only when not None). New `SYSTEM`: veto rules (default: follow the signal; the strategy's own entry conditions are not a reason to reject; reject only for clear extra risk; follow `karar_gunlugu` lessons; missed profitable signals are mistakes; `guven` = probability the decision proves right). `blind.training_trades` runs a signals-only `Simulation` over `Data(code, frame.iloc[:first], train_first, train_first, script.run(cut), {})` and returns its trades. `run_blind` (AI mode) computes `brief = learn.strategy_brief(decider, script.source)` once and `record = learn.track_record(all training trades)`, passes `strategy={"strateji_ozeti": brief, "strateji_gecmisi": record}` into each AI `Simulation`, which hands it to `snapshot`. `decide_now(..., brief=None, record=None, lessons=None)` does the same for live views.

- [ ] **Step 4: Run** `uv run pytest -q` → all pass.
- [ ] **Step 5: Commit** "Tell the decision model what the strategy does and veto instead of approve".

### Task 2: Lockstep walk over a shared date calendar

**Files:** Modify `marketalyzer/blind.py`; Test `tests/test_ai_learn.py`.

**Interfaces:**
- Produces: `Simulation.step(i) -> str | None` (fills, exits, equity; returns the event to decide on), `Simulation.prepare(i, event, journal_view=None) -> dict | None` (view, or None after a mismatch or leak), `Simulation.record(i, event, view, decision) -> str | None`, `Simulation.queue(action, note)`, `Simulation.finish(reason)`, `Simulation.trade_bars: list[tuple[int, int]]`; `_walk(sims, decider=None, *, cancelled, learner=None) -> None`.

- [ ] **Step 1: Failing test**

```python
def test_symbol_order_does_not_change_results():
    a = blind.run_blind(config(symbols=["THYAO", "GARAN"]), Decider("k", "m", complete=RuleModel()))
    b = blind.run_blind(config(symbols=["GARAN", "THYAO"]), Decider("k", "m", complete=RuleModel()))
    key = lambda d: (d["time"], d["symbol"])
    assert sorted(a["decisions"], key=key) == sorted(b["decisions"], key=key)
    assert a["ai"]["return_pct"] == b["ai"]["return_pct"]
```

- [ ] **Step 2: Run** → this property already holds per symbol today; it guards the refactor, and the existing blind tests must stay green through it.
- [ ] **Step 3: Implement.** Split `Simulation.run` into `step(i)` (pending fill → intrabar exits → peak/exposure/levels → equity point → `_event`), `prepare`, `record` (the old `_decision` body after `self.decide(view)`), `queue`, `finish`. `_sell` appends `(account.entry_bar, i)` to `trade_bars`. `_walk` builds the sorted union of test dates; per date it steps every symbol that has the bar, then (AI) prepares views, asks `decider.decide` for all of them in a `ThreadPoolExecutor(max_workers=min(4, n))` in symbol order, records, queues actions; signals-only queues `{"entry": "buy", "exit": "sell"}`. `Simulation.run()` stays as `_walk([self], ...)` for single use. `run_blind` uses `_walk` for both runs.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Walk every symbol on one date calendar".

### Task 3: Causal decision journal

**Files:** Modify `marketalyzer/ai/learn.py`, `marketalyzer/blind.py` (`BlindConfig.learning`, `_walk`, `summarize`); Test `tests/test_ai_learn.py`.

**Interfaces:**
- Produces: `learn.Journal(letters: dict[str, str])` with `add(record, *, holding, features, outcome=None) -> Entry`, `resolve(entry, pct, kind, at)`, `view(now) -> dict | None`, `fresh(now) -> int`, `mark(now)`, `lessons: list[str]`, `history: list[dict]`, `shown: list[tuple]` (now, resolved-at pairs for the audit); `Entry` fields `order, code, letter, event, label, action, holding, confidence, features, pct, kind, at`; `learn.features(view) -> dict`; `BlindConfig.learning: str = "off"` (`"off" | "journal" | "rounds"`).
- Outcomes: AL → the AI trade's return when it closes (`kind="işlem"`); BEKLE on an entry → the signals-only trade that opened at the next bar (`kind="sinyal işlemi"`, resolved at its exit bar), else the 10-bar forward return; SAT/TUT and reviews → 10-bar forward return from the next open (capped at the last bar).

- [ ] **Step 1: Failing tests**

```python
def test_journal_hides_outcomes_until_they_resolve():
    j = learn.Journal({"THYAO": "Hisse A"})
    t = pd.Timestamp
    e = j.add({"symbol": "THYAO", "event": "giriş", "label": "BEKLE", "action": "hold", "confidence": 60}, holding=False, features={})
    j.resolve(e, 9.9, "sinyal işlemi", t("2025-03-10"))
    assert j.view(t("2025-03-07")) is None
    v = j.view(t("2025-03-10"))
    assert v["ozet"]["reddedilen_giris"] == {"adet": 1, "kar_ettirecek_%": 100.0, "ort_%": 9.9}
    assert v["son_sonuclar"] == ["Hisse A · giriş · BEKLE %60 → sinyal işlemi %+9.9 (kaçırıldı)"]

def test_blind_journal_is_causal_and_names_no_ticker():
    model = RuleModel()
    result = blind.run_blind(config(learning="journal"), Decider("k", "m", complete=model))
    journal = result["learning"]
    assert journal["resolved"] > 0 and journal["shown_after_resolution"] is True
    texts = [v for v in model.views if "karar_gunlugu" in v]
    assert texts and all("THYAO" not in v and "GARAN" not in v and not DATE.search(v) for v in texts)

def test_rejected_entries_learn_from_the_signal_trade():
    def never(api_key, model, messages, **kwargs):
        return answer("BEKLE") if not brief(messages) else brief_answer()
    result = blind.run_blind(config(symbols=["THYAO"], learning="journal"), Decider("k", "m", complete=never))
    trades = {t["entry_time"]: t["return_pct"] for t in result["signal_trades"]}
    outcomes = [d for d in result["decisions"] if d.get("outcome_kind") == "sinyal işlemi"]
    assert outcomes and all(d["outcome_pct"] in trades.values() for d in outcomes)

def test_learning_keeps_later_bars_from_changing_earlier_decisions():
    short, long = RuleModel(), RuleModel()
    a = blind.run_blind(config(symbols=["THYAO"], end=END - timedelta(days=60), learning="journal"), Decider("k", "m", complete=short))
    b = blind.run_blind(config(symbols=["THYAO"], learning="journal"), Decider("k", "m", complete=long))
    shared = len(short.views)
    assert long.views[:shared] == short.views
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `Journal` (verdict words: BEKLE + gain "kaçırıldı", BEKLE + loss "doğru kaçınıldı", AL + gain "doğru", AL + loss "zarar", SAT + later fall "doğru satış", SAT + later rise "erken satış", TUT + rise "doğru", TUT + fall "tutmak zarar"; summary over resolved entries only; lines of the last `RECENT` resolved). In `_walk` with a learner: AI `_sell` resolves the open AL entry at the exit bar; after stepping a date, the view for every decision that date is `journal.view(date)` (recorded in `shown`); new entries get their counterfactual/forward outcome scheduled from the precomputed signals simulation (`trade_bars`) or the frame. `summarize` adds `result["learning"] = {"mode", "resolved", "lessons", "history", "shown_after_resolution"}` and each decision `outcome_pct`, `outcome_kind`. Audit note: "Karar günlüğündeki her sonuç yalnızca gerçekleştiği bardan sonraki kararlara gösterildi (N sonuç)."
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Feed resolved outcomes back to later decisions".

### Task 4: Lessons from reflection

**Files:** Modify `marketalyzer/ai/learn.py`, `marketalyzer/blind.py` (`run_blind(..., coach=None)`); Test `tests/test_ai_learn.py`.

**Interfaces:**
- Produces: `learn.Coach(api_key: str, model: str, complete=complete_chat, stream=stream_chat)`; `learn.REFLECT_SYSTEM`; `learn.reflect(journal, now, coach, *, strategy, codes) -> list[str] | None`; `learn.clean_lessons(items, codes) -> list[str]`; run events `{"type": "lesson", "lessons": [...], "resolved": n}`.

- [ ] **Step 1: Failing tests**

```python
def test_lessons_drop_tickers_dates_and_extras():
    assert learn.clean_lessons(["  RSI<30 iken al ", "THYAO'da dikkat", "2025-01-02 sonrası", "x" * 400, *"abcdef"], ["THYAO"]) == [
        "RSI<30 iken al", "a", "b", "c", "d"]

def test_reflection_every_six_outcomes_reaches_the_view():
    model = RuleModel()
    coach_calls = []
    def coach_complete(api_key, model_id, messages, **kwargs):
        coach_calls.append(messages[1]["content"])
        return answer_text('{"dersler": ["Aşırı satımda sinyali uygula."]}')
    events = []
    result = blind.run_blind(config(learning="journal"), Decider("k", "m", complete=model),
        coach=learn.Coach("k", "coach", complete=coach_complete), emit=events.append)
    assert coach_calls and "THYAO" not in coach_calls[0] and not DATE.search(coach_calls[0])
    assert any(e["type"] == "lesson" for e in events)
    assert any("Aşırı satımda sinyali uygula." in v for v in model.views)
    assert result["learning"]["lessons"] == ["Aşırı satımda sinyali uygula."]
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `reflect`: user content = JSON with `strateji`, `mevcut_dersler`, last 40 resolved entries (`hisse` letter, `olay`, `karar`, `guven`, `sonuc_%`, `sonuc_turu`, `ozellikler`); parse `{"dersler": [...]}` or `- ` lines; `clean_lessons` (trim, drop items with a run ticker or ISO date, cut to 200 chars dropping longer ones, keep 5). In `_walk`, before deciding on a date: if `coach` and `journal.fresh(date) >= REFLECT_EVERY`, reflect, set lessons, `mark(date)`, append history, emit `lesson`. Errors keep the old lessons.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Write lessons from resolved decisions".

### Task 5: Saved runs and lessons for live decisions

**Files:** Create `marketalyzer/ai/runs.py`; Modify `marketalyzer/web/app.py`; Test `tests/test_ai_learn.py`.

**Interfaces:**
- Produces: `runs.save(result) -> str` (id `YYYYmmdd_HHMMSS_<6 hex>`), `runs.list_runs(limit=20) -> list[dict]` (id, time, symbols, scripts, ai/signals/hold return, trades), `runs.load(run_id) -> dict` (ValueError if missing or bad id), `runs.latest_learning(script: str) -> dict | None` (`{"lessons", "brief", "record"}`); `GET /api/lab/runs`, `GET /api/lab/runs/{run_id}`; `/api/lab/live` passes the latest learning to `decide_now`.

- [ ] **Step 1: Failing tests** — save/list/load round trip in the temp home, bad ids rejected (`../x`), `latest_learning` picks the newest run that used the script; web: an AI blind run returns `run_id` and appears in `/api/lab/runs`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** files under `ai_home() / "lab" / "runs"` written with `write_json`; app saves after every AI run and adds `result["run_id"]`.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Save blind test runs and use their lessons live".

### Task 6: Walk-forward windows

**Files:** Modify `marketalyzer/blind.py`; Test `tests/test_ai_learn.py`.

**Interfaces:**
- Produces: `BlindConfig.rounds: int = 1` (1–4; `check()` rejects others and requires `learning == "rounds"` for more than 1); `blind.windows(data, rounds) -> list[tuple[pd.Timestamp, pd.Timestamp]]`; `run_blind(..., revise=None)` where `revise(round_no, script, report, cutoff: date) -> tuple[Script, dict] | None`; `result["rounds"]` rows `{"round", "start", "end", "script", "ai", "signals", "hold_return_pct", "benchmark_return_pct", "revision"}`; `result["initial_signals"]` (first script, signals-only, whole period) when rounds > 1.

- [ ] **Step 1: Failing tests**

```python
def test_windows_split_the_test_dates_evenly():
    cfg = config()
    data, _ = blind.load(cfg, services._script(None, cfg.source))
    parts = blind.windows(data, 3)
    assert len(parts) == 3 and parts[0][0] == min(d.frame.index[d.first] for d in data)
    assert all(a[1] < b[0] for a, b in zip(parts, parts[1:]))

def test_rounds_close_at_window_end_and_compound():
    result = blind.run_blind(config(mode="signals", learning="rounds", rounds=2))
    assert [r["round"] for r in result["rounds"]] == [1, 2]
    assert any(t["exit_reason"] == "tur sonu" for t in result["signal_trades"])

def test_one_round_matches_the_plain_run():
    plain = blind.run_blind(config(mode="signals"))
    one = blind.run_blind(config(mode="signals", learning="rounds", rounds=1))
    assert one["signals"] == plain["signals"]

def test_revision_trades_only_the_next_window():
    seen = []
    def revise(round_no, script, report, cutoff):
        seen.append((round_no, cutoff, report["round"]))
        return services._script(None, STRATEGY.replace("fast = input.int(10", "fast = input.int(5")), {"name": "ai_x_t2", "ok": True}
    result = blind.run_blind(config(learning="rounds", rounds=2), Decider("k", "m", complete=RuleModel()), revise=revise)
    assert seen == [(1, date.fromisoformat(result["rounds"][0]["end"][:10]), 1)]
    assert result["rounds"][1]["script"] == "ai_x_t2"
    assert all(d["time"] <= result["rounds"][0]["end"] for d in result["decisions"] if d["round"] == 1)

def test_failed_revision_keeps_the_script():
    result = blind.run_blind(config(learning="rounds", rounds=2), Decider("k", "m", complete=RuleModel()),
        revise=lambda *a: None)
    assert result["rounds"][1]["script"] == result["rounds"][0]["script"]
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `windows` (union of test dates split into `rounds` nearly equal chunks); `run_blind` loops over windows: per window, `Data` cut at the window end with `first` at the window start, the window's script run on the cut frame, training stats and track record from bars before the window start, allocations carried from the previous window's final equity (AI and signals separately), finish reason `"tur sonu"` except the last window `"dönem sonu (açık)"`. After each window but the last: window report (letters only), reflection if fresh outcomes, `revise(...)`; a returned script trades the next window. Curves, trades and decisions are concatenated (decisions carry `round`). One window reproduces the plain run.
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Split the blind test into walk-forward windows".

### Task 7: Script revision by the author

**Files:** Modify `marketalyzer/ai/author.py`; Test `tests/test_ai_lab.py`.

**Interfaces:**
- Produces: `author.revise_script(source, report_text, symbols, *, cutoff, years, api_key, model, emit, cancelled, name, interval="1d", stream=stream_chat) -> dict` (`name, source, explanation, attempts, usage`; ValueError if no valid script); `author.REVISE`; `blind.report_text(report: dict) -> str` (anonymous window report).

- [ ] **Step 1: Failing test** with `stream_replies`: revision prompt contains "Hisse A", the report text and the current source, no ticker or date; a broken first reply is fixed on the second attempt; the script is saved as `ai_test_t2`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** by moving `author_script`'s attempt loop into `_write(messages, frames, ...)` shared by both; `REVISE` states the goal (beat XU100 and signals-only in total return; at least one trade per symbol per window; drawdown below buy-and-hold; consider trend following, longer holds or looser entries when exposure is low and buy-and-hold is far ahead; follow the lessons; few simple rules, round thresholds).
- [ ] **Step 4: Run** `uv run pytest -q` → pass.
- [ ] **Step 5: Commit** "Let the author revise a script from a window report".

### Task 8: Web API, interface and docs

**Files:** Modify `marketalyzer/web/app.py`, `marketalyzer/web/static/js/pages/strategy.js`, `README.md`; Test `tests/test_ai_lab.py`.

**Interfaces:**
- Consumes: `learn.Coach`, `author.revise_script`, `runs.*`.
- Produces: `BlindRequest.learning: Literal["off", "journal", "rounds"] = "off"`, `BlindRequest.rounds: int = Field(4, ge=1, le=4)`; estimate adds `learning: {"reflections", "revisions", "coach_model"}`; SSE events `lesson`, `round`, `revision`.

- [ ] **Step 1: Failing web test**: AI blind run with `learning="rounds", rounds=2` and fakes for the decider, coach and author stream returns `rounds`, `learning`, `run_id`; events include `round` and `lesson`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** app wiring (coach model = `resolve_model(key, settings.model)`; `revise` closure calling `author.revise_script` with name `<script>_t<k>`); UI: "Öğrenme" select and "Tur" select, estimate text with extra calls, live lesson box and round markers, result rounds table with revised scripts ("Kullan" buttons), lessons list, decisions "Sonuç" column; README section.
- [ ] **Step 4: Run** `uv run pytest -q` and `node --check` on the changed scripts → pass.
- [ ] **Step 5: Commit** "Offer the learning blind test in the interface".

### Task 9: Deploy and validate on real data

- [ ] Push, update the VPS (`git pull && uv sync && systemctl restart marketalyzer`), confirm Netlify serves the new interface.
- [ ] Re-run the user's test (ENKAI, TUPRS, THYAO, BIMAS, ASELS; 2025-10-02 → 2026-10-02; 1 year training; script `ai_enkai_tuprs_thyao_bimas_20261003_0158`; `gemini-flash`) with learning off (veto only), `journal`, and `rounds=4`; compare with the baseline table (AI +2.27 % / 1 trade; signals +4.43 % / 28; hold +43.9 %; XU100 +10.52 %).
