// Hand-rolled SVG charts: line/area, multi-pane price charts with script plots,
// sparklines and the session arc. Labels and values are set with textContent;
// nothing from data goes into HTML.

const NS = "http://www.w3.org/2000/svg";
const AXIS_BAND = 22;   // room under the plot for x labels
const Y_LABELS = 58;    // room right of the plot for y labels
const PANE_GAP = 10;

export const token = (name) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const number = (digits) =>
  new Intl.NumberFormat("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const fmtNumber = (value, digits = 2) => number(digits).format(value);
export const fmtCompact = (value) =>
  new Intl.NumberFormat("tr-TR", { notation: "compact", maximumFractionDigits: 1 }).format(value);
export const fmtMoney = (value, digits = 2) =>
  new Intl.NumberFormat("tr-TR", {
    style: "currency", currency: "TRY", minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(value);
export const fmtPct = (value) =>
  new Intl.NumberFormat("tr-TR", {
    style: "percent", minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero",
  }).format(value / 100);
const fmtSmart = (value) => {
  const size = Math.abs(value);
  return fmtNumber(value, size >= 1000 ? 0 : size >= 10 ? 2 : size >= 1 ? 3 : 4);
};

// Market times are Istanbul times. Timestamps without an offset are Istanbul
// wall-clock times (Istanbul has been UTC+3 all year since 2016), and every
// formatter shows Istanbul time whatever the device's time zone.
const TZ = "Europe/Istanbul";
export const parseTime = (t) => {
  if (t.length === 10) return new Date(`${t}T00:00:00+03:00`);
  return new Date(/([zZ]|[+-]\d\d:?\d\d)$/.test(t) ? t : `${t}+03:00`);
};
const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: TZ });
const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "short", year: "numeric", timeZone: TZ });
const fullFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", year: "numeric", timeZone: TZ });
const timeFmt = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const stampFmt = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ,
});
export const fmtDate = (t) => fullFmt.format(parseTime(t));

function svg(name, attrs = {}, parent = null) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function div(className, parent, text) {
  const node = document.createElement("div");
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(min, max, count = 4) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) { min = 0; max = 1; }
  if (min === max) { min -= Math.abs(min) * 0.01 || 1; max += Math.abs(max) * 0.01 || 1; }
  const rough = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / magnitude;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * magnitude;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
  return { lo, hi, ticks, step };
}

// Axis labels: full numbers with as many decimals as the tick step needs;
// compact (1,2 Mn) only for large values, where digits would not fit.
function axisFormat({ ticks, step }) {
  const largest = Math.max(...ticks.map(Math.abs));
  if (largest >= 1e5) return fmtCompact;
  const digits = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3;
  return (v) => fmtNumber(v, digits);
}

const intraday = (times) => times.some((t) => t.length > 10);

function axisLabel(t, times) {
  const date = parseTime(t);
  if (intraday(times)) {
    const sameDay = dayFmt.format(parseTime(times[0])) === dayFmt.format(parseTime(times.at(-1)));
    return sameDay ? timeFmt.format(date) : dayFmt.format(date);
  }
  const span = parseTime(times.at(-1)) - parseTime(times[0]);
  return span > 400 * 864e5 ? monthFmt.format(date) : dayFmt.format(date);
}

export function tipTime(t) {
  const date = parseTime(t);
  return t.length > 10 && timeFmt.format(date) !== "00:00" ? stampFmt.format(date) : fullFmt.format(date);
}

function empty(container, message) {
  container.replaceChildren();
  container.classList.add("chart");
  div("chart-empty", container, message);
}

function tipLine(tip, color, value, name) {
  const line = div("line", tip);
  if (color) {
    const key = div("key", line);
    key.style.background = color;
  }
  const strong = document.createElement("b");
  strong.textContent = value;
  line.appendChild(strong);
  if (name) line.appendChild(document.createTextNode(` ${name}`));
}

function legend(container, items) {
  const box = div("legend", container);
  for (const item of items) {
    const span = document.createElement("span");
    const key = document.createElement("i");
    key.style.background = item.color;
    key.style.color = item.color;
    if (item.dash) key.className = "dash";
    span.append(key, document.createTextNode(item.name));
    box.appendChild(span);
  }
  return box;
}

