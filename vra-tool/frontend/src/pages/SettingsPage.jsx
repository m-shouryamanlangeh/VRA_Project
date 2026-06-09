import { useEffect, useState } from "react";
import { useToast } from "../ToastContext.jsx";
import {
  apiFetch,
  clearStoredGeminiKey,
  getStoredGeminiKey,
  setStoredGeminiKey,
} from "../api.js";

// Browser-local key UI — replaces the server-stored key flow for multi-tenant
// use. Each user pastes their own Gemini key; it lives only in their browser's
// localStorage and is sent as X-Gemini-Api-Key on every API call.
function BrowserKeyCard() {
  const { showToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [stored, setStored] = useState(getStoredGeminiKey());
  const [testing, setTesting] = useState(false);

  const masked = stored
    ? "•".repeat(Math.max(0, stored.length - 4)) + stored.slice(-4)
    : "";

  function save() {
    const v = draft.trim();
    if (!v) {
      showToast("Paste a Gemini API key first.", false);
      return;
    }
    setStoredGeminiKey(v);
    setStored(v);
    setDraft("");
    setEditing(false);
    showToast("Saved to this browser. Other users / browsers are unaffected.", true);
  }

  function clear() {
    if (!window.confirm("Remove the Gemini API key from this browser?")) return;
    clearStoredGeminiKey();
    setStored("");
    showToast("Key removed from this browser.", true);
  }

  async function testKey() {
    setTesting(true);
    try {
      const res = await apiFetch("/api/settings/test", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Test failed");
      if (data.ok) {
        showToast(data.message || "OK — key works.", true);
      } else {
        showToast(data.message || "Key rejected by Gemini.", false);
      }
    } catch (err) {
      showToast(err.message || String(err), false);
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="border border-paytm-blue/20 bg-paytm-blue/5 rounded-lg p-5 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-paytm-dark">Your Gemini API Key</h2>
        <span className="text-xs text-slate-500 uppercase">Browser-only</span>
      </div>
      <p className="text-sm text-slate-600">
        Paste your personal Gemini key. It's stored only in this browser's
        localStorage and sent with each generation request. Nothing is saved on
        the server — other users have their own keys, fully isolated.
      </p>
      <p className="text-xs text-slate-500">
        Get a free key at{" "}
        <a
          href="https://aistudio.google.com/apikey"
          target="_blank"
          rel="noreferrer"
          className="text-paytm-blue underline"
        >
          aistudio.google.com/apikey
        </a>
        .
      </p>

      {stored && !editing ? (
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <code className="flex-1 min-w-[220px] bg-white border border-slate-200 px-3 py-2 rounded text-sm">
            {masked}
          </code>
          <button
            type="button"
            onClick={() => {
              setEditing(true);
              setDraft("");
            }}
            className="text-sm font-medium text-paytm-blue hover:underline"
          >
            Replace
          </button>
          <button
            type="button"
            onClick={testKey}
            disabled={testing}
            className="text-sm font-medium px-3 py-2 rounded bg-paytm-dark text-white disabled:opacity-50"
          >
            {testing ? "Testing…" : "Test Key"}
          </button>
          <button
            type="button"
            onClick={clear}
            className="text-sm text-red-600 hover:text-red-700 font-medium"
          >
            Remove
          </button>
        </div>
      ) : (
        <div className="space-y-2 pt-1">
          <input
            type="password"
            placeholder="Paste your Gemini API key (starts with AIzaSy…)"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm font-mono"
            autoFocus
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!draft.trim()}
              className="text-sm font-medium px-3 py-2 rounded bg-paytm-blue text-white disabled:opacity-50"
            >
              Save to this browser
            </button>
            {stored && (
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setDraft("");
                }}
                className="text-sm text-slate-600 hover:text-slate-800"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

const DEFAULT_STATE = {
  llm_provider: "gemini",
  llm_model: "gemini-2.0-flash",
  temperature: 0.2,
  max_output_tokens: 16384,
  daily_quota_limit: 1500,
  keys: [],
  last_test_at: null,
  last_test_ok: null,
  last_test_message: "",
  last_generation_at: null,
  fernet_configured: true,
};

const MODEL_OPTIONS = [
  { value: "gemini-2.0-flash", label: "gemini-2.0-flash (default)" },
  { value: "gemini-2.0-flash-001", label: "gemini-2.0-flash-001" },
  { value: "gemini-2.5-flash", label: "gemini-2.5-flash" },
];

function KeyRow({ k, onStage, onUnstage, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const masked = k.pendingSuffix
    ? "••••" + k.pendingSuffix
    : k.masked_key || "••••";

  const meta = `${k.is_active ? "Active" : "Inactive"} · Usage today: ${k.usage_today ?? 0}/${k.daily_limit ?? 0}${k.quota_warning ? " ⚠️ over 80% of daily quota" : ""}`;

  const rowClass =
    "border rounded-lg p-4 space-y-2 " +
    (k.is_active
      ? "border-slate-100"
      : "border-amber-200 bg-amber-50/40");

  return (
    <div className={rowClass}>
      <div className="flex items-center justify-between">
        <div className="text-xs font-semibold text-slate-500 uppercase">
          {k.label || "Key"}
          {!k.is_active && (
            <span className="ml-2 text-amber-700 normal-case font-medium">
              auto-deactivated — reset to retry
            </span>
          )}
        </div>
        {k.id !== null && (
          <button
            type="button"
            onClick={() => onDelete(k)}
            title="Permanently delete this key"
            className="text-sm text-red-600 hover:text-red-700 font-medium"
          >
            🗑 Delete
          </button>
        )}
      </div>
      <div className="flex flex-wrap gap-2 items-center">
        <code className="flex-1 min-w-[200px] bg-slate-50 px-2 py-1 rounded text-sm">
          {masked}
        </code>
        <button
          type="button"
          className="text-sm text-paytm-blue font-medium"
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? "Cancel" : "Replace"}
        </button>
      </div>
      {editing && (
        <div className="space-y-2">
          <input
            type="password"
            placeholder="Paste new API key"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border border-slate-200 px-2 py-1 text-sm"
          />
          <button
            type="button"
            className="text-sm px-2 py-1 rounded bg-paytm-dark text-white"
            onClick={() => {
              if (!draft) return;
              onStage(k.uiId, draft);
              setEditing(false);
              setDraft("");
            }}
          >
            Apply key
          </button>
          {k.pendingSuffix && (
            <button
              type="button"
              className="text-sm px-2 py-1 rounded border border-slate-200 ml-2"
              onClick={() => onUnstage(k.uiId)}
            >
              Discard staged change
            </button>
          )}
        </div>
      )}
      <p className="text-xs text-slate-600">{meta}</p>
    </div>
  );
}

export default function SettingsPage() {
  const { showToast } = useToast();
  const [state, setState] = useState(DEFAULT_STATE);
  const [keys, setKeys] = useState([]);
  const [testResult, setTestResult] = useState("");
  const [fallbackCount, setFallbackCount] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  let nextUiId = 1;

  const buildKey = (k, idx) => ({
    uiId: idx + ":" + (k.id ?? "new"),
    id: k.id ?? null,
    label: k.label || "Key",
    masked_key: k.masked_key || "••••",
    is_active: k.is_active,
    usage_today: k.usage_today,
    daily_limit: k.daily_limit,
    quota_warning: k.quota_warning,
    pendingSecret: null,
    pendingSuffix: null,
  });

  async function load() {
    try {
      const res = await apiFetch("/api/settings");
      const s = await res.json();
      setState(s);
      setKeys((s.keys || []).map(buildKey));
    } catch (err) {
      showToast("Could not load settings: " + (err.message || err), false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function update(field, value) {
    setState((s) => ({ ...s, [field]: value }));
  }

  function stageSecret(uiId, secret) {
    setKeys((rows) =>
      rows.map((r) =>
        r.uiId === uiId
          ? { ...r, pendingSecret: secret, pendingSuffix: secret.slice(-4) }
          : r,
      ),
    );
    showToast("Key staged — click Save Settings to persist", true);
  }

  function unstageSecret(uiId) {
    setKeys((rows) =>
      rows.map((r) =>
        r.uiId === uiId ? { ...r, pendingSecret: null, pendingSuffix: null } : r,
      ),
    );
  }

  function addKey(label) {
    setKeys((rows) => [
      ...rows,
      {
        uiId: "new:" + Date.now() + ":" + (nextUiId++),
        id: null,
        label,
        masked_key: "(new)",
        is_active: true,
        usage_today: 0,
        daily_limit: state.daily_quota_limit || 1500,
        quota_warning: false,
        pendingSecret: null,
        pendingSuffix: null,
      },
    ]);
  }

  async function save() {
    const staged = keys
      .filter((k) => k.pendingSecret)
      .map((k) => ({ id: k.id, label: k.label, key: k.pendingSecret }));

    const body = {
      llm_provider: state.llm_provider,
      llm_model: state.llm_model,
      temperature: parseFloat(state.temperature),
      max_output_tokens: parseInt(state.max_output_tokens, 10),
      daily_quota_limit: parseInt(state.daily_quota_limit, 10),
      keys: staged,
    };
    try {
      const res = await apiFetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Save failed");
      showToast("Keys saved", true);
      load();
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  }

  async function deleteKey(k) {
    if (k.id == null) {
      // Unsaved row — just drop it from local state.
      setKeys((rows) => rows.filter((r) => r.uiId !== k.uiId));
      return;
    }
    if (!window.confirm(`Permanently delete the "${k.label}" key? This cannot be undone.`)) {
      return;
    }
    try {
      const res = await apiFetch(`/api/settings/keys/${k.id}`, { method: "DELETE" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Delete failed");
      showToast(`Deleted "${k.label}"`, true);
      load();
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  }

  async function resetKeys() {
    try {
      const res = await apiFetch("/api/settings/keys/reset", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Reset failed");
      if (data.count === 0) {
        showToast("No inactive keys to reset", true);
      } else {
        showToast(`Reactivated ${data.count} key(s): ${(data.reactivated || []).join(", ")}`, true);
      }
      load();
    } catch (err) {
      showToast(err.message || String(err), false);
    }
  }

  async function test() {
    setTestResult("…");
    try {
      const res = await apiFetch("/api/settings/test", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setTestResult("❌");
        showToast(data.detail || "Test failed", false);
        return;
      }
      setTestResult(data.ok ? "✅" : "❌");
      if (data.message) {
        showToast(data.message, !!data.ok);
      } else if (!data.ok) {
        showToast("Test failed — check API key and model", false);
      }
      load();
    } catch (err) {
      setTestResult("❌");
      showToast(err.message || String(err), false);
    }
  }

  const lastTestLine =
    "Last test: " +
    (state.last_test_at || "—") +
    (state.last_test_ok === true
      ? " ✅"
      : state.last_test_ok === false
        ? " ❌"
        : "");

  return (
    <main className="max-w-3xl mx-auto px-6 py-10">
      <h1 className="text-2xl font-bold text-paytm-dark mb-2">⚙️ Settings</h1>
      <p className="text-slate-600 text-sm mb-6">
        Bring your own <strong>Gemini</strong> API key. It's stored only in
        this browser — your usage and quota are isolated from anyone else
        using the portal.
      </p>

      {/* Browser-local key — the new multi-tenant flow */}
      <div className="mb-8">
        <BrowserKeyCard />
      </div>

      <details className="mb-6">
        <summary className="cursor-pointer text-sm font-medium text-slate-600 hover:text-paytm-blue">
          Advanced: server-stored keys (self-hosted deployments only)
        </summary>

        {!state.fernet_configured && (
          <div className="mt-3 p-4 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-sm">
            FERNET_KEY is missing or invalid. Set it in{" "}
            <code className="bg-white px-1 rounded">.env</code> (or Netlify
            environment variables) before saving API keys.
          </div>
        )}

      <div className="bg-white rounded-xl shadow border border-slate-100 p-6 space-y-6 mt-3">
        <div>
          <h2 className="text-sm font-semibold text-paytm-dark mb-3">API keys</h2>
          <div className="space-y-4">
            {keys.length === 0 && (
              <p className="text-sm text-slate-600 mb-2">
                No API keys stored yet. Add a Primary key (encrypted with
                FERNET_KEY).
              </p>
            )}
            {keys.map((k) => (
              <KeyRow
                key={k.uiId}
                k={k}
                onStage={stageSecret}
                onUnstage={unstageSecret}
                onDelete={deleteKey}
              />
            ))}
          </div>
          <div className="flex flex-wrap gap-4 mt-3">
            <button
              type="button"
              className="text-sm text-paytm-blue font-medium hover:underline"
              onClick={() => addKey("Primary")}
            >
              + Add Primary Key
            </button>
            <button
              type="button"
              className="text-sm text-paytm-blue font-medium hover:underline"
              onClick={() => {
                const next = fallbackCount + 1;
                setFallbackCount(next);
                addKey(next === 1 ? "Fallback" : "Fallback " + next);
              }}
            >
              + Add Fallback Key
            </button>
          </div>
        </div>

        <div className="flex flex-wrap gap-3 items-center">
          <button
            type="button"
            onClick={test}
            className="px-4 py-2 rounded-lg border border-slate-200 font-medium hover:bg-slate-50"
          >
            Test Connection
          </button>
          <button
            type="button"
            onClick={save}
            className="px-4 py-2 rounded-lg bg-paytm-blue text-white font-medium hover:opacity-90"
          >
            Save keys
          </button>
          <span className="text-sm text-slate-600" title="Latest test result">
            {testResult}
          </span>
        </div>

        <div className="border border-slate-100 rounded-lg">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            className="w-full text-left cursor-pointer px-4 py-3 text-sm font-medium text-paytm-blue hover:bg-slate-50 rounded-lg flex items-center justify-between"
          >
            <span>Advanced defaults (optional)</span>
            <span
              className="text-slate-400 transition-transform"
              style={{
                transform: advancedOpen ? "rotate(180deg)" : "rotate(0deg)",
              }}
            >
              ▼
            </span>
          </button>
          {advancedOpen && (
            <div className="px-4 pb-4 pt-0 border-t border-slate-100">
              <p className="text-xs text-slate-500 mt-3 mb-4">
                Only change these if you need a different model or quota display.
                Otherwise leave as loaded from the server.
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium mb-1">
                    LLM Provider
                  </label>
                  <select
                    value={state.llm_provider}
                    onChange={(e) => update("llm_provider", e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 bg-white"
                  >
                    <option value="gemini">Gemini</option>
                    <option value="openai" disabled>
                      OpenAI (soon)
                    </option>
                    <option value="anthropic" disabled>
                      Claude (soon)
                    </option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">Model</label>
                  <select
                    value={state.llm_model}
                    onChange={(e) => update("llm_model", e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2 bg-white"
                  >
                    {MODEL_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-500 mt-1">
                    Use 2.x Flash here. Gemini 3 preview /{" "}
                    <code className="bg-slate-100 px-0.5 rounded">
                      gemini-live-*
                    </code>{" "}
                    models need the Interactions API and will not work with this
                    app's generate path.
                  </p>
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">
                    Temperature
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0"
                    max="2"
                    value={state.temperature}
                    onChange={(e) => update("temperature", e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium mb-1">
                    Max output tokens
                  </label>
                  <input
                    type="number"
                    min="256"
                    value={state.max_output_tokens}
                    onChange={(e) => update("max_output_tokens", e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2"
                  />
                </div>
                <div className="md:col-span-2">
                  <label className="block text-sm font-medium mb-1">
                    Daily quota limit (display / 80% warning)
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={state.daily_quota_limit}
                    onChange={(e) => update("daily_quota_limit", e.target.value)}
                    className="w-full rounded-lg border border-slate-200 px-3 py-2"
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        <div className="border border-slate-100 rounded-lg p-4 bg-slate-50 text-sm space-y-1">
          <p className="font-medium text-paytm-dark">Status log</p>
          <p>{lastTestLine}</p>
          <p className="text-xs text-slate-600 break-words">
            {state.last_test_message || ""}
          </p>
          <p>Last successful generation: {state.last_generation_at || "—"}</p>
        </div>
      </div>
      </details>
    </main>
  );
}
