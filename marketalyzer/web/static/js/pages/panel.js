// Panel: the account at a glance, laid out like a hardware-wallet dashboard.
import {
  $, ago, api, approx, arcGauge, ask, busy, dotNumber, emit, esc, fmtMoney, fmtNumber, go, icon, initials,
  lineChart, loadPaper, loadQuotes, money, mount, normalize, on, pctText, pill, responsive, session, state,
  store, symbolColor, token, toast, tone,
} from "/static/js/core.js";

const RANGES = { "1H": ["Hafta", 7], "1A": ["Ay", 31], "3A": ["3 ay", 92], all: ["Tümü", 99999] };

export function render(view) {
  const local = {
    range: store.get("panel.range", "3A"),
    measure: store.get("panel.measure", "equity"),
    index: null,
    convertMode: "amount",
    convertSymbol: state.symbol,
    amount: 25000,
  };
  view.innerHTML = `
    <div class="dash">
      <div class="dash-top">
        <div class="hero" id="hero"></div>
        <div class="notify">
          <div class="notify-card" id="notify"></div>
          <button class="icon-btn has-dot alert" type="button" id="bell" aria-label="Bildirimler">${icon("bell")}</button>
        </div>
      </div>
      <div class="quick">
        <button type="button" data-quick="buy"><span class="ring">${icon("arrowdown")}</span>Al</button>
        <button type="button" data-quick="sell"><span class="ring">${icon("arrowup")}</span>Sat</button>
        <button type="button" data-quick="lab"><span class="ring">${icon("code")}</span>Script</button>
        <button type="button" data-quick="ai"><span class="ring violet">${icon("sparkle")}</span>Asistan</button>
      </div>
      <div class="row-assets">
        <article class="card asset glow-warm" id="asset-0"></article>
        <article class="card asset glow-cool" id="asset-1"></article>
        <div class="rail">
          <div class="rail-group">
            <a class="icon-btn" href="#/lab" title="Script editörü" aria-label="Script editörü">${icon("code")}</a>
            <a class="icon-btn" href="#/backtest" title="Backtest" aria-label="Backtest">${icon("flask")}</a>
            <a class="icon-btn" href="#/tarama" title="Tarama" aria-label="Tarama">${icon("filter")}</a>
          </div>
          <button class="icon-btn round" type="button" id="rail-more" title="Komut paleti" aria-label="Komut paleti">${icon("plus")}</button>
        </div>
        <article class="card ai-card glow-violet" id="ai-card"></article>
      </div>
      <div class="asset-notice" id="asset-notice"></div>
      <div class="row-tools">
        <section class="card chart-card" id="chart-card"></section>
        <section class="card ticket-card" id="ticket-card"></section>
        <section class="card convert-card glow-sky" id="convert-card"></section>
      </div>
      <article class="card ai-card glow-violet ai-mobile" id="ai-card-mobile"></article>
      <div class="footer-strip">
        <button class="footer-chip" type="button" data-go="#/lab"><span class="ico">${icon("zap")}</span>
          <div><b>Lab'a geç · <em>Pine</em></b><span>Script editörü ve backtest</span></div></button>
        <button class="footer-chip" type="button" data-ask><span class="ico">${icon("chat")}</span>
          <div><span>Fikrini anlat</span><b>Asistana sor</b></div></button>
        <button class="search-pill" type="button" data-palette>${icon("search")}<span>Aramaya başla…</span><kbd>⌘K</kbd></button>
        <div class="install-chip"><div class="phone"><img src="/static/icons/icon-192.png" alt=""></div>
          <span><b>Mobil uygulama</b>Ana ekrana ekle</span>
          <button class="icon-btn sm" type="button" data-install aria-label="Kurulum">${icon("qr")}</button></div>
      </div>
    </div>`;

  // ---------------------------------------------------------- hero
  const renderHero = () => {
    const p = state.paper;
    const box = $("#hero");
    const equity = p?.exists ? p.equity : 0;
    const curve = p?.exists ? p.equity_curve : [];
    const today = curve.length > 1 ? (equity / curve.at(-2).v - 1) * 100 : null;
    box.innerHTML = `
      <div class="updated">Son güncelleme · <span data-ago>${esc(ago(state.loadedAt))}</span>
        <span title="Fiyatlar yaklaşık 15 dk gecikmelidir">${icon("clock", "sm")}</span></div>
      <h2>Tahmini bakiye <button class="icon-btn xs ghost" type="button" data-hide aria-label="${state.hidden ? "Bakiyeyi göster" : "Bakiyeyi gizle"}">${icon(state.hidden ? "eyeoff" : "eye")}</button></h2>
      <div class="hero-figure">
        <span class="dotnum" aria-label="Özsermaye ${esc(fmtMoney(equity))}">${dotNumber(equity)}</span>
        <button class="chip sky" type="button" data-ccy>${state.currency === "TRY" ? "₺ TRY" : "$ USD"} ${icon("down", "sm")}</button>
      </div>
      <div class="hero-sub">
        <span class="approx">${esc(approx(equity))}</span>
        ${p?.exists ? `<span>Bugünkü K/Z <span class="pnl ${tone(today ?? 0)}">${esc(today === null ? "—" : pctText(today))}</span></span>
          <span>Toplam getiri <span class="pnl ${tone(p.return_pct)}">${esc(pctText(p.return_pct))}</span></span>
          <span>Nakit <b class="num">${esc(money(p.cash, { digits: 0 }))}</b></span>`
          : '<button class="btn small primary" type="button" data-open-account>Sanal hesap aç</button><span>100.000 TL ile, gerçek para olmadan.</span>'}
      </div>`;
  };

  const renderNotify = () => {
    const fill = state.paper?.exists ? state.paper.fills?.at(-1) : null;
    const box = $("#notify");
    if (fill) {
      box.innerHTML = `<span class="ico">${icon(fill.side === "buy" ? "arrowdown" : "arrowup")}</span>
        <div class="grow"><b>${esc(fill.symbol)} ${fill.side === "buy" ? "alındı" : "satıldı"}</b>
        <span>${esc(fmtNumber(fill.qty, 0))} lot @ ${esc(fmtNumber(fill.price))}</span></div><time>son işlem</time>`;
    } else {
      box.innerHTML = `<span class="ico">${icon("alert")}</span>
        <div class="grow"><b>Veri akışı</b><span>${state.meta?.demo ? "Demo: sentetik fiyatlar" : "Yahoo · ~15 dk gecikmeli"}</span></div>
        <time>şimdi</time>`;
    }
  };

  // ---------------------------------------------------------- assets
  const assetSymbols = () => {
    const held = (state.paper?.positions ?? []).slice().sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0));
    const symbols = held.map((x) => x.symbol);
    for (const s of state.watchlist) if (!symbols.includes(s) && s !== "XU100") symbols.push(s);
    return symbols.slice(0, 2);
  };

  const renderAsset = (box, symbol) => {
    if (!symbol) {
      box.innerHTML = '<div class="empty">İzleme listesine sembol ekleyin.</div>';
      return;
    }
    const q = state.quotes[symbol];
    const position = state.paper?.positions?.find((x) => x.symbol === symbol);
    const value = position ? position.market_value ?? position.qty * position.avg_cost : q?.last;
    const valueText = position ? money(value) : Number.isFinite(value) ? fmtNumber(value) : "—";
    const [lead, rest] = valueText.match(/^([^1-9]*)(.*)$/)?.slice(1) ?? ["", valueText];
    box.innerHTML = `
      <header>
        <span class="coin" style="--c:${symbolColor(symbol)}">${initials(symbol)}</span>
        <b>${esc(symbol)}</b>${position ? '<span class="chip tag green">Pozisyon</span>' : ""}
        <div class="acts">
          <a class="icon-btn xs" href="#/piyasa/${esc(symbol)}" aria-label="${esc(symbol)} grafiği">${icon("chart")}</a>
          <a class="icon-btn xs" href="#/backtest/${esc(symbol)}" aria-label="${esc(symbol)} backtest">${icon("flask")}</a>
          <button class="icon-btn xs" type="button" data-analyze="${esc(symbol)}" aria-label="${esc(symbol)} analizi">${icon("more")}</button>
        </div>
      </header>
      <div class="value"><span class="lead">${esc(lead)}</span>${esc(rest)}
        <button class="icon-btn xs ghost" type="button" data-hide aria-label="Gizle/göster">${icon(state.hidden ? "eyeoff" : "eye")}</button></div>
      <div class="approx">${position ? esc(approx(value)) : `${q?.change_pct !== undefined ? pill(q.change_pct) : ""} <span class="muted">${esc(q?.name || "Borsa İstanbul")}</span>`}</div>
      <div class="meta">
        ${position ? `
          <div><label>Adet</label><b>${esc(fmtNumber(position.qty, 0))} <small>lot</small></b></div>
          <div><label>Ort. maliyet</label><b>${esc(fmtNumber(position.avg_cost))}</b></div>
          <div><label>K/Z</label><b class="${tone(position.unrealized_pnl)}-text">${esc(position.unrealized_pnl === null ? "—" : money(position.unrealized_pnl, { digits: 0 }))}</b></div>`
        : `
          <div><label>Borsa</label><b>BIST <small>TRY</small></b></div>
          <div><label>Hacim</label><b>${esc(q?.volume ? new Intl.NumberFormat("tr-TR", { notation: "compact" }).format(q.volume) : "—")}</b></div>
          <div><label>Tarih</label><b>${esc(q?.date ?? "—")}</b></div>`}
      </div>
      <footer>
        <button class="btn" type="button" data-trade="buy" data-symbol="${esc(symbol)}">${icon("arrowdown", "sm")} Al</button>
        <button class="btn" type="button" data-trade="sell" data-symbol="${esc(symbol)}">${icon("arrowup", "sm")} Sat</button>
        <button class="btn" type="button" data-analyze="${esc(symbol)}">${icon("sparkle", "sm")} Analiz</button>
      </footer>`;
  };

  const renderAssets = () => {
    const [a, b] = assetSymbols();
    renderAsset($("#asset-0"), a);
    renderAsset($("#asset-1"), b);
    $("#asset-notice").innerHTML = `
      <span class="notice">${icon("alert", "sm")} ${state.meta?.demo ? "Demo modu: fiyatlar sentetik" : "Fiyatlar yaklaşık 15 dk gecikmeli"}</span>
      <button class="chip tag" type="button" data-why>Neden görüyorum?</button>`;
  };

  // ---------------------------------------------------------- AI card
  const renderAi = () => {
    const ai = state.meta?.ai;
    const html = `
      <header>${icon("sparkle")}<b>Asistan</b>
        <span class="spacer"></span><span class="badge violet">AI</span></header>
      <div class="text"><h3>Yapay zeka asistanı</h3>
        <p>Strateji fikrini Pine scripte çevirir, backtest eder, sonuçları maliyetler ve aşırı uyum açısından yorumlar.</p></div>
      <div class="actions">
        <a class="btn violet" href="#/asistan">Asistanı aç ${icon("external", "sm")}</a>
        ${ai?.configured
          ? `<a class="btn outline" href="#/ayarlar">${esc(ai.model ? ai.model.split("/").at(-1) : "Model seç")}</a>`
          : `<a class="btn outline" href="#/ayarlar">${icon("key", "sm")} API anahtarı bağla</a>`}
      </div>`;
    $("#ai-card").innerHTML = html;
    $("#ai-card-mobile").innerHTML = html;
  };

  // ---------------------------------------------------------- income chart
  const renderChart = () => {
    const card = $("#chart-card");
    const p = state.paper;
    const curve = p?.exists ? p.equity_curve : [];
    const days = RANGES[local.range][1];
    const cutoff = Date.now() - days * 864e5;
    const own = curve.filter((point) => new Date(point.t).getTime() >= cutoff);
    const useEquity = own.length >= 2;
    const index = local.index?.rows ?? [];
    card.innerHTML = `
      <div class="card-head">
        <select class="chip" data-measure aria-label="Gösterilen değer">
          <option value="equity" ${local.measure === "equity" ? "selected" : ""}>Getiri raporu</option>
          <option value="pct" ${local.measure === "pct" ? "selected" : ""}>Getiri %</option>
        </select>
        <select class="chip" data-range aria-label="Dönem">
          ${Object.entries(RANGES).map(([key, [label]]) => `<option value="${key}" ${key === local.range ? "selected" : ""}>${label}</option>`).join("")}
        </select>
        <span class="spacer"></span>
        <span class="sub">${useEquity ? "Özsermaye ve BIST 100" : "BIST 100"}</span>
      </div>
      <div class="chart-host" data-chart></div>
      <div class="compare" data-compare></div>`;
    const host = $("[data-chart]", card);
    let series;
    let first;
    let last;
    if (useEquity) {
      const start = own[0].v;
      const idx = index.filter((r) => new Date(r.t).getTime() >= cutoff);
      const base = idx[0]?.c;
      const scaleIndex = (r) => (local.measure === "pct" ? (r.c / base - 1) * 100 : (start * r.c) / base);
      const ownPoints = own.map((pt) => ({ t: pt.t, v: local.measure === "pct" ? (pt.v / start - 1) * 100 : pt.v }));
      // Align the index to the equity dates so both lines share the x axis.
      const byDate = new Map(idx.map((r) => [r.t.slice(0, 10), r]));
      let previous = null;
      const indexPoints = own.map((pt) => {
        const row = byDate.get(pt.t.slice(0, 10)) ?? previous;
        previous = row ?? previous;
        return { t: pt.t, v: row && base ? scaleIndex(row) : NaN };
      });
      series = [{ name: "Özsermaye", color: token("--accent"), points: ownPoints }];
      if (indexPoints.some((pt) => Number.isFinite(pt.v))) {
        series.push({ name: "BIST 100", color: "#9a9993", points: indexPoints, dash: true });
      }
      first = own[0].v;
      last = own.at(-1).v;
    } else {
      const idx = index.filter((r) => new Date(r.t).getTime() >= cutoff);
      const base = idx[0]?.c;
      series = [{
        name: "BIST 100", color: token("--accent"),
        points: idx.map((r) => ({ t: r.t, v: local.measure === "pct" ? (r.c / base - 1) * 100 : r.c })),
      }];
      first = idx[0]?.c;
      last = idx.at(-1)?.c;
    }
    const format = local.measure === "pct" ? (v) => pctText(v) : (v) => (useEquity ? money(v, { digits: 0 }) : fmtNumber(v, 0));
    const draw = () => lineChart(host, {
      series, height: Math.max(170, host.clientHeight || 190), format, label: "Getiri grafiği",
      emptyText: local.index ? "Bu dönem için veri yok." : "Yükleniyor…",
    });
    draw();
    mount(responsive(host, draw));
    const change = first ? (last / first - 1) * 100 : null;
    $("[data-compare]", card).innerHTML = `
      <div><label>Başlangıç</label><b>${icon("layers", "sm")} ${esc(useEquity ? money(first, { digits: 0 }) : fmtNumber(first ?? NaN, 0))}</b>
        <small>${esc(useEquity ? approx(first) : "puan")}</small></div>
      <div><label>Güncel</label><b>${icon("layers", "sm")} ${esc(useEquity ? money(last, { digits: 0 }) : fmtNumber(last ?? NaN, 0))}
        ${change === null ? "" : `<span class="pnl ${tone(change)}">${esc(pctText(change))}</span>`}</b>
        <small>${esc(useEquity ? approx(last) : "BIST 100")}</small></div>
      <span class="spacer"></span>
      <button class="icon-btn round sm" type="button" data-ask-chart aria-label="Asistana yorumlat">${icon("chat")}</button>`;
  };

  // ---------------------------------------------------------- ticket (P2P style)
  const t = state.ticket;
  const renderTicketCard = () => {
    const card = $("#ticket-card");
    const symbol = normalize(state.symbol);
    const q = state.quotes[symbol];
    const price = t.type === "market" ? q?.last : Number(t.price) || q?.last;
    const qty = Math.max(1, Math.floor(Number(t.qty) || 1));
    const s = session();
    card.innerHTML = `
      <div class="top">
        <button class="chip" type="button" data-pick-symbol><span class="coin" style="--c:${symbolColor(symbol)};width:18px;height:18px;font-size:8px">${initials(symbol)}</span>${esc(symbol)}</button>
        <button class="chip ${t.side === "buy" ? "green" : "red"}" type="button" data-flip>${t.side === "buy" ? "Al" : "Sat"}</button>
        <span class="spacer"></span>
        <button class="chip" type="button" data-cycle-type>${icon("wallet", "sm")} ${{ market: "Piyasa", limit: "Limit", stop: "Stop" }[t.type]}</button>
      </div>
      <div class="who">${icon("wallet", "sm")} Sanal hesap · ${esc(state.meta?.account ?? "web")}</div>
      <h3>Emri onayla</h3>
      <div class="clock">${esc(s.text)} <b data-clock>${esc(s.value)}</b></div>
      <div class="legs">
        <div><label for="ticket-qty">Adet</label><input id="ticket-qty" data-qty type="number" min="1" step="1" inputmode="numeric" value="${qty}" aria-label="Adet"></div>
        ${t.type === "market" ? "" : `<div><label for="ticket-price">Fiyat</label><input id="ticket-price" data-price type="number" step="0.01" min="0" inputmode="decimal" value="${esc(t.price || q?.last || "")}" aria-label="Fiyat"></div>`}
        <div class="end"><label>Tutar</label><b data-total>${esc(Number.isFinite(price) ? money(qty * price, { digits: 0, hideable: false }) : "—")}</b></div>
      </div>
      <div data-arc></div>
      <div class="bottom">
        <button class="btn white" type="button" data-send>${t.side === "buy" ? "Alış emrini gönder" : "Satış emrini gönder"}</button>
        <button class="icon-btn round" type="button" data-ask-ticket aria-label="Asistana sor">${icon("chat")}</button>
        <button class="icon-btn round" type="button" data-full-ticket aria-label="Ayrıntılı emir">${icon("flag")}</button>
      </div>`;
    $("[data-arc]", card).appendChild(arcGauge(s.progress, { width: 320, height: 74, active: s.open }));
  };

  // ---------------------------------------------------------- converter
  const renderConvert = () => {
    const card = $("#convert-card");
    const symbol = normalize(local.convertSymbol);
    const q = state.quotes[symbol];
    const power = state.paper?.exists ? state.paper.buying_power : null;
    const price = q?.last;
    const amountMode = local.convertMode === "amount";
    const lots = amountMode && price ? Math.floor(local.amount / price) : local.amount;
    const value = !amountMode && price ? local.amount * price : local.amount;
    const options = [...new Set([symbol, ...state.watchlist.filter((s) => s !== "XU100")])]
      .map((s) => `<option ${s === symbol ? "selected" : ""}>${esc(s)}</option>`).join("");
    card.innerHTML = `
      <div class="leg">
        <span class="chip">${amountMode ? "₺ TRY" : `<span class="coin" style="--c:${symbolColor(symbol)};width:18px;height:18px;font-size:8px">${initials(symbol)}</span> ${esc(symbol)}`}</span>
        <div class="bal">${amountMode ? "Alım gücü" : "Fiyat"}<b>${esc(amountMode ? (power === null ? "—" : money(power, { digits: 0 })) : (price ? fmtNumber(price) : "—"))}</b>${esc(amountMode && power !== null ? approx(power) : "")}</div>
        <label for="convert-input">${amountMode ? "Tutar (TL)" : "Adet (lot)"}</label>
        <input class="big" id="convert-input" data-amount type="number" inputmode="decimal" min="0" value="${esc(local.amount)}">
      </div>
      <div class="swap"><button class="icon-btn round" type="button" data-swap aria-label="Yönü değiştir">${icon("swap")}</button></div>
      <div class="leg">
        ${amountMode ? `<select class="chip" data-convert-symbol aria-label="Sembol">${options}</select>` : '<span class="chip">₺ TRY</span>'}
        <div class="bal">${amountMode ? "Fiyat" : "Alım gücü"}<b>${esc(amountMode ? (price ? fmtNumber(price) : "—") : (power === null ? "—" : money(power, { digits: 0 })))}</b></div>
        <label>${amountMode ? "Alınabilecek" : "Tutar"}</label>
        <div class="big" data-result>${esc(amountMode ? `${fmtNumber(lots || 0, 0)} lot` : fmtNumber(value || 0, 2))}</div>
      </div>
      <footer><button class="btn" type="button" data-to-ticket>${icon("swap", "sm")} Emir fişine aktar</button></footer>`;
  };

  const renderAll = () => {
    renderHero();
    renderNotify();
    renderAssets();
    renderAi();
    renderChart();
    renderTicketCard();
    renderConvert();
  };

  // ---------------------------------------------------------- events
  view.addEventListener("click", async (event) => {
    const target = event.target;
    if (target.closest("[data-hide]")) {
      state.hidden = !state.hidden;
      store.set("hidden", state.hidden);
      emit("display");
    } else if (target.closest("[data-ccy]")) {
      $("#currency").click();
    } else if (target.closest("[data-open-account]")) {
      const button = target.closest("button");
      busy(button, true);
      try {
        await api("/api/paper/init", {});
        toast("Sanal hesap açıldı.");
        await loadPaper();
      } catch (error) {
        toast(error.message, "error");
        busy(button, false);
      }
    } else if (target.closest("[data-trade]")) {
      const button = target.closest("[data-trade]");
      state.ticket.side = button.dataset.trade;
      emit("order", button.dataset.symbol);
    } else if (target.closest("[data-analyze]")) {
      const symbol = target.closest("[data-analyze]").dataset.analyze;
      emit("ask", `${symbol} için teknik analiz yap: trend, momentum, destek/direnç ve dikkat edilmesi gereken riskler.`);
    } else if (target.closest("[data-why]")) {
      toast(state.meta?.demo
        ? "Demo modunda internet olmadan denemek için sentetik fiyatlar kullanılır."
        : "Ücretsiz veri kaynağı (Yahoo) BIST fiyatlarını yaklaşık 15 dakika gecikmeli verir.");
    } else if (target.closest("#bell")) {
      go("#/emirler");
    } else if (target.closest("#rail-more") || target.closest("[data-palette]")) {
      emit("palette", "");
    } else if (target.closest("[data-go]")) {
      go(target.closest("[data-go]").dataset.go);
    } else if (target.closest("[data-ask]")) {
      go("#/asistan/yeni");
    } else if (target.closest("[data-install]")) {
      installHelp();
    } else if (target.closest("[data-quick]")) {
      const kind = target.closest("[data-quick]").dataset.quick;
      if (kind === "buy" || kind === "sell") {
        state.ticket.side = kind;
        emit("order");
      } else go(kind === "lab" ? "#/lab" : "#/asistan");
    } else if (target.closest("[data-ask-chart]")) {
      emit("ask", "Sanal hesabımın özsermaye gelişimini BIST 100 ile karşılaştırıp yorumlar mısın? Pozisyonlarımı ve risklerini de değerlendir.");
    } else if (target.closest("[data-flip]")) {
      t.side = t.side === "buy" ? "sell" : "buy";
      renderTicketCard();
    } else if (target.closest("[data-cycle-type]")) {
      t.type = { market: "limit", limit: "stop", stop: "market" }[t.type];
      renderTicketCard();
    } else if (target.closest("[data-pick-symbol]")) {
      const symbol = normalize(await ask("Sembol seç", "BIST kodu", state.symbol));
      if (symbol) {
        state.symbol = symbol;
        store.set("symbol", symbol);
        await loadQuotes([symbol]).catch((error) => toast(error.message, "error"));
        renderTicketCard();
      }
    } else if (target.closest("[data-full-ticket]")) {
      emit("order", state.symbol);
    } else if (target.closest("[data-ask-ticket]")) {
      emit("ask", `${state.symbol} için ${t.qty} lot ${t.side === "buy" ? "alış" : "satış"} emri vermeyi düşünüyorum. Hesap durumuma ve teknik görünüme göre riskleri değerlendirir misin?`);
    } else if (target.closest("[data-send]")) {
      if (!state.paper?.exists) {
        emit("order", state.symbol);
        return;
      }
      const button = target.closest("[data-send]");
      const body = { symbol: normalize(state.symbol), side: t.side, qty: Math.floor(Number(t.qty)), type: t.type, tif: t.tif };
      if (t.type !== "market") body.price = Number(t.price);
      busy(button, true, "Gönderiliyor…");
      try {
        const order = await api("/api/paper/order", body);
        toast(`Emir alındı (#${order.id}): ${order.symbol} ${order.qty} lot`);
        await loadPaper();
      } catch (error) {
        toast(error.message, "error");
        busy(button, false);
      }
    } else if (target.closest("[data-swap]")) {
      const symbol = normalize(local.convertSymbol);
      const price = state.quotes[symbol]?.last;
      if (price) {
        local.amount = local.convertMode === "amount" ? Math.floor(local.amount / price) : Math.round(local.amount * price);
      }
      local.convertMode = local.convertMode === "amount" ? "lots" : "amount";
      renderConvert();
    } else if (target.closest("[data-to-ticket]")) {
      const symbol = normalize(local.convertSymbol);
      const price = state.quotes[symbol]?.last;
      const lots = local.convertMode === "amount" ? Math.floor(local.amount / (price || Infinity)) : Math.floor(local.amount);
      if (!lots) {
        toast("Bu tutarla en az bir lot alınamıyor.", "error");
        return;
      }
      state.ticket.qty = lots;
      state.ticket.side = "buy";
      emit("order", symbol);
    }
  });
  view.addEventListener("change", async (event) => {
    if (event.target.matches("[data-range]")) {
      local.range = event.target.value;
      store.set("panel.range", local.range);
      await loadIndex();
    } else if (event.target.matches("[data-measure]")) {
      local.measure = event.target.value;
      store.set("panel.measure", local.measure);
      renderChart();
    } else if (event.target.matches("[data-convert-symbol]")) {
      local.convertSymbol = event.target.value;
      if (!state.quotes[local.convertSymbol]) await loadQuotes([local.convertSymbol]).catch(() => {});
      renderConvert();
    }
  });
  view.addEventListener("input", (event) => {
    if (event.target.matches("[data-qty]")) {
      t.qty = event.target.value;
      const q = state.quotes[normalize(state.symbol)];
      const price = t.type === "market" ? q?.last : Number(t.price) || q?.last;
      $("[data-total]", view).textContent = Number.isFinite(price) ? money(Math.max(0, Number(t.qty) || 0) * price, { digits: 0, hideable: false }) : "—";
    } else if (event.target.matches("[data-price]")) {
      t.price = event.target.value;
      const price = Number(t.price);
      $("[data-total]", view).textContent = price ? money((Number(t.qty) || 0) * price, { digits: 0, hideable: false }) : "—";
    } else if (event.target.matches("[data-amount]")) {
      local.amount = Number(event.target.value) || 0;
      const price = state.quotes[normalize(local.convertSymbol)]?.last;
      const result = $("[data-result]", view);
      if (local.convertMode === "amount") result.textContent = `${fmtNumber(price ? Math.floor(local.amount / price) : 0, 0)} lot`;
      else result.textContent = fmtNumber(price ? local.amount * price : 0, 2);
    }
  });

  async function loadIndex() {
    const span = local.range === "all" ? "5Y" : local.range === "1H" ? "1A" : local.range;
    try {
      local.index = await api(`/api/prices?symbol=XU100&span=${span}`);
    } catch {
      local.index = { rows: [] };
    }
    renderChart();
  }

  renderAll();
  const symbols = [...new Set([...state.watchlist, ...(state.paper?.positions ?? []).map((x) => x.symbol), normalize(state.symbol)])];
  loadQuotes(symbols).then(() => {
    renderAssets();
    renderTicketCard();
    renderConvert();
  }).catch((error) => toast(error.message, "error"));
  loadIndex();
  loadPaper();
  api("/api/meta").then((meta) => {
    state.meta = meta;
    renderAi();
  }).catch(() => {});

  const offs = [
    on("paper", () => {
      renderHero();
      renderNotify();
      renderAssets();
      renderChart();
      renderTicketCard();
      renderConvert();
    }),
    on("display", renderAll),
    on("fx", renderAll),
    on("tick", () => {
      const s = session();
      const clock = $("[data-clock]", view);
      if (clock) clock.textContent = s.value;
      const agoLabel = $("[data-ago]", view);
      if (agoLabel) agoLabel.textContent = ago(state.loadedAt);
    }),
  ];
  return () => offs.forEach((off) => off());
}

function installHelp() {
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const prompt = window.deferredInstall;
  if (prompt) {
    prompt.prompt();
    return;
  }
  toast(ios
    ? "Safari'de Paylaş > Ana Ekrana Ekle ile uygulamayı kurabilirsiniz."
    : "Tarayıcı menüsünden 'Uygulamayı yükle' ya da 'Ana ekrana ekle'yi seçin.");
}
