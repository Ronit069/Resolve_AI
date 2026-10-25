import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { signInAsApprover } from "./fixtures";

/**
 * Automated accessibility audit (issue #20) -- axe-core against all seven
 * screens, scoped to WCAG 2.0/2.1 A and AA (the issue's own "WCAG AA at
 * minimum" ask), run through a real signed-in session against real
 * backend data rather than static markup, so conditionally-rendered
 * states (loaded data, not just loading/empty) are actually covered.
 *
 * Complements, not replaces, the manual checks the issue also asks for
 * (keyboard-only navigation, focus visibility) -- axe-core cannot verify
 * those; see the issue/PR for that part of the audit.
 */
async function auditPage(page: Page, path: string) {
  await page.goto(path);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

test.describe("accessibility (WCAG 2.1 AA, axe-core)", () => {
  test("Login (/login)", async ({ page }) => {
    await auditPage(page, "/login");
  });

  test("Risk Command Center (/)", async ({ page }) => {
    await signInAsApprover(page);
    await auditPage(page, "/");
  });

  test("Dispute Queue (/queue)", async ({ page }) => {
    await signInAsApprover(page);
    await auditPage(page, "/queue");
  });

  test("Model Metrics (/metrics)", async ({ page }) => {
    await signInAsApprover(page);
    await auditPage(page, "/metrics");
  });

  test("Case Workspace, Response Review, Audit Activity (/cases/:caseId/*)", async ({ page }) => {
    await signInAsApprover(page);
    await page.goto("/queue");
    const firstCaseLink = page.locator("tbody tr").first().getByRole("link", { name: /Review/ });
    await expect(firstCaseLink).toBeVisible();
    await firstCaseLink.click();
    await expect(page.getByRole("heading", { name: "Case Workspace" })).toBeVisible();

    await auditPage(page, page.url());

    const caseUrl = page.url();
    await auditPage(page, `${caseUrl}/review`);
    await auditPage(page, `${caseUrl}/audit`);
  });
});
