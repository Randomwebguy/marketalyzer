// Orders: open and past orders, fills and positions of the paper account.
import {
  $, api, busy, emit, esc, fmtMoney, fmtNumber, icon, initials, loadPaper, money, on, pill, signedMoney,
  slotColor, state, symbolColor, tipTime, toast, tone,
} from "/static/js/core.js";

const STATUS = { open: "Açık", filled: "Gerçekleşti", cancelled: "İptal", expired: "Süresi doldu", rejected: "Reddedildi" };
const TYPES = { market: "Piyasa", limit: "Limit", stop: "Stop" };
let tab = "open";

/** The order list with open/past tabs, used here and on the trade page. */
export function ordersList(box, { compact = false } = {}) {
  const p = state.paper;
  const orders = p?.exists ? p.orders.slice().reverse() : [];
  const open = orders.filter((o) => o.status === "open");
  const past = orders.filter((o) => o.status !== "open");
  const shown = tab === "open" ? open : past;
  box.innerHTML = `
    <div class="card-head"><h2>Emirler</h2><span class="spacer"></span>
      <div class="segmented" role="group" aria-label="Emir listesi">
        <button type="button" data-tab="open" class="${tab === "open" ? "active" : ""}">Açık (${open.length})</button>
        <button type="button" data-tab="past" class="${tab === "past" ? "active" : ""}">Geçmiş</button>
      </div></div>
    ${shown.length ? `<div class="list">${shown.slice(0, compact ? 8 : 100).map((o) => {
      const price = o.limit_price ?? o.stop_price;
      const detail = [TYPES[o.type], price ? fmtNumber(price) : null, o.tif === "gtc" ? "İptale kadar" : "Gün", o.tag]
        .filter(Boolean).join(" · ");
      const end = o.status === "open"
        ? `<button class="btn small" type="button" data-cancel="${o.id}">İptal</button>`
        : `<b>${esc(STATUS[o.status])}</b><span>${esc(o.fill_price ? `@ ${fmtNumber(o.fill_price)}` : o.reason || "")}</span>`;
      return `<div class="list-row">
        <span class="coin lg" style="--c:${symbolColor(o.symbol)}">${initials(o.symbol)}</span>
        <div class="grow"><b>${esc(o.symbol)} <span class="${o.side === "buy" ? "up" : "down"}-text">${o.side === "buy" ? "Al" : "Sat"}</span> ${esc(fmtNumber(o.qty, 0))} lot</b>
          <span>${esc(detail)} · #${o.id} · ${esc(tipTime(o.submitted_at))}</span></div>
        <div class="end">${end}</div>
      </div>`;
    }).join("")}</div>`
    : `<div class="empty">${tab === "open" ? "Açık emir yok." : "Henüz kapanmış emir yok."}</div>`}`;
  box.onclick = async (event) => {
    const switcher = event.target.closest("[data-tab]");
    if (switcher) {
      tab = switcher.dataset.tab;
      ordersList(box, { compact });
      return;
    }
    const cancel = event.target.closest("[data-cancel]");
    if (!cancel) return;
    busy(cancel, true, "…");
    try {
      await api(`/api/paper/cancel/${cancel.dataset.cancel}`, {});
      toast("Emir iptal edildi.");
      await loadPaper();
    } catch (error) {
      toast(error.message, "error");
      busy(cancel, false);
    }
  };
}

export function render(root) {
  root.innerHTML = `
    <div class="grid">
      <section class="card span-7" data-orders></section>
      <div class="stack span-5">
        <section class="card" data-positions></section>
        <section class="card" data-allocation></section>
      </div>
      <section class="card span-12" data-fills></section>
    </div>`;
  const draw = () => {
    const p = state.paper;
    if (!p?.exists) {
      root.innerHTML = `<div class="card"><div class="empty"><b>Henüz sanal hesap yok</b>
        <a class="btn primary" href="#/islem">Hesap aç</a></div></div>`;
      return;
    }
    ordersList($("[data-orders]", root));
    positions($("[data-positions]", root), p);
    allocation($("[data-allocation]", root), p);
    fills($("[data-fills]", root), p);
  };
  draw();
  loadPaper();
  return [on("paper", draw), on("display", draw)].reduce((a, b) => () => { a(); b(); });
}

