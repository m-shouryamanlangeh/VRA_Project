(function () {
  // ── Provider → model catalogue ──────────────────────────────────────────
  var PROVIDER_MODELS = {
    gemini: {
      hint: "✅ Recommended for VRA. Has built-in live web search (Google Search Grounding) — free tier gives 1,500 req/day per key. Add keys from multiple Google accounts to multiply quota.",
      models: [
        { value: "gemini-2.0-flash",     label: "gemini-2.0-flash (recommended, fast)" },
        { value: "gemini-2.0-flash-001", label: "gemini-2.0-flash-001" },
        { value: "gemini-2.5-flash",     label: "gemini-2.5-flash (best quality)" },
      ]
    },
    openrouter: {
      hint: "✅ Knowledge mode — when web search is unavailable, the LLM uses its training knowledge to analyse the vendor. Use llama-3.3-70b (recommended) or a paid model with live search for best results.",
      models: [
        { value: "openai/gpt-oss-120b:free",                 label: "gpt-oss-120b:free  ✦ free ⭐ RECOMMENDED — 120B, best knowledge" },
        { value: "meta-llama/llama-3.3-70b-instruct:free",   label: "llama-3.3-70b:free  ✦ free — 70B, good fallback" },
        { value: "perplexity/sonar",                         label: "perplexity/sonar  🌐 LIVE WEB SEARCH (paid, best for VRA)" },
        { value: "perplexity/sonar-pro",                     label: "perplexity/sonar-pro  🌐 LIVE WEB SEARCH (paid, highest quality)" },
        { value: "openai/gpt-4o",                            label: "gpt-4o  🌐 web search via plugin (paid)" },
        { value: "openai/gpt-4o-mini",                       label: "gpt-4o-mini  🌐 web search via plugin (paid)" },
        { value: "openai/gpt-4.1",                           label: "gpt-4.1  🌐 web search via plugin (paid)" },
        { value: "anthropic/claude-sonnet-4-5",              label: "claude-sonnet-4-5  🌐 web search via plugin (paid)" },
        { value: "meta-llama/llama-3.1-8b-instruct",        label: "llama-3.1-8b-instruct  💰 ~$0.05/1M tokens" },
        { value: "openai/gpt-oss-20b:free",                  label: "gpt-oss-20b:free  ✦ free — small model, weak analysis" },
        { value: "google/gemma-4-31b-it:free",               label: "gemma-4-31b-it:free  ✦ free — no VRA specialisation" },
      ]
    },
  };

  function onProviderChange(provider, currentModel) {
    var cfg = PROVIDER_MODELS[provider] || { hint: "", models: [] };
    var hintEl = document.getElementById("provider-hint");
    if (hintEl) hintEl.textContent = cfg.hint;
    var sel = document.getElementById("llm_model");
    if (!sel) return;
    sel.innerHTML = "";
    cfg.models.forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m.value;
      opt.textContent = m.label;
      sel.appendChild(opt);
    });
    // Try to restore previous selection; fall back to first option
    if (currentModel && cfg.models.some(function(m){ return m.value === currentModel; })) {
      sel.value = currentModel;
    } else if (cfg.models.length > 0) {
      sel.value = cfg.models[0].value;
    }
  }

  // Wire up provider change event
  var providerSel = document.getElementById("llm_provider");
  if (providerSel) {
    providerSel.addEventListener("change", function () {
      onProviderChange(this.value);
    });
  }

  // ── Toast ────────────────────────────────────────────────────────────────
  function showToast(message, ok) {
    var el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.style.background = ok ? "#002970" : "#b91c1c";
    el.classList.remove("hidden");
    setTimeout(function () { el.classList.add("hidden"); }, 6000);
  }

  // ── Key rows ─────────────────────────────────────────────────────────────
  var keysContainer = document.getElementById("keys-container");
  var fallbackCount = 0;

  function renderKeyRow(k) {
    var wrap = document.createElement("div");
    wrap.className = "border border-slate-100 rounded-lg p-4 space-y-2";
    wrap.dataset.keyId = k.id != null ? String(k.id) : "";
    var label = document.createElement("div");
    label.className = "text-xs font-semibold text-slate-500 uppercase";
    label.textContent = k.label || "Key";
    var row = document.createElement("div");
    row.className = "flex flex-wrap gap-2 items-center";
    var masked = document.createElement("code");
    masked.className = "flex-1 min-w-[200px] bg-slate-50 px-2 py-1 rounded text-sm";
    masked.textContent = k.masked_key || "••••";
    var replaceBtn = document.createElement("button");
    replaceBtn.type = "button";
    replaceBtn.className = "text-sm text-paytm-blue font-medium";
    replaceBtn.textContent = "Replace";
    var input = document.createElement("input");
    input.type = "password";
    input.placeholder = "Paste new API key";
    input.className = "hidden w-full rounded border border-slate-200 px-2 py-1 text-sm";
    var saveRowBtn = document.createElement("button");
    saveRowBtn.type = "button";
    saveRowBtn.className = "hidden text-sm px-2 py-1 rounded bg-paytm-dark text-white";
    saveRowBtn.textContent = "Apply key";
    var meta = document.createElement("p");
    meta.className = "text-xs text-slate-600";
    var warn = k.quota_warning ? " ⚠️ over 80% of daily quota" : "";
    meta.textContent =
      (k.is_active ? "Active" : "Inactive") +
      " · Usage today: " + k.usage_today + "/" + k.daily_limit + warn;
    replaceBtn.addEventListener("click", function () {
      input.classList.toggle("hidden");
      saveRowBtn.classList.toggle("hidden");
    });
    saveRowBtn.addEventListener("click", function () {
      if (!input.value) { showToast("Enter a key", false); return; }
      k._pendingSecret = input.value;
      masked.textContent = "••••" + input.value.slice(-4);
      input.classList.add("hidden");
      saveRowBtn.classList.add("hidden");
      input.value = "";
      showToast("Key staged — click Save Settings to persist", true);
    });
    row.appendChild(masked);
    row.appendChild(replaceBtn);
    wrap.appendChild(label);
    wrap.appendChild(row);
    wrap.appendChild(input);
    wrap.appendChild(saveRowBtn);
    wrap.appendChild(meta);
    wrap._keyMeta = k;
    return wrap;
  }

  function collectKeysPayload() {
    var rows = keysContainer.querySelectorAll("[data-key-id]");
    var keys = [];
    rows.forEach(function (row) {
      var meta = row._keyMeta;
      var rawId = row.dataset.keyId;
      var id = rawId === "" || rawId === undefined ? null : parseInt(rawId, 10);
      if (meta && meta._pendingSecret) {
        keys.push({ id: id, label: meta.label, key: meta._pendingSecret });
        delete meta._pendingSecret;
      }
    });
    return keys;
  }

  // ── Load state from server ────────────────────────────────────────────────
  async function load() {
    var res = await fetch("/api/settings");
    var s = await res.json();

    var provider = s.llm_provider || "gemini";
    if (providerSel) providerSel.value = provider;

    // Populate model dropdown for current provider, then set saved model
    onProviderChange(provider, s.llm_model);

    // Update active provider banner
    var bannerText = document.getElementById("active-provider-text");
    var keysLabel = document.getElementById("keys-provider-label");
    var providerDisplay = provider === "openrouter" ? "OpenRouter" : provider === "gemini" ? "Gemini" : provider;
    var modelDisplay = s.llm_model || "";
    if (bannerText) {
      bannerText.innerHTML = "Active provider: <strong>" + providerDisplay + "</strong> &nbsp;|&nbsp; Model: <strong>" + modelDisplay + "</strong> &nbsp;|&nbsp; Hybrid mode: <strong>ON</strong> (DuckDuckGo fetches data, LLM analyses)";
    }
    if (keysLabel) {
      keysLabel.textContent = "(" + providerDisplay + ")";
    }

    document.getElementById("temperature").value = s.temperature;
    document.getElementById("max_output_tokens").value = s.max_output_tokens;
    document.getElementById("daily_quota_limit").value = s.daily_quota_limit;

    if (!s.fernet_configured) {
      document.getElementById("fernet-warn").classList.remove("hidden");
    }

    keysContainer.innerHTML = "";
    if (!(s.keys || []).length) {
      var hint = document.createElement("p");
      hint.className = "text-sm text-slate-600 mb-2";
      hint.textContent = "No API keys stored yet. Add a Primary key (encrypted with FERNET_KEY).";
      keysContainer.appendChild(hint);
    }
    (s.keys || []).forEach(function (k) {
      keysContainer.appendChild(renderKeyRow(k));
    });

    // Serper status badge
    var serperEl = document.getElementById("serper-status");
    if (serperEl) {
      if (s.serper_configured) {
        serperEl.textContent = "✅ Configured — Google Search active";
        serperEl.className = "text-xs px-2 py-1 rounded-full font-medium bg-green-100 text-green-800";
      } else {
        serperEl.textContent = "⚠️ Not set — using DuckDuckGo (unreliable)";
        serperEl.className = "text-xs px-2 py-1 rounded-full font-medium bg-amber-100 text-amber-800";
      }
    }

    if (document.getElementById("log-test")) {
      document.getElementById("log-test").textContent =
        "Last test: " + (s.last_test_at || "—") +
        (s.last_test_ok === true ? " ✅" : s.last_test_ok === false ? " ❌" : "");
    }
    var ltm = document.getElementById("log-test-msg");
    if (ltm) ltm.textContent = s.last_test_message || "";
    if (document.getElementById("log-gen")) {
      document.getElementById("log-gen").textContent =
        "Last successful generation: " + (s.last_generation_at || "—");
    }
  }

  // ── Add key buttons ───────────────────────────────────────────────────────
  document.getElementById("add-primary").addEventListener("click", function () {
    var wrap = renderKeyRow({
      id: null, label: "Primary", masked_key: "(new)", is_active: true,
      usage_today: 0,
      daily_limit: parseInt(document.getElementById("daily_quota_limit").value, 10) || 1500,
      quota_warning: false,
    });
    wrap.dataset.keyId = "";
    keysContainer.appendChild(wrap);
  });

  document.getElementById("add-fallback").addEventListener("click", function () {
    fallbackCount += 1;
    var label = fallbackCount === 1 ? "Fallback" : "Fallback " + fallbackCount;
    var wrap = renderKeyRow({
      id: null, label: label, masked_key: "(new)", is_active: true,
      usage_today: 0,
      daily_limit: parseInt(document.getElementById("daily_quota_limit").value, 10) || 1500,
      quota_warning: false,
    });
    wrap.dataset.keyId = "";
    keysContainer.appendChild(wrap);
  });

  // ── Save ──────────────────────────────────────────────────────────────────
  document.getElementById("btn-save").addEventListener("click", async function () {
    var staged = collectKeysPayload();
    var newRows = keysContainer.querySelectorAll('[data-key-id=""]');
    newRows.forEach(function (row) {
      var meta = row._keyMeta;
      var inp = row.querySelector('input[type="password"]');
      if (meta && meta.id == null && inp && inp.value) {
        staged.push({ id: null, label: meta.label, key: inp.value });
        inp.value = "";
      }
    });
    var body = {
      llm_provider:      document.getElementById("llm_provider").value,
      llm_model:         document.getElementById("llm_model").value,
      temperature:       parseFloat(document.getElementById("temperature").value),
      max_output_tokens: parseInt(document.getElementById("max_output_tokens").value, 10),
      daily_quota_limit: parseInt(document.getElementById("daily_quota_limit").value, 10),
      keys: staged,
    };
    var res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      var err = await res.json().catch(function () { return {}; });
      showToast(err.detail || "Save failed", false);
      return;
    }
    showToast("Settings saved", true);
    load();
  });

  // ── Test ──────────────────────────────────────────────────────────────────
  document.getElementById("btn-test").addEventListener("click", async function () {
    var el = document.getElementById("test-result");
    el.textContent = "…";
    var res = await fetch("/api/settings/test", { method: "POST" });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) {
      el.textContent = "❌";
      showToast(data.detail || "Test failed", false);
      return;
    }
    el.textContent = data.ok ? "✅" : "❌";
    if (data.message) {
      showToast(data.message, data.ok);
    } else if (!data.ok) {
      showToast("Test failed — check API key and model", false);
    }
    load();
  });

  // ── Serper key save ────────────────────────────────────────────────────────
  var serperSaveBtn = document.getElementById("btn-save-serper");
  if (serperSaveBtn) {
    serperSaveBtn.addEventListener("click", async function () {
      var inp = document.getElementById("serper_api_key");
      if (!inp || !inp.value.trim()) { showToast("Paste a Serper API key first", false); return; }
      var body = {
        llm_provider: document.getElementById("llm_provider").value,
        llm_model: document.getElementById("llm_model").value,
        temperature: parseFloat(document.getElementById("temperature").value),
        max_output_tokens: parseInt(document.getElementById("max_output_tokens").value, 10),
        daily_quota_limit: parseInt(document.getElementById("daily_quota_limit").value, 10),
        keys: [],
        serper_api_key: inp.value.trim(),
      };
      var res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        var err = await res.json().catch(function () { return {}; });
        showToast(err.detail || "Save failed", false);
        return;
      }
      inp.value = "";
      showToast("Serper key saved — Google Search now active ✅", true);
      load();
    });
  }

  load();
})();
