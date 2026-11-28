// Navigation accessibility and robustness (src/app/router.tsx, usePageAnnouncer).
import { expect, test } from "@playwright/test";

import { loginDemo } from "./helpers";

test("each page has its own tab title, and focus moves to the new page heading", async ({ page }) => {
  const warnings: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "warning" || m.type() === "error") warnings.push(m.text());
  });

  await loginDemo(page, "/agenda");
  await expect(page).toHaveTitle("Agenda · ENSORA+");

  await page.getByRole("link", { name: "Bibliothèque" }).click();
  const heading = page.getByRole("heading", { level: 1, name: "Bibliothèque" });
  await expect(page).toHaveTitle("Bibliothèque · ENSORA+");
  await expect(heading).toBeFocused();

  // A subject page is titled with the subject's own name.
  await page.goto("/subjects/10000000-0000-4000-8000-000000000001");
  await expect(page).toHaveTitle("Statistique inférentielle · ENSORA+");

  expect(warnings.filter((w) => /HydrateFallback/.test(w))).toEqual([]);
});

test("a signed-out visit to /onboarding goes to login without a full reload", async ({ page }) => {
  let documentLoads = 0;
  page.on("request", (r) => {
    if (r.resourceType() === "document") documentLoads++;
  });
  await page.goto("/onboarding");
  await expect(page).toHaveURL(/\/login\?next=%2Fonboarding/);
  await expect(page.getByRole("heading", { name: "Connexion" })).toBeVisible();
  await expect(page).toHaveTitle("Connexion · ENSORA+");
  expect(documentLoads).toBe(1);
});
