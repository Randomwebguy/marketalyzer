// Live view of the leveraged long/short experiment (paper only), refreshed every 10 s.
import { api, esc, fmtNumber, icon, lineChart, pctText, pill, tipTime, token, tone } from "/static/js/core.js";
import { tableHtml, tile } from "/static/js/results.js";

const EVERY = 10_000;
const usd = (v, d = 2) => new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD", minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const coin = (s) => String(s || "").replace(/USDT$/, "");
const price = (v) => (v >= 100 ? fmtNumber(v, 2) : v >= 1 ? fmtNumber(v, 4) : fmtNumber(v, 6));
const sideName = (side) => (side === "long" ? "LONG" : "SHORT");
const sideTone = (side) => (side === "long" ? "up" : "down");

function ladder(a) {
  return a.config.ladder.map((x, k) => (k === a.step ? `[${fmtNumber(x, 0)}x]` : `${fmtNumber(x, 0)}x`)).join(" → ");
}

function positionHtml(a) {
  const p = a.position;
  if (!p) {
    const best = Object.entries(a.scores || {}).sort((x, y) => y[1][a.side] - x[1][a.side])[0];
    const why = a.stopped ? "Hesap tabana indi; yeni pozisyon açılmıyor."
      : best ? `Pozisyon yok. En iyi ${a.side} skoru ${esc(coin(best[0]))} ${fmtNumber(best[1][a.side], 1)} (açmak için ≥ ${a.config.enter}).`
        : "Pozisyon yok; ilk 15 dakikalık kapanış bekleniyor.";
    return `<p class="muted small" style="margin:8px 0">${why}</p>`;
  }
  return `<div class="summary-rows" style="margin-top:8px">
    <div><span>Pozisyon</span><b><span class="${sideTone(a.side)}-text">${sideName(a.side)}</span> ${esc(coin(p.symbol))} · ${fmtNumber(p.leverage, 0)}x · marjin ${usd(p.margin + p.fees)}</b></div>
    <div><span>Giriş → anlık</span><b>${price(p.entry)} → ${price(p.price)} (${pctText(p.move_pct)})</b></div>
    <div><span>Açık K/Z</span><b class="${tone(p.pnl)}-text">${usd(p.pnl)} · marjine göre ${pctText(p.roe_pct)}</b></div>
    <div><span>Zarar durdur / kâr al</span><b>${price(p.stop)} (%${fmtNumber(p.to_stop_pct, 2)} uzak) / ${price(p.take)} (%${fmtNumber(p.to_take_pct, 2)})</b></div>
    <div><span>Tasfiye fiyatı</span><b>${price(p.liquidation)} (%${fmtNumber(p.to_liquidation_pct, 1)} uzak)</b></div>
    <div><span>Güven · süre</span><b>${fmtNumber(p.score, 1)} · ${p.bars}/${a.config.max_bars} bar · ${tipTime(p.opened)}</b></div>
  </div>`;
}

function accountHtml(a, k) {
  const s = a.stats;
  const winRate = s.trades ? (s.wins / s.trades) * 100 : null;
  return `<div class="lev-account" data-lev="${esc(a.account)}">
    <h3 class="sub-title" style="margin-top:0"><span class="${sideTone(a.side)}-text">${sideName(a.side)}</span> hesabı</h3>
    <div class="tiles">
      ${tile("Değer", usd(a.value), { cls: `${tone(a.return_pct)}-text`, note: `${pctText(a.return_pct)} · başlangıç ${usd(a.initial, 0)}` })}
      ${tile("Kaldıraç merdiveni", `${fmtNumber(a.leverage, 0)}x`, { note: ladder(a) })}
      ${tile("Döngü K/Z", usd(a.cycle_pnl), { cls: `${tone(a.cycle_pnl)}-text`, note: `kazanılan ${s.cycles_won} · kaybedilen ${s.cycles_lost}` })}
      ${tile("İşlem", fmtNumber(s.trades, 0), { note: `isabet ${winRate == null ? "—" : `%${fmtNumber(winRate, 0)}`} · tasfiye ${s.liquidations}` })}
    </div>
    ${positionHtml(a)}
    <div data-lev-curve="${k}" style="margin-top:8px"></div>
    <p class="muted small" style="margin:6px 0">Komisyon ${usd(s.fees)} · fonlama ${usd(s.funding)} · en kötü döngü ${usd(s.worst_cycle)}</p>
    <details class="more"><summary>Son işlemler (${a.trades.length})</summary><div class="table-wrap">${tableHtml([
      { key: "closed", label: "Kapanış", format: tipTime },
      { key: "symbol", label: "Coin", format: coin },
      { key: "leverage", label: "Kaldıraç", num: true, format: (v) => `${fmtNumber(v, 0)}x` },
      { key: "pnl", label: "K/Z", num: true, html: (t) => `<span class="${tone(t.pnl)}-text">${usd(t.pnl)}</span>` },
      { key: "roe_pct", label: "Marjine göre", num: true, html: (t) => pill(t.roe_pct) },
      { key: "reason", label: "Neden" },
      { key: "cycle", label: "Döngü" },
    ], a.trades)}</div></details>
  </div>`;
}

