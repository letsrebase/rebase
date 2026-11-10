/**
 * Makes the build installable (REB-731): emits the web manifest and the icons from
 * `shared/brand/pwa`, tells every page its theme colour and iOS touch icon, and writes
 * `sw.js` once the rest of the build is on disk, because the worker's precache list is
 * the build's own output.
 *
 * Build only. The dev server never registers a worker (`src/pwa/register.ts` checks
 * `import.meta.env.PROD`), since one that outlived a `pnpm dev` session would serve
 * yesterday's offline page over today's code.
 *
 * The worker is a second, tiny Vite build (a single IIFE, no chunks, so a classic
 * worker can run it) started from `writeBundle` rather than a second config: it needs
 * the file names the first build just chose. Written by hand rather than with
 * vite-plugin-pwa, whose Workbox runtime caches responses by pattern, which is the
 * rule this worker exists not to have.
 */
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { build, type Plugin } from 'vite'
import { paletteFrom } from '../../../../../shared/brand/contrast'
import { buildManifest, type ManifestIcon } from './manifest'
import { buildId, shellUrls } from './shell'

const BRAND_ICONS = path.resolve(import.meta.dirname, '../../../../../shared/brand/pwa')

const MANIFEST_ICONS: readonly (Omit<ManifestIcon, 'src'> & { file: string })[] = [
  { file: 'icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
  { file: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
  { file: 'icon-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
  { file: 'icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
]
const TOUCH_ICON = 'apple-touch-icon.png'

/** Where the icons are written, relative to `outDir`: under `assets/`, because that is
 *  the one directory the host vhost already hands to the container whatever the path
 *  (deploy/nginx/pigro.letsrebase.conf), and a manifest icon is fetched at its absolute
 *  URL whichever space the app was installed from. */
const ICON_DIR = 'assets/icons'

export function pwa(): Plugin {
  let base = '/'
  let root = process.cwd()
  let ground = ''

  return {
    name: 'pigrocrm:pwa',
    apply: 'build',

    configResolved(config) {
      base = config.base
      root = config.root
      const paper = paletteFrom(readFileSync(path.resolve(BRAND_ICONS, '../palette.css'), 'utf-8'))[
        '--color-paper'
      ]
      if (!paper) throw new Error('--color-paper is not declared in shared/brand/palette.css')
      ground = paper
    },

    buildStart() {
      for (const file of [...MANIFEST_ICONS.map((icon) => icon.file), TOUCH_ICON]) {
        this.emitFile({
          type: 'asset',
          fileName: `${ICON_DIR}/${file}`,
          source: readFileSync(path.join(BRAND_ICONS, file)),
        })
      }
    },

    generateBundle() {
      const icons = MANIFEST_ICONS.map(({ file, ...icon }) => ({
        src: `${base}${ICON_DIR}/${file}`,
        ...icon,
      }))
      this.emitFile({
        type: 'asset',
        fileName: 'manifest.webmanifest',
        source: JSON.stringify(buildManifest({ ground, icons }), null, 2),
      })
    },

    transformIndexHtml() {
      return [
        { tag: 'meta', attrs: { name: 'theme-color', content: ground }, injectTo: 'head' },
        {
          tag: 'link',
          attrs: { rel: 'apple-touch-icon', href: `${base}${ICON_DIR}/${TOUCH_ICON}` },
          injectTo: 'head',
        },
        {
          tag: 'meta',
          attrs: { name: 'apple-mobile-web-app-title', content: 'PigroCRM' },
          injectTo: 'head',
        },
      ]
    },

    async writeBundle(options, bundle) {
      const outDir = options.dir
      if (!outDir) throw new Error('pigrocrm:pwa needs build.outDir')
      const offline = bundle['offline.html']
      if (offline?.type !== 'asset') throw new Error('offline.html is not an output of this build')
      const html = String(offline.source)

      const urls = shellUrls(html, base, (url) => {
        const emitted = bundle[url.slice(base.length)]
        return emitted?.type === 'asset' ? String(emitted.source) : undefined
      })
      // A file the offline page names but the build did not write would make the
      // worker's install fail in every browser, after deploy. Fail here instead.
      const missing = urls.filter((url) => !existsSync(path.join(outDir, url.slice(base.length))))
      if (missing.length > 0) {
        throw new Error(`the offline page refers to files the build did not write: ${missing.join(', ')}`)
      }

      const contents = new Map(
        urls.map((url) => [url, readFileSync(path.join(outDir, url.slice(base.length)))] as const),
      )

      await build({
        configFile: false,
        root,
        logLevel: 'warn',
        publicDir: false,
        define: {
          __BUILD_ID__: JSON.stringify(buildId(html, urls, contents)),
          __SHELL_PATHS__: JSON.stringify(urls),
          // The picture inside a push notification: the 192 px icon, at the one URL the
          // host serves it from for every space (the manifest's own).
          __NOTIFICATION_ICON__: JSON.stringify(`${base}${ICON_DIR}/icon-192.png`),
        },
        build: {
          outDir,
          emptyOutDir: false,
          minify: true,
          target: 'es2022',
          lib: {
            entry: path.resolve(root, 'sw/index.ts'),
            formats: ['iife'],
            name: 'pigrocrmServiceWorker',
            fileName: () => 'sw.js',
          },
        },
      })
    },
  }
}
