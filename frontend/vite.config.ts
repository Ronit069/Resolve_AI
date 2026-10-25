import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { configDefaults } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    // e2e/ holds Playwright specs (issue #19), run via `npm run test:e2e`
    // (playwright test) -- vitest's default glob would otherwise also
    // pick them up and fail, since they use @playwright/test's own
    // test()/expect(), not vitest's.
    exclude: [...configDefaults.exclude, "e2e/**"],
  },
});
