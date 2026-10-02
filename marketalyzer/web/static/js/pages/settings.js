// Settings: OpenRouter key and model, assistant permissions, display, app.
import { $, api, busy, emit, esc, fmtMoney, fmtNumber, icon, state, store, toast } from "/static/js/core.js";

export function render(root) {
  const local = { settings: null, models: null, query: "", toolsOnly: true, default: null };
  root.innerHTML = `
    <div class="settings">
      <section class="card glow-violet" data-ai></section>
      <div class="stack">
        <section class="card" data-models></section>
        <section class="card" data-display></section>
        <section class="card" data-app></section>
      </div>
    </div>`;

  function drawAi() {
    const s = local.settings;
    const box = $("[data-ai]", root);
    box.innerHTML = `
      <div class="card-head"><h2>Yapay zeka · OpenRouter</h2><span class="spacer"></span><span class="badge violet">AI</span></div>
      <div class="key-status"><span class="light ${s?.configured ? "on" : ""}"></span>
        <div class="grow" style="flex:1"><b>${s?.configured ? "Anahtar bağlı" : "Anahtar yok"}</b>
          <div class="muted small">${s?.configured ? `${esc(s.key_hint)} · ${s.key_source === "env" ? "OPENROUTER_API_KEY ortam değişkeni" : "sunucudaki ayar dosyası"}` : "Asistanı kullanmak için bir OpenRouter API anahtarı ekleyin."}</div></div>
        ${s?.configured ? `<button class="btn small" type="button" data-test>${icon("zap", "sm")} Test et</button>` : ""}
      </div>
      <form class="form" data-key-form style="margin-top:14px">
        <label class="field">${s?.configured ? "Anahtarı değiştir" : "API anahtarı"}
          <input name="key" type="password" placeholder="sk-or-v1-…" autocomplete="off" spellcheck="false">
          <span class="hint">Anahtarınızı <a href="https://openrouter.ai/keys" target="_blank" rel="noopener noreferrer" style="text-decoration:underline">openrouter.ai/keys</a> adresinden oluşturun.
          Sunucuda yalnızca sizin okuyabileceğiniz bir dosyada (izin 600) saklanır ve tarayıcıya geri gönderilmez.</span></label>
        <div class="row-flex">
          <button class="btn violet" type="submit">${icon("key", "sm")} Kaydet</button>
          ${s?.configured && s.key_source !== "env" ? '<button class="btn ghost" type="button" data-clear>Anahtarı kaldır</button>' : ""}
        </div>
      </form>
      <div class="divider"></div>
      <div class="setting"><div class="grow"><b>Seçili model</b><span>${esc(s?.model || "Otomatik: araç kullanabilen en yeni Claude Sonnet")}</span></div>
        <a class="btn small" href="#models" data-scroll-models>Değiştir</a></div>
      <div class="setting"><div class="grow"><b>Asistan sanal emir verebilir</b>
        <span>Açıksa asistan, siz istediğinizde sanal hesapta emir verip iptal edebilir. Gerçek para yoktur.</span></div>
        <label class="switch"><input type="checkbox" data-trading ${s?.allow_trading ? "checked" : ""} aria-label="Asistan sanal emir verebilir"><span></span></label></div>
      <p class="muted small" style="margin:12px 0 0">Asistan; fiyat, teknik analiz, script, backtest, optimizasyon, walk-forward, tarama ve sanal hesap
        araçlarını kullanır. Kullanım ücreti OpenRouter hesabınızdan düşer; her sohbetin token ve maliyeti gösterilir.</p>`;
  }

  function drawModels() {
    const box = $("[data-models]", root);
    box.id = "models";
    if (!local.models) {
      box.innerHTML = `<div class="card-head"><h2>Model</h2></div>
        <div class="empty"><b>Model listesi</b>OpenRouter'daki modelleri fiyatlarıyla listeleyin.
        <div><button class="btn" type="button" data-load-models>${icon("refresh", "sm")} Modelleri yükle</button></div></div>`;
      return;
    }
    const q = local.query.toLowerCase();
    const shown = local.models
      .filter((m) => (!local.toolsOnly || m.tools) && (!q || `${m.id} ${m.name}`.toLowerCase().includes(q)))
      .slice(0, 200);
    const price = (m) => (m.prompt_price === null ? "—" : `$${fmtNumber(m.prompt_price, 2)} / $${fmtNumber(m.completion_price ?? 0, 2)}`);
    box.innerHTML = `
      <div class="card-head"><h2>Model</h2><span class="sub">${shown.length} model · 1M token giriş/çıkış fiyatı</span></div>
      <div class="row-flex" style="margin-bottom:10px">
        <input class="input" data-model-search placeholder="claude, gpt, gemini…" value="${esc(local.query)}" style="flex:1;height:34px" aria-label="Model ara">
        <label class="check"><input type="checkbox" data-tools-only ${local.toolsOnly ? "checked" : ""}> Araç destekleyenler</label>
      </div>
      <div class="model-list">${shown.map((m) => `
        <div class="model ${m.id === local.settings?.model ? "active" : ""}" data-pick-model="${esc(m.id)}">
          <b>${esc(m.name || m.id)}${m.id === local.default ? ' <span class="chip tag sky">önerilen</span>' : ""}</b>
          <span class="price">${esc(price(m))}</span>
          <code>${esc(m.id)}${m.context_length ? ` · ${fmtNumber(m.context_length / 1000, 0)}K bağlam` : ""}</code>
        </div>`).join("") || '<div class="empty">Sonuç yok.</div>'}</div>
      ${local.settings?.model ? '<button class="btn small ghost" type="button" data-auto-model style="margin-top:10px">Otomatik seçime dön</button>' : ""}`;
  }

  function drawDisplay() {
    $("[data-display]", root).innerHTML = `
      <div class="card-head"><h2>Görünüm</h2></div>
      <div class="setting"><div class="grow"><b>Bakiyeleri gizle</b><span>Ekran paylaşırken tutarları maskeler (kısayol: H).</span></div>
        <label class="switch"><input type="checkbox" data-hidden ${state.hidden ? "checked" : ""} aria-label="Bakiyeleri gizle"><span></span></label></div>
      <div class="setting"><div class="grow"><b>Para birimi</b><span>Tutarları TL ya da güncel kurla USD gösterir${state.usdtry ? ` (USD/TRY ${fmtNumber(state.usdtry, 2)})` : ""}.</span></div>
        <div class="segmented" data-currency>
          <button type="button" data-ccy="TRY" class="${state.currency === "TRY" ? "active" : ""}">₺ TRY</button>
          <button type="button" data-ccy="USD" class="${state.currency === "USD" ? "active" : ""}">$ USD</button></div></div>`;
  }

  function drawApp() {
    const p = state.paper;
    $("[data-app]", root).innerHTML = `
      <div class="card-head"><h2>Uygulama ve hesap</h2></div>
      <div class="summary-rows">
        <div><span>Veri</span><b>${state.meta?.demo ? "Demo (sentetik)" : "Yahoo · ~15 dk gecikmeli"}</b></div>
        <div><span>Sanal hesap</span><b>${p?.exists ? esc(fmtMoney(p.equity)) : "Yok"}</b></div>
        <div><span>Hesap adı</span><b>${esc(state.meta?.account ?? "—")}</b></div>
      </div>
      <div class="row-flex" style="margin-top:12px">
        <a class="btn small" href="#/islem/hesap">${icon("wallet", "sm")} Hesap ayarları</a>
        <button class="btn small" type="button" data-install>${icon("phone", "sm")} Ana ekrana ekle</button>
        <a class="btn small ghost" href="/logout">${icon("logout", "sm")} Çıkış</a>
      </div>`;
  }

  async function loadSettings() {
    local.settings = await api("/api/ai/settings").catch(() => null);
    if (state.meta) state.meta.ai = local.settings;
    drawAi();
    drawModels();
  }

  async function saveSettings(body, message) {
    try {
      local.settings = await api("/api/ai/settings", body);
      if (state.meta) state.meta.ai = local.settings;
      toast(message);
    } catch (error) {
      toast(error.message, "error");
    }
    drawAi();
    drawModels();
  }

  root.addEventListener("submit", async (event) => {
    if (!event.target.matches("[data-key-form]")) return;
    event.preventDefault();
    const key = event.target.elements.key.value.trim();
    if (!key) return;
    await saveSettings({ api_key: key }, "Anahtar kaydedildi.");
  });
  root.addEventListener("click", async (event) => {
    const t = event.target;
    if (t.closest("[data-test]")) {
      const button = t.closest("[data-test]");
      busy(button, true, "Deneniyor…");
      try {
        const info = await api("/api/ai/test", {});
        const limit = info.limit === null || info.limit === undefined ? "limitsiz" : `limit $${fmtNumber(info.limit, 2)}`;
        toast(`Bağlantı tamam · ${info.label || "anahtar"} · kullanım $${fmtNumber(info.usage ?? 0, 4)} · ${limit}`);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        busy(button, false);
      }
    } else if (t.closest("[data-clear]")) {
      if (confirm("Kayıtlı API anahtarı silinsin mi?")) await saveSettings({ clear_key: true }, "Anahtar kaldırıldı.");
    } else if (t.closest("[data-load-models]")) {
      const button = t.closest("[data-load-models]");
      busy(button, true, "Yükleniyor…");
      try {
        const body = await api("/api/ai/models");
        local.models = body.models;
        local.default = body.default;
      } catch (error) {
        toast(error.message, "error");
      }
      drawModels();
    } else if (t.closest("[data-pick-model]")) {
      const id = t.closest("[data-pick-model]").dataset.pickModel;
      await saveSettings({ model: id }, `Model seçildi: ${id}`);
    } else if (t.closest("[data-auto-model]")) {
      await saveSettings({ model: "" }, "Model otomatik seçilecek.");
    } else if (t.closest("[data-scroll-models]")) {
      event.preventDefault();
      $("[data-models]", root).scrollIntoView({ behavior: "smooth" });
    } else if (t.closest("[data-ccy]")) {
      const code = t.closest("[data-ccy]").dataset.ccy;
      if (code === "USD" && !state.usdtry) {
        toast("USD/TRY kuru alınamadı.", "error");
        return;
      }
      state.currency = code;
      store.set("currency", code);
      $("#currency").textContent = code === "TRY" ? "₺" : "$";
      emit("display");
      drawDisplay();
    } else if (t.closest("[data-install]")) {
      if (window.deferredInstall) window.deferredInstall.prompt();
      else toast("Tarayıcı menüsünden 'Uygulamayı yükle' ya da iPhone'da Paylaş > Ana Ekrana Ekle'yi seçin.");
    }
  });
  root.addEventListener("change", async (event) => {
    const t = event.target;
    if (t.matches("[data-trading]")) {
      await saveSettings({ allow_trading: t.checked }, t.checked ? "Asistan sanal emir verebilir." : "Asistanın emir izni kapatıldı.");
    } else if (t.matches("[data-hidden]")) {
      state.hidden = t.checked;
      store.set("hidden", state.hidden);
      emit("display");
    } else if (t.matches("[data-tools-only]")) {
      local.toolsOnly = t.checked;
      drawModels();
    }
  });
  root.addEventListener("input", (event) => {
    if (!event.target.matches("[data-model-search]")) return;
    local.query = event.target.value;
    const position = event.target.selectionStart;
    drawModels();
    const input = $("[data-model-search]", root);
    input.focus();
    input.setSelectionRange(position, position);
  });

  drawAi();
  drawModels();
  drawDisplay();
  drawApp();
  loadSettings();
}
