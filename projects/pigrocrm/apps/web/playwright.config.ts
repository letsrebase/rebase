import { defineConfig, devices } from '@playwright/test'
import { COMPOSE_ONLY } from './e2e/compose-only'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false, // one database, shared state
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  // A generous default: e2e/resilience.spec.ts kills the API and waits for
  // react-query's own retry policy (lib/query.ts: two retries with backoff on
  // anything that is not a 401/403) to give up before the error banner appears --
  // the library's own default 5s Playwright timeout can be tight for that one
  // assertion on a loaded CI box, and there is no cost to affording it everywhere
  // else.
  use: { trace: 'on-first-retry' },
  expect: { timeout: 8_000 },
  projects: [
    {
      name: 'chromium',
      // The composed stack's own specs run through playwright.compose.config.ts, never
      // against the preview server. The pattern is imported rather than written here: the two
      // configs spelling it separately is exactly how seven of these came to run in the
      // wrong one for a day (see e2e/compose-only.ts).
      testIgnore: COMPOSE_ONLY,
      use: { ...devices['Desktop Chrome'], baseURL: 'http://localhost:5173' },
    },
  ],
  webServer: {
    // The production build, not `pnpm dev`: e2e.sh runs `vite build` first (REB-659).
    // The dev server serves hundreds of module requests per page, and on a loaded box
    // Chromium aborts some of them (net::ERR_NETWORK_CHANGED) and fails a spec with no
    // defect in the app. `--strictPort` so a taken :5173 stops the run instead of
    // silently moving the server away from `baseURL`.
    command: 'pnpm exec vite preview --port 5173 --strictPort',
    url: 'http://localhost:5173/app/',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
