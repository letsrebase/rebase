# AGENTS.md — working on PigroCRM

The root [`AGENTS.md`](../../AGENTS.md) covers the monorepo: layout, the single
lockfiles, the CI contract, commit conventions. This file is only about this project.
Tracker: the **PigroCRM** project in Linear, conventions in `docs/tracker.md`. File
what you find, and close nothing you have not verified.

## What it is

An AI-first CRM for Italian freelancers, consultants and small startups. It covers
the whole cycle without changing application:

```
Contatto → Cliente → Deal → Offerta → Lavoro → Time tracking → Fattura → Analisi economica
```

The design record is `docs/superpowers/specs/`, in Italian, one document per slice.
Read the spec before rewriting something that looks odd: most of what looks odd here
was decided, and the reason is written down.

## The four principles that constrain the code

- **API first.** The UI uses only the public API. No logic exists in the frontend
  alone.
- **MCP first.** `apps/mcp` is not an adapter bolted on afterwards: it calls the same
  services the UI does, in process.
- **No duplication.** Every fact is stored once.
- **Single-tenant inside, several spaces outside.** No service knows about tenants.
  Signing up from the login gets you a *space*: a Postgres database of its own with
  the same schema, served under `/<name>/app` and `/<name>/api`. The root
  installation is unchanged. See
  `docs/superpowers/specs/2026-09-08-spazi-un-database-per-tenant-design.md`.

## Layout, and the one import rule that is enforced

```
packages/core/   the domain: models, services, migrations, rendering. Depends on neither adapter.
apps/api/        FastAPI. Imports core.
apps/mcp/        the MCP server, stdio and Streamable HTTP. Imports core.
apps/web/        Vite + React SPA, served under /app.
deploy/          the nginx vhost and the server setup script.
```

The public pages are **not here**: they are their own project, `projects/website`
(letsrebase.com and the pages the product signs itself with). Its built output is
still copied into this project's web image and served at the document root, which is
a serving arrangement and not a dependency of the application on it. The palette,
the typeface and the brand mark both surfaces use live in `shared/brand`.

**The UI comes from `@rebase/ui`, and a primitive is not written here.** The token
layer and the eighteen shared primitives live in that package (REB-299, REB-300):
`src/styles/tokens.css` is a three-line entry file that only fixes the import order
and declares nothing, and a component imports `@rebase/ui/button`, never a local copy.
Three generated files stayed under `src/components/ui/`, because nothing else renders
them: `avatar`, `command` and the `input-group` it composes. There is no
`components.json` in this application, on purpose: `shadcn add` is run in `shared/ui`
(`pnpm --filter @rebase/ui exec shadcn add <name>`), and a fourth local primitive here
needs a reason written down, not a generator flag. `shadows.test.tsx` holds those three
to the rule, and the package's `tokens.test.ts` and `e2e/gallery.spec.ts` hold the
system itself.

**`packages/core` may import neither adapter, and neither adapter may import the
other.** This is enforced twice and both are load-bearing: `ruff.toml`'s
`flake8-tidy-imports.banned-api` at the project level, narrowed by each adapter's own
nested `ruff.toml`, and `packages/core/tests/test_architecture.py`, which builds
core's allowed-import set **by reading `[project].dependencies` in
`packages/core/pyproject.toml` literally**. Adding a runtime import to core without
declaring it there fails that test, which is the point.

## Running it

From the repository root:

```
uv sync --frozen && pnpm install --frozen-lockfile
uv run pytest -q projects/pigrocrm/packages/core/tests   # narrow to what you touched
pnpm --filter web dev
```

`docker compose` runs from this directory, and its build context is the repository
root two levels up, because that is where the lockfiles are.

Four jobs run themselves once deployed, each one line in the deploy user's crontab:
`pigrocrm gmail-sync` and `pigrocrm reminders` every fifteen minutes, `pigrocrm automations`
every morning and `pigrocrm digest` every Monday at 08:00 Europe/Rome. All four walk the
root installation and then every space in the registry. None is a process this repository
starts on its own; the runbooks are `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`
(`gmail-sync` and `digest`), `docs/superpowers/notes/2026-10-26-reminders-cron-runbook.md`,
which also has the preview's line (`/opt/pigrocrm-preview`) and the healthchecks.io ping, and
`docs/superpowers/notes/2026-10-26-automations-cron-runbook.md`.

