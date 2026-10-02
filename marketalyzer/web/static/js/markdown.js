// A small, safe Markdown renderer for assistant replies. All text is escaped
// first; only a fixed set of tags is produced, and links must be http(s).
import { esc } from "/static/js/core.js";
import { highlightLine } from "/static/js/editor.js";

function inline(text) {
  const codes = [];
  let out = esc(text).replace(/`([^`]+)`/g, (_, code) => {
    codes.push(code);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, n) => `<code>${codes[Number(n)]}</code>`);
}

const isScript = (lang, code) =>
  ["pine", "pinescript", "pine5", ""].includes(lang) && /\b(strategy|indicator|study)\s*\(/.test(code);

function tableHtml(rows) {
  const cells = (row) => row.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(2).map(cells);
  return `<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>
    <tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

/** Render Markdown; returns {html, blocks} where blocks are the code blocks. */
export function renderMarkdown(text) {
  const lines = String(text ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let html = "";
  let i = 0;
  const startsBlock = (line) => /^(```|#{1,4}\s|>\s?|\s*([-*+]|\d+[.)])\s+|\|)/.test(line);
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```\s*([\w+-]*)\s*$/);
    if (fence) {
      const lang = fence[1].toLowerCase();
      const body = [];
      i += 1;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) body.push(lines[i++]);
      i += 1;
      const code = body.join("\n");
      const index = blocks.push({ lang, code }) - 1;
      const script = isScript(lang, code);
      html += `<div class="codeblock"><div class="bar">${esc(lang || "kod")}<span class="spacer"></span>
        <button class="btn small ghost" type="button" data-copy="${index}">Kopyala</button>
        ${script ? `<button class="btn small" type="button" data-open-code="${index}">Editörde aç</button>` : ""}</div>
        <pre><code>${script ? body.map(highlightLine).join("\n") : esc(code)}</code></pre></div>`;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      html += `<h${heading[1].length <= 2 ? 3 : 4}>${inline(heading[2])}</h${heading[1].length <= 2 ? 3 : 4}>`;
      i += 1;
      continue;
    }
    if (/^\s*\|/.test(line) && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1] ?? "")) {
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      html += tableHtml(rows);
      continue;
    }
    const item = line.match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
    if (item) {
      const ordered = /\d/.test(item[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (m) items.push(m[2]);
        else if (/^\s{2,}\S/.test(lines[i]) && items.length) items[items.length - 1] += ` ${lines[i].trim()}`;
        else break;
        i += 1;
      }
      const tag = ordered ? "ol" : "ul";
      html += `<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${tag}>`;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^>\s?/, ""));
      html += `<blockquote>${inline(quote.join(" "))}</blockquote>`;
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      html += '<div class="divider"></div>';
      i += 1;
      continue;
    }
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const paragraph = [];
    while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) paragraph.push(lines[i++]);
    if (!paragraph.length) paragraph.push(lines[i++]);
    html += `<p>${paragraph.map(inline).join("<br>")}</p>`;
  }
  return { html, blocks };
}
