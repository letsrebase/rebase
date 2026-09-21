import { tanstackRouter } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { defineConfig, type Plugin } from 'vite'
import { configDefaults } from 'vitest/config'

import { redirectToBase } from './src/lib/devBaseRedirect'

// Dev server only: the bare base (`/app`, `/app?tab=…`) gets `index.html` through a
// redirect to `/app/`, as nginx already does in production, instead of Vite's
// "did you mean /app/app?" hint page. See `src/lib/devBaseRedirect.ts`.
const devBaseRedirect: Plugin = {
  name: 'pigrocrm:dev-base-redirect',
  configureServer(server) {
    server.middlewares.use((req, res, next) => {
      const target = req.url ? redirectToBase(req.url, server.config.base) : null
      if (target === null) return next()
      res.statusCode = 302
      res.setHeader('Location', target)
      res.end()
    })
  },
}

export default defineConfig({
  // The SPA is served from /usr/share/nginx/html/app (Dockerfile.web) and matched by
  // `location ^~ /app/` (deploy/nginx/spa.conf). Without this, every emitted asset
  // URL is /assets/... and 404s under the new prefix. The API client is unaffected:
  // lib/api.ts uses `baseUrl: ''` with full `/api/...` keys, so its requests are
  // absolute-from-root and do not inherit this base.
  base: '/app/',
  plugins: [
    tanstackRouter({
      target: 'react',
      autoCodeSplitting: true,
      // Without this, a `*.test.tsx` colocated next to a route file (the same
      // convention every other feature in this codebase already uses -- see
      // `routes/app/customers/$customerId.test.tsx` and its two siblings) is scanned
      // as a route candidate too, and warns on every dev/build/test run ("does not
      // export a Route") since it obviously does not export one. A raw regex
      // source string, matched against the bare filename (confirmed by reading
      // `@tanstack/router-generator`'s own `getRouteNodes`), not a glob.
      routeFileIgnorePattern: '\\.test\\.tsx$',
    }),
    react(),
    tailwindcss(),
    devBaseRedirect,
  ],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: {
    port: 5173,
    // The browser talks to the same origin in dev and in production, so cookies
    // behave identically in both and there is no CORS special case to debug.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test-setup.ts',
    // 20 s, not Vitest's own 5 s. Several files here render a whole page --
    // `QueryClientProvider`, a mocked router, a `DataTable` and a Radix dialog or
    // dropdown per assertion -- and `userEvent`'s per-keystroke work is real time. On
    // a laptop running the full suite across every core at once, those files sit close
    // enough to 5 s that the timeout fired on whichever file happened to be scheduled
    // last, which is a flake and not a failure: the same file passes on its own. The
    // limit still exists (a genuinely hung `findBy*` fails rather than blocking the
    // run), just far enough out that scheduling luck is not what decides.
    testTimeout: 20_000,
    // `apps/web/e2e/*.spec.ts` (Playwright, run via `playwright.config.ts`/
    // `pnpm exec playwright test`) match Vitest's own default include glob
    // just as well as any `*.test.tsx` here does -- confirmed live: without
    // this, `pnpm vitest run` picked up all six Playwright files and failed
    // each one immediately (`test.beforeEach()`/`test()` "did not expect ...
    // to be called here", Playwright's own guard against its globals leaking
    // into a foreign runner). `configDefaults.exclude` is spread back in
    // because `test.exclude` *replaces* Vitest's own default list rather than
    // adding to it -- dropping that spread would silently stop ignoring
    // node_modules/dist/etc. The two suites stay genuinely separate (task-10's
    // own requirement) rather than merely usually-not-colliding by luck of
    // file naming.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
