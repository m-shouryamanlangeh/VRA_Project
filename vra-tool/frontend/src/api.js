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

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

// apiFetch automatically attaches the X-Gemini-Api-Key header on every
// request when a key is stored in localStorage. Override by passing an
// init.headers object that explicitly omits the header (e.g. {"X-Gemini-Api-Key": ""}).
export function apiFetch(path, init) {
  const opts = init ? { ...init } : {};
  const headers = new Headers(opts.headers || {});
  if (!headers.has("X-Gemini-Api-Key")) {
    const key = getStoredGeminiKey();
    if (key) headers.set("X-Gemini-Api-Key", key);
  }
  opts.headers = headers;
  return fetch(apiUrl(path), opts);
}
