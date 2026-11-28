import { expect, type Page } from "@playwright/test";

/** Opens the demo account (each test starts from fresh demo data). */
export async function loginDemo(page: Page, path = "/") {
  await page.goto(`/login?next=${encodeURIComponent(path)}`);
  await page.getByRole("button", { name: "Utiliser le compte de démonstration" }).click();
  // The ENSAI data import screen is shown first, then the asked page.
  await expect(page.locator("#page-title, #onboarding-title").first()).toBeVisible({ timeout: 20_000 });
}

export const PAGES = [
  "/",
  "/subjects",
  "/subjects/10000000-0000-4000-8000-000000000001",
  "/library",
  "/agenda",
  "/planner",
  "/tasks",
  "/tutor",
  "/studio",
  "/flashcards",
  "/quiz",
  "/exams",
  "/notes",
  "/focus",
  "/analytics",
  "/grades",
  "/profile",
  "/settings",
  "/notifications",
  "/onboarding",
];
