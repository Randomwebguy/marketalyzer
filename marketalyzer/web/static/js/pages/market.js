// Market: watchlist, price chart with script indicators, technical snapshot.
import {
  $, api, emit, esc, fmtNumber, icon, initials, loadQuotes, mount, normalize, on, pctText, pill,
  priceChart, responsive, sparkline, state, store, symbolColor, tipTime, toast, tone,
} from "/static/js/core.js";

const SPANS = { "1G": "1 gün", "1H": "1 hafta", "1A": "1 ay", "3A": "3 ay", "6A": "6 ay", "1Y": "1 yıl", "5Y": "5 yıl" };

export function render(root, { params }) {
  if (params[0]) {
    state.symbol = normalize(params[0]);
    store.set("symbol", state.symbol);
  }
  const local = {
    type: store.get("market.type", "candle"),
    indicators: store.get("market.indicators", ["bollinger", "rsi"]),
    scripts: [],
    data: null,
    table: false,
  };
  root.innerHTML = `
    <div class="market">
      <aside class="card watch">
        <div class="card-head"><h2>İzleme listesi</h2><span class="spacer"></span><span class="badge plain" data-count></span></div>
        <form class="search-pill" data-add style="height:40px;margin-bottom:10px">
          ${icon("plus")}<input class="input" style="border:0;background:none;height:auto;padding:0;box-shadow:none" name="symbol" placeholder="Sembol ekle" maxlength="12" autocomplete="off" aria-label="Sembol ekle">
        </form>
        <div class="scroll" data-watch></div>
      </aside>
      <div class="stack">
        <section class="card" data-chart-card>
          <div class="symbol-head" data-head></div>
          <div class="chart-tools">
            <div class="segmented" data-spans>${Object.entries(SPANS).map(([code, label]) =>
              `<button type="button" data-span="${code}" title="${esc(label)}" class="${code === state.span ? "active" : ""}">${code}</button>`).join("")}</div>
            <div class="segmented" data-types>
              <button type="button" data-type="candle" class="${local.type === "candle" ? "active" : ""}" title="Mum">${icon("candle", "sm")}</button>
              <button type="button" data-type="line" class="${local.type === "line" ? "active" : ""}" title="Çizgi">${icon("line", "sm")}</button>
            </div>
            <span class="spacer"></span>
            <div class="indicator-chips" data-chips></div>
            <button class="chip" type="button" data-add-indicator>${icon("layers")} Gösterge</button>
            <button class="icon-btn sm" type="button" data-table title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button>
          </div>
          <div data-chart><div class="skeleton" style="height:380px"></div></div>
          <div class="table-wrap" data-table-view hidden></div>
        </section>
        <section class="card" data-analysis><div class="skeleton" style="height:180px"></div></section>
      </div>
    </div>`;

  const renderWatch = () => {
    const box = $("[data-watch]", root);
    $("[data-count]", root).textContent = state.watchlist.length;
    box.innerHTML = state.watchlist.map((symbol) => {
      const q = state.quotes[symbol];
      const ok = q && !q.error;
      return `<div class="watch-row ${symbol === state.symbol ? "active" : ""}" data-symbol="${esc(symbol)}" role="button" tabindex="0">
        <span class="coin" style="--c:${symbolColor(symbol)}">${initials(symbol)}</span>
        <div class="name"><b>${esc(symbol)}</b><span>${esc(q?.error ? "Veri yok" : q?.name || "Borsa İstanbul")}</span></div>
        <div data-spark></div>
        <div class="end"><b>${ok ? esc(fmtNumber(q.last)) : "—"}</b>${ok ? pill(q.change_pct) : ""}</div>
      </div>`;
    }).join("") || '<div class="empty">Liste boş.</div>';
    for (const row of box.querySelectorAll(".watch-row")) {
      const q = state.quotes[row.dataset.symbol];
      if (q?.spark) $("[data-spark]", row).appendChild(sparkline(q.spark, { width: 64, height: 28, rising: q.change_pct >= 0 }));
    }
  };

  const renderHead = () => {
    const d = local.data;
    const q = state.quotes[state.symbol];
    const last = d?.rows?.at(-1)?.c ?? q?.last;
    const change = d?.rows?.length ? (d.rows.at(-1).c / d.rows[0].o - 1) * 100 : q?.change_pct;
    $("[data-head]", root).innerHTML = `
      <span class="coin lg" style="--c:${symbolColor(state.symbol)}">${initials(state.symbol)}</span>
      <div><h2 style="margin:0;font-size:18px;letter-spacing:-.02em">${esc(state.symbol)}</h2>
        <span class="muted small">${esc(d?.name || q?.name || "Borsa İstanbul")} · ${esc(SPANS[state.span])}</span></div>
      <span class="price">${Number.isFinite(last) ? esc(fmtNumber(last)) : "—"}</span>${pill(change)}
      <span class="spacer"></span>
      <button class="btn small" type="button" data-buy>${icon("arrowdown", "sm")} Al</button>
      <button class="btn small" type="button" data-sell>${icon("arrowup", "sm")} Sat</button>
      <button class="btn small violet" type="button" data-ask>${icon("sparkle", "sm")} Asistana sor</button>`;
  };

  const renderChips = () => {
    $("[data-chips]", root).innerHTML = local.indicators.map((name) => {
      const script = local.scripts.find((s) => s.name === name);
      return `<span class="chip tag">${esc(script?.title || name)}
        <button class="icon-btn xs ghost" type="button" data-remove-indicator="${esc(name)}" aria-label="Kaldır">${icon("x")}</button></span>`;
    }).join("");
  };

  const draw = () => {
    const d = local.data;
    const host = $("[data-chart]", root);
    if (!d) return;
    const overlays = [];
    const panes = [];
    const markers = [];
    for (const run of d.runs) {
      const own = run.plots.filter((p) => !p.overlay);
      overlays.push(...run.plots.filter((p) => p.overlay));
      if (own.length || (run.hlines.length && !run.script.overlay)) {
        panes.push({ title: run.script.title, plots: own, hlines: run.hlines });
      }
      markers.push(...run.entries.map((t) => ({ t, kind: "entry" })), ...run.exits.map((t) => ({ t, kind: "exit" })));
      for (const shape of run.shapes) {
        markers.push(...shape.times.map((t) => ({ t, kind: "shape", style: shape.style, location: shape.location, color: shape.color, text: shape.text })));
      }
    }
    const drawChart = () => priceChart(host, {
      rows: d.rows, type: local.type, overlays, panes, markers,
      height: window.innerWidth < 860 ? 300 : 380, paneHeight: 110,
      label: `${state.symbol} fiyat grafiği`,
    });
    drawChart();
    mount(responsive(host, drawChart));
    $("[data-table-view]", root).innerHTML = `<table><thead><tr><th>Zaman</th><th class="num">Açılış</th><th class="num">Yüksek</th>
      <th class="num">Düşük</th><th class="num">Kapanış</th><th class="num">Hacim</th></tr></thead><tbody>
      ${d.rows.slice(-80).reverse().map((r) => `<tr><td>${esc(tipTime(r.t))}</td><td class="num">${esc(fmtNumber(r.o))}</td>
        <td class="num">${esc(fmtNumber(r.h))}</td><td class="num">${esc(fmtNumber(r.l))}</td><td class="num">${esc(fmtNumber(r.c))}</td>
        <td class="num">${esc(new Intl.NumberFormat("tr-TR", { notation: "compact" }).format(r.v ?? 0))}</td></tr>`).join("")}</tbody></table>`;
  };

  async function loadChart() {
    const symbol = state.symbol;
    const card = $("[data-chart-card]", root);
    card.classList.add("loading");
    renderHead();
    try {
      const runs = await Promise.all(local.indicators.map((name) =>
        api("/api/scripts/run", { symbol, name, span: state.span }).catch((error) => {
          toast(`${name}: ${error.message}`, "error");
          return null;
        })));
      const ok = runs.filter(Boolean);
      let rows;
      let name;
      if (ok.length) {
        rows = ok[0].rows;
        name = ok[0].name;
      } else {
        const prices = await api(`/api/prices?symbol=${encodeURIComponent(symbol)}&span=${state.span}`);
        rows = prices.rows;
        name = prices.name;
      }
      if (symbol !== state.symbol) return;
      local.data = { rows, name, runs: ok };
      renderHead();
      draw();
    } catch (error) {
      $("[data-chart]", root).innerHTML = `<div class="chart-empty">${esc(error.message)}</div>`;
    } finally {
      card.classList.remove("loading");
    }
  }

  async function loadAnalysis() {
    const box = $("[data-analysis]", root);
    box.classList.add("loading");
    try {
      const a = await api(`/api/analysis?symbol=${encodeURIComponent(state.symbol)}`);
      if (a.symbol !== state.symbol) return;
      box.innerHTML = analysisHtml(a);
    } catch (error) {
      box.innerHTML = `<div class="card-head"><h2>Teknik özet</h2></div><div class="empty">${esc(error.message)}</div>`;
    } finally {
      box.classList.remove("loading");
    }
  }

  const select = (symbol) => {
    const code = normalize(symbol);
    if (!code) return;
    state.symbol = code;
    store.set("symbol", code);
    history.replaceState(null, "", `#/piyasa/${code}`);
    renderWatch();
    loadChart();
    loadAnalysis();
  };

  root.addEventListener("submit", (event) => {
    if (!event.target.matches("[data-add]")) return;
    event.preventDefault();
    const input = event.target.elements.symbol;
    const code = normalize(input.value);
    input.value = "";
    if (!code) return;
    if (!state.watchlist.includes(code)) {
      state.watchlist.push(code);
      store.set("watchlist", state.watchlist);
      loadQuotes([code]).then(renderWatch).catch((error) => toast(error.message, "error"));
    }
    select(code);
  });
  root.addEventListener("click", async (event) => {
    const t = event.target;
    const row = t.closest(".watch-row");
    if (row) return select(row.dataset.symbol);
    const span = t.closest("[data-span]");
    if (span) {
      state.span = span.dataset.span;
      store.set("span", state.span);
      root.querySelectorAll("[data-span]").forEach((b) => b.classList.toggle("active", b === span));
      return loadChart();
    }
    const type = t.closest("[data-type]");
    if (type) {
      local.type = type.dataset.type;
      store.set("market.type", local.type);
      root.querySelectorAll("[data-type]").forEach((b) => b.classList.toggle("active", b === type));
      return draw();
    }
    const remove = t.closest("[data-remove-indicator]");
    if (remove) {
      local.indicators = local.indicators.filter((n) => n !== remove.dataset.removeIndicator);
      store.set("market.indicators", local.indicators);
      renderChips();
      return loadChart();
    }
    if (t.closest("[data-add-indicator]")) return pickIndicator();
    if (t.closest("[data-table]")) {
      local.table = !local.table;
      $("[data-table-view]", root).hidden = !local.table;
      $("[data-chart]", root).hidden = local.table;
      return null;
    }
    if (t.closest("[data-buy]") || t.closest("[data-sell]")) {
      state.ticket.side = t.closest("[data-buy]") ? "buy" : "sell";
      return emit("order", state.symbol);
    }
    if (t.closest("[data-ask]") || t.closest("[data-ask-analysis]")) {
      return emit("ask", `${state.symbol} için teknik analiz yap: trend, momentum, oynaklık, destek/direnç seviyeleri ve dikkat edilmesi gereken riskler.`);
    }
    return null;
  });
  root.addEventListener("keydown", (event) => {
    const row = event.target.closest(".watch-row");
    if (row && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      select(row.dataset.symbol);
    }
  });

  async function pickIndicator() {
    const { sheet } = await import("/static/js/core.js");
    const { body, close } = sheet(`<div class="list">${local.scripts.map((s) => `
      <div class="list-row link" data-pick="${esc(s.name)}" style="padding:10px 8px">
        <span class="avatar">${icon(s.kind === "strategy" ? "flask" : "chart")}</span>
        <div class="grow"><b>${esc(s.title)}</b><span>${esc(s.description || s.name)}</span></div>
        <div class="end">${local.indicators.includes(s.name) ? '<span class="chip tag green">Ekli</span>' : `<span class="chip tag">${s.kind === "strategy" ? "Strateji" : "Gösterge"}</span>`}</div>
      </div>`).join("")}</div>
      <a class="btn block" style="margin-top:12px" href="#/lab/yeni">${icon("code")} Yeni gösterge yaz</a>`, { title: "Gösterge ekle" });
    body.addEventListener("click", (event) => {
      const pick = event.target.closest("[data-pick]");
      if (!pick) return;
      const name = pick.dataset.pick;
      if (!local.indicators.includes(name)) local.indicators.push(name);
      store.set("market.indicators", local.indicators);
      close();
      renderChips();
      loadChart();
    });
    body.addEventListener("click", (event) => {
      if (event.target.closest("a")) close();
    });
  }

  renderWatch();
  renderHead();
  api("/api/scripts").then((scripts) => {
    local.scripts = scripts.filter((s) => !s.error);
    renderChips();
  }).catch(() => {});
  loadQuotes([...state.watchlist, state.symbol]).then(() => {
    renderWatch();
    renderHead();
  }).catch((error) => toast(error.message, "error"));
  renderChips();
  loadChart();
  loadAnalysis();
  const off = on("display", renderWatch);
  return off;
}

