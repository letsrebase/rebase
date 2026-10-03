# Several aziende in one space: one register per azienda, one team per space

Date: 2026-10-03. Status: **proposed, awaiting Lorenzo's sign-off** (§10 lists what is
his to decide). Tracker: REB-614, project "Let one space run several aziende",
milestone "Design how one space holds several aziende". The six milestones after it are
the implementation order (§9). Written in English, per the repository's rule; the
specs it reads, `2026-08-20-slice-3-fatturazione-design.md`,
`2026-09-08-spazi-un-database-per-tenant-design.md` and
`2026-09-17-inviti-e-ruoli-di-uno-spazio-design.md`, are grandfathered Italian records.

The decisions below were taken with Ivan on 2026-10-03 and are recorded as taken. The
document leaves open only what §10 names.

## 0. Why

A space is one issuer, by construction and by record. Slice 1 wrote «Un'istanza = un'azienda»
(`2026-08-06-pigrocrm-core-crm-mcp-design.md:75`); slice 2 made the issuer a single row,
«Riga singola. Ragione sociale, P.IVA, …» (`2026-08-10-slice-2-documenti-e-template-design.md:143`);
and the database enforces it rather than the code: `emitter_profile.singleton` and
`fiscal_profile.singleton` are `Boolean`, always `True`, `unique=True`
(`packages/core/src/pigrocrm/core/emitter/models.py:24`, `fiscal/models.py:33`), so a
second insert fails on the constraint. The fiscal profile's own docstring refuses a
second row in words: «a second fiscal profile would create two answers to "which regime
am I in"» (`fiscal/models.py:55-56`).

Everything downstream assumes the one issuer:

- **The register.** `invoice_counters` is keyed by `anno` alone (`invoices/models.py:285`),
  the partial unique index is `(anno, numero)` (`invoices/models.py:192-196`), the storage
  prefix is `fatture/{anno}/{numero}` (`invoices/naming.py:83`), and every register read in
  `invoices/repository.py` (`lock_counter`, `last_issued_date`, `numbers_present`, `gaps`,
  `existing_by_number`) takes the year and nothing else.
- **The import.** `classify_direction` decides whether a parsed FatturaPA file is ours or
  a supplier's by comparing the file's `fornitore` against the one `EmitterProfile` row's
  P.IVA and codice fiscale (`invoices/import_direction.py:65-82`); its module docstring says
  so: «whichever space's database a request opened is the only `EmitterProfile` row this
  classifier can even see».
- **The taxes.** `paid_revenue_for_calendar_year` sums `Invoice.imponibile` across the whole
  space (`fiscal/ceiling.py:87-97`); `AnalyticsRepository.annual_revenue` does the same for
  the estimate (`analytics/repository.py:465-476`); both read one `FiscalProfile`.
- **The documents and the mail.** `DocumentService._template_scope` puts the live profile
  under `emittente` (`documents/service.py:709-731`), the reminders read the live
  `firma_email` and IBAN (`gmail/solleciti.py:423-464`), the Gmail `From` name is the one
  `ragione_sociale` (`gmail/send.py:536-545`), and the logo in every PDF is a static file
  copied from `render/assets/media/` (`render/pdf.py:173`): `logo_key` and `firma_key` are
  stored and read by no code.

Ivan, 2026-10-03: «se un singolo utente in un singolo spazio volesse gestire 2 entità
legali, come si potrebbe fare?». The case is humancraft today: a forfettario (a natural
person, who by law holds one P.IVA), an SRL, and a foreign company, working the same
customers and the same pipeline from one Google account, with the foreign customers
meant for the foreign company and the Italian ones for the SRL or the forfettario. And
it is meant to become product, not a flag for one space.

**The alternative, and why it lost.** Two spaces plus a page that sums them is almost
built: since REB-345 one identity lists and enters every space it may reach
(`2026-09-23-one-identity-across-several-spaces-design.md` §3), the switch is one click
in the sidebar's profile menu (`apps/web/src/components/AppShell.tsx:263-290`), and
inviting a colleague into one company would cost nothing, since roles are per space. It
lost on what the business actually is: one anagrafica, one pipeline, one mailbox. Two
spaces mean the same customer twice, a deal that has to know its space before it has a
proposal, hours tracked after choosing a space, and a Google account connected to one
space only (`2026-09-08-spazi…` §5, line 57, «Per gli spazi Gmail e Drive non esistono»). The
summary page would also be the first cross-space read in the product, which the
identity spec deliberately kept out (§5 there, «Every space's own database still has no
column, table, index or query that has ever heard of another space»). So the azienda
goes inside the space, and the price is §4: a visibility boundary inside one database,
which this product has never had, and which is therefore enforced by the database and
not by remembering to filter.

**What the word is.** «Azienda» is what the product says, in every label, message and
MCP description, and `azienda` is the identifier in code, tables and routes. Not
«soggetto», which is a tax word, and not «entità», which in this codebase already means
a record type (`activities.entity_type`).

## 1. The decisions, one paragraph each

