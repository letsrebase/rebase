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
`pigrocrm mail-sync` (named `gmail-sync` until REB-1014, still accepted as an alias) and
`pigrocrm reminders` every fifteen minutes, `pigrocrm automations`
every morning and `pigrocrm digest` every Monday at 08:00 Europe/Rome. All four walk the
root installation and then every space in the registry. None is a process this repository
starts on its own; the runbooks are `docs/superpowers/notes/2026-09-09-gmail-cron-runbook.md`
(`mail-sync` and `digest`), `docs/superpowers/notes/2026-10-26-reminders-cron-runbook.md`,
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
`core/modules/registry.py` decides whether `deals`, `contracts`, `time`, `purchases`, `taxes` or `cash` is on
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

**A purchase is anything the space pays for, and `cost_amount` is the only number a reader sums (REB-993).** `core/purchases/` replaced the cost: the table `purchases` is the old `costs` table renamed with its rows (an old cost is kind `expense`, its free-text supplier `merchant_name`, `cost_amount = amount`), plus `purchase_vat_lines` for the VAT recap of an `invoice` or `credit_note`. `PurchaseService` is the one writer: it checks the document (supplier role, a recap that adds up to the total, a credit note negative and linked to an invoice of the same supplier, withholding refused to a flat-rate buyer, which is not a withholding agent, the foreign currency's rate) and **freezes what the purchase costs** from the buyer's pack (`purchases/cost.py`, the one function: ordinary buyer = total less deductible VAT, flat-rate buyer = total plus the integrated VAT on a reverse charge, shared or `non-it` = total) with the pack and regime. The profit and loss, the deal's profit and loss, the cash picture and the late-entry count read `cost_amount` of registered, live rows (`status = 'registered'`), by the document `date`; cancelled and archived rows count nowhere. The monthly `PeriodLock` governs `date` as it governed a cost's. The activity feed and the custom-field definitions say `purchase`, the module switch is `module_purchases`, and `entity_visible`'s branch and the policy follow the rename (`0086_purchases.py`; the test template applies its `rewrite()` to every earlier revision's statements, as it does for `0077`). A fiscal rule the code uses is in the frame's A5 register as verified with its source; one that is not stays out of the code.

**«Clienti», «Fornitori» and «Acquisti» are pages of the registry and the purchases, not new data (REB-1023, REB-1053).** The
registry has a page per role, `/app/customers` and `/app/suppliers` (REB-1053 reversed the single «Anagrafiche» page of REB-1023:
`DECISIONS.md`, 2026-11-10), each its own title, strong button and list with no tab between them; one component,
`features/organizations/OrganizationsPage.tsx`, renders both over `GET /api/organizations?role=` (each page asks for its own name,
«Fornitori» `supplier` and «Clienti» `customer`; no organization is on neither list, because `OrganizationService` keeps `freelancer`
always with `supplier`: it adds the role to a write that lacks it, refuses to take it away, and a merge keeps it, REB-1061; «Fornitori» is the `purchases` module's page, so a space that runs `collaborators` with `purchases` off lists a
freelancer nowhere on the registry, as it already does for any supplier), and an organization with both roles is on both. An organization's own page stays `/app/organizations/<id>`; `/app/organizations` and `/app/clienti` are redirect stubs
(`?role=supplier` goes to «Fornitori», anything else to «Clienti»; `features/organizations/search.ts`). `/app/suppliers` is the
`purchases` module's by `ROUTE_MODULES`, like every other page of a module, and a `NavItem` (`components/nav.ts`) reads its modules
from its path alone; `registry` marks the two lists so an organization's own page lights the one it is read as
(`registryPageOf`). The engagements page is «Collaboratori» in the sidebar and as its title, after the module. «Acquisti» is `/app/purchases` (the list; `new`, `edit/$purchaseId` and `$purchaseId` are siblings, the
purchase's page is not a layout), its figures come from `total_count` and `total_amount` of `GET /api/purchases` (what the filters
select, summed by the server, never by the browser), and one component, `PurchaseFormPage`, is «Nuovo acquisto», the edit page and the
page that registers a file with the file beside the form (`?inbox_item_id=`). The payment column, the «Da pagare» and «Scaduti»
figures and «Segna come pagata…» do not exist until purchases write due items: they are absent from the pages, not empty. The phone
«+» «Spesa» sheet stores its optional photo through `POST /api/inbox/upload?read=false` (no classification, no model call) and
creates the receipt with that `inbox_item_id`; a person linking an item that is still `new` or `proposed` files it in the same
transaction (`core/inbox/filing.py::file_as_purchase`), an agent only links it, since accepting is never an agent's act.

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

**An expense note is `ExpenseNoteService`'s and it owns no figure: its purchase does (REB-1046).** `core/expense_notes/` holds
`expense_notes` (migration 0105): `submitted`, `approved`, `rejected`, `reimbursed`, no draft. Approving writes one purchase of kind
`expense_note` (`PurchaseService.register_expense_note`, the writer's half with no actor; `purchases.staff_member_id`; the purchase is
never edited, cancelled or archived from the purchases, and `PurchaseCreate` refuses the kind) and the one outgoing due item to the
person (source kind `expense_note`, counterparty `staff_member`, in `RESTRICTED_SOURCE_KINDS`: an administrator's and the staff member's own to read): a purchases due-item writer must skip this kind of purchase. Reimbursing is a second act,
once: a transfer dates the item (`paid` settles it), otherwise the item is settled like any other and `project_reimbursement`
(called by `DueItemService._project`) turns the note `reimbursed` or back to `approved` when a payment is reversed; in a payslip,
`record_staff_payslip` settles the same item through `settle_notes_in_payslip`, so no second due item exists. A note is own when
the staff member is the deciding user or the user typed it in; only the space's one active administrator decides on an own note
(`self_approved`, for the export), and an agent never does (`OwnExpenseNote`). A staff member reads their own notes, an
administrator (and a `full_access` token) every one in scope (`read_all_expense_notes`); `submit_expense_note` is a token's for its
owner's own staff member only. The surface needs `staff` and `purchases`, but the module's `requires` stays empty: with `purchases`
off «Persone dell'azienda» stays. `GET /api/expense-notes/summary` is how the web learns whether the caller is a staff member (the
staff routes refuse non-administrators).

**Bank accounts, statements and movements are written by `BankStatementService` (REB-998).**
`core/bank/` holds `bank_accounts` (ours, always a legal entity), `bank_statements`, `bank_movements`
and `statement_movements` (which statement carries which movement), plus `organization_ibans`, the
counterparties' account list (`OrganizationIbanService`; a movement's proposal waits `proposed`).
A statement and a movement share their account's legal entity by composite foreign key. A reader is
a function from bytes to `ParsedStatement` registered with `readers.register_reader`; it knows
nothing of accounts or dedup, and `import_statement` does the rest and never commits. A movement's
`dedup_key` (`dedup.py`) is the bank's reference when it has one, otherwise date, amount, description
hash and the occurrence index within the file, unique per account, so the same movement in two
statements is one row and two identical same-day payments are two. The covered period is the file's
or the one a person sets; it is never guessed. `bank/__init__.py` registers the readers: `banco_bpm_xlsx` (`banco_bpm_xlsx.py`, YouWeb's «Saldo e movimenti» export: no account, period, balance or reference, so the key is the reference-less one; a `-da contab` card payment is left out until booked; the SEPA `END TO END ID` of a description is kept in `bank_movements.end_to_end_id`, never as a dedup key since payers reuse it). And `camt053` (`camt053.py`, `.001.02` and `.001.08`, one `Stmt` per file: `AcctSvcrRef` is the bank reference, `EndToEndId` goes to `end_to_end_id`, `NOTPROVIDED` is none). The tables are the `cash` module's data (REB-1025). `cash` (key `cash`, «Cassa»,
default on for both profiles, `requires=()`) guards `/api/bank-accounts*`, `/api/bank-statements/*` and `/api/bank-movements/*` and the eight bank
tools; the schedule and the payables are core and answer without balances while it is off. Every method is an administrator's
(`require_admin`) and, for a token, `full_access`'s: `BankAccountService` (create, update, archive, read; an account is
never deleted, an archived one leaves the list and every balance, and its IBAN, currency and legal entity freeze with its
first statement), `BankStatementReadService` (statements and movements, keyset-paged on `period_to` and `booking_date`)
and `StatementImportService` (`import_file`, `revert_statement`: the file is read by its content, `STATEMENT_SIZE_MAX`
bounds it, the route checks the role before it reads the body and keeps one byte past the cap, and a statement
upload needs its own nginx `location` in the three conf files). **Every figure is the server's**: `balances.py` is the
balance (the latest imported closing balance, else the opening one, plus the movements booked after it; archived accounts and
accounts with no anchor have none) and `is_stale`, `coverage.py` is the twelve months an account shows (`covered`,
`missing` after `STATEMENT_GRACE_DAYS`, `pending`) and `missing_periods`, which the schedule's alerts read, and
`StatementCheck` is the stated balance against the movements a statement carries (`balance`) and its opening against the
previous statement's closing (`gap`). A revert drops the movements only that statement carries and refuses (409) while one of
them is no longer `unmatched`. The tools are registered in `tools/privileged.py` (full access only) with one action key each;
the upload, the revert and the coverage are REST only, and a movement's description and counterparty are the bank's free text.
Matching a movement to a due item is `core/bank/matching.py` (REB-1042, below); PDF statements and the transfer, ignore and «new expense» outcomes are later cards. Nothing here is scheduled.

**The engagements door writes a contract beside the deal (REB-1001).** When a match turns active, the freelancer's space gets rebase as a customer, the letter's deal (the hub still reads the deal and its hours; its `PUT` answer needs `deal_id`) and an `active` contract titled like the deal with one rate card from the letter's `fee_unit` (`day` is `daily`, `hour` is `hourly`, `flat` is `one_off`, valid for the letter's dates; the forecast `expected_quantity` is the expected days, or hours at `HOURS_PER_DAY`, with `delivery_period` `one_off`). `rebase_engagements.contract_id` records it with `deal_id`; a row with a deal and no contract is a space set up before this, and keeps its deal and hours alone. The registry has no migrations: `ensure_tenants_database` adds the column itself at boot (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, before the missing-column refusal), and a later column on a registry table goes in the same list (`REGISTRY_COLUMN_UPGRADES`). Dropping the deal for new matches waits for the hub to read days from work units. **A retry is answered the row's ids, never what the deal points at now** (`test_second_call_answers_the_same_ids_and_creates_nothing` re-points the deal by hand and pins it), so `EngagementService._recorded` must not read the customer from the deal: a fact in the registry that a change in a space makes stale is written by that change (a merge of the customer points `customer_id` at the survivor, REB-1031, below).

