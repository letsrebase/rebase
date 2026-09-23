# PostHog on every surface

Ivan asked on 2026-09-12: «vorrei aggiungere posthog per monitorare come viene utilizzata
la piattaforma, sia il crm che il sito web che tutte le superfici pubbliche», then «mi
interessa anche l'uso sul sito, quanti scaricano la guida e come e cosa cliccano sul
sito», then to connect the Postgres data too. This document is the design for all of
it; the work is five Linear cards, one per surface (ORB-183 website, ORB-184 CRM web,
ORB-185 hub, ORB-186 CRM MCP, ORB-187 warehouse sources and dashboards).

## What was decided, and by whom

Ivan, on 2026-09-12, from the options put to him:

- **PostHog Cloud EU**, one project for every surface. Data in Europe; a visitor of
  letsrebase.com who later opens a space on pigro.letsrebase.com is one person.
- **Consent gate on the site, identified tracking behind the logins.** The public
  pages load nothing before the visitor says yes, through the notice that already
  exists for the ChatGPT Ads pixel. Inside the CRM and the hub, after the login, there
  is no banner: the person is identified and the privacy page says so.
- **What is collected:** pageviews, autocapture, explicit events for the activation
  funnel, session replay. Server-side events from the API are left out of this
  version, except the MCP server, which is server-side by nature.
- **Identity:** the user's id as `distinct_id`, with email, name and role as person
  properties, and the space as a group. Ivan chose to send the email and the name.
- **Products enabled in PostHog:** Product Analytics, Web Analytics, Session Replay,
  MCP analytics. Not Metrics (infrastructure metrics, which nothing here emits; Ivan
  uses another APM), not Feature Flags, Experiments, Surveys, Support.

## One project, one key, one place

The PostHog project key (`phc_...`) is public by design: it ends up in every bundle
and every page that measures. It is committed, the way the pixel id already is, in a
new workspace package **`shared/analytics`** (`@orbiters/analytics`), because it is
the one value every surface must agree on. The package holds:

- `posthog.ts`: the key, the ingestion host (`https://eu.i.posthog.com`), the asset
  host (`https://eu-assets.i.posthog.com`), `INTERNAL_HOSTS` (the preview hostnames)
  and `analyticsEnabled(hostname)`, which is false on `localhost`, `127.0.0.1` and an
  empty hostname, so a developer's machine and a test (jsdom's default location is
  `localhost`) never send an event.
- `browser.ts`: the one `initAnalytics(...)` the two SPAs call, on top of `posthog-js`.
  It applies the shared policy (pageviews on history change, autocapture, replay with
  inputs masked, `maskText` for every text/attribute/autocapture property on request
  (both surfaces ask, REB-274), `person_profiles: 'identified_only'`, preview marked as
  internal) and exposes `identifyUser`, `identifyGroup`, `resetUser` and `capture` as
  thin wrappers that are no-ops when analytics is off.

The website does not import the package at runtime (see below). Its literal copy of the
key and hosts in `consent.js` is compared with the package's exports by
`pixel.test.ts`, which has the package as a dev dependency for that one import: the
same idiom `path-map-plugin.test.ts` uses to hold nginx.conf and the Vite plugin
together.

A personal API key (`phx_...`) is a different thing: it writes to the whole PostHog
account. It lives nowhere in this repository, in no `.env.example`, and in no card.

## The site (ORB-183)

No npm dependency: `projects/website/AGENTS.md` says a dependency here has to justify
itself against the page weight, and a tracker fetched before consent is the thing the
notice exists to prevent. So PostHog's own loader snippet is written into `consent.js`
as `loadPostHog()`, called from the same two places `loadPixel()` is called: after a
stored `granted`, and after the click on «Va bene». Before that click the browser makes
no request to PostHog, exactly as with OpenAI, and a refusal is final until the visitor
clears the site's data.

The notice's sentence changes from «Solo un cookie di misurazione, per sapere se un
annuncio funziona» to one that names both things it asks about: the ad and how the site
is used. It stays under 130 characters, links `/privacy`, keeps `No` and `Va bene`.

Configuration on the site: `api_host` and `defaults` as PostHog's snippet gives them,
`person_profiles: 'identified_only'`, `capture_pageview: true`, autocapture on,
`session_recording.maskAllInputs: true`. Autocapture is what answers «cosa cliccano»:
every click on a link or button is recorded with its text and destination, so «Entra e
scaricala» and the two doors into the hub are counted without a line of code each.

One explicit event: `iscrizione_community`, captured in `orbiters.js` in the same place
the pixel's `registration_completed` is measured, after the API answered 201, and
guarded the same way (`typeof window.posthog.capture === 'function'`, in a `try`).