**1.1 The azienda is the emitter row, freed of its singleton.** `emitter_profile` becomes
`aziende`, one row per azienda, same columns minus `singleton`, plus `nome` (the short
name the selector shows, «humancraft», distinct from `ragione_sociale`), `predefinita`
(exactly one `True` per space, a partial unique index) and `attiva`. The ORM class is
`Azienda`; `EmitterProfileService` becomes `AziendaService` with `list`, `get(id)`,
`create`, `update`, `set_default`, `deactivate`. Not a second table beside the old one:
two tables that both say who issues a document is the duplication rule broken in the one
place it matters most. The timeline rows already written with `entity = "emitter_profile"`
stay as they are; history is not rewritten.

**1.2 The fiscal profile belongs to an azienda.** `fiscal_profile` keeps its name and its
columns, loses `singleton`, and gains `azienda_id` (`NOT NULL`, `UNIQUE`, FK). One fiscal
profile per azienda, exactly one, which is the same guarantee as today multiplied by the
number of aziende. The snapshot taken at issue (`FiscalSnapshot`, `fiscal/service.py:28-45`)
keeps its fields, one of which widens (`codice_regime` becomes `str | None`, 1.3): it
already freezes what an invoice needs, and an issued invoice keeps reading its own copy
(`invoices/pdf.py:195-232`, `invoices/fatturapa.py:649-659`).

**1.3 A foreign azienda has a pack of its own and no SdI.** `pack_id` already decides the
ceiling and rivalsa arithmetic (`fiscal/pack.py:190-195`, REB-361). A new pack,
`non-it`, declares no ceiling, no bollo, no rivalsa. The pack alone is not enough: bollo,
natura and the line arithmetic come from the regime strategy, which `resolve_regime`
picks by RF code (`fiscal/regime.py:161-189`, called from `invoices/service.py:219, 234,
285, 924` and `fiscal/service.py:93`). So `resolve_regime` takes the pack as well as the
code, the `non-it` pack selects a `ForeignRegime` strategy (no bollo, no natura default,
the rate as entered), and `codice_regime` becomes `str | None` on the column, on
`FiscalSnapshot`, `FiscalProfileUpsert` and `FiscalProfileRead` (`fiscal/schemas.py:105,
135, 189`), `NULL` on a foreign azienda because RF01..RF19 are FatturaPA values and mean
nothing abroad. `InvoiceService.export_xml` (the `export_invoice_xml` action) on a
foreign azienda's invoice refuses with a domain error («questa azienda non emette fatture
elettroniche»), and the PDF carries the azienda's own header. The currency stays EUR: `invoices.divisa` exists and
is frozen, but multi-currency is a feature of its own and not this one.

**1.4 The register is per azienda.** `invoice_counters` is keyed `(azienda_id, anno)`, the
unique index becomes `(azienda_id, anno, numero) WHERE numero IS NOT NULL`,
`invoice_register_gaps` gets the same key, and every register read in
`InvoiceRepository` takes `azienda_id` with the year. `lock_counter`'s
`INSERT … ON CONFLICT DO NOTHING; SELECT … FOR UPDATE` idiom (slice 3 §3) is unchanged;
the lock is simply narrower, which is a property, not a cost: issuing for the SRL never
waits on the forfettario's lock. The displayed number stays `{anno}/{numero}`
(`naming.numero_completo`): in a list filtered to one azienda it is unambiguous, and in
«tutte» the list shows the azienda's `nome` beside it. The storage prefix for a new
invoice is `fatture/{azienda_id}/{anno}/{numero}`; artifacts already written keep the
key stored on their row and are never moved. The proforma sequence stays one per space:
`PROV-{anno}-{seq}` is a reference, not a fiscal number (slice 3 §5), and two aziende
sharing it costs nothing.

**1.5 An import lands on the azienda whose P.IVA issued it.** `classify_direction` takes
the active aziende instead of one profile: a `fornitore` matching azienda X's P.IVA or
codice fiscale is `outgoing` on X; one matching none is `incoming` and skipped as today
(`import_classification.py:32-56`). `review_import` shows the azienda in its answer, and
`confirm_import` and `import_issued` lock X's counter and build the snapshot from X's
profiles (`invoices/service.py:1394-1395`). An XML whose `fornitore` matches two aziende
is a configuration error (`ValidationFailed` on `aziende.partita_iva`), and the unique
index on `(partita_iva) WHERE partita_iva IS NOT NULL` in §2 makes it impossible to
save in the first place.

**1.6 The azienda is assigned on the customer and proposed by its nation.** `customers`
gets `azienda_id NOT NULL`. On creation the API proposes one when the caller gives
none: the active azienda whose `nazione` equals the customer's, else the only active
azienda whose `nazione` is not `IT` when the customer's is not `IT`, else the
`predefinita`; the SPA shows the proposal
in the form and the person may change it. The field stays editable on the customer
afterwards. Changing it moves nothing that already exists (next paragraph).

