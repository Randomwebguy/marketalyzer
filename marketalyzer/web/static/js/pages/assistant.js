// Assistant: chat with the AI, which uses the app's tools through OpenRouter or fal.ai.
import {
  $, api, ApiError, copyText, esc, fmtNumber, go, icon, loadPaper, state, store, stream, toast,
} from "/static/js/core.js";
import { renderMarkdown } from "/static/js/markdown.js";

const TOOL_LABELS = {
  market_overview: "Piyasa özeti",
  get_quotes: "Fiyatlar",
  price_history: "Fiyat geçmişi",
  technical_analysis: "Teknik analiz",
  list_strategies: "Stratejiler",
  list_scripts: "Scriptler",
  get_script: "Script oku",
  check_script: "Script kontrolü",
  save_script: "Script kaydet",
  run_script: "Script çalıştır",
  screen_symbols: "Tarama",
  backtest: "Backtest",
  optimize_strategy: "Optimizasyon",
  walk_forward: "Walk-forward",
  paper_account: "Sanal hesap",
  paper_order: "Sanal emir",
  paper_cancel: "Emir iptali",
};
const SUGGESTIONS = [
  ["Piyasa özeti", "Bugün BIST'te durum ne? Endeksleri ve likit hisselerdeki en büyük hareketleri özetle."],
  ["Teknik analiz", (s) => `${s} için detaylı teknik analiz yap: trend, momentum, destek/direnç ve riskler.`],
  ["Strateji yaz", (s) => `RSI ve 200 günlük ortalamayı birlikte kullanan bir strateji scripti yaz ve ${s} üzerinde son 3 yılda backtest et.`],
  ["Optimize et", "SMA kesişimi stratejisini GARAN'da optimize et ve aşırı uyum riskini holdout sonuçlarıyla değerlendir."],
  ["Tarama", "Likit BIST hisselerinde RSI dönüşü stratejisini tara; bugün sinyal verenleri karşılaştır."],
  ["Hesabım", "Sanal hesabımı, pozisyonlarımı ve risklerini değerlendir."],
];

function argsSummary(args) {
  return Object.entries(args ?? {})
    .filter(([key]) => key !== "source")
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
    .join(" · ") + (args?.source ? " · source=…" : "");
}

