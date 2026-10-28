import path from "node:path";

import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: process.env.VITEST ? [] : [tailwindcss(), reactRouter()],
  resolve: { alias: { "~": path.resolve(__dirname, "./app") } },
  test: { environment: "jsdom", globals: true, setupFiles: ["./vitest.setup.ts"], css: false },
});
