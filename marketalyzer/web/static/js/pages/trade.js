// Trade: order ticket, strategy steps and account settings.
import {
  $, api, busy, esc, fmtNumber, icon, loadPaper, normalize, on, paramFields, readParams, state,
  strategyOptions, toast,
} from "/static/js/core.js";
import { renderTicket } from "/static/js/ticket.js";
import { ordersList } from "/static/js/pages/orders.js";

export function render(root, { params }) {
  root.innerHTML = `
    <div class="grid">
      <section class="card span-5"><div class="card-head"><h2>Emir fişi</h2><span class="spacer"></span>
        <span class="chip tag">${icon("wallet", "sm")} Sanal</span></div><div data-ticket></div></section>
      <div class="stack span-7">
        <section class="card" data-orders></section>
        <section class="card" id="otomatik" data-auto></section>
        <section class="card" id="hesap" data-settings></section>
      </div>
    </div>`;
  const draw = () => {
    renderTicket($("[data-ticket]", root), { onDone: draw });
    ordersList($("[data-orders]", root), { compact: true });
    renderAuto($("[data-auto]", root));
    renderSettings($("[data-settings]", root));
  };
  draw();
  loadPaper();
  if (params[0]) setTimeout(() => document.getElementById(params[0])?.scrollIntoView({ behavior: "smooth" }), 300);
  return on("paper", draw);
}

function renderAuto(box) {
  const p = state.paper;
  const symbols = new Set([state.symbol, ...(p?.positions ?? []).map((x) => x.symbol)]);
  box.innerHTML = `
    <div class="card-head"><h2>Strateji ile işle</h2><span class="sub">Yeni barları işler, emirleri eşleştirir</span></div>
    <form class="form">
      <label class="field">Semboller<input name="symbols" value="${esc([...symbols].join(", "))}" autocomplete="off"></label>
      <div class="row">
        <label class="field">Strateji<select name="strategy">${strategyOptions("", { none: true })}</select></label>
        <label class="field">Periyot<select name="interval">${["1d", "1h", "15m", "5m"].map((v) => `<option>${v}</option>`).join("")}</select></label>
      </div>
      <div class="row" data-params></div>
      <button class="btn primary" type="submit">${icon("play", "sm")} Bir adım çalıştır</button>
      <p class="muted small" style="margin:0">Strateji seçildiyse tamamlanmış günlük barlara göre hedef pozisyonu belirler ve emir verir.
        Script stratejileri de kullanılabilir.</p>
    </form>`;
  const form = $("form", box);
  form.elements.strategy.addEventListener("change", () => {
    $("[data-params]", box).innerHTML = form.elements.strategy.value ? paramFields(form.elements.strategy.value) : "";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!state.paper?.exists) {
      toast("Önce bir sanal hesap açın.", "error");
      return;
    }
    const button = $("button[type=submit]", form);
    const body = {
      symbols: form.elements.symbols.value.split(/[\s,]+/).map(normalize).filter(Boolean),
      interval: form.elements.interval.value,
      params: readParams(form),
    };
    if (form.elements.strategy.value) body.strategy = form.elements.strategy.value;
    busy(button, true, "İşleniyor…");
    try {
      const report = await api("/api/paper/step", body);
      toast([`${report.bars} yeni bar`, `${report.fills.length} işlem`, `${report.orders.length} yeni emir`].join(" · "));
      Object.entries(report.errors).forEach(([symbol, message]) => toast(`${symbol}: ${message}`, "error"));
      const signals = Object.entries(report.signals).map(([s, w]) => `${s}: ${w ? "AL" : "NAKİT"}`);
      if (signals.length) toast(`Sinyal · ${signals.join(" · ")}`);
      await loadPaper();
    } catch (error) {
      toast(error.message, "error");
      busy(button, false);
    }
  });
}

function renderSettings(box) {
  const s = state.paper?.exists ? state.paper.settings : null;
  box.innerHTML = `
    <div class="card-head"><h2>Hesap ayarları</h2></div>
    ${s ? `<div class="summary-rows" style="margin-bottom:14px">
      <div><span>Komisyon</span><b>%${esc(fmtNumber(s.commission_rate * 100, 3))} + BSMV %${esc(fmtNumber(s.bsmv_rate * 100, 0))}</b></div>
      <div><span>Kayma (gidiş-dönüş)</span><b>%${esc(fmtNumber(s.slippage * 100, 2))}</b></div>
      <div><span>Temettü stopajı</span><b>%${esc(fmtNumber(s.dividend_tax * 100, 0))}</b></div>
    </div>` : ""}
    <details class="more"><summary>${s ? "Hesabı sıfırla" : "Hesap aç"}</summary>
      <form class="form">
        <div class="row">
          <label class="field">Başlangıç (TL)<input name="cash" type="number" value="100000" min="1"></label>
          <label class="field">Komisyon oranı<input name="commission" type="number" step="0.0001" value="${s?.commission_rate ?? 0.002}"></label>
        </div>
        <div class="row">
          <label class="field">Kayma oranı<input name="slippage" type="number" step="0.0001" value="${s?.slippage ?? 0.001}"></label>
          <label class="field">Temettü stopajı<input name="dividend_tax" type="number" step="0.01" value="${s?.dividend_tax ?? 0.15}"></label>
        </div>
        <button class="btn" type="submit">${s ? "Hesabı sil ve yeniden aç" : "Hesap aç"}</button>
      </form>
    </details>`;
  $("form", box).addEventListener("submit", async (event) => {
    event.preventDefault();
    if (s && !confirm("Mevcut sanal hesap ve tüm işlemleri silinecek. Devam edilsin mi?")) return;
    const form = event.currentTarget;
    const body = { reset: Boolean(s) };
    for (const name of ["cash", "commission", "slippage", "dividend_tax"]) body[name] = Number(form.elements[name].value);
    try {
      await api("/api/paper/init", body);
      toast(s ? "Sanal hesap yeniden açıldı." : "Sanal hesap açıldı.");
      await loadPaper();
    } catch (error) {
      toast(error.message, "error");
    }
  });
}
