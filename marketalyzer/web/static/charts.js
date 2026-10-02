// Hand-rolled SVG charts: line/area, candlestick and sparkline.
// Labels and values are set with textContent; nothing from data goes into HTML.

const NS = "http://www.w3.org/2000/svg";
const AXIS_BAND = 22;   // room under the plot for x labels
const Y_LABELS = 56;    // room right of the plot for y labels

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
const fullFmt = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric", month: "short", year: "numeric", timeZone: TZ,
});
const timeFmt = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const stampFmt = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: TZ,
});
const istanbulClock = (date) => timeFmt.format(date);
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
  if (min === max) { min -= Math.abs(min) * 0.01 || 1; max += Math.abs(max) * 0.01 || 1; }
  const rough = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const norm = rough / magnitude;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * magnitude;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toPrecision(12)));
  return { lo, hi, ticks };
}

function intraday(times) {
  return times.some((t) => t.length > 10);
}

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
  return t.length > 10 && istanbulClock(date) !== "00:00" ? stampFmt.format(date) : fullFmt.format(date);
}

function empty(container, message) {
  container.replaceChildren();
  div("chart-empty", container, message);
}

// Shared frame: hairline grid, y labels on the right, x labels below, and a
// crosshair that snaps to the nearest index and fills a tooltip.
function frame(container, { height, times, min, max, format, onHover }) {
  container.replaceChildren();
  container.classList.add("chart");
  const width = Math.max(container.clientWidth, 240);
  const plotW = width - Y_LABELS;
  const plotH = height - AXIS_BAND;
  const { lo, hi, ticks } = niceTicks(min, max);
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`, height, role: "img",
  }, container);
  const n = times.length;
  const x = (i) => (n === 1 ? plotW / 2 : (i / (n - 1)) * (plotW - 8) + 4);
  const y = (v) => plotH - ((v - lo) / (hi - lo)) * (plotH - 8) - 4;

  const grid = svg("g", {}, root);
  for (const tick of ticks) {
    const ty = y(tick);
    svg("line", { x1: 0, x2: plotW, y1: ty, y2: ty, stroke: token("--grid"), "stroke-width": 1 }, grid);
    const label = svg("text", { x: plotW + 8, y: ty + 4, class: "axis-label" }, grid);
    label.textContent = format(tick, true);
  }
  svg("line", {
    x1: 0, x2: plotW, y1: plotH, y2: plotH, stroke: token("--axis"), "stroke-width": 1,
  }, grid);
  const labels = Math.max(2, Math.min(5, Math.floor(plotW / 110)));
  const used = new Set();
  for (let k = 0; k < labels; k++) {
    const i = Math.round((k / (labels - 1)) * (n - 1));
    if (used.has(i)) continue;
    used.add(i);
    const anchor = k === 0 ? "start" : k === labels - 1 ? "end" : "middle";
    const label = svg("text", {
      x: x(i), y: height - 4, class: "axis-label", "text-anchor": anchor,
    }, grid);
    label.textContent = axisLabel(times[i], times);
  }

  const marks = svg("g", {}, root);
  const hover = svg("g", { visibility: "hidden" }, root);
  const cross = svg("line", {
    y1: 0, y2: plotH, stroke: token("--line-strong") || "rgba(255,255,255,.2)", "stroke-width": 1,
  }, hover);
  const tip = div("tip", container);
  tip.hidden = true;
  const hit = svg("rect", {
    x: 0, y: 0, width: plotW, height: plotH, fill: "transparent", tabindex: 0,
  }, root);

  function show(i) {
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    hover.setAttribute("visibility", "visible");
    tip.replaceChildren();
    div("when", tip, tipTime(times[i]));
    onHover(i, tip, hover, { x, y });
    tip.hidden = false;
    const left = x(i) + 14 + tip.offsetWidth > plotW ? x(i) - 14 - tip.offsetWidth : x(i) + 14;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = "8px";
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
    const current = Number(hit.dataset.index ?? n - 1);
    const next = event.key === "ArrowLeft" ? current - 1 : event.key === "ArrowRight" ? current + 1 : null;
    if (next === null) return;
    hit.dataset.index = Math.max(0, Math.min(n - 1, next));
    show(Number(hit.dataset.index));
  });
  return { root, marks, hover, x, y, plotW, plotH };
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
  if (name) line.appendChild(document.createTextNode(name));
}

/**
 * Line chart over aligned series: [{name, color, points: [{t, v}]}].
 * One series draws a soft area wash; two or more get a legend and end labels.
 */
export function lineChart(container, {
  series, height = 240, format = (v) => fmtNumber(v), area = true, markers = [],
  label = "Grafik", emptyText = "Grafik için yeterli veri yok.",
}) {
  const first = series[0]?.points ?? [];
  if (first.length < 2) {
    empty(container, emptyText);
    return;
  }
  const times = first.map((p) => p.t);
  const values = series.flatMap((s) => s.points.map((p) => p.v)).filter(Number.isFinite);
  const host = document.createElement("div");
  container.replaceChildren();
  if (series.length > 1) {
    const legend = div("legend", container);
    for (const s of series) {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.style.background = s.color;
      item.append(key, document.createTextNode(s.name));
      legend.appendChild(item);
    }
  }
  container.appendChild(host);
  const axisFormat = (v, axis) => (axis ? fmtCompact(v) : format(v));
  const f = frame(host, {
    height, times, min: Math.min(...values), max: Math.max(...values), format: axisFormat,
    onHover(i, tip, hover, { x, y }) {
      hover.querySelectorAll("circle").forEach((dot) => dot.remove());
      for (const s of series) {
        const v = s.points[i]?.v;
        if (!Number.isFinite(v)) continue;
        svg("circle", {
          cx: x(i), cy: y(v), r: 4, fill: s.color, stroke: token("--surface"), "stroke-width": 2,
        }, hover);
        tipLine(tip, series.length > 1 ? s.color : null, format(v), series.length > 1 ? s.name : "");
      }
    },
  });
  f.root.setAttribute("aria-label", label);

  for (const marker of markers) {
    const i = times.findIndex((t) => t >= marker);
    if (i <= 0) continue;
    svg("line", {
      x1: f.x(i), x2: f.x(i), y1: 0, y2: f.plotH, stroke: token("--axis"), "stroke-width": 1,
    }, f.marks);
  }
  const defs = svg("defs", {}, f.root);
  series.forEach((s, index) => {
    const pts = s.points.map((p, i) => [f.x(i), f.y(p.v)]).filter(([, py]) => Number.isFinite(py));
    const d = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
    if (area && series.length === 1) {
      const id = `wash-${Math.random().toString(36).slice(2)}`;
      const gradient = svg("linearGradient", { id, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      svg("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": 0.22 }, gradient);
      svg("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 }, gradient);
      svg("path", {
        d: `${d}L${pts.at(-1)[0]},${f.plotH}L${pts[0][0]},${f.plotH}Z`, fill: `url(#${id})`,
      }, f.marks);
    }
    svg("path", {
      d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round",
      "stroke-linecap": "round",
    }, f.marks);
    const [ex, ey] = pts.at(-1);
    svg("circle", {
      cx: ex, cy: ey, r: 4, fill: s.color, stroke: token("--surface"), "stroke-width": 2,
    }, f.marks);
    s.endY = ey;
    s.index = index;
  });
  // End labels only when they do not collide; the legend always names the series.
  if (series.length > 1 && series.length <= 4) {
    const ends = series.map((s) => s.endY).sort((a, b) => a - b);
    const clear = ends.every((v, i) => i === 0 || v - ends[i - 1] > 16);
    if (clear) {
      for (const s of series) {
        const text = svg("text", {
          x: f.plotW - 10, y: s.endY - 8, "text-anchor": "end", class: "end-label",
        }, f.marks);
        text.textContent = s.name;
      }
    }
  }
}

