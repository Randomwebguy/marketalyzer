// Screener: run one script over many symbols and compare their last bar.
import {
  $, api, busy, emit, esc, fmtNumber, icon, initials, normalize, pctText, store, symbolColor, toast, tone,
} from "/static/js/core.js";

const DEFAULT = ["THYAO", "GARAN", "AKBNK", "ASELS", "BIMAS", "EREGL", "KCHOL", "SISE", "TUPRS", "YKBNK", "FROTO", "PGSUS"];

export function render(root) {
  const local = { scripts: [], result: null, sort: "symbol" };
  root.innerHTML = `
    <div class="grid">
      <section class="card span-4" style="align-self:start">
        <div class="card-head"><h2>Tarama</h2></div>
        <form class="form" data-form>
          <label class="field">Script<select name="name"></select><span class="hint" data-doc></span></label>
          <label class="field">Semboller<textarea name="symbols" rows="4">${esc(store.get("screen.symbols", DEFAULT).join(", "))}</textarea>
            <span class="hint">En fazla 30 sembol; virgülle ayırın.</span></label>
          <label class="field">Periyot<select name="interval"><option value="1d">Günlük</option><option value="1W">Haftalık</option><option value="1h">Saatlik</option></select></label>
          <button class="btn primary block" type="submit">${icon("filter", "sm")} Taramayı çalıştır</button>
          <p class="muted small" style="margin:0">Strateji scriptlerinde son barda giriş/çıkış sinyali ve pozisyon durumu,
            göstergelerde çizimlerin son değerleri ve tetiklenen alarmlar listelenir.</p>
        </form>
      </section>
      <section class="card span-8" data-results><div class="empty"><b>Bir script seçin</b>Ör. RSI dönüşü stratejisini 12 hissede çalıştırıp
        bugün sinyal verenleri bulun.</div></section>
    </div>`;
  const form = $("[data-form]", root);
  const select = form.elements.name;

  api("/api/scripts").then((scripts) => {
    local.scripts = scripts.filter((s) => !s.error);
    const last = store.get("screen.script", "rsi_reversion");
    select.innerHTML = local.scripts.map((s) =>
      `<option value="${esc(s.name)}" ${s.name === last ? "selected" : ""}>${esc(s.title)} (${s.kind === "strategy" ? "strateji" : "gösterge"})</option>`).join("");
    showDoc();
  }).catch((error) => toast(error.message, "error"));

  function showDoc() {
    const script = local.scripts.find((s) => s.name === select.value);
    $("[data-doc]", form).textContent = script?.description ?? "";
  }
  select.addEventListener("change", showDoc);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const symbols = form.elements.symbols.value.split(/[\s,]+/).map(normalize).filter(Boolean);
    store.set("screen.symbols", symbols);
    store.set("screen.script", select.value);
    busy(button, true, "Taranıyor…");
    $("[data-results]", root).classList.add("loading");
    try {
      local.result = await api("/api/scripts/screen", { name: select.value, symbols, interval: form.elements.interval.value });
      draw();
    } catch (error) {
      toast(error.message, "error");
    } finally {
      busy(button, false);
      $("[data-results]", root).classList.remove("loading");
    }
  });

  function draw() {
    const r = local.result;
    const box = $("[data-results]", root);
    const rows = r.results.slice();
    const plotNames = [...new Set(rows.flatMap((row) => Object.keys(row.plots ?? {})))].slice(0, 4);
    const strategy = rows.some((row) => "entry_signal" in row);
    const score = (row) => (row.error ? -2 : row.entry_signal ? 3 : row.exit_signal ? 2 : row.alerts?.length ? 1 : 0);
    rows.sort((a, b) => score(b) - score(a) || a.symbol.localeCompare(b.symbol));
    const hits = rows.filter((row) => score(row) > 0).length;
    box.innerHTML = `
      <div class="card-head"><h2>${esc(r.script)}</h2><span class="sub">${rows.length} sembol · ${hits} sinyal</span>
        <span class="spacer"></span>
        <button class="btn small violet" type="button" data-ask>${icon("sparkle", "sm")} Yorumlat</button></div>
      <div class="table-wrap"><table><thead><tr><th>Sembol</th><th class="num">Kapanış</th><th class="num">Değişim</th>
        ${strategy ? "<th>Sinyal</th><th>Pozisyon</th>" : ""}
        ${plotNames.map((n) => `<th class="num">${esc(n)}</th>`).join("")}<th>Alarm</th><th></th></tr></thead><tbody>
        ${rows.map((row) => row.error
          ? `<tr><td><b>${esc(row.symbol)}</b></td><td colspan="${4 + plotNames.length + (strategy ? 2 : 0)}" class="error">${esc(row.error)}</td></tr>`
          : `<tr>
            <td><span class="row-flex" style="gap:8px"><span class="coin" style="--c:${symbolColor(row.symbol)}">${initials(row.symbol)}</span><b>${esc(row.symbol)}</b></span></td>
            <td class="num">${esc(fmtNumber(row.close))}</td>
            <td class="num ${tone(row.change_pct)}-text">${esc(pctText(row.change_pct))}</td>
            ${strategy ? `<td>${row.entry_signal ? '<span class="chip tag green">▲ Giriş</span>' : row.exit_signal ? '<span class="chip tag red">▼ Çıkış</span>' : '<span class="muted">—</span>'}</td>
              <td>${row.in_position ? "Pozisyonda" : '<span class="muted">Nakit</span>'}</td>` : ""}
            ${plotNames.map((n) => `<td class="num">${esc(row.plots?.[n] === null || row.plots?.[n] === undefined ? "—" : fmtNumber(row.plots[n]))}</td>`).join("")}
            <td>${esc([...(row.alerts ?? []), ...(row.shapes ?? [])].join(", ") || "—")}</td>
            <td><a class="icon-btn xs" href="#/piyasa/${esc(row.symbol)}" aria-label="${esc(row.symbol)} grafiği">${icon("chart")}</a></td>
          </tr>`).join("")}</tbody></table></div>`;
    $("[data-ask]", box).onclick = () => emit("ask",
      `"${r.script}" taramasının sonuçlarını yorumlar mısın? Sinyal veren hisseleri teknik açıdan karşılaştır: ${rows.filter((row) => score(row) > 0).map((row) => row.symbol).join(", ") || "sinyal yok"}.`);
  }
}
