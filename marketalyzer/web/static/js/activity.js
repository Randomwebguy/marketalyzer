// "Canlı hesaplar": every running paper account at a glance, with its latest moves.
import { api, esc, fmtNumber, icon, pctText, tone } from "/static/js/core.js";
import { tableHtml } from "/static/js/results.js";

const EVERY = 20_000;
const usd = (v) => new Intl.NumberFormat("tr-TR", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(v);
const coin = (s) => String(s || "").replace(/(-USD|USDT)$/, "");
const clock = (iso) => (iso ? new Date(iso).toLocaleString("tr-TR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");

function since(iso) {
  if (!iso) return "—";
  const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
  if (minutes < 1) return "az önce";
  if (minutes < 60) return `${minutes} dk önce`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} sa önce`;
  return `${Math.round(minutes / 1440)} gün önce`;
}

// The next decision: crypto daily accounts after each UTC midnight, the trend experiment every 4 hours.
function nextDecision(hours) {
  const now = new Date();
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, hours === 24 ? 10 : 0, 5));
  while (next <= now) next.setUTCHours(next.getUTCHours() + hours);
  return next;
}

function rows(crypto, leverage) {
  const out = [];
  for (const a of crypto?.accounts || []) {
    out.push({
      key: `crypto:${a.account}`, name: a.short || a.label, kind: a.kind === "sized" ? "Kripto · Binance, günlük" : "Kripto · günlük kural",
      value: a.value, ret: a.return_pct, open: a.positions.length, last: a.fills[0]?.time,
      ran: a.equity.at(-1)?.t, next: nextDecision(24),
    });
  }
  for (const a of leverage?.accounts || []) {
    const lastMove = [a.trades[0]?.closed, ...a.positions.map((p) => p.opened)].filter(Boolean).sort().at(-1);
    out.push({
      key: `lev:${a.account}`, name: `${a.side === "long" ? "LONG" : "SHORT"} trend (kaldıraçlı)`, kind: "Vadeli · 4 saatlik",
      value: a.value, ret: a.return_pct, open: a.positions.length, last: lastMove,
      ran: a.equity.at(-1)?.t, next: nextDecision(4),
    });
  }
  return out;
}

function feed(crypto, leverage) {
  const events = [];
  for (const a of crypto?.accounts || []) {
    for (const f of a.fills) {
      events.push({ time: f.time, account: a.short || a.label, text: `${f.side === "buy" ? "AL" : "SAT"} ${coin(f.symbol)} · ${usd(f.units * f.price)}${f.pnl != null ? ` · K/Z ${usd(f.pnl)}` : ""}`, tone: f.side === "buy" ? "up" : "down" });
    }
  }
  for (const a of leverage?.accounts || []) {
    const side = a.side === "long" ? "LONG" : "SHORT";
    for (const p of a.positions) events.push({ time: p.opened, account: `${side} trend`, text: `${side} açıldı ${coin(p.symbol)} · ${usd(p.units * p.entry)}`, tone: "up" });
    for (const t of a.trades) events.push({ time: t.closed, account: `${side} trend`, text: `kapandı ${coin(t.symbol)} · ${usd(t.pnl)} (${fmtNumber(t.r, 2)}R) · ${t.reason}`, tone: tone(t.pnl) });
  }
  return events.sort((x, y) => (y.time > x.time ? 1 : -1)).slice(0, 8);
}

export function mountActivity(el, { onPick } = {}) {
  let timer = null;
  let mounted = false;
  const draw = (crypto, leverage) => {
    const list = rows(crypto, leverage);
    const ranAt = list.map((r) => r.ran).filter(Boolean).sort().at(-1);
    const head = `<div class="card-head"><h2>Canlı hesaplar</h2><span class="sub">sanal · VPS'te 7/24</span><span class="spacer"></span>
      <span class="muted small">${icon("clock", "sm")} sistem son çalışma: ${since(ranAt)}</span></div>`;
    if (!list.length) {
      el.innerHTML = `${head}<p class="muted small">Hesaplar yükleniyor ya da sunucuya ulaşılamıyor…</p>`;
      return;
    }
    const events = feed(crypto, leverage);
    el.innerHTML = `${head}
      <div class="table-wrap">${tableHtml([
        { key: "name", label: "Hesap", html: (r) => `<button type="button" data-pick="${esc(r.key)}" style="all:unset;cursor:pointer;text-decoration:underline dotted"><b>${esc(r.name)}</b></button><div class="muted small">${esc(r.kind)}</div>` },
        { key: "value", label: "Değer", num: true, html: (r) => `${usd(r.value)}<div class="small ${tone(r.ret)}-text">${pctText(r.ret)}</div>` },
        { key: "open", label: "Açık poz.", num: true, html: (r) => (r.open ? `<b>${r.open}</b>` : `<span class="muted">0</span>`) },
        { key: "last", label: "Son işlem", html: (r) => (r.last ? since(r.last) : `<span class="muted">henüz yok</span>`) },
        { key: "next", label: "Sıradaki karar", html: (r) => clock(r.next.toISOString()) },
      ], list)}</div>
      <h3 class="sub-title">Son hareketler</h3>
      ${events.length ? `<ul class="plain" style="margin:0;padding-left:0;list-style:none">${events.map((e) => `<li style="padding:4px 0;border-bottom:1px solid var(--line, rgba(127,127,127,.15))">
          <span class="muted small" style="display:inline-block;min-width:110px">${clock(e.time)}</span>
          <b>${esc(e.account)}</b> · <span class="${e.tone}-text">${esc(e.text)}</span></li>`).join("")}</ul>`
        : `<p class="muted small">Henüz işlem yok: kurallar sinyal bekliyor.</p>`}
      <p class="muted small" style="margin-top:8px">Kripto hesapları her gün 00:10 UTC'de, kaldıraçlı trend hesapları her 4 saatte bir karar verir; arada sistem fiyatları ve stopları kontrol eder. Ayrıntı için hesabın adına tıklayın.</p>`;
  };
  const load = async () => {
    if (mounted && !el.isConnected) { clearInterval(timer); return; }
    mounted = mounted || el.isConnected;
    const [crypto, leverage] = await Promise.all([
      api("/api/crypto").catch(() => null),
      api("/api/leverage").catch(() => null),
    ]);
    draw(crypto, leverage);
  };
  el.addEventListener("click", (event) => {
    const button = event.target.closest("[data-pick]");
    if (button) onPick?.(button.dataset.pick);
  });
  el.innerHTML = `<div class="card-head"><h2>Canlı hesaplar</h2></div><p class="muted small">Yükleniyor…</p>`;
  load();
  timer = setInterval(load, EVERY);
}
