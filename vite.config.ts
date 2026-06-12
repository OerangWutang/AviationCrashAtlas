import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Proxy all API and auth traffic to the BFF (port 3001).
      // The BFF owns sessions/auth and proxies to FastAPI for /api/... routes.
      "/api": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
      "/health": {
        target: "http://localhost:3001",
        changeOrigin: true,
      },
    },
  },
// ── Vitest ────────────────────────────────────────────────────────────────
  test: {
    // Use jsdom so React components can render with a DOM.
    environment: "jsdom",

    // Run the setup file before each test file.
    setupFiles: ["src/test/setup.ts"],

    // Include component test files alongside source.
    include: ["src/**/*.test.{ts,tsx}"],

    // Global APIs (describe, it, expect) available without import.
    globals: true,

    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/main.tsx",
        "src/vite-env.d.ts",
      ],
    },
  },
});
