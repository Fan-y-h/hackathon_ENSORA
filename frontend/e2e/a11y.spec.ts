// Accessibility audit (axe-core, WCAG 2 A/AA + best practices) of every screen, light and dark.
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { loginDemo, PAGES } from "./helpers";

for (const theme of ["light", "dark"] as const) {
  test(`no axe violation on any screen (${theme} theme)`, async ({ page }) => {
    await loginDemo(page);
    await page.evaluate((t) => localStorage.setItem("ensora.theme", t), theme);
    const problems: string[] = [];
    for (const path of PAGES) {
      await page.goto(path);
      await expect(page.locator("#page-title, #onboarding-title").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "best-practice"]).analyze();
      for (const v of results.violations) problems.push(`${path}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target.join(" ")}`);
    }
    expect(problems).toEqual([]);
  });
}
