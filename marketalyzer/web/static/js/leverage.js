// Live view of the leveraged long/short trend experiment (paper only), refreshed every 10 s.
import { api, esc, fmtNumber, icon, lineChart, pctText, pill, tipTime, token, tone } from "/static/js/core.js";
import { tableHtml, tile } from "/static/js/results.js";

const EVERY = 10_000;
const usd = (v, d = 2) => new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD", minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
const coin = (s) => String(s || "").replace(/USDT$/, "");
const price = (v) => (v >= 100 ? fmtNumber(v, 2) : v >= 1 ? fmtNumber(v, 4) : fmtNumber(v, 6));
const sideName = (side) => (side === "long" ? "LONG" : "SHORT");
const sideTone = (side) => (side === "long" ? "up" : "down");
const r = (v) => `${v > 0 ? "+" : ""}${fmtNumber(v, 2)}R`;

function positionsHtml(a) {
  if (!a.positions.length) {
    return `<p class="muted small" style="margin:8px 0">Açık pozisyon yok: evrende ${a.side === "long" ? "yukarı" : "aşağı"} kırılım ve günlük trend onayı bekleniyor.</p>`;
  }
  return `<div class="table-wrap">${tableHtml([
    { key: "symbol", label: "Coin", html: (p) => `<b>${esc(coin(p.symbol))}</b>` },
    { key: "notional", label: "Büyüklük", num: true, format: (v) => usd(v, 0) },
    { key: "entry", label: "Giriş → anlık", num: true, format: (v, p) => `${price(p.entry)} → ${price(p.price)}` },
    { key: "pnl", label: "Açık K/Z", num: true, html: (p) => `<span class="${tone(p.pnl)}-text">${usd(p.pnl)}</span>` },
    { key: "r_now", label: "R", num: true, html: (p) => `<span class="${tone(p.r_now)}-text">${r(p.r_now)}</span>` },
    { key: "stop", label: "Stop (uzaklık)", num: true, format: (v, p) => `${price(p.stop)} (%${fmtNumber(p.to_stop_pct, 1)})` },
    { key: "liquidation", label: "Tasfiye", num: true, format: (v, p) => `${price(p.liquidation)} (%${fmtNumber(p.to_liquidation_pct, 0)})` },
    { key: "opened", label: "Açılış", format: tipTime },
  ], a.positions)}</div>`;
}

function accountHtml(a, k) {
  const s = a.stats;
  const winRate = s.trades ? (s.wins / s.trades) * 100 : null;
  const avgR = s.trades ? s.r_total / s.trades : null;
  return `<div class="lev-account">
    <h3 class="sub-title" style="margin-top:0"><span class="${sideTone(a.side)}-text">${sideName(a.side)}</span> hesabı</h3>
    <div class="tiles">
      ${tile("Değer", usd(a.value), { cls: `${tone(a.return_pct)}-text`, note: `${pctText(a.return_pct)} · başlangıç ${usd(a.initial, 0)}` })}
      ${tile("Pozisyonlar", `${a.positions.length} / ${a.rules.max_positions}`, { note: `toplam büyüklük ${fmtNumber(a.exposure, 2)}x (en fazla ${a.rules.max_gross}x)` })}
      ${tile("İşlem", fmtNumber(s.trades, 0), { note: `isabet ${winRate == null ? "—" : `%${fmtNumber(winRate, 0)}`} · tasfiye ${s.liquidations}` })}
      ${tile("İşlem başı", avgR == null ? "—" : r(avgR), { cls: `${tone(avgR)}-text`, note: `toplam ${r(s.r_total)}` })}
    </div>
    <h3 class="sub-title">Açık pozisyonlar</h3>
    ${positionsHtml(a)}
    <div data-lev-curve="${k}" style="margin-top:8px"></div>
    <p class="muted small" style="margin:6px 0">Komisyon ${usd(s.fees)} · fonlama ${usd(s.funding)} (eksi: alınan)</p>
    <details class="more"><summary>Son işlemler (${a.trades.length})</summary><div class="table-wrap">${tableHtml([
      { key: "closed", label: "Kapanış", format: tipTime },
      { key: "symbol", label: "Coin", format: coin },
      { key: "pnl", label: "K/Z", num: true, html: (t) => `<span class="${tone(t.pnl)}-text">${usd(t.pnl)}</span>` },
      { key: "r", label: "R", num: true, html: (t) => `<span class="${tone(t.r)}-text">${r(t.r)}</span>` },
      { key: "bars", label: "Süre", num: true, format: (v) => `${fmtNumber(v / 6, 1)} gün` },
      { key: "reason", label: "Neden" },
    ], a.trades)}</div></details>
  </div>`;
}

