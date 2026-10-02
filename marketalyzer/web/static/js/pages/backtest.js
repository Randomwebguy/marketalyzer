// Backtest: run or optimize any strategy, built-in or script, on one symbol.
import {
  $, api, busy, dateOnly, esc, icon, normalize, paramFields, readParams, state, store, strategyOptions, toast,
} from "/static/js/core.js";
import { showBacktest } from "/static/js/results.js";

export function strategyForm(id, { extra = "", submit, strategy }) {
  const doc = state.meta?.strategies?.[strategy]?.doc ?? "";
  return `
    <form class="form" id="${id}">
      <label class="field">Sembol<input name="symbol" value="${esc(state.symbol)}" maxlength="12" autocomplete="off" required></label>
      <label class="field">Strateji<select name="strategy">${strategyOptions(strategy)}</select>
        <span class="hint" data-doc>${esc(doc)}</span></label>
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

export function common(form) {
  return {
    symbol: normalize(form.elements.symbol.value),
    strategy: form.elements.strategy.value,
    cash: Number(form.elements.cash.value),
    commission: Number(form.elements.commission.value),
    slippage: Number(form.elements.slippage.value),
  };
}

export function render(root, { params }) {
  if (params[0]) state.symbol = normalize(params[0]);
  const strategy = store.get("backtest.strategy", "sma_cross");
  const chosen = state.meta?.strategies?.[strategy] ? strategy : "sma_cross";
  root.innerHTML = `
    <div class="grid">
      <section class="card span-4" style="align-self:start">
        <div class="card-head"><h2>Backtest</h2><span class="spacer"></span><a class="chip tag" href="#/lab">${icon("code", "sm")} Script yaz</a></div>
        ${strategyForm("bt-form", {
          strategy: chosen,
          submit: "Backtest çalıştır",
          extra: `
            <div class="row">
              <label class="field">Başlangıç<input name="start" type="date" value="${dateOnly(-3 * 365)}"></label>
              <label class="field">Bitiş<input name="end" type="date"></label>
            </div>
            <div class="row" data-params>${paramFields(chosen)}</div>
            <label class="check"><input type="checkbox" name="optimize"> Parametreleri optimize et (son %30 teste ayrılır)</label>`,
        })}
      </section>
      <div class="stack span-8" data-results>
        <section class="card"><div class="empty"><b>Bir strateji deneyin</b>
          Sonuçlar komisyon, BSMV ve kayma dahil hesaplanır; XU100 ve USD bazında getiriyle karşılaştırılır.
          Script stratejileri de listede.</div></section>
      </div>
    </div>`;
  const form = $("#bt-form", root);
  form.elements.strategy.addEventListener("change", () => {
    const name = form.elements.strategy.value;
    store.set("backtest.strategy", name);
    $("[data-doc]", form).textContent = state.meta?.strategies?.[name]?.doc ?? "";
    $("[data-params]", form).innerHTML = paramFields(name);
  });
  form.elements.optimize.addEventListener("change", () => {
    $("[data-params]", form).hidden = form.elements.optimize.checked;
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const results = $("[data-results]", root);
    const body = { ...common(form), params: readParams(form), optimize: form.elements.optimize.checked };
    if (form.elements.start.value) body.start = form.elements.start.value;
    if (form.elements.end.value) body.end = form.elements.end.value;
    state.symbol = body.symbol;
    store.set("symbol", body.symbol);
    busy(button, true, body.optimize ? "Optimize ediliyor…" : "Çalışıyor…");
    results.classList.add("loading");
    try {
      showBacktest(results, await api("/api/backtest", body), {
        onApply: (best) => {
          form.elements.optimize.checked = false;
          $("[data-params]", form).hidden = false;
          $("[data-params]", form).innerHTML = paramFields(body.strategy, best);
          toast("En iyi parametreler forma yazıldı.");
        },
      });
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
      results.classList.remove("loading");
    }
  });
}
