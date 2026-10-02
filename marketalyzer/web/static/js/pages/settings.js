// Settings: AI provider (OpenRouter or fal.ai), its key and model, assistant permissions, display, app.
import { $, api, busy, emit, esc, fmtMoney, fmtNumber, icon, state, store, toast } from "/static/js/core.js";

// The request fields and help for each provider's key.
const PROVIDERS = {
  openrouter: {
    label: "OpenRouter", field: "api_key", clear: "clear_key", env: "OPENROUTER_API_KEY",
    placeholder: "sk-or-v1-…", url: "https://openrouter.ai/keys", site: "openrouter.ai/keys",
  },
  fal: {
    label: "fal.ai", field: "fal_key", clear: "clear_fal_key", env: "FAL_KEY",
    placeholder: "fal_sk_…:…", url: "https://fal.ai/dashboard/keys", site: "fal.ai/dashboard/keys",
  },
};

export function render(root) {
  const local = {
    settings: null, models: null, query: "", toolsOnly: true, default: null, decision: null, speed: null, testing: false,
  };
  root.innerHTML = `
    <div class="settings">
      <div class="stack">
        <section class="card glow-violet" data-ai></section>
        <section class="card" data-decision></section>
      </div>
      <div class="stack">
        <section class="card" data-models></section>
        <section class="card" data-display></section>
        <section class="card" data-app></section>
      </div>
    </div>`;

  function drawAi() {
    const s = local.settings;
    const provider = s?.provider ?? "openrouter";
    const p = PROVIDERS[provider];
    const box = $("[data-ai]", root);
    box.innerHTML = `
      <div class="card-head"><h2>Yapay zeka · ${p.label}</h2><span class="spacer"></span><span class="badge violet">AI</span></div>
      <div class="setting"><div class="grow"><b>Sağlayıcı</b>
        <span>Sohbet, script yazma, kör test ve canlı kararlar seçili sağlayıcıyla çalışır. fal.ai aynı modelleri kendi hesabınızdan ücretlendirir.</span></div>
        <div class="segmented" role="group" aria-label="Yapay zeka sağlayıcısı">${Object.entries(PROVIDERS).map(([key, item]) => `
          <button type="button" data-pick-provider="${key}" class="${key === provider ? "active" : ""}" aria-pressed="${key === provider}">${item.label}${s?.providers?.[key]?.configured ? " ✓" : ""}</button>`).join("")}
        </div></div>
      <div class="key-status"><span class="light ${s?.configured ? "on" : ""}"></span>
        <div class="grow" style="flex:1"><b>${s?.configured ? `${p.label} anahtarı bağlı` : `${p.label} anahtarı yok`}</b>
          <div class="muted small">${s?.configured ? `${esc(s.key_hint)} · ${s.key_source === "env" ? `${p.env} ortam değişkeni` : "sunucudaki ayar dosyası"}` : `Asistanı kullanmak için bir ${p.label} API anahtarı ekleyin.`}</div></div>
        ${s?.configured ? `<button class="btn small" type="button" data-test>${icon("zap", "sm")} Test et</button>` : ""}
      </div>
      <form class="form" data-key-form style="margin-top:14px">
        <label class="field">${s?.configured ? `${p.label} anahtarını değiştir` : `${p.label} API anahtarı`}
          <input name="key" type="password" placeholder="${p.placeholder}" autocomplete="off" spellcheck="false">
          <span class="hint">Anahtarınızı <a href="${p.url}" target="_blank" rel="noopener noreferrer" style="text-decoration:underline">${p.site}</a> adresinden oluşturun.
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
        araçlarını kullanır. Kullanım ücreti ${p.label} hesabınızdan düşer; her sohbetin token ve maliyeti gösterilir.</p>`;
  }

  function drawModels() {
    const box = $("[data-models]", root);
    box.id = "models";
    if (!local.models) {
      box.innerHTML = `<div class="card-head"><h2>Model</h2></div>
        <div class="empty"><b>Model listesi</b>OpenRouter'daki modelleri fiyatlarıyla listeleyin${local.settings?.provider === "fal" ? "; fal.ai aynı model kimliklerini kullanır" : ""}.
        <div><button class="btn" type="button" data-load-models>${icon("refresh", "sm")} Modelleri yükle</button></div></div>`;
      return;
    }
    const q = local.query.toLowerCase();
    const shown = local.models
      .filter((m) => (!local.toolsOnly || m.tools) && (!q || `${m.id} ${m.name}`.toLowerCase().includes(q)))
      .slice(0, 200);
    const price = (m) => (m.prompt_price === null ? "—" : `$${fmtNumber(m.prompt_price, 2)} / $${fmtNumber(m.completion_price ?? 0, 2)}`);
    box.innerHTML = `
      <div class="card-head"><h2>Model</h2><span class="sub">${shown.length} model · 1M token giriş/çıkış fiyatı${local.settings?.provider === "fal" ? " · OpenRouter listesi, fal.ai'de aynı kimlikler" : ""}</span></div>
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

  function drawDecision() {
    const box = $("[data-decision]", root);
    const d = local.decision;
    const current = d?.current;
    const price = (row) => (row.prompt_price == null ? "fiyat bilinmiyor" : `$${fmtNumber(row.prompt_price, 2)} / $${fmtNumber(row.completion_price ?? 0, 2)} · 1M token`);
    const custom = current && d && !d.presets.some((row) => row.key === current) ? current : "";
    const speed = local.speed;
    box.innerHTML = `
      <div class="card-head"><h2>Karar modeli</h2><span class="sub">Kör test ve canlı AL/SAT kararları</span></div>
      <p class="muted small" style="margin:0 0 10px">Her sinyalde hızlı karar gerektiği için küçük, hızlı modeller kullanılır. Her aile
        OpenRouter'daki en yeni sürüme çözülür; istek en düşük gecikmeli sağlayıcıya yönlendirilir ve yanıt tek satır JSON'dur.</p>
      ${d ? `<div class="model-list">${d.presets.map((row) => `
        <div class="model ${row.key === current ? "active" : ""}" data-pick-decision="${esc(row.key)}" role="button" tabindex="0">
          <b>${esc(row.label)}${row.key === d.default ? ' <span class="chip tag sky">varsayılan</span>' : ""}</b>
          <span class="price">${esc(price(row))}</span>
          <code>${esc(row.model)}</code>
          <span class="muted small">${esc(row.note)}</span>
        </div>`).join("")}</div>
        ${d.error ? `<p class="muted small">Model listesi alınamadı (${esc(d.error)}); bilinen sürümler gösteriliyor.</p>` : ""}
        <form class="row-flex" data-custom-decision style="margin-top:10px">
          <input class="input" name="model" placeholder="özel model kimliği (ör. openai/gpt-4.1-mini)" value="${esc(custom)}" style="flex:1;height:34px" aria-label="Özel karar modeli">
          <button class="btn small" type="submit">Kaydet</button>
        </form>` : '<div class="empty">Yükleniyor…</div>'}
      <div class="divider"></div>
      <div class="row-flex"><b style="font-weight:500">Hız testi</b><span class="muted small">Aynı örnek kararı her modele iki kez sorar.</span>
        <span class="spacer"></span><button class="btn small" type="button" data-speed ${local.testing ? "disabled" : ""}>${icon("zap", "sm")} ${local.testing ? "Ölçülüyor…" : "Hız testi"}</button></div>
      ${speed ? `<div class="table-wrap speed-table" style="margin-top:10px"><table><thead><tr>
          <th>Model</th><th class="num">Süre</th><th>Örnek karar</th><th></th></tr></thead>
        <tbody>${speed.map((row) => `<tr>
          <td>${esc(row.label)}<div class="muted small">${esc(row.model)}</div></td>
          <td class="num">${row.ok ? `<b>${fmtNumber(row.average_ms, 0)} ms</b><div class="muted small">en iyi ${fmtNumber(row.best_ms, 0)} · $${fmtNumber(row.cost ?? 0, 5)}</div>` : `<span class="error">${esc(row.error ?? "hata")}</span>`}</td>
          <td>${row.ok ? `${esc(row.decision)} %${row.confidence ?? "—"}<div class="muted small">${esc(row.reason ?? "")}</div>` : "—"}</td>
          <td>${row.ok && row.key !== current ? `<button class="btn small" type="button" data-pick-decision="${esc(row.key)}">Seç</button>` : ""}</td>
        </tr>`).join("")}</tbody></table></div>
        <p class="muted small">Süreler bu sunucudan ${PROVIDERS[local.settings?.provider ?? "openrouter"].label} üzerinden gidiş-dönüştür; ağınıza ve sağlayıcının o anki yüküne göre değişir.</p>` : ""}`;
  }

  async function loadDecision() {
    local.decision = await api("/api/ai/decision-models").catch(() => null);
    drawDecision();
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
    loadDecision();
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
    if (body.decision_model !== undefined || body.api_key || body.fal_key || body.provider) loadDecision();
  }

  root.addEventListener("submit", async (event) => {
    if (event.target.matches("[data-custom-decision]")) {
      event.preventDefault();
      const model = event.target.elements.model.value.trim();
      await saveSettings({ decision_model: model }, model ? `Karar modeli: ${model}` : "Varsayılan karar modeli kullanılacak.");
      return;
    }
    if (!event.target.matches("[data-key-form]")) return;
    event.preventDefault();
    const key = event.target.elements.key.value.trim();
    if (!key) return;
    const provider = local.settings?.provider ?? "openrouter";
    await saveSettings({ provider, [PROVIDERS[provider].field]: key }, `${PROVIDERS[provider].label} anahtarı kaydedildi.`);
  });
  root.addEventListener("click", async (event) => {
    const t = event.target;
    if (t.closest("[data-test]")) {
      const button = t.closest("[data-test]");
      busy(button, true, "Deneniyor…");
      try {
        const info = await api("/api/ai/test", {});
        if (info.provider === "fal") {
          toast(`Bağlantı tamam · fal.ai · ${info.model} yanıt verdi · $${fmtNumber(info.cost ?? 0, 6)}`);
        } else {
          const limit = info.limit === null || info.limit === undefined ? "limitsiz" : `limit $${fmtNumber(info.limit, 2)}`;
          toast(`Bağlantı tamam · ${info.label || "anahtar"} · kullanım $${fmtNumber(info.usage ?? 0, 4)} · ${limit}`);
        }
      } catch (error) {
        toast(error.message, "error");
      } finally {
        busy(button, false);
      }
    } else if (t.closest("[data-pick-provider]")) {
      const key = t.closest("[data-pick-provider]").dataset.pickProvider;
      if (key !== local.settings?.provider) await saveSettings({ provider: key }, `Sağlayıcı: ${PROVIDERS[key].label}`);
    } else if (t.closest("[data-clear]")) {
      const p = PROVIDERS[local.settings?.provider ?? "openrouter"];
      if (confirm(`Kayıtlı ${p.label} anahtarı silinsin mi?`)) await saveSettings({ [p.clear]: true }, "Anahtar kaldırıldı.");
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
    } else if (t.closest("[data-pick-decision]")) {
      const key = t.closest("[data-pick-decision]").dataset.pickDecision;
      await saveSettings({ decision_model: key }, "Karar modeli seçildi.");
    } else if (t.closest("[data-speed]")) {
      if (!local.settings?.configured) {
        toast("Önce seçili sağlayıcı için bir API anahtarı kaydedin.", "error");
        return;
      }
      local.testing = true;
      drawDecision();
      try {
        local.speed = (await api("/api/ai/speed-test", {})).results;
      } catch (error) {
        toast(error.message, "error");
      }
      local.testing = false;
      drawDecision();
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

  root.addEventListener("keydown", (event) => {
    const row = event.target.closest(".model[data-pick-decision]");
    if (row && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      row.click();
    }
  });

  drawAi();
  drawDecision();
  drawModels();
  drawDisplay();
  drawApp();
  loadSettings();
}