function linePath(points) {
  // Points with a missing y break the line, as Pine draws na.
  let d = "";
  let pen = false;
  for (const [px, py] of points) {
    if (!Number.isFinite(py)) {
      pen = false;
      continue;
    }
    d += `${pen ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
    pen = true;
  }
  return d;
}

function stepPath(points) {
  let d = "";
  let previous = null;
  for (const [px, py] of points) {
    if (!Number.isFinite(py)) {
      previous = null;
      continue;
    }
    d += previous === null ? `M${px.toFixed(1)},${py.toFixed(1)}` : `H${px.toFixed(1)}V${py.toFixed(1)}`;
    previous = py;
  }
  return d;
}

// Hover handling shared by the charts: a crosshair snapping to the nearest bar.
function crosshair(root, container, { width, n, x, plotW, top, bottom, onHover }) {
  const hover = svg("g", { visibility: "hidden" }, root);
  const line = svg("line", { y1: top, y2: bottom, stroke: "rgba(255,255,255,.22)", "stroke-width": 1, "stroke-dasharray": "3 3" }, hover);
  const tip = div("tip", container);
  tip.hidden = true;
  const hit = svg("rect", { x: 0, y: top, width: plotW, height: bottom - top, fill: "transparent", tabindex: 0 }, root);
  let current = n - 1;
  function show(i) {
    current = i;
    line.setAttribute("x1", x(i));
    line.setAttribute("x2", x(i));
    hover.setAttribute("visibility", "visible");
    hover.querySelectorAll(".dot").forEach((dot) => dot.remove());
    tip.replaceChildren();
    onHover(i, tip, hover);
    tip.hidden = false;
    const left = x(i) + 14 + tip.offsetWidth > plotW ? x(i) - 14 - tip.offsetWidth : x(i) + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${top + 6}px`;
  }
  function hide() {
    hover.setAttribute("visibility", "hidden");
    tip.hidden = true;
  }
  const nearest = (event) => {
    const box = root.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    return Math.max(0, Math.min(n - 1, Math.round(((px - 4) / (plotW - 8)) * (n - 1))));
  };
  hit.addEventListener("pointermove", (event) => show(nearest(event)));
  hit.addEventListener("pointerdown", (event) => show(nearest(event)));
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(n - 1));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (event) => {
    const step = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
    if (!step) return;
    event.preventDefault();
    show(Math.max(0, Math.min(n - 1, current + step)));
  });
  return hover;
}

function yAxis(group, { y, ticks, plotW, format, top, bottom }) {
  for (const tick of ticks) {
    const ty = y(tick);
    if (ty < top - 1 || ty > bottom + 1) continue;
    svg("line", { x1: 0, x2: plotW, y1: ty, y2: ty, stroke: token("--grid"), "stroke-width": 1 }, group);
    const label = svg("text", { x: plotW + 8, y: ty + 4, class: "axis-label" }, group);
    label.textContent = format(tick);
  }
}

function xAxis(group, { x, times, plotW, y }) {
  const n = times.length;
  const labels = Math.max(2, Math.min(6, Math.floor(plotW / 110)));
  const used = new Set();
  for (let k = 0; k < labels; k++) {
    const i = Math.round((k / (labels - 1)) * (n - 1));
    if (used.has(i)) continue;
    used.add(i);
    const anchor = k === 0 ? "start" : k === labels - 1 ? "end" : "middle";
    const label = svg("text", { x: x(i), y, class: "axis-label", "text-anchor": anchor }, group);
    label.textContent = axisLabel(times[i], times);
  }
}

/**
 * Line chart over aligned series: [{name, color, points: [{t, v}], dash}].
 * One series draws a soft area wash; two or more get a legend and end values.
 */