After each space a real run writes that space's row in `reminder_job_run`
(`core/reminders/jobs.py`, `JobClock`): the last run that ended and, when the latest attempt
failed, why. Impostazioni → Esecuzioni, `GET /api/scheduled-jobs`, the MCP tool
`describe_scheduled_jobs` and the reminder `job_late` read it. A fifth command is a new
entry in `SCHEDULED_JOBS` and a write through `JobClock`, not a second table.

## Things that will cost you an afternoon if you do not know them

**A route file in `apps/web/src/routes/` exports only `Route`.** Any other export turns off
the router's automatic code splitting for that route and pulls its whole page into the
entry chunk (it was 376 kB before the pages moved, 121 kB after). The page component lives
in `src/features/<x>/` with its test, and the route is a one-line wiring of it; ESLint's
`no-restricted-exports` fails the build otherwise.

**The document renderer shells out to Pandoc and Typst.** Roughly thirty tests fail
without both on `PATH`, with `strumento di composizione non installato`. The versions
that matter are pinned in `Dockerfile.api` — Pandoc 3.8.2.1 and Typst 0.14.2 — and
Typst's diagnostic format and the auto-typography workaround were verified against
0.14.2 specifically, not assumed stable across releases. CI installs the same pair.

**`cookie_secure` defaults to true and `docker-compose.yml` never overrides it for
the `api` container, deliberately.** On plain HTTP the browser silently drops the
session cookie: login answers 200 and every request after it is unauthenticated. If
you are debugging "the login works but nothing else does", this is it — and in
production it means TLS has to be configured *before* anyone tries to log in.

**The API's throttles trust `X-Real-IP`, so nothing but nginx may reach port 8000.**
`pigrocrm_api.ratelimit` keys its buckets on the client address nginx writes into
`X-Real-IP`; a deployment that publishes the API's port directly lets any caller mint a
fresh bucket per request by typing the header. Compose publishes none: keep it so. The
limiter is in-process and per worker (one uvicorn worker today), the floor under nginx's
`limit_req`, not a substitute. It covers the signup (per client, a global rate, a cap on
signups in flight and `PIGROCRM_MAX_SPACES`), login (per client, and a backoff on one
client trying one account), `/auth/verify`, `/auth/refresh`, `/identity/enter`, bad PAT
bearers, and the mails these public routes send (per recipient address). Refusals are
logged on `pigrocrm_api.security` with the scope and the address, never an email or a
token. The API refuses to boot with the published placeholder `PIGROCRM_JWT_SECRET` unless
`cookie_secure` is off, and an access token carries the space it was minted for (`aud`),
which `get_actor` checks: the root and every space share one signing secret.

