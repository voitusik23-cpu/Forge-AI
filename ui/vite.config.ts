import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true, target: "es2020" },
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8011", changeOrigin: true } },
  },
});
