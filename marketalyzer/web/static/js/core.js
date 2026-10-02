// Shared state and helpers for every page. Text from the server is escaped
// with esc() in templates or set with textContent; never inserted as HTML.
import { fmtMoney, fmtNumber, fmtPct, parseTime } from "/static/js/charts.js";

export * from "/static/js/charts.js";

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);

const ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="8" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="3" y="15" width="7" height="6" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/>',
  market: '<path d="M3 17l5-5 4 4 8-9"/><path d="M15 7h5v5"/>',
  orders: '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  trade: '<path d="M4 7h14l-3-3"/><path d="M20 17H6l3 3"/>',
  code: '<path d="M8 8l-4 4 4 4M16 8l4 4-4 4M13.5 5l-3 14"/>',
  flask: '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"/><path d="M7.5 14h9"/>',
  walk: '<path d="M3 17l6-6 4 4 8-8"/><path d="M3 21h18"/>',
  filter: '<path d="M4 5h16l-6 7v6l-4 2v-8z"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  bell: '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7c1.8 0 3.4-.5 4.8-1.2"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
  doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  more: '<circle cx="12" cy="5" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="12" cy="19" r="1.2"/>',
  send: '<path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1.1-4.8A8 8 0 1 1 21 12z"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  swap: '<path d="M7 4v14M7 18l-3-3M7 18l3-3M17 20V6M17 6l-3 3M17 6l3 3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  play: '<path d="M7 5l12 7-12 7z"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  external: '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 10v10"/>',
  phone: '<rect x="7" y="2" width="10" height="20" rx="2.5"/><path d="M11 18h2"/>',
  qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h3v-3"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  check: '<path d="M5 12l5 5L20 7"/>',
  alert: '<path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/>',
  wallet: '<rect x="3" y="6" width="18" height="14" rx="3"/><path d="M3 10h18M16 15h2"/>',
  save: '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2z"/><path d="M8 3v5h7V3M8 21v-7h8v7"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4h6v3"/>',
  book: '<path d="M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z"/><path d="M20 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7z"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  arrowup: '<path d="M7 17L17 7"/><path d="M8 7h9v9"/>',
  arrowdown: '<path d="M7 7l10 10"/><path d="M17 8v9H8"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  candle: '<path d="M7 3v4M7 17v4M17 5v3M17 16v4"/><rect x="4.5" y="7" width="5" height="10" rx="1"/><rect x="14.5" y="8" width="5" height="8" rx="1"/>',
  line: '<path d="M3 17l5-6 4 3 9-9"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M14 9l2 2"/>',
  logout: '<path d="M14 4h6v16h-6M10 12h10M7 9l-3 3 3 3"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
};
export const icon = (name, cls = "") =>
  `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] ?? ""}</svg>`;

export const store = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(`marketalyzer.${key}`);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(`marketalyzer.${key}`, JSON.stringify(value));
    } catch {
      // Storage can be unavailable (private mode); the app works without it.
    }
  },
};

export const state = {
  meta: null,
  paper: null,
  quotes: {},
  usdtry: null,
  loadedAt: null,
  observers: [],
  cleanup: [],
  watchlist: store.get("watchlist", ["XU100", "THYAO", "GARAN", "ASELS", "BIMAS", "KCHOL", "TUPRS"]),
  symbol: store.get("symbol", "THYAO"),
  span: store.get("span", "3A"),
  hidden: store.get("hidden", false),
  currency: store.get("currency", "TRY"),
  ticket: { side: "buy", type: "market", tif: "day", qty: 10, price: "" },
};

// ---------------------------------------------------------------- events

const listeners = {};
export function on(name, handler) {
  (listeners[name] ??= new Set()).add(handler);
  return () => listeners[name].delete(handler);
}
export function emit(name, data) {
  for (const handler of listeners[name] ?? []) handler(data);
}

// ---------------------------------------------------------------- server