**An agent credential does only what `core/agent_actions.py` classifies.** A personal access
token is `Actor(type="mcp")` on MCP and on every REST route alike, and `Actor.require_write`
and its siblings refuse it any action that is not in `AGENT_ALLOWED_ACTIONS` (what a tool does),
in `FULL_ACCESS_ACTIONS` with a token that carries `full_access` under the space's ceiling
(REB-810), or never: `PERSON_ONLY_ACTIONS` (accounts and tokens, the space settings, the Google
and Slack connections, mail in the owner's name, the shape of the CRM). An action in none of
the three is refused, so a new `require_*` string is closed to agents until it is classified
(`packages/core/tests/test_agent_actions.py` fails first), and a route with no service behind it
takes `person_only(action)`. Action keys are English snake_case; the Italian sentence a person
reads is a label in `ACTION_LABELS`. `apps/api/tests/test_role_matrix.py` walks every writing
route with an administrator's token and lists the ones it reaches (`AGENT_REACHABLE`).

**A module that is off is guarded once per surface, never by an `if` in a handler (REB-953).**
`core/modules/registry.py` decides whether `deals`, `contracts`, `time`, `costs` or `taxes` is on
(a stored `module_*` row over the profile's default, then the dependency `time` -> `deals`);
each surface asks it in one place. REST: `module_guard(...)` from `pigrocrm_api.deps`, listed
after `get_actor` on a router or on one route (a route across modules lists all of them), which
answers 404 `module_off` with the Italian sentence. MCP: `pigrocrm_mcp/modules.py::TOOL_MODULES`,
applied once by `prune_off_modules` in `build_server`; `tests/test_module_guard.py` fails for a
tool that is in neither that table nor its `CORE_TOOLS`, so a new tool is classified on purpose.
Jobs: `Detector.modules` for the reminders, `RULE_MODULES` for the automations, and the digest's
sections. A core read that spans modules (search, the calendar, the customer card) takes
`modules=` and leaves that part out. The profile and the switches are written only by
`SpaceModulesService` (`set_space_profile`, `set_space_modules`: unscoped admin, and for a token
`full_access`), never through `SpaceSettingsUpdate`. Switching off touches no record. The onboarding
(REB-957) is the same two actions plus `skip_space_onboarding` («Decido dopo» on the first step:
`describe_space` and `GET /api/settings/modules` answer `onboarding` as `pending`, `skipped` or
`done`, and choosing a profile is what makes it `done`); the web gate is `OnboardingGate` in
`routes/app.tsx`, and `e2e-setup.sh` answers it for the seeded space.

**A due item is written by `DueItemService` and nothing else (REB-967).** `due_items` and
`due_item_settlements` are core, never behind a module: an amount owed or to be received, in or
out, with the payments against it. The service has two halves told apart by the signature. `create`,
`drop`, `reschedule` and `attach_dependency` take no actor and never commit: the service that owns a source (an
invoice, a purchase, a tax) calls them in its own transaction. `settle`, `reverse_settlement`, `get`
and `list` take an actor and are what the routes and the four MCP tools call. A payment beyond what
remains is refused, a part-paid item stays `expected`, a reversal stamps the settlement and never
deletes it, and credits are negative items with negative payments. `due_date` is stored, written
only by the service from the due rule (`rules.py`) and re-dated whenever the item it depends on
settles, is reversed or is dropped; a dependency counts as settled only when settled in full. Who
may settle depends on the record, not the tool: an incoming ordinary item is a collaborator's, an
outgoing or fiscal one (`RESTRICTED_SOURCE_KINDS`) an administrator's and, for a token, `full_access`'s
(`settle_outgoing_due_item`; `FULL_ACCESS_BY_THE_RECORD` in `test_mcp_invoice_ban.py`). A settlement
and a dependent share the legal entity of their item by composite foreign key, so the policy of both
tables is the plain `legal_entity_visible(legal_entity_id)`. A source kind is a string validated in
code (`SOURCE_KINDS`), never a CHECK.

**Engagements with freelancers are the `collaborators` module and every read and write is an administrator's (REB-976).** `core/collaborators/` holds the `engagements` table (the CRM's name for the hub's match; `hub_match_id` is the import's idempotency key), the freelancer's identity (`organizations.hub_freelancer_id`, unique, only on an organization with the role `freelancer`, set by an admin and never changed; the fiscal kind is `supplier_kind` limited to `professional`, `flat_rate` and `occasional`) and `FreelancerService` (availability, `set_tax_data`). The module is **off** unless the space switches it on or already holds engagements (`Module.asks_onboarding`: an answer the onboarding does not ask yet reads as off even with no profile); `ensure-space-defaults` writes it off for every profile, and the release that moves the hub's matches is the one that writes it on for rebase's own space. `days_per_week` is 0.5 to 5 in half steps and null only on an imported engagement; availability counts **active** engagements only, a week touched by one with no `days_per_week` is `unknown` («sconosciuta») and never `free`, and a part-week counts in full. A new engagement is saved with a warning, never refused, when it overlaps a live one of the same freelancer (`overlap`, and `over_capacity` above five days a week). The payment rule is `fixed_term` (`payment_days` 1 to 60, 30 from month end) or `on_collection` (`collection_days` 1 to 60 and **no** fixed term: the printed clause names no floor); the schema holds both, and the due-date arithmetic and the letter are later cards. States and edges are `rules.TRANSITIONS`; once an engagement is out for signature only `days_per_week` and its client, deal and contract change. Cancelling, closing, creating and the tax data are `FULL_ACCESS_ACTIONS`; the signature that makes an engagement `active` is `activate_engagement`, a person's act. The table has no legal entity of its own: reads join the freelancer's organization so its row policy hides an engagement from a scoped admin.

**An issued invoice's due item is the truth, and the invoice's `payment_status` and `payment_date`
are its projection (REB-968).** Emission, import and `set_payment_state` call `DueItemService`
(`open_for_invoice`, `record_invoice_payment`, `drop_for_invoice`), and `settle` and
`reverse_settlement` project onto the invoice, so the two columns have no other writer. «Da incassare»,
the six buckets, the payment reminders, the `invoice_overdue` detector, the automation and the
digest join the invoice to its item (`due_items/sales.py`: `join_sales_item`, `remaining_amount`,
`still_owed`) and read its remaining amount and due date; the ceiling, `monthly_paid` and the
digest's «Incassate» read each settlement by its own date (the ceiling counts a settlement's share of
`taxable_amount`). The readers that want only the state (the list filter, the calendar, the year CSV)
keep the projected columns. A test that builds an issued `Invoice` row by hand gets its item from
`fakes/hand_built_invoices.py`, the stand-in for the boot step; `ensure-space-defaults` runs
`reconcile_sales_invoices` on every space and on the root: it gives each issued invoice without an item
its item (a paid one settled on its `payment_date`, else its issue date) and brings an item that disagrees
with its invoice back in step (`repair_invoice`: the old API marked it paid or unpaid, moved the date or
annulled it after the item existed), idempotently. The old API keeps serving while `migrate` runs, so
`pigrocrm reconcile-due-items` runs the same step again as the api container starts (compose `command`),
after the old one is gone, and may run from cron; it answers 1 when a space failed, while the boot step
follows the boot's convention (a line on stderr, exit 0).
**Staff and directors are written by `StaffService`, and pay is an administrator's before anything else
(REB-977).** `staff_members` and `payroll_settings` belong to the module `staff` (default off for both
profiles, on for a space with no profile): REST `/api/staff` carries `module_guard("staff")` and the
four tools (`create_staff_member`, `update_staff_member`, `get_staff_member`, `list_staff`) are in
`TOOL_MODULES` and registered in `tools/privileged.py`, so a token without `full_access` does not list
them. Every service method calls `require_admin` first, before it looks a row up, so a collaborator
or a readonly member learns neither pay nor whether a member exists; which legal entities an administrator
reaches is the plain `legal_entity_scope` policy. The method names are unique on purpose (`FORBIDDEN_SERVICE_CALLS`
finds the privileged tools by name). Eligibility reads the fiscal pack of the legal entity (`EMPLOYEE_PACKS`,
`DIRECTOR_PACKS` in `staff/models.py`): never `non-it`, directors only on `it-company`, a pack no legal
entity carries yet, so a director is refused until the taxes design ships it. The kind decides the columns and the
table's CHECKs say so; a rate left out is «not stated», never a default, because the payroll consultant verifies it.
The legal entity's pay day is REST only and person-only (`set_payroll_pay_day`); a member's own override is a field
of `update_staff_member`. Nothing is deleted: `end_date` is how someone leaves. The estimate, the payslips and the
screens are later cards.

