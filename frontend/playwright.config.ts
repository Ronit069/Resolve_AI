import { defineConfig, devices } from "@playwright/test";

/**
 * True browser-against-real-backend E2E config (issue #19). Assumes the
 * full stack (frontend dev/preview server + the real FastAPI backend,
 * migrated and seeded via scripts/seed_demo.py) is already running --
 * this config does not start either, since CI brings them up via the
 * existing docker-compose.yaml (see .github/workflows/docker-build.yml)
 * and local runs are expected to do the same via `npm run dev` + a
 * running backend.
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // CI/most local setups resolve this via `playwright install`
        // (the default, no executablePath) -- this override exists only
        // for sandboxes with a pre-installed, differently-versioned
        // Chromium (e.g. this Claude Code sandbox) and has no effect
        // elsewhere since the env var is otherwise unset.
        ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
          ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } }
          : {}),
      },
    },
  ],
});