function boardHtml(accounts) {
  const [longs, shorts] = [accounts.find((a) => a.side === "long"), accounts.find((a) => a.side === "short")];
  const symbols = [...new Set([...Object.keys(longs?.board || {}), ...Object.keys(shorts?.board || {})])];
  if (!symbols.length) return "";
  const rows = symbols.map((symbol) => ({ symbol, l: longs?.board?.[symbol], s: shorts?.board?.[symbol] }))
    .sort((x, y) => Math.min(x.l?.to_breakout_pct ?? 99, x.s?.to_breakout_pct ?? 99) - Math.min(y.l?.to_breakout_pct ?? 99, y.s?.to_breakout_pct ?? 99));
  const cell = (b, side) => {
    if (!b) return "—";
    const state = b.enter ? `<b class="${sideTone(side)}-text">KIRILIM</b>` : b.trend ? `%${fmtNumber(Math.max(b.to_breakout_pct, 0), 1)} kaldı` : `<span class="muted">trend yok</span>`;
    return state;
  };
  const decided = accounts[0].decided;
  return `<h3 class="sub-title">Evren · son 4 saatlik kapanış ${decided ? tipTime(decided) : "—"}</h3>
    <p class="muted small" style="margin:0 0 6px">Long için fiyat son 5 günün en yüksek kapanışını, short için en düşüğünü geçmeli; günlük trend (50 günlük ortalama) aynı yönde olmalı.</p>
    <div class="table-wrap">${tableHtml([
      { key: "symbol", label: "Coin", html: (x) => `<b>${esc(coin(x.symbol))}</b>` },
      { key: "l", label: "Long", html: (x) => cell(x.l, "long") },
      { key: "s", label: "Short", html: (x) => cell(x.s, "short") },
      { key: "atr", label: "4 sa oynaklık", num: true, format: (v, x) => `%${fmtNumber((x.l || x.s).atr_pct, 2)}` },
      { key: "close", label: "Fiyat", num: true, format: (v, x) => price((x.l || x.s).close) },
    ], rows)}</div>`;
}

function compactHtml(data) {
  return `<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">${data.accounts.map((a) => {
    const open = a.positions.reduce((n, p) => n + p.pnl, 0);
    return `<div style="flex:1 1 220px"><b class="${sideTone(a.side)}-text">${sideName(a.side)}</b> · <b>${usd(a.value)}</b> ${pill(a.return_pct)}
      <div class="muted small">${a.positions.length ? `${a.positions.map((p) => esc(coin(p.symbol))).join(", ")} · açık ${usd(open)}` : "pozisyon yok"}</div></div>`;
  }).join("")}<a class="btn small" href="#/strateji">Ayrıntı</a></div>`;
}

// Mount the live view into ``el``; it refreshes itself until ``el`` leaves the page.
export function mountLeverage(el, { compact = false } = {}) {
  let seen = null;
  const draw = (data) => {
    const live = `<span class="muted small" title="Fiyatlar Binance vadeli; sayfa 10 sn'de bir yenilenir">${icon("clock", "sm")} canlı · ${seen ? new Date(seen).toLocaleTimeString("tr-TR") : "—"}</span>`;
    const head = `<div class="card-head"><h2>${compact ? "Kaldıraç deneyi (canlı)" : "7 · Kaldıraçlı long / short trend deneyi (canlı)"}</h2>
      <span class="sub">sanal · Binance vadeli fiyatları · 4 saatlik</span><span class="spacer"></span>${live}</div>`;
    if (!data) { el.innerHTML = `${head}<p class="muted small">Yükleniyor…</p>`; return; }
    if (!data.exists) { el.innerHTML = `${head}<p class="notice">${icon("alert", "sm")} Deney hesapları henüz açılmadı (sunucuda <code>marketalyzer-leverage init</code>).</p>`; return; }
    if (compact) { el.innerHTML = `${head}${compactHtml(data)}`; return; }
    const x = data.accounts[0].rules;
    el.innerHTML = `${head}
      <p class="muted small" style="margin:0 0 10px">Biri yalnızca long, diğeri yalnızca short açan iki hesap; martingale yok. Evren her ay Binance'te hacme göre ilk 20 coinin vadeli kontratları.
        Her 4 saatlik kapanışta fiyat son ${x.entry_bars / 6} günün en yüksek (short: en düşük) kapanışını geçerse ve coinin günlük kapanışı ${x.trend_days} günlük ortalamasının üstündeyse (short: altında) pozisyon açılır.
        Stop ${x.stop_atr} ATR'den başlar ve son ${x.exit_bars / 6} günün en düşüğünü (short: en yükseğini) izleyerek yalnızca daralır. Her işlem stopta özsermayenin %${x.risk * 100}'ini riske eder;
        en fazla ${x.max_positions} pozisyon, coin başına en fazla ${x.max_position}x, toplam en fazla ${x.max_gross}x, izole marjin ${x.leverage}x. Stoplar her dakika 1 dakikalık mumlarla kontrol edilir;
        komisyon %0,05, kayma %0,02, fonlama gerçek oranla 8 saatte bir. Gerçek emir gönderilmez.
        <b>Geçmiş test</b> (2024 → 2026-10, o tarihteki evren): long işlem başı +0,32R, Sharpe 1,05; short neredeyse başa baş (+0,04R).</p>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px">${data.accounts.map(accountHtml).join("")}</div>
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
