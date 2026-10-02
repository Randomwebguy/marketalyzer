// marketalyzer web app: panel, trading, backtest and walk-forward views.
import {
  candleChart, fmtDate, fmtMoney, fmtNumber, fmtPct, lineChart, responsive, sparkline, tipTime, token,
} from "/static/charts.js";

const $ = (selector, root = document) => root.querySelector(selector);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const ICONS = {
  panel: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  trade: '<path d="M4 7h14l-3-3"/><path d="M20 17H6l3 3"/>',
  backtest: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
  walk: '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  up: '<path d="M7 17L17 7"/><path d="M8 7h9v9"/>',
  down: '<path d="M7 7l10 10"/><path d="M17 8v9H8"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
};
const icon = (name) => `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

const STRATEGY_TEXT = {
  sma_cross: "Hızlı hareketli ortalama yavaş olanı yukarı kestiğinde alır, aşağı kestiğinde satar.",
  rsi_reversion: "RSI alt eşiğin altına inince alır, üst eşiğin üstüne çıkınca satar.",
};
const PARAM_TEXT = {
  fast: "Hızlı SMA (gün)", slow: "Yavaş SMA (gün)", period: "RSI periyodu",
  lower: "Alt eşik", upper: "Üst eşik",
};
const SPANS = {
  "1G": "1 gün", "1H": "1 hafta", "1A": "1 ay", "3A": "3 ay", "6A": "6 ay", "1Y": "1 yıl", "5Y": "5 yıl",
};
const STATUS = {
  open: "Açık", filled: "Gerçekleşti", cancelled: "İptal", expired: "Süresi doldu", rejected: "Reddedildi",
};
const TYPES = { market: "Piyasa", limit: "Limit", stop: "Stop" };
// Dark categorical slots 1-7, validated on the card surface. Slot 8 (red) is left
// out so a holding never looks like a loss.
const SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"];
const TICKS = [[2500, 2.5], [1000, 1], [500, 0.5], [250, 0.25], [100, 0.1], [50, 0.05], [20, 0.02], [0, 0.01]];

const store = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(`marketalyzer.${key}`);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`marketalyzer.${key}`, JSON.stringify(value));
    } catch {
      // Storage can be unavailable (private mode); the app works without it.
    }
  },
};

const state = {
  meta: null,
  paper: null,
  quotes: {},
  observers: [],
  watchlist: store.get("watchlist", ["XU100", "THYAO", "GARAN", "ASELS", "BIMAS", "KCHOL"]),
  symbol: store.get("symbol", "THYAO"),
  span: store.get("span", "3A"),
  ticket: { side: "buy", type: "market", tif: "day", qty: 10, price: "" },
  ordersTab: "open",
  stepInterval: "1d",
};

// ---------------------------------------------------------------- helpers

async function api(path, body) {
  const options = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const response = await fetch(path, { ...options, credentials: "same-origin" });
  if (response.status === 401) {
    location.href = "/";
    throw new Error("Oturum gerekli.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc?.at(-1) ?? ""}: ${d.msg}`).join("\n")
      : data.detail;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return data;
}

function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toast").appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 6000 : 3000);
}

const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const arrow = (v) => (v > 0 ? "▲ " : v < 0 ? "▼ " : "");
const pill = (v) => (Number.isFinite(v) ? `<span class="pill ${tone(v)}">${arrow(v)}${esc(fmtPct(v))}</span>` : "");
const signedMoney = (v) =>
  new Intl.NumberFormat("tr-TR", {
    style: "currency", currency: "TRY", signDisplay: "exceptZero", maximumFractionDigits: 2,
  }).format(v);
const pctText = (v) => (Number.isFinite(v) ? `${arrow(v)}${fmtPct(v)}` : "—");
const initials = (symbol) => esc(String(symbol).replace(/[^A-Z0-9]/gi, "").slice(0, 2));
const normalize = (text) => String(text || "").trim().toUpperCase().replace(/\.(IS|E)$/, "");
const tickSize = (price) => TICKS.find(([floor]) => price >= floor)?.[1] ?? 0.01;
const dateOnly = (offsetDays) => {
  const day = new Date(Date.now() + offsetDays * 864e5);
  return day.toISOString().slice(0, 10);
};

