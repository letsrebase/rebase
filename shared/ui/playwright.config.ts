import { defineConfig, devices } from '@playwright/test'

/**
 * The gallery, in a real browser. Everything the token test can prove by reading the
 * stylesheet, it proves there and faster; this suite exists for the half that only a
 * browser knows: what the cascade actually computes on a rendered element, what axe
 * measures on the pixels, and what happens when the visitor's OS is in dark mode.
 *
 * Port 4174, because 4173 is the website's preview and this box runs both.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://localhost:4174', trace: 'on-first-retry' },
  expect: { timeout: 8_000 },
  webServer: {
    // `preview` serves `gallery/dist`, so the build has to have happened first.
    command: 'pnpm build && pnpm exec vite preview --port 4174 --strictPort',
    url: 'http://localhost:4174/',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
