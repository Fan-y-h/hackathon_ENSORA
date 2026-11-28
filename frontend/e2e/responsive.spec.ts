// Phone width: no screen may scroll sideways (wide tables and calendars scroll inside their own box).
import { expect, test } from "@playwright/test";

import { loginDemo, PAGES } from "./helpers";

test("no horizontal page overflow on a phone", async ({ page }) => {
  await loginDemo(page);
  const overflowing: string[] = [];
  for (const path of PAGES) {
    await page.goto(path);
    await expect(page.locator("#page-title, #onboarding-title").first()).toBeVisible();
    const tooWide = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    if (tooWide) overflowing.push(path);
  }
  expect(overflowing).toEqual([]);
});