export function render(root, { params }) {
  const local = {
    id: params[0] && params[0] !== "yeni" ? params[0] : null,
    settings: null,
    controller: null,
    running: false,
    list: [],
    model: null,
    context: store.get("assistant.context", null),
    useSymbol: true,
  };
  root.innerHTML = `
    <div class="assistant">
      <aside class="card convo-list">
        <div class="card-head"><h2>Sohbetler</h2><span class="spacer"></span>
          <button class="btn small" type="button" data-new>${icon("plus", "sm")} Yeni</button></div>
        <div class="scroll" data-list></div>
      </aside>
      <section class="card chat">
        <div class="chat-head">
          <button class="icon-btn sm" type="button" data-toggle-list aria-label="Sohbetler">${icon("menu")}</button>
          <h2 data-title>Yeni sohbet</h2><span class="spacer"></span>
          <span class="chip tag" data-model hidden></span>
          <a class="icon-btn sm" href="#/ayarlar" aria-label="Asistan ayarları">${icon("settings")}</a>
        </div>
        <div class="thread" data-thread aria-live="polite"></div>
        <div class="composer">
          <div class="ctx" data-ctx></div>
          <form class="box" data-form>
            <textarea name="message" rows="1" placeholder="Mesajınızı yazın…" aria-label="Mesaj"></textarea>
            <button class="icon-btn round" type="submit" data-send aria-label="Gönder">${icon("send")}</button>
          </form>
          <div class="meta"><span>Yanıtlar yatırım tavsiyesi değildir · sanal hesap, gerçek para yok</span><span data-usage></span></div>
        </div>
      </section>
    </div>`;
  const thread = $("[data-thread]", root);
  const form = $("[data-form]", root);
  const input = form.elements.message;

  // ---------------------------------------------------------- list
  async function loadList() {
    try {
      local.list = await api("/api/ai/conversations");
    } catch {
      local.list = [];
    }
    $("[data-list]", root).innerHTML = local.list.map((c) => `
      <div class="convo ${c.id === local.id ? "active" : ""}" data-open="${esc(c.id)}">
        <div class="grow"><b>${esc(c.title)}</b><span>${esc(new Date(c.updated).toLocaleString("tr-TR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))} · ${c.messages} mesaj</span></div>
        <button class="icon-btn xs ghost del" type="button" data-delete="${esc(c.id)}" aria-label="Sil">${icon("trash")}</button>
      </div>`).join("") || '<div class="empty small">Henüz sohbet yok.</div>';
  }

  // ---------------------------------------------------------- messages
  const scroll = () => {
    thread.scrollTop = thread.scrollHeight;
  };

  function messageNode(role) {
    const node = document.createElement("div");
    node.className = `msg ${role}`;
    node.innerHTML = `<div class="who">${role === "user" ? "Siz" : icon("sparkle", "sm")}</div><div class="body"></div>`;
    thread.appendChild(node);
    return $(".body", node);
  }

  function userMessage(text) {
    const body = messageNode("user");
    body.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  }

  function textSegment(body) {
    const node = document.createElement("div");
    node.className = "bubble md";
    body.appendChild(node);
    return { node, text: "", blocks: [] };
  }

  function paintSegment(segment) {
    const { html, blocks } = renderMarkdown(segment.text);
    segment.node.innerHTML = html;
    segment.blocks = blocks;
    segment.node.dataset.blocks = JSON.stringify(blocks);
  }

  function toolNode(body, { id, name, args }) {
    const node = document.createElement("details");
    node.className = "tool";
    node.dataset.id = id;
    node.innerHTML = `<summary><span class="state"><span class="spin"></span></span>
      <b>${esc(TOOL_LABELS[name] || name)}</b><span class="args">${esc(argsSummary(args))}</span></summary>
      <pre data-result>Çalışıyor…</pre>`;
    node.dataset.name = name;
    node.dataset.args = JSON.stringify(args ?? {});
    body.appendChild(node);
    return node;
  }

  function finishTool(node, { ok, result }) {
    $(".state", node).innerHTML = ok ? icon("check", "sm") : icon("x", "sm");
    node.classList.toggle("bad", !ok);
    const text = typeof result === "string" ? result : JSON.stringify(result, null, 2);
    $("[data-result]", node).textContent = text.length > 4000 ? `${text.slice(0, 4000)}\n…` : text;
    const name = node.dataset.name;
    const args = JSON.parse(node.dataset.args || "{}");
    const actions = [];
    if (ok && name === "save_script" && result?.saved) actions.push(`<a class="btn small" href="#/lab/${esc(result.saved)}">${icon("code", "sm")} Editörde aç</a>`);
    if (ok && ["technical_analysis", "run_script", "price_history"].includes(name) && args.symbol) {
      actions.push(`<a class="btn small" href="#/piyasa/${esc(String(args.symbol).toUpperCase())}">${icon("chart", "sm")} Grafikte göster</a>`);
    }
    if (ok && name === "backtest" && args.strategy && !args.source) {
      actions.push(`<a class="btn small" href="#/backtest/${esc(String(args.symbol).toUpperCase())}">${icon("flask", "sm")} Backtest sayfası</a>`);
    }
    if (actions.length) node.insertAdjacentHTML("beforeend", `<div class="tool-actions">${actions.join("")}</div>`);
  }

  function noticeMessage(text, kind = "notice") {
    const body = messageNode(kind);
    body.innerHTML = `<div class="bubble">${esc(text)}</div>`;
  }

  function showStored(messages) {
    thread.replaceChildren();
    let body = null;
    let segment = null;
    for (const m of messages) {
      if (m.role === "user") {
        userMessage(m.text);
        body = null;
        segment = null;
      } else {
        body ??= messageNode("ai");
        if (m.role === "assistant") {
          segment = textSegment(body);
          segment.text = m.text;
          paintSegment(segment);
        } else if (m.role === "tool") {
          const node = toolNode(body, m);
          if (m.ok !== null && m.ok !== undefined) finishTool(node, m);
          segment = null;
        }
      }
    }
    if (!messages.length) showWelcome();
    scroll();
  }

  function showWelcome() {
    const symbol = state.symbol || "THYAO";
    thread.innerHTML = `
      <div class="chat-hero"><div class="orb">${icon("sparkle", "lg")}</div>
        <h2>Ne üzerinde çalışalım?</h2>
        <p>Asistan piyasa verisine, teknik analize, script editörüne, backtest ve walk-forward araçlarına ve sanal hesabınıza erişebilir.
          Her sayıyı araçlardan alır.</p></div>
      <div class="suggestions">${SUGGESTIONS.map(([title, text]) => {
        const prompt = typeof text === "function" ? text(symbol) : text;
        return `<button class="suggestion" type="button" data-suggest="${esc(prompt)}"><b>${esc(title)}</b>${esc(prompt)}</button>`;
      }).join("")}</div>`;
  }

  function showKeyNeeded() {
    thread.innerHTML = `
      <div class="chat-hero"><div class="orb">${icon("key", "lg")}</div>
        <h2>API anahtarı gerekli</h2>
        <p>Asistan, seçtiğiniz modeli OpenRouter ya da fal.ai üzerinden kullanır. Anahtarınızı
          <a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer" style="text-decoration:underline">openrouter.ai/keys</a> veya
          <a href="https://fal.ai/dashboard/keys" target="_blank" rel="noopener noreferrer" style="text-decoration:underline">fal.ai/dashboard/keys</a>
          adresinden alın. Anahtar yalnızca sunucuda saklanır, tarayıcıya geri gönderilmez.</p></div>
      <form class="card flat" data-key-form style="max-width:520px;margin:0 auto;width:100%">
        <div class="form">
          <label class="field">OpenRouter ya da fal.ai API anahtarı<input name="key" type="password" placeholder="sk-or-v1-… / fal_sk_…" autocomplete="off" required></label>
          <button class="btn violet block" type="submit">${icon("key", "sm")} Kaydet ve başla</button>
        </div>
      </form>`;
    $("[data-key-form]", thread).addEventListener("submit", async (event) => {
      event.preventDefault();
      const key = event.target.elements.key.value.trim();
      // fal.ai keys are "fal_sk_…:…" or "<id>:<secret>"; OpenRouter's have no colon.
      const fal = key.startsWith("fal_") || key.includes(":");
      try {
        local.settings = await api("/api/ai/settings", fal ? { provider: "fal", fal_key: key } : { provider: "openrouter", api_key: key });
        if (state.meta) state.meta.ai = local.settings;
        toast("Anahtar kaydedildi.");
        showWelcome();
        sendDraft();
      } catch (error) {
        toast(error.message, "error");
      }
    });
  }

  function drawContext() {
    const chips = [];
    if (state.symbol) {
      chips.push(`<button class="chip tag ${local.useSymbol ? "sky" : ""}" type="button" data-ctx-symbol>${icon("chart", "sm")} ${esc(state.symbol)}</button>`);
    }
    if (local.context?.script_source) {
      chips.push(`<span class="chip tag sky">${icon("code", "sm")} Editördeki script: ${esc(local.context.script_name)}
        <button class="icon-btn xs ghost" type="button" data-ctx-clear aria-label="Kaldır">${icon("x")}</button></span>`);
    }
    $("[data-ctx]", root).innerHTML = chips.join("");
  }

  function setRunning(on) {
    local.running = on;
    const button = $("[data-send]", root);
    button.innerHTML = icon(on ? "stop" : "send");
    button.setAttribute("aria-label", on ? "Durdur" : "Gönder");
    button.classList.toggle("alert", on);
  }

  // ---------------------------------------------------------- sending
  async function send(text) {
    const message = text.trim();
    if (!message || local.running) return;
    if (!local.settings?.configured) {
      store.set("draft", message);
      showKeyNeeded();
      return;
    }
    if (thread.querySelector(".chat-hero")) thread.replaceChildren();
    userMessage(message);
    input.value = "";
    input.style.height = "";
    const body = messageNode("ai");
    const typing = document.createElement("div");
    typing.className = "typing";
    typing.innerHTML = "<i></i><i></i><i></i>";
    body.appendChild(typing);
    scroll();
    let segment = null;
    let frame = 0;
    const tools = new Map();
    const context = { page: document.title.split(" · ")[0] };
    if (local.useSymbol && state.symbol) context.symbol = state.symbol;
    if (local.context?.script_source) Object.assign(context, local.context);
    local.controller = new AbortController();
    setRunning(true);
    const onEvent = (event) => {
      if (event.type === "start") {
        local.id = event.conversation_id;
        history.replaceState(null, "", `#/asistan/${local.id}`);
        $("[data-title]", root).textContent = event.title;
      } else if (event.type === "model") {
        local.model = event.model;
        const chip = $("[data-model]", root);
        chip.hidden = false;
        chip.textContent = event.model.split("/").at(-1);
      } else if (event.type === "text") {
        segment ??= textSegment(body);
        segment.text += event.text;
        if (!frame) {
          frame = requestAnimationFrame(() => {
            frame = 0;
            paintSegment(segment);
            body.appendChild(typing);
            scroll();
          });
        }
      } else if (event.type === "tool_start") {
        if (segment) paintSegment(segment);
        segment = null;
        tools.set(event.id, toolNode(body, event));
        body.appendChild(typing);
        scroll();
      } else if (event.type === "tool_end") {
        const node = tools.get(event.id);
        if (node) finishTool(node, event);
      } else if (event.type === "notice") {
        const note = document.createElement("div");
        note.className = "muted small";
        note.textContent = event.text;
        body.appendChild(note);
      } else if (event.type === "error") {
        const error = document.createElement("div");
        error.className = "bubble error";
        error.innerHTML = `${esc(event.message)} <a href="#/ayarlar" style="text-decoration:underline">Ayarlar</a>`;
        body.appendChild(error);
      } else if (event.type === "usage") {
        const cost = event.cost ? ` · $${fmtNumber(event.cost, 4)}` : "";
        $("[data-usage]", root).textContent = `${fmtNumber(event.prompt_tokens + event.completion_tokens, 0)} token${cost}`;
      } else if (event.type === "script_saved") {
        toast(`Script kaydedildi: ${event.name}`);
        api("/api/meta").then((meta) => { state.meta = meta; }).catch(() => {});
      } else if (event.type === "paper_changed") {
        loadPaper();
      } else if (event.type === "done") {
        if (event.title) $("[data-title]", root).textContent = event.title;
        if (event.usage?.cost) {
          $("[data-usage]", root).textContent = `Sohbet toplamı: ${fmtNumber((event.usage.prompt_tokens ?? 0) + (event.usage.completion_tokens ?? 0), 0)} token · $${fmtNumber(event.usage.cost, 4)}`;
        }
      }
    };
    try {
      await stream("/api/ai/chat", { message, conversation_id: local.id, context }, onEvent, local.controller.signal);
    } catch (error) {
      if (error.name !== "AbortError") {
        const note = document.createElement("div");
        note.className = "bubble error";
        note.textContent = error instanceof ApiError ? error.message : `Bağlantı hatası: ${error.message}`;
        body.appendChild(note);
      } else {
        const note = document.createElement("div");
        note.className = "muted small";
        note.textContent = "Durduruldu.";
        body.appendChild(note);
      }
    } finally {
      cancelAnimationFrame(frame);
      if (segment) paintSegment(segment);
      typing.remove();
      setRunning(false);
      local.controller = null;
      scroll();
      loadList();
    }
  }

  function stop() {
    local.controller?.abort();
    if (local.id) api(`/api/ai/stop/${local.id}`, {}).catch(() => {});
  }

  function sendDraft() {
    const draft = store.get("draft", "");
    if (!draft) return;
    store.set("draft", "");
    if (local.settings?.configured) send(draft);
    else input.value = draft;
  }

  async function openConversation(id) {
    try {
      const conversation = await api(`/api/ai/conversations/${id}`);
      local.id = conversation.id;
      $("[data-title]", root).textContent = conversation.title;
      const chip = $("[data-model]", root);
      chip.hidden = !conversation.model;
      chip.textContent = (conversation.model || "").split("/").at(-1);
      $("[data-usage]", root).textContent = conversation.usage?.cost
        ? `Sohbet toplamı: $${fmtNumber(conversation.usage.cost, 4)}` : "";
      showStored(conversation.messages);
      history.replaceState(null, "", `#/asistan/${conversation.id}`);
      loadList();
    } catch (error) {
      toast(error.message, "error");
      newConversation();
    }
  }

  function newConversation() {
    if (local.running) stop();
    local.id = null;
    $("[data-title]", root).textContent = "Yeni sohbet";
    $("[data-usage]", root).textContent = "";
    if (local.settings?.configured) showWelcome();
    else showKeyNeeded();
    history.replaceState(null, "", "#/asistan/yeni");
    loadList();
    input.focus();
  }

  // ---------------------------------------------------------- events
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (local.running) stop();
    else send(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!local.running) send(input.value);
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(180, input.scrollHeight)}px`;
  });
  root.addEventListener("click", async (event) => {
    const t = event.target;
    const suggestion = t.closest("[data-suggest]");
    if (suggestion) return send(suggestion.dataset.suggest);
    if (t.closest("[data-new]")) {
      root.querySelector(".assistant").classList.remove("show-list");
      return newConversation();
    }
    const del = t.closest("[data-delete]");
    if (del) {
      event.stopPropagation();
      if (!confirm("Sohbet silinsin mi?")) return null;
      await api(`/api/ai/conversations/${del.dataset.delete}`, undefined, "DELETE").catch((error) => toast(error.message, "error"));
      if (del.dataset.delete === local.id) newConversation();
      return loadList();
    }
    const openItem = t.closest("[data-open]");
    if (openItem) {
      root.querySelector(".assistant").classList.remove("show-list");
      return openConversation(openItem.dataset.open);
    }
    if (t.closest("[data-toggle-list]")) return root.querySelector(".assistant").classList.toggle("show-list");
    if (t.closest("[data-ctx-symbol]")) {
      local.useSymbol = !local.useSymbol;
      return drawContext();
    }
    if (t.closest("[data-ctx-clear]")) {
      local.context = null;
      store.set("assistant.context", null);
      return drawContext();
    }
    const copy = t.closest("[data-copy]");
    if (copy) {
      const blocks = JSON.parse(copy.closest(".md").dataset.blocks || "[]");
      return copyText(blocks[Number(copy.dataset.copy)]?.code ?? "");
    }
    const openCode = t.closest("[data-open-code]");
    if (openCode) {
      const blocks = JSON.parse(openCode.closest(".md").dataset.blocks || "[]");
      store.set("lab.pending", { source: blocks[Number(openCode.dataset.openCode)]?.code ?? "", name: "" });
      return go("#/lab/yeni");
    }
    return null;
  });

  // ---------------------------------------------------------- start
  drawContext();
  setRunning(false);
  api("/api/ai/settings").then((settings) => {
    local.settings = settings;
    if (local.id) openConversation(local.id);
    else if (!settings.configured) showKeyNeeded();
    else showWelcome();
    sendDraft();
  }).catch((error) => toast(error.message, "error"));
  loadList();
  return () => local.controller?.abort();
}
