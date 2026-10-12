import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import fs from "fs";
import path from "path";
import { defineConfig, loadEnv } from "vite";

// VITE_* is documented in the repo-root .env.example; the workspace runs vite from frontend/.
const ENV_DIR = path.resolve(__dirname, "..");

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ENV_DIR, "");
  return {
    envDir: ENV_DIR,
    plugins: [
      react(),
      tailwindcss(),
      {
        // public/mockServiceWorker.js serves the dev-only v1 mock (src/mocks, VITE_MOCK_V1=1);
        // production must not ship it.
        name: "drop-mock-service-worker",
        apply: "build",
        closeBundle() {
          fs.rmSync(path.resolve(__dirname, "dist/mockServiceWorker.js"), { force: true });
        },
      },
    ],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "."),
      },
    },
    preview: {
      // vite preview validates the incoming Host header by default (DNS
      // rebinding protection) — this is reached via the k8s NodePort
      // (<node-ip>:30280) or through Kong, neither of which is "localhost",
      // so the default would 403 every real request. Trusted internal
      // network, not a public-facing preview server.
      allowedHosts: true,
    },
    server: {
      // Behind a NodePort/reverse proxy, the browser's page origin (e.g.
      // :30280) differs from the port this dev server actually listens on
      // inside its container (:8280) — the HMR client otherwise tries to
      // open a websocket back to the container-internal port, which the
      // browser can't reach at all. VITE_HMR_CLIENT_PORT tells it which
      // externally-reachable port to use instead (deploy/k8s/production/service/
      // frontend.yaml sets it to the NodePort, 30280).
      hmr: process.env.DISABLE_HMR !== "true"
        ? { clientPort: Number(env.VITE_HMR_CLIENT_PORT) || undefined }
        : false,
      watch: process.env.DISABLE_HMR === "true" ? null : {},
      // Everything goes through Kong (VITE_API_URL, default :30100), which
      // verifies the Keycloak JWT and injects the identity headers itself.
      //
      // This used to be the dev substitute for Kong: it proxied /api/orchestrate
      // to :8003 and the runtime paths to :8240 while injecting a hardcoded
      // `platform-admin,tenant-admin,engineer` identity. Those ports are closed
      // now and both services verify their caller, so the forgery would fail —
      // and it should: a dev server silently granting admin is exactly the
      // bypass the gateway exists to prevent. Kong already has routes for both
      // (deploy/docker/kong/kong-setup.sh: runtime-route, orchestrator-route).
      proxy: (() => {
        const gateway = { target: env.VITE_API_URL || "http://localhost:30100", changeOrigin: true };
        return {
          // auth_api validates the real Bearer token itself, but it also sits
          // behind Kong — one gateway, one identity path, no exceptions.
          "/api": gateway,
        };
      })(),
    },
  };
});