function errorText(data, status) {
  const detail = data?.detail;
  if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.at(-1) ?? ""}: ${d.msg}`).join("\n");
  if (detail && typeof detail === "object") {
    return detail.line ? `Satır ${detail.line}: ${detail.message}` : detail.message;
  }
  return detail || `HTTP ${status}`;
}

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

// The interface can be served by another site (a fixed Netlify address) while the
// server runs elsewhere behind a tunnel whose address changes. That site's
// index.html carries <meta name="marketalyzer-backend">; /backend.json then names
// the server, and requests carry the access token kept in this browser.
const TOKEN_KEY = "marketalyzer.token";
const OFFLINE = "Sunucuya ulaşılamıyor. Bilgisayarınızda marketalyzer ve tünelin açık olduğundan emin olun.";

async function readBackend() {
  try {
    const response = await fetch("/backend.json", { cache: "no-store" });
    const config = response.ok ? await response.json() : null;
    return config?.api ? String(config.api).replace(/\/+$/, "") : null;
  } catch {
    return null;
  }
}

function takeUrlToken() {
  const url = new URL(location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Private mode: the token lives only in this page.
  }
  sessionToken = token;
  url.searchParams.delete("token");
  history.replaceState(null, "", url.pathname + url.search + url.hash);
}

let sessionToken = "";
export const remote = document.querySelector('meta[name="marketalyzer-backend"]')
  ? { api: null, ready: null }
  : null;
if (remote) {
  takeUrlToken();
  remote.ready = readBackend().then((api) => {
    remote.api = api;
    return api;
  });
}

function storedToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || sessionToken;
  } catch {
    return sessionToken;
  }
}

/** Ask for the access token on a remotely served interface, then reload. */
export function showLogin(message = "") {
  if (document.getElementById("remote-login")) return;
  const box = document.createElement("div");
  box.id = "remote-login";
  box.className = "remote-login";
  box.innerHTML = `<form class="card">
      <h2>marketalyzer</h2>
      <p class="muted small">Sunucunun yazdırdığı adresteki <code>?token=</code> değerini girin. Bu tarayıcı onu hatırlar.</p>
      ${message ? `<p class="error small">${esc(message)}</p>` : ""}
      <label class="field">Erişim anahtarı<input name="token" type="password" autocomplete="current-password" required></label>
      <button class="btn primary block" type="submit">Giriş</button>
    </form>`;
  box.querySelector("form").addEventListener("submit", (event) => {
    event.preventDefault();
    const token = event.target.elements.token.value.trim();
    try {
      localStorage.setItem(TOKEN_KEY, token);
    } catch {
      sessionToken = token;
    }
    location.reload();
  });
  document.body.appendChild(box);
  box.querySelector("input").focus();
}

/** Sign out: forget the token (remote) or drop the cookie (same server). */
export function logout() {
  if (!remote) {
    location.href = "/logout";
    return;
  }
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing stored.
  }
  sessionToken = "";
  showLogin();
}

/** A server address for links opened outside fetch, such as report pages. */
export function apiLink(path) {
  if (!remote?.api) return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${remote.api}${path}${separator}token=${encodeURIComponent(storedToken())}`;
}

async function send(path, options) {
  if (!remote) {
    try {
      return await fetch(path, { ...options, credentials: "same-origin" });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError(OFFLINE, 0);
    }
  }
  if (!storedToken()) {
    showLogin();
    throw new ApiError("Erişim anahtarı gerekli.", 401);
  }
  await remote.ready;
  const request = () => fetch(`${remote.api}${path}`, {
    ...options,
    credentials: "omit",
    headers: { ...(options.headers ?? {}), Authorization: `Bearer ${storedToken()}` },
  });
  try {
    if (!remote.api) throw new TypeError("no server");
    return await request();
  } catch (error) {
    if (error.name === "AbortError") throw error;
    // The tunnel may have restarted under a new address: read it again once.
    const api = await readBackend();
    if (api && api !== remote.api) {
      remote.api = api;
      try {
        return await request();
      } catch (retry) {
        if (retry.name === "AbortError") throw retry;
      }
    }
    throw new ApiError(OFFLINE, 0);
  }
}

function unauthorized() {
  if (remote) {
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      // Nothing stored.
    }
    sessionToken = "";
    showLogin("Anahtar geçersiz ya da değişmiş.");
  } else {
    location.href = "/";
  }
  return new ApiError("Oturum gerekli.", 401);
}

export async function api(path, body, method) {
  const options = { method: method || (body === undefined ? "GET" : "POST") };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await send(path, options);
  if (response.status === 401) throw unauthorized();
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(errorText(data, response.status), response.status, data.detail);
  return data;
}