**Engagements with freelancers are the `collaborators` module and every read and write is an administrator's (REB-976).** `core/collaborators/` holds the `engagements` table (the CRM's name for the hub's match; `hub_match_id` is the import's idempotency key), the freelancer's identity (`organizations.hub_freelancer_id`, unique, only on an organization with the role `freelancer`, set by an admin and never changed; the fiscal kind is `supplier_kind` limited to `professional`, `flat_rate` and `occasional`) and `FreelancerService` (availability, `set_tax_data`). The module is **off** unless the space switches it on or already holds engagements (`Module.asks_onboarding`: an answer the onboarding does not ask yet reads as off even with no profile); `ensure-space-defaults` writes it off for every profile, and the release that moves the hub's matches is the one that writes it on for rebase's own space. `days_per_week` is 0.5 to 5 in half steps and null only on an imported engagement; availability counts **live** engagements (draft, out for signature and active, since REB-1035), a week touched by one with no `days_per_week` is `unknown` («sconosciuta») and never `free`, and a part-week counts in full. A new engagement is saved with a warning, never refused, when it overlaps a live one of the same freelancer (`overlap`, and `over_capacity` above five days a week). The payment rule is `fixed_term` (`payment_days` 1 to 60, 30 from month end) or `on_collection` (`collection_days` 1 to 60 and **no** fixed term: the printed clause names no floor); the schema holds both, and the due-date arithmetic and the letter are later cards. States and edges are `rules.TRANSITIONS`; once an engagement is out for signature only `days_per_week` and its client, deal and contract change. Cancelling, closing, creating and the tax data are `FULL_ACCESS_ACTIONS`; the signature that makes an engagement `active` is `activate_engagement`, a person's act. The table has no legal entity of its own: reads join the freelancer's organization so its row policy hides an engagement from a scoped admin.

**«Incarichi» reads what the server names, and the freelancer's card is a closed list (REB-1035).** `GET /api/engagements` answers `freelancer_name`, `client_name`, `waiting` (what a person must do: `letter_not_created`, `letter_not_sent`, `missing_days_per_week`, `missing_client`, `report_undecided` with its month and tacit date) and `letter_document_id`; `bucket` is the list's three tabs and `GET /api/engagements/counts` the count on the entry, both built from the one `waiting_for_a_person` clause in `collaborators/repository.py`, which `EngagementService._waiting` words per row: change one and the other in the same commit. `EngagementPaymentService` (`payment_read.py`) names the month each engagement due item pays for by its forecast key, so a page never parses a description. The freelancer's own card (`GET /api/contracts/{id}/rebase-engagement`, `engagements/own.py`) goes through the registry row the door wrote for the contract and is built by `collaborators/own_view.py` as a closed list of fields: add a field there on purpose, never by widening `EngagementRead`. Availability counts `LIVE_STATES`, the same three the overlap warning counts. Approving or contesting a month stays a person's button: `approve_monthly_report` and `record_monthly_report` are person-only, and the page has no agent path.