**A router authenticates with `dependencies=[Depends(get_actor)]`, not per handler.** A
handler that never names the actor is still behind it, and `test_routes_authenticate.py`
asks every route of the OpenAPI document once with no credential. The open routes are the
session mechanics, the signup, the identity cookie, the service-token engagements and the
OAuth callbacks (`oauth_callback.finish_consent`, one helper for Gmail, Drive and Slack).
An upload takes the `Request` and checks the role *before* `read_upload` parses the
multipart body (`uploads.py`): a declared `File()` makes Starlette spool the body to disk
before any check. A list route declares one `*ListParams` model from `query_params.py`
(FastAPI takes a query model only as the route's single query parameter), and a file
download goes through `downloads.attachment_response`.

**A file that arrives goes through the inbox, and a route that takes a body needs its own nginx
location (REB-971).** `core/inbox/` stores every uploaded file as an `inbox_items` row (`InboxUploads`,
REST and web only: bytes never travel over MCP, so `InboxService`'s two reads are the only tools),
deduplicated by `content_sha256` and `natural_key` under an advisory lock, a `.p7m` opened with
`asn1crypto`, a ZIP kept as the parent of its entries. Nginx answers 413 to any body over 1 MiB
unless the route has its own `location` with `client_max_body_size 105m` in all three of
`deploy/nginx/spa.conf`, `pigro.letsrebase.conf` and `preview.pigro.letsrebase.conf` (the host
files are applied by hand); `src/test/upload-body-size.test.ts` holds the documents and the
inbox routes to it, so a fourth upload route adds its location and its case there.

**What arrives is read once by `InboxClassifier`, and nothing it reads writes a record (REB-972).**
`core/inbox/classifier.py` runs after an upload's transaction: a FatturaPA is routed (a sales invoice
waits in the issued review, `inbox/issued.py`; a purchase is held with its parsed fields), a PDF or a
photo is read by PigroCRM's own model into a `supplier_invoice`, `receipt` or `bank_statement`
**proposal** and nothing else, or, with the model off, left unclassified for the user's agent
(`propose_inbox_item`). The model is `core/ai/`: off by default, `ModelGateway` is the only caller of
`ai/transport.py` (built on `http.HttpCall`, so a test passes a fake and never a key), reserves the
estimated cost before the network (a file at what it weighs, a call of unknown outcome kept as
spent) and refuses past the daily cap. A paid reading is claimed first (`inbox_items.read_started_at`,
`read_attempts`: two runs never pay for one file, and an automatic run stops after three tries), the
classify routes have their own long-read nginx locations in all three conf files, and a proposal
read from the inbox is visible through its item in the row-level policy too (migration 0080). The API tests' `client` fixture
overrides `get_model_call` with `scripted_model.ScriptedModel`, which fails a test that reaches it
with no answer queued. A proposal's visibility is its source's (`proposals/access.py`); accepting
one of the three new kinds is refused until the module that files it exists.

**What a person or an agent says about an item that waits is three acts of `InboxService`, and a proposal
waiting on the item is a person's (REB-984).** `discard`, `mark_duplicate` and `set_kind` (REST
`/api/inbox/items/{id}/discard|duplicate|kind`, MCP `discard_inbox_item`, `mark_inbox_item_duplicate`,
`set_inbox_item_kind`, all in `AGENT_ALLOWED_ACTIONS`) read the item under a row lock with the visibility of
`inbox/access.py`, answer an idempotent request with the item as it is, refuse an item that is no longer `new` or
`proposed` with a 409, and record an activity entry on the item (that timeline is where «by whom» lives; the row has no
column for it). A person's act rejects a pending proposal on the item through `ProposalService.reject`; an agent's
is refused, since an agent never decides a proposal. Nothing is written as a record, and a kind a FatturaPA decided
(`parsed_fields` set) does not change. Marking a copy re-points every copy the item had under `db/scope.py::widened`, the caller's own scope restored after, so a copy their scope hides never keeps pointing at a copy; and the classifier asks `_acted_on` after the model returns, so a reading that completes after a person discarded the item, marked a copy or said what it is changes nothing (REB-985). «In arrivo» is `features/inbox/`: `labels.ts` holds the words and the
`TABS`, `queries.ts` the keyset lists (one per state of a tab, merged by id) and the waiting count the sidebar
draws, and `IssuedReviewDialog` runs the issued-invoice review of `features/invoices/ImportReview.tsx` on an
item's own file. The model's state is read from `GET /api/settings/ai-model`, which only an unscoped administrator
may, so «Leggi i documenti in attesa» is that administrator's alone.

