---
name: posthog-analytics
description: Use when a change touches what this monorepo sends to PostHog or reads from it: adding or renaming an event in the website, the CRM, the hub or the MCP servers, sending an event from an API server, counting signups, activations or funnel steps, answering «how many people did X», exposing a database table to PostHog's data warehouse, or diagnosing a sync that failed, a count that looks too low, or an event that never arrives. Triggers on «PostHog», «analytics», «funnel», «warehouse», «evento», «tracciamento», «quante persone», «sync failed», and on any new `*_POSTHOG_*` setting.
---

# PostHog in this monorepo

One PostHog project (Cloud EU, team 272585, timezone Europe/Rome) receives every
surface: the website, the CRM, the hub, the CRM's MCP server, and since REB-215 the hub's
API. The record is `docs/design/2026-09-12-posthog-analytics-design.md`; the server side
of the warehouse is `docs/adding-a-project.md` § 7 «What PostHog's warehouse reads»;
the package is `shared/analytics` (its README says why it exists). Where this skill and
those documents disagree, the document is right and the skill has a bug. What this
skill adds is the order of operations and the traps that cost a wasted afternoon.

## The map

| Surface | How PostHog gets in | Where events are named |
|---|---|---|
| `projects/website` | PostHog's loader inside `consent.js`, **after the visitor's yes**; no npm dependency; the key is a literal that `pixel.test.ts` compares with the package | `community.js` (`iscrizione_community`) |
| `projects/pigrocrm/apps/web` | `initAnalytics` from `@rebase/analytics/browser` in `main.tsx`, text masked in replay | the middleware table in `src/lib/analytics.ts`: a successful API call earns an event (`spazio_creato`, `fattura_emessa`, …) |
| `projects/hub/apps/web` | the same `initAnalytics`, inputs masked | `src/lib/analytics.ts`: `useWizardAnalytics` (`wizard_iniziato`, `wizard_passo`, `wizard_completato`), `guida_scaricata` in `pages/member/Area.tsx` |
| `projects/hub/apps/api` | `rebase_core.analytics` (`Tracker`), a background task after the row is committed | `iscrizione_completata` |
| `projects/pigrocrm/apps/mcp` | `posthog.mcp.instrument` with `context=False` and `enable_exception_autocapture=False` | PostHog's own `$mcp_tool_call` and friends |
| `projects/hub/apps/mcp` | not instrumented on purpose: its only users are admins | — |

The key (`phc_…`) is public and committed in `shared/analytics/posthog.ts`; a second key
or host is a fork of the person model and the 2026-09-12 DECISIONS row forbids it. A
personal key (`phx_…`) never enters a file, a card or a chat transcript.

## An event's shape

- **Name**: Italian, snake_case, a past participle of what the person did
  (`cliente_creato`, `iscrizione_completata`, `guida_scaricata`), or `wizard_passo` for
  a step. English and `$`-prefixed names belong to PostHog.
- **Properties**: `tipo` for the kind (`freelance` / `azienda`), `via` for who sent it
  (`server`, `ui`), `passo` / `passi` for a step, the `utm_*` and `origine` the row was
  stored with. Never a name, an address, a rate or anything that names a person: the
  identify call carries those, once, behind a login.
- **Identity**: behind a login the browser calls `identifyUser(<uuid>, {email, nome})`,
  so a server event for that person is captured on `distinct_id=str(<uuid>)` and needs
  no flag. An anonymous visitor stays anonymous (`person_profiles: 'identified_only'`):
  the browser posts its own id with the write (`distinctId()` from
  `@rebase/analytics/browser`, a form field bounded to 200 characters, never stored), the
  server captures on that id with `$process_person_profile: false`, and when the browser
  sent none the server invents a UUID so the event still counts.
- **Two halves, one count**: a browser event stays the last step of the client-side
  funnel; the server's event is the total, under a name of its own
  (`guida_scaricata` in the browser, `guida_consegnata` from the API), because a name
  shared by the two halves doubles every naive count. Never quote a browser count as a
  total: on 2026-09-14/15 three profiles out of seven reached the hub with no browser
  event at all.
- **Identified person, no flag**: `$process_person_profile: false` is for the anonymous
  case only. A server event for a member goes on `str(member.id)` with no flag, so it
  joins the profile the browser's `identifyUser` created and inherits its properties
  (`$internal_or_test_user` included); with the flag it would be an event nobody can
  filter by person.

## Adding a browser event

1. Name it in the surface's `analytics.ts` (the hub's hooks, the CRM's route table);
   nothing calls `posthog` directly and no feature file imports `posthog-js`.
2. Call `capture` from `@rebase/analytics/browser`. It is a no-op on localhost and under
   a test runner, so the feature never checks whether analytics is on.
