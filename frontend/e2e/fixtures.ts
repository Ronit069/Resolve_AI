import type { Page } from "@playwright/test";

const DEMO_APPROVER_EMAIL = "demo.approver@resolveai-demo.com";
// Matches scripts/seed_demo.py's own DEMO_USER_PASSWORD env var/default --
// read the same way so a shared demo environment overriding the password
// doesn't desync the two.
const DEMO_APPROVER_PASSWORD = process.env.DEMO_USER_PASSWORD ?? "resolveai-demo-password";

/**
 * Signs in through the real sign-in form as the seeded demo.approver
 * account (role APPROVER). Real auth, not the dev identity selector --
 * issue #22's fix (reseeding demo accounts off the reserved ".local"
 * TLD) made that actually possible; this exercises the same login path
 * a real user hits, which is a better test than routing around it.
 */
export async function signInAsApprover(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(DEMO_APPROVER_EMAIL);
  await page.getByLabel("Password").fill(DEMO_APPROVER_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");
}
