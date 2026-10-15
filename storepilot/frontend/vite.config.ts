import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev mode: `npm run dev` on :5173, API and WebSocket go to the backend
// (STOREPILOT_BACKEND, default the installed service on :8080; ../dev.sh uses :8081).
const backend = process.env.STOREPILOT_BACKEND ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": backend,
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 800 },
});