3. Tests mock the package once at the top of the file:
   `vi.mock('@rebase/analytics/browser', () => ({ capture: vi.fn(), identifyUser: vi.fn(), resetUser: vi.fn(), distinctId: vi.fn(() => 'anon-1') }))`
   and assert on `vi.mocked(capture).mock.calls`. A wizard that keeps a draft in
   `localStorage` needs `beforeEach(() => window.localStorage.clear())` too: jsdom keeps
   it across the tests of one file.
4. Write the event into the design doc's section for that surface and, for a new kind
   of measurement, one DECISIONS row.

## Adding a server event (the hub)

1. A method on `Tracker` in `projects/hub/packages/core/src/rebase_core/analytics.py`,
   the same `try/except → False` shape as `application`: a capture that raises is not
   the person's problem. Unit test in `projects/hub/packages/core/tests/test_analytics.py`
   with a recording callable, asserting the exact `(event, distinct_id, properties)`.
2. In the route: `background: BackgroundTasks` and `tracker: TrackerDep`
   (`rebase_api.deps`), `background.add_task(tracker.method, …)` **after** the service
   committed, guarded by `if tracker is not None`. The API's test `client` carries no
   key, so a test overrides `get_tracker` with a `Tracker(recording_capture)`; the
   `tracked` fixture in `projects/hub/apps/api/tests/test_hub_api.py` is the model.
