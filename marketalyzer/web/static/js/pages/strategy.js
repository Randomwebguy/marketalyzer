// AI strategy lab: study chosen stocks, let the AI write a Pine script from the
// findings, then test it blind: the AI decides bar by bar seeing only the past.
import {
  $, api, busy, dateOnly, emit, esc, fmtDate, fmtMoney, fmtNumber, icon, lineChart, normalize, pctText,
  pill, slotColor, store, stream, tipTime, toast, token, tone,
} from "/static/js/core.js";
import { highlightLine } from "/static/js/editor.js";
import { renderMarkdown } from "/static/js/markdown.js";
import { chartWithTable, tableHtml, tile } from "/static/js/results.js";

export const TARGETS = ["ENKAI", "TUPRS", "THYAO", "BIMAS", "ASELS"];
const MAX = 8;
const YEARS = [1, 2, 3, 5];
const REVIEWS = [[0, "Yalnızca yeni sinyallerde"], [5, "Her 5 barda bir"], [10, "Her 10 barda bir"], [20, "Her 20 barda bir"]];
const LEARNING = [
  ["off", "Kapalı"],
  ["journal", "Karar günlüğü: sonuçlarından öğrenir"],
  ["rounds", "Günlük + script turları"],
];
const ROUNDS = [2, 3, 4];
const INTERVALS = [["1d", "1 gün"], ["1h", "1 saat"], ["1W", "1 hafta"]];
const STAGES = {
  study: "Sinyaller inceleniyor", writing: "Model scripti yazıyor", fixing: "Model scripti düzeltiyor",
  checking: "Script doğrulanıyor", saved: "Kaydedildi", loading: "Veriler yükleniyor",
  strategy: "Strateji özetleniyor", deciding: "Model bar bar karar veriyor", revising: "Script geliştiriliyor",
  summary: "Sonuçlar hesaplanıyor",
};
// Results survive leaving the page; the module stays loaded.
const memory = { study: null, author: null, blind: null, live: null, rotation: null, presets: null, scripts: null };
// Turkey's large caps, the rotation's default universe (with survivorship bias).
const LARGE_CAPS = [
  "AKBNK", "GARAN", "ISCTR", "YKBNK", "KCHOL", "SAHOL", "SISE", "EREGL", "FROTO", "TOASO",
  "TCELL", "TUPRS", "THYAO", "BIMAS", "ASELS", "ENKAI", "PGSUS", "ARCLK", "PETKM", "KRDMD",
];

const num = (v, d = 2) => (Number.isFinite(v) ? fmtNumber(v, d) : "—");
const pctCell = (v) => (Number.isFinite(v) ? `<span class="${tone(v)}-text">${esc(pctText(v))}</span>` : "—");
const signedPct = (v) => (Number.isFinite(v) ? `${v > 0 ? "+" : ""}${fmtNumber(v, 2)}` : "—");
const decisionChip = (label) => {
  const cls = { AL: "green", SAT: "red", TUT: "sky", BEKLE: "" }[label] ?? "";
  return `<span class="chip tag ${cls}">${esc(label)}</span>`;
};

function readSetup() {
  return {
    symbols: store.get("strategy.symbols", TARGETS),
    start: store.get("strategy.start", dateOnly(-365)),
    end: store.get("strategy.end", dateOnly(0)),
    years: store.get("strategy.years", 2),
  };
}