**1.7 Deals, contracts, documents and invoices take their azienda at creation and keep
it.** Each of `deals`, `contracts`, `documents`, `invoices` gets `azienda_id NOT NULL`,
copied from the parent the record is born under (the customer; the deal, for an
invoice born from one; for a document, whichever of customer, deal or contract it
hangs on, since exactly one is set, `documents/schemas.py:55-58`) in the service's
`create`, with no column on the request schema except where §3 says so. It is
not a join to the customer, deliberately: Ivan, 2026-10-03, «i deal aperti restano dove
sono, non si sposta niente di quello che già c'è». A customer moved from the SRL to the
foreign company tomorrow keeps its open deals and its issued invoices where they are,
and only what is created after the move follows it. An issued invoice freezes its
azienda twice over, in the column and in the snapshot's `emittente`. `costs` gets
`azienda_id` nullable: a cost with a deal copies the deal's, a cost without a deal is
«condivisa», `NULL`, and appears only in «tutte». `period_locks`, `cost_categories`,
`pipeline_stages`, `templates`, `field_definitions`, `users`, `space_settings`, the
Google account and the Drive grant stay space-level: a closed month is closed for
everybody, a stage list is one.

**1.8 Every document and every mail speaks for the azienda of its record.**
`_template_scope` reads `emittente` from the document's azienda; `InvoiceService` from
the invoice's; the reminders read `firma_email` and the IBAN from the invoice's azienda;
a draft started from a customer reads the customer's; the Gmail `From` display name is
that azienda's `ragione_sociale` and the Message-ID domain falls back to its `sito_web`.
A mailbox stays one per user, as today (`google_accounts.user_id` is unique,
`gmail/models.py:59`): an azienda has no mailbox of its own, the address is the sender's
and the display name is the azienda's, which is what Ivan asked for («un account google a
livello di spazio che comprende le N entità»). The logo and the signature become real:
`PUT /api/aziende/{id}/logo` and `/firma` store a PNG or SVG under the azienda's own key
(`logo_key`, `firma_key`, finally read), the Typst job copies that file in place of the
bundled `media/logo.png`, and an azienda without a logo renders its `ragione_sociale` in
type and no image. `regenerate` on a document version re-reads the azienda's live profile
exactly as it re-reads the live emitter today (`documents/service.py:843`).

**1.9 The dashboard sums the cash and keeps the taxes apart.** `GET /api/analytics/overview`,
`GET /api/dashboard/sales`, `GET /api/dashboard/receivables` and `GET /api/analytics/pnl`
take an optional `azienda_id`; omitted means every azienda the caller may see, which is
what «tutte» shows: incassato, da incassare, scaduto, pipeline and projection added up.
`GET /api/analytics/fiscal`, `/ceilings` and `/ceilings/simulate` take a required
`azienda_id` when the space has more than one azienda (a 400 names the parameter) and
resolve to the only one the caller may see otherwise; the coefficients of a forfettario and the arithmetic
of an SRL do not add, and the ceiling exists only on a pack that declares one. In «tutte»
the economic tab renders one «Stima fiscale» card per azienda whose profile carries
coefficients, and nothing for the others. The P&L stays per deal and does not change.

**1.10 The selector lives in the sidebar and appears from the second azienda on.** Above
the navigation groups, below the global search: a `Select` from `@rebase/ui/select` with
«Tutte le aziende» first and each active azienda by `nome`. The choice is kept per space
and per user in `localStorage`, the key shape `features/get-started/invoiceHandoff.ts` already uses
(`pigrocrm.azienda:${tenantPrefix || '/'}:${userId}`), and exposed by an
`AziendaProvider` under `AuthProvider`. Lists and dashboards pass it as `azienda_id`;
query keys that depend on it carry it, and a switch invalidates them rather than
clearing the cache. A space with one azienda renders no selector, no «Azienda» field in
any form and the same Impostazioni it has today: the feature is invisible until a second
azienda exists, which is the condition for shipping it to every space.

**1.11 An invitation may be scoped to one azienda, and the database keeps the scope.**
`POST /api/users/invites` gains `aziende: [uuid]` (omitted: every azienda, as today);
accepting it writes the rows of `user_aziende`. The same field goes on the admin's role
update of a member. A user with no rows is unscoped and sees the space as today; a user
with rows sees, and writes, only the records of those aziende, on every route, in the
search, in the digest and over MCP, because the rows are hidden by Postgres row-level
security and not by a `WHERE` someone remembered to add (§4). Roles do not change:
`admin`, `collaboratore`, `readonly` stay per space (`2026-09-17…` §1, «The three roles
hold exactly as they do today»), and a scope composes with any of them. Two rules
follow. A space keeps at least one active **unscoped** admin at all times, the same
guard that already keeps one active admin. And space-level configuration (Team, Spazio,
Google, Drive, templates, stages, categories, period locks) requires an unscoped admin:
`Actor.require_admin` grows a `require_unscoped_admin` used there, while an azienda's
own profile is editable by an admin whose scope includes it.

**1.12 Nothing that exists moves.** The migration binds every existing row to the one
azienda the space already has, which becomes `predefinita`. No reassignment by nation,
no re-keying of storage, no renumbering. Humancraft's second and third aziende are
created from Impostazioni and its foreign customers are moved by hand, one by one, which
with the rule in 1.7 moves nothing behind them.