function meter(value, min, max, zones = []) {
  const pos = (v) => `${Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100))}%`;
  return `<div class="meter">${zones.map(([a, b, color]) =>
    `<span class="zone" style="left:${pos(a)};width:calc(${pos(b)} - ${pos(a)});background:${color}"></span>`).join("")}
    ${Number.isFinite(value) ? `<i style="left:${pos(value)}"></i>` : ""}</div>`;
}

function gauge(title, value, text, min, max, zones, scale) {
  return `<div class="gauge"><div class="top"><span>${esc(title)}</span><b>${esc(text)}</b></div>
    ${meter(value, min, max, zones)}<div class="scale">${scale.map((s) => `<span>${esc(s)}</span>`).join("")}</div></div>`;
}

function analysisHtml(a) {
  const ma = a.moving_averages;
  const range = a.range_52w;
  const hot = "rgba(230,103,103,.35)";
  const cold = "rgba(12,163,12,.35)";
  const rangePos = range.high > range.low ? ((a.close - range.low) / (range.high - range.low)) * 100 : NaN;
  const maRow = (label, value) => {
    if (value === null) return `<div><span>${label}</span><b>—</b></div>`;
    const above = a.close > value;
    return `<div><span>${label}</span><b>${esc(fmtNumber(value))} <span class="pill ${above ? "up" : "down"}">${above ? "▲ üstünde" : "▼ altında"}</span></b></div>`;
  };
  return `
    <div class="card-head"><h2>Teknik özet</h2><span class="sub">${esc(a.symbol)} · ${esc(tipTime(a.date))}</span>
      <span class="spacer"></span><button class="btn small violet" type="button" data-ask-analysis>${icon("sparkle", "sm")} Yorumlat</button></div>
    <div class="gauge-tiles">
      ${gauge("RSI (14)", a.rsi14, a.rsi14 === null ? "—" : fmtNumber(a.rsi14, 1), 0, 100, [[0, 30, cold], [70, 100, hot]], ["0", "30", "70", "100"])}
      ${gauge("Stokastik %K", a.stochastic.k, a.stochastic.k === null ? "—" : fmtNumber(a.stochastic.k, 1), 0, 100, [[0, 20, cold], [80, 100, hot]], ["0", "20", "80", "100"])}
      ${gauge("ADX (14)", a.adx14.adx, a.adx14.adx === null ? "—" : fmtNumber(a.adx14.adx, 1), 0, 60, [[25, 60, "rgba(57,135,229,.35)"]], ["0", "25", "60"])}
      ${gauge("Bollinger %B", a.bollinger.percent_b, a.bollinger.percent_b === null ? "—" : fmtNumber(a.bollinger.percent_b, 2), -0.2, 1.2, [[-0.2, 0, cold], [1, 1.2, hot]], ["alt", "orta", "üst"])}
      ${gauge("52 hafta aralığı", rangePos, Number.isFinite(rangePos) ? `%${fmtNumber(rangePos, 0)}` : "—", 0, 100, [], [fmtNumber(range.low), fmtNumber(range.high)])}
      ${gauge("Hacim / 20 gün", a.volume_vs_20d, a.volume_vs_20d === null ? "—" : `${fmtNumber(a.volume_vs_20d, 2)}x`, 0, 3, [[1.5, 3, "rgba(242,201,76,.35)"]], ["0", "1x", "3x"])}
    </div>
    <div class="grid" style="margin-top:14px">
      <div class="span-6"><div class="summary-rows">
        ${maRow("SMA 20", ma.sma20)}${maRow("SMA 50", ma.sma50)}${maRow("SMA 200", ma.sma200)}
        <div><span>MACD / sinyal</span><b>${esc(a.macd.macd === null ? "—" : `${fmtNumber(a.macd.macd, 3)} / ${fmtNumber(a.macd.signal, 3)}`)}</b></div>
        <div><span>ATR (14)</span><b>${esc(a.atr14 === null ? "—" : `${fmtNumber(a.atr14)} · %${fmtNumber(a.atr_pct, 2)}`)}</b></div>
        <div><span>Supertrend</span><b>${esc(a.supertrend.direction)} · ${esc(a.supertrend.line === null ? "—" : fmtNumber(a.supertrend.line))}</b></div>
      </div></div>
      <div class="span-6">
        <div class="label" style="margin-bottom:8px">Sinyaller</div>
        <ul class="signals">${a.signals.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>
        <div class="label" style="margin:12px 0 6px">Değişim</div>
        <div class="row-flex">${[["1 bar", a.change_pct["1_bar"]], ["1 hafta", a.change_pct["5_bar"]], ["1 ay", a.change_pct["21_bar"]], ["3 ay", a.change_pct["63_bar"]], ["1 yıl", a.change_pct["252_bar"]]]
          .map(([label, v]) => `<span class="chip tag">${esc(label)} <b class="${tone(v)}-text">${esc(pctText(v))}</b></span>`).join("")}</div>
        <div class="label" style="margin:12px 0 6px">Pivot destek / direnç</div>
        <div class="row-flex">${a.pivot_support.map((v) => `<span class="chip tag green">D ${esc(fmtNumber(v))}</span>`).join("")}
          ${a.pivot_resistance.map((v) => `<span class="chip tag red">R ${esc(fmtNumber(v))}</span>`).join("")}</div>
      </div>
    </div>`;
}
