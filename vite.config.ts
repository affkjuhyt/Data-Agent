import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  return {
    plugins: [react(), tailwindcss()],
    define: {
      "process.env.GEMINI_API_KEY": JSON.stringify(env.GEMINI_API_KEY),
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "."),
      },
    },
    server: {
      hmr: process.env.DISABLE_HMR !== "true",
      watch: process.env.DISABLE_HMR === "true" ? null : {},
      // Local dev gateway: there is no Kong in front of the agent tier, so the dev
      // server plays that role — it routes each /api path to the owning service and
      // injects the identity headers those services expect (Kong would add these
      // from the JWT). Tenant = the seeded Dev Tenant (see scripts/seed_dev_agent.sql).
      proxy: (() => {
        const identity = {
          "X-Tenant-Id": "a0000000-0000-0000-0000-000000000001",
          "X-User-ID": "11111111-1111-1111-1111-111111111111",
          "X-User-Roles": "platform-admin,tenant-admin,engineer",
        };
        const orch = { target: "http://localhost:8003", changeOrigin: true, headers: identity };
        const rt   = { target: "http://localhost:8001", changeOrigin: true, headers: identity };
        return {
          "/api/orchestrate":    orch,   // orchestrator — the agent turn (SSE)
          "/api/catalog":        rt,     // workflow-runtime — agent list
          "/api/llm-providers":  rt,
          "/api/runtime-config": rt,
          "/api":                { target: "http://localhost:8000", changeOrigin: true },
        };
      })(),
    },
  };
});