`privacy.html` and `termini.html` load neither the notice nor any tracker, as today.
`pitch.html` is not measured either: it is shared by link and `noindex`.

## The CRM (ORB-184)

`posthog-js` as a runtime dependency of `projects/pigrocrm/apps/web`, initialised once
in `main.tsx` through `initAnalytics` from `@orbiters/analytics` with
`capture_pageview: 'history_change'` (TanStack Router pushes history) and replay with
**every input and every text masked**: a recording of the CRM must show where the
person clicks and stops, never an invoice amount or a customer's name.

Identity follows `AuthProvider`: when `user` becomes non-null, `identifyUser(user.id,
{ email, nome, ruolo })` and `identifyGroup('spazio', slug)`, with the root
installation as `root` (from `lib/tenant.ts`); on logout, `resetUser()` before the
page leaves. No banner: the CRM is a service the person signed up for, and the privacy
page states what is recorded.

The funnel events are not added feature by feature. `lib/api.ts` is the one
`openapi-fetch` client, and it takes a middleware: `lib/analytics.ts` registers one
that turns a **closed table** of successful calls into named events, so a feature file
never mentions analytics and an action counts only when the API said yes.

| Call | Event |
|---|---|
| `POST /api/tenants/` | `spazio_creato` |
| `POST /api/auth/entra` | `entrato_con_link` |
| `POST /api/customers` | `cliente_creato` |
| `POST /api/customers/from-suggestions` (the Gmail proposals ticked, REB-223) | `clienti_importati` |
| `POST /api/deals` | `deal_creato` |
| `POST /api/documents` | `documento_creato` |
| `POST /api/documents/from-template` | `documento_creato` |
| `POST /api/time-entries` | `ore_registrate` |
| `POST /api/invoices/{invoice_id}/issue` | `fattura_emessa` |
| `POST /api/tokens` | `assistente_collegato` |
| `PUT /api/settings/emitter` (the emitter profile) | `profilo_emittente_salvato` |

The exact path of the last row is read from `api-types.ts` when the card is
implemented; the table is the contract. «Primo cliente» and «attivato entro sette
giorni» (spec `projects/pigrocrm/docs/superpowers/specs/2026-09-12-onboarding-product-led-design.md`
§4) are funnels PostHog computes from these events and the `spazio_creato` timestamp;
the code does not decide what «first» means. Since REB-223 a space's first customers can
arrive in one batch from the Gmail proposals, which earns one `clienti_importati` and no
`cliente_creato`: a «primo cliente» funnel counts either event.

## The hub (ORB-185)

The same `initAnalytics`, replay with inputs masked (the wizards are forms), pageviews
on history change. Events, in the wizards and the member area:

