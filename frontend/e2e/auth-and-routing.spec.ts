import { test, expect } from "@playwright/test";

/**
 * Frontend-only smoke tests (issue #19) -- no backend mutation, and no
 * dependency on seeded demo data: they exercise client-side routing and
 * the real sign-in form's error handling.
 */
test.describe("auth and routing", () => {
  test("an unauthenticated visitor hitting a protected route is redirected to /login", async ({ page }) => {
    await page.goto("/queue");
    await expect(page).toHaveURL("/login");
  });

  test("an unauthenticated visitor hitting a case route is redirected to /login", async ({ page }) => {
    await page.goto("/cases/00000000-0000-0000-0000-000000000000");
    await expect(page).toHaveURL("/login");
  });

  test("real sign-in with wrong credentials shows the server's error, not a silent failure", async ({ page }) => {
    // A syntactically valid but non-existent address, deliberately not
    // one of the @resolveai.local demo accounts: those addresses hit a
    // separate, pre-existing EmailStr-validation bug (see global-setup.ts)
    // that this assertion isn't meant to exercise.
    await page.goto("/login");
    await page.getByLabel("Email").fill("nobody@example.com");
    await page.getByLabel("Password").fill("whatever-password");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("alert")).toBeVisible();
    await expect(page).toHaveURL("/login");
  });
});
