/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/**
 * The P1 sources API (mounted under /api/v1/sources) requires a local bearer token that the
 * backend regenerates at each start in `<P1 data dir>/api-token`. The dev proxy reads it
 * server-side on every request and adds the header: the token never reaches the browser bundle.
 */
function sourcesTokenFile(env: Record<string, string>): string {
  return (
    env.ENSORA_P1_TOKEN_FILE ||
    join(env.LOCAL_DATA_DIR || join(homedir(), ".local", "share", "ensora-plus"), "api-token")
  );
}

function readToken(file: string): string | null {
  try {
    return readFileSync(file, "ascii").trim() || null;
  } catch {
    return null; // backend not started yet, or sources disabled: the API answers 401/404
  }
}

export default defineConfig(({ mode }) => {
  const env = { ...process.env, ...loadEnv(mode, ".", "") } as Record<string, string>;
  const target = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";
  const tokenFile = sourcesTokenFile(env);
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      // Same-origin calls in development: no CORS round-trip, no credentials in the bundle.
      // Order matters: the more specific /api/v1/sources rule must come first.
      proxy: {
        "/api/v1/sources": {
          target,
          changeOrigin: false,
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq) => {
              const token = readToken(tokenFile);
              if (token) proxyReq.setHeader("Authorization", `Bearer ${token}`);
            });
          },
        },
        "/api": { target, changeOrigin: false },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./tests/setup.ts"],
      include: ["tests/**/*.test.{ts,tsx}"],
    },
  };
});