export function lineChart(container, {
  series, height = 240, format = (v) => fmtNumber(v), area = true, markers = [],
  label = "Grafik", emptyText = "Grafik için yeterli veri yok.", endValues = true,
}) {
  const first = series[0]?.points ?? [];
  if (first.length < 2) {
    empty(container, emptyText);
    return;
  }
  container.replaceChildren();
  container.classList.add("chart");
  if (series.length > 1) legend(container, series);
  const host = div("", container);
  host.style.position = "relative";
  const times = first.map((p) => p.t);
  const values = series.flatMap((s) => s.points.map((p) => p.v)).filter(Number.isFinite);
  const width = Math.max(host.clientWidth || container.clientWidth, 240);
  const plotW = width - Y_LABELS;
  const plotH = height - AXIS_BAND;
  const scale = niceTicks(Math.min(...values), Math.max(...values));
  const { lo, hi, ticks } = scale;
  const n = times.length;
  const x = (i) => (n === 1 ? plotW / 2 : (i / (n - 1)) * (plotW - 8) + 4);
  const y = (v) => plotH - ((v - lo) / (hi - lo)) * (plotH - 8) - 4;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": label }, host);
  const grid = svg("g", {}, root);
  yAxis(grid, { y, ticks, plotW, format: axisFormat(scale), top: 0, bottom: plotH });
  svg("line", { x1: 0, x2: plotW, y1: plotH, y2: plotH, stroke: token("--axis"), "stroke-width": 1 }, grid);
  xAxis(grid, { x, times, plotW, y: height - 4 });
  const marks = svg("g", {}, root);
  for (const marker of markers) {
    const i = times.findIndex((t) => t >= marker);
    if (i <= 0) continue;
    svg("line", { x1: x(i), x2: x(i), y1: 0, y2: plotH, stroke: token("--axis"), "stroke-width": 1 }, marks);
  }
  const defs = svg("defs", {}, root);
  const ends = [];
  for (const s of series) {
    const pts = s.points.map((p, i) => [x(i), Number.isFinite(p.v) ? y(p.v) : NaN]);
    const d = linePath(pts);
    const valid = pts.filter(([, py]) => Number.isFinite(py));
    if (!valid.length) continue;
    if (area && series.length === 1) {
      const id = `wash-${Math.random().toString(36).slice(2)}`;
      const gradient = svg("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      svg("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": 0.24 }, gradient);
      svg("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 }, gradient);
      svg("path", { d: `${d}L${valid.at(-1)[0]},${plotH}L${valid[0][0]},${plotH}Z`, fill: `url(#${id})` }, marks);
    }
    svg("path", {
      d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
      ...(s.dash ? { "stroke-dasharray": "2 5" } : {}),
    }, marks);
    const [ex, ey] = valid.at(-1);
    svg("circle", { cx: ex, cy: ey, r: 4, fill: s.color, stroke: token("--surface"), "stroke-width": 2 }, marks);
    ends.push({ y: ey, text: format(s.points.at(-1)?.v ?? NaN) });
  }
  // End values only when they do not collide; the legend names the series.
  if (endValues && ends.length && ends.length <= 4) {
    const sorted = ends.map((e) => e.y).sort((a, b) => a - b);
    if (sorted.every((v, i) => i === 0 || v - sorted[i - 1] > 16)) {
      for (const end of ends) {
        const text = svg("text", { x: plotW - 10, y: end.y - 9, "text-anchor": "end", class: "end-label" }, marks);
        text.textContent = end.text;
      }
    }
  }
  crosshair(root, host, {
    width, n, x, plotW, top: 0, bottom: plotH,
    onHover(i, tip, hover) {
      div("when", tip, tipTime(times[i]));
      for (const s of series) {
        const v = s.points[i]?.v;
        if (!Number.isFinite(v)) continue;
        svg("circle", { class: "dot", cx: x(i), cy: y(v), r: 4, fill: s.color, stroke: token("--surface"), "stroke-width": 2 }, hover);
        tipLine(tip, series.length > 1 ? s.color : null, format(v), series.length > 1 ? s.name : "");
      }
    },
  });
}

/** Candlestick chart of rows [{t, o, h, l, c}]. Up candles are hollow. */
export function candleChart(container, rows, { height = 280, label = "Fiyat grafiği" } = {}) {
  priceChart(container, { rows, height, label, volume: false });
}

const SHAPES = {
  triangleup: (cx, cy, r) => `M${cx},${cy - r}L${cx + r},${cy + r * 0.8}L${cx - r},${cy + r * 0.8}Z`,
  arrowup: (cx, cy, r) => `M${cx},${cy - r}L${cx + r},${cy + r * 0.8}L${cx - r},${cy + r * 0.8}Z`,
  labelup: (cx, cy, r) => `M${cx},${cy - r}L${cx + r},${cy + r * 0.8}L${cx - r},${cy + r * 0.8}Z`,
  triangledown: (cx, cy, r) => `M${cx},${cy + r}L${cx + r},${cy - r * 0.8}L${cx - r},${cy - r * 0.8}Z`,
  arrowdown: (cx, cy, r) => `M${cx},${cy + r}L${cx + r},${cy - r * 0.8}L${cx - r},${cy - r * 0.8}Z`,
  labeldown: (cx, cy, r) => `M${cx},${cy + r}L${cx + r},${cy - r * 0.8}L${cx - r},${cy - r * 0.8}Z`,
  diamond: (cx, cy, r) => `M${cx},${cy - r}L${cx + r},${cy}L${cx},${cy + r}L${cx - r},${cy}Z`,
  square: (cx, cy, r) => `M${cx - r},${cy - r}h${2 * r}v${2 * r}h${-2 * r}Z`,
  xcross: (cx, cy, r) => `M${cx - r},${cy - r}L${cx + r},${cy + r}M${cx + r},${cy - r}L${cx - r},${cy + r}`,
  cross: (cx, cy, r) => `M${cx - r},${cy}H${cx + r}M${cx},${cy - r}V${cy + r}`,
};

function drawPlot(group, plot, { x, y, base, slot }) {
  const values = plot.values;
  const color = plot.color || "#bdbcb6";
  const width = Math.max(1, Math.min(3, plot.linewidth || 1)) + 0.5;
  if (plot.style === "histogram" || plot.style === "columns") {
    const bar = Math.max(1, Math.min(14, slot * (plot.style === "columns" ? 0.7 : 0.35)));
    values.forEach((v, i) => {
      if (!Number.isFinite(v)) return;
      const top = Math.min(y(v), base);
      svg("rect", {
        x: x(i) - bar / 2, y: top, width: bar, height: Math.max(1, Math.abs(y(v) - base)),
        fill: plot.colors?.[i] || color, opacity: 0.85, rx: Math.min(2, bar / 3),
      }, group);
    });
    return;
  }
  if (plot.style === "circles") {
    values.forEach((v, i) => {
      if (Number.isFinite(v)) svg("circle", { cx: x(i), cy: y(v), r: 2, fill: plot.colors?.[i] || color }, group);
    });
    return;
  }
  const pts = values.map((v, i) => [x(i), Number.isFinite(v) ? y(v) : NaN]);
  if (plot.style === "area") {
    const valid = pts.filter(([, py]) => Number.isFinite(py));
    if (valid.length > 1) {
      svg("path", {
        d: `${linePath(pts)}L${valid.at(-1)[0]},${base}L${valid[0][0]},${base}Z`,
        fill: color, opacity: 0.12,
      }, group);
    }
  }
  svg("path", {
    d: plot.style === "step" ? stepPath(pts) : linePath(pts), fill: "none", stroke: color,
    "stroke-width": width, "stroke-linejoin": "round", "stroke-linecap": "round",
  }, group);
}

function finite(values) {
  return values.filter(Number.isFinite);
}

/**
 * A price chart with optional volume, script overlays, indicator panes and
 * markers. rows: [{t, o, h, l, c, v}]; overlays: [{title, values, color, style}];
 * panes: [{title, plots: [...], hlines: [{price, title, color}]}];
 * markers: [{t, kind: "entry" | "exit" | "shape", style, location, color, text, title}].
 */
export function priceChart(container, {
  rows, type = "candle", overlays = [], panes = [], markers = [], volume = true,
  height = 340, paneHeight = 120, label = "Fiyat grafiği", format = fmtSmart,
}) {
  if (!rows?.length) {
    empty(container, "Bu aralıkta fiyat verisi yok.");
    return;
  }
  container.replaceChildren();
  container.classList.add("chart");
  const legendItems = [
    ...overlays.map((p) => ({ name: p.title, color: p.color || "#bdbcb6" })),
    ...panes.flatMap((pane) => pane.plots.map((p) => ({ name: p.title, color: p.color || "#bdbcb6" }))),
  ];
  if (legendItems.length >= 1) legend(container, legendItems);
  const host = div("", container);
  host.style.position = "relative";
  const width = Math.max(host.clientWidth || container.clientWidth, 260);
  const plotW = width - Y_LABELS;
  const n = rows.length;
  const times = rows.map((r) => r.t);
  const x = (i) => (n === 1 ? plotW / 2 : (i / (n - 1)) * (plotW - 10) + 5);
  const slot = (plotW - 10) / Math.max(n, 1);
  const total = height + panes.length * (paneHeight + PANE_GAP) + AXIS_BAND;
  const root = svg("svg", { viewBox: `0 0 ${width} ${total}`, height: total, role: "img", "aria-label": label }, host);
  const grid = svg("g", {}, root);
  const marks = svg("g", {}, root);
  const up = token("--up");
  const down = token("--down");
  const surface = token("--surface");

  // Price pane.
  const priceValues = [
    ...rows.flatMap((r) => [r.h, r.l]),
    ...overlays.flatMap((p) => finite(p.values)),
  ];
  let lowest = Math.min(...finite(priceValues));
  let highest = Math.max(...finite(priceValues));
  const pad = (highest - lowest) * 0.04 || 1;
  lowest -= pad;
  highest += pad;
  // Volume adds nothing when it never changes (some indices and demo data).
  const vols = rows.map((r) => r.v ?? 0);
  const showVolume = volume && Math.max(...vols) > Math.min(...vols);
  const volumeBand = showVolume ? height * 0.18 : 0;
  const priceBottom = height - 4;
  const priceTop = 6;
  const scale = niceTicks(lowest, highest, 5);
  const y = (v) => priceBottom - volumeBand - ((v - lowest) / (highest - lowest)) * (priceBottom - volumeBand - priceTop);
  yAxis(grid, { y, ticks: scale.ticks, plotW, format: axisFormat(scale), top: priceTop, bottom: priceBottom - volumeBand });
  svg("line", { x1: 0, x2: plotW, y1: height, y2: height, stroke: token("--axis"), "stroke-width": 1 }, grid);

  if (showVolume) {
    const maxVol = Math.max(...vols, 1);
    const bar = Math.max(1, Math.min(12, slot * 0.6));
    rows.forEach((r, i) => {
      const h = (vols[i] / maxVol) * (volumeBand - 4);
      if (h <= 0) return;
      svg("rect", {
        x: x(i) - bar / 2, y: priceBottom - h, width: bar, height: h, rx: Math.min(1.5, bar / 3),
        fill: r.c >= r.o ? up : down, opacity: 0.22,
      }, marks);
    });
  }

  if (type === "line") {
    const pts = rows.map((r, i) => [x(i), y(r.c)]);
    const defs = svg("defs", {}, root);
    const id = `wash-${Math.random().toString(36).slice(2)}`;
    const gradient = svg("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
    const color = token("--accent");
    svg("stop", { offset: "0%", "stop-color": color, "stop-opacity": 0.22 }, gradient);
    svg("stop", { offset: "100%", "stop-color": color, "stop-opacity": 0 }, gradient);
    svg("path", { d: `${linePath(pts)}L${pts.at(-1)[0]},${priceBottom - volumeBand}L${pts[0][0]},${priceBottom - volumeBand}Z`, fill: `url(#${id})` }, marks);
    svg("path", { d: linePath(pts), fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }, marks);
  } else {
    const body = Math.max(1, Math.min(22, slot * 0.64));
    for (const [i, r] of rows.entries()) {
      const rising = r.c >= r.o;
      const color = rising ? up : down;
      const cx = x(i);
      svg("line", { x1: cx, x2: cx, y1: y(r.h), y2: y(r.l), stroke: color, "stroke-width": 1 }, marks);
      const top = y(Math.max(r.o, r.c));
      const bottom = y(Math.min(r.o, r.c));
      // Too narrow to read as hollow: fill it; the tooltip still gives the direction.
      svg("rect", {
        x: cx - body / 2, y: top, width: body, height: Math.max(1, bottom - top), rx: Math.min(2, body / 4),
        fill: rising && body > 3 ? surface : color, stroke: color, "stroke-width": rising && body > 3 ? 1.4 : 0,
      }, marks);
    }
  }
  for (const plot of overlays) drawPlot(marks, plot, { x, y, base: priceBottom - volumeBand, slot });

  // Markers: entries below the bar, exits above, script shapes where asked.
  const index = new Map(times.map((t, i) => [t, i]));
  for (const marker of markers) {
    const i = index.get(marker.t);
    if (i === undefined) continue;
    const r = rows[i];
    const cx = x(i);
    const below = marker.kind === "entry" || marker.location === "belowbar" || marker.location === "bottom";
    const cy = below ? y(r.l) + 10 : y(r.h) - 10;
    const style = marker.kind === "entry" ? "triangleup" : marker.kind === "exit" ? "triangledown" : marker.style;
    const color = marker.color || (marker.kind === "entry" ? up : marker.kind === "exit" ? down : "#bdbcb6");
    if (style === "char" || style === "circle" || !SHAPES[style]) {
      if (style === "char" && marker.text) {
        const text = svg("text", { x: cx, y: cy + 4, "text-anchor": "middle", fill: color, "font-size": 11 }, marks);
        text.textContent = marker.text.slice(0, 2);
      } else {
        svg("circle", { cx, cy, r: 3.5, fill: color, stroke: surface, "stroke-width": 1.5 }, marks);
      }
      continue;
    }
    const shape = svg("path", { d: SHAPES[style](cx, cy, 5), fill: color, stroke: color, "stroke-width": style.includes("cross") ? 1.6 : 0 }, marks);
    shape.setAttribute("stroke-linecap", "round");
  }

  // Indicator panes.
  const paneScales = [];
  panes.forEach((pane, k) => {
    const top = height + PANE_GAP + k * (paneHeight + PANE_GAP);
    const bottom = top + paneHeight;
    const values = [
      ...pane.plots.flatMap((p) => finite(p.values)),
      ...(pane.hlines ?? []).map((h) => h.price),
    ];
    const histogram = pane.plots.some((p) => p.style === "histogram" || p.style === "columns");
    let lo = values.length ? Math.min(...values) : 0;
    let hi = values.length ? Math.max(...values) : 1;
    if (histogram) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const ticks = niceTicks(lo, hi, 3);
    const yy = (v) => bottom - 4 - ((v - ticks.lo) / (ticks.hi - ticks.lo || 1)) * (paneHeight - 8);
    paneScales.push({ pane, yy });
    svg("line", { x1: 0, x2: plotW, y1: top - PANE_GAP / 2, y2: top - PANE_GAP / 2, stroke: token("--axis"), "stroke-width": 1 }, grid);
    yAxis(grid, { y: yy, ticks: ticks.ticks, plotW, format: axisFormat(ticks), top, bottom });
    const title = svg("text", { x: 4, y: top + 12, class: "pane-title" }, grid);
    title.textContent = pane.title;
    for (const line of pane.hlines ?? []) {
      svg("line", {
        x1: 0, x2: plotW, y1: yy(line.price), y2: yy(line.price), stroke: line.color || token("--muted"),
        "stroke-width": 1, "stroke-dasharray": "4 4", opacity: 0.7,
      }, marks);
    }
    const base = yy(Math.max(Math.min(0, ticks.hi), ticks.lo));
    for (const plot of pane.plots) drawPlot(marks, plot, { x, y: yy, base, slot });
  });
  xAxis(grid, { x, times, plotW, y: total - 5 });

  crosshair(root, host, {
    width, n, x, plotW, top: 0, bottom: total - AXIS_BAND,
    onHover(i, tip) {
      const r = rows[i];
      div("when", tip, tipTime(r.t));
      const previous = i > 0 ? rows[i - 1].c : r.o;
      const change = (r.c / previous - 1) * 100;
      tipLine(tip, null, format(r.c), "kapanış");
      div("", tip, `A ${format(r.o)} · Y ${format(r.h)} · D ${format(r.l)}`).style.marginTop = "2px";
      const delta = div(change >= 0 ? "up-text" : "down-text", tip, `${change >= 0 ? "▲" : "▼"} ${fmtPct(change)}`);
      delta.style.marginTop = "2px";
      for (const plot of overlays) {
        const v = plot.values[i];
        if (Number.isFinite(v)) tipLine(tip, plot.color || "#bdbcb6", format(v), plot.title);
      }
      for (const { pane } of paneScales) {
        for (const plot of pane.plots) {
          const v = plot.values[i];
          if (Number.isFinite(v)) tipLine(tip, plot.colors?.[i] || plot.color || "#bdbcb6", fmtSmart(v), plot.title);
        }
      }
    },
  });
}

/** Small trend line in the de-emphasis gray; the end dot shows direction. */
export function sparkline(values, { width = 120, height = 36, rising = true, color } = {}) {
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, "aria-hidden": "true" });
  if (values.length < 2) return root;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const x = (i) => (i / (values.length - 1)) * (width - 8) + 2;
  const y = (v) => (max === min ? height / 2 : height - 4 - ((v - min) / (max - min)) * (height - 8));
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  svg("path", { d, fill: "none", stroke: color || token("--muted"), "stroke-width": 1.5, "stroke-linejoin": "round" }, root);
  svg("circle", {
    cx: x(values.length - 1), cy: y(values.at(-1)), r: 3.2,
    fill: rising ? token("--up") : token("--down"), stroke: token("--surface"), "stroke-width": 2,
  }, root);
  return root;
}

/** The trading session as a wide arc with a glowing marker at the current time. */
export function arcGauge(progress, { width = 300, height = 78, active = true, compact = false } = {}) {
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "arc", "aria-hidden": "true", preserveAspectRatio: "none" });
  const p0 = [6, height - 6];
  const p2 = [width - 6, height - 6];
  const c = [width / 2, compact ? -height * 0.2 : -height * 0.55];
  const t = Math.max(0, Math.min(1, progress));
  const lerp = (a, b) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
  const p01 = lerp(p0, c);
  const p12 = lerp(c, p2);
  const point = lerp(p01, p12);
  const defs = svg("defs", {}, root);
  const id = `arc-${Math.random().toString(36).slice(2)}`;
  const gradient = svg("linearGradient", { id, x1: 0, x2: 1, y1: 0, y2: 0 }, defs);
  svg("stop", { offset: "0%", "stop-color": "#ffffff", "stop-opacity": 0.12 }, gradient);
  svg("stop", { offset: "100%", "stop-color": "#ffffff", "stop-opacity": 0.95 }, gradient);
  const blur = svg("filter", { id: `${id}-glow`, x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
  svg("feGaussianBlur", { stdDeviation: 3 }, blur);
  svg("path", { d: `M${p0}Q${c} ${p2}`, fill: "none", stroke: "#2a2a29", "stroke-width": compact ? 3 : 4, "stroke-linecap": "round" }, root);
  if (t > 0) {
    const d = `M${p0}Q${p01} ${point}`;
    svg("path", { d, fill: "none", stroke: `url(#${id})`, "stroke-width": compact ? 4 : 6, "stroke-linecap": "round", filter: `url(#${id}-glow)`, opacity: 0.6 }, root);
    svg("path", { d, fill: "none", stroke: `url(#${id})`, "stroke-width": compact ? 3 : 4, "stroke-linecap": "round" }, root);
  }
  svg("circle", { cx: p0[0], cy: p0[1], r: compact ? 2.5 : 3.5, fill: active ? token("--up") : "#5c5b57" }, root);
  svg("circle", { cx: p2[0], cy: p2[1], r: compact ? 2.5 : 3.5, fill: "#5c5b57" }, root);
  svg("circle", { cx: point[0], cy: point[1], r: compact ? 3.5 : 5, fill: "#fff", filter: `url(#${id}-glow)` }, root);
  svg("circle", { cx: point[0], cy: point[1], r: compact ? 2.5 : 3.5, fill: "#fff" }, root);
  return root;
}

/** Re-render a chart when its container is resized. */
export function responsive(container, draw) {
  let width = 0;
  const observer = new ResizeObserver(() => {
    if (Math.abs(container.clientWidth - width) < 8) return;
    width = container.clientWidth;
    draw();
  });
  observer.observe(container);
  return observer;
}
