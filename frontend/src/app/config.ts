// Global settings read from the environment (.env, .env.production, .env.demo, .env.local).

/**
 * true unless VITE_USE_MOCKS is exactly "false": demo data by default in development.
 * `npm run build` loads .env.production (mocks off); `npm run build:demo` loads .env.demo (mocks on).
 * The same rule is written inline in src/main.tsx (needed there so Vite can drop the demo
 * backend from real builds): if you change it here, change it there too.
 */
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS !== "false";

/** Base path of the team API, as defined in docs/API_CONTRACTS.md. */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";
