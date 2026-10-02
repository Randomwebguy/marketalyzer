// marketalyzer web app shell: routing, sidebar, tab bar, command palette.
import {
  $, $$, api, arcGauge, emit, esc, fmtNumber, go, icon, initials, loadFx, loadPaper, logout, money, on, remote,
  session, sheet, state, store, symbolColor, toast,
} from "/static/js/core.js";

const page = (name) => () => import(`/static/js/pages/${name}.js`);

const ROUTES = [
  { id: "panel", group: "piyasa", title: "Panel", icon: "dashboard", load: page("panel") },
  { id: "piyasa", group: "piyasa", title: "Piyasa", icon: "market", load: page("market") },
  { id: "emirler", group: "piyasa", title: "Emirler", icon: "orders", badge: "orders", load: page("orders") },
  {
    id: "islem", group: "piyasa", title: "İşlem", icon: "trade", load: page("trade"),
    children: [["Emir fişi", "#/islem"], ["Strateji ile işle", "#/islem/otomatik"], ["Hesap ayarları", "#/islem/hesap"]],
  },
  { id: "strateji", group: "lab", title: "AI Strateji", icon: "target", tag: "AI", load: page("strategy") },
  { id: "lab", group: "lab", title: "Script editörü", icon: "code", load: page("lab") },
  { id: "backtest", group: "lab", title: "Backtest", icon: "flask", load: page("backtest") },
  { id: "walkforward", group: "lab", title: "Walk-forward", icon: "walk", load: page("walkforward") },
  { id: "tarama", group: "lab", title: "Tarama", icon: "filter", load: page("screener") },
  { id: "asistan", group: "lab", title: "Asistan", icon: "sparkle", tag: "AI", load: page("assistant") },
  { id: "ayarlar", group: null, title: "Ayarlar", icon: "settings", load: page("settings") },
];
const TABS = ["panel", "piyasa", "asistan", "lab", "islem"];
const SUBTITLES = {
  panel: "", piyasa: "Fiyatlar, göstergeler ve teknik özet", emirler: "Sanal hesabın emirleri ve işlemleri",
  islem: "Gerçek para kullanılmaz; emirler sanal hesapta eşleşir", lab: "Pine Script benzeri gösterge ve stratejiler",
  backtest: "BIST maliyetleriyle geçmiş veride test", walkforward: "Geçmişte optimize et, ileriye dönük test et",
  tarama: "Bir scripti birçok hissede çalıştır",
  strateji: "Sinyal araştırması, AI ile Pine Script ve kör backtest", asistan: "OpenRouter üzerinden yapay zeka", ayarlar: "API anahtarı, model ve hesap",
};

