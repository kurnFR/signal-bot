import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8050",
        changeOrigin: true,
      },
      "/ws": {
        target: "http://127.0.0.1:8050",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("lightweight-charts")) return "charts-vendor";
          if (id.includes("lucide-react") || id.includes("cmdk") || id.includes("react-resizable-panels")) {
            return "ui-vendor";
          }
          if (id.includes("react-dom") || id.includes("/react/") || id.includes("scheduler")) {
            return "react-vendor";
          }
          return "vendor";
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    clearMocks: true,
    exclude: ["e2e/**", "node_modules/**", "dist/**", "test-results/**", "playwright-report/**"],
  },
});
