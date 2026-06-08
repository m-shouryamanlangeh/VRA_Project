import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the React app runs on :5173 and proxies API calls to FastAPI on :8000.
// In production (Netlify), Netlify redirects /generate, /download/*, /api/* etc. to the
// /.netlify/functions/api Lambda — see ../netlify.toml.

const API_PATHS = [
  "/generate",
  "/download",
  "/api",
  "/settings/test",
  "/health",
];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: API_PATHS.reduce((acc, p) => {
      acc[p] = {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      };
      return acc;
    }, {}),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