function positions(box, p) {
  box.innerHTML = `
    <div class="card-head"><h2>Pozisyonlar</h2><span class="spacer"></span><span class="badge plain">${p.positions.length}</span></div>
    ${p.positions.length ? `<div class="list">${p.positions.map((x) => {
      const cost = x.qty * x.avg_cost;
      const pct = cost ? (x.unrealized_pnl / cost) * 100 : 0;
      return `<div class="list-row">
        <span class="coin lg" style="--c:${symbolColor(x.symbol)}">${initials(x.symbol)}</span>
        <div class="grow"><b>${esc(x.symbol)}</b><span>${esc(fmtNumber(x.qty, 0))} lot · ort. ${esc(fmtNumber(x.avg_cost))}</span></div>
        <div class="end"><b>${esc(x.market_value === null ? "—" : money(x.market_value))}</b>${pill(pct)}</div>
        <button class="icon-btn sm" type="button" data-sell="${esc(x.symbol)}" aria-label="${esc(x.symbol)} sat">${icon("arrowup")}</button>
      </div>`;
    }).join("")}</div>` : '<div class="empty">Açık pozisyon yok.</div>'}`;
  box.onclick = (event) => {
    const sell = event.target.closest("[data-sell]");
    if (!sell) return;
    state.ticket.side = "sell";
    state.ticket.qty = p.positions.find((x) => x.symbol === sell.dataset.sell)?.qty ?? 1;
    emit("order", sell.dataset.sell);
  };
}

function allocation(box, p) {
  // Fixed palette order (validated for adjacent contrast); positions arrive
  // sorted by symbol, so a holding keeps its color while the list is stable.
  const holdings = p.positions.slice(0, 7).map((x, i) => ({
    label: x.symbol, value: x.market_value ?? x.qty * x.avg_cost, color: slotColor(i),
  }));
  const rest = p.positions.slice(7);
  if (rest.length) {
    holdings.push({ label: `Diğer (${rest.length})`, value: rest.reduce((a, x) => a + (x.market_value ?? x.qty * x.avg_cost), 0), color: "#84837e" });
  }
  holdings.push({ label: "Nakit", value: p.cash, color: "#bdbcb6" });
  const total = holdings.reduce((a, b) => a + b.value, 0) || 1;
  box.innerHTML = `
    <div class="card-head"><h2>Dağılım</h2><span class="spacer"></span><span class="sub num">${esc(money(total, { digits: 0 }))}</span></div>
    <div class="alloc-bar" role="img" aria-label="Portföy dağılımı">${holdings.filter((x) => x.value > 0).map((x) =>
      `<span style="flex:${x.value / total};background:${x.color}" title="${esc(x.label)}"></span>`).join("")}</div>
    <div class="list">${holdings.map((x) => `<div class="list-row" style="padding:8px 0">
      <span class="swatch" style="background:${x.color}"></span><div class="grow"><b>${esc(x.label)}</b></div>
      <div class="end"><b>%${esc(fmtNumber((x.value / total) * 100, 1))}</b><span>${esc(money(x.value, { digits: 0 }))}</span></div>
    </div>`).join("")}</div>`;
}

function fills(box, p) {
  const rows = p.fills.slice().reverse();
  box.innerHTML = `
    <div class="card-head"><h2>Gerçekleşen işlemler</h2><span class="spacer"></span>
      <span class="sub">Gerçekleşen K/Z <b class="${tone(p.realized_pnl)}-text num">${esc(signedMoney(p.realized_pnl))}</b> ·
      Net temettü <b class="num">${esc(fmtMoney(p.dividends))}</b></span></div>
    ${rows.length ? `<div class="table-wrap"><table><thead><tr><th>Zaman</th><th>Sembol</th><th>Yön</th>
      <th class="num">Lot</th><th class="num">Fiyat</th><th class="num">Tutar</th><th class="num">Maliyet</th></tr></thead><tbody>
      ${rows.map((f) => `<tr><td>${esc(tipTime(f.time))}</td><td>${esc(f.symbol)}</td>
        <td class="${f.side === "buy" ? "up" : "down"}-text">${f.side === "buy" ? "Alış" : "Satış"}</td>
        <td class="num">${esc(fmtNumber(f.qty, 0))}</td><td class="num">${esc(fmtNumber(f.price))}</td>
        <td class="num">${esc(fmtMoney(f.qty * f.price))}</td><td class="num">${esc(fmtMoney(f.commission))}</td></tr>`).join("")}
      </tbody></table></div>` : '<div class="empty">Henüz gerçekleşen işlem yok.</div>'}`;
}