export function render(root) {
  const setup = readSetup();
  root.innerHTML = `
    <div class="grid strategy">
      <aside class="stack span-4 strategy-side">
        <section class="card glow-violet" data-setup></section>
        <section class="card" data-steps></section>
      </aside>
      <div class="stack span-8">
        <section class="card" data-study></section>
        <section class="card" data-author></section>
        <section class="card" data-blind></section>
        <section class="card" data-live></section>
        <section class="card" data-rotation></section>
      </div>
    </div>`;

  // ------------------------------------------------------------- setup

  function cutoff() {
    const day = new Date(`${setup.start}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - 1);
    return day.toISOString().slice(0, 10);
  }

  function trainStart() {
    const day = new Date(`${setup.start}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - Math.round(setup.years * 365.25));
    return day.toISOString().slice(0, 10);
  }

  function save() {
    store.set("strategy.symbols", setup.symbols);
    store.set("strategy.start", setup.start);
    store.set("strategy.end", setup.end);
    store.set("strategy.years", setup.years);
  }

  function timeline() {
    const t0 = Date.parse(trainStart());
    const t1 = Date.parse(setup.start);
    const t2 = Date.parse(setup.end);
    const total = Math.max(t2 - t0, 1);
    const train = Math.max(8, Math.min(92, ((t1 - t0) / total) * 100));
    return `
      <div class="timeline" role="img" aria-label="Eğitim ${esc(trainStart())} – ${esc(cutoff())}, test ${esc(setup.start)} – ${esc(setup.end)}">
        <div class="seg train" style="width:${train}%"><span>Eğitim</span></div>
        <div class="seg test" style="width:${100 - train}%"><span>Kör test</span></div>
        <i class="cut" style="left:${train}%"></i>
      </div>
      <div class="timeline-labels"><span>${esc(fmtDate(trainStart()))}</span><span>${esc(fmtDate(setup.start))}</span><span>${esc(fmtDate(setup.end))}</span></div>
      <p class="muted small" style="margin:10px 0 0">Araştırma ve script yalnızca eğitim dönemini görür. Testte model her kararı
        o bara kadarki verilerle verir; hisse adı, tarih ve fiyat seviyesi modele gönderilmez.</p>`;
  }

  function drawSetup() {
    const box = $("[data-setup]", root);
    box.innerHTML = `
      <div class="card-head"><h2>Hisseler ve dönem</h2><span class="spacer"></span><span class="badge violet">AI</span></div>
      <div class="symbol-chips" data-chips>${setup.symbols.map((s) => `
        <span class="chip">${esc(s)}<button type="button" class="x" data-remove="${esc(s)}" aria-label="${esc(s)} kaldır">${icon("x", "sm")}</button></span>`).join("")}
        ${setup.symbols.length < MAX ? `<input class="chip-input" data-add placeholder="Hisse ekle (ör. THY)" maxlength="16" autocomplete="off" aria-label="Hisse ekle">` : ""}
      </div>
      <div class="row-flex" style="margin:8px 0 14px">
        <button type="button" class="chip tag sky" data-targets>${icon("flag", "sm")} Hedef sepet</button>
        <button type="button" class="chip tag" data-one>Tek hisse</button>
        <span class="muted small">${setup.symbols.length}/${MAX} hisse</span>
      </div>
      <form class="form" data-setup-form>
        <div class="row">
          <label class="field">Test başlangıcı<input name="start" type="date" value="${esc(setup.start)}" max="${dateOnly(-1)}" required></label>
          <label class="field">Test bitişi<input name="end" type="date" value="${esc(setup.end)}" max="${dateOnly(0)}" required></label>
        </div>
        <div class="field">Eğitim (araştırma) süresi
          <div class="segmented block" data-years>${YEARS.map((y) => `<button type="button" data-y="${y}" class="${setup.years === y ? "active" : ""}">${y} yıl</button>`).join("")}</div>
        </div>
      </form>
      <div data-timeline>${timeline()}</div>
      <button class="btn primary block" type="button" data-run-study style="margin-top:14px">${icon("search", "sm")} Sinyalleri incele</button>`;
  }

  function addSymbol(value) {
    const code = normalize(value);
    if (!code) return;
    if (!/^[A-Z0-9]{2,12}$/.test(code)) return toast(`Geçersiz kod: ${value}`, "error");
    if (setup.symbols.includes(code)) return;
    if (setup.symbols.length >= MAX) return toast(`En fazla ${MAX} hisse seçilebilir.`, "error");
    setup.symbols = [...setup.symbols, code];
    save();
    drawSetup();
    $("[data-add]", root)?.focus();
  }

  function drawSteps() {
    const done = {
      study: Boolean(memory.study), author: Boolean(memory.author?.result), blind: Boolean(memory.blind?.result),
      live: Boolean(memory.live), rotation: Boolean(memory.rotation),
    };
    const steps = [
      ["study", "Sinyal araştırması", "Davranış, likidite, ~35 sinyalin geçmiş isabeti"],
      ["author", "Pine Script", "AI araştırmaya göre strateji yazar, doğrular, kaydeder"],
      ["blind", "Kör backtest", "Hızlı model her sinyali onaylar ya da reddeder"],
      ["live", "Canlı karar", "Bugünkü bar için AL/SAT kararı, sanal hesap"],
      ["rotation", "Momentum rotasyonu", "Her ay en güçlü hisseleri tutar; bugünün seçimleri"],
    ];
    $("[data-steps]", root).innerHTML = `
      <div class="card-head"><h2>Akış</h2></div>
      <ol class="steps">${steps.map(([key, title, text], i) => `
        <li class="${done[key] ? "done" : ""}"><a href="#" data-jump="${key}"><span class="n">${done[key] ? icon("check", "sm") : i + 1}</span>
          <span><b>${esc(title)}</b><small>${esc(text)}</small></span></a></li>`).join("")}</ol>`;
  }

  root.addEventListener("click", (event) => {
    const remove = event.target.closest("[data-remove]");
    if (remove) {
      setup.symbols = setup.symbols.filter((s) => s !== remove.dataset.remove);
      save();
      drawSetup();
      return;
    }
    if (event.target.closest("[data-targets]")) {
      setup.symbols = [...TARGETS];
      save();
      drawSetup();
      return;
    }
    if (event.target.closest("[data-one]")) {
      setup.symbols = setup.symbols.slice(0, 1);
      save();
      drawSetup();
      return;
    }
    const year = event.target.closest("[data-y]");
    if (year) {
      setup.years = Number(year.dataset.y);
      save();
      drawSetup();
      return;
    }
    const jump = event.target.closest("[data-jump]");
    if (jump) {
      event.preventDefault();
      $(`[data-${jump.dataset.jump}]`, root)?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (event.target.closest("[data-run-study]")) runStudy(event.target.closest("[data-run-study]"));
  });
  root.addEventListener("keydown", (event) => {
    const input = event.target.closest("[data-add]");
    if (input && (event.key === "Enter" || event.key === ",")) {
      event.preventDefault();
      input.value.split(/[\s,]+/).forEach(addSymbol);
    } else if (input && event.key === "Backspace" && !input.value && setup.symbols.length) {
      setup.symbols = setup.symbols.slice(0, -1);
      save();
      drawSetup();
      $("[data-add]", root)?.focus();
    }
  });
  root.addEventListener("change", (event) => {
    const form = event.target.closest("[data-setup-form]");
    if (!form) return;
    setup.start = form.elements.start.value || setup.start;
    setup.end = form.elements.end.value || setup.end;
    save();
    $("[data-timeline]", root).innerHTML = timeline();
  });

  // ------------------------------------------------------------- study

  async function runStudy(button) {
    if (!setup.symbols.length) return toast("En az bir hisse seçin.", "error");
    if (setup.start >= setup.end) return toast("Test başlangıcı bitişten önce olmalı.", "error");
    busy(button, true, "İnceleniyor…");
    $("[data-study]", root).classList.add("loading");
    try {
      memory.study = await api("/api/lab/study", { symbols: setup.symbols, cutoff: cutoff(), years: setup.years });
      drawStudy();
      drawSteps();
      $("[data-study]", root).scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
      $("[data-study]", root).classList.remove("loading");
    }
  }

  function correlationTable(corr) {
    if (!corr) return "";
    const cell = (v, i, j) => {
      if (i === j) return '<td class="num muted">—</td>';
      const strength = Math.max(0, Math.min(1, Math.abs(v ?? 0)));
      return `<td class="num heat" style="--heat:${(strength * 0.55).toFixed(2)}">${num(v, 2)}</td>`;
    };
    return `
      <h3 class="sub-title">Getiri korelasyonu <span class="muted small">ortalama ${num(corr.average, 2)} · ${corr.bars} bar</span></h3>
      <div class="table-wrap"><table class="corr"><thead><tr><th></th>${corr.symbols.map((s) => `<th class="num">${esc(s)}</th>`).join("")}</tr></thead>
      <tbody>${corr.matrix.map((row, i) => `<tr><th>${esc(corr.symbols[i])}</th>${row.map((v, j) => cell(v, i, j)).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="muted small">Yüksek korelasyon, hisselerin birlikte hareket ettiğini gösterir; sepet o kadar az çeşitlenir.</p>`;
  }

  function drawStudy() {
    const box = $("[data-study]", root);
    const r = memory.study;
    if (!r) {
      box.innerHTML = `<div class="card-head"><h2>1 · Sinyal araştırması</h2></div>
        <div class="empty"><b>Hisselerin geçmişini inceleyin</b>Seçilen hisselerin eğitim dönemindeki trendi, oynaklığı, likiditesi
        ve yaklaşık 35 sinyalden (trend, kırılım, momentum, dönüş, hacim, fiyat) sonra ne olduğu ölçülür.</div>`;
      return;
    }
    const symbols = r.symbols.map((s) => ({ symbol: s.symbol, ...s.behavior, active: s.active_now.length }));
    const ranking = r.ranking.filter((row) => row.events).slice(0, 12);
    box.innerHTML = `
      <div class="card-head"><h2>1 · Sinyal araştırması</h2>
        <span class="sub">${esc(fmtDate(r.start))} – ${esc(fmtDate(r.cutoff))} · ${r.symbols.length} hisse</span></div>
      <div class="table-wrap">${tableHtml([
        { key: "symbol", label: "Hisse", html: (s) => `<b>${esc(s.symbol)}</b>` },
        { key: "return_pct", label: "Getiri", num: true, html: (s) => pctCell(s.return_pct) },
        { key: "volatility_ann_pct", label: "Yıllık oynaklık", num: true, format: (v) => (Number.isFinite(v) ? `%${fmtNumber(v, 1)}` : "—") },
        { key: "max_drawdown_pct", label: "Maks. düşüş", num: true, format: (v) => pctText(v) },
        { key: "beta", label: "Beta", num: true, format: (v) => num(v) },
        { key: "tendency", label: "Eğilim", format: (v) => String(v).split(" (")[0] },
        { key: "liquidity", label: "Likidite", format: (v) => v?.bucket ?? "—" },
        { key: "active", label: "Bugün aktif sinyal", num: true },
      ], symbols)}</div>
      <h3 class="sub-title">En güçlü sinyaller <span class="muted small">10 bar sonra, dönem ortalamasına göre fazla getiri</span></h3>
      <div class="table-wrap">${tableHtml([
        { key: "label", label: "Sinyal", html: (row) => `${esc(row.label)}<div class="muted small">${esc(row.group)} · beklenen ${esc(row.bias)}</div>` },
        { key: "events", label: "Olay", num: true },
        { key: "excess_pct", label: "Fazla getiri", num: true, html: (row) => (Number.isFinite(row.excess_pct) ? `<span class="${tone(row.excess_pct)}-text">${signedPct(row.excess_pct)} puan</span>` : "—") },
        { key: "score", label: "Skor", num: true, html: (row) => (Number.isFinite(row.score) ? `<span class="${row.score > 0 ? "up" : "down"}-text">${row.score > 0 ? "▲" : "▼"} ${fmtNumber(Math.abs(row.score), 2)}</span>` : "—") },
        { key: "agreeing_symbols", label: "Destekleyen", num: true, format: (v) => `${v}/${r.symbols.length}` },
        { key: "active_now", label: "Şu an", html: (row) => row.active_now.map((s) => `<span class="chip tag sky">${esc(s)}</span>`).join(" ") || '<span class="muted">—</span>' },
      ], ranking)}</div>
      <p class="muted small">Skor: sinyalin beklenen yönde ürettiği fazla getiri (▲ beklendiği gibi, ▼ tersine çalıştı).
        Destekleyen: anlamlı ve beklenen yönde çalıştığı hisse sayısı.</p>
      ${correlationTable(r.correlation)}
      <div class="console" style="margin-top:12px">${r.notes.map((n) => `<div>${esc(n)}</div>`).join("")}</div>`;
  }

  // ------------------------------------------------------------- author

  let authorAbort = null;

  function drawAuthor() {
    const box = $("[data-author]", root);
    const a = memory.author;
    const running = Boolean(authorAbort);
    const stages = a?.stages ?? [];
    const result = a?.result;
    const code = result?.source ?? a?.text ?? "";
    const lines = code.replace(/^[\s\S]*?```pine[^\n]*\n/, "").replace(/```[\s\S]*$/, "").split("\n");
    box.innerHTML = `
      <div class="card-head"><h2>2 · Pine Script oluştur</h2>${a?.model ? `<span class="sub">${esc(a.model)}</span>` : ""}
        <span class="spacer"></span>${running ? `<button class="btn small" type="button" data-author-stop>${icon("stop", "sm")} Durdur</button>` : ""}</div>
      <form class="form" data-author-form>
        <label class="field">Hedef (isteğe bağlı)
          <textarea name="goal" rows="2" maxlength="1000" placeholder="Boş bırakılırsa: trend takibi, her hissede çeyrekte en az 2 işlem. ör. En fazla %8 zarar, daha uzun tutma">${esc(store.get("strategy.goal", ""))}</textarea></label>
        <div class="row">
          <label class="field">Script adı (isteğe bağlı)<input name="name" maxlength="48" placeholder="otomatik: ai_enkai_…" pattern="[a-z0-9][a-z0-9_-]*"></label>
          <div class="field"><span>&nbsp;</span><button class="btn violet block" type="submit" ${running ? "disabled" : ""}>${icon("sparkle", "sm")} Yapay zekaya yazdır</button></div>
        </div>
      </form>
      <p class="muted small" style="margin:6px 0 0">Model yalnızca eğitim dönemi araştırmasını görür (hisseler "Hisse A, B…", tarih yok).
        Yazdığı script derlenir, eğitim verisinde denenir ve hata varsa düzeltmesi istenir. Ayarlar'daki asistan modeli kullanılır.</p>
      ${stages.length ? `<ol class="stage-list">${stages.map((s, i) => `
        <li class="${i === stages.length - 1 && running ? "active" : "done"}">${i === stages.length - 1 && running ? '<span class="spin"></span>' : icon("check", "sm")}${esc(s)}</li>`).join("")}</ol>` : ""}
      ${(a?.reviews ?? []).length ? `<div class="console" style="margin-top:10px">${a.reviews.map((r) => `
        <div class="${r.problem ? "warn" : ""}">${r.attempt}. deneme: ${r.problem ? esc(r.problem) : `geçti${r.entries ? ` · eğitimde giriş sayısı: ${esc(Object.entries(r.entries).map(([k, v]) => `${k} ${v.entries}`).join(", "))}` : ""}`}</div>`).join("")}</div>` : ""}
      ${a?.error ? `<div class="console" style="margin-top:10px"><div class="bad">${esc(a.error)}</div></div>` : ""}
      ${code ? `<div class="codeblock gen-code"><pre>${lines.map((line) => `<span class="ln">${highlightLine(line)}</span>`).join("\n")}</pre></div>` : ""}
      ${result ? `
        <div class="row-flex" style="margin-top:12px">
          <span class="chip tag green">${icon("check", "sm")} ${esc(result.name)} kaydedildi</span>
          <span class="muted small">${result.attempts}. denemede · $${fmtNumber(result.usage?.cost ?? 0, 4)}</span>
          <span class="spacer"></span>
          <a class="btn small" href="#/lab/${encodeURIComponent(result.name)}">${icon("code", "sm")} Editörde aç</a>
          <button class="btn small primary" type="button" data-use-script="${esc(result.name)}">${icon("play", "sm")} Kör testte kullan</button>
        </div>
        ${result.explanation ? `<div class="md" style="margin-top:12px">${renderMarkdown(result.explanation).html}</div>` : ""}` : ""}`;
  }

  async function runAuthor(form) {
    if (!setup.symbols.length) return toast("En az bir hisse seçin.", "error");
    const goal = form.elements.goal.value.trim();
    store.set("strategy.goal", goal);
    memory.author = { stages: [], reviews: [], text: "" };
    authorAbort = new AbortController();
    drawAuthor();
    let pending = 0;
    const redraw = () => {
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = 0;
        drawAuthor();
      });
    };
    try {
      await stream("/api/lab/author", {
        symbols: setup.symbols, cutoff: cutoff(), years: setup.years, goal: goal || null,
        name: form.elements.name.value.trim() || null,
      }, (event) => {
        const a = memory.author;
        if (event.type === "model") a.model = event.model;
        else if (event.type === "stage") {
          if (event.stage === "fixing") a.text = "";
          a.stages.push(event.message || STAGES[event.stage] || event.stage);
        } else if (event.type === "study") {
          memory.study = event.study;
          drawStudy();
        } else if (event.type === "text") a.text += event.text;
        else if (event.type === "review") a.reviews.push(event);
        else if (event.type === "result") {
          a.result = event.result;
          memory.scripts = null;
          blindForm.script = event.result.name;
        } else if (event.type === "error") a.error = event.message;
        else if (event.type === "stopped") a.error = "Durduruldu.";
        redraw();
      }, authorAbort.signal);
    } catch (error) {
      if (error.name !== "AbortError") {
        memory.author.error = error.message;
        toast(error.message, "error");
      }
    } finally {
      authorAbort = null;
      drawAuthor();
      drawSteps();
      drawBlind();
    }
  }

  // ------------------------------------------------------------- blind test

  const blindForm = {
    script: store.get("strategy.script", "supertrend_sik"),
    mode: store.get("strategy.mode", "ai"),
    model: store.get("strategy.model", ""),
    review: store.get("strategy.review", 0),
    stop: store.get("strategy.stop", ""),
    cash: store.get("strategy.cash", 100000),
    cache: store.get("strategy.cache", true),
    learning: store.get("strategy.learning", "off"),
    rounds: store.get("strategy.rounds", 4),
    interval: store.get("strategy.interval", "1d"),
  };
  let blindAbort = null;
  let blindJob = null;

  async function loadOptions() {
    const [scripts, presets] = await Promise.all([
      memory.scripts ? Promise.resolve(memory.scripts) : api("/api/scripts").catch(() => []),
      memory.presets ? Promise.resolve(memory.presets) : api("/api/ai/decision-models").catch(() => null),
    ]);
    memory.scripts = scripts;
    memory.presets = presets;
    drawBlind();
    drawLive();
  }

  function scriptOptions() {
    const list = (memory.scripts ?? []).filter((s) => s.kind === "strategy");
    if (blindForm.script && !list.some((s) => s.name === blindForm.script)) list.unshift({ name: blindForm.script, title: blindForm.script });
    const ai = list.filter((s) => s.name.startsWith("ai_"));
    const rest = list.filter((s) => !s.name.startsWith("ai_"));
    const option = (s) => `<option value="${esc(s.name)}" ${s.name === blindForm.script ? "selected" : ""}>${esc(s.title && s.title !== s.name ? `${s.name} · ${s.title}` : s.name)}</option>`;
    return `${ai.length ? `<optgroup label="Yapay zekanın yazdıkları">${ai.map(option).join("")}</optgroup>` : ""}
      <optgroup label="Scriptler">${rest.map(option).join("")}</optgroup>`;
  }

  function modelOptions() {
    const p = memory.presets;
    const current = p?.current;
    const presets = p?.presets ?? [];
    const label = (row) => `${row.label} · ${row.model}${row.prompt_price != null ? ` · $${fmtNumber(row.prompt_price, 2)}/$${fmtNumber(row.completion_price ?? 0, 2)}` : ""}`;
    const custom = current && !presets.some((row) => row.key === current) ? current : null;
    return `<option value="" ${!blindForm.model ? "selected" : ""}>Ayarlardaki model${current ? ` (${esc(presets.find((row) => row.key === current)?.label ?? current)})` : ""}</option>
      ${presets.map((row) => `<option value="${esc(row.key)}" ${blindForm.model === row.key ? "selected" : ""}>${esc(label(row))}</option>`).join("")}
      ${custom ? `<option value="${esc(custom)}" ${blindForm.model === custom ? "selected" : ""}>${esc(custom)}</option>` : ""}`;
  }

  function blindBody() {
    return {
      symbols: setup.symbols,
      start: setup.start,
      end: setup.end,
      years: setup.years,
      script: blindForm.script,
      mode: blindForm.mode,
      review_every: Number(blindForm.review),
      stop_loss_pct: blindForm.stop ? Number(blindForm.stop) : null,
      cash: Number(blindForm.cash) || 100000,
      decision_model: blindForm.model || null,
      use_cache: blindForm.cache,
      learning: blindForm.mode === "ai" ? blindForm.learning : "off",
      rounds: Number(blindForm.rounds) || 4,
      interval: blindForm.interval,
    };
  }

  function drawBlind() {
    const box = $("[data-blind]", root);
    const b = memory.blind;
    const running = Boolean(blindAbort);
    const ai = blindForm.mode === "ai";
    box.innerHTML = `
      <div class="card-head"><h2>3 · Kör backtest</h2><span class="sub">${esc(setup.symbols.join(", "))} · ${esc(fmtDate(setup.start))} – ${esc(fmtDate(setup.end))}</span>
        <span class="spacer"></span>${running ? `<button class="btn small" type="button" data-blind-stop>${icon("stop", "sm")} Durdur</button>` : ""}</div>
      <form class="form" data-blind-form>
        <div class="row">
          <label class="field">Strateji scripti<select name="script">${scriptOptions()}</select></label>
          <div class="field">Kararı veren
            <div class="segmented block" data-mode>
              <button type="button" data-m="ai" class="${ai ? "active" : ""}">${icon("sparkle", "sm")} AI + sinyaller</button>
              <button type="button" data-m="signals" class="${ai ? "" : "active"}">Sadece sinyaller</button></div></div>
        </div>
        ${ai ? `<div class="row">
          <label class="field">Karar modeli<select name="model">${modelOptions()}</select>
            <span class="hint">Hızlı modeller; <a href="#/ayarlar" style="text-decoration:underline">Ayarlar</a>'dan hız testi yapıp varsayılanı değiştirebilirsiniz.</span></label>
          <label class="field">Gözden geçirme<select name="review">${REVIEWS.map(([v, l]) => `<option value="${v}" ${Number(blindForm.review) === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="hint">Pozisyondayken ya da giriş koşulu sürerken model bu aralıkla yeniden sorulur.</span></label>
        </div>
        <div class="row">
          <label class="field">Öğrenme<select name="learning">${LEARNING.map(([v, l]) => `<option value="${v}" ${blindForm.learning === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="hint">Model yalnızca sonucu belli olmuş kararlarından öğrenir; her 6 sonuçta asistan modeli ders notu yazar.</span></label>
          ${blindForm.learning === "rounds" ? `<label class="field">Tur sayısı<select name="rounds">${ROUNDS.map((n) => `<option value="${n}" ${Number(blindForm.rounds) === n ? "selected" : ""}>${n} pencere</option>`).join("")}</select>
            <span class="hint">Test dönemi eşit pencerelere bölünür; script her pencere sonunda yalnızca o güne kadarki veriyle geliştirilir ve sonraki pencerede kullanılır.</span></label>` : ""}
        </div>` : ""}
        <div class="row">
          <label class="field">Zaman dilimi<select name="interval">${INTERVALS.map(([v, l]) => `<option value="${v}" ${blindForm.interval === v ? "selected" : ""}>${l}</option>`).join("")}</select>
            <span class="hint">${blindForm.interval === "1h" ? "Saatlik veri yalnızca son 2 yıl için var; eğitim ve test bu aralığa sığmalı. Karar sayısı ve maliyet günlüğün yaklaşık 9 katıdır." : "Karar her barın kapanışında verilir, emir sonraki barın açılışında gerçekleşir."}</span></label>
          <span></span>
        </div>
        <div class="row">
          <label class="field">Zarar durdur % (isteğe bağlı)<input name="stop" type="number" min="0.5" max="49" step="0.5" value="${esc(blindForm.stop)}" placeholder="script belirler"></label>
          <label class="field">Sermaye (TL, hisselere eşit bölünür)<input name="cash" type="number" min="1000" step="1000" value="${esc(blindForm.cash)}"></label>
        </div>
        ${ai ? `<label class="check"><input type="checkbox" name="cache" ${blindForm.cache ? "checked" : ""}> Aynı durum için önceki kararları kullan (tekrarlanabilir, ücretsiz)</label>` : ""}
        <div class="row-flex">
          <button class="btn" type="button" data-estimate ${running ? "disabled" : ""}>${icon("clock", "sm")} Tahmin et</button>
          <button class="btn primary" type="submit" ${running ? "disabled" : ""}>${icon("play", "sm")} Kör testi başlat</button>
          <span class="muted small" data-estimate-text>${b?.estimate ? estimateText(b.estimate) : ""}</span>
        </div>
      </form>
      <div data-blind-live>${running || b?.decisions?.length ? liveLog() : ""}</div>
      ${b?.error ? `<div class="console" style="margin-top:10px"><div class="bad">${esc(b.error)}</div></div>` : ""}
      <div data-blind-result></div>`;
    if (b?.result) showBlind($("[data-blind-result]", box), b.result);
  }

  function estimateText(e) {
    if (e.mode !== "ai") return `${e.symbols.reduce((n, s) => n + s.entries + s.exits, 0)} sinyal olayı · yapay zeka çağrısı yok`;
    const cost = e.cost_usd == null ? "maliyet bilinmiyor" : `~$${fmtNumber(e.cost_usd, 3)}`;
    const l = e.learning;
    const extra = l ? ` · + ~${l.reflections} ders notu${l.revisions ? `, ${l.revisions} script revizyonu` : ""} (${l.coach_model ?? "asistan modeli"})` : "";
    return `En fazla ~${e.decisions} karar · ${cost} · ~${Math.max(1, Math.round(e.seconds / 60))} dk · ${e.model?.id ?? ""}${extra}`;
  }

  function liveLog() {
    const b = memory.blind ?? {};
    // While running: newest arrivals first; afterwards: by bar time.
    const list = b.result
      ? (b.decisions ?? []).slice().sort((x, y) => y.time.localeCompare(x.time) || x.symbol.localeCompare(y.symbol)).slice(0, 60)
      : (b.decisions ?? []).slice(-60).reverse();
    const stats = b.decisions ?? [];
    const timed = stats.filter((d) => !d.cached && !d.error);
    const avg = timed.length ? Math.round(timed.reduce((n, d) => n + d.latency_ms, 0) / timed.length) : null;
    const progress = b.plan?.decisions ? Math.min(100, (stats.length / b.plan.decisions) * 100) : null;
    const brief = b.strategy?.brief;
    return `
      <div class="live-head">
        ${blindAbort ? `<span class="spin"></span><b>${esc(b.stage ?? "Çalışıyor")}</b>` : `<b>Kararlar</b>`}
        ${b.model ? `<span class="chip tag violet">${esc(b.model.preset ?? b.model.name ?? b.model.id)}</span>` : ""}
        ${b.round && blindAbort ? `<span class="chip tag">Tur ${b.round.round}/${b.round.rounds} · ${esc(b.round.script)}</span>` : ""}
        <span class="spacer"></span>
        <span class="muted small">${stats.length} karar · AL ${stats.filter((d) => d.action === "buy").length} · SAT ${stats.filter((d) => d.action === "sell").length}${avg != null ? ` · ort. ${avg} ms` : ""}</span>
      </div>
      ${progress != null && blindAbort ? `<div class="progress"><i style="width:${progress.toFixed(1)}%"></i></div>` : ""}
      ${brief?.ozet && blindAbort ? `<p class="muted small" style="margin:6px 0">Strateji (${esc(brief.tur)}): ${esc(brief.ozet)}</p>` : ""}
      ${b.lessons?.length && blindAbort ? `<div class="notice" style="margin:8px 0">${icon("sparkle", "sm")} <b>Ders notları</b>
        <ul style="margin:4px 0 0 18px">${b.lessons.map((lesson) => `<li>${esc(lesson)}</li>`).join("")}</ul></div>` : ""}
      <div class="table-wrap decision-log">${tableHtml([
        { key: "time", label: "Bar", format: tipTime },
        { key: "symbol", label: "Hisse" },
        { key: "event", label: "Olay" },
        { key: "label", label: "Karar", html: (d) => decisionChip(d.label) },
        { key: "confidence", label: "Güven", num: true, format: (v) => (v == null ? "—" : `%${v}`) },
        { key: "reason", label: "Gerekçe", html: (d) => (d.error ? `<span class="error">${esc(d.error)}</span>` : esc(d.reason)) },
        { key: "latency_ms", label: "Süre", num: true, format: (v, d) => (d.cached ? "önbellek" : `${v} ms`) },
      ], list)}</div>`;
  }

  const outcomeCell = (d) => (d.outcome_pct == null ? "—" : `${pill(d.outcome_pct)}<div class="muted small">${esc(d.outcome_kind ?? "")}</div>`);

  function revisionCell(revision) {
    if (!revision) return "—";
    if (!revision.ok) return `<span class="muted small">Script korundu${revision.problem ? `: ${esc(revision.problem)}` : ""}</span>`;
    return `<button class="btn small" type="button" data-use-script="${esc(revision.name)}">${esc(revision.name)}</button>
      ${revision.explanation ? `<details class="more"><summary>Ne değişti?</summary><div class="md">${renderMarkdown(revision.explanation)}</div></details>` : ""}`;
  }

  function learningHtml(r) {
    const l = r.learning;
    const brief = r.ai?.strategy?.strateji_ozeti;
    const record = r.ai?.strategy?.strateji_gecmisi;
    const parts = [];
    if (brief?.ozet) {
      parts.push(`<p class="muted small" style="margin:0 0 6px">Karar modeline anlatılan strateji (${esc(brief.tur)}): ${esc(brief.ozet)}
        ${record?.islem ? ` · Test öncesi ${record.islem} işlem, kazançlı %${fmtNumber(record["kazancli_%"] ?? 0, 0)}, ortalama ${signedPct(record["ort_getiri_%"])}%` : ""}</p>`);
    }
    if (l) {
      parts.push(`<p class="muted small" style="margin:0 0 6px">${l.resolved} kararın sonucu günlüğe girdi · ${l.history.length} ders notu güncellemesi · $${fmtNumber(l.cost_usd ?? 0, 4)}</p>
        ${l.lessons.length ? `<ol style="margin:0 0 0 18px">${l.lessons.map((lesson) => `<li>${esc(lesson)}</li>`).join("")}</ol>` : `<p class="muted small">Ders notu yazılmadı.</p>`}`);
    }
    return parts.length ? `<h3 class="sub-title">Öğrenme</h3>${parts.join("")}` : "";
  }

  function roundsHtml(r) {
    if (!r.rounds?.length) return "";
    return `<h3 class="sub-title">Turlar</h3>
      <div class="table-wrap">${tableHtml([
        { key: "round", label: "Tur", num: true },
        { key: "start", label: "Pencere", html: (w) => `${esc(fmtDate(w.start))} – ${esc(fmtDate(w.end))}` },
        { key: "script", label: "Script", html: (w) => `<code>${esc(w.script)}</code>` },
        { key: "ai", label: "Yapay zeka", num: true, html: (w) => (w.ai ? `${pctCell(w.ai.return_pct)}<div class="muted small">${w.ai.trades} işlem</div>` : "—") },
        { key: "signals", label: "Sinyaller", num: true, html: (w) => `${pctCell(w.signals?.return_pct)}<div class="muted small">${w.signals?.trades ?? 0} işlem</div>` },
        { key: "hold_return_pct", label: "Al-tut", num: true, html: (w) => pctCell(w.hold_return_pct) },
        { key: "benchmark_return_pct", label: "XU100", num: true, html: (w) => pctCell(w.benchmark_return_pct) },
        { key: "revision", label: "Sonraki tur için", html: (w) => revisionCell(w.revision) },
      ], r.rounds)}</div>`;
  }

  async function estimate(button) {
    busy(button, true, "Hesaplanıyor…");
    try {
      const e = await api("/api/lab/estimate", blindBody());
      memory.blind = { ...(memory.blind ?? {}), estimate: e };
      const text = $("[data-estimate-text]", root);
      if (text) text.textContent = estimateText(e);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
    }
  }

  async function runBlind() {
    if (!setup.symbols.length) return toast("En az bir hisse seçin.", "error");
    if (!blindForm.script) return toast("Bir strateji scripti seçin.", "error");
    const estimateResult = memory.blind?.estimate;
    memory.blind = { decisions: [], estimate: estimateResult, stage: "Başlıyor" };
    blindAbort = new AbortController();
    drawBlind();
    let pending = 0;
    const redrawLog = () => {
      if (pending) return;
      pending = requestAnimationFrame(() => {
        pending = 0;
        const live = $("[data-blind-live]", root);
        if (live) live.innerHTML = liveLog();
      });
    };
    try {
      await stream("/api/lab/blind", blindBody(), (event) => {
        const b = memory.blind;
        if (event.type === "start") blindJob = event.job_id;
        else if (event.type === "model") b.model = event.model;
        else if (event.type === "coach") b.coach = event.model;
        else if (event.type === "stage") b.stage = event.message || STAGES[event.stage];
        else if (event.type === "plan") b.plan = event;
        else if (event.type === "strategy") b.strategy = { brief: event.strateji_ozeti, record: event.strateji_gecmisi };
        else if (event.type === "lesson") b.lessons = event.lessons;
        else if (event.type === "round") b.round = event;
        else if (event.type === "round_end") (b.rounds ??= []).push(event.row);
        else if (event.type === "revision_review") b.stage = `Script düzeltiliyor (${event.attempt}. deneme)`;
        else if (event.type === "decision") b.decisions.push(event.decision);
        else if (event.type === "result") b.result = event.result;
        else if (event.type === "error") b.error = event.message;
        else if (event.type === "stopped") b.error = "Durduruldu.";
        redrawLog();
      }, blindAbort.signal);
    } catch (error) {
      if (error.name !== "AbortError") {
        memory.blind.error = error.message;
        toast(error.message, "error");
      }
    } finally {
      blindAbort = null;
      blindJob = null;
      // Revised scripts were saved: list them in the script picker.
      if (memory.blind?.rounds?.length) {
        memory.scripts = null;
        loadOptions();
      }
      drawBlind();
      drawSteps();
      $("[data-blind-result]", root)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function showBlind(host, r) {
    const ai = r.ai;
    const main = ai ?? r.signals;
    const audit = r.audit;
    host.innerHTML = `
      ${audit ? `<div class="audit ${audit.blind ? "ok" : "bad"}">
        <div class="audit-head">${icon(audit.blind ? "check" : "alert")}<b>${audit.blind ? "Kör test doğrulandı" : "Kör test kuralı ihlali"}</b>
          <span class="muted small">${audit.decisions} karar · ${audit.rechecks} yeniden hesaplama · ${audit.mismatches} uyuşmazlık · ${audit.leaks} sızıntı</span></div>
        <ul>${audit.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></div>` : ""}
      <div class="tiles" style="margin-top:12px">
        ${ai ? tile("Yapay zeka getirisi", pctText(ai.return_pct), { cls: `${tone(ai.return_pct)}-text`, note: `Son ${fmtMoney(ai.equity_final, 0)}` }) : ""}
        ${tile("Sadece sinyaller", pctText(r.signals.return_pct), { cls: ai ? "" : `${tone(r.signals.return_pct)}-text`, note: `${r.signals.trades} işlem` })}
        ${tile("Al ve tut", pctText(r.hold.return_pct))}
        ${r.benchmark ? tile("XU100", pctText(r.benchmark.return_pct)) : ""}
        ${tile("En büyük düşüş", pctText(main.max_drawdown_pct), { note: ai ? `Sinyaller ${pctText(r.signals.max_drawdown_pct)}` : "" })}
        ${tile("Sharpe", main.sharpe == null ? "—" : fmtNumber(main.sharpe, 2), { note: ai ? `Sinyaller ${r.signals.sharpe == null ? "—" : fmtNumber(r.signals.sharpe, 2)}` : "" })}
        ${tile("İşlem", String(main.trades), { note: main.win_rate_pct == null ? "" : `Kazançlı %${fmtNumber(main.win_rate_pct, 0)}` })}
        ${ai ? tile("Karar", String(ai.decisions), { note: `AL ${ai.buys} · SAT ${ai.sells} · bekle ${ai.holds}` }) : ""}
        ${ai ? tile("Ort. karar süresi", ai.avg_latency_ms == null ? "önbellek" : `${fmtNumber(ai.avg_latency_ms, 0)} ms`, { note: `$${fmtNumber(ai.cost_usd ?? 0, 4)} · ${ai.errors} hata` }) : ""}
        ${r.initial_signals ? tile("İlk script, sinyaller", pctText(r.initial_signals.return_pct), { note: `${r.initial_signals.trades} işlem · revizyonsuz` }) : ""}
      </div>
      ${main.trades < 10 ? `<p class="notice" style="margin:12px 0 0">${icon("alert", "sm")} İşlem sayısı az; sonuç istatistiksel olarak zayıf.</p>` : ""}
      ${roundsHtml(r)}
      ${learningHtml(r)}
      <div class="card-head" style="margin-top:16px"><h3>Özsermaye</h3><span class="spacer"></span>
        <button class="icon-btn sm" type="button" data-curve-toggle title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button></div>
      <div data-curve></div>
      <h3 class="sub-title">Hisse bazında</h3>
      <div class="table-wrap">${tableHtml([
        { key: "symbol", label: "Hisse", html: (s) => `<b>${esc(s.symbol)}</b>` },
        ...(ai ? [{ key: "ai_return_pct", label: "Yapay zeka", num: true, html: (s) => pctCell(s.ai_return_pct) },
          { key: "ai_trades", label: "AI işlem", num: true }] : []),
        { key: "signals_return_pct", label: "Sinyaller", num: true, html: (s) => pctCell(s.signals_return_pct) },
        { key: "signals_trades", label: "Sinyal işlem", num: true },
        { key: "hold_return_pct", label: "Al ve tut", num: true, html: (s) => pctCell(s.hold_return_pct) },
      ], r.symbols)}</div>
      ${ai ? `<details class="more" style="margin-top:12px"><summary>Tüm kararlar (${r.decisions.length})</summary>
        <div class="table-wrap">${tableHtml([
          { key: "time", label: "Bar", format: tipTime },
          { key: "symbol", label: "Hisse" },
          { key: "event", label: "Olay" },
          { key: "signals", label: "Aktif sinyaller", format: (v) => (v.length ? v.join("; ") : "—") },
          { key: "label", label: "Karar", html: (d) => decisionChip(d.label) },
          { key: "confidence", label: "Güven", num: true, format: (v) => (v == null ? "—" : `%${v}`) },
          { key: "reason", label: "Gerekçe", html: (d) => (d.error ? `<span class="error">${esc(d.error)}</span>` : esc(d.reason)) },
          ...(r.learning ? [{ key: "outcome_pct", label: "Sonuç", num: true, html: outcomeCell }] : []),
          { key: "data_end", label: "Gördüğü son bar", format: tipTime },
        ], r.decisions.slice().reverse())}</div></details>` : ""}
      <details class="more" style="margin-top:8px"><summary>İşlemler (${(ai ? r.trades : r.signal_trades).length})</summary>
        <div class="table-wrap">${tableHtml([
          { key: "symbol", label: "Hisse" },
          { key: "entry_time", label: "Giriş", format: tipTime },
          { key: "exit_time", label: "Çıkış", format: tipTime },
          { key: "qty", label: "Lot", num: true },
          { key: "entry_price", label: "Giriş fiyatı", num: true, format: (v) => num(v) },
          { key: "exit_price", label: "Çıkış fiyatı", num: true, format: (v) => num(v) },
          { key: "return_pct", label: "Getiri", num: true, html: (t) => pill(t.return_pct) },
          { key: "exit_reason", label: "Çıkış nedeni" },
        ], (ai ? r.trades : r.signal_trades).slice().reverse())}</div></details>`;
    const series = [
      ai && { name: "Yapay zeka", color: token("--accent"), points: ai.equity },
      { name: "Sadece sinyaller", color: token("--series-2"), points: r.signals.equity },
      { name: "Al ve tut", color: slotColor(2), points: r.hold.equity },
      r.benchmark && { name: "XU100", color: slotColor(3), points: r.benchmark.equity, dash: true },
    ].filter(Boolean);
    const toggle = chartWithTable($("[data-curve]", host),
      (slot) => lineChart(slot, { series, height: 260, format: (v) => fmtMoney(v, 0), label: "Kör test özsermayesi" }),
      () => tableHtml([
        { key: "t", label: "Tarih", format: tipTime },
        ...series.map((s, k) => ({ key: `s${k}`, label: s.name, num: true, format: (v) => (v == null ? "—" : fmtMoney(v)) })),
      ], series[0].points.map((p, i) => Object.fromEntries([["t", p.t], ...series.map((s, k) => [`s${k}`, s.points[i]?.v])])).slice(-120).reverse()));
    $("[data-curve-toggle]", host).onclick = (event) => {
      event.currentTarget.innerHTML = icon(toggle() ? "chart" : "table");
    };
  }

  // ------------------------------------------------------------- live decision

  function drawLive() {
    const box = $("[data-live]", root);
    const live = memory.live;
    box.innerHTML = `
      <div class="card-head"><h2>4 · Canlı karar</h2><span class="sub">Bugünkü son bar · sanal hesap</span></div>
      <p class="muted small" style="margin:0 0 12px">Seçili script ve karar modeliyle her hisse için şu anki duruma karar alınır. Sanal hesapta
        tutulan hisseler için SAT/TUT, diğerleri için AL/BEKLE sorulur. Emir otomatik verilmez; isterseniz emir fişini açarsınız.</p>
      <button class="btn violet" type="button" data-live-run>${icon("zap", "sm")} Şimdi karar al</button>
      ${live ? `<div class="row-flex" style="margin-top:12px"><span class="muted small">${esc(live.model?.preset ?? live.model?.id ?? "")} · ${esc(live.model?.id ?? "")}</span></div>
        ${live.lessons?.length ? `<div class="notice" style="margin:8px 0">${icon("sparkle", "sm")} <b>Son öğrenen kör testin ders notları kullanıldı</b>
          <ul style="margin:4px 0 0 18px">${live.lessons.map((lesson) => `<li>${esc(lesson)}</li>`).join("")}</ul></div>` : ""}
        <div class="table-wrap" style="margin-top:8px">${tableHtml([
          { key: "symbol", label: "Hisse", html: (d) => `<b>${esc(d.symbol)}</b>${d.held ? `<div class="muted small">${d.held} lot</div>` : ""}` },
          { key: "time", label: "Bar", format: tipTime },
          { key: "close", label: "Kapanış", num: true, format: (v) => num(v) },
          { key: "event", label: "Olay" },
          { key: "label", label: "Karar", html: (d) => decisionChip(d.label) },
          { key: "confidence", label: "Güven", num: true, format: (v) => (v == null ? "—" : `%${v}`) },
          { key: "reason", label: "Gerekçe", html: (d) => (d.error ? `<span class="error">${esc(d.error)}</span>` : esc(d.reason)) },
          { key: "latency_ms", label: "Süre", num: true, format: (v) => `${v} ms` },
          { key: "act", label: "", html: (d) => (d.action !== "hold" ? `<button class="btn small" type="button" data-order="${esc(d.symbol)}">Emir fişi</button>` : "") },
        ], live.decisions)}</div>` : ""}`;
  }

  async function runLive(button) {
    if (!setup.symbols.length) return toast("En az bir hisse seçin.", "error");
    busy(button, true, "Karar veriliyor…");
    try {
      memory.live = await api("/api/lab/live", {
        symbols: setup.symbols, script: blindForm.script, decision_model: blindForm.model || null, years: setup.years,
      });
      drawLive();
      drawSteps();
    } catch (error) {
      toast(error.message, "error");
      busy(button, false);
    }
  }

  // ------------------------------------------------------------- momentum rotation

  const rotationForm = {
    symbols: store.get("strategy.rotation.symbols", LARGE_CAPS.join(", ")),
    lookback: store.get("strategy.rotation.lookback", 6),
    top: store.get("strategy.rotation.top", 5),
    absolute: store.get("strategy.rotation.absolute", false),
    market: store.get("strategy.rotation.market", false),
  };

  function drawRotation() {
    const box = $("[data-rotation]", root);
    const r = memory.rotation;
    const option = (value, current, label) => `<option value="${value}" ${Number(current) === value ? "selected" : ""}>${label}</option>`;
    box.innerHTML = `
      <div class="card-head"><h2>5 · Momentum rotasyonu</h2><span class="sub">${esc(fmtDate(setup.start))} – ${esc(fmtDate(setup.end))}</span></div>
      <p class="muted small" style="margin:0 0 12px">Her ayın son kapanışında hisseler son aylardaki getirilerine göre sıralanır; en güçlüler ertesi gün açılışta
        alınır, listeden düşenler satılır. Hisseler arası göreli güç (momentum) BIST'te 2005'ten beri anlamlı bulunan bir etkidir. Sıralama yalnızca
        o güne kadarki fiyatlarla yapılır; yapay zeka çağrısı yoktur.</p>
      <form class="form" data-rotation-form>
        <label class="field">Hisse listesi (virgülle, 2-30 hisse)<textarea name="symbols" rows="2" spellcheck="false">${esc(rotationForm.symbols)}</textarea>
          <span class="hint">Varsayılan liste BIST'in 20 büyük şirketi. Bugünün büyüklerinden seçmek geçmiş sonuçları iyimser gösterir: dönem içinde endeksten düşenler listede yok.</span></label>
        <div class="row">
          <label class="field">Momentum ufku<select name="lookback">${[6, 9, 12].map((m) => option(m, rotationForm.lookback, `${m} ay (son ay hariç)`)).join("")}</select></label>
          <label class="field">Tutulacak hisse<select name="top">${[3, 5, 7].map((n) => option(n, rotationForm.top, `${n} hisse`)).join("")}</select></label>
        </div>
        <label class="check"><input type="checkbox" name="absolute" ${rotationForm.absolute ? "checked" : ""}> Yalnızca 200 günlük ortalamasının üstündeki hisseleri al (değilse o pay nakitte kalır)</label>
        <label class="check"><input type="checkbox" name="market" ${rotationForm.market ? "checked" : ""}> XU100 200 günlük ortalamasının altındayken tamamen nakde geç</label>
        <div class="row-flex"><button class="btn primary" type="submit" data-rotation-run>${icon("play", "sm")} Rotasyonu test et</button></div>
      </form>
      <div data-rotation-result></div>`;
    if (r) showRotation($("[data-rotation-result]", box), r);
  }

  function showRotation(host, r) {
    const m = r.rotation;
    const current = r.current;
    host.innerHTML = `
      <div class="tiles" style="margin-top:12px">
        ${tile("Rotasyon getirisi", pctText(m.return_pct), { cls: `${tone(m.return_pct)}-text`, note: `Son ${fmtMoney(m.equity_final, 0)}` })}
        ${tile("Eşit ağırlıklı al-tut", pctText(r.equal.return_pct), { note: `${r.config.symbols.length} hisse` })}
        ${r.benchmark ? tile("XU100", pctText(r.benchmark.return_pct)) : ""}
        ${tile("En büyük düşüş", pctText(m.max_drawdown_pct))}
        ${tile("Sharpe", m.sharpe == null ? "—" : fmtNumber(m.sharpe, 2))}
        ${tile("İşlem", String(m.trades), { note: m.win_rate_pct == null ? "" : `Kazançlı %${fmtNumber(m.win_rate_pct, 0)}` })}
        ${tile("Ay", String(r.rebalances.length), { note: `Piyasada %${fmtNumber(m.exposure_pct ?? 0, 0)}` })}
      </div>
      <div class="card-head" style="margin-top:16px"><h3>Özsermaye</h3></div>
      <div data-rotation-curve></div>
      <h3 class="sub-title">Bu ay tutulacaklar <span class="muted small">(${esc(tipTime(current.as_of))} kapanışına göre, ertesi açılışta)</span></h3>
      ${current.picks.length ? `<div class="table-wrap">${tableHtml([
        { key: "symbol", label: "Hisse", html: (p) => `<b>${esc(p.symbol)}</b>` },
        { key: "momentum_pct", label: "Momentum", num: true, html: (p) => pctCell(p.momentum_pct) },
        { key: "above_200", label: "200 gün üstünde", format: (v) => (v ? "evet" : "hayır") },
        { key: "act", label: "", html: (p) => `<button class="btn small" type="button" data-order="${esc(p.symbol)}">Emir fişi</button>` },
      ], current.picks)}</div>` : `<p class="notice">${icon("alert", "sm")} Filtreler nedeniyle bu ay nakitte kalınıyor.</p>`}
      <details class="more" style="margin-top:8px"><summary>Tüm sıralama (${current.ranking.length})</summary>
        <div class="table-wrap">${tableHtml([
          { key: "symbol", label: "Hisse" },
          { key: "momentum_pct", label: "Momentum", num: true, html: (p) => pctCell(p.momentum_pct) },
          { key: "above_200", label: "200 gün üstünde", format: (v) => (v ? "evet" : "hayır") },
        ], current.ranking)}</div></details>
      <details class="more" style="margin-top:8px"><summary>Ay ay değişimler (${r.rebalances.length})</summary>
        <div class="table-wrap">${tableHtml([
          { key: "decided", label: "Karar", format: (t) => fmtDate(t) },
          { key: "executed", label: "Uygulama", format: (t) => fmtDate(t) },
          { key: "picks", label: "Seçilenler", format: (v) => (v.length ? v.map((p) => p.symbol).join(", ") : "nakit") },
          { key: "sold", label: "Satılan", format: (v) => (v.length ? v.join(", ") : "—") },
          { key: "bought", label: "Alınan", format: (v) => (v.length ? v.join(", ") : "—") },
        ], r.rebalances.slice().reverse())}</div></details>
      <details class="more" style="margin-top:8px"><summary>İşlemler (${r.trades.length})</summary>
        <div class="table-wrap">${tableHtml([
          { key: "symbol", label: "Hisse" },
          { key: "entry_time", label: "Giriş", format: tipTime },
          { key: "exit_time", label: "Çıkış", format: tipTime },
          { key: "qty", label: "Lot", num: true },
          { key: "return_pct", label: "Getiri", num: true, html: (t) => pill(t.return_pct) },
          { key: "exit_reason", label: "Neden" },
        ], r.trades.slice().reverse())}</div></details>
      <ul class="muted small" style="margin:10px 0 0 18px">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`;
    const series = [
      { name: "Rotasyon", color: token("--accent"), points: m.equity },
      { name: "Eşit ağırlık", color: token("--series-2"), points: r.equal.equity },
      r.benchmark && { name: "XU100", color: slotColor(3), points: r.benchmark.equity, dash: true },
    ].filter(Boolean);
    chartWithTable($("[data-rotation-curve]", host),
      (slot) => lineChart(slot, { series, height: 240, format: (v) => fmtMoney(v, 0), label: "Rotasyon özsermayesi" }),
      () => tableHtml([
        { key: "t", label: "Tarih", format: tipTime },
        ...series.map((s, k) => ({ key: `s${k}`, label: s.name, num: true, format: (v) => (v == null ? "—" : fmtMoney(v)) })),
      ], series[0].points.map((p, i) => Object.fromEntries([["t", p.t], ...series.map((s, k) => [`s${k}`, s.points[i]?.v])])).slice(-120).reverse()));
  }

  async function runRotation(button) {
    const symbols = rotationForm.symbols.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    if (symbols.length < 2) return toast("En az 2 hisse girin.", "error");
    busy(button, true, "Hesaplanıyor…");
    try {
      memory.rotation = await api("/api/lab/rotation", {
        symbols, start: setup.start, end: setup.end, lookback_months: Number(rotationForm.lookback),
        top: Number(rotationForm.top), absolute: rotationForm.absolute, market: rotationForm.market,
      });
      drawRotation();
      drawSteps();
    } catch (error) {
      toast(error.message, "error");
      busy(button, false);
    }
  }

  // ------------------------------------------------------------- events

  root.addEventListener("submit", (event) => {
    if (event.target.matches("[data-author-form]")) {
      event.preventDefault();
      runAuthor(event.target);
    } else if (event.target.matches("[data-blind-form]")) {
      event.preventDefault();
      runBlind();
    } else if (event.target.matches("[data-rotation-form]")) {
      event.preventDefault();
      runRotation($("[data-rotation-run]", event.target));
    }
  });
  root.addEventListener("input", (event) => {
    if (!event.target.closest("[data-rotation-form]")) return;
    const el = event.target;
    if (el.name === "symbols") rotationForm.symbols = el.value;
    if (el.name === "lookback") rotationForm.lookback = Number(el.value);
    if (el.name === "top") rotationForm.top = Number(el.value);
    if (el.name === "absolute") rotationForm.absolute = el.checked;
    if (el.name === "market") rotationForm.market = el.checked;
    for (const [key, value] of Object.entries(rotationForm)) store.set(`strategy.rotation.${key}`, value);
  });
  root.addEventListener("input", (event) => {
    const form = event.target.closest("[data-blind-form]");
    if (!form) return;
    const el = event.target;
    if (el.name === "script") blindForm.script = el.value;
    if (el.name === "model") blindForm.model = el.value;
    if (el.name === "review") blindForm.review = Number(el.value);
    if (el.name === "stop") blindForm.stop = el.value;
    if (el.name === "cash") blindForm.cash = el.value;
    if (el.name === "cache") blindForm.cache = el.checked;
    if (el.name === "learning") blindForm.learning = el.value;
    if (el.name === "rounds") blindForm.rounds = Number(el.value);
    if (el.name === "interval") blindForm.interval = el.value;
    for (const [key, value] of Object.entries(blindForm)) store.set(`strategy.${key}`, value);
    if (el.name === "learning" || el.name === "interval") {
      drawBlind();
      return;
    }
    const text = $("[data-estimate-text]", root);
    if (text) text.textContent = "";
  });
  root.addEventListener("click", (event) => {
    const mode = event.target.closest("[data-m]");
    if (mode) {
      blindForm.mode = mode.dataset.m;
      store.set("strategy.mode", blindForm.mode);
      drawBlind();
      return;
    }
    if (event.target.closest("[data-estimate]")) return estimate(event.target.closest("[data-estimate]"));
    if (event.target.closest("[data-author-stop]")) {
      authorAbort?.abort();
      return;
    }
    if (event.target.closest("[data-blind-stop]")) {
      if (blindJob) api(`/api/lab/jobs/${blindJob}/stop`, {}).catch(() => {});
      blindAbort?.abort();
      return;
    }
    const use = event.target.closest("[data-use-script]");
    if (use) {
      blindForm.script = use.dataset.useScript;
      store.set("strategy.script", blindForm.script);
      drawBlind();
      $("[data-blind]", root).scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    if (event.target.closest("[data-live-run]")) return runLive(event.target.closest("[data-live-run]"));
    const order = event.target.closest("[data-order]");
    if (order) emit("order", order.dataset.order);
  });

  drawSetup();
  drawSteps();
  drawStudy();
  drawAuthor();
  drawBlind();
  drawLive();
  drawRotation();
  loadOptions();
  return () => {
    authorAbort?.abort();
    blindAbort?.abort();
  };
}