/** POST and read a server-sent event stream, calling onEvent for each event. */
export async function stream(path, body, onEvent, signal) {
  const response = await send(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) throw unauthorized();
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(errorText(data, response.status), response.status, data.detail);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (data) onEvent(JSON.parse(data));
    }
  }
}

// ---------------------------------------------------------------- feedback

export function toast(message, kind = "info") {
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = message;
  $("#toast").appendChild(node);
  setTimeout(() => node.remove(), kind === "error" ? 6500 : 3200);
}

export function busy(button, on, label) {
  if (!button) return;
  if (on) {
    button.dataset.label = button.innerHTML;
    button.disabled = true;
    button.textContent = label || "Çalışıyor…";
  } else {
    button.disabled = false;
    if (button.dataset.label) button.innerHTML = button.dataset.label;
  }
}

// ---------------------------------------------------------------- formatting

export const tone = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
export const arrow = (v) => (v > 0 ? "▲ " : v < 0 ? "▼ " : "");
export const pill = (v) =>
  (Number.isFinite(v) ? `<span class="pill ${tone(v)}">${arrow(v)}${esc(fmtPct(v))}</span>` : "");
export const pctText = (v) => (Number.isFinite(v) ? `${arrow(v)}${fmtPct(v)}` : "—");
export const signedMoney = (v) =>
  new Intl.NumberFormat("tr-TR", {
    style: "currency", currency: "TRY", signDisplay: "exceptZero", maximumFractionDigits: 2,
  }).format(v);
export const initials = (symbol) => esc(String(symbol).replace(/[^A-Z0-9]/gi, "").slice(0, 2).toUpperCase());
// Common company names people type instead of the ticker.
const ALIASES = {
  THY: "THYAO", "TÜRKHAVAYOLLARI": "THYAO", BIM: "BIMAS", "BİM": "BIMAS", "BİMAŞ": "BIMAS", BIMAŞ: "BIMAS",
  ASELSAN: "ASELS", TUPRAS: "TUPRS", "TÜPRAŞ": "TUPRS", "TUPRAŞ": "TUPRS", "TÜPRAS": "TUPRS", ENKA: "ENKAI",
  GARANTI: "GARAN", "GARANTİ": "GARAN", AKBANK: "AKBNK", SISECAM: "SISE", "ŞİŞECAM": "SISE", EREGLI: "EREGL",
  "EREĞLİ": "EREGL", KOC: "KCHOL", "KOÇ": "KCHOL", YAPIKREDI: "YKBNK", "YAPIKREDİ": "YKBNK", FORD: "FROTO",
  TURKCELL: "TCELL", PEGASUS: "PGSUS", SABANCI: "SAHOL", "ŞİŞE": "SISE",
};
export const normalize = (text) => {
  const code = String(text || "").trim().toUpperCase().replace(/\.(IS|E)$/, "");
  return ALIASES[code.replace(/\s+/g, "")] ?? code;
};
const TICKS = [[2500, 2.5], [1000, 1], [500, 0.5], [250, 0.25], [100, 0.1], [50, 0.05], [20, 0.02], [0, 0.01]];
export const tickSize = (price) => TICKS.find(([floor]) => price >= floor)?.[1] ?? 0.01;
export const dateOnly = (offsetDays) => new Date(Date.now() + offsetDays * 864e5).toISOString().slice(0, 10);

// Each symbol keeps one of the validated categorical colors.
const SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"];
export function symbolColor(symbol) {
  let hash = 0;
  for (const c of String(symbol)) hash = (hash * 31 + c.charCodeAt(0)) >>> 0;
  return SLOTS[hash % SLOTS.length];
}
export const slotColor = (index) => SLOTS[index % SLOTS.length];

