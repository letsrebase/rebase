import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * Only the gallery is an application here: the package ships source, which each SPA
 * bundles itself. `root` is the gallery directory so its `index.html` is the entry and
 * the primitives stay flat beside `package.json`, the way `@rebase/analytics` keeps
 * its modules.
 *
 * `pnpm --filter @rebase/ui dev` serves it; `build` emits `gallery/dist`, which is
 * what `uishot` and `uislop` run against in REB-301.
 */
export default defineConfig({
  root: 'gallery',
  base: './',
  plugins: [react(), tailwindcss()],
  build: { outDir: 'dist', emptyOutDir: true },
})