## 2. Data model and migration

One Alembic revision per milestone that changes the schema, `0045` to `0048` after
`0044_proposals`, each a transaction of its own, cut along the steps below as §9 says.
Each reaches every space at the first boot after its deploy through `pigrocrm
ensure-space-defaults` (`projects/pigrocrm/AGENTS.md` § Migrations), as every migration
does. The steps, in order:

1. `ALTER TABLE emitter_profile RENAME TO aziende`; drop the `singleton` unique
   constraint and column; add `nome VARCHAR(80) NOT NULL` backfilled from
   `ragione_sociale`, `predefinita BOOLEAN NOT NULL DEFAULT false`, `attiva BOOLEAN NOT
   NULL DEFAULT true`; `UPDATE aziende SET predefinita = true` (there is at most one
   row); partial unique index `uq_aziende_predefinita ON aziende (predefinita) WHERE
   predefinita`; partial unique index `uq_aziende_partita_iva ON aziende (partita_iva)
   WHERE partita_iva IS NOT NULL`. A space with no emitter row yet (possible only
   between signup and the first save, since `TenantService` writes one at provisioning,
   `tenants/service.py:165-175`) gets one inserted from the space's slug, so the `NOT
   NULL` columns below have a value to point at.
2. `fiscal_profile`: add `azienda_id UUID REFERENCES aziende(id)`, backfill from the one
   azienda, set `NOT NULL`, `UNIQUE`; drop `singleton`; `codice_regime` to nullable.
3. `invoice_counters`: add `azienda_id`, backfill, `NOT NULL`; primary key becomes
   `(azienda_id, anno)`. `invoice_register_gaps`: the same, unique `(azienda_id, anno,
   numero)`.
4. `customers`, `deals`, `contracts`, `documents`, `invoices`: add `azienda_id UUID
   REFERENCES aziende(id)`, backfill from the one azienda, set `NOT NULL`, index each.
   `invoices`: drop `uq_invoices_anno_numero`, create `uq_invoices_azienda_anno_numero
   ON invoices (azienda_id, anno, numero) WHERE numero IS NOT NULL`.
5. `costs`: add `azienda_id` nullable, backfill from the deal's where `deal_id IS NOT
   NULL`, leave `NULL` otherwise, index.
6. `user_aziende (user_id UUID REFERENCES users(id) ON DELETE CASCADE, azienda_id UUID
   REFERENCES aziende(id) ON DELETE CASCADE, PRIMARY KEY (user_id, azienda_id))`. Empty
   after the migration: nobody is scoped until an admin scopes them.
7. `invitations`: add `aziende UUID[] NULL` (the scope the invitee will get; `NULL` is
   unscoped). An array and not a join table, because an invitation is spent once and
   its rows would be orphans the moment it is accepted or revoked; it carries no foreign
   key, so accepting drops any id that no longer names an active azienda.
8. The row-level security policies of §4, and `FORCE ROW LEVEL SECURITY` on every table
   that gets one.

`compare_metadata` in the migration test must come back empty after each revision.
Each `downgrade` reverses its own steps; the one thing `0045` cannot give back is a
second azienda's rows, so it refuses when `aziende` holds more than one row.

Nothing in the registry database changes. `identities`, `tenants` and the chooser know
nothing of aziende: a scope is a fact inside one space, like a role (`2026-09-23…` §1,
«a role is meaningless outside a space»).

## 3. API

Routes added, in `apps/api/src/pigrocrm_api/routers/aziende.py`:

| Route | Who | What |
|---|---|---|
| `GET /api/aziende` | any member | The aziende the caller may see, `predefinita` first. A scoped user gets their scope only. |
| `POST /api/aziende` | unscoped admin | Creates one; `AziendaCreate` is `EmitterProfileUpsert` plus `nome` and a required `fiscal_profile` body, so an azienda never exists without its profile (1.2). |
| `GET /api/aziende/{id}`, `PUT /api/aziende/{id}` | admin whose scope includes it | Read and replace, the old `/api/emitter` shape. |
| `POST /api/aziende/{id}/predefinita` | unscoped admin | Moves the default. |
| `DELETE /api/aziende/{id}` | unscoped admin | Deactivates (`attiva = false`); refused on the default. Never a row delete: an azienda that issued an invoice stays readable forever. The answer says how many customers still point at it, and creating a deal, a document or a proforma under such a customer is refused («sposta prima il cliente su un'azienda attiva»). |
| `GET /api/aziende/{id}/fiscal-profile`, `PUT …` | admin whose scope includes it | The old `/api/fiscal-profile` shape, keyed. |
| `PUT /api/aziende/{id}/logo`, `PUT …/firma`, `DELETE …` | admin whose scope includes it | A PNG or SVG under 1 MiB into `DocumentStorage` under `aziende/{id}/`; writes `logo_key` / `firma_key`. |

Routes removed: `GET/PUT /api/emitter` and `GET/PUT /api/fiscal-profile`. No alias
answering the default azienda: an alias is a second way to say the same thing and the
exact shape 1.1 refuses. The SPA and the MCP server are the only clients, both move in
the same milestone, and the routes already moved once without aliases, from Italian to English, in PR #220
(project "PigroCRM v4 - routes in English").

Routes changed:

- `POST /api/customers` accepts an optional `azienda_id`; the answer carries the one
  chosen or proposed (1.6). `PATCH /api/customers/{id}` accepts it too. `CustomerRead`,
  `DealRead`, `ContractRead`, `DocumentRead`, `InvoiceRead` and `CostRead` carry
  `azienda_id`, read-only everywhere but on the customer and on a cost without a deal.
- Every list route that today takes `customer_id` or `anno` takes an optional
  `azienda_id` as well: customers, deals, contracts, documents, invoices, costs, time
  entries, the calendar, the receivables. Omitted means everything the caller may see.
- The dashboard and analytics routes as 1.9 says.
- `POST /api/users/invites` and `PATCH /api/users/{id}` take `aziende: [uuid] | null`.
  `GET /api/auth/me` and `UserRead` carry `aziende: [uuid] | null`, so the SPA knows
  whether to pin the selector and the Team panel can show the scope.
- `POST /api/invoices/import` and the review step answer the azienda the file landed on
  (1.5).

Errors keep the project's shapes: `NotFound("azienda", id)`, `ValidationFailed("aziende",
"partita_iva", …)`, `PermissionDenied` with the action name. A scoped user asking for a
record outside their scope gets a 404, never a 403: the row does not exist for them,
which is what the database says and what the API should repeat, so the existence of
another azienda's invoice is not readable from the status code. Reads and the services
that `get` before they write already yield that. A write the policy refuses outright,
an `INSERT` on another azienda or an `UPDATE` that would move a row across the line,
surfaces from Postgres as `InsufficientPrivilege` (SQLSTATE 42501), which today would be
a 500: the API maps it to the same 404, and `test_azienda_scope_api.py` asserts it on a
`PATCH /api/customers/{id}` that tries to move a customer out of scope.

## 4. The scope, enforced by the database

**Why the database and not the repositories.** The spaces spec chose one database per
space over a `tenant_id` column on thirty tables because «ogni dimenticanza [sarebbe]
una fuga di dati fra clienti» (`2026-09-08…:18-20`). The azienda is that column, inside
one database, and the same objection applies to a filter in every repository: a
`select(Customer)` written next year without the clause shows B the SRL's neighbour.
The repository already answers this class of problem the same way twice: a period's
closure is a trigger (REB-359, «Enforce the day lifecycle by database trigger, not by
application code») and the one-row profiles were a unique index, not a check in the
service. So the boundary is Postgres row-level security: a scoped session cannot read or
write a row outside its scope whatever SQL the application sends.

**The session variable.** One transaction-local setting, `pigrocrm.aziende`, written
with `set_config(name, value, true)` exactly as `work_units/service.py:60-68` already
writes `pigrocrm.actor` and `pigrocrm.motivo`. Its value is `*` for an unscoped actor,
or the comma-joined UUIDs of the actor's scope. A connection with no value set reads
`NULL` on its first use and the empty string once a transaction-local value has
expired on a pooled connection (measured on Postgres 17); `string_to_array('', ',')` is
`{}`, so the predicate below is false for both: the default is closed. The scope rides
on the actor: `Actor` gains `aziende: tuple[UUID, ...] | None`, set by `get_actor` and
`callback_actor` (`deps.py`) from `user_aziende`, by `PatService.resolve` from the
owner's rows, and `None` for `Actor.system()` and `Actor.rebase()`. It is bound in one
place, `bind_scope(session, actor)`, called by `get_actor` the moment the actor is
known, by the MCP guard for the PAT's owner, and by the CLI and the jobs. Because
`set_config(..., true)` dies with the transaction and a service commits in the middle
of a request, `bind_scope` registers an `after_begin` listener on the session, which
runs the `set_config` on the connection it is handed at the start of every
transaction (measured: it fires again after each commit or rollback, and the call is
accepted inside a `REPEATABLE READ READ ONLY` transaction). On the dashboards' second
session (`get_snapshot_session`) `bind_scope` registers the listener and executes
nothing itself, because `DashboardService._open_snapshot` refuses a session anything
has already touched (`dashboard/service.py:108-124`). Nothing else in the application
ever reads the setting.

**The role.** A table owner is not subject to its own policies unless the table says
`FORCE ROW LEVEL SECURITY`, and a superuser is never subject to them. Today one URL does
everything: `PIGROCRM_DATABASE_URL` is the compose file's `POSTGRES_USER`, which is the
image's bootstrap superuser, and the same credentials run Alembic, provision spaces
(`tenants/database.py:46-48`, «under the CRM's credentials») and serve requests. So the
API gets a role of its own, `pigrocrm_app`: `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB`,
granted `SELECT, INSERT, UPDATE, DELETE` on every table and `USAGE` on every sequence of
every space's database, with `ALTER DEFAULT PRIVILEGES` so a table a later migration
creates is granted too. `PIGROCRM_DATABASE_URL` points at it; a new
`PIGROCRM_ADMIN_DATABASE_URL` keeps the owner for `alembic upgrade`, `ensure-space-defaults`
and `CREATE DATABASE`; `tenant_database_url` derives both from a space's `db_name`.
`FORCE ROW LEVEL SECURITY` goes on every policied table anyway, so the owner is subject
to them too; a superuser still is not, and the test suite's PostgreSQL user is one
(testcontainers' `test`, `rolsuper = true`), so every existing test runs with the
policies bypassed by design and only the `pigrocrm_app` fixture of
`test_azienda_scope.py` proves anything about them. The role cannot be created by a
`docker-entrypoint-initdb.d` script, which runs only on an empty data directory and
`PIGROCRM_DATA_DIR` already holds the production and preview clusters; and `ALTER
DEFAULT PRIVILEGES` is per database and per grantor. So the API boot, on the admin URL
and before `ensure-space-defaults`, creates the role and applies the grants
idempotently to the root and to every space in the registry, and provisioning does the
same for a new space right after its `CREATE DATABASE` (`tenants/service.py`). The
deploy's `.env` gains the second URL; `.env.example` documents both, and a `.env`
missing the admin URL fails the boot the way a missing `PIGROCRM_DATA_DIR` does
(REB-258).

**The policies.** One predicate, written once as a SQL function `azienda_visibile(uuid)
RETURNS boolean` marked `STABLE`:

```sql
current_setting('pigrocrm.aziende', true) = '*'
OR $1 = ANY (string_to_array(current_setting('pigrocrm.aziende', true), ',')::uuid[])
```

(the column side uncast, so `ix_*_azienda_id` stays usable) and one policy per table,
`FOR ALL … USING (…) WITH CHECK (…)`, with the same expression on both sides except
where the table below says otherwise, so a scoped user can neither read nor insert nor
move a row across the line. A nullable parent follows the `costs` shape: a row whose
parent is `NULL` is visible, and insertable, only when the setting is `*`. By table:

| Table | Predicate |
|---|---|
| `customers`, `deals`, `contracts`, `documents`, `invoices` | `azienda_visibile(azienda_id)` |
| `costs` | `azienda_id IS NULL AND current_setting(…) = '*'`, or `azienda_visibile(azienda_id)` |
| `people` | `EXISTS` on its customer; `customer_id IS NULL` only for `*` |
| `time_entries` | `EXISTS` on its deal |
| `time_timers` | `EXISTS` on its deal; `deal_id IS NULL` only for `*` |
| `invoice_lines`, `payment_reminders` | `EXISTS` on its invoice |
| `document_versions` | `EXISTS` on its document |
| `rate_cards`, `renewal_assumptions`, `contract_expenses`, `work_units` | `EXISTS` on its contract |
| `work_unit_transitions` | `EXISTS` on its work unit |
| `approvals` | `EXISTS` on its contract (`contract_id` is `NOT NULL`; the FK to work units runs the other way) |
| `proposals` | `EXISTS` on its document (`document_id` is `NOT NULL`, `contract_id` is not) |
| `invoice_counters`, `invoice_register_gaps` | `azienda_visibile(azienda_id)`, so a scoped admin declares gaps on their own register only |
| `attivita` | every non-null parent among customer, deal, invoice, person visible; all four `NULL` only for `*` |
| `gmail_message_links` | a `CASE` on `entity_type` (customer, person, deal) to the row `entity_id` names |
| `email_drafts` | the same `CASE` on its own `entity_type` / `entity_id` (`gmail/models.py:259-267`) |
| `gmail_messages` | `USING`: a visible link exists, or no link exists and the setting is `*`. `WITH CHECK (true)`: the sync and the send insert the message before its links (`gmail/sync.py:847, 877`, `gmail/send.py:602, 646`), so the symmetric check would refuse every insert by a scoped sender |
| `activities` | a `CASE` on `entity_type` over the types above; space-level entities visible to all |
| `aziende`, `fiscal_profile` | `azienda_visibile(id)` / `azienda_visibile(azienda_id)` |

Space-level tables carry no policy: `users`, `invitations`, `user_aziende` (it carries an
`azienda_id` and is the one declared exception of the introspection test below), the
tokens,
`pipeline_stages`, `templates`, `cost_categories`, `field_definitions`, `period_locks`,
`space_settings`, `automation_config`, `digests`, `google_accounts`,
`google_drive_accounts`, `gmail_known_addresses`. Reading them is harmless and writing
them is `require_unscoped_admin` (1.11).

The `*` branch short-circuits before any `EXISTS`, so an unscoped request pays nothing
beyond a settings read; a scoped request pays an index lookup per child row on foreign
keys that are already indexed and already in every list's own `WHERE`. It is measured
before the milestone closes, on the humancraft space with three aziende, for the five
lists and the three dashboards; the one table whose policy is not a plain `EXISTS`,
`activities`, is the one to watch and the one §10 asks about.

**What the scope changes elsewhere.** The digest row stays one per week
(`digests.settimana` is unique, `digest/models.py:20`) and the render is repeated once
per distinct scope among the recipients, each with that scope bound, so B's Monday mail
sums rebase alone. The global search goes through the same tables and needs no change:
the `pg_trgm` queries return what the policies let through. The MCP server binds the
PAT owner's scope, so B's agent sees rebase alone. The engagements door
(`Actor.rebase()`) is unscoped. The Gmail sync runs as the mailbox's owner
(`cli.py:732` builds the actor from the user, not from `Actor.system()`) and binds `*`
whoever that owner is: it reads one person's mailbox, it links messages to customers of
every azienda, and a scoped owner then sees of their own mailbox only what is linked
inside their scope.

**How it is proven.** `packages/core/tests/test_azienda_scope.py` opens the test database
through a `pigrocrm_app` role the fixture creates, binds B's scope, and for every
repository `list` asserts the count against the rows of B's azienda; then runs raw
`SELECT count(*)` on every policied table and gets the same numbers, which is the claim
that no code filter is involved; then opens a session with nothing bound and gets zero
rows from each, which is the closed default; then tries an `INSERT` of an invoice on the
other azienda and gets a policy error. A second test introspects `pg_policies` against
the list above and the list of tables carrying `azienda_id` or a foreign key path to
one, so a table added later without a policy fails the build the way `test_architecture`
fails an undeclared import. `apps/api/tests/test_azienda_scope_api.py` repeats the
boundary over HTTP for the five lists, the three dashboards, the search and a direct
`GET` by id (404), and over MCP for `search_everything` and `list_invoices`.

## 5. SPA

- **`AziendaProvider`** (`src/lib/azienda.tsx`) under `AuthProvider`: the aziende from
  `GET /api/aziende`, the selected id or `null` for «tutte», the setter, and `scoped`
  (true when `me.aziende` is a list). With one visible azienda the selection is pinned
  to it and the selector is not rendered.
- **The selector** in `AppShell.tsx`, between the search button and the navigation
  groups. A scoped user with one azienda sees a label, not a control.
- **Impostazioni → Aziende** replaces the `issuer` and `fiscal` tabs of
  `features/settings/tabs.ts`: a list with the default marked, «Nuova azienda», and per
  azienda the two panels that exist today (`EmitterPanel` and the settings `FiscalPanel`) plus the logo
  and signature upload. With one azienda the page opens straight on it, so the two
  panels read as they do today. The old routes `/app/settings/issuer` and `/fiscal`
  redirect, as the Italian ones already do.
- **Forms.** The customer form gets «Azienda» with the proposal from `nazione` applied
  live; `NewProformaDialog` names the azienda that will issue («Emessa da humancraft»);
  the detail pages of deal, contract, document and invoice show it read-only in the
  header; the cost form shows it only for a cost without a deal («Condivisa» by default).
- **Lists** pass the selection; in «tutte» the invoice list shows `nome` beside the
  number and the customer list shows it as a column.
- **Dashboard.** The selection is one more input of `useEconomicOverview`,
  `useCommercialDashboard`, `useReceivablesDashboard`; in «tutte» the «Stima fiscale»
  card repeats per azienda with coefficients (1.9). The URL search keeps `{tab, da, a,
  base}` and does not carry the azienda: it is a session choice, like the sidebar's
  collapsed groups, not a view a link should fix.
- **Team.** The invite dialog gets an «Aziende» multi-select, all checked; a member row
  shows «Tutte» or the names; the role dialog edits the same.
- **Query keys.** `queryKeys` gains the azienda where a read depends on it; a switch
  calls `invalidateQueries` on those prefixes.
- **First steps.** The «I tuoi dati fiscali» step reads the default azienda.

## 6. Rendering and mail

The Typst header templates read `emittente.*` as today; `build_header` and
`build_invoice_header` take the azienda's logo bytes from storage and write them into the
job directory under the name the template expects, or emit the header without `#image`
when there is none. The signature image goes the same way on the invoice PDF. The static
`media/logo.png` and `sign_is.png` leave the bundle: REB-48 already called shipping a
person's assets a defect, and a per-azienda upload is what finally removes the need. The
mail paths change one lookup each: `_signature_name`, `_emitter_scope`, `_iban` and the
Message-ID fallback resolve the azienda from the record they are sending about, through
the invoice for a reminder and through the customer for a draft.

