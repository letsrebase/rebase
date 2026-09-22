# website

letsrebase.com: the public site. Today that is the rebase landing at `/` and the
two policy pages (`/privacy`, `/terms`); it is called `website` rather than `landing`
because it is expected to grow past those.

Five HTML pages, five scripts, four stylesheets. No React, no Tailwind, no router.
That absence is the requirement rather than an omission: this is the first page a
visitor loads, and it does not drag an application bundle behind it. The build takes
about 300 milliseconds. Anything added here should keep that true.

## Commands

```
pnpm --filter website dev        # :5173, with /api proxied to a running CRM API
pnpm --filter website build      # dist/
pnpm --filter website test       # vitest, no services, about a second
pnpm --filter website test:e2e   # Playwright against `vite preview` on :4173
pnpm --filter website lint
```

`WEBSITE_API_URL` repoints the dev and preview proxy (default `http://localhost:8000`).

## The pages

| Page | Served at | What it is |
|---|---|---|
| `src/index.html` | `letsrebase.com/` | The rebase landing: two doors into the hub, how it works, the four voices, the perks. Since 2026-09-11 (ORB-145) |
| `src/pigrocrm.html` | `/pigrocrm` | PigroCRM's own page (ORB-159): one door into rebase beside a drawn Claude conversation, the four things inside, the guide, the closing box. Its own `pigrocrm.css` on top of `landing.css` |
| `src/privacy.html` | `/privacy` | Privacy notice |
| `src/terms.html` | `/terms` | Terms |
| `src/pitch.html` | `/pitch` | The pitch deck, nineteen slides with keyboard, swipe and wheel navigation; shared by link, `noindex`. Its own stylesheet, `pitch.css`; its pictures under `src/pitch/` |

The community page (its own signup form, at `/community`, `/orbiters` before
REB-212) is gone (REB-72): both names 301 to `/` now, a week after the landing had
held the front door long enough that nothing still pointed people at the old one. The
hub owns every signup since 2026-09-09 (REB-17), so the page's own form was not
carried forward.

The landing's two calls to action point at `/hub/freelance` and `/hub/aziende`,
implemented in the rebase hub's API (`projects/hub/apps/api`) and reached on the same
origin. The landing's two calls to action point at `/hub/freelance` and `/hub/aziende`,
the hub's wizards, on the same origin again. Those paths are the things this project
does not own, and why the dev server proxies `/api` and leaves `/hub/` alone.

## Measurement

Two trackers, and one door for both: `src/consent.js` shows the cookie notice and
injects the ChatGPT Ads pixel and PostHog only after «Va bene». Before that click no
page requests either host; a refusal is remembered. `src/pixel.test.ts` is the rule book
(which pages may carry a tracker, that no page carries one in its markup, that the
PostHog literals equal `shared/analytics`), `src/consent.test.ts` drives the gate in a
DOM, and `e2e/site.spec.ts` watches the network. The policy pages describe both in
`privacy.html` § Cookie e misurazione. Design: `docs/design/2026-09-12-posthog-analytics-design.md`.

## Colour, typeface and the mark

All three come from [`shared/brand`](../../shared/brand), and none of them may be
restated here:

- **Palette.** `src/palette-plugin.ts` reads the seven shared tokens out of
  `@rebase/brand/palette.css` at build time and prepends them to its two consumers
  (`landing.css`, `pitch.css`) as plain custom properties. The
  application consumes the same file as part of its Tailwind theme. A hex pasted into a
  stylesheet here is the fork both mechanisms exist to prevent, and the plugin fails the
  build if the palette stops being extractable.
- **Typeface.** Outfit, self-hosted, declared once in `@rebase/brand/font.css` and
  prepended the same way. Nothing is fetched from a CDN, on purpose: PigroCRM is sold
  on self-hosting, and a webfont request hands every visitor's IP to a third party.
- **The mark.** The four tiles are `.glyph` in `src/system.css` here, used by
  `landing.css` alone now that the community page is gone (REB-72), a second, larger
  drawing of the same four colours in `pitch.css` for the deck's own chrome, and
  Tailwind classes in the application's `BrandMark.tsx`. The first and third assert
  their order against
  `@rebase/brand/mark`, so those two cannot drift.

## How it is served

Its own container. `Dockerfile` builds the pages into an nginx image, `docker-compose.yml`
runs it on 127.0.0.1:8082 (8083 for preview), and `deploy/nginx.conf` inside the image
holds the path map: which extensionless path is which file, and a 404 for anything
else. That map and `src/path-map-plugin.ts` say the same
thing twice, once for production and once for the dev and preview servers. Change one
and change the other: `path-map-plugin.test.ts` reads `nginx.conf` and fails until you
have.

`robots.txt` (REB-109) and `sitemap.xml` (REB-110) are a third kind of build output,
next to the six pages and the hashed assets: `src/path-map-plugin.ts`'s own
`writeBundle` hook writes both straight into `dist/`, since neither has an HTML input
in `vite.config.ts` to build it from. `GENERATED_PATHS` in that file lists them, and
is where the next file made this way goes.

`deploy/letsrebase.conf` is the host's vhost: it terminates TLS and sends everything
here except what belongs to the other tenants of the origin: `/hub/`, `/api/hub/`,
`/api/community/signups` and `/health` go to the rebase hub (`projects/hub`), and
`/app` and `/app/` redirect to `pigro.letsrebase.com`, which is the CRM.

Deploys are `deploy-website.yml`: preview on a push to `main` that touched this project,
production on a `website-v<semver>` tag. Until 2026-09-09 this project had no deployable
of its own and was carried inside the CRM's web image; the four public paths that
answered on `pigro.letsrebase.com` are now 301s to this site, so a policy page has one
canonical copy.
