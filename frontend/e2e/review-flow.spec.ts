import { test, expect } from "@playwright/test";
import { signInAsApprover } from "./fixtures";

/**
 * Core reviewer flow (issue #19): login -> dispute queue -> case
 * workspace -> response review -> audit activity, against the real
 * backend and the three demo cases scripts/seed_demo.py seeds (CONTEST,
 * REVIEW, ACCEPT/missing-evidence -- see that script's docstring).
 *
 * Deliberately read-only: it never submits a review action or generates
 * a draft, so it's safe to re-run against a long-lived seeded stack
 * without mutating case state the next run depends on.
 */
test.describe("reviewer flow", () => {
  test("signs in and reaches the risk command center", async ({ page }) => {
    await signInAsApprover(page);
    await expect(page).toHaveURL("/");
    // Scoped to the navbar: the Risk Command Center itself also renders
    // an "Open Dispute Queue →" CTA link once its metrics load, which a
    // bare name match ambiguously also matches (a strict-mode violation
    // that surfaced intermittently in CI depending on fetch timing).
    await expect(page.locator("nav").getByRole("link", { name: "Dispute Queue" })).toBeVisible();
  });

  test("walks queue -> case workspace -> response review -> audit activity for the REVIEW-path demo case", async ({
    page,
  }) => {
    await signInAsApprover(page);

    await page.locator("nav").getByRole("link", { name: "Dispute Queue" }).click();
    await expect(page.getByRole("heading", { name: "Dispute Queue" })).toBeVisible();

    // The REVIEW-path demo case (seed_demo.py's case 2) is the only
    // seeded case left PENDING -- case 1 (CONTEST) and case 3 (ACCEPT)
    // are both finalized to DONE -- so filtering by status isolates it
    // regardless of row ordering/ties on respond_by.
    await page.getByLabel("Status").selectOption("PENDING");

    const pendingRow = page.locator("tbody tr", { hasText: "REVIEW" }).first();
    await expect(pendingRow).toBeVisible();
    await pendingRow.getByRole("link", { name: /Review/ }).click();

    await expect(page.getByRole("heading", { name: "Case Workspace" })).toBeVisible();
    const riskSection = page.locator("section", { hasText: "Risk Assessment" });
    await expect(riskSection).toBeVisible();
    await expect(riskSection.getByText("REVIEW", { exact: true })).toBeVisible();

    await page.getByRole("link", { name: /Go to Response Review/ }).click();
    await expect(page.getByRole("heading", { name: "Response Review" })).toBeVisible();
    // Either a generated draft or the "no draft yet" empty state is a
    // valid resolved loading state -- this assertion only rules out an
    // error/infinite-loading state, it never generates a draft itself.
    await expect(page.getByText(/Generating\.\.\./)).not.toBeVisible();

    const caseUrl = page.url().replace(/\/review$/, "");
    await page.goto(`${caseUrl}/audit`);
    await expect(page.getByRole("heading", { name: "Audit / Activity" })).toBeVisible();
  });
});