function busy(button, on, label) {
  if (!button) return;
  if (on) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = label || "Çalışıyor…";
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

function mountChart(host, draw) {
  draw();
  state.observers.push(responsive(host, draw));
}

// A chart card body with a table twin (the accessible view of every chart).
function chartWithTable(host, draw, table) {
  host.innerHTML = `<div class="chart-slot"></div><div class="table-wrap" hidden></div>`;
  const slot = $(".chart-slot", host);
  const wrap = $(".table-wrap", host);
  mountChart(slot, () => draw(slot));
  return () => {
    const showTable = wrap.hidden;
    if (showTable) wrap.innerHTML = table();
    wrap.hidden = !showTable;
    slot.hidden = showTable;
    return showTable;
  };
}

function tableHtml(columns, rows) {
  if (!rows.length) return '<div class="empty">Kayıt yok.</div>';
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((c) => {
    const value = c.html ? c.html(row) : esc(c.format ? c.format(row[c.key], row) : row[c.key]);
    return `<td class="${c.num ? "num" : ""}">${value}</td>`;
  }).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function tile(label, value, { cls = "", note = "" } = {}) {
  return `<div class="stat"><div class="label">${esc(label)}</div>
    <div class="value ${cls}">${esc(value)}</div>${note ? `<div class="note">${esc(note)}</div>` : ""}</div>`;
}

function emptyAccount(container, onOpen) {
  container.innerHTML = `<div class="empty"><b>Henüz sanal hesap yok</b>
    100.000 TL ile başlayan bir hesap açıp işlemleri gerçek para olmadan deneyin.</div>
    <div style="text-align:center"><button class="btn primary" type="button">Sanal hesap aç</button></div>`;
  $("button", container).onclick = async (event) => {
    busy(event.currentTarget, true);
    try {
      await api("/api/paper/init", {});
      toast("Sanal hesap açıldı.");
      await loadPaper();
      onOpen();
    } catch (error) {
      toast(error.message, "error");
      busy(event.currentTarget, false);
    }
  };
}

async function loadPaper() {
  try {
    state.paper = await api("/api/paper");
  } catch (error) {
    toast(error.message, "error");
  }
  $("#side-equity").textContent = state.paper?.exists ? fmtMoney(state.paper.equity) : "—";
  return state.paper;
}

async function loadQuotes(symbols) {
  const list = symbols.filter(Boolean);
  if (!list.length) return [];
  const quotes = await api(`/api/watchlist?symbols=${encodeURIComponent(list.join(","))}`);
  for (const quote of quotes) state.quotes[quote.symbol] = quote;
  return quotes;
}

function strategyOptions(selected, { none = false } = {}) {
  const names = Object.keys(state.meta?.strategies ?? {});
  const options = names.map((name) =>
    `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(name)}</option>`);
  if (none) options.unshift(`<option value="" ${selected ? "" : "selected"}>Strateji yok (yalnızca emirler)</option>`);
  return options.join("");
}

function paramFields(strategy, values = {}) {
  const params = state.meta?.strategies?.[strategy]?.params ?? {};
  return Object.entries(params).map(([name, value]) => `
    <label class="field">${esc(PARAM_TEXT[name] || name)}
      <input name="param:${esc(name)}" type="number" step="any" value="${esc(values[name] ?? value)}">
    </label>`).join("");
}

function readParams(form) {
  const params = {};
  for (const input of form.querySelectorAll("[name^='param:']")) {
    if (input.value !== "") params[input.name.slice(6)] = Number(input.value);
  }
  return params;
}

function selectSymbol(symbol) {
  const code = normalize(symbol);
  if (!code) return;
  state.symbol = code;
  store.set("symbol", code);
  const current = currentRoute().id;
  if (current === "panel") {
    renderWatchlist();
    loadPrice();
  } else if (current === "islem") {
    renderTicket();
  } else {
    const input = $("#view input[name=symbol]");
    if (input) input.value = code;
  }
}

// ---------------------------------------------------------------- routing

const ROUTES = [
  { id: "panel", title: "Panel", icon: "panel", render: renderPanel },
  { id: "islem", title: "İşlem", icon: "trade", render: renderTrade },
  { id: "backtest", title: "Backtest", icon: "backtest", render: renderBacktest },
  { id: "walkforward", title: "Walk-forward", icon: "walk", render: renderWalkForward },
];

function currentRoute() {
  const id = location.hash.replace(/^#\//, "") || "panel";
  return ROUTES.find((r) => r.id === id) ?? ROUTES[0];
}

function route() {
  const current = currentRoute();
  document.querySelectorAll("[data-route]").forEach((link) => {
    const active = link.dataset.route === current.id;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("#page-title").textContent = current.title;
  document.title = `${current.title} · marketalyzer`;
  state.observers.forEach((observer) => observer.disconnect());
  state.observers = [];
  const view = $("#view");
  view.replaceChildren();
  current.render(view);
}

// ---------------------------------------------------------------- panel

function renderPanel(view) {
  view.innerHTML = `
    <div class="grid">
      <section class="card span-8" id="hero"></section>
      <div class="stack span-4">
        <div class="stats" id="stats"></div>
        <section class="card" id="allocation" hidden></section>
      </div>
      <div class="section-head span-12">
        <h2>İzleme listesi</h2>
        <span class="spacer"></span>
        <form id="add-symbol" class="search compact">
          ${icon("plus")}
          <input name="symbol" placeholder="Sembol ekle" maxlength="12" autocomplete="off" aria-label="İzleme listesine sembol ekle">
        </form>
      </div>
      <div class="watchlist span-12" id="watchlist"></div>
      <div class="section-head span-12" id="market-head"></div>
      <section class="card span-8" id="price-card"></section>
      <section class="card span-4" id="positions"></section>
      <section class="card span-12" id="activity"></section>
    </div>`;
  $("#add-symbol").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = event.currentTarget.elements.symbol;
    const code = normalize(input.value);
    input.value = "";
    if (!code) return;
    if (!state.watchlist.includes(code)) {
      state.watchlist.push(code);
      store.set("watchlist", state.watchlist);
    }
    selectSymbol(code);
    loadWatchlist();
  });
  renderMarketHead();
  loadPaper().then(renderAccountWidgets);
  loadWatchlist();
  loadPrice();
}

function renderAccountWidgets() {
  renderHero();
  renderStats();
  renderAllocation();
  renderPositions();
  renderActivity();
}

function renderHero() {
  const box = $("#hero");
  if (!box) return;
  const p = state.paper;
  if (!p?.exists) {
    emptyAccount(box, renderAccountWidgets);
    return;
  }
  box.innerHTML = `
    <div class="card-head">
      <div class="hero-label">Toplam varlık</div>
      <span class="spacer"></span>
      <button class="btn small" type="button" id="step">${icon("play")} Veriyi işle</button>
    </div>
    <div class="hero-value">${esc(fmtMoney(p.equity))}</div>
    <div class="hero-meta">
      ${pill(p.return_pct)}
      <span>Başlangıç ${esc(fmtMoney(p.initial_cash, 0))}</span>
      <span>Nakit ${esc(fmtMoney(p.cash))}</span>
      <span>Alım gücü ${esc(fmtMoney(p.buying_power))}</span>
    </div>
    <div id="equity-chart" style="margin-top:16px"></div>`;
  // The chart fills what the card has left once the row height is settled.
  const host = $("#equity-chart");
  mountChart(host, () => lineChart(host, {
    series: [{ name: "Özsermaye", color: token("--accent"), points: p.equity_curve }],
    height: Math.max(210, host.clientHeight),
    format: (v) => fmtMoney(v, 0),
    label: "Özsermaye grafiği",
    emptyText: "Özsermaye geçmişi, “Veriyi işle” her çalıştığında birikir.",
  }));
  $("#step").onclick = async (event) => {
    const symbols = new Set([state.symbol, ...p.positions.map((x) => x.symbol),
      ...p.open_orders.map((o) => o.symbol)]);
    await runStep(event.currentTarget, { symbols: [...symbols], interval: "1d" });
    renderAccountWidgets();
  };
}

async function runStep(button, body) {
  busy(button, true, "İşleniyor…");
  try {
    const report = await api("/api/paper/step", body);
    const errors = Object.entries(report.errors);
    const parts = [`${report.bars} yeni bar`, `${report.fills.length} işlem`, `${report.orders.length} yeni emir`];
    toast(parts.join(" · "));
    errors.forEach(([symbol, message]) => toast(`${symbol}: ${message}`, "error"));
    await loadPaper();
    return report;
  } catch (error) {
    toast(error.message, "error");
    return null;
  } finally {
    busy(button, false);
  }
}

function renderStats() {
  const box = $("#stats");
  if (!box) return;
  const p = state.paper;
  if (!p?.exists) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = [
    tile("Gerçekleşen K/Z", signedMoney(p.realized_pnl), { cls: `${tone(p.realized_pnl)}-text` }),
    tile("Gerçekleşmemiş K/Z", signedMoney(p.unrealized_pnl), { cls: `${tone(p.unrealized_pnl)}-text` }),
    tile("Net temettü", fmtMoney(p.dividends), { note: `Stopaj %${fmtNumber(p.settings.dividend_tax * 100, 0)}` }),
    tile("Açık emir", String(p.open_orders.length), { note: `${p.positions.length} açık pozisyon` }),
  ].join("");
}

function renderAllocation() {
  const box = $("#allocation");
  if (!box) return;
  const p = state.paper;
  if (!p?.exists) {
    box.hidden = true;
    return;
  }
  // Positions arrive sorted by symbol, so a holding keeps its color.
  const holdings = p.positions.map((x, i) => ({
    label: x.symbol,
    value: x.market_value ?? x.qty * x.avg_cost,
    color: SLOTS[i],
  }));
  const shown = holdings.slice(0, SLOTS.length - 1);
  const rest = holdings.slice(SLOTS.length - 1);
  if (rest.length) {
    shown.push({ label: `Diğer (${rest.length})`, value: rest.reduce((a, b) => a + b.value, 0), color: token("--muted") });
  }
  shown.push({ label: "Nakit", value: p.cash, color: token("--text-2") });
  const total = shown.reduce((a, b) => a + b.value, 0) || 1;
  box.hidden = false;
  box.innerHTML = `
    <div class="card-head"><h2>Dağılım</h2><span class="spacer"></span>
      <span class="sub">${esc(fmtMoney(total, 0))}</span></div>
    <div class="alloc-bar" role="img" aria-label="Portföy dağılımı">
      ${shown.filter((x) => x.value > 0).map((x) =>
        `<span style="flex:${x.value / total};background:${x.color}" title="${esc(x.label)}"></span>`).join("")}
    </div>
    <div class="list">${shown.map((x) => `
      <div class="list-row compact">
        <span class="swatch" style="background:${x.color}"></span>
        <div class="grow"><b>${esc(x.label)}</b></div>
        <div class="end"><b>%${esc(fmtNumber((x.value / total) * 100, 1))}</b><span>${esc(fmtMoney(x.value, 0))}</span></div>
      </div>`).join("")}</div>`;
}

async function loadWatchlist() {
  const box = $("#watchlist");
  if (!box) return;
  renderWatchlist();
  box.classList.add("loading");
  try {
    await loadQuotes(state.watchlist);
  } catch (error) {
    toast(error.message, "error");
  }
  box.classList.remove("loading");
  renderWatchlist();
}

function renderWatchlist() {
  const box = $("#watchlist");
  if (!box) return;
  if (!state.watchlist.length) {
    box.innerHTML = '<div class="empty">İzleme listesi boş. Yukarıdan sembol ekleyin.</div>';
    return;
  }
  box.innerHTML = state.watchlist.map((symbol) => {
    const q = state.quotes[symbol];
    const ok = q && !q.error;
    return `
      <div class="ticker ${symbol === state.symbol ? "active" : ""}" data-symbol="${esc(symbol)}" role="button" tabindex="0" aria-label="${esc(symbol)} grafiğini göster">
        <button class="icon-btn small remove" type="button" data-remove="${esc(symbol)}" aria-label="${esc(symbol)} listeden çıkar">${icon("x")}</button>
        <div class="top">
          <span class="avatar">${initials(symbol)}</span>
          <div class="name"><b>${esc(symbol)}</b><span>${esc(q?.error ? "Veri alınamadı" : q?.name || "Borsa İstanbul")}</span></div>
        </div>
        <div class="row">
          <span class="price">${ok ? esc(fmtNumber(q.last)) : "—"}</span>${ok ? pill(q.change_pct) : ""}
        </div>
        <div class="spark"></div>
      </div>`;
  }).join("");
  for (const card of box.querySelectorAll(".ticker")) {
    const q = state.quotes[card.dataset.symbol];
    const slot = $(".spark", card);
    if (q?.spark) {
      slot.appendChild(sparkline(q.spark, {
        width: Math.max(60, slot.clientWidth), height: 34, rising: q.change_pct >= 0,
      }));
    }
  }
  box.onclick = (event) => {
    const remove = event.target.closest("[data-remove]");
    if (remove) {
      event.stopPropagation();
      state.watchlist = state.watchlist.filter((s) => s !== remove.dataset.remove);
      store.set("watchlist", state.watchlist);
      renderWatchlist();
      return;
    }
    const card = event.target.closest(".ticker");
    if (card) selectSymbol(card.dataset.symbol);
  };
  box.onkeydown = (event) => {
    const card = event.target.closest(".ticker");
    if (card && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      selectSymbol(card.dataset.symbol);
    }
  };
}

function renderMarketHead(data) {
  const head = $("#market-head");
  if (!head) return;
  const name = data?.name || state.quotes[state.symbol]?.name || "";
  head.innerHTML = `
    <h2>${esc(state.symbol)}</h2><span class="sub">${esc(name)}</span>
    <span class="spacer"></span>
    <div class="segmented" role="group" aria-label="Zaman aralığı">
      ${Object.entries(SPANS).map(([code, label]) =>
        `<button type="button" data-span="${code}" title="${esc(label)}" class="${code === state.span ? "active" : ""}">${code}</button>`).join("")}
    </div>`;
  head.onclick = (event) => {
    const button = event.target.closest("[data-span]");
    if (!button) return;
    state.span = button.dataset.span;
    store.set("span", state.span);
    renderMarketHead(data);
    loadPrice();
  };
}

async function loadPrice() {
  const card = $("#price-card");
  if (!card) return;
  renderMarketHead();
  card.classList.add("loading");
  if (!card.innerHTML) card.innerHTML = '<div class="chart-empty">Yükleniyor…</div>';
  try {
    const data = await api(`/api/prices?symbol=${encodeURIComponent(state.symbol)}&span=${state.span}`);
    if (data.symbol !== state.symbol) return;
    renderMarketHead(data);
    drawPrice(card, data);
  } catch (error) {
    card.innerHTML = `<div class="chart-empty">${esc(error.message)}</div>`;
  } finally {
    card.classList.remove("loading");
  }
}

function drawPrice(card, data) {
  card.innerHTML = `
    <div class="card-head">
      <div>
        <div class="hero-label">Son fiyat · ${esc(SPANS[data.span])} değişim</div>
        <div style="font-size:28px;font-weight:650;letter-spacing:-0.02em">${esc(fmtMoney(data.last))}</div>
      </div>
      ${pill(data.change_pct)}
      <span class="spacer"></span>
      <button class="icon-btn small" type="button" id="price-toggle" title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button>
    </div>
    <div id="price-body"></div>`;
  const toggle = chartWithTable($("#price-body"),
    (slot) => candleChart(slot, data.rows, { height: 300, label: `${data.symbol} fiyat grafiği` }),
    () => tableHtml([
      { key: "t", label: "Zaman", format: tipTime },
      { key: "o", label: "Açılış", num: true, format: (v) => fmtNumber(v) },
      { key: "h", label: "Yüksek", num: true, format: (v) => fmtNumber(v) },
      { key: "l", label: "Düşük", num: true, format: (v) => fmtNumber(v) },
      { key: "c", label: "Kapanış", num: true, format: (v) => fmtNumber(v) },
    ], data.rows.slice(-60).reverse()));
  $("#price-toggle").onclick = (event) => {
    const table = toggle();
    event.currentTarget.innerHTML = icon(table ? "chart" : "table");
  };
}

function renderPositions() {
  const box = $("#positions");
  if (!box) return;
  const p = state.paper;
  const positions = p?.exists ? p.positions : [];
  box.innerHTML = `
    <div class="card-head"><h2>Pozisyonlar</h2><span class="spacer"></span>
      <span class="chip">${positions.length}</span></div>
    ${positions.length ? `<div class="list">${positions.map((x) => {
      const cost = x.qty * x.avg_cost;
      const pct = cost ? (x.unrealized_pnl / cost) * 100 : 0;
      return `<div class="list-row">
        <span class="avatar">${initials(x.symbol)}</span>
        <div class="grow"><b>${esc(x.symbol)}</b><span>${esc(fmtNumber(x.qty, 0))} adet · ort. ${esc(fmtNumber(x.avg_cost))}</span></div>
        <div class="end"><b>${esc(x.market_value === null ? "—" : fmtMoney(x.market_value))}</b>${pill(pct)}</div>
      </div>`;
    }).join("")}</div>`
    : '<div class="empty"><b>Açık pozisyon yok</b><a href="#/islem">İşlem sekmesinden</a> emir verebilirsiniz.</div>'}`;
}

function renderActivity() {
  const box = $("#activity");
  if (!box) return;
  const fills = state.paper?.exists ? state.paper.fills.slice().reverse().slice(0, 8) : [];
  box.innerHTML = `
    <div class="card-head"><h2>Son işlemler</h2></div>
    ${fills.length ? `<div class="list">${fills.map((f) => `
      <div class="list-row">
        <span class="avatar ${f.side === "buy" ? "up-text" : "down-text"}">${icon(f.side === "buy" ? "down" : "up")}</span>
        <div class="grow"><b>${esc(f.symbol)} ${f.side === "buy" ? "alış" : "satış"}</b>
          <span>${esc(fmtNumber(f.qty, 0))} adet @ ${esc(fmtNumber(f.price))} · ${esc(tipTime(f.time))}</span></div>
        <div class="end"><b>${esc(fmtMoney(f.qty * f.price))}</b><span>maliyet ${esc(fmtMoney(f.commission))}</span></div>
      </div>`).join("")}</div>`
    : '<div class="empty">Henüz gerçekleşen işlem yok.</div>'}`;
}

// ---------------------------------------------------------------- trading

function renderTrade(view) {
  view.innerHTML = `
    <div class="grid">
      <section class="card span-5" id="ticket"></section>
      <div class="stack span-7">
        <section class="card" id="orders"></section>
        <section class="card" id="auto"></section>
        <section class="card" id="settings"></section>
      </div>
    </div>`;
  loadPaper().then(() => {
    renderTicket();
    renderOrders();
    renderAuto();
    renderSettings();
  });
}

function refreshTrade() {
  renderTicket();
  renderOrders();
  renderSettings();
}

function renderTicket() {
  const box = $("#ticket");
  if (!box) return;
  const p = state.paper;
  if (!p?.exists) {
    emptyAccount(box, refreshTrade);
    return;
  }
  const t = state.ticket;
  const seg = (name, options) => options.map(([value, label]) =>
    `<button type="button" data-${name}="${value}" class="${name === "side" ? value : ""} ${t[name] === value ? "active" : ""}">${label}</button>`).join("");
  box.innerHTML = `
    <div class="card-head"><h2>Emir</h2><span class="spacer"></span>
      <span class="chip">Alım gücü ${esc(fmtMoney(p.buying_power))}</span></div>
    <form class="form" id="ticket-form" novalidate>
      <div class="segmented block" role="group" aria-label="Yön">${seg("side", [["buy", "Al"], ["sell", "Sat"]])}</div>
      <label class="field">Sembol
        <input name="symbol" value="${esc(state.symbol)}" maxlength="12" autocomplete="off" spellcheck="false">
      </label>
      <div class="summary-rows">
        <div><span>Son fiyat</span><b id="ticket-last">—</b></div>
        <div><span>Elde</span><b id="ticket-held">—</b></div>
      </div>
      <label class="field">Adet
        <div class="stepper">
          <button type="button" data-step="-1" aria-label="Azalt">−</button>
          <input name="qty" type="number" min="1" step="1" inputmode="numeric" value="${esc(t.qty)}">
          <button type="button" data-step="1" aria-label="Artır">+</button>
        </div>
      </label>
      <div class="segmented block" role="group" aria-label="Emir tipi">${seg("type", [["market", "Piyasa"], ["limit", "Limit"], ["stop", "Stop"]])}</div>
      <label class="field" id="price-field" ${t.type === "market" ? "hidden" : ""}>${t.type === "stop" ? "Stop fiyatı" : "Limit fiyatı"} (TL)
        <input name="price" type="number" step="0.01" min="0" inputmode="decimal" value="${esc(t.price)}">
        <span class="hint" id="tick-hint"></span>
      </label>
      <div class="segmented block" role="group" aria-label="Geçerlilik">${seg("tif", [["day", "Gün"], ["gtc", "İptale kadar"]])}</div>
      <div class="summary-rows">
        <div><span>Tahmini tutar</span><b id="est-value">—</b></div>
        <div><span>Komisyon + BSMV</span><b id="est-cost">—</b></div>
      </div>
      <button class="btn block ${t.side}" id="ticket-submit" type="submit"></button>
      <p class="muted small" style="margin:0">Emir, verildikten sonraki ilk barda değerlendirilir. Fiyatlar yaklaşık 15 dk gecikmelidir.</p>
    </form>`;
  const form = $("#ticket-form");
  const update = () => {
    const symbol = normalize(form.elements.symbol.value);
    const quote = state.quotes[symbol];
    const held = p.positions.find((x) => x.symbol === symbol)?.qty ?? 0;
    const qty = Math.max(0, Math.floor(Number(form.elements.qty.value) || 0));
    const last = quote && !quote.error ? quote.last : null;
    const price = t.type === "market" ? last : Number(form.elements.price.value) || null;
    $("#ticket-last").textContent = last === null ? "—" : fmtMoney(last);
    $("#ticket-held").textContent = `${fmtNumber(held, 0)} adet`;
    const value = price ? qty * price : null;
    const s = p.settings;
    const commission = value === null ? null
      : Math.max(value * s.commission_rate, qty >= 1 ? s.min_commission : 0) * (1 + s.bsmv_rate);
    $("#est-value").textContent = value === null ? "—" : fmtMoney(value);
    $("#est-cost").textContent = commission === null ? "—" : fmtMoney(commission);
    if (t.type !== "market" && price) {
      const tick = tickSize(price);
      const onGrid = Math.abs(Math.round(price / tick) * tick - price) < 1e-9;
      $("#tick-hint").textContent = onGrid
        ? `Fiyat adımı ${fmtNumber(tick)}`
        : `Fiyat adımına uymuyor (adım ${fmtNumber(tick)})`;
      $("#tick-hint").className = `hint ${onGrid ? "" : "error"}`;
    }
    const button = $("#ticket-submit");
    button.textContent = `${symbol || "Sembol"} ${t.side === "buy" ? "al" : "sat"}`;
  };
  form.addEventListener("click", (event) => {
    const option = event.target.closest("[data-side],[data-type],[data-tif]");
    if (option) {
      for (const key of ["side", "type", "tif"]) {
        if (option.dataset[key]) t[key] = option.dataset[key];
      }
      t.qty = form.elements.qty.value;
      t.price = form.elements.price.value;
      state.symbol = normalize(form.elements.symbol.value) || state.symbol;
      renderTicket();
      return;
    }
    const step = event.target.closest("[data-step]");
    if (step) {
      const qty = Math.max(1, (Number(form.elements.qty.value) || 0) + Number(step.dataset.step));
      form.elements.qty.value = qty;
      update();
    }
  });
  form.addEventListener("input", update);
  form.elements.symbol.addEventListener("change", async () => {
    const symbol = normalize(form.elements.symbol.value);
    state.symbol = symbol;
    store.set("symbol", symbol);
    try {
      await loadQuotes([symbol]);
    } catch (error) {
      toast(error.message, "error");
    }
    update();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#ticket-submit");
    const body = {
      symbol: normalize(form.elements.symbol.value),
      side: t.side,
      qty: Math.floor(Number(form.elements.qty.value)),
      type: t.type,
      tif: t.tif,
    };
    if (t.type !== "market") body.price = Number(form.elements.price.value);
    busy(button, true, "Gönderiliyor…");
    try {
      const order = await api("/api/paper/order", body);
      toast(`Emir alındı (#${order.id}): ${order.symbol} ${order.qty} adet`);
      t.qty = body.qty;
      t.price = form.elements.price.value;
      await loadPaper();
      refreshTrade();
    } catch (error) {
      toast(error.message, "error");
      busy(button, false);
    }
  });
  update();
  if (!state.quotes[normalize(state.symbol)]) {
    loadQuotes([normalize(state.symbol)]).then(update).catch(() => {});
  }
}

function renderOrders() {
  const box = $("#orders");
  if (!box) return;
  const p = state.paper;
  const orders = p?.exists ? p.orders.slice().reverse() : [];
  const open = orders.filter((o) => o.status === "open");
  const past = orders.filter((o) => o.status !== "open");
  const shown = state.ordersTab === "open" ? open : past;
  box.innerHTML = `
    <div class="card-head"><h2>Emirler</h2><span class="spacer"></span>
      <div class="segmented" role="group" aria-label="Emir listesi">
        <button type="button" data-tab="open" class="${state.ordersTab === "open" ? "active" : ""}">Açık (${open.length})</button>
        <button type="button" data-tab="past" class="${state.ordersTab === "past" ? "active" : ""}">Geçmiş</button>
      </div></div>
    ${shown.length ? `<div class="list">${shown.map((o) => {
      const price = o.limit_price ?? o.stop_price;
      const detail = [TYPES[o.type], price ? fmtNumber(price) : null, o.tif === "gtc" ? "İptale kadar" : "Gün"]
        .filter(Boolean).join(" · ");
      const end = o.status === "open"
        ? `<button class="btn small" type="button" data-cancel="${o.id}">İptal</button>`
        : `<b>${esc(STATUS[o.status])}</b><span>${esc(o.fill_price ? `@ ${fmtNumber(o.fill_price)}` : o.reason || "")}</span>`;
      return `<div class="list-row">
        <span class="avatar ${o.side === "buy" ? "up-text" : "down-text"}">${icon(o.side === "buy" ? "down" : "up")}</span>
        <div class="grow"><b>${esc(o.symbol)} ${o.side === "buy" ? "Al" : "Sat"} ${esc(fmtNumber(o.qty, 0))}</b>
          <span>${esc(detail)} · #${o.id} · ${esc(tipTime(o.submitted_at))}</span></div>
        <div class="end">${end}</div>
      </div>`;
    }).join("")}</div>`
    : `<div class="empty">${state.ordersTab === "open" ? "Açık emir yok." : "Henüz kapanmış emir yok."}</div>`}`;
  box.onclick = async (event) => {
    const tab = event.target.closest("[data-tab]");
    if (tab) {
      state.ordersTab = tab.dataset.tab;
      renderOrders();
      return;
    }
    const cancel = event.target.closest("[data-cancel]");
    if (!cancel) return;
    busy(cancel, true, "…");
    try {
      await api(`/api/paper/cancel/${cancel.dataset.cancel}`, {});
      toast("Emir iptal edildi.");
      await loadPaper();
      refreshTrade();
    } catch (error) {
      toast(error.message, "error");
      busy(cancel, false);
    }
  };
}

function renderAuto() {
  const box = $("#auto");
  if (!box) return;
  const p = state.paper;
  const symbols = new Set([state.symbol, ...(p?.positions ?? []).map((x) => x.symbol)]);
  box.innerHTML = `
    <div class="card-head"><h2>Veriyi işle</h2></div>
    <form class="form" id="auto-form">
      <label class="field">Semboller
        <input name="symbols" value="${esc([...symbols].join(", "))}" autocomplete="off">
      </label>
      <div class="row">
        <label class="field">Strateji
          <select name="strategy">${strategyOptions("", { none: true })}</select>
        </label>
        <label class="field">Periyot
          <select name="interval">
            ${["1d", "1h", "15m", "5m"].map((v) => `<option ${v === state.stepInterval ? "selected" : ""}>${v}</option>`).join("")}
          </select>
        </label>
      </div>
      <div class="row" id="auto-params"></div>
      <button class="btn primary" type="submit">${icon("play")} Bir adım çalıştır</button>
      <p class="muted small" style="margin:0">Yeni barları işler ve emirleri eşleştirir. Strateji seçildiyse, tamamlanmış günlük barlara göre emir verir.</p>
    </form>`;
  const form = $("#auto-form");
  form.elements.strategy.addEventListener("change", () => {
    $("#auto-params").innerHTML = form.elements.strategy.value ? paramFields(form.elements.strategy.value) : "";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.paper?.exists) {
      toast("Önce bir sanal hesap açın.", "error");
      return;
    }
    state.stepInterval = form.elements.interval.value;
    const body = {
      symbols: form.elements.symbols.value.split(/[\s,]+/).map(normalize).filter(Boolean),
      interval: state.stepInterval,
      params: readParams(form),
    };
    if (form.elements.strategy.value) body.strategy = form.elements.strategy.value;
    const report = await runStep($("button[type=submit]", form), body);
    if (report) {
      const signals = Object.entries(report.signals).map(([s, w]) => `${s}: ${w ? "AL" : "NAKİT"}`);
      if (signals.length) toast(`Sinyal · ${signals.join(" · ")}`);
      refreshTrade();
    }
  });
}

function renderSettings() {
  const box = $("#settings");
  if (!box) return;
  const p = state.paper;
  const s = p?.exists ? p.settings : null;
  box.innerHTML = `
    <div class="card-head"><h2>Hesap ayarları</h2></div>
    ${s ? `<div class="summary-rows" style="margin-bottom:14px">
      <div><span>Komisyon</span><b>%${esc(fmtNumber(s.commission_rate * 100, 3))} + BSMV %${esc(fmtNumber(s.bsmv_rate * 100, 0))}</b></div>
      <div><span>Kayma (gidiş-dönüş)</span><b>%${esc(fmtNumber(s.slippage * 100, 2))}</b></div>
      <div><span>Temettü stopajı</span><b>%${esc(fmtNumber(s.dividend_tax * 100, 0))}</b></div>
    </div>` : ""}
    <details class="more"><summary>Hesabı sıfırla</summary>
      <form class="form" id="reset-form">
        <div class="row">
          <label class="field">Başlangıç (TL)<input name="cash" type="number" value="100000" min="1"></label>
          <label class="field">Komisyon oranı<input name="commission" type="number" step="0.0001" value="${s?.commission_rate ?? 0.002}"></label>
        </div>
        <div class="row">
          <label class="field">Kayma oranı<input name="slippage" type="number" step="0.0001" value="${s?.slippage ?? 0.001}"></label>
          <label class="field">Temettü stopajı<input name="dividend_tax" type="number" step="0.01" value="${s?.dividend_tax ?? 0.15}"></label>
        </div>
        <button class="btn" type="submit">Hesabı sil ve yeniden aç</button>
      </form>
    </details>`;
  $("#reset-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!confirm("Mevcut sanal hesap ve tüm işlemleri silinecek. Devam edilsin mi?")) return;
    const form = event.currentTarget;
    const body = { reset: true };
    for (const name of ["cash", "commission", "slippage", "dividend_tax"]) body[name] = Number(form.elements[name].value);
    try {
      await api("/api/paper/init", body);
      toast("Sanal hesap yeniden açıldı.");
      await loadPaper();
      refreshTrade();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

// ---------------------------------------------------------------- backtest

function strategyForm({ id, extra = "", submit }) {
  return `
    <form class="form" id="${id}">
      <label class="field">Sembol<input name="symbol" value="${esc(state.symbol)}" maxlength="12" autocomplete="off" required></label>
      <label class="field">Strateji<select name="strategy">${strategyOptions("sma_cross")}</select>
        <span class="hint" data-doc>${esc(STRATEGY_TEXT.sma_cross)}</span></label>
      ${extra}
      <details class="more"><summary>Maliyetler ve sermaye</summary>
        <div class="form">
          <div class="row">
            <label class="field">Başlangıç (TL)<input name="cash" type="number" value="100000" min="1"></label>
            <label class="field">Komisyon oranı<input name="commission" type="number" step="0.0001" value="0.002"></label>
          </div>
          <label class="field">Kayma oranı (gidiş-dönüş)<input name="slippage" type="number" step="0.0001" value="0.001"></label>
        </div>
      </details>
      <button class="btn primary block" type="submit">${esc(submit)}</button>
    </form>`;
}

function bindStrategyDoc(form, onChange) {
  form.elements.strategy.addEventListener("change", () => {
    $("[data-doc]", form).textContent = STRATEGY_TEXT[form.elements.strategy.value] || "";
    onChange?.();
  });
}

function common(form) {
  return {
    symbol: normalize(form.elements.symbol.value),
    strategy: form.elements.strategy.value,
    cash: Number(form.elements.cash.value),
    commission: Number(form.elements.commission.value),
    slippage: Number(form.elements.slippage.value),
  };
}

function renderBacktest(view) {
  view.innerHTML = `
    <div class="grid">
      <section class="card span-4" id="bt-card"></section>
      <div class="stack span-8" id="bt-results">
        <section class="card"><div class="empty"><b>Bir strateji deneyin</b>
          Sonuçlar komisyon, BSMV ve kayma dahil hesaplanır; XU100 ve USD bazında getiriyle karşılaştırılır.</div></section>
      </div>
    </div>`;
  const extra = `
    <div class="row">
      <label class="field">Başlangıç<input name="start" type="date" value="${dateOnly(-3 * 365)}"></label>
      <label class="field">Bitiş<input name="end" type="date"></label>
    </div>
    <div class="row" id="bt-params">${paramFields("sma_cross")}</div>
    <label class="check"><input type="checkbox" name="optimize"> Parametreleri optimize et (son %30 teste ayrılır)</label>`;
  $("#bt-card").innerHTML = `<div class="card-head"><h2>Backtest</h2></div>${strategyForm({ id: "bt-form", extra, submit: "Backtest çalıştır" })}`;
  const form = $("#bt-form");
  bindStrategyDoc(form, () => { $("#bt-params").innerHTML = paramFields(form.elements.strategy.value); });
  form.elements.optimize.addEventListener("change", () => {
    $("#bt-params").hidden = form.elements.optimize.checked;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const results = $("#bt-results");
    const body = {
      ...common(form),
      params: readParams(form),
      optimize: form.elements.optimize.checked,
    };
    if (form.elements.start.value) body.start = form.elements.start.value;
    if (form.elements.end.value) body.end = form.elements.end.value;
    state.symbol = body.symbol;
    store.set("symbol", body.symbol);
    busy(button, true, body.optimize ? "Optimize ediliyor…" : "Çalışıyor…");
    results.classList.add("loading");
    try {
      showBacktest(results, await api("/api/backtest", body));
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
      results.classList.remove("loading");
    }
  });
}

function summaryTiles(r) {
  return [
    tile("Getiri", pctText(r.return_pct), { cls: `${tone(r.return_pct)}-text`, note: `Son bakiye ${fmtMoney(r.equity_final, 0)}` }),
    tile("Al ve tut", pctText(r.buy_hold_return_pct)),
    r.benchmark_return_pct !== undefined ? tile("XU100", pctText(r.benchmark_return_pct)) : "",
    r.return_usd_pct !== undefined ? tile("USD bazında", pctText(r.return_usd_pct), { cls: `${tone(r.return_usd_pct)}-text`, note: `USD/TRY ${pctText(r.usdtry_change_pct)}` }) : "",
    tile("Sharpe", r.sharpe === null ? "—" : fmtNumber(r.sharpe)),
    tile("En büyük düşüş", pctText(r.max_drawdown_pct)),
    tile("İşlem", String(r.trades), { note: r.win_rate_pct === null ? "" : `Kazançlı %${fmtNumber(r.win_rate_pct, 0)}` }),
    tile("Komisyon", fmtMoney(r.commissions, 0)),
  ].join("");
}

function showBacktest(results, r) {
  const main = r.out_of_sample || r.in_sample || r;
  const params = r.best_params || main.params;
  const optimized = Boolean(r.best_params);
  results.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>${esc(main.symbol || "")} · ${esc(main.strategy)}</h2>
        <span class="sub">${esc(fmtDate(main.start))} – ${esc(fmtDate(main.end))}</span>
        <span class="spacer"></span>
        <a class="btn small" href="${esc(r.plot)}" target="_blank" rel="noopener">${icon("external")} Ayrıntılı grafik</a></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
        ${Object.entries(params).map(([k, v]) => `<span class="chip">${esc(PARAM_TEXT[k] || k)}: ${esc(v)}</span>`).join("")}
        ${optimized ? `<span class="chip">${r.out_of_sample ? "Sonuçlar: test dönemi (görülmemiş veri)" : "Sonuçlar: eğitim dönemi"}</span>` : ""}
      </div>
      <div class="tiles">${summaryTiles(main)}</div>
    </section>
    ${optimized && r.out_of_sample ? `
    <section class="card">
      <div class="card-head"><h2>Eğitim ve test karşılaştırması</h2></div>
      <div class="table-wrap">${tableHtml([
        { key: "name", label: "Dönem" },
        { key: "return_pct", label: "Getiri", num: true, format: pctText },
        { key: "buy_hold_return_pct", label: "Al ve tut", num: true, format: pctText },
        { key: "sharpe", label: "Sharpe", num: true, format: (v) => (v === null ? "—" : fmtNumber(v)) },
        { key: "max_drawdown_pct", label: "En büyük düşüş", num: true, format: pctText },
        { key: "trades", label: "İşlem", num: true },
      ], [{ name: "Eğitim", ...r.in_sample }, { name: "Test", ...r.out_of_sample }])}</div>
      <p class="muted small">Eğitimde iyi, testte zayıfsa parametreler geçmişe uydurulmuş olabilir.</p>
    </section>` : ""}
    <section class="card">
      <div class="card-head"><h2>Strateji ve al-tut</h2><span class="spacer"></span>
        <button class="icon-btn small" type="button" id="bt-toggle" title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button></div>
      <div id="bt-chart"></div>
    </section>
    <section class="card">
      <div class="card-head"><h2>İşlemler</h2><span class="spacer"></span><span class="chip">${r.trade_list.length}</span></div>
      <div class="table-wrap">${tableHtml([
        { key: "entry_time", label: "Giriş", format: tipTime },
        { key: "exit_time", label: "Çıkış", format: tipTime },
        { key: "size", label: "Adet", num: true, format: (v) => fmtNumber(v, 0) },
        { key: "entry_price", label: "Giriş fiyatı", num: true, format: (v) => fmtNumber(v) },
        { key: "exit_price", label: "Çıkış fiyatı", num: true, format: (v) => fmtNumber(v) },
        { key: "pnl", label: "K/Z", num: true, html: (t) => `<span class="${tone(t.pnl)}-text">${esc(signedMoney(t.pnl))}</span>` },
        { key: "return_pct", label: "Getiri", num: true, html: (t) => pill(t.return_pct) },
      ], r.trade_list.slice().reverse())}</div>
    </section>`;
  const series = [
    { name: "Strateji", color: token("--accent"), points: r.equity },
    { name: "Al ve tut", color: token("--series-2"), points: r.hold },
  ].filter((s) => s.points.length);
  const toggle = chartWithTable($("#bt-chart"),
    (slot) => lineChart(slot, { series, height: 260, format: (v) => fmtMoney(v, 0), label: "Strateji ve al-tut özsermayesi" }),
    () => tableHtml([
      { key: "t", label: "Tarih", format: tipTime },
      { key: "v", label: "Strateji", num: true, format: (v) => fmtMoney(v) },
      { key: "h", label: "Al ve tut", num: true, format: (v) => (v === undefined ? "—" : fmtMoney(v)) },
    ], r.equity.map((p, i) => ({ ...p, h: r.hold[i]?.v })).slice(-120).reverse()));
  $("#bt-toggle").onclick = (event) => {
    event.currentTarget.innerHTML = icon(toggle() ? "chart" : "table");
  };
}

// ---------------------------------------------------------------- walk-forward

function renderWalkForward(view) {
  view.innerHTML = `
    <div class="grid">
      <section class="card span-4" id="wf-card"></section>
      <div class="stack span-8" id="wf-results">
        <section class="card"><div class="empty"><b>Geçmişten ileriye dönük test</b>
          Her pencerede parametreler önceki dönemde optimize edilir, sonraki dönem sanal hesapta işlenir.
          Yalnızca bu ileri dönemler sayılır. Birkaç dakika sürebilir.</div></section>
      </div>
    </div>`;
  const extra = `
    <div class="row">
      <label class="field">Başlangıç<input name="start" type="date" value="2018-01-01" required></label>
      <label class="field">Bitiş<input name="end" type="date"></label>
    </div>
    <div class="row">
      <label class="field">Eğitim penceresi (gün)<input name="train" type="number" value="504" min="20"></label>
      <label class="field">Test penceresi (gün)<input name="test" type="number" value="126" min="1"></label>
    </div>`;
  $("#wf-card").innerHTML = `<div class="card-head"><h2>Walk-forward</h2></div>${strategyForm({ id: "wf-form", extra, submit: "Testi başlat" })}`;
  const form = $("#wf-form");
  bindStrategyDoc(form);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const results = $("#wf-results");
    const body = {
      ...common(form),
      start: form.elements.start.value,
      train: Number(form.elements.train.value),
      test: Number(form.elements.test.value),
    };
    if (form.elements.end.value) body.end = form.elements.end.value;
    busy(button, true, "Pencereler işleniyor…");
    results.classList.add("loading");
    try {
      showWalkForward(results, await api("/api/walkforward", body));
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
      results.classList.remove("loading");
    }
  });
}

function showWalkForward(results, r) {
  results.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>${esc(r.symbol)} · ${esc(r.strategy)}</h2>
        <span class="sub">${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))} · ${r.windows.length} pencere</span></div>
      <div class="tiles">
        ${tile("İleri dönem getirisi", pctText(r.return_pct), { cls: `${tone(r.return_pct)}-text`, note: `Son özsermaye ${fmtMoney(r.equity, 0)}` })}
        ${tile("Al ve tut", pctText(r.buy_hold_return_pct))}
        ${tile("En büyük düşüş", pctText(r.max_drawdown_pct))}
        ${tile("İşlem", String(r.trades))}
        ${tile("Komisyon", fmtMoney(r.commissions, 0))}
        ${tile("Net temettü", fmtMoney(r.dividends, 0))}
      </div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Sanal hesap özsermayesi</h2><span class="sub">Dikey çizgiler pencere başlangıçları</span>
        <span class="spacer"></span>
        <button class="icon-btn small" type="button" id="wf-toggle" title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button></div>
      <div id="wf-chart"></div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Pencereler</h2></div>
      <div class="table-wrap">${tableHtml([
        { key: "test_start", label: "Test başı", format: tipTime },
        { key: "test_end", label: "Test sonu", format: tipTime },
        { key: "params", label: "Parametreler", format: (p) => Object.entries(p).map(([k, v]) => `${k}=${v}`).join(", ") },
        { key: "in_sample_sharpe", label: "Eğitim Sharpe", num: true, format: (v) => (v === null ? "—" : fmtNumber(v)) },
        { key: "forward_return_pct", label: "İleri", num: true, html: (w) => pill(w.forward_return_pct) },
        { key: "buy_hold_return_pct", label: "Al ve tut", num: true, format: pctText },
        { key: "trades", label: "İşlem", num: true },
      ], r.windows)}</div>
    </section>`;
  const toggle = chartWithTable($("#wf-chart"),
    (slot) => lineChart(slot, {
      series: [{ name: "Özsermaye", color: token("--accent"), points: r.equity }],
      height: 240, format: (v) => fmtMoney(v, 0), markers: r.windows.map((w) => w.test_start),
      label: "Walk-forward özsermayesi",
    }),
    () => tableHtml([
      { key: "t", label: "Tarih", format: tipTime },
      { key: "v", label: "Özsermaye", num: true, format: (v) => fmtMoney(v) },
    ], r.equity.slice(-120).reverse()));
  $("#wf-toggle").onclick = (event) => {
    event.currentTarget.innerHTML = icon(toggle() ? "chart" : "table");
  };
}

// ---------------------------------------------------------------- start

function buildShell() {
  const links = ROUTES.map((r) =>
    `<a href="#/${r.id}" data-route="${r.id}">${icon(r.icon)}<span>${esc(r.title)}</span></a>`).join("");
  $("#nav").innerHTML = links;
  $("#tabbar").innerHTML = links;
  $("#page-sub").textContent = new Intl.DateTimeFormat("tr-TR", {
    weekday: "long", day: "numeric", month: "long",
  }).format(new Date());
  $("#search").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = event.currentTarget.elements.symbol;
    selectSymbol(input.value);
    input.value = "";
    input.blur();
  });
  $("#refresh").addEventListener("click", route);
  window.addEventListener("hashchange", route);
}

async function start() {
  buildShell();
  try {
    state.meta = await api("/api/meta");
    $("#demo-banner").hidden = !state.meta.demo;
  } catch (error) {
    toast(error.message, "error");
  }
  route();
  if (currentRoute().id !== "panel" && currentRoute().id !== "islem") loadPaper();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

start();
