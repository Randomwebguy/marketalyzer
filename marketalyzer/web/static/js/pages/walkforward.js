// Walk-forward: tune on a past window, paper trade the next, repeat.
import { $, api, busy, esc, state, store, toast } from "/static/js/core.js";
import { showWalkForward } from "/static/js/results.js";
import { common, strategyForm } from "/static/js/pages/backtest.js";

export function render(root) {
  const strategy = store.get("backtest.strategy", "sma_cross");
  const chosen = state.meta?.strategies?.[strategy] ? strategy : "sma_cross";
  root.innerHTML = `
    <div class="grid">
      <section class="card span-4" style="align-self:start">
        <div class="card-head"><h2>Walk-forward</h2></div>
        ${strategyForm("wf-form", {
          strategy: chosen,
          submit: "Testi başlat",
          extra: `
            <div class="row">
              <label class="field">Başlangıç<input name="start" type="date" value="2018-01-01" required></label>
              <label class="field">Bitiş<input name="end" type="date"></label>
            </div>
            <div class="row">
              <label class="field">Eğitim penceresi (bar)<input name="train" type="number" value="504" min="20"></label>
              <label class="field">Test penceresi (bar)<input name="test" type="number" value="126" min="1"></label>
            </div>`,
        })}
      </section>
      <div class="stack span-8" data-results>
        <section class="card"><div class="empty"><b>Geçmişten ileriye dönük test</b>
          Her pencerede parametreler önceki dönemde optimize edilir, sonraki dönem sanal hesapta işlenir.
          Yalnızca bu ileri dönemler sayılır. Birkaç dakika sürebilir.</div></section>
      </div>
    </div>`;
  const form = $("#wf-form", root);
  form.elements.strategy.addEventListener("change", () => {
    const name = form.elements.strategy.value;
    store.set("backtest.strategy", name);
    $("[data-doc]", form).textContent = state.meta?.strategies?.[name]?.doc ?? "";
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const results = $("[data-results]", root);
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
      results.innerHTML = `<section class="card"><div class="empty"><b>Test çalışmadı</b>${esc(error.message)}</div></section>`;
      toast(error.message, "error");
    } finally {
      busy(button, false);
      results.classList.remove("loading");
    }
  });
}
