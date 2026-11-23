import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? "/",
  plugins: [react(), tailwindcss()],
  build: {
    target: "es2022",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ["maplibre-gl", "pmtiles"],
          echarts: ["echarts"],
        },
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/__tests__/**/*.test.ts"],
  },
});
