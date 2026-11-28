// The jury journey in a real browser, on the production build with demo data.
import { expect, test } from "@playwright/test";

import { loginDemo } from "./helpers";

test("ENSAI login: username + password, data import from the ENSAI sources, then back to the asked page", async ({ page }) => {
  await page.goto("/planner");
  await expect(page).toHaveURL(/\/login\?next=%2Fplanner/);
  await page.getByLabel("Identifiant ENSAI").fill("jean@ensai.fr");
  await page.getByLabel("Mot de passe").fill("secret");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByText(/Identifiant ENSAI invalide/)).toBeVisible();

  await page.getByLabel("Identifiant ENSAI").fill("id2a123");
  await page.getByLabel("Mot de passe").fill("secret");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("heading", { name: "Récupération de vos données" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1, name: "Smart Planner" })).toBeVisible({ timeout: 20_000 });

  // Identity is read from the ENSAI account, not typed.
  await page.goto("/profile");
  await expect(page.getByText("Récupéré de votre compte ENSAI")).toBeVisible();
});

test("agenda confirmation, scope, plan preview and acceptance; state survives a reload", async ({ page }) => {
  await loginDemo(page, "/agenda");
  await page.getByRole("button", { name: /Oui, c'est un examen/ }).click();
  await expect(page.getByText("évaluation à confirmer")).toHaveCount(0);

  await page.goto("/planner");
  await page.getByRole("button", { name: "Confirmer le programme" }).click();
  await page.getByRole("button", { name: "Confirmer ce programme" }).click();
  await page.getByRole("button", { name: "Générer une proposition" }).click();
  await expect(page.getByText("Proposition (pas encore appliquée)")).toBeVisible();
  await page.getByRole("button", { name: "Accepter", exact: true }).click();
  await expect(page.getByRole("button", { name: "Verrouiller la session" }).first()).toBeVisible();
  const sessions = await page.getByRole("button", { name: "Verrouiller la session" }).count();

  await page.reload();
  await expect(page.getByRole("button", { name: "Verrouiller la session" })).toHaveCount(sessions);
});

test("tutor answers from the sources with citations, or says they are insufficient", async ({ page }) => {
  await loginDemo(page, "/tutor");
  await page.getByLabel("Votre question").fill("Explique la p-valeur");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(page.getByText("Appuyée sur vos sources")).toBeVisible();
  await expect(page.locator('[aria-label="Réponse du tuteur"] ol li a').first()).toBeVisible();

  await page.getByLabel("Votre question").fill("Quelle est la recette de la tarte tatin ?");
  await page.getByRole("button", { name: "Envoyer" }).click();
  await expect(page.getByText("Sources insuffisantes")).toBeVisible();
});

test("studio generates a sheet with formulas and sources, then quiz updates mastery", async ({ page }) => {
  await loginDemo(page, "/studio");
  await page.getByLabel("Matière").selectOption({ label: "Statistique inférentielle" });
  await page.getByRole("button", { name: "Générer", exact: true }).click();
  await expect(page.locator(".katex").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('a[href*="cite="]').first()).toBeVisible();

  await page.goto("/quiz");
  await page.getByRole("button", { name: "Commencer" }).first().click();
  await page.getByRole("radio").first().check();
  await page.getByRole("button", { name: "Rendre ma copie" }).click();
  await page.getByRole("button", { name: "Rendre quand même" }).click();
  await expect(page.getByText("Réponse attendue").first()).toBeVisible();
  await expect(page.getByText("Niveau mis à jour")).toBeVisible();
});

test("grades: a mark is saved, can be erased (missing, not 0), and simulation saves nothing", async ({ page }) => {
  await loginDemo(page, "/grades");
  const partiel = page.getByLabel(/Note de « Partiel »/);
  await partiel.fill("14,5");
  await partiel.press("Enter");
  await expect(page.getByText("12,91")).toHaveCount(0);

  await partiel.fill("");
  await partiel.press("Enter");
  await expect(page.getByText("12,91")).toBeVisible();

  await page.getByRole("button", { name: "Simuler" }).click();
  await page.getByLabel(/Note hypothétique/).first().fill("16");
  await page.getByRole("button", { name: "Calculer" }).click();
  await expect(page.getByRole("columnheader", { name: "Projection" })).toBeVisible();
});

test("first-run setup is reached from the profile and saves goals and availability", async ({ page }) => {
  await loginDemo(page, "/profile");
  await page.getByRole("link", { name: "Refaire la configuration" }).click();
  await expect(page.getByRole("heading", { name: "Configurer mon espace" })).toBeVisible();
  await page.getByRole("button", { name: "Suivant" }).click(); // scolarité
  await page.getByRole("button", { name: "Suivant" }).click(); // connexions
  await page.getByRole("button", { name: "Suivant" }).click(); // matières
  await page.getByRole("button", { name: "Exercices" }).click(); // formats, at the top
  await expect(page.getByText("Ces cours existent en deux versions")).toBeVisible();
  await page.getByRole("button", { name: "Suivant" }).click(); // objectifs et préférences
  await page.getByRole("button", { name: /^Connecter/ }).first().click(); // Google Agenda, the ENSAI timetable is already there
  await page.getByLabel("Pomodoro long 50 / 10").check({ force: true });
  await page.getByRole("button", { name: "Terminer" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Bonjour");
});