3. **A new `REBASE_*` setting needs three edits, not one**: `rebase_core/config.py`,
   `projects/hub/.env.example`, and the `x-api-environment` anchor in
   `projects/hub/docker-compose.yml`. The container receives only the variables that
   anchor names; `hub-v0.23.0` shipped the tracker with the key in the server's `.env`
   and `printenv` inside `rebase-api-1` was empty until `hub-v0.23.1` (PR #123) added
   the two lines. The CRM has the same anchor and the same rule under `PIGROCRM_*`.
4. The value itself is written by hand on the server, in `${DEPLOY_PATH}/.env`
   (`/opt/hub/.env`, `/opt/hub-preview/.env`): the deploy never rsyncs a `.env`. The
   container reads it at start, so the next tag deploy or `docker compose up -d` picks
   it up. Verify inside the container, not on the host:

   ```bash
   ssh orbiters 'sudo docker exec rebase-api-1 sh -c "printenv REBASE_POSTHOG_KEY | cut -c1-8"; \
     sudo docker exec rebase-api-1 uv run --no-sync python -c \
     "from rebase_core.config import get_settings; from rebase_core.analytics import tracker_from_settings, shutdown; print(tracker_from_settings(get_settings()) is not None); shutdown()"'
   ```

5. Preview's API sends too. A server event captured on a browser id lands on a person
   the preview browser already marked `$internal_or_test_user`; one captured on an
   invented id cannot be told from production. Say so on the card when it matters, and
   leave the key out of preview's `.env` if a measurement cannot afford it.

## Reading it back

The PostHog MCP (`mcp__plugin_posthog_posthog__exec`) is the way in from a session. The
OAuth grant it asks for on first use can be completed from Chrome, «Read-only» is enough
for every query, and a write (a source edit, a schema reload, a cohort) needs a
reconnect with that scope, which no browser login changes. The `querying-posthog-data`
skill has the general method; these are the project's rules on top of it:

- **Bound every query on `timestamp`**. For browser events filter
  `properties.$host = 'letsrebase.com'` (or `pigro.letsrebase.com`): the previews report
  under their own hosts and the old `joinorbiters.com` events are still there. A server
  event carries no `$host` (`via = 'server'` is its mark) and a `$host` filter drops it:
  tell preview apart there by `person.properties.$internal_or_test_user`, which the
  preview browser set on the same person. Count people with `uniq(person_id)`, never
  `distinct_id`; show times with `toTimeZone(timestamp, 'Europe/Rome')`, and remember
  that the hub's `created_at` is UTC when you line the two up.
- **`read-data-schema` under-reports.** Its event list omitted `$autocapture` and
  `$pageleave` while both were flowing, and it lists events not seen in 30 days as
  reference only. When a name is missing, `SELECT event, count() … GROUP BY event` for
  the last week before concluding the event does not exist.
- **The landing is blind before consent.** The website loads PostHog only after the yes;
  the SPAs load it always. A funnel from `/` to `/hub/freelance` therefore counts only
  consenting visitors, and on 2026-09-15 two of ten wizard visitors had a landing
  pageview. Start funnels at the SPA's first pageview or say the top is missing.
- **`wizard_passo` fires on every step shown, backward moves included**, so «last passo
  per person» is not «where they left»; take the first in-order occurrence of each step
  (a `FunnelsQuery` does) or `max(passo)` per session with the completion joined.
- **Ground truth is the product's own database.** Before quoting a signup count, read
  `mcp__rebase-hub__list_talenti` (`stato="lead"` for the bare sign-ups, REB-288's
  removal pass folded the old `list_signups` and `list_freelancers` into it; the
  warehouse tables below work too) and say how many the events missed. Those rows are
  other people's data: counts and patterns travel, names and addresses do not.
- Live checks from Playwright: PostHog drops events when `navigator.webdriver` is true,
  so `posthog.set_config({ opt_out_useragent_filter: true })` after load; ingestion is
  visible after about a minute. Dashboards 952104 (site), 952105 (activation), 952106
  (MCP).

## The warehouse

Two Postgres sources (prefixes `hub`, `pigro`) reach the production containers on the
server's loopback (55435, 55432) through the `posthog` SSH user with a key pair, «Require
TLS through tunnel» **off**, as role `posthog_ro`. A synced table has two names, both
real: `postgres.hub.<table>` is what HogQL resolves (the schema's `hogql_name`) and
`hubpostgres_<table>` is the label the UI and `external-data-sources-list` show; a
`SELECT count() FROM postgres.hub.<table>` settles any doubt. The runbook in
`docs/adding-a-project.md` § 7 has the tunnel, the role and the rotations; what follows
is the day-to-day.

**A table PostHog should see.** `GRANT SELECT ON public.<table> TO posthog_ro` in the
production database (grants are per table; a migration never extends them, and the role
has no default privileges), then on the source's Schemas tab «Pull new schemas», enable
the row, pick the sync method (full refresh for small tables whose rows change in place),
«Sync all enabled schemas» → «Sync now». Add the table to the runbook's list. Sessions,
tokens and admin users stay ungranted. A table that holds a credential, even hashed
(`admin_tokens.token_hash`), gets a DECISIONS row and, if the answer is yes, a view
that leaves the column out (`CREATE VIEW <table>_posthog AS SELECT <the other columns>`,
`GRANT SELECT` on the view), created by hand in the production database like every
grant: the sync reads whole rows, so a column-level grant fails it, and a view in a
migration would need a role the local databases do not have.

**A table or column a migration moved.** The same failure as the database rename below,
from inside this repository, and it has already happened once: the identity merge renamed
`member_logins` to `logins` (REB-281, migration 0012) and PostHog paused that sync nine
days later, because a `GRANT` travels with a rename and nothing here refuses anybody.
Before a migration that renames or drops a table or a column merges, read the runbook's
list and say what the sync becomes; `projects/hub/packages/core/tests/test_warehouse_contract.py`
fails on a renamed or dropped table and on a new binary column in a synced one, so the
pull request asks before the email does. Three shapes, three answers:

- **a synced table renamed or dropped**: schema refresh on the source, enable the sync on
  the new name, delete the old table's rows in PostHog, move the name in the runbook and
  in `WAREHOUSE_TABLES`. No grant to write: the old one followed the table;
- **a column moved out of a synced table**: nothing fails. The data simply stops arriving
  and every dashboard that read it goes quietly empty, which is what the identity merge
  did to `nome`, `cognome`, `email` and `linkedin_url` when they moved into `users`, a
  table deliberately ungranted. Say so on the card, and treat granting the new table as
  the decision it is, not as a repair;
- **a binary column added to a synced table**: a source selects every column unless a
  person unchecks it in «Columns», so the file leaves with the next sync.
  `freelancers.cv_bytes` is the live case and is listed in `BINARY_COLUMNS_DECIDED`
  because production holds it today, not because it is settled.

**A database or role that moved.** The source stores the database name: the
`orbiters` → `rebase` rename of 2026-09-15 left the hub source failing with «Something
this sync depends on … no longer exists» and its tables switched off. The fix is on the
source's Configuration tab: change the field, leave password and private key blank (a
blank secret keeps the stored one; «Source updated» confirms), then re-enable the rows
and sync. Read the source's `job_inputs` back through the MCP to confirm.

**Reading a failure.** `external-data-sources-list` gives `latest_error` per source and
per schema; a schema with `should_sync: false` and a `Failed` status was switched off
by the failure and needs the toggle back on after the cause is gone. «no longer
exists» is a name (database, table, column) or a grant; an SSH sentence is the tunnel
(`sshd -T -C user=posthog` on the server prints the effective `permitopen`); a TLS
sentence is the switch above. Never «resync» first: it discards the synced data and
answers nothing the reload would not.

## Red flags

| Thought | Reality |
|---|---|
| «The count from PostHog is the number of signups» | It is the number of browsers that could reach PostHog. Read the hub. |
| «I put the key in `.env`, the deploy will pick it up» | The compose anchor names what the container sees. Three edits, then `printenv` inside the container. |
| «`read-data-schema` did not list it, so the event does not exist» | It omitted `$autocapture` while it flowed. Count it in SQL. |
| «I'll add `properties.host` to the server event to tell preview apart» | Invented property nobody else sends. Use the browser's id and `$internal_or_test_user`, or keep preview's key empty. |
| «The table is in the migration, PostHog will see it» | Grants are per table. `GRANT SELECT`, then «Pull new schemas». |
| «The sync failed, I'll resync from scratch» | Fix the name or the grant, reload, and only wipe when the data itself is wrong. |
| «The MCP can update the source» | Only with `external_data_source:write`; the read-only grant does the queries, the UI does the edits. |
