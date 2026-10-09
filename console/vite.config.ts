import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.DEFLECT_API_URL ?? "http://localhost:8000";
const routes = ["/tickets", "/approvals", "/metrics", "/config", "/demo", "/health"];

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: Object.fromEntries(routes.map((route) => [route, { target: api, changeOrigin: true }])),
  },
  test: { environment: "node" },
});
