import fs from "node:fs";
import type { Page } from "@playwright/test";
import { DEMO_APPROVER_FIXTURE_PATH } from "./global-setup";

export function readApproverUserId(): string {
  const raw = fs.readFileSync(DEMO_APPROVER_FIXTURE_PATH, "utf-8");
  const { approverUserId } = JSON.parse(raw) as { approverUserId: string };
  return approverUserId;
}

/**
 * Signs in via the dev identity selector as the seeded demo.approver
 * account (role APPROVER) -- see global-setup.ts for why this suite
 * doesn't use the real sign-in form.
 */
export async function signInAsApprover(page: Page): Promise<void> {
  const approverUserId = readApproverUserId();

  await page.goto("/login");
  await page.getByRole("button", { name: /use development identity selector/i }).click();
  await page.getByLabel(/user id/i).fill(approverUserId);
  await page.getByLabel(/role/i).selectOption("APPROVER");
  await page.getByRole("button", { name: /continue with dev identity/i }).click();
}
