// Lab: write Pine-like scripts, run them on a chart, backtest and tune them.
import {
  $, api, busy, dateOnly, emit, esc, icon, mount, normalize, priceChart, responsive, state, store, tipTime,
  toast,
} from "/static/js/core.js";
import { createEditor } from "/static/js/editor.js";
import { showBacktest, showWalkForward } from "/static/js/results.js";

const TEMPLATE = `//@version=5
// Yeni strateji: hızlı EMA yavaşı yukarı kesince al, aşağı kesince sat.
strategy("Yeni strateji", overlay=true)

fastLength = input.int(9, "Hızlı EMA", minval=5, maxval=20, step=5)
slowLength = input.int(21, "Yavaş EMA", minval=20, maxval=60, step=10)

fast = ta.ema(close, fastLength)
slow = ta.ema(close, slowLength)
plot(fast, "Hızlı EMA", color=color.blue)
plot(slow, "Yavaş EMA", color=color.orange)

if ta.crossover(fast, slow)
    strategy.entry("Al", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Al")
`;
const SPANS = ["3A", "6A", "1Y", "5Y", "1G", "1H"];
let reference = null;

export function render(root, { params }) {
  const local = {
    scripts: [],
    name: null,
    builtin: false,
    saved: "",
    check: null,
    inputs: {},
    side: "params",
    result: "chart",
    run: null,
    backtest: null,
    symbol: state.symbol,
    span: store.get("lab.span", "1Y"),
    query: "",
  };
  root.innerHTML = `
    <div class="lab">
      <aside class="card lab-list">
        <div class="card-head"><h2>Scriptler</h2><span class="spacer"></span>
          <button class="btn small" type="button" data-new>${icon("plus", "sm")} Yeni</button></div>
        <input class="input" data-search placeholder="Ara…" aria-label="Script ara" style="height:34px;margin-bottom:8px">
        <div class="scroll" data-list></div>
      </aside>
      <section class="card lab-editor">
        <div class="editor-bar">
          <button class="icon-btn sm lab-list-toggle" type="button" data-show-list aria-label="Scriptler">${icon("menu")}</button>
          <input class="name" data-name spellcheck="false" aria-label="Script adı" placeholder="script_adi">
          <span class="chip tag" data-kind>—</span>
          <button class="icon-btn sm" type="button" data-delete aria-label="Sil" title="Sil">${icon("trash")}</button>
          <span class="spacer"></span>
          <button class="btn small" type="button" data-save title="Ctrl+S">${icon("save", "sm")} Kaydet</button>
          <button class="btn small primary" type="button" data-run title="Ctrl+Enter">${icon("play", "sm")} Çalıştır</button>
          <button class="btn small" type="button" data-backtest>${icon("flask", "sm")} Backtest</button>
          <button class="btn small" type="button" data-optimize>${icon("zap", "sm")} Optimize</button>
          <button class="btn small" type="button" data-walk>${icon("walk", "sm")} Walk-forward</button>
          <button class="btn small violet" type="button" data-ai>${icon("sparkle", "sm")} Asistan</button>
        </div>
        <div data-editor style="display:flex;flex-direction:column;flex:1;min-height:0"></div>
        <div class="editor-status"><span class="state" data-state>Hazır</span><span data-pos>1:1</span>
          <span class="spacer"></span><span>Pine Script v5 alt kümesi</span></div>
      </section>
      <aside class="card lab-side">
        <div class="tabs" data-side-tabs>
          <button type="button" data-side="params">Parametreler</button>
          <button type="button" data-side="console">Konsol <span class="badge plain" data-console-count>0</span></button>
          <button type="button" data-side="reference">Başvuru</button>
        </div>
        <div class="body" data-side-body></div>
      </aside>
      <section class="lab-result stack">
        <div class="card">
          <div class="card-head" style="flex-wrap:wrap">
            <div class="tabs" data-result-tabs style="margin:0;border:0">
              <button type="button" data-result="chart">Grafik</button>
              <button type="button" data-result="tester">Strateji testi</button>
            </div>
            <span class="spacer"></span>
            <input class="input" data-symbol value="${esc(local.symbol)}" maxlength="12" aria-label="Sembol" style="width:110px;height:32px;text-transform:uppercase">
            <div class="segmented" data-spans>${SPANS.map((s) => `<button type="button" data-span="${s}" class="${s === local.span ? "active" : ""}">${s}</button>`).join("")}</div>
          </div>
          <div data-result-body><div class="empty"><b>Çalıştır'a basın</b>Script seçili sembolde çalışır; çizimler ve sinyaller burada görünür.</div></div>
        </div>
        <div class="stack" data-tester-extra></div>
      </section>
    </div>`;

  const editor = createEditor($("[data-editor]", root), {
    value: "",
    onChange: () => {
      updateDirty();
      scheduleCheck();
      updatePos();
    },
    onRun: () => runScript(),
    onSave: () => save(),
    candidates: () => (reference ?? []).map((r) => ({ name: r.name, signature: r.signature, doc: r.doc, kind: r.category })),
  });
  editor.textarea.addEventListener("keyup", updatePos);
  editor.textarea.addEventListener("click", updatePos);

  function updatePos() {
    const { line, col } = editor.position();
    $("[data-pos]", root).textContent = `${line}:${col}`;
  }
  function updateDirty() {
    const dirty = editor.value !== local.saved;
    $("[data-save]", root).classList.toggle("primary", dirty);
  }

  // ---------------------------------------------------------- list
  const drawList = () => {
    const q = local.query.toLowerCase();
    const match = (s) => !q || `${s.name} ${s.title} ${s.description}`.toLowerCase().includes(q);
    const mine = local.scripts.filter((s) => !s.builtin && match(s));
    const library = local.scripts.filter((s) => s.builtin && match(s));
    const item = (s) => `<div class="script-item ${s.name === local.name ? "active" : ""}" data-open="${esc(s.name)}">
      <b>${icon(s.kind === "strategy" ? "flask" : s.error ? "alert" : "chart", "sm")} ${esc(s.title || s.name)}</b>
      <span>${esc(s.name)}${s.customized ? " · düzenlendi" : ""}${s.error ? " · hata" : ""}</span></div>`;
    $("[data-list]", root).innerHTML = `
      ${mine.length ? `<div class="group-title">Benim scriptlerim</div>${mine.map(item).join("")}` : ""}
      <div class="group-title">Hazır kütüphane</div>${library.map(item).join("") || '<div class="empty">Sonuç yok.</div>'}`;
  };

  async function loadList() {
    try {
      local.scripts = await api("/api/scripts");
    } catch (error) {
      toast(error.message, "error");
    }
    drawList();
  }

  async function open(name) {
    if (editor.value !== local.saved && !confirm("Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?")) return;
    try {
      const script = await api(`/api/scripts/${encodeURIComponent(name)}`);
      load(script.name, script.source, script.builtin);
      history.replaceState(null, "", `#/lab/${script.name}`);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function load(name, source, builtin = false) {
    local.name = name;
    local.builtin = builtin;
    local.saved = builtin ? source : name ? source : "";
    local.inputs = {};
    editor.value = source;
    $("[data-name]", root).value = name || "";
    store.set("lab.current", name);
    drawList();
    updateDirty();
    check();
  }

  // ---------------------------------------------------------- checking
  let timer = null;
  function scheduleCheck() {
    clearTimeout(timer);
    timer = setTimeout(check, 600);
  }

  async function check() {
    const source = editor.value;
    try {
      const result = await api("/api/scripts/check", { source });
      if (source !== editor.value) return;
      local.check = result;
    } catch (error) {
      local.check = { ok: false, error: { message: error.message } };
    }
    const c = local.check;
    const status = $("[data-state]", root);
    status.className = `state ${c.ok ? "ok" : "bad"}`;
    status.textContent = c.ok ? `Derlendi · ${c.inputs.length} parametre${c.stateful ? " · bar bar" : ""}`
      : `Satır ${c.error.line ?? "?"}: ${c.error.message}`;
    $("[data-kind]", root).textContent = c.ok ? (c.kind === "strategy" ? "Strateji" : "Gösterge") : "Hata";
    $("[data-kind]", root).className = `chip tag ${c.ok ? (c.kind === "strategy" ? "sky" : "") : "red"}`;
    editor.setError(c.ok ? null : c.error.line);
    for (const name of ["data-backtest", "data-optimize", "data-walk"]) {
      $(`[${name}]`, root).disabled = !c.ok || c.kind !== "strategy";
    }
    drawSide();
  }

  // ---------------------------------------------------------- side panel
  function inputField(spec) {
    const value = local.inputs[spec.name] ?? spec.default;
    const id = `in-${spec.name}`;
    if (spec.kind === "bool") {
      return `<label class="check"><input type="checkbox" data-input="${esc(spec.name)}" ${value ? "checked" : ""}> ${esc(spec.title)}</label>`;
    }
    const options = spec.options ?? (spec.kind === "source" ? ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4", "volume"] : null);
    if (options) {
      return `<label class="field" for="${id}">${esc(spec.title)}<select id="${id}" data-input="${esc(spec.name)}">
        ${options.map((o) => `<option ${String(o) === String(value) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
    }
    const numeric = spec.kind === "int" || spec.kind === "float";
    const range = [spec.minval, spec.maxval].some((v) => v !== null && v !== undefined)
      ? `<span class="hint">${spec.minval ?? "−∞"} … ${spec.maxval ?? "∞"}${spec.step ? ` · adım ${spec.step}` : ""}</span>` : "";
    return `<label class="field" for="${id}"><span>${esc(spec.title)} <code class="faint">${esc(spec.name)}</code></span>
      <input id="${id}" data-input="${esc(spec.name)}" ${numeric ? `type="number" step="${spec.kind === "int" ? 1 : "any"}"` : ""} value="${esc(value)}">${range}</label>`;
  }

  function drawSide() {
    root.querySelectorAll("[data-side]").forEach((b) => b.classList.toggle("active", b.dataset.side === local.side));
    const body = $("[data-side-body]", root);
    const c = local.check;
    const messages = [];
    if (c && !c.ok) messages.push({ kind: "bad", text: `Satır ${c.error.line ?? "?"}${c.error.col ? `:${c.error.col}` : ""} · ${c.error.message}`, line: c.error.line });
    for (const w of c?.warnings ?? []) messages.push({ kind: "warn", text: w });
    for (const w of local.run?.warnings ?? []) if (!(c?.warnings ?? []).includes(w)) messages.push({ kind: "warn", text: w });
    for (const alert of local.run?.alerts ?? []) {
      messages.push({ kind: "", text: `🔔 ${alert.title}: ${alert.times.length ? `son ${tipTime(alert.times.at(-1))} (${alert.times.length} kez)` : "tetiklenmedi"}` });
    }
    if (local.run) {
      messages.push({ kind: "", text: `${local.run.symbol} · ${local.run.rows.length} bar · ${local.run.plots.length} çizim · ${local.run.entries.length} giriş / ${local.run.exits.length} çıkış sinyali` });
    }
    $("[data-console-count]", root).textContent = messages.length;
    if (local.side === "params") {
      const inputs = c?.ok ? c.inputs : [];
      body.innerHTML = inputs.length
        ? `<form class="form" data-inputs>${inputs.map(inputField).join("")}
            <div class="row-flex"><button class="btn small primary" type="submit">${icon("play", "sm")} Uygula ve çalıştır</button>
            <button class="btn small ghost" type="button" data-reset-inputs>Varsayılanlar</button></div></form>`
        : `<div class="empty">${c?.ok ? "Bu scriptin parametresi yok. <code>input.int()</code> ile ekleyin." : "Script derlenince parametreler burada görünür."}</div>`;
    } else if (local.side === "console") {
      body.innerHTML = messages.length
        ? `<div class="console">${messages.map((m) => `<div class="${m.kind}" ${m.line ? `data-goto="${m.line}" style="cursor:pointer"` : ""}>${esc(m.text)}</div>`).join("")}</div>`
        : '<div class="empty">Mesaj yok.</div>';
    } else {
      drawReference(body);
    }
  }

  async function drawReference(body) {
    if (!reference) {
      body.innerHTML = '<div class="skeleton" style="height:200px"></div>';
      reference = await api("/api/scripts/reference").catch(() => []);
    }
    body.innerHTML = `<input class="input" data-ref-search placeholder="ta.rsi, crossover, plot…" style="height:34px;margin-bottom:6px" aria-label="Fonksiyon ara">
      <div data-ref-list></div>`;
    const list = $("[data-ref-list]", body);
    const draw = (q) => {
      const query = q.toLowerCase();
      const shown = reference.filter((r) => !query || `${r.name} ${r.doc}`.toLowerCase().includes(query)).slice(0, 80);
      list.innerHTML = shown.map((r) => `<div class="ref-item" data-insert="${esc(r.signature)}" title="Editöre ekle">
        <code>${esc(r.signature)}</code><span>${esc(r.doc)}</span></div>`).join("") || '<div class="empty">Sonuç yok.</div>';
    };
    draw("");
    $("[data-ref-search]", body).addEventListener("input", (event) => draw(event.target.value));
  }

  // ---------------------------------------------------------- running
  function currentInputs() {
    const known = new Set((local.check?.inputs ?? []).map((i) => i.name));
    return Object.fromEntries(Object.entries(local.inputs).filter(([k]) => known.has(k)));
  }

  async function runScript() {
    const button = $("[data-run]", root);
    local.symbol = normalize($("[data-symbol]", root).value) || local.symbol;
    busy(button, true, "Çalışıyor…");
    try {
      local.run = await api("/api/scripts/run", {
        symbol: local.symbol, source: editor.value, inputs: currentInputs(), span: local.span,
      });
      local.result = "chart";
      drawResult();
    } catch (error) {
      if (error.detail?.line) editor.setError(error.detail.line);
      toast(error.message, "error");
      local.side = "console";
    } finally {
      busy(button, false);
      drawSide();
    }
  }

  function drawResult() {
    root.querySelectorAll("[data-result]").forEach((b) => b.classList.toggle("active", b.dataset.result === local.result));
    const body = $("[data-result-body]", root);
    const extra = $("[data-tester-extra]", root);
    if (local.result === "tester") {
      if (!local.backtest) {
        body.innerHTML = '<div class="empty"><b>Henüz test yok</b>Backtest, Optimize ya da Walk-forward ile başlatın. Strateji scriptleri için kullanılabilir.</div>';
        extra.innerHTML = "";
        return;
      }
      body.innerHTML = "";
      if (local.backtest.kind === "walk") showWalkForward(extra, local.backtest.data);
      else showBacktest(extra, local.backtest.data, { onApply: applyParams });
      return;
    }
    extra.innerHTML = "";
    const run = local.run;
    if (!run) return;
    const overlays = run.plots.filter((p) => p.overlay);
    const pane = run.plots.filter((p) => !p.overlay);
    const panes = pane.length || (run.hlines.length && !run.script.overlay)
      ? [{ title: run.script.title, plots: pane, hlines: run.hlines }] : [];
    const markers = [
      ...run.entries.map((t) => ({ t, kind: "entry" })),
      ...run.exits.map((t) => ({ t, kind: "exit" })),
      ...run.shapes.flatMap((s) => s.times.map((t) => ({ t, kind: "shape", style: s.style, location: s.location, color: s.color, text: s.text }))),
    ];
    body.innerHTML = "<div data-plot></div>";
    const host = $("[data-plot]", body);
    const draw = () => priceChart(host, {
      rows: run.rows, overlays, panes, markers, height: 340, paneHeight: 130,
      label: `${run.symbol} üzerinde ${run.script.title}`,
    });
    draw();
    mount(responsive(host, draw));
  }

  function applyParams(params) {
    local.inputs = { ...local.inputs, ...params };
    local.side = "params";
    drawSide();
    toast("Parametreler uygulandı; Çalıştır ile grafiği güncelleyin.");
  }

  async function test(kind) {
    if (!local.check?.ok || local.check.kind !== "strategy") {
      toast("Test için script strategy(...) ile bildirilmeli ve hatasız olmalı.", "error");
      return;
    }
    const button = $(`[data-${kind}]`, root);
    local.symbol = normalize($("[data-symbol]", root).value) || local.symbol;
    const common = { symbol: local.symbol, source: editor.value, strategy: null };
    busy(button, true, kind === "walk" ? "Pencereler…" : kind === "optimize" ? "Optimize…" : "Test…");
    try {
      let data;
      if (kind === "walk") {
        data = await api("/api/walkforward", { ...common, start: dateOnly(-5 * 365), train: 504, test: 126 });
      } else {
        data = await api("/api/backtest", {
          ...common, start: dateOnly(-3 * 365), params: currentInputs(), optimize: kind === "optimize",
        });
      }
      local.backtest = { kind, data };
      local.result = "tester";
      drawResult();
      $("[data-result-tabs]", root).scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
    }
  }

  async function save() {
    const name = $("[data-name]", root).value.trim().toLowerCase();
    if (!name) {
      toast("Kaydetmek için bir ad girin (küçük harf, rakam, - ve _).", "error");
      $("[data-name]", root).focus();
      return;
    }
    const builtin = local.scripts.find((s) => s.name === name && s.builtin);
    if (builtin && !confirm("Bu ad hazır kütüphanede var. Kendi kopyanız olarak kaydedilsin mi? (Silerseniz orijinali geri gelir.)")) return;
    try {
      const saved = await api(`/api/scripts/${encodeURIComponent(name)}`, { source: editor.value }, "PUT");
      local.name = saved.name;
      local.builtin = false;
      local.saved = editor.value;
      updateDirty();
      toast(saved.error ? `Kaydedildi, ama hata var: Satır ${saved.error.line}` : "Kaydedildi.");
      history.replaceState(null, "", `#/lab/${saved.name}`);
      await loadList();
      state.meta = await api("/api/meta").catch(() => state.meta);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function remove() {
    const script = local.scripts.find((s) => s.name === local.name);
    if (!script || script.builtin) {
      toast("Hazır kütüphane scriptleri silinemez.", "error");
      return;
    }
    if (!confirm(`'${script.name}' silinsin mi?`)) return;
    try {
      const result = await api(`/api/scripts/${encodeURIComponent(script.name)}`, undefined, "DELETE");
      toast(result.restored_builtin ? "Silindi; hazır sürüm geri geldi." : "Silindi.");
      local.saved = editor.value;
      await loadList();
      if (result.restored_builtin) open(script.name);
      else load(null, TEMPLATE);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  function askAi() {
    const c = local.check;
    store.set("assistant.context", {
      page: "Script editörü", symbol: local.symbol, script_name: local.name || "kaydedilmemiş", script_source: editor.value,
    });
    const text = c && !c.ok
      ? `Editördeki scriptte şu hata var: "Satır ${c.error.line}: ${c.error.message}". Düzeltip tam kodu verir misin?`
      : c?.kind === "strategy"
        ? `Editördeki stratejiyi ${local.symbol} üzerinde backtest et, sonuçları yorumla ve geliştirme önerileri ver.`
        : "Editördeki scripti açıklar mısın? Nasıl geliştirebilirim?";
    emit("ask", text);
  }

  // ---------------------------------------------------------- events
  root.addEventListener("click", (event) => {
    const t = event.target;
    const openItem = t.closest("[data-open]");
    if (openItem) {
      root.querySelector(".lab").classList.remove("show-list");
      return open(openItem.dataset.open);
    }
    if (t.closest("[data-new]")) {
      if (editor.value !== local.saved && !confirm("Kaydedilmemiş değişiklikler kaybolacak. Devam edilsin mi?")) return null;
      load(null, TEMPLATE);
      $("[data-name]", root).focus();
      return history.replaceState(null, "", "#/lab/yeni");
    }
    if (t.closest("[data-show-list]")) return root.querySelector(".lab").classList.toggle("show-list");
    if (t.closest("[data-save]")) return save();
    if (t.closest("[data-run]")) return runScript();
    if (t.closest("[data-backtest]")) return test("backtest");
    if (t.closest("[data-optimize]")) return test("optimize");
    if (t.closest("[data-walk]")) return test("walk");
    if (t.closest("[data-ai]")) return askAi();
    if (t.closest("[data-delete]")) return remove();
    const side = t.closest("[data-side]");
    if (side) {
      local.side = side.dataset.side;
      return drawSide();
    }
    const result = t.closest("[data-result]");
    if (result) {
      local.result = result.dataset.result;
      return drawResult();
    }
    const span = t.closest("[data-span]");
    if (span) {
      local.span = span.dataset.span;
      store.set("lab.span", local.span);
      root.querySelectorAll("[data-span]").forEach((b) => b.classList.toggle("active", b === span));
      return runScript();
    }
    const insert = t.closest("[data-insert]");
    if (insert) return editor.insert(insert.dataset.insert);
    const gotoLine = t.closest("[data-goto]");
    if (gotoLine) return editor.goto(Number(gotoLine.dataset.goto));
    if (t.closest("[data-reset-inputs]")) {
      local.inputs = {};
      return drawSide();
    }
    return null;
  });
  root.addEventListener("submit", (event) => {
    if (!event.target.matches("[data-inputs]")) return;
    event.preventDefault();
    for (const field of event.target.querySelectorAll("[data-input]")) {
      const spec = local.check.inputs.find((i) => i.name === field.dataset.input);
      let value = field.type === "checkbox" ? field.checked : field.value;
      if (spec.kind === "int" || spec.kind === "float") value = Number(value);
      local.inputs[spec.name] = value;
    }
    runScript();
  });
  root.addEventListener("input", (event) => {
    if (event.target.matches("[data-search]")) {
      local.query = event.target.value;
      drawList();
    }
  });
  $("[data-symbol]", root).addEventListener("keydown", (event) => {
    if (event.key === "Enter") runScript();
  });

  // ---------------------------------------------------------- start
  loadList();
  api("/api/scripts/reference").then((r) => { reference = r; }).catch(() => {});
  const pending = store.get("lab.pending", null);
  if (pending) {
    store.set("lab.pending", null);
    load(null, pending.source);
    $("[data-name]", root).value = pending.name || "";
    toast("Asistanın scripti editöre yüklendi; kaydetmeyi unutmayın.");
  } else if (params[0] && params[0] !== "yeni") {
    open(params[0]);
  } else if (params[0] === "yeni") {
    load(null, TEMPLATE);
  } else {
    open(store.get("lab.current", null) || "sma_cross");
  }
  drawSide();
  drawResult();
  const onLeave = (event) => {
    if (editor.value !== local.saved) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  window.addEventListener("beforeunload", onLeave);
  return () => window.removeEventListener("beforeunload", onLeave);
}