/** Money in the chosen display currency; hidden as dots when balances are hidden. */
export function money(value, { digits = 2, hideable = true, currency } = {}) {
  if (!Number.isFinite(value)) return "—";
  if (hideable && state.hidden) return "••••••";
  const code = currency || state.currency;
  if (code === "USD" && state.usdtry) {
    return new Intl.NumberFormat("tr-TR", {
      style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(value / state.usdtry);
  }
  return fmtMoney(value, digits);
}

/** The other currency as an approximate value ("≈ $ 1 234"). */
export function approx(value) {
  if (!Number.isFinite(value) || !state.usdtry) return "";
  if (state.hidden) return "≈ ••••";
  if (state.currency === "USD") return `≈ ${fmtMoney(value, 0)}`;
  return `≈ $ ${fmtNumber(value / state.usdtry, 0)}`;
}

/** A big number split into integer and fraction for the dot-matrix display. */
export function dotNumber(value, digits = 2) {
  if (state.hidden) return '<span class="masked">••••••</span>';
  const shown = state.currency === "USD" && state.usdtry ? value / state.usdtry : value;
  const text = new Intl.NumberFormat("tr-TR", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  }).format(shown).replace(/\./g, " ");
  const [whole, frac] = text.split(",");
  return `${esc(whole)}${frac ? `<span class="frac">,${esc(frac)}</span>` : ""}`;
}

export function ago(date) {
  if (!date) return "—";
  const seconds = Math.max(0, Math.round((Date.now() - date) / 1000));
  if (seconds < 60) return `${seconds} sn önce`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} dk önce`;
  return `${Math.round(seconds / 3600)} sa önce`;
}

// ---------------------------------------------------------------- session clock

const clockFmt = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Istanbul", hour: "2-digit", minute: "2-digit", second: "2-digit", weekday: "short", hour12: false,
});
const OPEN = 10 * 3600;
const CLOSE = 18 * 3600 + 10 * 60;
const pad = (n) => String(n).padStart(2, "0");
const duration = (seconds) => {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
};

/** Borsa Istanbul's continuous session (10:00-18:10, weekdays), in Istanbul time. */
export function session(now = new Date()) {
  const parts = Object.fromEntries(clockFmt.formatToParts(now).map((p) => [p.type, p.value]));
  const seconds = Number(parts.hour) * 3600 + Number(parts.minute) * 60 + Number(parts.second);
  const weekend = parts.weekday === "Sat" || parts.weekday === "Sun";
  const open = !weekend && seconds >= OPEN && seconds < CLOSE;
  const progress = open ? (seconds - OPEN) / (CLOSE - OPEN) : seconds >= CLOSE || weekend ? 1 : 0;
  let label;
  if (open) label = { text: "Seans kapanışına", value: duration(CLOSE - seconds) };
  else if (!weekend && seconds < OPEN) label = { text: "Seans açılışına", value: duration(OPEN - seconds) };
  else label = { text: "Seans kapalı", value: weekend ? "hafta sonu" : "yarın 10:00" };
  return { open, progress, ...label, clock: `${parts.hour}:${parts.minute}` };
}

// ---------------------------------------------------------------- data

export async function loadPaper() {
  try {
    state.paper = await api("/api/paper");
    state.loadedAt = Date.now();
  } catch (error) {
    toast(error.message, "error");
  }
  emit("paper", state.paper);
  return state.paper;
}

export async function loadQuotes(symbols) {
  const list = [...new Set(symbols.filter(Boolean))];
  if (!list.length) return [];
  const quotes = await api(`/api/watchlist?symbols=${encodeURIComponent(list.join(","))}`);
  for (const quote of quotes) state.quotes[quote.symbol] = quote;
  state.loadedAt = Date.now();
  emit("quotes", quotes);
  return quotes;
}

export async function loadFx() {
  try {
    const data = await api("/api/fx?pair=USDTRY");
    state.usdtry = data.last;
  } catch {
    state.usdtry = null;
  }
  emit("fx", state.usdtry);
}

export function strategyOptions(selected, { none = false } = {}) {
  const catalog = state.meta?.strategies ?? {};
  const builtin = Object.entries(catalog).filter(([, s]) => s.kind === "builtin");
  const scripts = Object.entries(catalog).filter(([, s]) => s.kind === "script");
  const option = ([name, s]) =>
    `<option value="${esc(name)}" ${name === selected ? "selected" : ""}>${esc(s.label || name)}</option>`;
  return [
    none ? `<option value="" ${selected ? "" : "selected"}>Strateji yok (yalnızca emirler)</option>` : "",
    `<optgroup label="Hazır stratejiler">${builtin.map(option).join("")}</optgroup>`,
    scripts.length ? `<optgroup label="Script stratejileri">${scripts.map(option).join("")}</optgroup>` : "",
  ].join("");
}

export function paramFields(strategy, values = {}) {
  const entry = state.meta?.strategies?.[strategy];
  if (!entry) return "";
  const inputs = Object.fromEntries((entry.inputs ?? []).map((i) => [i.name, i]));
  return Object.entries(entry.params).map(([name, value]) => {
    const spec = inputs[name];
    const label = spec?.title || name;
    if (typeof value === "boolean") {
      return `<label class="check"><input type="checkbox" name="param:${esc(name)}" ${values[name] ?? value ? "checked" : ""}> ${esc(label)}</label>`;
    }
    if (typeof value === "string") {
      const options = spec?.options ?? (spec?.kind === "source" ? ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4"] : null);
      if (options) {
        return `<label class="field">${esc(label)}<select name="param:${esc(name)}">${options.map((o) =>
          `<option ${String(values[name] ?? value) === String(o) ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
      }
      return `<label class="field">${esc(label)}<input name="param:${esc(name)}" value="${esc(values[name] ?? value)}"></label>`;
    }
    const attrs = [
      spec?.minval !== null && spec?.minval !== undefined ? `min="${spec.minval}"` : "",
      spec?.maxval !== null && spec?.maxval !== undefined ? `max="${spec.maxval}"` : "",
    ].join(" ");
    return `<label class="field">${esc(label)}
      <input name="param:${esc(name)}" type="number" step="any" ${attrs} value="${esc(values[name] ?? value)}"></label>`;
  }).join("");
}

export function readParams(form) {
  const params = {};
  for (const input of form.querySelectorAll("[name^='param:']")) {
    const name = input.name.slice(6);
    if (input.type === "checkbox") params[name] = input.checked;
    else if (input.type === "number") {
      if (input.value !== "") params[name] = Number(input.value);
    } else params[name] = input.value;
  }
  return params;
}

// ---------------------------------------------------------------- overlays

/** Open a dialog (a bottom sheet on phones). Returns {node, close}. */
export function sheet(html, { wide = false, title = "", className = "" } = {}) {
  const scrim = document.createElement("div");
  scrim.className = "scrim";
  scrim.innerHTML = `<div class="sheet ${wide ? "wide" : ""} ${className}" role="dialog" aria-modal="true" aria-label="${esc(title)}">
    <div class="grabber" aria-hidden="true"></div>
    ${title ? `<div class="sheet-head"><h2>${esc(title)}</h2><span class="spacer"></span>
      <button class="icon-btn sm ghost" type="button" data-close aria-label="Kapat">${icon("x")}</button></div>` : ""}
    <div class="sheet-body">${html}</div></div>`;
  const previous = document.activeElement;
  const close = () => {
    scrim.remove();
    document.removeEventListener("keydown", onKey);
    previous?.focus?.();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  scrim.addEventListener("click", (event) => {
    if (event.target === scrim || event.target.closest("[data-close]")) close();
  });
  document.addEventListener("keydown", onKey);
  $("#layer").appendChild(scrim);
  const node = $(".sheet", scrim);
  (node.querySelector("input, textarea, select, button:not([data-close])") ?? node).focus();
  return { node, body: $(".sheet-body", node), close };
}

/** Ask for a short text in a small dialog; resolves to "" when cancelled. */
export function ask(title, label, value = "") {
  return new Promise((resolve) => {
    const { node, close } = sheet(`
      <form class="form"><label class="field">${esc(label)}
        <input name="value" value="${esc(value)}" autocomplete="off" spellcheck="false"></label>
        <button class="btn primary block" type="submit">Tamam</button></form>`, { title });
    let done = false;
    const finish = (result) => {
      if (done) return;
      done = true;
      resolve(result);
    };
    const form = $("form", node);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      finish(form.elements.value.value.trim());
      close();
    });
    new MutationObserver((_, observer) => {
      if (!node.isConnected) {
        observer.disconnect();
        finish("");
      }
    }).observe($("#layer"), { childList: true });
    setTimeout(() => form.elements.value.select(), 40);
  });
}

export function go(hash) {
  if (location.hash === hash) emit("route");
  else location.hash = hash;
}

export function copyText(text) {
  navigator.clipboard?.writeText(text).then(
    () => toast("Kopyalandı."),
    () => toast("Kopyalanamadı.", "error"),
  );
}

export function mount(observer) {
  state.observers.push(observer);
}

export const when = (t) => parseTime(t);