function boardHtml(accounts) {
  const scores = accounts[0]?.scores || {};
  const rows = Object.entries(scores).map(([symbol, s]) => ({ symbol, ...s }))
    .sort((x, y) => Math.max(y.long, y.short) - Math.max(x.long, x.short));
  if (!rows.length) return "";
  const enter = accounts[0].config.enter;
  const bar = (v, side) => `<span style="display:inline-flex;align-items:center;gap:6px;justify-content:flex-end;width:100%">
    <span style="display:inline-block;height:6px;width:${Math.round(v * 0.6)}px;border-radius:3px;background:${side === "long" ? token("--up") : token("--down")};opacity:${v >= enter ? 1 : 0.4}"></span>
    <b style="min-width:34px">${fmtNumber(v, 1)}</b></span>`;
  const decided = accounts[0].decided;
  return `<h3 class="sub-title">Güven skorları · son 15 dk kapanışı ${decided ? tipTime(decided) : "—"} (açma eşiği ${enter})</h3>
    <div class="table-wrap">${tableHtml([
      { key: "symbol", label: "Coin", html: (r) => `<b>${esc(coin(r.symbol))}</b>` },
      { key: "long", label: "Long", num: true, html: (r) => bar(r.long, "long") },
      { key: "short", label: "Short", num: true, html: (r) => bar(r.short, "short") },
      { key: "close", label: "Fiyat", num: true, format: (v) => price(v) },
    ], rows)}</div>`;
}

function compactHtml(data) {
  return `<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">${data.accounts.map((a) => {
    const p = a.position;
    return `<div style="flex:1 1 220px"><b class="${sideTone(a.side)}-text">${sideName(a.side)}</b> · <b>${usd(a.value)}</b> ${pill(a.return_pct)}
      <div class="muted small">${p ? `${esc(coin(p.symbol))} ${fmtNumber(p.leverage, 0)}x · açık ${usd(p.pnl)} (${pctText(p.roe_pct)})` : `pozisyon yok · ${fmtNumber(a.leverage, 0)}x sırada`}</div></div>`;
  }).join("")}<a class="btn small" href="#/strateji">Ayrıntı</a></div>`;
}

// Mount the live view into ``el``; it refreshes itself until ``el`` leaves the page.
export function mountLeverage(el, { compact = false } = {}) {
  let seen = null;
  const draw = (data) => {
    const live = `<span class="muted small" title="Fiyatlar Binance vadeli; sayfa 10 sn'de bir yenilenir">${icon("clock", "sm")} canlı · ${seen ? new Date(seen).toLocaleTimeString("tr-TR") : "—"}</span>`;
    const head = `<div class="card-head"><h2>${compact ? "Kaldıraç deneyi (canlı)" : "7 · Kaldıraçlı long / short deneyi (canlı)"}</h2>
      <span class="sub">sanal · Binance vadeli fiyatları · martingale</span><span class="spacer"></span>${live}</div>`;
    if (!data) { el.innerHTML = `${head}<p class="muted small">Yükleniyor…</p>`; return; }
    if (!data.exists) { el.innerHTML = `${head}<p class="notice">${icon("alert", "sm")} Deney hesapları henüz açılmadı (sunucuda <code>marketalyzer-leverage init</code>).</p>`; return; }
    if (compact) { el.innerHTML = `${head}${compactHtml(data)}`; return; }
    const c = data.accounts[0].config;
    el.innerHTML = `${head}
      <p class="muted small" style="margin:0 0 10px">Biri yalnızca long, diğeri yalnızca short açan iki hesap. En işlem gören ${data.accounts[0].watch.length} vadeli kontrat her 15 dakikalık kapanışta 15 ve 30 dakikalık grafiklerden 0-100 güven skoruyla puanlanır
        (30 dk ve 15 dk trend, RSI, MACD, kırılım, hacim). Skor ${c.enter} ve üstündeyse en yüksek skorlu coinde pozisyon açılır: marjin, döngü başındaki cüzdanın %${Math.round(c.margin * 100)}'i.
        Zarar durdur ${c.stop_atr} ATR, kâr al ${c.take_atr} ATR (15 dk); skor ${c.leave}'in altına düşerse ya da ${c.max_bars} bar (${c.max_bars / 4} saat) dolarsa kapanır.
        Martingale: kayıptan sonra kaldıraç ${c.ladder.map((x) => `${x}x`).join(" → ")} çıkar; döngünün toplamı kâra geçince 2x'e döner, ${c.ladder.at(-1)}x'te kaybedilirse döngü zararla kapanır.
        Pozisyon her dakika 1 dakikalık mumlarla kontrol edilir (tasfiye, stop, hedef); komisyon %0,05, kayma %0,02, fonlama 8 saatte bir. Gerçek emir gönderilmez.
        <b>Uyarı:</b> aynı kurallar son 60 günde iki hesabı da büyük zarara uğrattı (README, "Kaldıraçlı deney").</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px">${data.accounts.map(accountHtml).join("")}</div>
      ${boardHtml(data.accounts)}`;
    data.accounts.forEach((a, k) => {
      if (a.equity.length > 1) {
        lineChart(el.querySelector(`[data-lev-curve="${k}"]`), {
          series: [{ name: `${sideName(a.side)} hesabı`, color: a.side === "long" ? token("--up") : token("--down"), points: a.equity }],
          height: 150, format: (v) => usd(v, 0), label: `${sideName(a.side)} hesabının değeri`,
        });
      }
    });
  };
  let timer = null;
  let mounted = false;
  const load = async () => {
    if (mounted && !el.isConnected) { clearInterval(timer); return; }
    mounted = mounted || el.isConnected;
    try {
      const data = await api("/api/leverage");
      seen = Date.now();
      const open = [...el.querySelectorAll("details")].map((d) => d.open);
      draw(data);
      el.querySelectorAll("details").forEach((d, k) => { if (open[k]) d.open = true; });
    } catch {
      /* keep the last view; the next tick retries */
    }
  };
  draw(null);
  load();
  timer = setInterval(load, EVERY);
}
