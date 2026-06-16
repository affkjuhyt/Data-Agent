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
        // Start permissive; raise as coverage grows. Failing CI on day 1
        // with a 60% threshold would block all PRs.
        lines: 0,
        functions: 0,
        branches: 0,
        statements: 0,
      },
    },
  },
});
