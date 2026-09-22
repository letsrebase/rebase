# website: what to know before changing it

letsrebase.com. Read `README.md` here first for what the pages are and how to run
them; this file is the part that is easy to get wrong.

Tracker: the **Website** project in Linear, conventions in `docs/tracker.md` at the
repository root. Everything in the repository is written in English, product copy
excepted: these pages speak Italian to their visitors and that is what they should keep
doing.

## No framework, and that is the requirement

No React, no Tailwind, no router, no CSS framework. Six HTML pages, six scripts,
five stylesheets, and a build that takes about 300 milliseconds. This is the first
thing a visitor loads and it must not drag an application bundle behind it. A dependency
added here has to justify itself against that, and "the CRM already uses it" is not a
justification: the CRM is behind a login and this is not.

## A tracker enters through `consent.js` or not at all

The ChatGPT Ads pixel and PostHog are both injected by `src/consent.js` after the
visitor's yes; neither sits in a `<head>`, and `pixel.test.ts` fails the page that tries.
PostHog's key and hosts are literals there because the file runs without a bundler, and
the same test compares them with `shared/analytics`, the source every other surface
imports. Change the key there first, then here.

## Colour, typeface and the mark come from `shared/brand`

Never restate them. `src/palette-plugin.ts` reads the shared tokens out of
`@rebase/brand/palette.css` at build time and prepends them to the stylesheets; the
CRM consumes the same file through its Tailwind theme. A hex typed into a stylesheet
here is the fork both mechanisms exist to prevent, and the plugin fails the build when
the palette stops being extractable rather than shipping pages with no colour.

The token count in `EXPECTED_TOKEN_COUNT` is asserted deliberately: adding a token to
the shared palette without deciding whether these pages need it is a failing test, not a
silently thinner site.

## The page names still say "landing", on purpose

`src/landing.css`, `src/landing.js` and the three `landing-*.test.ts` files are named
after the landing page, `index.html`, which is PigroCRM's own page and shares its
stylesheet with `/privacy` and `/terms`. The **project** was renamed from `landing` to
`website` on 2026-09-09 because it is the whole site; the page inside it did not go
anywhere. The community page, `src/community.*`, is gone (REB-72): `/community` and
its own old name `/orbiters` (REB-212, 2026-09-15) both 301 to `/` now.

## What this project does not own

`/api/community/signups` is the rebase hub's, implemented in `projects/hub/apps/api`,
and reached on the same origin; nothing in this project calls it any more since the
community page's own signup form was retired (REB-72). The dev and preview servers
still proxy `/api` so it can be exercised directly; `WEBSITE_API_URL` repoints it.

Serving is owned here since 2026-09-09: this project builds its own image and runs its
own container. What that means in practice is that the path map exists twice, in
`deploy/nginx.conf` for production and in `src/path-map-plugin.ts` for the dev and
preview servers. `path-map-plugin.test.ts` reads the first and compares it with the
second, so a page added in one and not the other is a failing unit test rather than a
site that passes the suite and 404s in production. The e2e suite runs against `vite
preview`, so it checks the second copy on a real server; the first is checked by
`website-image` in preflight and by the deploy's own health check. The one place the
two deliberately differ is the paths the host vhost gives to other tenants of the
origin (`/api`, `/hub`, `/app`, `/health`): the container 404s them, the Vite servers
proxy `/api` and answer a plain-text stand-in for the rest, because they cannot run
the CRM or the hub and a redirect into production would make the suite depend on it.

## Verification

`pnpm --filter website lint | test | build | test:e2e`, all fast. The e2e suite runs
Playwright against `vite preview` on the fixed port 4173, which is why its preflight
check is `serial: true`. A change to the rendered pages is not done until you have run
it, and a visual claim needs a render, not a description.

**Two checkouts cannot run that preview at the same time, and it fails loud rather
than quiet.** `preview.strictPort` is `true` in `vite.config.ts`, so a second `pnpm
preview` (or a second `pnpm test:e2e`, which chains `build && preview` itself) on a
box already running one exits immediately: `error when starting preview server:
Error: Port 4173 is already in use`, confirmed live by running `pnpm exec vite
preview` against this project while another checkout's preview already held 4173.
Three agents hit this on 2026-09-10 running the same day's issues in parallel
checkouts. Wait for the other preview to exit, or run one checkout's e2e suite at a
time; do not edit `vite.config.ts` or `playwright.config.ts` to move the port
instead, and if you do to unblock yourself locally, revert it before committing —
an uncommitted port override is easy to leave in.