**`pg_trgm` is created by the migrations at API start-up.** On a managed Postgres
where the user cannot create extensions, the deploy fails at boot with
`permission denied to create extension "pg_trgm"`. That is the wanted behaviour: the
alternative is an application that starts and then scans sequentially in silence.

**The e2e ports are hardcoded, not read from the environment.**
`apps/web/scripts/e2e-env.sh` exports 55433 (its own Postgres), 8000 (the API) and
5173 (`vite preview` over the production build `e2e.sh` makes first) as literals, and the script clears whatever holds :8000 and :5173 before
it starts. On this shared box that is a real collision with other projects, and it is
why `pigrocrm-e2e` is `serial: true` in `.github/preflight.json`. Editing those three
values into `${VAR:-default}` form is a genuine improvement and has not been done.

**Two concurrent `pigrocrm-e2e` runs still corrupt each other through the pidfile, not
just the ports.** `e2e/resilience.spec.ts` kills the API mid-suite and relaunches it,
tracking the pid through the fixed path `PIGROCRM_E2E_API_PIDFILE`
(`/tmp/pigrocrm-e2e-api.pid` by default) rather than through a per-checkout handle. A
second run alive on the same box reaping this pid before `killApi()` gets to it, or
removing the pidfile in its own teardown, no longer kills the run: `helpers.ts`'s
`killApi()` treats `ESRCH` and a missing pidfile as "already gone" and waits for the
port to actually close either way (REB-91, fixing the `kill ESRCH` observed live on
2026-09-10 with several other agents' containers and dev servers on the same development machine,
filed as ORB-91). What the fix does not remove: a second run that *replaces* the pid
with its own, live API before this one gets to it, which this process has no way to
tell from its own API still running. Run `pigrocrm-e2e` one checkout at a time; the
pidfile trap does not show up any other way.

