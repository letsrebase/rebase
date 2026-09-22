import path from 'node:path'
import { defineConfig } from 'vitest/config'
import { palettePlugin } from './src/palette-plugin'
import { pathMapPlugin } from './src/path-map-plugin'

const apiUrl = process.env.WEBSITE_API_URL ?? 'http://localhost:8000'

export default defineConfig({
  root: path.resolve(__dirname, 'src'),
  // Absolute, not './': nginx serves these three files from the document root, and
  // /privacy is one path segment deep only by URL, not by directory. A relative
  // base would still work here, but it would break the moment a page moved.
  base: '/',
  // No React, no Tailwind, no TanStack router plugin. That absence is the
  // requirement, not an omission: spec 9.4 exists so these pages do not drag the
  // application's bundle behind it.
  //
  // `pathMapPlugin` is the dev and preview servers' copy of deploy/nginx.conf: `/` is
  // the landing, `/pigrocrm` the CRM's page, `/community` and `/orbiters` both 301 to
  // it since the community page was retired (REB-72), and anything else a 404. Its
  // unit test reads nginx.conf, so the two cannot drift quietly.
  plugins: [palettePlugin(), pathMapPlugin()],
  // 'mpa' turns off Vite's fallback to index.html for a path that resolves to no file.
  // With it on, an unknown path answered 200 with PigroCRM's page here and 404 in
  // production (ORB-21); the plugin above decides the known paths, this makes the
  // unknown ones fail the same way on both.
  appType: 'mpa',
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    // field.js is shared by every page landing.js mounts it on; inlining it would put a copy
    // inside two HTML files instead of one cacheable asset.
    assetsInlineLimit: 0,
    rollupOptions: {
      input: {
        index: path.resolve(__dirname, 'src/index.html'),
        pigrocrm: path.resolve(__dirname, 'src/pigrocrm.html'),
        privacy: path.resolve(__dirname, 'src/privacy.html'),
        terms: path.resolve(__dirname, 'src/terms.html'),
        pitch: path.resolve(__dirname, 'src/pitch.html'),
      },
    },
  },
  // `/api/community/signups` is the hub's own endpoint, reached on the same origin.
  // Nothing in this project calls it any more since the community page's signup form
  // was retired (REB-72), but dev and preview still proxy `/api` so it can be
  // exercised directly; WEBSITE_API_URL points it elsewhere.
  server: { proxy: { '/api': { target: apiUrl, changeOrigin: true } } },
  preview: { port: 4173, strictPort: true, proxy: { '/api': { target: apiUrl, changeOrigin: true } } },
  // The suite reads the built and unbuilt files off disk and asserts on their text:
  // which tokens a sheet is allowed to declare, what each page's markup promises, how
  // the field behaves. `jsdom` is here for the two files that construct elements;
  // nothing renders a framework, because there is no framework.
  test: { environment: 'jsdom', globals: true },
})
