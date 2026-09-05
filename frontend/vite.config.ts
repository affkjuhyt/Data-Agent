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
    optimizeDeps: {
      // Without this, Vite only discovers `motion`/`framer-motion` the first
      // time a component using them renders, after the initial page load
      // has already pre-bundled react/react-dom. That late discovery
      // triggers a second, separate optimize pass that re-bundles react
      // under a new dep hash, so the page ends up with two different react
      // module instances and hooks break with "Cannot read properties of
      // null (reading 'useState'/'useContext')". Listing them here forces
      // both into the same cold-start scan as react itself.
      include: ["motion", "motion/react", "framer-motion"],
    },
    server: {
      // Behind a NodePort/reverse proxy, the browser's page origin (e.g.
      // :30280) differs from the port this dev server actually listens on
      // inside its container (:8280) — the HMR client otherwise tries to
      // open a websocket back to the container-internal port, which the
      // browser can't reach at all. VITE_HMR_CLIENT_PORT tells it which
      // externally-reachable port to use instead (deploy/k8s/service/
      // frontend.yaml sets it to the NodePort, 30280).
      hmr: process.env.DISABLE_HMR !== "true"
        ? { clientPort: Number(env.VITE_HMR_CLIENT_PORT) || undefined }
        : false,
      watch: process.env.DISABLE_HMR === "true" ? null : {},
      // Everything goes through Kong (VITE_API_URL, default :8100), which
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
        const gateway = { target: env.VITE_API_URL || "http://localhost:8100", changeOrigin: true };
        return {
          // auth_api validates the real Bearer token itself, but it also sits
          // behind Kong — one gateway, one identity path, no exceptions.
          "/api": gateway,
        };
      })(),
    },
  };
});
