// Backtest and walk-forward result views, shared by the Lab and their pages.
import {
  $, esc, fmtDate, fmtMoney, fmtNumber, icon, lineChart, mount, pctText, pill, responsive, signedMoney,
  tipTime, token, tone,
} from "/static/js/core.js";

export function tile(label, value, { cls = "", note = "" } = {}) {
  return `<div class="stat"><div class="label">${esc(label)}</div>
    <div class="value ${cls}">${esc(value)}</div>${note ? `<div class="note">${esc(note)}</div>` : ""}</div>`;
}

export function tableHtml(columns, rows) {
  if (!rows.length) return '<div class="empty">Kayıt yok.</div>';
  const head = columns.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("");
  const body = rows.map((row) => `<tr>${columns.map((c) => {
    const value = c.html ? c.html(row) : esc(c.format ? c.format(row[c.key], row) : row[c.key]);
    return `<td class="${c.num ? "num" : ""}">${value}</td>`;
  }).join("")}</tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/** A chart with a table twin: the accessible view of every chart. */
export function chartWithTable(host, draw, table) {
  host.innerHTML = '<div class="chart-slot"></div><div class="table-wrap" hidden></div>';
  const slot = $(".chart-slot", host);
  const wrap = $(".table-wrap", host);
  draw(slot);
  mount(responsive(slot, () => draw(slot)));
  return () => {
    const showTable = wrap.hidden;
    if (showTable) wrap.innerHTML = table();
    wrap.hidden = !showTable;
    slot.hidden = showTable;
    return showTable;
  };
}

function summaryTiles(r) {
  const sharpe = r.sharpe === null ? "—" : fmtNumber(r.sharpe);
  return [
    tile("Getiri", pctText(r.return_pct), { cls: `${tone(r.return_pct)}-text`, note: `Son bakiye ${fmtMoney(r.equity_final, 0)}` }),
    tile("Al ve tut", pctText(r.buy_hold_return_pct)),
    r.benchmark_return_pct !== undefined && r.benchmark_return_pct !== null ? tile("XU100", pctText(r.benchmark_return_pct)) : "",
    r.return_usd_pct !== undefined && r.return_usd_pct !== null
      ? tile("USD bazında", pctText(r.return_usd_pct), { cls: `${tone(r.return_usd_pct)}-text`, note: `USD/TRY ${pctText(r.usdtry_change_pct)}` })
      : "",
    tile("Sharpe", sharpe),
    tile("En büyük düşüş", pctText(r.max_drawdown_pct)),
    tile("İşlem", String(r.trades ?? 0), { note: r.win_rate_pct === null ? "" : `Kazançlı %${fmtNumber(r.win_rate_pct, 0)}` }),
    tile("Komisyon", fmtMoney(r.commissions ?? 0, 0)),
  ].join("");
}

const paramChips = (params) =>
  Object.entries(params ?? {}).map(([k, v]) => `<span class="chip tag">${esc(k)} = ${esc(v)}</span>`).join("");

export function showBacktest(container, r, { onApply } = {}) {
  const main = r.out_of_sample || r.in_sample || r;
  const params = r.best_params || main.params;
  const optimized = Boolean(r.best_params);
  const few = (main.trades ?? 0) < 10;
  container.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>${esc(main.symbol || "")} · ${esc(main.strategy || r.strategy || "")}</h2>
        <span class="sub">${esc(fmtDate(main.start))} – ${esc(fmtDate(main.end))}</span><span class="spacer"></span>
        ${r.plot ? `<a class="btn small" href="${esc(r.plot)}" target="_blank" rel="noopener">${icon("external", "sm")} Ayrıntılı grafik</a>` : ""}</div>
      <div class="row-flex" style="margin-bottom:12px">${paramChips(params)}
        ${optimized ? `<span class="chip tag sky">${r.out_of_sample ? "Sonuçlar: test dönemi (görülmemiş veri)" : "Sonuçlar: eğitim dönemi"}</span>` : ""}
        ${optimized && onApply ? '<button class="btn small" type="button" data-apply>Parametreleri uygula</button>' : ""}
      </div>
      <div class="tiles">${summaryTiles(main)}</div>
      ${few ? `<p class="notice" style="margin:12px 0 0">${icon("alert", "sm")} İşlem sayısı az; sonuç istatistiksel olarak zayıf.</p>` : ""}
      ${(r.warnings ?? []).length ? `<div class="console" style="margin-top:12px">${r.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}</div>` : ""}
    </section>
    ${optimized && r.out_of_sample ? `
    <section class="card">
      <div class="card-head"><h2>Eğitim ve test karşılaştırması</h2><span class="sub">${esc(r.combinations ?? "")} kombinasyon</span></div>
      <div class="table-wrap">${tableHtml([
        { key: "name", label: "Dönem" },
        { key: "return_pct", label: "Getiri", num: true, format: pctText },
        { key: "buy_hold_return_pct", label: "Al ve tut", num: true, format: pctText },
        { key: "sharpe", label: "Sharpe", num: true, format: (v) => (v === null ? "—" : fmtNumber(v)) },
        { key: "max_drawdown_pct", label: "En büyük düşüş", num: true, format: pctText },
        { key: "trades", label: "İşlem", num: true },
      ], [{ name: "Eğitim", ...r.in_sample }, { name: "Test", ...r.out_of_sample }])}</div>
      <p class="muted small">Eğitimde iyi, testte zayıfsa parametreler geçmişe uydurulmuş olabilir.</p>
    </section>` : ""}
    <section class="card">
      <div class="card-head"><h2>Strateji ve al-tut</h2><span class="spacer"></span>
        <button class="icon-btn sm" type="button" data-toggle title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button></div>
      <div data-chart></div>
    </section>
    <section class="card">
      <div class="card-head"><h2>İşlemler</h2><span class="spacer"></span><span class="badge plain">${(r.trade_list ?? []).length}</span></div>
      <div class="table-wrap">${tableHtml([
        { key: "entry_time", label: "Giriş", format: tipTime },
        { key: "exit_time", label: "Çıkış", format: tipTime },
        { key: "size", label: "Lot", num: true, format: (v) => fmtNumber(v, 0) },
        { key: "entry_price", label: "Giriş fiyatı", num: true, format: (v) => fmtNumber(v) },
        { key: "exit_price", label: "Çıkış fiyatı", num: true, format: (v) => fmtNumber(v) },
        { key: "pnl", label: "K/Z", num: true, html: (t) => `<span class="${tone(t.pnl)}-text">${esc(signedMoney(t.pnl))}</span>` },
        { key: "return_pct", label: "Getiri", num: true, html: (t) => pill(t.return_pct) },
      ], (r.trade_list ?? []).slice().reverse())}</div>
    </section>`;
  const series = [
    { name: "Strateji", color: token("--accent"), points: r.equity ?? [] },
    { name: "Al ve tut", color: token("--series-2"), points: r.hold ?? [] },
  ].filter((s) => s.points.length);
  const toggle = chartWithTable($("[data-chart]", container),
    (slot) => lineChart(slot, { series, height: 260, format: (v) => fmtMoney(v, 0), label: "Strateji ve al-tut özsermayesi" }),
    () => tableHtml([
      { key: "t", label: "Tarih", format: tipTime },
      { key: "v", label: "Strateji", num: true, format: (v) => fmtMoney(v) },
      { key: "h", label: "Al ve tut", num: true, format: (v) => (v === undefined ? "—" : fmtMoney(v)) },
    ], (r.equity ?? []).map((p, i) => ({ ...p, h: r.hold?.[i]?.v })).slice(-120).reverse()));
  $("[data-toggle]", container).onclick = (event) => {
    event.currentTarget.innerHTML = icon(toggle() ? "chart" : "table");
  };
  const apply = $("[data-apply]", container);
  if (apply) apply.onclick = () => onApply(params);
}

export function showWalkForward(container, r) {
  container.innerHTML = `
    <section class="card">
      <div class="card-head"><h2>${esc(r.symbol)} · ${esc(r.strategy)}</h2>
        <span class="sub">${esc(fmtDate(r.start))} – ${esc(fmtDate(r.end))} · ${r.windows.length} pencere</span></div>
      <div class="tiles">
        ${tile("İleri dönem getirisi", pctText(r.return_pct), { cls: `${tone(r.return_pct)}-text`, note: `Son özsermaye ${fmtMoney(r.equity_final, 0)}` })}
        ${tile("Al ve tut", pctText(r.buy_hold_return_pct))}
        ${tile("En büyük düşüş", pctText(r.max_drawdown_pct))}
        ${tile("İşlem", String(r.trades))}
        ${tile("Komisyon", fmtMoney(r.commissions, 0))}
        ${tile("Net temettü", fmtMoney(r.dividends, 0))}
      </div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Sanal hesap özsermayesi</h2><span class="sub">Dikey çizgiler pencere başlangıçları</span>
        <span class="spacer"></span>
        <button class="icon-btn sm" type="button" data-toggle title="Tablo görünümü" aria-label="Tablo görünümü">${icon("table")}</button></div>
      <div data-chart></div>
    </section>
    <section class="card">
      <div class="card-head"><h2>Pencereler</h2></div>
      <div class="table-wrap">${tableHtml([
        { key: "test_start", label: "Test başı", format: tipTime },
        { key: "test_end", label: "Test sonu", format: tipTime },
        { key: "params", label: "Parametreler", format: (p) => Object.entries(p).map(([k, v]) => `${k}=${v}`).join(", ") },
        { key: "in_sample_sharpe", label: "Eğitim Sharpe", num: true, format: (v) => (v === null ? "—" : fmtNumber(v)) },
        { key: "forward_return_pct", label: "İleri", num: true, html: (w) => pill(w.forward_return_pct) },
        { key: "buy_hold_return_pct", label: "Al ve tut", num: true, format: pctText },
        { key: "trades", label: "İşlem", num: true },
      ], r.windows)}</div>
    </section>`;
  const toggle = chartWithTable($("[data-chart]", container),
    (slot) => lineChart(slot, {
      series: [{ name: "Özsermaye", color: token("--accent"), points: r.equity }],
      height: 240, format: (v) => fmtMoney(v, 0), markers: r.windows.map((w) => w.test_start),
      label: "Walk-forward özsermayesi",
    }),
    () => tableHtml([
      { key: "t", label: "Tarih", format: tipTime },
      { key: "v", label: "Özsermaye", num: true, format: (v) => fmtMoney(v) },
    ], r.equity.slice(-120).reverse()));
  $("[data-toggle]", container).onclick = (event) => {
    event.currentTarget.innerHTML = icon(toggle() ? "chart" : "table");
  };
}
