import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  // Cast: @vitejs/plugin-react is typed against the project's `vite`, while
  // vitest/config expects a plugin from its own nested `vite` copy. The two
  // Plugin types are structurally identical but nominally distinct.
  plugins: [react() as never],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Above vitest.setup.ts's 5 s for findBy/waitFor, so a test with two slow waits on a loaded
    // machine fails on what it found, not on the clock.
    testTimeout: 20_000,
    css: true,
    include: ["src/**/*.{test,spec}.{ts,tsx}", "tests/frontend/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["node_modules", "dist", "archive", ".git"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.d.ts",
        "src/**/*.test.{ts,tsx}",
        "src/**/*.spec.{ts,tsx}",
        "src/main.tsx",
        "src/vite-env.d.ts",
      ],
      thresholds: {
        // Global stays permissive while overall coverage is still low — a
        // repo-wide 60% gate on day 1 would block every PR. Instead we ratchet
        // per-file: once a module has real tests, it gets a high per-file
        // threshold so its coverage can't silently regress. Add modules here as
        // they gain tests; raise the global numbers once enough files qualify.
        lines: 0,
        functions: 0,
        branches: 0,
        statements: 0,
      },
    },
  },
});
