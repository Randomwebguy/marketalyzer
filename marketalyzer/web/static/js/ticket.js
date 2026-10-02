// The order ticket, shared by the trade page and the "Emir ver" sheet.
import {
  $, api, busy, esc, fmtMoney, fmtNumber, loadPaper, loadQuotes, normalize, state, store, tickSize, toast,
} from "/static/js/core.js";

async function openAccount(container, onDone) {
  container.innerHTML = `<div class="empty"><b>Henüz sanal hesap yok</b>
    100.000 TL ile başlayan bir hesap açıp işlemleri gerçek para olmadan deneyin.
    <div><button class="btn primary" type="button">Sanal hesap aç</button></div></div>`;
  $("button", container).onclick = async (event) => {
    busy(event.currentTarget, true);
    try {
      await api("/api/paper/init", {});
      toast("Sanal hesap açıldı.");
      await loadPaper();
      onDone?.();
    } catch (error) {
      toast(error.message, "error");
      busy(event.currentTarget, false);
    }
  };
}

export function renderTicket(container, { onDone } = {}) {
  const p = state.paper;
  if (!p?.exists) {
    openAccount(container, () => renderTicket(container, { onDone }));
    return;
  }
  const t = state.ticket;
  const seg = (name, options) => options.map(([value, label]) =>
    `<button type="button" data-${name}="${value}" class="${name === "side" ? value : ""} ${t[name] === value ? "active" : ""}">${label}</button>`).join("");
  container.innerHTML = `
    <form class="form" novalidate>
      <div class="segmented block" role="group" aria-label="Yön">${seg("side", [["buy", "Al"], ["sell", "Sat"]])}</div>
      <label class="field">Sembol
        <input name="symbol" value="${esc(state.symbol)}" maxlength="12" autocomplete="off" spellcheck="false">
      </label>
      <div class="summary-rows">
        <div><span>Son fiyat</span><b data-last>—</b></div>
        <div><span>Elde</span><b data-held>—</b></div>
        <div><span>Alım gücü</span><b>${esc(fmtMoney(p.buying_power))}</b></div>
      </div>
      <label class="field">Adet (lot)
        <div class="stepper">
          <button type="button" data-step="-1" aria-label="Azalt">−</button>
          <input name="qty" type="number" min="1" step="1" inputmode="numeric" value="${esc(t.qty)}">
          <button type="button" data-step="1" aria-label="Artır">+</button>
        </div>
      </label>
      <div class="segmented block" role="group" aria-label="Emir tipi">${seg("type", [["market", "Piyasa"], ["limit", "Limit"], ["stop", "Stop"]])}</div>
      <label class="field" data-price-field ${t.type === "market" ? "hidden" : ""}>${t.type === "stop" ? "Stop fiyatı" : "Limit fiyatı"} (TL)
        <input name="price" type="number" step="0.01" min="0" inputmode="decimal" value="${esc(t.price)}">
        <span class="hint" data-tick></span>
      </label>
      <div class="segmented block" role="group" aria-label="Geçerlilik">${seg("tif", [["day", "Gün"], ["gtc", "İptale kadar"]])}</div>
      <div class="summary-rows">
        <div><span>Tahmini tutar</span><b data-value>—</b></div>
        <div><span>Komisyon + BSMV</span><b data-cost>—</b></div>
      </div>
      <button class="btn block ${t.side}" type="submit" data-submit></button>
      <p class="muted small" style="margin:0">Emir, verildikten sonraki ilk barda değerlendirilir. Fiyatlar yaklaşık 15 dk gecikmelidir.</p>
    </form>`;
  const form = $("form", container);
  const update = () => {
    const symbol = normalize(form.elements.symbol.value);
    const quote = state.quotes[symbol];
    const held = p.positions.find((x) => x.symbol === symbol)?.qty ?? 0;
    const qty = Math.max(0, Math.floor(Number(form.elements.qty.value) || 0));
    const last = quote && !quote.error ? quote.last : null;
    const price = t.type === "market" ? last : Number(form.elements.price.value) || null;
    $("[data-last]", form).textContent = last === null ? "—" : fmtMoney(last);
    $("[data-held]", form).textContent = `${fmtNumber(held, 0)} lot`;
    const value = price ? qty * price : null;
    const s = p.settings;
    const commission = value === null ? null
      : Math.max(value * s.commission_rate, qty >= 1 ? s.min_commission : 0) * (1 + s.bsmv_rate);
    $("[data-value]", form).textContent = value === null ? "—" : fmtMoney(value);
    $("[data-cost]", form).textContent = commission === null ? "—" : fmtMoney(commission);
    if (t.type !== "market" && price) {
      const tick = tickSize(price);
      const onGrid = Math.abs(Math.round(price / tick) * tick - price) < 1e-9;
      const hint = $("[data-tick]", form);
      hint.textContent = onGrid ? `Fiyat adımı ${fmtNumber(tick)}` : `Fiyat adımına uymuyor (adım ${fmtNumber(tick)})`;
      hint.className = `hint ${onGrid ? "" : "error"}`;
    }
    $("[data-submit]", form).textContent = `${symbol || "Sembol"} ${t.side === "buy" ? "al" : "sat"}`;
  };
  form.addEventListener("click", (event) => {
    const option = event.target.closest("[data-side],[data-type],[data-tif]");
    if (option) {
      for (const key of ["side", "type", "tif"]) if (option.dataset[key]) t[key] = option.dataset[key];
      t.qty = form.elements.qty.value;
      t.price = form.elements.price.value;
      state.symbol = normalize(form.elements.symbol.value) || state.symbol;
      renderTicket(container, { onDone });
      return;
    }
    const step = event.target.closest("[data-step]");
    if (step) {
      form.elements.qty.value = Math.max(1, (Number(form.elements.qty.value) || 0) + Number(step.dataset.step));
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
    const button = $("[data-submit]", form);
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
      toast(`Emir alındı (#${order.id}): ${order.symbol} ${order.qty} lot`);
      t.qty = body.qty;
      t.price = form.elements.price.value;
      await loadPaper();
      onDone?.(order);
    } catch (error) {
      toast(error.message, "error");
      busy(button, false);
    }
  });
  update();
  const symbol = normalize(state.symbol);
  if (!state.quotes[symbol]) loadQuotes([symbol]).then(update).catch(() => {});
}

export async function submitQuickOrder(body) {
  const order = await api("/api/paper/order", body);
  toast(`Emir alındı (#${order.id}): ${order.symbol} ${order.qty} lot`);
  await loadPaper();
  return order;
}
