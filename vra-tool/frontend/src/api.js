// Centralized API base URL.
// In dev (Vite), VITE_API_BASE_URL is unset → relative paths resolved via the
// dev proxy in vite.config.js. In production (Netlify build), set
// VITE_API_BASE_URL=https://<your-backend>.onrender.com so all calls go
// cross-origin to the Render-hosted FastAPI backend.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

// localStorage key for the user's Gemini API key. Per-browser, per-user.
// Never sent to the backend except as an X-Gemini-Api-Key request header on
// outgoing API calls — the backend uses it for the one LLM call and discards
// it. Two users on two browsers each have their own key, fully isolated.
export const GEMINI_KEY_STORAGE = "vra_gemini_api_key";

export function getStoredGeminiKey() {
  try {
    return (localStorage.getItem(GEMINI_KEY_STORAGE) || "").trim();
  } catch {
    // localStorage can throw in private-browsing / sandboxed iframes.
    return "";
  }
}

export function setStoredGeminiKey(key) {
  try {
    const v = (key || "").trim();
    if (v) localStorage.setItem(GEMINI_KEY_STORAGE, v);
    else localStorage.removeItem(GEMINI_KEY_STORAGE);
  } catch {
    /* ignore localStorage write failures */
  }
}

export function clearStoredGeminiKey() {
  try {
    localStorage.removeItem(GEMINI_KEY_STORAGE);
  } catch {
    /* ignore */
  }
}

// ── LLM model selection (per-browser, like the key) ─────────────────────────
// The chosen model lives only in this browser's localStorage and is sent as the
// X-Llm-Model request header. The backend honors it only if it's an allowlisted
// model (see GEMINI_MODEL_CHOICES in app/core/vra_service.py); otherwise it
// falls back to the server default. Keep these values in sync with that list.
export const MODEL_STORAGE = "vra_llm_model";

export const DEFAULT_MODEL = "gemini-2.5-flash";

export const MODEL_OPTIONS = [
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash — best quality (default)" },
  { value: "gemini-2.0-flash", label: "Gemini 2.0 Flash — faster, lower cost" },
  { value: "gemini-2.0-flash-001", label: "Gemini 2.0 Flash 001 — pinned build" },
];

export function getStoredModel() {
  try {
    return (localStorage.getItem(MODEL_STORAGE) || "").trim() || DEFAULT_MODEL;
  } catch {
    return DEFAULT_MODEL;
  }
}

export function setStoredModel(model) {
  try {
    const v = (model || "").trim();
    // Only persist a non-default choice; default → clear so no header is sent.
    if (v && v !== DEFAULT_MODEL) localStorage.setItem(MODEL_STORAGE, v);
    else localStorage.removeItem(MODEL_STORAGE);
  } catch {
    /* ignore localStorage write failures */
  }
}

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

// apiFetch automatically attaches the X-Gemini-Api-Key and X-Llm-Model headers
// on every request from this browser's localStorage. Override either by passing
// an init.headers object that explicitly sets it (e.g. {"X-Gemini-Api-Key": ""}).
export function apiFetch(path, init) {
  const opts = init ? { ...init } : {};
  const headers = new Headers(opts.headers || {});
  if (!headers.has("X-Gemini-Api-Key")) {
    const key = getStoredGeminiKey();
    if (key) headers.set("X-Gemini-Api-Key", key);
  }
  if (!headers.has("X-Llm-Model")) {
    const model = getStoredModel();
    if (model) headers.set("X-Llm-Model", model);
  }
  opts.headers = headers;
  return fetch(apiUrl(path), opts);
}