## 7. MCP surface

Tools renamed and added, every description in Italian as the product's own:
`describe_emitter_profile` → `list_aziende` and `describe_azienda(azienda_id?)`;
`update_emitter_profile` → `update_azienda(azienda_id?, …)` and `create_azienda(…)`
(admin, agent-allowed like `update_fiscal_profile` since ORB-188: setup, not history);
`describe_fiscal_profile` and `update_fiscal_profile` take `azienda_id?`. Every optional
`azienda_id` resolves to the only azienda when there is one, so a prompt written for a
one-azienda space keeps working. `create_customer` takes `azienda_id?` with the same
proposal rule; `search_everything`, `search_customers`, `search_deals`,
`list_invoices` and the four dashboard tools (`get_commercial_dashboard`,
`get_economic_dashboard`, `get_receivables_dashboard`, `get_operational_dashboard`) take
`azienda_id?` as a filter. `test_mcp_surface_coverage.py` and `test_mcp_invoice_ban.py`
move with the names; nothing joins or leaves `AGENT_FORBIDDEN_ACTIONS`. The agent's
scope is its owner's (§4), so no tool needs to check it.

## 8. Tests, by milestone

- **Azienda row.** Migration round-trip on a space with and without an emitter row;
  `uq_aziende_predefinita`; create, default move, deactivate refusals; the two settings
  panels unchanged by snapshot test with one azienda; MCP tool renames covered.
