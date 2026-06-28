import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard is read-only and talks to the AxonRelay REST API. In dev we
// proxy /api to the local backend; in production the reverse proxy serves both.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