**Migrations live in `packages/core/migrations` with `alembic.ini` beside them**, and
`tenants/service.py` finds that file from `pigrocrm.core.__file__` rather than from
the checkout, so it works identically in the image. The schema is the `migrate` service's,
a one-shot of `docker-compose.yml` that runs once per deploy and before `api` starts (`api`
waits for it with `service_completed_successfully`, and the previous `api` keeps serving
meanwhile): it runs `alembic upgrade head`, which migrates the **root** database only, and then
`pigrocrm ensure-space-defaults`, which brings every space in the registry to head with the same
`migrate_to_head` provisioning uses (ORB-189: a space was migrated once, at creation, and
`pigrocrm-v0.13.0` broke every space's login by shipping a migration they had never received),
then gives it its default stages, templates and cost categories where a table is empty, then its
profile where none is stored (REB-955, frame design B7: `company` for the space
`PIGROCRM_LEADS_SPACE` names, the root included when `PIGROCRM_ROOT_SLUG` is that name; `solo`
with `deals` on for a space whose `rebase_engagements` row has `space_created`; nothing for the
rest, who get the onboarding; never over a stored profile or module row), and
never fails the boot (spec 2026-09-12 §6.5). It also creates and grants `pigrocrm_app`, and, as
root for the one command that needs it, hands the documents directory to the image's uid 10001
before it drops to that user (`setpriv`), so the unprivileged `api` can write there with nothing
to prepare on the host. A deploy therefore shows `migrate` as `exited (0)`, which
`_deploy-compose.yml` accepts for that service alone. A new migration reaches the spaces at the
deploy, not before: nothing else runs DDL on a space.

**A deal, a customer and a person have no `note` column: they have a thread (REB-879).** The
table `notes` (`core/notes/`) holds one row per remark, signed by the actor that wrote it: the
member's id and name, the door it came through (`source`: `web` for a browser session, `mcp` for
a token, `system` for a job, `rebase` for the engagements door, NULL for a note moved over from
the old column, shown «Nota precedente») and the moment. A caller never passes an author, a
date or a source. `OrganizationCreate`, `PersonCreate` and `DealCreate` still accept `note`, which
becomes the record's first note, written in the creation's own transaction by `append_note`
(`notes/writer.py`, a function and not a `NoteService` method so the MCP coverage sweep does not
ask a tool for it); the Update and Read schemas have no `note`, and it is not a native field
(`schema_registry.NOT_COLUMNS`). A note is edited or deleted by its author or an admin (by anyone who may
write the record, when it came over from the old field with no author and no source), never a
`system` or `rebase` one, and every read carries `editable` for the caller. Each add, edit
and delete is an entry on the parent's timeline (`note_added`, `note_edited`, `note_deleted`);
the whole text an edit replaced is a row of `note_revisions`, since a timeline payload is cut at
2000 characters (`GET /api/notes/{id}/revisions`). `note` is a reserved name: no custom field
may take it. The table has the row-level policy of its parent through
`entity_visible` (migration 0064), so a scoped member reads and writes the notes of their
legal entities alone. The `rebase:request=<uuid>` / `rebase:match=<uuid>` references of the engagements
door are not text any more: they are `deals.rebase_refs`, written only through
`DealService.create(rebase_ref=...)` and `attach_rebase_ref` and found by
`DealRepository.find_by_rebase_ref`; the door's human sentence is the deal's (or the customer's)
first note, written by `Actor.rebase()`. The hub's lead payload still sends `deal.note`.

**A space's own Google client secret and token key are sealed, with a key derived from
`PIGROCRM_JWT_SECRET` (PCORE-B-02).** `space_settings` stores `google_client_secret` and
`google_token_key` as `sealed:v1:...`, never plain (`space_settings/sealing.py`, migration 0065).
Secret rotation: rotating the JWT secret therefore also unsets every space's own Google
client (a space on the root's shared client is untouched): the two rows read as not set until
the space re-enters its client secret and reconnects Gmail and Drive. A row written before
migration 0065 and not yet sealed is read as it is. A process holds at most a dozen
space engines with a pool of four each (`tenants/registry.py`), and its request path never
creates a database or a table: `ensure-space-defaults` at boot is what creates the registry.
The `pigrocrm` command is the package `core/cli/` (`python -m pigrocrm.core.cli` works as
well): one module per family of commands and one walk over the root and the registry's spaces
(`cli/spaces_walk.py`).

**`--no-sync` on the container's `uv run` calls is load-bearing.** Without it, uv
re-evaluates the environment against the default group selection — which includes
`dev` — decides the image's venv is out of date, and downloads mypy and ruff into a
running production container on every start. That happened once, live.

**The installable app is five files that must agree, and a worker that keeps nothing a person
is shown (REB-731).** `apps/web/pwa/plugin.ts` emits `manifest.webmanifest` and the icons
(drawn from `shared/brand` by `build:pwa-icons`) and, after the main build, builds `sw/index.ts`
into `sw.js` with the offline page's files baked in as its precache; `offline.html` is a second
Vite entry, so it renders with no API and no session. A space is its own installed app: the SPA
links `/<slug>/app/manifest.webmanifest` and registers `/<slug>/app/sw.js` with scope
`/<slug>/app` (`src/pwa/`), and `spa.conf` serves the three files under every slug, with
`Service-Worker-Allowed` for the worker, which is what lets it take that scope. **The scope has
no trailing slash on purpose:** the router's home is `/<slug>/app?tab=...`, and `/<slug>/app/`
would leave it outside the app. The worker never stores a document or an API response and never
touches a request that is not a page load or one of its own files (`sw/policy.ts`); do not add a
runtime cache to it without a decision in `docs/design/DECISIONS.md`. Push (REB-738) plugs in as
its own file under `sw/`. `vite preview` serves the worker too and sets the same header
(`vite.config.ts`); the dev server never registers one, so `pnpm dev` shows none of this: test
it on `pnpm --filter web build` plus `vite preview` or the compose stack.

**An MCP error reaches the assistant only in the type its surface passes on.** On
`mcp` 2.2 a tool keeps the text of a `ToolError` (an `is_error` result), and a resource
or a prompt keeps the text and the code of an `MCPError` (a JSON-RPC error); anything else
becomes a generic «Error executing tool <name>», «Error rendering prompt <name>» or
«Internal server error», and the domain sentence is lost. `apps/mcp`'s `server.py`
translates a `DomainError` in one place, the guards `_make_guards` returns (`guard` for tools, `protocol_guard`
for resources and prompts); a new tool, resource or prompt goes through it, never through
its own `try`. The codes follow one rule: `-32602` for a record that does not exist (the
MCP spec's own code for a bad prompt name or a missing argument, and for a missing resource),
`DOMAIN_REFUSAL` (`-32010`, in the range JSON-RPC leaves to implementations) for every
other domain refusal, and `-32603` only for a real crash, which is the SDK's to answer. A
test of an MCP error asserts what a `Client` receives, not what the server raised in
process: the prompt tests did the latter and missed that the modern protocol already
answered «Internal server error» on 2.0 (REB-451). Re-read those paths in `.venv` at every
`mcp` upgrade.

## Deploying

Through CI only, as every project here (`docs/adding-a-project.md` §7): preview on a
push to `main` that touched PigroCRM, production on a tag `pigrocrm-v<semver>`, both by
`.github/workflows/deploy-pigrocrm.yml` calling `_deploy-compose.yml`. The production
compose project is `pigrocrm`; the preview's is `pigrocrm-preview`.

Each environment's `.env` is `${DEPLOY_PATH}/.env`, the root of that environment's
checkout and two levels above the compose file: the deploy passes
`--env-file "${DEPLOY_PATH}/.env"`, never rsyncs a `.env`, and reads nothing beside the
compose file. It holds the `PIGROCRM_*` and `POSTGRES_*` values and is never in the
repository. Locally, `docker compose` instead reads `projects/pigrocrm/.env`, beside
this file, and `uv run uvicorn`/`uv run pytest` read `.env` in the repository root
(`.env.example`'s own header says the same, in more detail). `PIGROCRM_DATA_DIR` has no
default in the compose file, so a `.env` that forgets it fails the stack instead of
mounting an empty directory (REB-258).

**Two database roles since REB-634** (spec 2026-10-03 §4 «The role»). Requests run as
`pigrocrm_app`, `LOGIN NOSUPERUSER NOBYPASSRLS`, which is what keeps a scoped member
inside their legal entities: a superuser is never subject to a row-level policy. The compose
file builds `PIGROCRM_DATABASE_URL` for that role from `PIGROCRM_APP_PASSWORD`, which
has no default either, and `PIGROCRM_ADMIN_DATABASE_URL` for the bootstrap superuser,
which keeps the owner's work: `alembic upgrade head`, `ensure-space-defaults`, `CREATE
DATABASE`. Nothing is created by hand: the boot (`ensure-space-defaults`) creates the role
if it is missing, sets the password it finds in the URL and applies the grants to the
root and to every space, and provisioning does the same for a new space. On a deployed
host `PIGROCRM_APP_PASSWORD` is a **GitHub Environment secret** the deploy writes into
`${DEPLOY_PATH}/.env`: `deploy-pigrocrm.yml` lists it in `env-secrets` and
`_deploy-compose.yml` templates it in, so it is set the same way the host is told which
server and path to use, not by a hand edit on a host nobody who runs the board can SSH
to (REB-650). A developer's checkout keeps one URL and the policies bypassed, and sets
`PIGROCRM_APP_PASSWORD` in its own local `.env` by hand like every other local secret;
`.env.example` says how to see the scope locally.

Ports, loopback only, from the table in `docs/adding-a-project.md` §7: production web
8080, Postgres 55432; preview web 8081, Postgres 55434.

**Launching the stdio MCP on the host without the token on the command line.** A
client that can speak Streamable HTTP should use `/<slug>/mcp` with the token as a
bearer, and nothing below concerns it. A client that starts the stdio server over ssh
(Claude Code's `ssh <host> "... python -m pigrocrm_mcp"`) must not pass the token as
`docker compose exec -e PIGROCRM_TOKEN=<value>`: the value then sits in clear text in
the sudo log, the journal and `ps` on the host, and in the client's own config file
(REB-127). Instead, the token lives on the host in `/etc/pigrocrm/mcp-token.env`
(directory 0700, file 0600, both root's, one line `PIGROCRM_TOKEN=pgc_…`). It is
outside the checkout, so no deploy touches it. The launch names only the path:

```
ssh -o BatchMode=yes <host> "cd /opt/pigrocrm/projects/pigrocrm && sudo sh -c 'set -a; . /etc/pigrocrm/mcp-token.env; set +a; exec docker compose -p pigrocrm --env-file ../../.env exec -T -e PIGROCRM_TOKEN api uv run --no-sync python -m pigrocrm_mcp'"
```

`-e PIGROCRM_TOKEN` with no value makes compose copy the variable from its own
environment, so the logged command and `ps` show the name and never the value.
`--env-file ../../.env` is the deploy's own `.env`, the same one the digest's cron line
names: compose interpolates the required `PIGROCRM_*` and `POSTGRES_*` values even for
an `exec`. Do not rely on a `.env` symlink beside the compose file. The
server resolves the token once, at start-up (`apps/mcp/src/pigrocrm_mcp/__main__.py`).
To rotate it:
1. Mint the new token. Use the Agenti page of the sidebar, or run `PatService.create` inside the
   api container with its stdout redirected into the file *on the host*, so the value
   never crosses the ssh connection.
2. Restart the client's MCP server.
3. Check that the new token's `last_used_at` moved.
4. Revoke the old token.

Revoking a token does not stop a stdio process that is already running with it, and
the next start refuses it.

## Namespace

Every environment variable is `PIGROCRM_*`, the CLI is `pigrocrm`, the databases and
the e2e container carry the name too. None of that is monorepo-wide: it is this
project's namespace and it stays project-scoped. `.env.example` documents every
variable and is the file to read before adding another.