- **Register.** Two aziende issue in the same year and both get `1`; the lock of one
  does not block the other (two sessions); `import_issued` with a number taken on the
  other azienda succeeds; `declare_gaps` per azienda; the import review names the
  azienda; an XML with an unknown P.IVA is `incoming_skipped`; a foreign azienda's
  `export_xml` refuses and its PDF renders without bollo.
- **Chain.** The nation proposal on create; a moved customer leaves its deals, documents
  and invoices behind; every list filtered by `azienda_id`; «tutte» equals the sum.
- **Rendering and mail.** A document of azienda X carries X's header and X's logo; a
  reminder for X's invoice signs as X and names X's IBAN; the `From` display name.
- **Dashboard.** `/overview` in «tutte» equals the sum of the per-azienda calls;
  `/fiscal` without `azienda_id` on a two-azienda space is 400 and resolves on a
  one-azienda space; no ceiling on a `non-it` pack.
- **Scope.** §4's two test files, plus `e2e/aziende.spec.ts`: A creates a second
  azienda, invites B scoped to it, B logs in, sees the pinned selector, the filtered
  lists, a 404 on A's other invoice, and a digest preview with rebase's numbers only.

## 9. Rollout: the order the milestones land in

1. **Turn the emitter profile into the azienda row.** Migration steps 1, 2, 6 and 7 of
   §2 without the `NOT NULL` children yet; `AziendaService`; the routes of §3 for
   aziende; Impostazioni → Aziende; the MCP renames. Ships invisible: one azienda, no
   selector.