- `wizard_iniziato`, `wizard_passo` (with `passo`, the step index, and since REB-122
  `schermata`, the screen's own id) and `wizard_completato`, each with `tipo`
  (`freelance` or `azienda`) and, when present,
  `perk` from the query string (`?perk=guida` is how the site's guide section arrives).
  `wizard_passo` is what makes «where do they leave» (ORB-122) a funnel, and the
  milestone «Three screens instead of eight» needs that baseline before the grouping.
  It fires for every step the engine shows, backward moves included, so a funnel takes
  the first in-order occurrence of each step and a «last `passo` per person» reading is
  not «where they left».
- `guida_scaricata`, on the click of «Scarica la guida» in `pages/member/Area.tsx`. The
  PDF is served by `GET /api/hub/me/guida` behind the member cookie, so this is the only
  place a download can be counted; the site only links to the wizard.
- Identity: the member (`id`, email, nome) when the area loads, the admin when the
  admin area loads; `resetUser()` on either logout.

The hub's own MCP server is not instrumented: its only user is Ivan.

**Amended 2026-09-15 (REB-215).** «Server-side events from the API are left out of this
version» stops holding for one event: `iscrizione_completata`, sent by the API from
`rebase_core.analytics` after `POST /api/hub/freelancers` and `/companies` answer, on
the browser's own distinct id (posted with the application) and with
`$process_person_profile: false`. The first two days of `wizard_*` showed why: three of
seven profiles reached the hub with no browser event at all. `wizard_completato` stays
as the client-side funnel's last step; the server event is what counts completions.

**Amended 2026-09-17 (REB-274).** «Replay with inputs masked (the wizards are forms)»
stopped holding the day the admin area shipped: it renders every freelancer's and
company's name, email, rate, LinkedIn URL and links as plain text, and a `mailto:` or
LinkedIn anchor's own `href` carries the address regardless of what its text shows.
The hub now asks `initAnalytics({ maskText: true })`, the same as the CRM, which
`browser.ts` turns into three separate PostHog switches: `session_recording`'s
`maskTextSelector`/`maskAllElementAttributes` (replay's own text and DOM attributes)
and the top-level `mask_all_text`/`mask_all_element_attributes` (autocapture's
`$el_text`/`attr__href` properties, a path replay's own masking never reaches). The
wizard funnel events (`wizard_iniziato`, `wizard_passo`, `wizard_completato`) are
unaffected, since they are explicit `capture` calls, neither replay nor autocapture.

**Amended 2026-09-22 (REB-122).** `passo` alone stopped being enough to read a funnel
by screen once REB-120/121 were scoped to regroup the wizards' fields onto fewer
screens: the same numeric `passo` would mean a different screen before and after, so a
baseline read today would go stale the moment the regroup ships. `wizard_passo` now
also carries `schermata`, the screen's own id (`Screen<T>['id']`, a field's id today,
one field per screen until the regroup), so the pre-regroup rows stay self-describing
for as long as PostHog keeps them, independent of which commit's field order applied
when they were captured. `wizard_iniziato` and `wizard_completato` are unchanged: they
carry no step at all.

## The CRM's MCP server (ORB-186)

`posthog` (the Python SDK, 7.40 or later, which knows `mcp` 2.x and `MCPServer`) as a
dependency of `projects/pigrocrm/apps/mcp` only. `packages/core` does not gain it:
`test_architecture.py` reads core's dependency list literally and that is the point.

In `build_server`, when `PIGROCRM_POSTHOG_KEY` is set: `posthog.mcp.instrument(mcp,
client, ...)` with a callback that reads the actor of the current call and answers the
user's id as `distinct_id` and the space slug as the `spazio` group. The wrapper emits
`$mcp_tool_call`, `$mcp_tools_list` and `$mcp_initialize` with the tool name, the
parameters, the response, the duration and the error flag. An empty key means no client
is built and nothing changes. The key is forwarded by `docker-compose.yml` with an
empty default and documented in `.env.example`; it is the same public project key, read
from the environment rather than imported, because the MCP runs in the API image and a
self-hosted CRM must be able to leave it empty.

This card lands after ORB-170 (the HTTP transport), because the instrument call has to
sit where both transports pass and the identity callback has to read the per-request
actor that ORB-170 introduces.

## The tables behind the events (ORB-187)

PostHog's Postgres source reads one database through an SSH tunnel, authenticated with
a key pair generated for it (the private half lives in PostHog's source configuration,
the public half on the server) and with «Require TLS through tunnel» off, since the
containers speak plain Postgres and the tunnel is the encryption. Two sources:

- the hub's database (`orbiters`): `signups`, the freelancer and company applications,
  the members. This is the table side of the site's funnel.
- the CRM's registry database: `tenants` (slug, owner, created_at). This is the table
  side of the activation funnel.

The CRM keeps one database per space, so the spaces' own tables (customers, invoices)
are not connected: one source per space does not scale, and the activation funnel is
read from events. On the server: a `posthog` SSH user whose only permitted action is
the port forward to the two loopback ports, and a `posthog_ro` role in each database
with `SELECT` on the tables above and nothing else. The procedure, with no secret in
it, is the closing comment of ORB-187 and the runbook paragraph it adds to
`docs/adding-a-project.md` § 7.

Dashboards, once events flow: the site (pageviews, clicks by CTA, the funnel CTA →
wizard → link → guide), activation (spazio creato → primo cliente → attivato a 7
giorni, and «assistente collegato»), MCP usage (calls by tool, errors, duration).

## Privacy and the record

`privacy.html` § Cookie changes with the first card: the paragraph that says the CRM
has «nessuna analitica» becomes false the day ORB-184 ships, so it is rewritten now to
say that inside the CRM and the hub PostHog records pages, clicks and sessions of the
signed-in person, with which data and on what legal basis, and that on the site PostHog
arrives through the same yes as the pixel. `docs/design/DECISIONS.md` gets one row for
the rule: **a tracker on this repository's surfaces is PostHog, one project, configured
from `shared/analytics`; on the public pages it enters through `consent.js` and nowhere
else; behind a login it identifies the person and the privacy page says so.**

## Verification

- Website: `pixel.test.ts` and `consent.test.ts` extended (nothing fetched before a
  yes, the loader inside the accepting branch, the literal key equals the shared one,
  privacy and termini clean); `e2e/site.spec.ts` names PostHog's hosts next to the
  pixel's; the weight budget excludes them the same way.
- CRM and hub: unit tests on the middleware table and on identify/reset following the
  session; the shared package tests its own policy with `posthog-js` mocked.
- MCP: the server builds with and without a key, `tools/list` answers, the wrapper sees
  a call.
- Live: after each deploy, the event appears in PostHog's activity view for that
  surface, checked by hand and written on the card.
