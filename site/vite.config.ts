import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const SITE_ROOT = path.dirname(fileURLToPath(import.meta.url));

function exposeLlmsTxt(): Plugin {
  let outDir = "dist";
  let base = "/";
  const sourcePath = () => path.resolve(SITE_ROOT, "public/data/llms.txt");

  return {
    name: "ogprod-expose-llms-txt",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
      base = config.base.endsWith("/") ? config.base : `${config.base}/`;
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const pathname = (req.url ?? "").split("?", 1)[0];
        if (pathname === `${base}llms.txt` || pathname === "/llms.txt") {
          const src = sourcePath();
          if (fs.existsSync(src)) {
            res.setHeader("Content-Type", "text/markdown; charset=utf-8");
            res.end(fs.readFileSync(src, "utf-8"));
            return;
          }
        }
        next();
      });
    },
    closeBundle() {
      const src = sourcePath();
      if (fs.existsSync(src) && fs.existsSync(outDir)) {
        fs.copyFileSync(src, path.join(outDir, "llms.txt"));
      }
    },
  };
}

export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? "/",
  plugins: [react(), tailwindcss(), exposeLlmsTxt()],
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