2. **Number and import invoices per azienda.** Steps 3 and the `invoices` part of 4;
   the register per azienda; the import; the `non-it` pack. Still invisible with one
   azienda.
3. **Assign customers to an azienda and inherit it down the chain.** The rest of
   step 4 and step 5; the proposal; the selector; the filters. The first visible piece,
   and only from the second azienda on.
4. **Render every document and email from its azienda.** §6 and the uploads.
5. **Sum the cash across aziende and keep the taxes apart.** 1.9 end to end.
6. **Scope an invitation to one azienda with row-level security.** §4: the role, the
   policies, the binding, the invitation field, the Team panel, the tests. Last because
   every column it guards must exist first, and because it is the one milestone that
   changes how the API connects to the database.

Humancraft's cut-over, after milestone 3: create the SRL and the foreign azienda in
Impostazioni, each with its fiscal profile; move the foreign customers to the foreign
azienda by hand; from then on new deals follow. After milestone 6: invite B scoped to
rebase. Nothing that exists is touched at any step.

The decision rows for `docs/design/DECISIONS.md` go in the sign-off commit, with the
status line of this document: one for «a visibility boundary inside a space is a
Postgres policy, never an application filter», one for «the API connects as a
non-owner role; the owner runs migrations and provisioning».

## 10. Decisions for Lorenzo

