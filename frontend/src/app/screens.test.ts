// A screen keyed by a mistyped or renamed path would silently show "prévu prochainement".
// These checks make that mistake fail the tests instead.
import { describe, expect, it } from "vitest";

import { APP_ROUTES, SUBJECT_DETAIL_ROUTE } from "./routes";
import { SCREENS, STANDALONE_SCREENS } from "./screens";

const routePaths = [...APP_ROUTES, SUBJECT_DETAIL_ROUTE].map((r) => r.path);

describe("screens and routes", () => {
  it("every built screen is attached to an existing route", () => {
    // "/login" is intentionally not a menu route (it is not in routes.ts).
    for (const path of [...Object.keys(SCREENS), ...Object.keys(STANDALONE_SCREENS)].filter((p) => p !== "/login")) {
      expect(routePaths, `screen "${path}" has no route in routes.ts`).toContain(path);
    }
  });

  it("every route meant for the hackathon has a built screen", () => {
    // "/" is the eagerly loaded dashboard; "later" routes are planned placeholders on purpose.
    const expected = [...APP_ROUTES, SUBJECT_DETAIL_ROUTE].filter((r) => r.path !== "/" && r.priority !== "later");
    const built = new Set([...Object.keys(SCREENS), ...Object.keys(STANDALONE_SCREENS)]);
    for (const route of expected) expect(built.has(route.path), `route "${route.path}" (${route.label}) has no screen`).toBe(true);
  });
});
