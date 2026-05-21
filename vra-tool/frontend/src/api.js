// Centralized API base URL.
// In dev (Vite), VITE_API_BASE_URL is unset → relative paths resolved via the
// dev proxy in vite.config.js. In production (Netlify build), set
// VITE_API_BASE_URL=https://<your-backend>.onrender.com so all calls go
// cross-origin to the Render-hosted FastAPI backend.
const BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

export function apiFetch(path, init) {
  return fetch(apiUrl(path), init);
}