function parseHash() {
  const [id, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
  const route = ROUTES.find((r) => r.id === id) ?? ROUTES[0];
  return { route, params: rest.map(decodeURIComponent) };
}

let renderToken = 0;
let mode = store.get("mode", "piyasa");

async function render() {
  const { route, params } = parseHash();
  if (route.group) mode = route.group;
  renderNav(route);
  $("#page-title").textContent = route.title;
  $("#page-sub").textContent = SUBTITLES[route.id] ?? "";
  document.title = `${route.title} · marketalyzer`;
  document.body.dataset.route = route.id;
  state.observers.forEach((observer) => observer.disconnect());
  state.observers = [];
  state.cleanup.forEach((fn) => fn());
  state.cleanup = [];
  const view = $("#view");
  const token = ++renderToken;
  view.classList.add("loading");
  try {
    const module = await route.load();
    if (token !== renderToken) return;
    // A fresh element per page, so page listeners go away with the page.
    const host = document.createElement("div");
    host.className = `page page-${route.id}`;
    view.replaceChildren(host);
    view.classList.remove("loading");
    const cleanup = module.render(host, { params, route });
    if (typeof cleanup === "function") state.cleanup.push(cleanup);
  } catch (error) {
    view.classList.remove("loading");
    view.innerHTML = `<div class="card"><div class="empty"><b>Sayfa yüklenemedi</b>${esc(error.message)}</div></div>`;
  }
  view.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------------- sidebar

function renderNav(current = parseHash().route) {
  $$("#mode button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
  const openOrders = state.paper?.exists ? state.paper.open_orders.length : 0;
  const links = ROUTES.filter((r) => r.group === mode).map((r) => {
    const active = r.id === current.id;
    const badge = r.badge === "orders" && openOrders ? `<span class="badge amber">${openOrders}</span>` : "";
    const tag = r.tag ? `<span class="badge violet">${esc(r.tag)}</span>` : "";
    const caret = r.children ? icon(active ? "up" : "down", "sm") : "";
    const sub = r.children && active
      ? `<div class="sub">${r.children.map(([label, href]) => `<a href="${href}" class="${location.hash === href ? "active" : ""}">${esc(label)}</a>`).join("")}</div>`
      : "";
    return `<a href="#/${r.id}" data-route="${r.id}" class="${active ? "active" : ""}" ${active ? 'aria-current="page"' : ""}>
      ${icon(r.icon)}<span>${esc(r.title)}</span><span class="tail">${badge}${tag}${caret}</span></a>${sub}`;
  });
  const settings = ROUTES.at(-1);
  links.push(`<a href="#/ayarlar" class="${current.id === "ayarlar" ? "active" : ""}">${icon(settings.icon)}<span>Ayarlar</span></a>`);
  $("#nav").innerHTML = links.join("");
  $("#tabbar").innerHTML = TABS.map((id) => {
    const r = ROUTES.find((x) => x.id === id);
    const active = r.id === current.id || (id === "lab" && ["backtest", "walkforward", "tarama", "strateji"].includes(current.id));
    if (id === "asistan") {
      return `<a href="#/asistan" class="fab ${active ? "active" : ""}" aria-label="Asistan"><span class="orb">${icon("sparkle", "lg")}</span></a>`;
    }
    const label = { panel: "Panel", piyasa: "Piyasa", lab: "Lab", islem: "İşlem" }[id];
    return `<a href="#/${r.id}" class="${active ? "active" : ""}">${icon(r.icon)}<span>${label}</span></a>`;
  }).join("");
  $("#orders-badge").textContent = openOrders;
  $("#orders-badge").hidden = !openOrders;
  renderSideOrders();
}

function renderSideOrders() {
  const box = $("#side-orders");
  const p = state.paper;
  if (!p?.exists) {
    box.innerHTML = `<div class="side-account"><div class="label">Sanal hesap</div>
      <div class="value">Henüz yok</div>
      <a class="btn small" style="margin-top:10px;width:100%" href="#/islem">Hesap aç</a></div>`;
    return;
  }
  const orders = p.open_orders;
  if (!orders.length) {
    box.innerHTML = `<div class="side-account"><div class="label">Özsermaye</div>
      <div class="value">${esc(money(p.equity))}</div>
      <div class="label" style="margin-top:6px">Açık emir yok · gerçek para kullanılmaz</div></div>`;
    return;
  }
  const s = session();
  const [first, ...rest] = orders;
  const price = first.limit_price ?? first.stop_price;
  box.innerHTML = `
    <div class="order-mini open">
      <div class="who"><span class="coin" style="--c:${symbolColor(first.symbol)}">${initials(first.symbol)}</span>
        <b>${esc(first.symbol)}</b> ${first.side === "buy" ? "Al" : "Sat"} ${esc(first.qty)}
        <span class="tail">#${esc(first.id)}</span></div>
      <h4>${first.type === "market" ? "Piyasa emri" : `${first.type === "limit" ? "Limit" : "Stop"} ${esc(fmtNumber(price))}`}</h4>
      <div class="when">${esc(s.text)} ${esc(s.value)}</div>
      <div data-arc></div>
      <div class="actions">
        <button class="btn small" type="button" data-cancel="${first.id}">İptal et</button>
        <button class="icon-btn sm" type="button" data-ask-order="${first.id}" aria-label="Asistana sor">${icon("chat")}</button>
      </div>
    </div>
    ${rest.slice(0, 3).map((o) => `
      <a class="order-mini" href="#/emirler"><div class="who"><span class="coin" style="--c:${symbolColor(o.symbol)}">${initials(o.symbol)}</span>
        <b>${esc(o.symbol)}</b> ${o.side === "buy" ? "Al" : "Sat"} ${esc(o.qty)}
        <span class="tail">${icon("clock", "sm")} ${esc(s.open ? s.value : "—")}</span></div></a>`).join("")}`;
  $("[data-arc]", box).appendChild(arcGauge(s.progress, { width: 200, height: 34, active: s.open, compact: true }));
  box.onclick = async (event) => {
    const cancel = event.target.closest("[data-cancel]");
    if (cancel) {
      try {
        await api(`/api/paper/cancel/${cancel.dataset.cancel}`, {});
        toast("Emir iptal edildi.");
        await loadPaper();
      } catch (error) {
        toast(error.message, "error");
      }
    }
    const ask = event.target.closest("[data-ask-order]");
    if (ask) {
      const order = orders.find((o) => String(o.id) === ask.dataset.askOrder);
      store.set("draft", `#${order.id} numaralı ${order.symbol} ${order.side === "buy" ? "alış" : "satış"} emrimi değerlendirir misin? Hesap durumuma ve teknik görünüme bak.`);
      go("#/asistan");
    }
  };
}

// ---------------------------------------------------------------- palette

function paletteItems(query) {
  const q = query.trim().toLocaleLowerCase("tr");
  const items = [];
  const typed = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
  // Prefer a known symbol the query is the start of: "asel" means ASELS.
  const code = state.watchlist.find((s) => s.startsWith(typed)) ?? typed;
  if (typed.length >= 2) {
    items.push({ group: "Sembol", icon: "market", label: `${code} grafiğini aç`, hint: "Piyasa", run: () => go(`#/piyasa/${code}`) });
    items.push({ group: "Sembol", icon: "trade", label: `${code} için emir ver`, hint: "Emir", run: () => openOrderSheet(code) });
    items.push({ group: "Sembol", icon: "sparkle", label: `${code} teknik analizini sor`, hint: "Asistan", run: () => askAssistant(`${code} için teknik analiz yap ve önemli seviyeleri özetle.`) });
  }
  for (const r of ROUTES) items.push({ group: "Sayfalar", icon: r.icon, label: r.title, hint: SUBTITLES[r.id], run: () => go(`#/${r.id}`) });
  for (const symbol of state.watchlist) items.push({ group: "İzleme listesi", icon: "chart", label: symbol, hint: state.quotes[symbol]?.name ?? "", run: () => go(`#/piyasa/${symbol}`) });
  for (const [name, s] of Object.entries(state.meta?.strategies ?? {})) {
    if (s.kind === "script") items.push({ group: "Scriptler", icon: "code", label: s.label, hint: name, run: () => go(`#/lab/${name.slice(7)}`) });
  }
  items.push(
    { group: "Eylemler", icon: "plus", label: "Yeni script", hint: "Lab", run: () => go("#/lab/yeni") },
    { group: "Eylemler", icon: "chat", label: "Yeni sohbet", hint: "Asistan", run: () => go("#/asistan/yeni") },
    { group: "Eylemler", icon: "wallet", label: "Emir ver", hint: "Sanal hesap", run: () => openOrderSheet() },
    { group: "Eylemler", icon: state.hidden ? "eye" : "eyeoff", label: state.hidden ? "Bakiyeleri göster" : "Bakiyeleri gizle", hint: "Gizlilik", run: toggleHidden },
    { group: "Eylemler", icon: "refresh", label: "Sayfayı yenile", hint: "R", run: render },
  );
  if (!q) return items.filter((i) => i.group !== "Sembol");
  return items.filter((i) => `${i.label} ${i.hint} ${i.group}`.toLocaleLowerCase("tr").includes(q) || i.group === "Sembol");
}

export function openPalette(initial = "") {
  const { node, close } = sheet(`
    <input type="search" placeholder="Sembol, sayfa, script veya komut ara…" aria-label="Komut ara" autocomplete="off" spellcheck="false">
    <div class="items" role="listbox"></div>
    <div class="foot"><span><kbd>↑</kbd> <kbd>↓</kbd> seç</span><span><kbd>↵</kbd> aç</span><span><kbd>esc</kbd> kapat</span></div>`,
  { className: "palette", title: "" });
  const input = $("input", node);
  const list = $(".items", node);
  let items = [];
  let active = 0;
  const draw = () => {
    items = paletteItems(input.value).slice(0, 40);
    active = Math.min(active, Math.max(items.length - 1, 0));
    let group = "";
    list.innerHTML = items.map((item, i) => {
      const head = item.group !== group ? `<div class="group">${esc((group = item.group))}</div>` : "";
      return `${head}<div class="item ${i === active ? "active" : ""}" data-index="${i}" role="option">
        <span class="ico">${icon(item.icon)}</span><b>${esc(item.label)}</b><span class="hint">${esc(item.hint || "")}</span></div>`;
    }).join("") || '<div class="empty">Sonuç yok.</div>';
    $(".item.active", list)?.scrollIntoView({ block: "nearest" });
  };
  const run = (i) => {
    const item = items[i];
    if (!item) return;
    close();
    item.run();
  };
  input.value = initial;
  input.addEventListener("input", () => { active = 0; draw(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { active = Math.min(items.length - 1, active + 1); draw(); event.preventDefault(); }
    if (event.key === "ArrowUp") { active = Math.max(0, active - 1); draw(); event.preventDefault(); }
    if (event.key === "Enter") { run(active); event.preventDefault(); }
  });
  list.addEventListener("click", (event) => {
    const item = event.target.closest("[data-index]");
    if (item) run(Number(item.dataset.index));
  });
  draw();
  input.focus();
}

// ---------------------------------------------------------------- actions

export async function openOrderSheet(symbol) {
  const { renderTicket } = await import("/static/js/ticket.js");
  if (symbol) state.symbol = symbol;
  const { body, close } = sheet('<div class="ticket-host"></div>', { title: "Emir ver" });
  renderTicket($(".ticket-host", body), { onDone: close });
}

export function askAssistant(text) {
  store.set("draft", text);
  go("#/asistan/yeni");
}

function toggleHidden() {
  state.hidden = !state.hidden;
  store.set("hidden", state.hidden);
  emit("display");
}

function toggleCurrency() {
  if (!state.usdtry) {
    toast("USD/TRY kuru alınamadı.", "error");
    return;
  }
  state.currency = state.currency === "TRY" ? "USD" : "TRY";
  store.set("currency", state.currency);
  $("#currency").textContent = state.currency === "TRY" ? "₺" : "$";
  emit("display");
}

function showHelp() {
  sheet(`
    <div class="kv">
      <div><span>Komut paleti</span><b><kbd>⌘</kbd> / <kbd>Ctrl</kbd> + <kbd>K</kbd></b></div>
      <div><span>Emir ver</span><b><kbd>B</kbd></b></div>
      <div><span>Asistan</span><b><kbd>A</kbd></b></div>
      <div><span>Script çalıştır (editörde)</span><b><kbd>Ctrl</kbd> + <kbd>↵</kbd></b></div>
      <div><span>Scripti kaydet (editörde)</span><b><kbd>Ctrl</kbd> + <kbd>S</kbd></b></div>
      <div><span>Bakiyeleri gizle</span><b><kbd>H</kbd></b></div>
    </div>
    <div class="divider"></div>
    <p class="muted small" style="margin:0">marketalyzer bir araştırma ve simülasyon aracıdır. Gerçek para ve gerçek emir yoktur.
    Fiyatlar yaklaşık 15 dakika gecikmelidir. Yapay zeka yanıtları yatırım tavsiyesi değildir.</p>`, { title: "Kısayollar ve yardım" });
}

// ---------------------------------------------------------------- start

function bindShell() {
  $("#mode").addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    mode = button.dataset.mode;
    store.set("mode", mode);
    const first = ROUTES.find((r) => r.group === mode);
    go(`#/${first.id}`);
  });
  $("#ask").addEventListener("click", () => go("#/asistan"));
  $("#order-btn").addEventListener("click", () => openOrderSheet());
  $("#help").addEventListener("click", showHelp);
  $("#currency").addEventListener("click", toggleCurrency);
  $("#currency").textContent = state.currency === "TRY" ? "₺" : "$";
  window.addEventListener("hashchange", render);
  on("route", render);
  on("paper", () => renderNav());
  on("display", () => renderNav());
  on("palette", (text) => openPalette(text));
  on("order", (symbol) => openOrderSheet(symbol));
  on("ask", (text) => askAssistant(text));
  document.addEventListener("keydown", (event) => {
    const typing = event.target.closest?.("input, textarea, select, [contenteditable]");
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openPalette();
      return;
    }
    if (typing || event.metaKey || event.ctrlKey || event.altKey || $("#layer").children.length) return;
    if (event.key === "b") openOrderSheet();
    if (event.key === "a") go("#/asistan");
    if (event.key === "h") toggleHidden();
    if (event.key === "/") { event.preventDefault(); openPalette(); }
  });
  // Keep the session clocks and "updated" labels current.
  setInterval(() => emit("tick"), 1000);
  let minute = -1;
  on("tick", () => {
    const now = new Date().getMinutes();
    if (now !== minute) {
      minute = now;
      if (state.paper?.open_orders?.length) renderSideOrders();
    }
  });
}

async function start() {
  bindShell();
  renderNav();
  try {
    state.meta = await api("/api/meta");
    $("#demo-banner").hidden = !state.meta.demo;
    document.body.classList.toggle("demo", state.meta.demo);
    $("#account-id").textContent = `Hesap · ${state.meta.account}`;
    $("#plan").textContent = state.meta.demo ? "Demo · sanal hesap" : "Sanal hesap";
  } catch (error) {
    toast(error.message, "error");
  }
  await Promise.all([loadPaper(), loadFx()]);
  render();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  // On a remotely served interface "Çıkış" forgets the token kept in this browser.
  if (remote) {
    document.addEventListener("click", (event) => {
      if (!event.target.closest('a[href="/logout"]')) return;
      event.preventDefault();
      logout();
    });
  }
}

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  window.deferredInstall = event;
});

start();
