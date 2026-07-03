import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Ariadne frontend dev server. Proxies /api and /health to the FastAPI backend so the
// app works with same-origin fetches in dev and in a built deployment behind one host.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/config": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
