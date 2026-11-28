// Startup behaviour of src/main.tsx.
import { expect, test } from "@playwright/test";

test("a clear message (not a vague server error) when the demo backend cannot load", async ({ page }) => {
  // Simulate a failed download of the demo backend chunk.
  await page.route(/\/assets\/browser-[^/]+\.js$/, (route) => route.abort());
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Le mode démonstration n'a pas pu démarrer" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Recharger la page" })).toBeVisible();
});

test("the page is never blank while the app starts", async ({ page }) => {
  // Delay the app scripts: the startup message from index.html must be visible meanwhile.
  await page.route(/\/assets\/index-[^/]+\.js$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    await route.continue();
  });
  await page.goto("/", { waitUntil: "commit" });
  await expect(page.getByText("Chargement d'Ensora+…")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Connexion" })).toBeVisible({ timeout: 15_000 });
});
