// A small code editor for the script language: a transparent textarea over a
// highlighted copy, with line numbers, error marks, indentation and completion.
import { esc } from "/static/js/core.js";

const KEYWORDS = new Set(["if", "else", "var", "varip", "and", "or", "not", "true", "false", "na", "for", "while", "switch", "import", "export", "type", "method", "return"]);
const TYPES = new Set(["int", "float", "bool", "string", "color", "series", "simple", "const"]);
const NAMESPACES = new Set(["ta", "math", "strategy", "input", "color", "plot", "shape", "location", "size", "hline", "display", "syminfo", "timeframe", "barstate", "str", "dayofweek", "request", "label", "line", "box", "table", "array", "extend", "xloc", "yloc", "position", "text", "font", "format", "scale", "alert", "order"]);
const VARIABLES = new Set(["open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4", "bar_index", "last_bar_index", "time", "year", "month", "dayofmonth", "dayofweek", "hour", "minute", "weekofyear"]);
const TOKEN = /(\/\/.*)|("(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?)|(#[0-9a-fA-F]{6,8}\b)|(\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?\b|\.\d+)|([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)|(:=|=>|==|!=|<=|>=|[-+*/%<>=?:])/g;
const LINE_HEIGHT = 21;
const PAD_TOP = 12;
const PAD_LEFT = 16;

export function highlightLine(text) {
  let out = "";
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    out += esc(text.slice(last, match.index));
    last = match.index + match[0].length;
    const [word, comment, string, color, num, name, op] = match;
    if (comment) out += `<span class="tok-com">${esc(word)}</span>`;
    else if (string) out += `<span class="tok-str">${esc(word)}</span>`;
    else if (color) out += `<span class="tok-col">${esc(word)}</span>`;
    else if (num) out += `<span class="tok-num">${esc(word)}</span>`;
    else if (op) out += `<span class="tok-op">${esc(word)}</span>`;
    else if (name) {
      const call = /^\s*\(/.test(text.slice(last));
      const dot = name.lastIndexOf(".");
      const head = dot > 0 ? name.slice(0, dot + 1) : "";
      const tail = dot > 0 ? name.slice(dot + 1) : name;
      const ns = head && NAMESPACES.has(head.split(".")[0]) ? `<span class="tok-ns">${esc(head)}</span>` : esc(head);
      if (!head && KEYWORDS.has(name)) out += `<span class="tok-kw">${esc(name)}</span>`;
      else if (!head && TYPES.has(name) && !call) out += `<span class="tok-type">${esc(name)}</span>`;
      else if (call) out += `${ns}<span class="tok-fn">${esc(tail)}</span>`;
      else if (!head && VARIABLES.has(name)) out += `<span class="tok-var">${esc(name)}</span>`;
      else if (head && NAMESPACES.has(head.split(".")[0])) out += `${ns}<span class="tok-var">${esc(tail)}</span>`;
      else out += esc(name);
    }
  }
  return out + esc(text.slice(last));
}

/**
 * Create an editor in ``container``. Options: value, onChange(value),
 * onRun(), onSave(), candidates() -> [{name, signature, doc, kind}].
 */
export function createEditor(container, { value = "", onChange, onRun, onSave, candidates = () => [] } = {}) {
  container.innerHTML = `
    <div class="code-editor">
      <div class="code-gutter" aria-hidden="true"></div>
      <div class="code-wrap"><pre aria-hidden="true"></pre>
        <textarea spellcheck="false" autocapitalize="off" autocomplete="off" autocorrect="off" wrap="off" aria-label="Script kodu"></textarea></div>
      <div class="completion" role="listbox" hidden></div>
    </div>`;
  const root = container.querySelector(".code-editor");
  const gutter = root.querySelector(".code-gutter");
  const pre = root.querySelector("pre");
  const area = root.querySelector("textarea");
  const menu = root.querySelector(".completion");
  let errorLine = null;
  let items = [];
  let active = 0;
  let prefix = "";

  const probe = document.createElement("span");
  probe.textContent = "M".repeat(20);
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre";
  pre.appendChild(probe);
  let charWidth = probe.getBoundingClientRect().width / 20 || 7.8;
  probe.remove();

  function paint() {
    const lines = area.value.split("\n");
    pre.innerHTML = lines.map((line, i) =>
      `<span class="ln${i + 1 === errorLine ? " bad" : ""}">${highlightLine(line)}</span>`).join("");
    gutter.innerHTML = lines.map((_, i) => `<div class="${i + 1 === errorLine ? "bad" : ""}">${i + 1}</div>`).join("");
  }

  function caret() {
    const before = area.value.slice(0, area.selectionStart);
    const lines = before.split("\n");
    return { line: lines.length, col: lines.at(-1).length + 1, before };
  }

  function insert(text, replace = 0) {
    area.focus();
    if (replace) area.setSelectionRange(area.selectionStart - replace, area.selectionEnd);
    // execCommand keeps the browser's undo history; setRangeText is the fallback.
    if (!document.execCommand?.("insertText", false, text)) {
      area.setRangeText(text, area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input"));
    }
  }

  function closeMenu() {
    menu.hidden = true;
    items = [];
  }

  function openMenu() {
    const { before, line, col } = caret();
    const match = before.match(/[A-Za-z_][A-Za-z0-9_.]*$/);
    prefix = match ? match[0] : "";
    if (prefix.length < 2 || /^\d/.test(prefix)) {
      closeMenu();
      return;
    }
    const lower = prefix.toLowerCase();
    const local = [...new Set([...area.value.matchAll(/^\s*(?:var\s+|varip\s+)?(?:(?:int|float|bool|string|color)\s+)?([A-Za-z_]\w*)\s*:?=/gm)].map((m) => m[1]))]
      .map((name) => ({ name, doc: "değişken", kind: "local" }));
    items = [...local, ...candidates()]
      .filter((c) => c.name.toLowerCase().startsWith(lower) && c.name !== prefix)
      .slice(0, 8);
    if (!items.length) {
      closeMenu();
      return;
    }
    active = 0;
    menu.innerHTML = items.map((c, i) => `<div class="${i === active ? "active" : ""}" data-i="${i}" role="option">
      <span>${esc(c.signature || c.name)}</span>${c.doc ? `<small>${esc(c.doc)}</small>` : ""}</div>`).join("");
    menu.style.left = `${gutter.offsetWidth + PAD_LEFT + (col - 1 - prefix.length) * charWidth}px`;
    menu.style.top = `${PAD_TOP + line * LINE_HEIGHT + 4}px`;
    menu.hidden = false;
  }

  function choose(i) {
    const item = items[i];
    if (!item) return;
    const fn = item.signature && item.signature.includes("(") && item.kind !== "local";
    closeMenu();
    insert(fn ? `${item.name}(` : item.name, prefix.length);
  }

  function indentation(line) {
    return line.match(/^\s*/)[0];
  }

  area.addEventListener("input", () => {
    if (errorLine !== null) errorLine = null;
    paint();
    onChange?.(area.value);
    openMenu();
  });
  area.addEventListener("keydown", (event) => {
    const mod = event.metaKey || event.ctrlKey;
    if (!menu.hidden) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        active = (active + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
        menu.querySelectorAll("[data-i]").forEach((el, i) => el.classList.toggle("active", i === active));
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        choose(active);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu();
        return;
      }
    }
    if (mod && event.key === "Enter") {
      event.preventDefault();
      onRun?.();
    } else if (mod && event.key.toLowerCase() === "s") {
      event.preventDefault();
      onSave?.();
    } else if (mod && event.key === " ") {
      event.preventDefault();
      openMenu();
    } else if (event.key === "Tab") {
      event.preventDefault();
      const { selectionStart: start, selectionEnd: end, value: text } = area;
      if (start === end && !event.shiftKey) {
        insert("    ");
        return;
      }
      const from = text.lastIndexOf("\n", start - 1) + 1;
      const block = text.slice(from, end);
      const changed = block.split("\n").map((line) =>
        (event.shiftKey ? line.replace(/^ {1,4}/, "") : `    ${line}`)).join("\n");
      area.setSelectionRange(from, end);
      insert(changed);
      area.setSelectionRange(from, from + changed.length);
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const { before } = caret();
      const line = before.slice(before.lastIndexOf("\n") + 1);
      let indent = indentation(line);
      const trimmed = line.trim();
      if (/^(if|else)\b/.test(trimmed) || trimmed.endsWith("=>")) indent += "    ";
      insert(`\n${indent}`);
    }
  });
  area.addEventListener("blur", () => setTimeout(closeMenu, 150));
  area.addEventListener("click", closeMenu);
  menu.addEventListener("mousedown", (event) => {
    const item = event.target.closest("[data-i]");
    if (item) {
      event.preventDefault();
      choose(Number(item.dataset.i));
    }
  });

  area.value = value;
  paint();
  return {
    get value() {
      return area.value;
    },
    set value(text) {
      area.value = text;
      errorLine = null;
      paint();
    },
    position: caret,
    setError(line) {
      errorLine = line || null;
      paint();
      if (errorLine) {
        const target = PAD_TOP + (errorLine - 3) * LINE_HEIGHT;
        if (target < root.scrollTop || target > root.scrollTop + root.clientHeight - 80) root.scrollTop = Math.max(0, target);
      }
    },
    focus: () => area.focus(),
    insert,
    goto(line) {
      const lines = area.value.split("\n");
      const offset = lines.slice(0, line - 1).reduce((sum, l) => sum + l.length + 1, 0);
      area.focus();
      area.setSelectionRange(offset, offset);
      root.scrollTop = Math.max(0, PAD_TOP + (line - 4) * LINE_HEIGHT);
    },
    textarea: area,
  };
}