1. **Two database URLs, or one role made less powerful.** §4 proposes `pigrocrm_app`
   for requests and the owner for migrations and provisioning, which is two URLs in
   every `.env`. The alternative keeps one URL and makes that role `NOSUPERUSER` after
   bootstrap, relying on `FORCE ROW LEVEL SECURITY` to cover the owner. It is one
   setting fewer and one `ALTER ROLE` that has to run on every environment by hand,
   which is the kind of step this repository has ruled out of a release. I recommend
   the two URLs.
2. **A policy on `activities`, or no timeline for a scoped user.** The polymorphic
   `entity_type`/`entity_id` makes its policy the only `CASE` in the set and the only one
   whose cost is not a plain index lookup. The alternative hides the timeline from a
   scoped user altogether. I recommend the policy, measured, with the hide as the
   fallback if the measurement is bad.
3. **Rename `emitter_profile` to `aziende`, or keep the table name.** A rename touches
   the entity label of every error and timeline row written from now on; keeping it
   leaves a table whose name says one row when it holds three. I recommend the rename.
4. **One line about the law, or none.** A forfettario who controls an SRL in the same
   line of business is excluded from the regime (L. 190/2014, art. 1, c. 57, lett. d).
   The product does not check it and should not read as asserting the combination is
   fine; I recommend one sentence in the azienda form's help text and nothing in the
   code.
