/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The app is bundled into android/app/src/main/assets/public by `cap sync`.
// Relative base so the bundle works from the WebView's local origin.
export default defineConfig({
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  test: { environment: "node", include: ["test/**/*.test.ts"] },
});