**Two organizations that are one company are folded by `OrganizationService.merge`, and a table that points at `organizations` joins it the day it appears (REB-995).** `organizations/merge.py` walks `REFERENCES` (foreign key columns), `LINKED` (`entity_type`/`entity_id` pairs: notes, timeline, drafts) and four steps with a decision of their own (contact points and Gmail message links the survivor already has are dropped, an open automation task the survivor already has for the same rule is archived, pending contract proposals have their `customer_id` rewritten); the preview (`dry_run`, true unless sent as false) and the merge run the same code. `test_organization_merge.py::test_every_foreign_key_to_organizations_is_covered_by_the_merge` fails for a new foreign key (a purchase's `supplier_id`) that is in none of them. It is an unscoped administrator's (`merge_organizations`, in `FULL_ACCESS_ACTIONS`; REST `POST /api/organizations/{id}/merge`), two rows under different legal entities make the survivor shared, and the loser is archived with `merged_into` on its timeline and `merged` on the survivor's. A snapshot frozen on an issued invoice keeps the loser's name. **One row lives outside the space's database (REB-1031):** the registry's `rebase_engagements.customer_id` names the customer the engagements door made, and `merge` takes an `EngagementRegistry` (`engagements/registry.py`) on the registry's own session: the REST route builds it from `TenantsRegistryDep` and the request's slug, the MCP tool from `McpContext.registry_session` (the HTTP transport gives one to every space's server) and the space's name, and the root has no such rows (a `None` slug finds none; the MCP tool passes no registry for it). It is a step of the same walk, counted by the preview as `rebase_engagements` and written by the merge, and the registry commits before the space does: a failure between the two leaves a registry that names the survivor, a live row of the same company, and a merge that can be run again, where the other order would leave an archived customer in the registry and no way to repair it. A new registry row that holds an organization id of a space joins this step the day it appears. Not covered: a merge that lands inside the few seconds of the first door call of a match, before its row has a `customer_id`, is overwritten by that call's step 6.

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
follows the boot's convention (a line on stderr, exit 0). It also refreshes the forecast due items of what
active engagements pay their freelancers (`EngagementForecastService.refresh_all`, REB-1003), which is what
moves the horizon of an open-ended engagement forward and pairs a client invoice written elsewhere: it
needs a daily run to keep that horizon, the container start alone only moves it at a release.
**What a customer pays is `net_receivable`, and what a sale carries beyond its lines is `invoices/charges.py` (REB-994).**
`compute_charges` is the one place that decides, from the stored lines, the frozen profile and the customer
(`customer_kind`, `is_withholding_agent`, `country`), the withholding (the pack's `SalesWithholding`: 20%, only on the
`it-standard` pack and only for a withholding agent in Italy), the split payment (a public body that does not withhold, where
there is VAT) and the net (`total` minus the withholding minus the VAT of a split payment). `apply_totals` stores it on a draft
and `issue` stores it again from the customer as it is then; an import declares `withholding_amount` and `split_payment` and the
net follows. `net_receivable` is what the due item's amount, `monthly_paid` and `monthly_unpaid` read; `total` stays what the
document prints. The exporter reads the stored figures: `DatiRitenuta` and `Ritenuta` `SI` from the withholding,
`EsigibilitaIVA` `S` from the split payment, `ImportoPagamento` from the net, and `FPA12` with the 6-character IPA code from the
customer's frozen kind (`PartySnapshot.customer_kind`). A service to a business abroad on the ordinary regime is `N2.1`
(`_ordinary.resolve_line_vat`); a consumer abroad is refused until its own card. The pension fund and the INPS surcharge
(`DatiCassaPrevidenziale`) are not computed or exported, because a sale has no field for them; their bases are settled in the A5 register (REB-1099): the fund's contribution is in the VAT base and out of the withholding base, the 4% surcharge in both. The withholding needs one more taxpayer fact: `fiscal_profile.income_kind` (`self_employment` or `business`, asked of `it-standard` only, migration 0114): art. 25 c. 1 DPR 600/73 withholds on income of arts and professions, so `business` is not withheld and an unanswered profile is read as a professional. The ordinary regime owes the 2 euro stamp duty on its untaxed groups (`N2.1`) above the threshold, as the flat-rate regime does (`regime._stamp_duty_on_untaxed`).
follows the boot's convention (a line on stderr, exit 0).

**Forecasts of money in are stored due items in the state `forecast`, replaced by their document and never
added to it (REB-997).** `due_items/forecast.py` (`ForecastService`) materialises three rules: an active
contract's `fixed_recurring` occurrences (`contract_recurring_fee`, source kind `contract_occurrence`,
`source_id` the contract), its `worked` days on no line grouped by billing period (`contract_worked_days`,
priced by `work_units/gathering.py`, the same gather the draft uses), and every draft, draft proforma and
confirmed proforma (`draft_invoice`, kind `sales_draft`, `source_id` the invoice; its own kind so no reader
that joins `sales_invoice` to its invoice meets it). `forecast_key` is a uuid5 of the kind, the rate card or
contract and the period, unique among live forecasts by a partial index. An invoice line created from a
contract carries `rate_card_id` and `period_start` (`InvoiceLineIn`, both or neither; `bind_work_units`
sets them, and `InvoiceContext.check_billed_cards` refuses a card of another customer's contract), and a
line covers the occurrence whose span (to the next occurrence) holds its `period_start`. The date is
`due_date_for_customer`, the arithmetic `InvoiceContext.due_date_from_terms` calls, from the occurrence
(fee) or from the later of today and the period's end (days); never `Contract.payment_days`. A contract
billed on completion with no `end` has an undated forecast. The amount is the invoice's gross total by the
same regime; withholding and split payment come with the real invoice. Renewal, deals and contracts that are
not `active` are not forecast, nor are other currencies or a legal entity with no fiscal profile. Every
writer takes the **contract row first** (`refresh_contract`, `lock_contracts`), and a writer of a worked day
(`transition`, `create`, an approval, `bind_work_units`, deleting or annulling a draft that billed days)
takes its contract before the day, an invoice before its contracts, so a job, an issue and a day's move
queue and never wait for one another; `sync_invoice` runs in the transaction that creates, edits, deletes,
issues or annuls an invoice (`replace_lines` hands it the contracts the old lines billed), and
`refresh_contract` in the one that changes a contract, a rate card, a day or an approval. The daily job is
`refresh_forecasts`, run by `pigrocrm automations` after the task rules: it drops a forecast whose date has
passed, counts a contract or a draft it could not read in `failed` (a line on stderr and exit 1, the others
go on), and `--dry-run` rolls it back. A change to a customer's terms or to the profile's `due_days` waits
for the next pass. A fee invoice replaces its forecast only when its line names the card and the
period (`InvoiceLineIn`), otherwise the forecast stands until its date passes; the action that writes
such an invoice is `FeeBillingService.bill` (REB-1032, next paragraph).
The test-only triggers in the root `conftest.py` delete the forecasts of a hand-deleted invoice or contract.
**Recurring expenses forecast money out from a rule due items owns (REB-1022).** `RecurringExpense`
(`recurring_expenses`, migration 0098, behind `module_guard("purchases")`) is a supplier, a total
amount (VAT included), a `cadence` (`monthly` to `annual`), the first due date, an optional last one and
`stopped_on`; `RecurringExpenseService` creates, lists and stops it (REST `/api/recurring-expenses`, tools
`create_recurring_expense`, `list_recurring_expenses`, `stop_recurring_expense`, all classified like a
purchase). `ForecastService.refresh_recurring_expense` writes one `forecast` out per due date inside
`HORIZON_DAYS`, source kind `recurring_expense`, keyed `forecast_key("recurring_expense", rule, date)`,
dated on the due date itself. **A purchase of the period replaces it**: a registered, uncancelled,
unarchived purchase of that supplier with a positive amount, in the rule's company or shared, dated in
the calendar month, bimonth, quarter, half-year or year the due date falls in. `PurchaseService` rereads
the supplier's rules in its own transaction on create, update, cancel, archive and restore, and
`refresh_forecasts` (so `pigrocrm automations`) rereads every active rule and every rule that still has a
forecast, isolated per rule. Until the purchases cards write a due item for a purchase, the purchase is
what replaces the forecast and no item stands in for it on the schedule.
**The cash schedule, the payables mirror and the phone's figures are one read, `CashScheduleService` (REB-1025).**
`due_items/schedule.py` answers `GET /api/cash/schedule`, `/payables` and `/summary` and the tool `get_cash_schedule`; they
are core (no `module_guard`, `get_cash_schedule` is in `CORE_TOOLS`), and the module `cash` only adds the balances. Every
figure is the **remaining** amount of an open EUR item (`schedule_queries.py`; another currency is in no figure and only
keeps `has_open_items` true). Weeks start on Monday and the current one is whole: today and the days after count in it,
earlier days and every item due before today are **late** (`expected` only: a forecast whose date passed is nobody's
money any more and is in no figure), and an undated item is in no week and not late. Late money is never in the projected
balance (`balance_end` = the EUR accounts' total plus each week's in less out, forecasts in). The balance part (accounts,
`total_balance`, `balance_end`, the alerts about them) needs `cash` on and `read_bank_accounts` (an administrator; for a
token `full_access`), and the kinds of `RESTRICTED_SOURCE_KINDS` need `read_fiscal_due_items`, both asked once in
`schedule.py` and never in a route. A solo space has no outgoing figure and no balance by week, lists its own taxes
(`TAX_SOURCE_KINDS`) in a table no balance subtracts, and gets zeros from `/payables`. The six payable bands are the
receivables' (`BAND_LABELS`, same bounds), pinned by `test_cash_payables.py`, and `receivable.total` of the summary is
`InvoiceRepository.sum_unpaid` to the cent (`test_cash_summary.py`). The schedule is read in the request's own session
(no `REPEATABLE READ` snapshot, unlike the dashboards).
**A source kind of a module that is off is in no figure of that read** (`SOURCE_KIND_MODULES` in `schedule_queries.py`, one
filter in the shared predicate: weeks, late figures, `has_open_items`, the taxes table, the payables and the summary; the
list `GET /api/due-items` is a list of records and stays unfiltered). The mapping, read from the routes' guards and the
kinds' writers: `purchase`, `recurring_expense`, `integration_vat`, `iva_settlement` (the `iva_settlements` route),
`withholding` and `cash_basis_vat` (the purchases-and-IVA design writes them) are `purchases`; `tax_instalment` is `taxes`;
`payroll` is `staff`; `engagement` is `collaborators`; `contract_occurrence` is `contracts`; `sales_invoice` and
`sales_draft` are the core's, as is any kind not named. `test_cash_modules.py` fails for a kind in `SOURCE_KINDS` that is in
neither place. The server also compares against zero (`below_zero` on each account card and on `total_balance`,
`balance_below_zero` on the summary, never the browser), and `next_7_days` carries the forecast part of each total
(`in_forecast`, `out_forecast`), already inside it, so the Home can draw it lighter.
**The web draws the schedule once, in `features/cash/schedule/CashSchedule.tsx`, and mounts it twice (REB-1025).** `CashPage`
(`/app/cash`, the sidebar's «Cassa», module `cash`) and the Home tab «Scadenziario» (`ReceivablesTab`, tab id `receivables`)
render the same component; `features/cash/account/` is the account page (`/app/cash/accounts/$accountId`, an administrator's,
with the upload and account dialogs the schedule's buttons open) and `features/cash/home/` the phone Home's four figures
and the one-line band both use (`CashBand`). Every figure is the server's string: `features/cash` is in
`no-browser-arithmetic.test.ts`'s roots, so a `Number()` on an amount fails the suite. The browser keeps no constant the
server owns: the horizon options, `stale_days`, `unmatched_days`, `is_overdue_unmatched`, the grace and `oldest_days` are read
from the response. A balance the viewer may not read is `null` in the response and absent on screen, never «0,00 €».
**A bank movement is matched to the due items it pays by `MovementMatchService` (REB-1042).** `core/bank/matching.py`,
`match_rank.py` (the pure ranking), `match_reads.py` and `match_schemas.py`; the routes are `/api/bank-movements/*` (module `cash`,
administrator) and the tools `get_bank_movement` and `propose_movement_match`, both `full_access` and **nothing more**: an agent proposes
and reads, and `confirm_movement_match` and `undo_movement_match` are `PERSON_ONLY_ACTIONS` (`Actor.require_person`, then `require_admin`).
Three tables carry it: `movement_matches` (one row per proposal or decision: `proposed`, `confirmed`, `dismissed`, `undone`, who and why;
at most one live row per movement by a partial unique index), `movement_match_items` (the items and the part of the movement each takes) and
`due_item_settlements.bank_movement_id`, the **evidence** of a payment. A proposal is ranked by the amount (`exact_amount`, `items_total` for
a split of one counterparty's items that adds up), the document's number in the bank's text (`document_number`: an invoice's `2026/35` as
`2026/35` or `35/2026`, a purchase's own number), an accepted counterparty IBAN (`known_iban`, which only ranks) and the counterparty's name;
`StatementImportService.import_file` writes a `system` proposal only for the one fit that has evidence besides the amount and nothing beside
it, and **never settles**. Confirming settles each item through `DueItemService.settle_from_movement` (dated on the booking date, the item's
own permission, an outgoing or fiscal item is an administrator's act, the month lock does not apply), or, when the item was already marked
paid by hand for the same amount (within `ADOPT_WINDOW_DAYS`), attaches the movement to that payment instead of making a second one
(`adopt_for_movement`). The selection has to cover the movement exactly (a last item paid in part needs `accept_partial`); the same currency,
company and direction only. A payment a movement proves is taken back only through its match (`reverse_settlement` and «segna come non
pagata» refuse it); undoing reverses what the match wrote (the row stays, stamped) or only detaches what it adopted, and the match stays as
`undone`. A statement whose movement was ever confirmed cannot be reverted. Every figure of a candidate or a preview (`allocated`,
`difference`, `partial`) is the server's.

**«Fattura il canone» bills one occurrence of a recurring fee as a draft (REB-1032).** `contracts/fee_billing.py`:
`bill` makes the draft invoice with the card's fee on one line, `rate_card_id` and `period_start` (the
occurrence date) on it and the covered days in its words, under the contract's customer and legal entity, and
issues nothing; `occurrences` lists a contract's occurrences with the document that bills each (`to_bill`,
`drafted`, `issued`), by default the last 90 days and the forecasts' horizon ahead. The occurrence span and the
covering rule are `occurrence_spans` and `period_covered` in `due_items/forecast.py`, the forecasts' own, so the
two cannot drift. A date that is not one of the card's occurrences is refused naming the ones that are; an
occurrence a draft invoice or a proforma already bills answers that document (`created` false, status 200 on
REST), one an issued invoice bills is a 409 naming it, and a deleted draft or an annulled invoice frees it. The
contract row is locked first, so a double click queues. It is an administrator's act (`bill_contract_fee`, in
`FULL_ACCESS_ACTIONS` beside `bill_contract_days`): REST `POST /api/contracts/{id}/invoice-fee` and `GET
.../fee-occurrences`, the MCP tools `bill_contract_fee` (privileged) and `list_fee_occurrences` (every token's), and
the «Canoni» section of the contract's page. A contract still a draft, or not in euros, is not billed from here.
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
of `update_staff_member`. Nothing is deleted: `end_date` is how someone leaves. The estimate (`staff/payroll.py`) and the
staff page are REB-1008 and REB-1027. A recorded payslip is corrected by `StaffService.correct_staff_payslip` (REB-1030:
REST `PATCH /api/staff/{id}/payslips/{payslip_id}`, «Correggi il cedolino» in the staff panel), person-only like
`record_staff_payslip`: no token reaches it. The payslip is rewritten in place (a month has one), the net pay and
withholding due items it wrote follow (`restate_payslip_items`; a payment already recorded on one that would move is a
409 and nothing is written, an item an administrator dropped stays dropped), the forecasts are refreshed and the
timeline entry `staff_payslip_corrected` says who and which month, never the figures.
**A payslip from the inbox is a `payslip` proposal a person accepts, and pay is read only by `Actor.reads_pay` (REB-1045).**
`Actor.reads_pay` is an administrator, and an agent only with `full_access`. The inbox kinds `payslip` and `tax_document`
(`inbox/models.py::PAY_KINDS`) are visible only to it after classification, whatever the legal entity (`inbox/access.py`:
the same clause every read carries), a proposal with the target type `payslip` is listed, read and decided only by it
(`proposals/access.py`), and saying an item is one of the two kinds (`set_inbox_item_kind`) needs it too
(`Actor.require_pay_reader`, checked before the item is looked up). A `payslip` proposal reads from an inbox item like
the other three, its `proposed_fields` are `staff/schemas.py::PayslipProposalFields` (stored as parsed: the month as its first
day, the amounts as decimal strings) and creating one sets the item's legal entity to the person's. The space's model asks for
the figures only when `ai_model_config.read_payslips` is on (a second consent, `payslip_notice`, switched off by a different
provider or model) **and** the upload is an administrator's: otherwise a payslip is `other` to it, as before
(`InboxClassifier._reads_payslips`, `ai/reader.py::PAYSLIP_ADDENDUM`). `staff/matching.py` finds the one person in service
whose name is the document's; none, several or a missing figure leaves the item a `payslip` with a sentence and no proposal.
Accepting is `ProposalService.accept` (a person, never a token): `StaffService.file_proposed_payslip` and
`record_staff_payslip` are the same `_write_payslip` (checks, row, due items, forecasts), the item becomes `filed` and keeps its
bytes as `staff_payslips.inbox_item_id` (the payslip's PDF), and a refusal (a closed month, a month already recorded, a person
not in service) leaves the proposal pending. Rejecting keeps the item a `payslip`, still hidden from whoever may not read pay.
`MemberMonthRead.received` is what makes a month «Ricevuto». **Closing a month** is `StaffService.close_payroll_month` (REST
`POST /api/staff/payroll/{legal_entity_id}/closures`, the tool of the same name, `full_access`): only a month that has ended,
once, never reopened. `payroll_month_closures.snapshot` is the month as `get_payroll_month` read it, so a closed month reads
as it was closed whatever the pay inputs become (`PayrollService.live_month` is what is frozen), the forecasts it was closed with
are left alone until their date passes (`_sync_forecasts`), the period P&L reads its personnel cost from the snapshot
(`personnel_cost_in_range`), and a recording, a correction or an acceptance for it is a 409 that names the month.
**A declared tax figure is typed, never computed, and it outranks the estimate (REB-1006).**
`declared_tax_figures` holds, per legal entity, tax (`DeclaredTax`: `irpef`, `substitute_tax`, `ires`, `irap`, `inps`)
and year, what the accountant's return says: tax due, balance (signed) and advances, any of which may be left out
(never read as zero). `DeclaredTaxFigureService.set_declared_tax_figure` is the only writer: an administrator, or a
token with `full_access` (REST `PUT /api/analytics/fiscal/declared` and the privileged MCP tool of the same name), and
typing again replaces the row whole. `AnalyticsService.get_fiscal_estimate` answers the figures beside the estimate
(`declared`) and says the year's `reading` (`declared` as soon as one exists), so a newer estimate, which is written by
another path, never touches them; a «what if» with a supplied revenue carries none. Typing one recomputes the legal
entity's tax due items (REB-1074, below). Reading a figure from a document is its own card.

**An earlier year's loss reduces the IRES and a person's IRPEF, and where it comes from is told (REB-1088).**
`fiscal/loss_carry.py` applies the pack's `loss_carry` section (`law_loss_carry.py`: art. 84 TUIR for an srl, art. 8 c. 1 and
c. 3 for a person, with the TUIR renumbering from 2027 by D.Lgs. 117/2026 left to a later entry). The losses are the typed ones
(`tax_losses`, written only by `TaxLossService`: an administrator, or a `full_access` token; REST `PUT`/`DELETE
/api/analytics/fiscal/losses`, the tools `set_tax_loss` and `delete_tax_loss`; typing one recomputes the tax due items) and,
for an srl only, an earlier year's own estimated loss where none is typed, which the payload (`losses.estimated`, the
origin's `estimated`) and the assumptions label. Each loss keeps a remainder at the end of a year and is rolled forward on each
in-between year's income before any loss (`income_before_loss`); a year with no basis stops the roll and the tax has no figure
(`missing`) until a remainder is typed. The 80% is the pack's; the 100% of the first three periods needs `activity_start_year`
and `new_activity` typed with the loss. The estimate's payload shows `losses` (`used`, per-origin `available`, `used`,
`remaining`), a `loss_carried` line, and the INPS stays on the income before them. A person's loss of an art or a profession is
never carried (art. 8 c. 1): only a typed business loss is. «Tasse» types them in the tax's panel beside the declared figures
(`TaxLosses.tsx`).

**The tax calendar is pack data keyed by the calendar year a date falls in (REB-1004).** `FiscalPack.calendars`
holds `TaxCalendarLaw` entries (`fiscal/law.py`), each a list of `TaxDateRule` with the nominal days and the primary
source; a decree that moves a date is a new entry from that year, never a branch in a service. `fiscal/calendar.py`
turns an entry into the dates of one year and moves a deadline on a Saturday, a Sunday or a national holiday to
the next working day, keeping `nominal_date` beside `due_date`. A year with no entry has no dates (`None`, then an
empty list), never another year's. A date enters a pack only after its row in the frame's A5 register is
«verified» with the source. `get_tax_calendar` (`AnalyticsService`, registered in `tools/privileged.py`, module
`taxes`) is an administrator's and a `full_access` token's, as `get_fiscal_estimate` is.

**The payment rules of income taxes are pack data too (REB-1067).** `FiscalPack.payment_rules` holds `PaymentRulesLaw`
entries (`fiscal/law_payment.py`; the figures are built in `fiscal/payment_data.py`), keyed like the calendar by the
calendar year a payment or a filing falls in, read through `payment_rules_for(year)`: the instalment rule and its interest
(4% a year to the second instalment, 0.33% for each later one), the 0.40% deferral, the forecast-method conditions, the
penalty and the legal interest on a shortfall, the F24 code of each line of `F24Line` (a tax code or an INPS causale,
`f24_code(line)`), the dates the returns are filed and the year a withholding suffered is credited.
`fiscal/payment_rules.py` holds the pure lookups (`instalment_schedule`, `instalment_interest_percent`,
`return_filing_date`); they compute no tax and write no due item. Every figure names its
primary source and the day it was read (in the entry's own `source` or in the docstring of its law) and is a «verified»
row of the frame's A5 register; a rule no primary source states (the last instalment of a company with another year end,
the IRAP return of a person, the withholding credit of a company) is not in the pack and is on the accountant's list
(REB-975).

**The taxpayer's own facts are fiscal-profile fields, each nullable and kept when a save leaves them out (REB-1070).** `fiscal_profile` (migration 0107) holds, beside `inps_category` and `isa`, `inps_trade`, `inps_covered_elsewhere`, `inps_before_1996` and `flat_rate_inps_reduction` (the arguments of `InpsYearLaw.contributions`), `ateco_group` (an annex 4 key of `law_flat_rate`, validated against `ANNEX_4`), `flat_rate_start_year` and the three c. 65 answers `startup_no_prior_activity`, `startup_not_a_continuation`, `startup_predecessor_within_limit` (true = the condition is met). `None` is «not stated»: a figure that needs one gives no figure. `schemas.TAXPAYER_PARAMETERS` is the list; it is in `KEPT_WHEN_OMITTED`, in the frozen estimate's parameters and on the MCP `update_fiscal_profile`/`describe_fiscal_profile`. `FiscalProfileService._check_taxpayer_parameters` refuses a value on a pack that does not read it (INPS facts: `it-flat-rate` and `it-standard`; the flat-rate ones: `it-flat-rate`; `it-company` and `non-it` none) and the law's pairings (trade, the 1996 ceiling and the 35% reduction are artigiani e commercianti's, the lower rate is the gestione separata's); `_pack_defaults` clears them when the pack does not read them. `household` (REB-1087) is the one structured member of that list, a `Household` JSON on `it-standard` only (`ORDINARY_PARAMETERS`; the flat-rate pack, an srl and a company abroad refuse a value and a save that moves to them clears it). `FiscalProfileRead.flat_rate_groups` lists annex 4 from the flat-rate pack so the form shows each group's coefficient and carries no figure of law; picking a group proposes its coefficient in the typed `profitability_coefficient`, which stays what the estimate reads. There is no professional-fund (cassa) category: the INPS law cards cover the gestione separata and artigiani e commercianti only. `FiscalEstimate.missing_parameters` names the typed rates the profile leaves empty, and «Tasse» says them.

**The INPS reductions are three mutually exclusive facts, and the flat-rate 35% follows circolare 35/2016 (REB-1097).**
`inps_over_65_reduction`, `inps_new_enrolment_reduction` (with `inps_first_enrolment_month`, migration 0115) and
`flat_rate_inps_reduction` mean «INPS granted it», never inferred from an age or a history; `FiscalProfileService`
refuses two at once and the pack's lookup does too. An absent one is no reduction and the estimate says so in its
assumptions, it is not a `missing` fact. The 35% is on the whole contribution on the minimum and on the excess, so a
flat-rate artisan or trader owes a reduced fixed part (circolare 29/2015 no longer applies to it); the two 50% reductions
halve the IVS only (the traders' 0.48, `TradeRates.indemnity_add_on`, and the maternity stay whole). The new-enrolment rule
is a `NewEnrolmentReduction` of the year's `law_inps` entry, so 2027 decides for itself. A change of a recorded year's INPS
entry goes through the ledger's `--correct` command.

**The flat-rate law and each regime's income basis are pack data (REB-1066).** `TaxYearLaw.flat_rate` (`fiscal/law_flat_rate.py`)
holds annex 4 of L. 190/2014 by ATECO 2007 group (`ANNEX_4`, the nine groups and six distinct coefficients), the 15% and
5% substitute rates with c. 65's start-up conditions, what each rate applies to and the substitute tax advances (from 2026
only); `TaxYearLaw.income_basis` (`fiscal/law_income_basis.py`) says cash for a person (art. 54 TUIR) and for the flat-rate
regime (c. 64) and accrual for an srl (art. 83 and 109 TUIR), each with the law's wording. The flat-rate pack has three
entries because the law changed twice: 2015 (start-up relief is a third off the income), 2016 (a 5% rate) and 2026 (the
advances were read that year). **The estimate follows c. 64's order:** the INPS contributions are owed on the flat-rate
income, are deducted from it, and the substitute tax rate applies to the rest (`FiscalEstimate.substitute_tax_base`
echoes it). Before REB-1066 the contributions were taken on the income less the substitute tax, which no source supports;
the total to set aside and the net income moved by at most two cents of rounding, the split between the two lines moved. A profile coefficient that is
not in annex 4 is a `coefficient_warning` on the estimate and never a refusal, so a profile saved earlier keeps working;
a rule that moves a figure an estimate has shown says so in the pull request with a worked example.

**An estimate reads one revenue and cost basis, `fiscal/basis.py::year_basis` (REB-1071).** Given a legal entity, a year and its
pack it reads `TaxYearLaw.income_basis` and answers a `YearBasis` (`fiscal/basis_schemas.py`), or `None` where the law states no
basis for the year (a year before the pack's law, a company abroad: no revenue, no figure, never a basis chosen in a
service). **Cash** (a person, the flat-rate regime): revenue is the taxable share of every settlement of the year by its own
date (`fiscal/ceiling.py::paid_revenue_for_calendar_year`, the ceiling's reading, so an invoice issued in December and paid in
January is January's), the 4% INPS surcharge line included: it is collected revenue like a fee, in the ceiling and in the
coefficient's base alike (REB-1100; `StatutoryCharge` has no second flag any more); costs, for a regime that deducts them (not the flat-rate one:
`costs` is `None`), are the purchases by what their `purchase` due items were paid in the year and, for a purchase with no due item
(none is written yet), by document date, said as the `purchases_by_document_date` assumption, plus the `payroll` items settled in
the year. **Accrual** (an srl): revenue by competence (`coalesce(accrual_from, issue_date)`) in the financial year closing in
`year` by the legal entity's own year end (`financial_year`), purchases by document date, and staff and director cost as
`PayrollService.personnel_cost_by_kind` accrues them (a month somebody closed counts as closed; a person with no cost is
`staff_cost_not_estimated`), told apart because IRAP reads them as different lines (REB-1073). What a reader could expect in a
figure and is not in it is `left_out` (`not_collected`, `shared_purchases`, the purchases of no legal entity),
each with its Italian reason. `AnalyticsService._estimate_year` is the only place an estimate's revenue is chosen, so
`/api/analytics/fiscal`, the MCP `get_fiscal_estimate` and the economic overview's `tax` answer one figure for one year; only
`projected_tax` is a «what if» on a revenue the caller supplies, and it names no `basis`. Sales credit notes are not in the
register (PigroCRM does not issue them and imports none), so none is netted; a purchase credit note is a negative `cost_amount`.
A test that issues invoices and asks for a flat-rate estimate collects them first (`fakes/collected_invoices.py`).

**Tax rules change every year, so a year keeps its own law (REB-1083).** `fiscal/pack.py`'s module docstring lists which `FiscalPack` sections are statutory (an entry holds until a later `from_year` entry replaces it: `laws`, `calendars`, `payment_rules`, `ceilings`, `charges`, read with `law_for`, `calendar_for`, `payment_rules_for`, `ceilings_for`, `charges_for`) and which are yearly figures (read by exact year, no figure and a `missing` sentence otherwise: `inps`, `irpef`, `chamber_fees`, and the IRAP, municipal surcharge and legal interest tables). A new year's law is a new entry in the section it changes, never an edit of an earlier one, and a year with no entry in a yearly section is never read from its neighbour. `fiscal/law_ledger.json` fingerprints every recorded year of every pack and `tests/test_law_ledger.py` refuses a change to one unless the same diff carries a correction entry with its source and reason (REB-1084); `docs/yearly-tax-law.md` is the yearly pass that adds a year and records it with `python -m pigrocrm.core.fiscal.law_ledger --add-year`.

**An ordinary person's estimate is `fiscal/ordinary_estimate.py` (REB-1072).** `FiscalEstimate.person` (an `OrdinaryEstimate`) carries
the INPS and the IRPEF with its regional and municipal surcharge, each with `lines`/`parts` (signed, so a screen draws and adds
nothing), what it assumes, the sentences for what is missing and the sources of its law; `contributions` is its INPS and `due_total`
its net IRPEF with surcharges plus INPS. The order is the law's: income (art. 54 TUIR: collected less paid, a loss gives zero), INPS on it
(`FiscalPack.inps_for`), the contributions deducted from the total income (`IrpefLaw.social_security_deduction`, art. 10 c. 1 lett. e
TUIR, cited: the INPS payments recorded in the year, `fiscal/inps_paid.py`, REB-1096, else the year's own computed contributions, said in the assumptions; the flat-rate estimate reads them the same way, art. 1 c. 64 L. 190/2014, and `FiscalEstimate.contributions_deducted` is what it deducted), IRPEF by brackets,
the regional surcharge of the region of the legal entity's address (`law_irpef.region_key`: ISTAT code, `BZ`/`TN` by province) and
the municipal one of its cadastral code (`MunicipalSurcharge.surcharge`: the exemption threshold first, then the brackets by slice
on the whole income), the withholdings the customers kept on the settlements of the year (`withholdings_suffered`, credited to the
year `WithholdingCreditLaw` says) taken off the gross IRPEF alone, and what they exceed it by kept as `credit`, never a negative
tax; the surcharges are added after in `irpef.with_surcharges` (which `due_total` uses). **No figure
without its facts**: no INPS category (and the fact each category reads: `inps_covered_elsewhere`, `inps_trade`, `inps_before_1996` only
where the ceiling decides) means no INPS and so no IRPEF, since a base with no contributions deducted would overstate it; no
address means no surcharge; a 2026 municipal row that is `pending` (the Finanze shows `0*` until 20 December) means no municipal
figure and no total, and the previous year's rate is not borrowed in code: after 20 December the Finanze carries it over on its own
list, and the next import of that list brings it. **The IRPEF is net of its detractions (REB-1087)**: art. 13 c. 5 TUIR for
self-employment income (`IrpefLaw.self_employment_detraction`) and art. 12 TUIR for the spouse, the children of 21 to 29 and the
disabled ones and the cohabiting ascendants (`IrpefLaw.family_detractions`), both computed on the reference income (the income before the
contributions are deducted, not the base) and shown as negative `parts` between `gross_irpef` and the withholdings. The family ones read
`fiscal_profile.household` (`fiscal/household.py`, one JSON document per tax year, asked in the form for `it-standard` only; `None` or
another year's is «not stated»: no family detraction, and the assumptions say so rather than naming it missing); the same household
gives the regions' rules (`RegionalSurchargeLaw.child_rule` and `reduced_rate`, `ProgressiveDetraction` for Bolzano). A surcharge is
tested on the net IRPEF (`net == 0` gives none and asks for no address). A fact a computed rule needs and the household leaves open (the
income of the others a disabled dependant depends on, for Veneto and Marche) gives no regional figure and is named in `missing`. Not
applied, and written in the A5 register as to verify: the 50 euro of art. 13 c. 5-ter, Puglia's 375 euro with fewer than four children,
Campania's 30 euro for a disabled child, Bolzano's second detraction above 90,000 euro read literally. The readings' `irpef` is that
net IRPEF (`irpef.amount`), which is what the advances are based on. A what-if `revenue` carries no `person`, and the economic overview draws no projection or net for it.
**A frozen year of another pack wins** (`_estimate_year`): the ordinary pack's law covers 2026 too, but a flat-rate year frozen when
the entity left the flat-rate pack is served as it was.

**«Tasse» draws what the server says and adds nothing (REB-1026).** `GET /api/analytics/fiscal` answers `readings`
(`fiscal/readings.py`): one line per tax the pack lists for the year (`TaxYearLaw.taxes`, taxes design § 1), each with its
`estimate`, `declared` (the typed return's tax due) and `paid` (no source writes one yet, so always `null`), the reading that
`counts` (paid over declared over estimate, by stage, never by time) and the totals of each column, which add one kind of
reading only and say how many lines they leave out. A figure that does not exist is `null`, never zero, and `estimable` tells a
tax the code has no estimate for (its rule is «to verify») from one whose profile parameter is missing. The dates are
`GET /api/analytics/fiscal/calendar` (an administrator's, module `taxes`). The `it-company` pack carries its own calendar (REB-1033): IVA as the ordinary regime's, IRES and IRAP balance and advances, withholding on pay to others, the chamber of commerce fee date; the income-tax and fee dates are `calendar_year_only` rules, and `tax_calendar(pack, year, calendar_year_end=...)` is the one function any caller reads a pack's dates through (the tax-calendar read passes the legal entity's year end), so a legal entity with another year end gets no date that is wrong. `features/taxes/` holds the page (`/app/taxes`,
tabs «Imposte», «Scadenze» and «Rate», one light page with no tabs for a solo flat-rate space) and the Home's next two dates
(`HomeTaxDates`: a block after «Quando arrivano i soldi» on the solo Home, under its first figure on a phone, and a band under «Da fare» on the company Home at every width); the folder
is under `no-browser-arithmetic.test.ts`, so nothing there parses an amount. «Scadenze» merges the
pack's dates with the year's tax due items (`useTaxDueItems`: `GET /api/due-items`, open and settled, tax items only), each with its
amount and state pill («Previsto», «Da pagare», «Scaduto», «Pagato»; a day with items drops the pack's line for the taxes the items
cover); «Rate» lists the instalment plans and the notice plans (`GET /fiscal/plans`) with the instalments' items; the tax panel
changes the advance method (`FiscalEstimate.advance_method`) and the June instalments, and «Nuovo piano» enters a notice's plan.
A carried difference that waits («da confermare» in the description) shows as `DueItemRead.carry_to_confirm` and an administrator
confirms it with `DueItemService.confirm_tax_carry` (REST `POST /api/due-items/{id}/carry-confirmation`, person-only: no agent
credential, no MCP tool; column `due_items.carry_confirmed_at`, timeline `tax_carry_confirmed`); a rewrite with the same description keeps the
confirmation, another difference asks again. A date's F24 (REB-1076) is a chip on its row («da creare», «creato», «pagato · ricevuta») with the group's server net;
its panel groups the day's items (`POST /api/f24-groups`, credits among them), shows the pack's code of each line
(«codice da verificare» when none), ungroups an unpaid group and links or detaches the receipt (an inbox `tax_document`).
Typing a declared figure waits for its own card.

**IRAP of an srl is pack data (REB-1065).** `CompanyTaxLaw.irap` (`fiscal/law_irap.py`) holds the base (`IrapBaseItem`: which items of sections A and B of art. 2425 c.c. add, are deducted or are left out, art. 5 c. 1 D.Lgs. 446/1997), the deductions of art. 11 (permanent staff cost, the flat deduction for staff who are not permanent, the small-base table of c. 4-bis, and `small_base_deduction`), the territorial rule of art. 4 and the 2026 ordinary rate of every region and autonomous province (`rate_for(year, region)`, keyed by ISTAT region code with `BZ` and `TN` apart; the caller takes the region from the place data of the address). The lookup answers `None` for a year or key it has no row for, never a guess. The module docstring names the simplification the estimate has to make (revenue, purchases and staff cost instead of the income statement) and what is not modelled: the rates of special subjects and the regional reductions that depend on the company.

**An srl's activity chooses some IRAP rates, and it is asked, never guessed (REB-1086).** `fiscal_profile.ateco_2007_code` and `ateco_2025_code` (`NN.NN.NN`, migration 0113, `schemas.COMPANY_PARAMETERS`; only `it-company` reads them, `_check_taxpayer_parameters` refuses them on the other packs and `_pack_defaults` clears them) are what the regional rows read: the lists are written in ATECO 2007 and table 1 of D.L. 21/2026 (two points more for the energy activities in 2026 and 2027) in ATECO 2025. `IrapLaw.rate_for(year, region, IrapActivity)` answers an `IrapRateAnswer` (`fiscal/law_irap_ateco.py`): the first row of the region's table whose codes hold, else the ordinary rate with the notes a person must read, else no rate and why (`unanswered`: a code is needed, a list the pack cannot decide, no row published for table 1). The rows are the Finanze dataset's, one `AtecoRate` each with its `CODICE_ALI`; the long lists are `law_irap_ateco_lists.py`. A region with a list and no code stated gives no IRAP figure (the ordinary row is wrong for most companies in Toscana, Umbria and Emilia-Romagna); a region with no list gives the ordinary rate and says table 1 was not checked. The period of art. 11 c. 4-bis.2 is pro-rated on the calendar year the period ends in (`calendar_year_days`: the day count of a leap year is the pack's inference), for the small-base table, the 400,000 test and the 1,850 euro (days of the employment and part-time share, `BasisEmployee.part_time_percent`). A change to a recorded year of `laws` or `irap_rates` needs a ledger correction (`docs/yearly-tax-law.md`).

**An srl with places of business in several regions splits its IRAP base by the pay of the staff of each (REB-1092).** `legal_entity_sites` (migration 0125, `legal_entities/site_service.py`, `/api/legal-entities/{id}/sites`, person-only `update_legal_entity_sites`) holds the places of business beside the registered office, each with its `region` (the key of the IRAP rate table: an ISTAT code or `BZ`/`TN`, validated against the pack's rows by `irap_regions()`), an optional city and an optional address; the registered office is the legal entity's own address and is not a row. A staff member works at one place, `staff_members.site_id` or `at_registered_office` (a check says never both; both empty is «not stated»; the composite foreign key keeps the site inside the person's legal entity), and a site with people assigned is not removed. `fiscal/irap_sites.py` is the pure rule (art. 4 c. 2 D.Lgs. 446/1997 and the instructions to quadro IR, cited in its docstring): it applies only when a site is in another region than the registered office's (`splits`), so one site, none, or sites in the registered office's own region are the single-region estimate of before, with the same figures. The key is the gross pay of the payroll estimate (`PayrollService.staff_pay_in_range`, directors included, closed months as closed); a person counts for the site's region from three calendar months of service in the period, and the pay of a shorter service goes to the registered office's region, as does the activity of a site with no staff for that time. A person with no place stated, or with no estimable pay, stops the split: `irap.missing` names them and `irap.amount` and the total are `None`, never a guess. The rate of each region comes from `IrapLaw.rate_for` through the caller's function, so the ATECO rule of REB-1086 is not repeated; `irap.regions` carries each part (pay, share, base, rate, tax) and `rate` and `region` are `null` when it is filled. Not modelled, and said in the payload: staff seconded to or from third parties, the regions' own deductions and credits, the history of transfers.

**Tax balances and advances are due items (REB-1074).** `TaxDueItemService.refresh_tax_due_items` (`fiscal/tax_items.py`) writes, per legal entity,
tax (`irpef`, `substitute_tax`, `ires`, `irap`) and tax year, the balance and the two advances as due items out to the tax office
(`tax_balance` and `tax_advance`, restricted kinds of the module `taxes`, so every read, settlement, schedule line and Home figure of
one is an administrator's and a `full_access` token's). `fiscal/advances.py` holds the pure lookups over the pack: the rule
(`advance_rule`: percentage, 40/60 or 50/50, floor, single-payment limit, from `IncomeTaxAdvanceLaw`, `FlatRateAdvanceLaw` or
`CompanyAdvanceLaw`), the amounts and the dates (a person's and the flat-rate regime's from the pack's calendar, a company's from
its fiscal year end for a year end on the last day of a month, except that a calendar-year company's first advance and balance follow the calendar, so the 2026 deferral of art. 1-sexies D.L. 63/2026 to 20 July reaches them, REB-1100; a company's advance is paid once when its **first instalment** is not above 103 euro, for IRES and IRAP alike, art. 17 c. 3 D.P.R. 435/2001, REB-1098); a rule, figure or date the pack does not state is a `Missing` with
its reason and gives no item. An srl's withholdings suffered are credited to the fiscal period of the invoice's accrual (`WithholdingCreditLaw(income_basis="accrual")`, `company_withholdings_suffered`), netted in the IRES estimate itself (`ires.withholdings`, `ires.credit`) and never a credit item of their own; the F24 read says when to tick «non coincidente con anno solare» (`fiscal_year_not_calendar_year`). The base of an advance is the **historical** method's (default: the previous year's declared tax,
else its estimate) or the **forecast** method's (the current estimate), chosen per legal entity and tax year with
`TaxAdvanceMethodService.set_tax_advance_method` (REST `PUT /api/analytics/fiscal/advance-method`, privileged MCP tool of the
same name, `full_access`, table `tax_advance_methods`). An item is `forecast` on an estimate, `expected` on a declared figure,
`settled` when paid. The refresh is idempotent and called where a figure or choice changes (`set_declared_tax_figure`,
`set_tax_advance_method`) and by the daily pass (`AutomationPass._refresh_tax_items`): an item that is as it should be is left
alone, one that is not is dropped and recreated, one a payment touched is never changed, and the difference a paid advance leaves
when its base moves is carried to the next item of the tax with «da confermare» in its description. A payment is created only from
today to `HORIZON_DAYS` ahead. **INPS writes its two advances only (REB-1097):** `fiscal/inps_advances.py` asks
`InpsYearLaw.advances` for a person's `tax_advance` items with `tax = "inps"`, on the previous year's estimated income
(`AnalyticsService.estimated_inps_income`) and the INPS law of the year, always `forecast` (a declared INPS figure is a
contribution, not the income the advances are computed on); a missing fact (category, gestione, the flat-rate 35% election of
a flat-rate person) gives no item and the reason. The four fixed instalments of the minimum and the balance are not written
yet, and an INPS item has no F24 code yet.

**Instalment plans and notice plans are stored and write their own items (REB-1075).** `TaxInstalmentPlanService.set_tax_instalment_plan`
(`fiscal/instalment_plan.py`; REST `PUT /api/analytics/fiscal/instalment-plan`, privileged MCP tool of the same name, `full_access`,
table `tax_instalment_plans`) stores the number of monthly instalments of one income tax, or of all of a year's, for the balance of a
tax year and the first advance of the next (the same day); the dates and the interest are never stored but read from the pack
(`plan_math.plan_schedule` over `payment_rules.instalment_schedule`, the last instalment 16 December of the year the return is filed).
`TaxDueItemService` then writes `tax_instalment` items (`instalment_slot_id`) in place of the single June items and **stops
rewriting a payment's instalments once a payment touches one**, and the service refuses to change or remove a plan then (409); the
November advance is never split. `TaxNoticePlanService` (`fiscal/notice_plan.py`; `POST/PUT/DELETE /api/analytics/fiscal/notice-plans`,
`GET .../plans`, tools `create_tax_notice_plan`, `update_tax_notice_plan`, `delete_tax_notice_plan`, `list_tax_plans`, table
`tax_notice_plans`) stores a tax office notice typed by hand and writes its `notice_plan` items with it; the refresh never touches them.

**Tax credits and F24 groups (REB-1076, REB-1100).** A credit is a negative due item of the restricted kind `tax_credit` (table `tax_credits` carries its origin), and **no code writes one today**: a withholding suffered is deducted inside the income-tax return (RN33 col. 4 into RN34), so the person's IRPEF estimate and the advance base are already net of it (`ordinary_estimate.py`) and it is no line of an F24, and the credit of an IVA period is taken in by the next periodic settlement (`credit_in`), not an F24 compensation line. `TaxCreditService` (REB-1076) wrote both and counted the withholding twice; REB-1100 removed it, and migration 0120 drops the open `tax_credit` items of the two origins (`withholding_suffered`, `iva_carry_forward`), deletes their `tax_credits` rows and dissolves the open F24 groups that held them; a credit that is a line of a settled group or has a payment on it stays, logged by id. The `tax_credits` table, its origins and the credit-line machinery below stay for a future origin (a credit a return shows and a person uses in compensation, art. 17 D.Lgs. 241/1997; it needs a new origin, a migration and a typed entry), and for the credit lines of F24s already settled. `F24GroupService` (`fiscal/f24.py`; REST `/api/f24-groups`, privileged MCP tools `group_f24`, `ungroup_f24`, `list_f24_groups`, `full_access`) groups tax-office due items of one date, credits included, into an F24: each line keeps the pack's code (`fiscal/f24_codes.py`, frozen at grouping) or none, read «codice da verificare» (IVA 60xx, withholding 1040 and payroll codes are not in the pack). A tax forecast becomes `expected` when grouped; a grouped item is frozen (`DueItemService` refuses drop, restate, reschedule and manual settle; the tax refresh keeps it). One bank movement equal to the net settles the group: `MovementMatchService` offers an open group as a candidate and settles it as a whole (`MovementMatchItem.credit`), undo reopens it. A group whose net is zero or a credit (REB-1093) has no movement to match and is no candidate: a person marks it filed (`F24GroupService.file_f24`, REST `POST /api/f24-groups/{id}/file`, person-only like confirming a match, so no MCP tool; `reopen_f24` takes it back). It settles through the same writers as a match (`DueItemService._record_payment`, `F24GroupRepository.settle`, no movement): debits paid in full, credits spent oldest tax year first only as far as the debits need, the rest of a credit released and open for another F24 (`F24GroupItem.used_amount`, `settlement_id`; `F24GroupRead.settled_by`, `credit_left`). A group with an amount to pay cannot be filed. Migration 0113 relaxes `ck_f24_groups_settled` (a settled group has its day, a movement only ever sits on a settled one). Receipts are inbox items of kind `tax_document` linked to the group (`f24_group_receipts`).

**The srl's annual chamber fee is pack data (REB-1068).** `FiscalPack.chamber_fees` (one `ChamberFeeLaw` per fee year, read by exact year with `chamber_fee_for`; `fiscal/law_chamber_fee.py`) holds the 2014 tiers, the 50% cut, the chambers with the provinces they cover and their 2026 increases (20% for the 61 of the decree of 17 March 2026, plus 50% for the six Sicilian ones, Napoli none). `ChamberFeeLaw.fee(year, turnover, province)` answers whole euro and `None` for a year other than its own and for an unknown or unmapped province (`OG` and `OT`, which D.P.R. 183/2026 adds: no chamber verified for them), never a guess; `BT` is Bari's and `SU` and `VS` Cagliari-Oristano's (REB-1100). `first_year_fee` is the fixed amount of a company that registered in the year (the first tier, cut and increased, due `first_year_days` after the application): `TaxDueItemService` writes it as an `expected` item dated that many days after `LegalEntity.registration_date` (the day of the application, optional, typed in the Azienda form) when its year is the fee year, and the turnover-based `forecast` otherwise. It computes the fee of the head office only; local units are not modelled.

**The IVA settlement is `IvaSettlementService.settle_iva_period` (REB-1021).** For a legal entity whose pack has a
periodic IVA (`FiscalPack.has_periodic_iva`: `it-standard`), by `FiscalProfile.iva_frequency` (monthly or quarterly),
it computes one period together with every earlier period back to the first one ever settled, so the carry-over is
never stale, and stores each in
`iva_settlements`: sales VAT (not a split-payment sale, the buyer pays that), reverse-charge VAT owed, deductible
purchase VAT, the credit and the small debit carried in, the quarterly 1% and the signed result. The figures of the
law (the 100 euro limit, the periods that may wait, the 1%, the quarterly turnover limits) are `IvaSettlementLaw` in
the pack, the payment date is its calendar's (`payment_date`), and the rows of the A5 register behind them are marked
verified or to verify. A result that is owed and may not wait becomes a due item (`iva_settlement`, tax office, tax
`iva`, `DueItemService.create`, then `restate`/`drop` on a recomputation); a payment recorded on it freezes it. A sale is
placed by its issue date, not its operation date (an invoice stores none): that is in the A5 row and not hidden. The trigger is a step of the daily `pigrocrm automations` pass
(`AutomationPass._settle_iva`, with the `purchases` module on): `settle_elapsed_periods` settles the latest elapsed
period of the current year, which recomputes every earlier one, and keeps the previous year in the window until the
pack's annual-return date. It is idempotent, isolated per legal entity (savepoint, failure counted by class name,
stderr, exit 1), and a period whose due item already has a payment is reported on stderr and left as it was, not
raised. `GET /api/iva-settlements?year=` and the MCP tool `list_iva_settlements` (`full_access`) read what it wrote;
`settle_iva_period` stays a service call (closing a period calls it, below). Not here: the December advance, cash-basis IVA,
the srl's IVA dates (the `it-company` pack carries a calendar but no `iva` law, so it has no periodic settlement).

**«IVA» draws what the server reads and adds nothing (REB-1024).** `IvaRegisterService` (`core/fiscal/iva_register.py`) is
the read: `list_periods` answers a year's periods with the ladder (the stored settlement, or for a period not settled
yet the running figures from `compute_figures` and the previous period's stored result, `null` when that carry-over is
unknown: the previous period is not stored while an earlier one is) and the state (`open` to the last day included),
and `read_sales_register` and `read_purchase_register` list one period's documents, 50 at a time by `offset`, with the
summary by rate and the period's totals. The registers select with `iva_settlement.sales_conditions` and
`purchase_conditions` and total with `sales_vat` and `purchase_vat`, the settlement's own, so a register adds up to its
line to the cent; a sale is placed by its issue date, a purchase by its `iva_period`, a declared register gap sits in the
period of the invoice before it, and a split-payment sale is listed with its VAT and counted as `vat_due` zero. REST is
`GET /api/iva-periods`, `/api/iva-periods/entities` (the legal entities whose pack settles IVA by period) and
`/api/iva-registers/{sales,purchases}`, the MCP tools `list_iva_periods` and `get_iva_register`; all an administrator's
(`full_access` for a token) under the `purchases` module. The page is `/app/vat` (`features/iva/`, under
`no-browser-arithmetic.test.ts`): it opens on the period running now, one legal entity at a time (the page's own «Azienda»
select under «Tutte le aziende» with several), the sidebar entry is drawn by a `NavGate` (`useNavGates`: an administrator,
`purchases` on, at least one eligible entity) and a figure the server did not send reads «non calcolabile». The «Integrazioni» chip waits for the integration
documents, which do not exist. A period is read with its lock (`closed`, `closed_at`, `closed_by`).

**A closed IVA period refuses every write that changes its registers (REB-1036).** `IvaPeriodLockService`
(`core/fiscal/iva_lock.py`, table `iva_period_locks`, migration 0101): an unscoped administrator, or a token with
`full_access`, closes a period that has ended (`close_iva_period`, `POST /api/iva-period-locks`, tool `close_iva_period`)
and reopens it (`reopen_iva_period`, `DELETE /api/iva-period-locks/{year}/{number}`, tool `reopen_iva_period`). Closing
settles the period first, in the same transaction (`settle_iva_period(commit=False)`), so what is frozen is what the
registers add up to now, and a settlement a payment froze that would change refuses the close. The row is a switch: the
id is a uuid5 of the legal entity, the frequency, the year and the number, so the timeline of the period
(`iva_period_closed`, `iva_period_reopened`) is one sequence across any number of closes, and reopening deletes the row.
`period_start` and `period_end` are stored so a write finds its lock by the day it is dated, whatever the frequency. The
checks are `assert_writable` calls, in the service that owns each write: `PurchaseService` (an invoice or credit note,
by its `iva_period`, the old and the new one: create, update, cancel, delete and restore; a receipt or an expense is in
no register) and `InvoiceEmission.issue` and `annul` and `IssuedInvoiceImporter.import_issued` (by the issue date, which
is how the sales register places a sale; the FatturaPA confirmation checks it too and reports the sentence as the invoice's note). The refusal is a 409 whose sentence names the period («il periodo IVA 1°
trimestre 2026 è chiuso: riaprilo...»), the same over REST and MCP. A payment, `mark_transmitted_externally`, a draft
or a proforma, a declared register gap (it changes a listing, not a figure) and the settlement itself are not refused, since none changes a register. The routes and tools sit under the `purchases` module while the sales checks do not: a closed period stays closed if the module is switched off, and is reopened by switching it back on. A write holds a shared advisory
lock on its legal entity's year of locks and a close or a reopen an exclusive one (a period with no row has nothing to
`FOR SHARE`, as `PeriodLockRepository.lock_month` says), so a close never commits between a write's check and the write.
It is separate from the monthly `period_locks` of `timetracking/locks.py`, which stays the hours' and the cost side of
purchases', and neither implies the other. The page's «Chiudi il periodo» and «Riapri» are an unscoped administrator's
(`lockMode` in `features/iva/lock.ts`; a scoped one is told why), each behind its question, and the period in the
URL is `?period=` **with** `?frequency=`: a number is a period only with its frequency, so the sidebar's legal entity
cannot turn a quarterly `3` into March.

**An IMAP or PEC mailbox is `mailboxes/imap_*` and its password is sealed with its own key (REB-1015).**
`ImapReader` (`imap_client.py`) is the only code that opens an IMAP socket: TLS or STARTTLS only, the host
resolved once and every address global (`resolve_public`), the certificate checked, headers and BODYSTRUCTURE
fetched with `PEEK` on a read-only INBOX, never a body, a flag or a delete. Its failures are `auth` or
`transient` and become the mailbox's health (`active`, `failing`, `auth_failed`, `disconnected`); an
`auth_failed` mailbox is not tried again until a person types a new password. The password is sealed with
`PIGROCRM_MAILBOX_TOKEN_KEY` derived per space (`space_mailbox_token_key`), never the JWT secret. Connecting
is `require_person`, so a token is refused whatever it carries. Tests use `tests/fakes/fake_imap.py`, a
fake server on the loopback with a real TLS handshake: pass `resolve=loopback` and `fake.client_context()`,
never a flag in production code. `mail-sync` reads IMAP beside Gmail; a sink per purpose (`MessageSink`) is
how REB-1016 plugs in.

**«Esporta per il commercialista» is `/app/export` and draws what the server says (REB-1028).** `features/export/` is an
administrator's page (the entry names `action: 'build_accountant_export'` in `components/nav.ts`, so the sidebar and «Altro» hide it
from other roles by the `lib/permissions.ts` table, which `permissions.test.ts` pins to the core's `require_admin` strings; the first
entry gated by role) with the tab and the open export in the URL (`?tab=prepare|exports|links&export=&link=`). «Prepara» is the form: company (a
field from the second company on, preset from the sidebar, «Tutte le aziende» a separate option never preselected and absent for a
scoped admin), period (the calendar month, quarter or year, opening on the last complete period of the company's IVA frequency) and
«Cosa contiene», which is `GET /api/accountant-export/parts` (the parts that apply to the company's modules and pack, in the index's
order). «Esportazioni» reads `GET /exports?legal_entity_id=&year=&offset=&limit=` (`items` and `total_count`, 50 rows at a time) and its
panel reads the snapshot the build kept in `checklist`: the parts with their counts, the documents that could not be attached (the
«N aperti» of the list; gold, never danger, and never recomputed after the build) and the download of each ZIP. The snapshot keeps the
`cause` of each part left out: a part the company's **pack** carries not is named in the panel, one a **module** switched off is never
drawn, and a part whose source is not built (`unavailable`) is drawn nowhere, since the page may not name a module that is off or not
built. A build is synchronous: the page shows its row («In preparazione») while the request runs, a refusal stays as a row with the
reason and «Riprova» (which makes a new export, since an export is never replaced), and the mutation's own `onSuccess` refreshes the
list so an admin who left finds it done. The readiness checklist and the payroll parts have no data yet and are drawn nowhere; each
joins with its own card. The folder is under `no-browser-arithmetic.test.ts`.

**An export is handed over by a share link or a copy in Drive (REB-1047).** `core/accountant_export/sharing.py` is two halves. `ExportShareService`
(`create_export_share_link`, `list_export_share_links`, `get_export_share_link`, `revoke_export_share_link`; tables `accountant_export_links` and
`accountant_export_link_openings`, migration 0104, both under the `legal_entity_visible` policy of their export) is an administrator's whose scope
covers the export; only the create is also a `full_access` token's (the MCP tool `create_export_share_link`, which answers the link's `url`, never
bytes), and list, read and revoke are `PERSON_ONLY_ACTIONS`. A link is 256 random bits (`token_urlsafe(32)`) stored **only as its SHA-256**: the
raw address is in the answer that makes it and nowhere else, so the page shows it once in the dialog and no list has a «Copia». It lives 7 days,
at most 30, and ends by `revoked_at` or `expires_at`; an export deleted at 13 months ends its links. `ExportShareDoor` is the public half, not a
`*Service` because it has no actor: it binds the system scope for the one row a token hash names, answers `export_link_unknown` (404),
`export_link_expired` or `export_link_revoked` (410), reads the file **then** counts the opening (`download_count` by `UPDATE ... + 1`) and writes
an audit row (when, client address, which ZIP), so a download that failed leaves none. The routes are `POST /api/accountant-export/shared/describe`
and `/download` (`routers/shared_export.py`, no login, the token in the **POST body** so it reaches no log, history or `Referer`; the file is
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`), throttled by `ratelimit.spend_one` (30 a minute per client), a backoff on a client
that keeps sending tokens that do not resolve, and nginx's own `pigro_share` zone (`infra/origin/nginx/rate-limits.conf`, applied by hand to the
host's vhosts). What the accountant opens is `/app/shared-export?t=` (`SharedExportPage`; `PUBLIC_APP_ROUTES`; `main.tsx` strips the `?t=` before
anything runs; its nginx location is `access_log off` in the three conf files). The link's address is built from `PIGROCRM_PUBLIC_URL` only, never
from a request's `Host`. «Link condivisi» is the third tab (`?tab=links&link=`), every link of the space with its count, state and openings.
**Drive delivery** (`drive_delivery.py`, `ExportDriveService`, table `accountant_export_drive_writes`) copies the ZIPs under
`commercialista/<entity-slug>/<period>/<timestamp>/` (`2026-t3`, `2026-09`, `2026`, or the two dates) when `storage_backend` is `gdrive`; with local
storage `GET /api/accountant-export/drive` answers `local_storage` and the panel says so with no button, with Drive chosen and no account and folder
`not_connected`. It is a person's act (`write_export_to_drive` is person only, no tool), and the 13-month deletion removes the copies too.

**An export says whether its period is ready, and the payroll consultant has an export of its own (REB-1048).** `accountant_export/checklist.py` is «Il periodo è pronto?»
(design § 2): `compute_checklist` runs the seven `CHECKS` (`inbox`, `purchase_documents`, `invoice_files`, `bank_statements`, `bank_movements`, `iva_period`, `payroll`)
over the entity's `Unit`s and answers a count of open points and the first five by name per check, never a figure. A check of a module that is off, of a pack it does
not apply to (IVA for a flat-rate entity) or of a part not asked for (`payroll` only with `payroll_parts=["payslips"]`) is not in the list. `get_export_checklist`
(`GET /api/accountant-export/checklist`, an administrator's, `full_access` for a token, in `FULL_ACCESS_ACTIONS`) computes it now and stores nothing; **a build computes
it again and keeps it under `readiness` in the export's `checklist` snapshot and in a table of `indice.csv`**, so what the page showed is never trusted and a later fix
never rewrites it (an export made before this has no `readiness`; the page then reads the missing documents, as it did). `bank/coverage.py::statement_gaps` is the
statement check (no grace: a period is exported after the fact). **`kind="payroll_changes"` is «variazioni paghe»** (`registry.py::PAYROLL_CHANGE_PARTS`, `payroll.py`): hires
and leavers by `start_date` and `end_date`, the people whose record was edited in the period (the timeline stamps the edit and never from what to what, so the file says
«condizioni attuali: i valori precedenti non sono conservati») and the approved expense notes whose `payroll_month` is in the period; one company, an administrator who reads
pay, the `staff` module, no checklist. **`payroll_parts=["payslips"]`** adds the payslips part (`PartDefinition.opt_in`, in no build unless asked and listed by
`GET /parts` with its `opt_in` name). Either sets `accountant_exports.payroll` (migration 0106), and **an export with `payroll` has no plain link**: `create_export_share_link`
refuses one with no `recipient_email`, and `ExportShareDoor._live` refuses a payroll row's link that has none. A link with a recipient is a **code link**: `/shared/code`
mails six digits (`mail/export_code.py`, 15 minutes, five wrong answers burn it, one mail a minute and five an hour per link, held only as a salted hash in
`accountant_export_link_codes`), `/shared/verify` trades the right one for a half-hour `access` (random, held as a hash), and `describe` lists no file and `download` serves
none without it (`export_code_required` 403). The nginx location of the door names the two new routes (both vhosts).

**A space mailbox's attachments reach the inbox through `BillingSink` and nothing else (REB-1016).** `inbox/mail_intake.py`
decides from BODYSTRUCTURE which parts to fetch (an accepted extension, not inline, not a small image, not the
mailbox's own mail), fetches them with `ImapReader.fetch_part` and files them with `InboxUploads.upload(origin=...,
commit=False)`: the items are `source = 'mailbox'` with the message's sender, subject and date and never its body, and
they stay when the mailbox is disconnected. A personal mailbox cannot hold `billing` and the sink refuses one anyway.
A sink never commits and `discard` undoes its bytes; the tests use the fake IMAP server with `Part(content=...)`.

**Signing a letter is `SigningService`, and it is off unless the installation has a Documenso (REB-1019).**
`core/documenso/` is the API v2 client (`client.py`), its no-redirect transport and `routing.py`;
`core/collaborators/signing.py` sends (`send_engagement`: the framework agreement first when the freelancer has none
active, the letter waits `pending` and is released when it is signed; a draft text never leaves; **only the freelancer
signs**), and `signing_recovery.py` confirms, stores the sealed PDF as the document's next version, mails both copies and
releases letters. The state is columns of the `documents` row (`signature_state`, `envelope_id`, `signed_version`...,
migration 0099), null on every other document. `PIGROCRM_DOCUMENSO_URL` and `PIGROCRM_DOCUMENSO_API_TOKEN` together switch
signing on, `PIGROCRM_DOCUMENSO_WEBHOOK_SECRET` the root route `POST /api/documenso/webhook`; empty (the compose default) the
app boots as before, a send answers 503 `signing_unavailable`, and a letter is downloaded, signed by hand and its PDF uploaded
(`upload_signed_copy`, a person's act). A new envelope's `externalId` is `<tenant id>:<document id>` (`root:` for the root
installation): Documenso takes any string there (v2.18.0 `z.string().trim().max(255)`, no index). The webhook routes by it
(`routing.route_envelope`) and **ignores** an envelope whose `externalId` is not this installation's own, a bare hub document
id included unless the leads space holds it. Cancelling an engagement must go through `SigningService.engagement_service()`,
or a letter out for signature refuses it. The signature recovery is a step of the daily `pigrocrm automations` pass
(`cli/signing.py::sweep_space`), isolated per space; `contracts-check` and `documenso-check` are commands. The MCP container has
no mail key by design, so the MCP send tools answer 503 there until that is decided.

**What the CRM tells the hub about an engagement goes through an outbox (REB-1106).** `NoticeOutbox.emit`
(`core/collaborators/outbox.py`) writes one `engagement_notices` row in the transaction of every
change the hub must hear of (created; a draft's role, dates, fee or client changed, `updated`; sent;
signed; cancelled; closed; a framework notice served, once for each live engagement of that
freelancer): the engagement's own `sequence` from 1, and the whole read model as it was (`payload`,
never recomputed at delivery). A freelancer with no `hub_freelancer_id` writes nothing. The row has
no foreign key and no legal-entity policy on purpose (it is bookkeeping read by no person), and
**only the space `PIGROCRM_LEADS_SPACE` names writes or sweeps them** (a session carries its space
in `session.info[SPACE_SLUG_KEY]`, set by the registry's factories and the cron walk; one that
carries none, a test's, does not hold a notice back): `pigrocrm
reminders`, the quarter-hourly pass, calls `HubNoticeSweep` (`collaborators/hub_notices.py`) ahead
of the reminders, so an hour's hub outage is retried by itself, with a backoff of 5 minutes doubling
to 6 hours. It posts to `{PIGROCRM_HUB_URL}/api/hub/engagement-notices` under
`PIGROCRM_HUB_NOTICES_TOKEN` (the hub's `REBASE_PIGRO_NOTICES_TOKEN`; never the engagements token),
HTTPS only or plain HTTP to this machine, no redirect followed, no database lock held across the
call. 2xx is `delivered`; 401, 403, 404, 408, 409, 429, 5xx and no answer are retried; any other 4xx
is `rejected` and goes to `stderr` and the pass's exit status. Without the token the feature is
inert and the rows wait. The same token and URL ask `POST /api/hub/referrer-name` for the letters'
`{{segnalato-da}}` and `{{azienda-segnalata-da}}` (`LetterService._referrers`): printed only for a
verified referral, stored with the document version, kept once stored, asked again at send only
when the stored value is «nessuno», and «nessuno» on any failure; an administrator cannot type
either field. The MCP container does not hold the token, so a letter made there stores «nessuno» and
is asked again at send. Design: `collaborators-in-the-crm` § 5.

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
with `deals` on for a space whose `rebase_engagements` row has `space_created`, and the engagements
door gives a space it opens `solo` with `contracts` on; nothing for the
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

**A legal entity's region and municipality are read from its address, never stored (REB-1069).** `core/places/` holds ISTAT's
«Elenco comuni italiani» as `datasets/istat_municipalities.csv` (rebuilt by `scripts/import_istat_municipalities.py` from the
workbook, never the `.csv` ISTAT keeps beside it, which is a 2024 copy; the source, the day and the SHA-256 are in the sidecar
JSON). `LegalEntityRead.place` (REST, MCP `describe_legal_entity`) is `locate(city, province, country)`: `matched` with the ISTAT
and cadastral codes and the region, `unknown` when no municipality of that province reads like the city (case, accents,
apostrophes and spacing ignored, the other language of a bilingual name accepted; the region still comes from the province),
`not_italian` otherwise. A save never depends on it, a replacement list needs no migration, and `place` is on the read only, so
`update_legal_entity` drops it on the round trip like the two image flags. `GET /api/legal-entities/municipalities?province=` feeds
the picker in «Impostazioni → Aziende», which writes the exact name into the city. **Name a shipped dataset directory
`datasets/`, never `data/`:** the root `.gitignore` ignores any directory called `data`, so git refuses to add it and hatchling
leaves it out of the wheel.

**The municipal IRPEF surcharge is the Finanze's lists, one CSV per tax year (REB-1063).** `fiscal/datasets/municipal_surcharge_<year>.csv`
(rebuilt by `scripts/import_municipal_surcharge.py --year` from the Department's «Elenchi generali aggiornati quotidianamente», which are
rebuilt daily; the sidecar JSON says the day, the SHA-256 and whether the list is `final`) is read by `fiscal/law_municipal_surcharge.py`
and reached as `pack.municipal_surcharge` on `it-standard`: `lookup(year, cadastral_code)` and `rate(year, cadastral_code, income)`,
keyed by the cadastral code of `LegalEntityRead.place`. A row is `adopted`, `none` (never instituted), `pending` (`0*` before 20 December:
no deliberation yet, no figure) or `irregular` (a municipality's published sentences are not brackets: no figure); a code or a year
that is not there is no figure too. 2026 was partial when read (Milano and Roma had none): re-import it after 20 December 2026. How the
bracket rates are applied to an income is not in the list and not in this module: the estimate card's, with the A5 row «to verify».

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
