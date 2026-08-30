import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig, loadEnv } from "vite";

// VITE_* is documented in the repo-root .env.example; the workspace runs vite from frontend/.
const ENV_DIR = path.resolve(__dirname, "..");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ENV_DIR, "");
  return {
    envDir: ENV_DIR,
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
      // Everything goes through Kong (VITE_API_URL, default :8000), which
      // verifies the Keycloak JWT and injects the identity headers itself.
      //
      // This used to be the dev substitute for Kong: it proxied /api/orchestrate
      // to :8003 and the runtime paths to :8001 while injecting a hardcoded
      // `platform-admin,tenant-admin,engineer` identity. Those ports are closed
      // now and both services verify their caller, so the forgery would fail —
      // and it should: a dev server silently granting admin is exactly the
      // bypass the gateway exists to prevent. Kong already has routes for both
      // (deploy/docker/kong/kong-setup.sh: runtime-route, orchestrator-route).
      proxy: (() => {
        const gateway = { target: env.VITE_API_URL || "http://localhost:8000", changeOrigin: true };
        return {
          // auth-api validates the real Bearer token itself, but it also sits
          // behind Kong — one gateway, one identity path, no exceptions.
          "/api": gateway,
        };
      })(),
    },
  };
});