/** Candlestick chart of rows [{t, o, h, l, c}]. Up candles are hollow. */
export function candleChart(container, rows, { height = 280, label = "Fiyat grafiği" } = {}) {
  if (!rows.length) {
    empty(container, "Bu aralıkta fiyat verisi yok.");
    return;
  }
  const times = rows.map((r) => r.t);
  const up = token("--up");
  const down = token("--down");
  const surface = token("--surface");
  const f = frame(container, {
    height, times,
    min: Math.min(...rows.map((r) => r.l)),
    max: Math.max(...rows.map((r) => r.h)),
    format: (v, axis) => (axis ? fmtCompact(v) : fmtNumber(v)),
    onHover(i, tip) {
      const r = rows[i];
      const previous = i > 0 ? rows[i - 1].c : r.o;
      const change = (r.c / previous - 1) * 100;
      tipLine(tip, null, fmtNumber(r.c), " kapanış");
      const detail = div("", tip, `A ${fmtNumber(r.o)} · Y ${fmtNumber(r.h)} · D ${fmtNumber(r.l)}`);
      detail.style.marginTop = "2px";
      const delta = div(change >= 0 ? "up-text" : "down-text", tip,
        `${change >= 0 ? "▲" : "▼"} ${fmtPct(change)}`);
      delta.style.marginTop = "2px";
    },
  });
  f.root.setAttribute("aria-label", label);
  const slot = f.plotW / rows.length;
  const body = Math.max(1, Math.min(24, slot * 0.62));
  for (const [i, r] of rows.entries()) {
    const rising = r.c >= r.o;
    const color = rising ? up : down;
    const cx = f.x(i);
    svg("line", {
      x1: cx, x2: cx, y1: f.y(r.h), y2: f.y(r.l), stroke: color, "stroke-width": 1,
    }, f.marks);
    const top = f.y(Math.max(r.o, r.c));
    const bottom = f.y(Math.min(r.o, r.c));
    svg("rect", {
      x: cx - body / 2, y: top, width: body, height: Math.max(1, bottom - top),
      // Too narrow to read as hollow: fill it; the tooltip and table still
      // give the direction.
      rx: Math.min(2, body / 4), fill: rising && body > 3 ? surface : color, stroke: color,
      "stroke-width": rising && body > 3 ? 1.5 : 0,
    }, f.marks);
  }
}

/** Small trend line in the de-emphasis gray; the end dot shows direction. */
export function sparkline(values, { width = 120, height = 36, rising = true } = {}) {
  const root = svg("svg", {
    viewBox: `0 0 ${width} ${height}`, width, height, "aria-hidden": "true",
  });
  if (values.length < 2) return root;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const x = (i) => (i / (values.length - 1)) * (width - 8) + 2;
  const y = (v) => (max === min ? height / 2 : height - 4 - ((v - min) / (max - min)) * (height - 8));
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  svg("path", {
    d, fill: "none", stroke: token("--muted"), "stroke-width": 1.5, "stroke-linejoin": "round",
  }, root);
  svg("circle", {
    cx: x(values.length - 1), cy: y(values.at(-1)), r: 3.5,
    fill: rising ? token("--up") : token("--down"), stroke: token("--surface"), "stroke-width": 2,
  }, root);
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
