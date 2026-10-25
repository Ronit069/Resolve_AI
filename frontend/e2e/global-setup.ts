import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Playwright's global setup runs in its own process, separate from the
// test workers -- an env var set here would not be visible to them, so
// the looked-up user_id is written to a file instead and read back by
// e2e/fixtures.ts.
export const DEMO_APPROVER_FIXTURE_PATH = path.join(__dirname, ".demo-approver-user-id.json");

const DEMO_APPROVER_EMAIL = "demo.approver@resolveai.local";

/**
 * This suite drives the UI through the dev identity selector (not real
 * sign-in) because of a separate, pre-existing bug: the login endpoint's
 * EmailStr validation rejects the demo accounts' @resolveai.local
 * addresses outright (".local" is a reserved/special-use TLD per RFC
 * 6761) -- so no password, correct or not, ever gets a demo account
 * past POST /api/v1/auth/login today. Tracked separately; not fixed
 * here since it touches production auth validation.
 *
 * The dev selector needs the demo approver's actual user_id, which
 * scripts/seed_demo.py assigns via uuid.uuid4() -- different on every
 * fresh seed (e.g. every CI run) -- so it can't be hardcoded. Looked up
 * directly from Postgres instead of adding a new backend endpoint for
 * what is purely a test-setup concern.
 */
async function globalSetup() {
  const host = process.env.E2E_POSTGRES_HOST ?? "localhost";
  const port = process.env.E2E_POSTGRES_PORT ?? "5433";
  const user = process.env.E2E_POSTGRES_USER ?? "resolve_user";
  const password = process.env.E2E_POSTGRES_PASSWORD ?? "resolve_password";
  const database = process.env.E2E_POSTGRES_DB ?? "resolve_db";

  let userId: string;
  try {
    userId = execFileSync(
      "psql",
      [
        "-h", host,
        "-p", port,
        "-U", user,
        "-d", database,
        "-t", "-A",
        "-c", `select user_id from app_users where email = '${DEMO_APPROVER_EMAIL}'`,
      ],
      { env: { ...process.env, PGPASSWORD: password } }
    ).toString().trim();
  } catch (err) {
    throw new Error(
      `Could not query Postgres (${host}:${port}/${database}) for the seeded demo approver user. ` +
        "Is the backend stack up and has scripts/seed_demo.py been run against it? " +
        `Original error: ${(err as Error).message}`
    );
  }

  if (!userId) {
    throw new Error(
      `No app_users row found for ${DEMO_APPROVER_EMAIL}. Run ` +
        "`python scripts/seed_demo.py` against the backend this E2E suite targets before running tests."
    );
  }

  fs.writeFileSync(DEMO_APPROVER_FIXTURE_PATH, JSON.stringify({ approverUserId: userId }));
}

export default globalSetup;
