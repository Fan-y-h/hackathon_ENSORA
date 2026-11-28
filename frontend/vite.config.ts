import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // "@/components/Button" means "src/components/Button".
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    // When the team backend runs on port 8000, every /api/... call is forwarded to it.
    // The browser only talks to one address, so no CORS configuration is needed.
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    // API tests run in Node with the mock backend (src/mocks/node.ts).
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
