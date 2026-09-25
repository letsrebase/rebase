# Hours per match: the CRM door and the hub report. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a match turns active the hub gets the freelancer a PigroCRM space with
rebase as the customer and the letter as a deal; the freelancer logs hours there; the
admin reads them per day, week and month against the letter's expected days, invoices
included, on the match and over MCP.

**Architecture:** Two milestones, one per project, the CRM's first. In
`projects/pigrocrm` a service-token door on the root installation:
`PUT /api/rebase/engagements/{match_id}` sets up space, customer «rebase» and deal
idempotently (a registry table `rebase_engagements` keys it), and
`GET /api/rebase/engagements/{match_id}/report` answers the deal's hours per entry with
the invoice each sits on. In `projects/hub` a `rebase_core.engagements` service calls
that door through the existing `HttpCall` seam when a match turns active, retries from
`contracts-sweep`, stores slug, deal id and state on the match, groups the report by ISO
week and month, and hands it to the admin API, the «Consuntivo» page and two MCP tools.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic (hub only; the CRM registry is
`create_all`), FastAPI, Pydantic 2, pytest with testcontainers Postgres; React 19,
TanStack Router and Query, `@rebase/ui`, Vitest. pandoc and Typst for the letter.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-hours-report-per-match-design.md`
(REB-489). Section numbers below (§) are the spec's.

## Global Constraints

- Everything is English except what the product says to a person: UI copy, mail text,
  API error sentences, OpenAPI and MCP descriptions, CLI output are Italian (root
  `AGENTS.md`, Conventions).
- Commits: Conventional Commits, first person, the body's last line the card (`REB-N.`);
  `git add <paths>`, never `-A`; **no AI co-author trailer in any form**: ignore any
  harness attribution reminder, run `git log -1 --format=%B` after every commit and
  amend if one appears; a migration is a commit of its own. **Never `git stash`**: the
  stash is shared across worktrees; copy files aside instead.
- The hub imports nothing from `pigrocrm*`, and the CRM nothing from `rebase_*`
  (`projects/hub/AGENTS.md`, The one rule). `packages/core` imports neither adapter;
  `apps/api` never imports `apps/mcp`. The CRM's `test_architecture.py` reads core's
  `pyproject.toml` dependencies literally: no new runtime import in core without a
  declaration there.
- The web apps write no UI primitive: everything from `@rebase/ui/*`.
- Hub migrations are conditional (`ADD COLUMN IF NOT EXISTS`, the `pg_constraint` guard
  for a `CHECK`), and `test_migrations.py`'s `compare_metadata` stays `[]`. The CRM
  registry has no Alembic: a new `TenantsBase` table is created by
  `ensure_tenants_database` at boot, and only if its module is imported in
  `tenants/database.py`.
- Every new setting takes three edits: `config.py`, `.env.example`, and the
  `x-api-environment` anchor of that project's `docker-compose.yml`
  (`REBASE_*` for the hub, `PIGROCRM_*` for the CRM).
- A seam never raises past its boundary and nothing logs an address or a token
  (`rebase_core/http.py`, `rebase_core/pigro.py`).
- Money and hours are `Decimal`, never float; a rate has six places
  (`deals.tariffa_oraria`), hours two.
- Local checks (`preflight` is not installed on this Mac): `uv run ruff check
  projects/<name>`, `uv run mypy` on the project's packages, the pytest suites as
  separate processes in the **foreground** and never two DB-backed runs at once, then
  `pnpm --filter <hub|web> lint`, `test`, `build`. Never use ports 55432 or 55433 for a
  test database (they are ssh tunnels to production). Check `df -h /` before building an
  image; stop under 3 GB free.
- Shared test helpers live in modules beside the tests (the hub's `fakes_contracts.py`),
  importable by name; imports go at the top of a test file (ruff E402).

## Review Focus

Five failure modes the spec implies and the plain feature tests would miss. Each is
pinned by a test in the task named.

1. **Two letters of one freelancer activating in the same minute.** One space, two
   deals: the advisory lock on the lowercased address, and a second call that finds the
   row the first wrote (Task A4, `test_two_matches_one_email_share_one_space`).
2. **A crash between creating the deal and recording it.** The next call finds the deal
   by its deterministic name under the customer and completes the row instead of
   creating a twin (Task A4, `test_retry_after_deal_created_but_unrecorded_reuses_it`).
3. **Case and spaces in the address.** `Ada@Studio.it ` owns the space registered as
   `ada@studio.it`; the lock and the lookup both lowercase and strip (Task A4,
   `test_owner_lookup_is_case_insensitive`).
4. **A week that straddles two months or two years.** Per-week totals use ISO weeks
   (`2026-12-31` is `2026-W53`, `2027-01-01` too), per-month totals the calendar month,
   and both sum to the same total (Task B2, `test_report_groups_by_iso_week_and_month`).
5. **The freelancer deletes the deal.** The CRM answers `409`, the hub writes `errore`
   with the CRM's sentence, «Riprova» does not recreate anything (Task A4,
   `test_deleted_deal_answers_409`; Task B2, `test_link_writes_the_crm_sentence_on_409`).

---

## Card groups and order

Each task is one Linear card and one agent run. Milestone A is the CRM's, on the draft
PR of branch `ivansala/milestone-crm-engagements-door` (from `origin/main`). Milestone B
is the hub's, on the draft PR of branch `ivansala/milestone-link-a-match-to-its-hours`,
created from the tip of PR #416's branch `ivansala/milestone-simpler-matches` (it
rewrites `matches.py`, `signing.py`, «Crea match» and «Match e contratti»); it merges
after #416. A task starts when every task it depends on is on its milestone branch.

| Task | Card | Title | Depends on |
|---|---|---|---|
| A1 | REB-490 | Add the engagements token and the «rebase» actor to the CRM | none |
| A2 | REB-491 | Store rebase's engagements in the CRM registry | none |
| A3 | REB-492 | Share the space's welcome step between the signup and the door | none |
| A4 | REB-493 | Set up a freelancer's space, customer and deal for a match, idempotently | A1, A2, A3 |
| A5 | REB-494 | Answer a match's hours per entry with their invoices | A4 |
| A6 | REB-495 | Serve the engagements door over the API under its token | A5 |
| A7 | REB-496 | Ship the CRM door to the preview and exercise it | A6 |
| B1 | REB-497 | Carry the expected days and the Pigro link on a match | none |
| B2 | REB-498 | Link a match to its CRM deal and read its report from the hub core | B1 |
| B3 | REB-499 | Link on activation, from the sweep, and on «Riprova» over the API | B2 |
| B4 | REB-500 | Say the link's state on the card and show the hours link to the member | B3 |
| B5 | REB-501 | Read the report and retry the link over the hub MCP server | B3 |
| B6 | REB-502 | Ask the expected days in «Crea match» and show the link on the lists | B4 |
| B7 | REB-503 | Show a match's hours per day, week and month on «Consuntivo» | B6 |
| B8 | REB-504 | Put the reporting clause in the letter and record the decision | B1 |
| B9 | REB-505 | Prove the flow on the preview and land the hub milestone | A7, B5, B7, B8 |

## File structure

**CRM (`projects/pigrocrm`)**

- `packages/core/src/pigrocrm/core/config.py`: `engagements_token`.
- `packages/core/src/pigrocrm/core/actor.py`: `ActorType` gains `"rebase"`, `Actor.rebase()`.
- `packages/core/src/pigrocrm/core/engagements/__init__.py`, `models.py` (the registry
  row `RebaseEngagement`), `schemas.py` (`EngagementUpsert`, `EngagementRead`,
  `EngagementReport` and its parts), `service.py` (`EngagementService`).
- `packages/core/src/pigrocrm/core/tenants/database.py`: imports `engagements.models`.
- `packages/core/src/pigrocrm/core/tenants/welcome.py`: the post-provision step the
  signup route and the door share.
- `apps/api/src/pigrocrm_api/service_token.py`: `require_service_token`.
- `apps/api/src/pigrocrm_api/routers/engagements.py`: the two routes; `main.py` lists it.
- Tests: `packages/core/tests/test_engagements.py`, `test_actor.py` (extended),
  `apps/api/tests/test_engagements_api.py`, `test_tenants_api.py` (unchanged behaviour).

**Hub (`projects/hub`)**

- `packages/core/migrations/versions/00NN_match_pigro_link.py` (NN: next free when the
  branch lands; `0020` is PR #407's).
- `packages/core/src/rebase_core/models.py`: eight columns on `Match`.
- `packages/core/src/rebase_core/contract_schemas.py`: `MatchCreate.giorni_previsti`,
  the `pigro_*` fields on `MatchRead` and `MatchListItem`, `MemberContract.pigro_url`,
  `MatchReport` and its parts.
- `packages/core/src/rebase_core/config.py`: `pigro_engagements_token`.
- `packages/core/src/rebase_core/engagements.py`: `EngagementService`, the grouping.
- `packages/core/src/rebase_core/mail.py`: `engagement_ready_mail`.
- `packages/core/src/rebase_core/signing.py`: `da_collegare` on activation; `finish` and
  `sweep` call the link.
- `packages/core/src/rebase_core/match_words.py`: the link sentence and `riprova_pigro`.
- `packages/core/src/rebase_core/cli.py`: the sweep's line.
- `packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md`: the clause.
- `apps/api/src/rebase_api/deps.py`: `EngagementsDep`; `routers/matches.py`: two routes;
  `routers/members.py`: `pigro_url` rides the existing read.
- `apps/mcp/src/rebase_mcp/server.py`: two tools; `create_match` gains `giorni_previsti`.
- `apps/web/src/lib/api.ts`, `contracts.ts`, `format.ts`: types, client, labels.
- `apps/web/src/pages/admin/crea-match/CondizioniStep.tsx`, `ControllaStep.tsx`;
  `contratti/Cards.tsx`; `lists.tsx`; `Matches.tsx`; new `pages/admin/Consuntivo.tsx`;
  `router.tsx`; `pages/member/Contratti.tsx`.
- `docs/design/DECISIONS.md`: one row.
- Tests: `packages/core/tests/test_engagements.py`, `test_matches.py`, `test_signing.py`,
  `test_migrations.py`, `test_match_words.py`, `test_cli.py`, `test_contract_render.py`,
  `test_web_labels.py`; `apps/api/tests/test_matches_api.py`, `test_members_api.py`;
  `apps/mcp/tests/test_tools.py`; the web `*.test.tsx` beside each page.

---

## Milestone A: Open a door in the CRM for rebase's engagements

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-crm-engagements-door ../pigrocrm-crm-door origin/main`, `uv sync
--frozen`, and `gh pr create --draft` with the milestone's name and the checklist of
cards A1 to A7 (`.claude/skills/pr-creation`, § Before the branch exists, step 3). Read
`projects/pigrocrm/AGENTS.md` first.

### Task A1: The engagements token and the «rebase» actor

**Files:**
- Modify: `packages/core/src/pigrocrm/core/config.py` (after `registry_token`)
- Modify: `projects/pigrocrm/.env.example` (after `PIGROCRM_REGISTRY_TOKEN`)
- Modify: `projects/pigrocrm/docker-compose.yml` (`x-api-environment`, after the registry line)
- Modify: `packages/core/src/pigrocrm/core/actor.py`
- Modify: wherever the SPA or the core turns `actor.type` into a word. Find it:
  `git grep -n '"system"' projects/pigrocrm/packages/core/src projects/pigrocrm/apps/web/src`
- Test: `packages/core/tests/test_actor.py`, `packages/core/tests/test_config.py` (or
  the test file that already covers `registry_token`; find it with `git grep -l
  registry_token projects/pigrocrm/packages/core/tests`)

**Interfaces:**
- Produces: `Settings.engagements_token: str` (default `""`, `repr=False`, env
  `PIGROCRM_ENGAGEMENTS_TOKEN`); `Actor.rebase() -> Actor` with `id=None`,
  `type="rebase"`, `role="admin"`, `full_access=False`; `ActorType` includes `"rebase"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_rebase_actor_is_an_admin_that_is_not_an_agent() -> None:
    actor = Actor.rebase()
    assert actor.type == "rebase"
    assert actor.can_write and actor.can_administer
    actor.require_write("creare un cliente")  # no AgentForbidden: not an mcp actor


def test_engagements_token_defaults_empty_and_hides_from_repr() -> None:
    settings = Settings(_env_file=None)
    assert settings.engagements_token == ""
    assert "engagements_token" not in repr(Settings(engagements_token="x", _env_file=None))
```

- [ ] **Step 2: Run them, see them fail** (`uv run pytest projects/pigrocrm/packages/core/tests/test_actor.py -q -k rebase`; `AttributeError`).
- [ ] **Step 3: Implement.** In `actor.py`: `ActorType = Literal["user", "mcp", "system", "rebase"]` and

```python
    @classmethod
    def rebase(cls) -> Self:
        """rebase acting inside a freelancer's space through the engagements door
        (spec 2026-09-25 § 2.5): an admin for what it writes, never an agent, and named
        «rebase» in the timeline so the freelancer knows who did what."""
        return cls(id=None, type="rebase", role="admin")
```

  In `config.py`, under `registry_token`, with a comment in the same voice: what the
  token opens (`/api/rebase/engagements/...`), that empty means the routes do not exist,
  that it is a secret read from the environment only. `.env.example`: the same in two
  lines, «generated once (`openssl rand -hex 32`) and set identically as
  `REBASE_PIGRO_ENGAGEMENTS_TOKEN` in the hub's own `.env`». Compose:
  `PIGROCRM_ENGAGEMENTS_TOKEN: ${PIGROCRM_ENGAGEMENTS_TOKEN:-}`. Where the actor's type
  becomes a word for a person (the timeline), add «rebase» beside «sistema»; if the
  word is built in the SPA, it is one entry in that map.
- [ ] **Step 4: Run the core suite for actor, config and activities** (`uv run pytest projects/pigrocrm/packages/core/tests/test_actor.py <the config test file> projects/pigrocrm/packages/core/tests/test_activities*.py -q`). Expected: pass.
- [ ] **Step 5: Commit** `feat(core): the engagements token and the rebase actor exist` with `REB-490.` as the last line.

### Task A2: The registry table `rebase_engagements`

**Files:**
- Create: `packages/core/src/pigrocrm/core/engagements/__init__.py`, `models.py`
- Modify: `packages/core/src/pigrocrm/core/tenants/database.py` (import the module beside `identity.models`)
- Test: `packages/core/tests/test_engagements.py` (new; the registry fixture of `packages/core/tests/test_tenants.py` is the model: read how it builds `Settings` on the container and calls `ensure_tenants_database`)

**Interfaces:**
- Produces:

```python
class RebaseEngagement(TenantsBase, PrimaryKeyMixin):
    """One row per hub match (spec 2026-09-25 § 2.2): which space, which customer and
    which deal the door set up for it. `match_id` is the hub's id and the idempotency
    key; `deal_id` stays NULL between step 3 and step 6 of `EngagementService.ensure`."""

    __tablename__ = "rebase_engagements"

    match_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False, index=True)
    customer_id: Mapped[UUID | None] = mapped_column(default=None)
    deal_id: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

  (Use the same `PrimaryKeyMixin` and column idioms `identity/models.py` uses; keep the
  hub's `match_id` as a separate unique column rather than the primary key so the
  mixin's id stays what every registry row has.)

- [ ] **Step 1: Failing test** `test_registry_has_the_engagements_table`: `ensure_tenants_database(settings)` then `inspect(engine).has_table("rebase_engagements")`.
- [ ] **Step 2: Run, fails** (`uv run pytest projects/pigrocrm/packages/core/tests/test_engagements.py -q`).
- [ ] **Step 3: Implement** the model and the import line in `tenants/database.py` with a comment: same reason as `identity.models`.
- [ ] **Step 4: Run** the new test and `test_tenants.py`. Expected: pass.
- [ ] **Step 5: Commit** `feat(core): the registry records rebase's engagements` (`REB-491.`).

### Task A3: The welcome step, shared

**Files:**
- Create: `packages/core/src/pigrocrm/core/tenants/welcome.py`
- Modify: `apps/api/src/pigrocrm_api/routers/tenants.py` (`signup`: the block from `MagicLinkService(space, settings).request(...)` to `background.add_task(sender.send, welcome_mail(...))` moves)
- Test: `apps/api/tests/test_tenants_api.py` (the signup mail tests keep passing), `packages/core/tests/test_tenants.py` (one new test)

**Interfaces:**
- Produces:

```python
def welcome_link(space: Session, settings: Settings, owner_email: str, slug: str) -> str | None:
    """The magic link that enters a freshly provisioned space, or None when this
    installation has no public origin: the signup route and the engagements door both
    send `welcome_mail` with it (spec 2026-09-25 § 2.3 step 3)."""

def welcome(space: Session, settings: Settings, sender: EmailSender | None, owner_email: str, slug: str, *, membro: bool) -> Mail | None:
    """The welcome mail ready to send, or None when nothing can be sent (no sender, no
    origin): the caller decides whether to send it in the background or at once."""
```

- [ ] **Step 1: Failing test** in `test_tenants.py`: with a `RecordingSender` and `public_url="https://pigro.test"`, `welcome(...)` answers a `Mail` whose html contains `/{slug}/app/verify?t=`; with `public_url=""` it answers `None`.
- [ ] **Step 2: Run, fails.**
- [ ] **Step 3: Implement** `welcome.py` and make `signup` call `welcome(...)` and `background.add_task(sender.send, mail)` when it is not None. The access cookie and `Location` stay in the router.
- [ ] **Step 4: Run** `test_tenants.py` and `test_tenants_api.py` (foreground, one at a time). Expected: pass, mail tests unchanged.
- [ ] **Step 5: Commit** `refactor(api): the signup's welcome step lives in core, ready for a second caller` (`REB-492.`).

### Task A4: `EngagementService.ensure`

**Files:**
- Create: `packages/core/src/pigrocrm/core/engagements/schemas.py`, `service.py`
- Test: `packages/core/tests/test_engagements.py`

**Interfaces:**
- Consumes: `TenantService.provision`, `TenantService.availability`, `slugify`,
  `SLUG_MAX` (`tenants/schemas.py`), `welcome` (A3), `CustomerService.create`,
  `DealService.create`, `PipelineService.default_stage`, `UserRepository.get_by_email`,
  `Actor.rebase()`, `RebaseEngagement` (A2), `tenant_database_url`,
  `tenant_database_name`, `session_factory`.
- Produces:

```python
class EngagementFreelancer(BaseModel):
    email: EmailStr
    nome: SafeStr = Field(min_length=1, max_length=100)
    cognome: SafeStr = Field(min_length=1, max_length=100)

class EngagementLetter(BaseModel):
    numero: SafeStr = Field(min_length=1, max_length=20)
    ruolo: SafeStr = Field(min_length=1, max_length=200)
    azienda: SafeStr = Field(min_length=1, max_length=255)
    data_inizio: date
    data_fine: date | None = None
    compenso: Decimal = Field(gt=0, max_digits=9, decimal_places=2)
    giorni_previsti: int | None = Field(default=None, ge=1, le=366)

class EngagementRebase(BaseModel):
    ragione_sociale: SafeStr = Field(min_length=1, max_length=255)
    partita_iva: SafeStr | None = None
    codice_fiscale: SafeStr | None = None
    sede: SafeStr | None = None
    pec: EmailStr | None = None
    codice_destinatario: SafeStr | None = None

class EngagementUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    freelancer: EngagementFreelancer
    lettera: EngagementLetter
    rebase: EngagementRebase

class EngagementRead(BaseModel):
    slug: str
    url: str
    customer_id: UUID
    deal_id: UUID
    deal_url: str
    spazio_creato: bool
    creato: bool

HOURS_PER_DAY = Decimal(8)

def deal_name(numero: str, ruolo: str, azienda: str) -> str:
    return f"Lettera n. {numero} · {ruolo} per {azienda}"

class EngagementService:
    def __init__(self, registry: Session, settings: Settings, sender: EmailSender | None = None) -> None: ...
    def ensure(self, match_id: UUID, data: EngagementUpsert) -> EngagementRead: ...
```

  `ensure` is § 2.3 steps 1 to 6, in order, in one method with small helpers:
  `_lock(email)` (`SELECT pg_advisory_xact_lock(hashtext(:email))` on the registry
  session, `email` lowercased and stripped), `_owned_space(email) -> Tenant | None`
  (`lower(owner_email) = :email`, oldest `created_at`), `_provision(freelancer) ->
  Tenant` (slug candidates: `slugify(f"{nome} {cognome}")`, then `f"{base[:SLUG_MAX-2]}-{n}"`
  for n from 2 while `availability(slug).disponibile` is false; `TenantSignup(slug, nome=f"{nome} {cognome}", email, membro=True)`;
  then `welcome(...)` and `sender.send(mail)` at once, since there is no request to
  background it), `_space_session(tenant)` (an engine from `tenant_database_url`, disposed
  in `finally`), `_customer(space, data) -> UUID` (by `partita_iva` when rebase has one,
  else by `ragione_sociale` among live customers; create with the note of § 2.3 step 4),
  `_deal(space, customer_id, data, owner_id) -> UUID` (reuse the live deal with
  `deal_name(...)` under that customer, else create: `tariffa_oraria = (compenso /
  HOURS_PER_DAY).quantize(Decimal("0.000001"))`, `ore_preventivate = giorni_previsti *
  HOURS_PER_DAY` or None, `data_chiusura_prevista = data_fine`, `owner_id`, the note of
  step 5, the default stage). Errors: a live row whose deal is soft-deleted or missing
  raises `Conflict("engagement", "Il deal di questa lettera è stato eliminato nello spazio.", match_id=str(match_id))`.
  `url` is `f"{settings.public_url.rstrip('/')}/{slug}/app/"`, `deal_url` that plus
  `deals/{deal_id}` (check the SPA's deal route in `apps/web/src` and use its English
  path).

- [ ] **Step 1: Write the failing tests** (each on the container, registry emptied and spaces dropped in a fixture shaped like `test_tenants_api.py`'s `_serving`, but on core alone):

```python
def test_first_call_creates_space_customer_and_deal(...): ...   # 201 semantics: creato and spazio_creato True; the space's customer «rebase» with the VAT number; the deal's name, rate 50.000000 for a 400.00 fee, 320.00 hours for 40 days, the note, the timeline actor «rebase»
def test_second_call_answers_the_same_ids_and_creates_nothing(...): ...
def test_owned_space_is_reused_oldest_first(...): ...            # two rows for the address, the older wins
def test_owner_lookup_is_case_insensitive(...): ...              # "Ada@Studio.it " finds "ada@studio.it"
def test_slug_collision_takes_a_suffix(...): ...                 # "ada-lovelace" taken -> "ada-lovelace-2"
def test_two_matches_one_email_share_one_space(...): ...         # two match ids, one tenant row, two deals
def test_retry_after_deal_created_but_unrecorded_reuses_it(...): ...  # write the row with deal_id NULL and a deal with the name already in the space; ensure() records that deal, count stays 1
def test_deleted_deal_answers_409(...): ...                      # soft-delete the deal in the space; Conflict with the sentence
def test_welcome_mail_is_sent_once_for_a_new_space(...): ...     # RecordingSender: one mail, none on the second call
```

- [ ] **Step 2: Run, all fail** (`uv run pytest projects/pigrocrm/packages/core/tests/test_engagements.py -q`).
- [ ] **Step 3: Implement** `schemas.py` and `service.py` as above. Every write in a space goes through the existing services with `Actor.rebase()`; nothing touches a space's tables directly except the reads that find a customer or a deal by name (use the services' list methods where they exist).
- [ ] **Step 4: Run** the file until green, then `uv run ruff check projects/pigrocrm && uv run mypy projects/pigrocrm/packages/core`.
- [ ] **Step 5: Commit** `feat(core): the engagements door sets up a freelancer's space, customer and deal for a match` (`REB-493.`).

### Task A5: `EngagementService.report`

**Files:**
- Modify: `packages/core/src/pigrocrm/core/engagements/schemas.py`, `service.py`
- Test: `packages/core/tests/test_engagements.py`

**Interfaces:**
- Consumes: `TimeEntryService.list(TimeEntryListQuery, actor)` paged with `cursor`
  until `next_cursor` is None; `TimeEntryService.deal_summary(deal_id, actor)`;
  `InvoiceLine`, `Invoice` (`invoices/models.py`) read in one query for the
  `invoice_line_id`s of the page.
- Produces:

```python
REPORT_MAX_DAYS = 400

class ReportInvoice(BaseModel):
    id: UUID
    tipo: str            # "fattura" | "proforma"
    anno: int | None
    numero: int | None
    stato: str
    stato_pagamento: str
    data: date | None    # data_emissione
    ore: Decimal         # only on the `fatture` list: the hours of this report on it

class ReportEntry(BaseModel):
    data: date
    ore: Decimal
    descrizione: str
    fatturabile: bool
    fattura: ReportInvoice | None   # `ore` there is this entry's hours

class ReportDeal(BaseModel):
    id: UUID
    nome: str
    tariffa_oraria: Decimal | None
    ore_preventivate: Decimal | None
    stato: str           # DealTimeSummary.stato

class EngagementReport(BaseModel):
    slug: str
    deal_url: str
    deal: ReportDeal
    giorni: list[ReportEntry]
    totale_ore: Decimal
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    fatture: list[ReportInvoice]

class EngagementService:
    def report(self, match_id: UUID, da: date | None = None, a: date | None = None) -> EngagementReport: ...
```

  `da` defaults to the deal's `created_at` date, `a` to today (`settings`' clock if the
  CRM has one, else `date.today()` in Europe/Rome the way `calendario` does); `a < da`
  or a span over `REPORT_MAX_DAYS` raises `ValidationFailed("engagement", "periodo",
  "al massimo 400 giorni")`. Missing row: `NotFound("engagement", str(match_id))`;
  deal gone: the `Conflict` of A4. Entries sorted by `data` then `created_at`.
  `ore_fatturate` sums entries with an `invoice_line_id`; `fatture` is one row per
  distinct invoice with the summed hours, newest first.

- [ ] **Step 1: Failing tests**: `test_report_lists_entries_with_their_invoice` (three entries, two on one issued invoice, one free: totals 24/16/8, one invoice with 16 hours), `test_report_defaults_and_caps_the_period` (401 days: `ValidationFailed`), `test_report_of_unknown_match_is_not_found`, `test_report_of_deleted_deal_answers_409`.
- [ ] **Step 2: Run, fail.** **Step 3: Implement.** **Step 4: Run green, ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): the engagements door answers a match's hours with their invoices` (`REB-494.`).

### Task A6: The two routes under the token

**Files:**
- Create: `apps/api/src/pigrocrm_api/service_token.py`, `apps/api/src/pigrocrm_api/routers/engagements.py`
- Modify: `apps/api/src/pigrocrm_api/main.py` (the router list, beside `tenants`), `apps/api/src/pigrocrm_api/routers/tenants.py` (`list_spaces` uses `require_service_token` too, same behaviour)
- Test: `apps/api/tests/test_engagements_api.py`, `apps/api/tests/test_tenants_api.py` (unchanged)

**Interfaces:**
- Produces:

```python
def require_service_token(configured: str, authorization: str | None) -> None:
    """404 when `configured` is empty (the door does not exist), 401 when the bearer is
    missing or differs, compared in constant time on bytes (Starlette decodes headers
    as latin-1). The one check `GET /api/tenants/` and the engagements routes share."""
```

  Routes, `router = APIRouter(prefix="/api/rebase/engagements", tags=["engagements"], responses=PROBLEM_RESPONSES)`:
  - `PUT /{match_id}` (`EngagementUpsert` body) → `EngagementRead`, status `201` when
    `creato` else `200` (set `response.status_code`).
  - `GET /{match_id}/report?da&a` → `EngagementReport`.
  Both take `registry: TenantsRegistryDep, settings: SettingsDep, sender: SenderDep,
  authorization: Annotated[str | None, Header()] = None`. Descriptions in Italian, one
  sentence each: what the route does and who holds the token. Errors map through the
  existing problem handlers (`Conflict` → 409, `ValidationFailed` → 422, `NotFound` → 404).

- [ ] **Step 1: Failing tests** with a `_serving`-style client and `engagements_token="un-token-per-la-porta"` in the settings: `test_door_is_absent_without_the_token` (404), `test_wrong_bearer_is_401`, `test_registry_token_does_not_open_the_door` (the registry token → 401), `test_put_creates_then_answers_200`, `test_put_refuses_an_unknown_field` (422), `test_report_answers_the_hours` (after logging two entries in the space through its own API or service), `test_report_of_deleted_deal_is_409`.
- [ ] **Step 2: Run, fail.** **Step 3: Implement**, and switch `list_spaces` to `require_service_token(settings.registry_token, authorization)`.
- [ ] **Step 4: Run** `test_engagements_api.py`, then `test_tenants_api.py`; ruff and mypy on `apps/api`.
- [ ] **Step 5: Commit** `feat(api): rebase's engagements door answers under its own token` (`REB-495.`).

### Task A7: Ship the door to the preview and exercise it

**Files:** none in the repository beyond the PR body; the host `.env` of the preview CRM
(`ssh orbiters`, the compose project of the preview) gains `PIGROCRM_ENGAGEMENTS_TOKEN`.

- [ ] **Step 1:** `gh pr ready` on the milestone's draft PR, then the review loop of `.claude/skills/pr-creation` (fresh reviewer before ready, Greptile and CodeRabbit to 5/5 and clean), merge with a merge commit.
- [ ] **Step 2:** The preview deploys from `main`. Generate the token (`openssl rand -hex 32`), add it to the preview CRM's `.env`, restart the `api` service (`docker compose up -d api` in the preview's compose directory), and read the boot log for the registry table (`ensure-space-defaults` runs `create_all`).
- [ ] **Step 3:** `curl -X PUT https://preview-pigro.<host>/api/rebase/engagements/<uuid4> -H "Authorization: Bearer $TOKEN" -d '<a body with a test address of ours>'` answers `201` with a slug; the same call again answers `200`; the report answers the empty deal; a wrong token answers `401`. Drop the test space afterwards the way `test_tenants_api.py` does (`drop_database`), or leave it if it is our own address and say so.
- [ ] **Step 4:** Closing comment on each card A1 to A7 with the evidence (run ids, the curl lines and their answers), the milestone's cards moved to `Done` by hand (the milestone branch carries no id).
- [ ] **Step 5:** Tell Ivan the production `.env` needs the same variable before the `pigrocrm-v*` tag that ships this (memory: a production deploy is his call).

---

## Milestone B: Link an active match to its deal and read the hours in the admin

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-link-a-match-to-its-hours ../pigrocrm-hub-hours
origin/ivansala/milestone-simpler-matches` (PR #416's tip: the hub half builds on the
three-step wizard, the cards and `match_words`), `uv sync --frozen && pnpm install
--frozen-lockfile --prefer-offline`, the draft PR with cards B1 to B9. Read
`projects/hub/AGENTS.md` first, then `rebase_core/matches.py`, `signing.py`,
`match_words.py` and `pigro.py` on that branch. When #416 merges, `git merge
origin/main` into the milestone branch before `gh pr ready`.

### Task B1: The expected days and the link on a match

**Files:**
- Create: `packages/core/migrations/versions/00NN_match_pigro_link.py`
- Modify: `packages/core/src/rebase_core/models.py` (`Match`), `contract_schemas.py` (`MatchCreate`, `MatchRead`, `MatchListItem`), `matches.py` (`create` stores `giorni_previsti`; `_match_read` and `_list_item` carry the new fields)
- Test: `packages/core/tests/test_migrations.py` (`compare_metadata` stays `[]`), `test_matches.py`

**Interfaces:**
- Produces, on `Match`:

```python
    giorni_previsti: Mapped[int | None] = mapped_column(Integer, default=None)
    pigro_stato: Mapped[str | None] = mapped_column(String(20), default=None)
    pigro_slug: Mapped[str | None] = mapped_column(String(32), default=None)
    pigro_deal_id: Mapped[UUID | None] = mapped_column(default=None)
    pigro_url: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_collegato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_errore: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_tentato_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

  with `CheckConstraint("giorni_previsti IS NULL OR giorni_previsti BETWEEN 1 AND 366", name="ck_matches_giorni_previsti")`
  and `CheckConstraint("pigro_stato IS NULL OR pigro_stato IN ('da_collegare', 'collegato', 'errore')", name="ck_matches_pigro_stato")`;
  `PIGRO_STATES = ("da_collegare", "collegato", "errore")` in `models.py`.
  `MatchCreate.giorni_previsti: int | None = Field(default=None, ge=1, le=366)`.
  `MatchRead` and `MatchListItem` gain `giorni_previsti: int | None` and
  `pigro_stato`, `pigro_slug`, `pigro_url`, `pigro_collegato_il`, `pigro_errore`,
  `pigro_tentato_il` (the list item: `pigro_stato` and `pigro_url` only).
  The migration: eight `ADD COLUMN IF NOT EXISTS`, the two `CHECK`s through the
  `pg_constraint` guard, and `UPDATE matches SET pigro_stato = 'da_collegare' WHERE
  stato = 'attivo' AND pigro_stato IS NULL` (§ 3.1's backfill), in the docstring style of
  `0019_documenso_envelopes.py`.

- [ ] **Step 1: Failing tests**: `test_create_stores_the_expected_days` (a `MatchCreate` with `giorni_previsti=40` reads back 40; `None` reads back None; 0 and 367 are `ValidationError`), `test_migrations.py` runs as is (fails until the migration exists because `compare_metadata` sees the columns).
- [ ] **Step 2: Run, fail.** **Step 3: Implement** the model and schemas, then the migration **in its own commit**.
- [ ] **Step 4: Run** `test_migrations.py` and `test_matches.py`; ruff, mypy.
- [ ] **Step 5: Commits** `feat(core): a match carries its expected days and its Pigro link` and `feat(core): migration 00NN adds the expected days and the Pigro link to matches` (`REB-497.`).

### Task B2: The hub's `EngagementService`

**Files:**
- Modify: `packages/core/src/rebase_core/config.py` (`pigro_engagements_token`), `projects/hub/.env.example`, `projects/hub/docker-compose.yml` (`x-api-environment`)
- Create: `packages/core/src/rebase_core/engagements.py`
- Modify: `packages/core/src/rebase_core/contract_schemas.py` (`MatchReport` and parts), `mail.py` (`engagement_ready_mail`), `pigro.py` (export `PigroUnavailable` as is)
- Test: `packages/core/tests/test_engagements.py` (new; a recorded `HttpCall` the way `test_pigro*.py` does, find it with `git grep -l PigroRegistry projects/hub/packages/core/tests`)

**Interfaces:**
- Consumes: `MatchService.lock_match`, `MatchService.get`, `ContractDocument.data` of
  the match's letter, `User` of the freelancer, `Company`, `signer_data(settings.signer_json)`
  (`contracts/fields.py`), `AdminActionService.record`, `EmailSender`, `HttpCall`.
- Produces:

```python
PIGRO_NOT_CONFIGURED = "Consuntivo non configurato su questo ambiente."
HOURS_PER_DAY = Decimal(8)

class PigroLinkResult(NamedTuple):
    linked: int
    failed: int

class ReportDay(BaseModel):
    data: date
    ore: Decimal
    descrizioni: list[str]
    fattura: str | None          # "12/2026" or None

class ReportWeek(BaseModel):
    settimana: str               # "2026-W40"
    da: date
    a: date
    ore: Decimal

class ReportMonth(BaseModel):
    mese: str                    # "2026-10"
    ore: Decimal

class ReportInvoice(BaseModel):
    numero: str                  # "12/2026", or "proforma 3/2026"
    tipo: str
    data: date | None
    stato: str
    stato_pagamento: str
    ore: Decimal

class MatchReport(BaseModel):
    match_id: UUID
    pigro_url: str | None
    pigro_stato: str | None
    giorni_previsti: int | None
    ore_previste: Decimal | None
    totale_ore: Decimal
    giorni_equivalenti: Decimal
    avanzamento: Decimal | None  # percent, two places
    ore_fatturate: Decimal
    ore_non_fatturate: Decimal
    per_giorno: list[ReportDay]
    per_settimana: list[ReportWeek]
    per_mese: list[ReportMonth]
    fatture: list[ReportInvoice]

def group_report(rows: list[dict[str, Any]], giorni_previsti: int | None) -> tuple[...]:
    """Pure: the CRM's `giorni` rows into per_giorno, per_settimana (ISO), per_mese and
    the totals. Tested on its own."""

class EngagementService:
    def __init__(self, session: Session, settings: Settings, http: HttpCall, *, sender: EmailSender | None = None, now: Callable[[], datetime] = utcnow, today: Callable[[], date] = rome_today) -> None: ...
    def payload(self, match_id: UUID) -> dict[str, Any]: ...
    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead: ...
    def link_pending(self) -> PigroLinkResult: ...
    def report(self, match_id: UUID, da: date | None = None, a: date | None = None) -> MatchReport: ...
```

  `link`: lock the match; not `attivo` → `ValidationFailed("match", "stato", "Si collega
  a Pigro solo un match attivo.")`; token empty → set `da_collegare` if unset, log once
  at info, return; else `PUT {pigro_api_url}/api/rebase/engagements/{match_id}` with
  `Authorization: Bearer`, `Content-Type: application/json`, body from `payload`; `201`
  or `200` → `collegato`, slug, deal id, url, `pigro_collegato_il = now()`, `pigro_errore
  = None`; on the first `collegato` send `engagement_ready_mail`; `409` → `errore` with
  the body's `detail`; any other status, an exception from `http`, a body that is not
  the shape → `errore` with `PigroUnavailable`'s sentences (reuse them). Always
  `pigro_tentato_il = now()`. With `admin_id`, `AdminActionService.record(entity_type="match", entity_id, kind="pigro_link", admin_id, payload={"esito": stato, "errore": ...})`.
  Commit, answer `MatchService.get`.
  `report`: not `collegato` → `ValidationFailed("match", "pigro_stato", <the state's sentence>)`;
  `GET .../report?da=&a=` with `da` = the letter's `data_inizio` (from
  `ContractDocument.data`) and `a` = `today()`, capped at 400 days from `da`; non-200 →
  `PigroUnavailable`; then `group_report`.

```python
def engagement_ready_mail(to: str, *, nome: str, numero: str, azienda: str, deal_url: str, spazio_creato: bool) -> Mail:
    """Subject «La tua lettera n. {numero} è attiva: le ore si registrano su Pigro»; the
    paragraphs of spec § 3.7, the hub's frame and button."""
```

- [ ] **Step 1: Failing tests**: `test_payload_from_a_signed_match` (fields and rebase's data from a signer mapping), `test_link_refuses_a_match_that_is_not_active`, `test_link_without_token_marks_da_collegare_and_calls_nothing`, `test_link_on_201_is_collegato_and_mails_once` (a second `link` with a recorded `200` sends no second mail), `test_link_writes_the_crm_sentence_on_409`, `test_link_on_refused_connection_is_errore_with_the_sentence`, `test_link_records_an_admin_action_when_an_admin_asked`, `test_link_pending_counts`, `test_report_groups_by_iso_week_and_month` (`group_report` with entries on 2026-12-28, 2026-12-31, 2027-01-01, 2027-01-04: weeks `2026-W53` and `2027-W01`, months `2026-12` and `2027-01`, both summing to the same total; `avanzamento` `"30.00"` for 96 hours of 40 days; `None` without days), `test_report_refuses_a_match_not_linked`.
- [ ] **Step 2: Run, fail.** **Step 3: Implement** (settings first: the three edits, one line each with the comment in `config.py`'s voice).
- [ ] **Step 4: Run green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): the hub links a match to its Pigro deal and reads its hours` (`REB-498.`).

### Task B3: On activation, from the sweep, on «Riprova», over the API

**Files:**
- Modify: `packages/core/src/rebase_core/signing.py` (`_confirm_completion`: `match.pigro_stato = "da_collegare"` beside `match.stato = "attivo"`; `_finish_outcome`: after the completion step, when the match just turned active, call the engagement link in the same try/except shape as the signed-copy step; `sweep`: after the loop, `EngagementService(...).link_pending()` and a third `SweepResult` field `linked` plus `link_failed`)
- Modify: `packages/core/src/rebase_core/cli.py` (`contracts_sweep` prints `, N match collegati a Pigro` and `, M non collegati` when not zero; builds the service with `settings`, `urllib_call`, `sender_from_settings`)
- Modify: `apps/api/src/rebase_api/deps.py` (`get_engagements` → `EngagementService`, `EngagementsDep`), `apps/api/src/rebase_api/routers/matches.py` (`POST /api/hub/matches/{match_id}/pigro/link` → `MatchRead`; `GET /api/hub/matches/{match_id}/report` → `MatchReport`; both `AdminDep`)
- Test: `packages/core/tests/test_signing.py`, `test_cli.py`, `apps/api/tests/test_matches_api.py`

**Interfaces:**
- Consumes: B2's service. `SigningService.__init__` gains `engagements: EngagementService | None = None` (None: no link attempted, the sweep prints nothing about Pigro), so the API's `SigningDep` factory and the CLI build it.
- Produces: the two routes; `SweepResult(touched, unconfirmed, linked, link_failed)`.

- [ ] **Step 1: Failing tests**: `test_confirm_completion_marks_the_match_da_collegare` (test_signing), `test_finish_links_a_match_that_just_turned_active` (a recorded `201`: `collegato` after `finish`), `test_sweep_retries_errore_matches` (an `errore` match and a recorded `201`: `linked == 1`), `test_contracts_sweep_prints_the_pigro_counts` (test_cli), `test_post_pigro_link_answers_the_match` and `test_get_report_answers_the_grouped_report` and `test_get_report_of_unlinked_match_is_409`... (the API maps `ValidationFailed` to 422 today: keep the repository's mapping and assert on it; say so in the PR body).
- [ ] **Step 2: Run, fail.** **Step 3: Implement.** The webhook route already returns before `finish` runs (background); nothing new waits on the CRM.
- [ ] **Step 4: Run** `test_signing.py`, `test_cli.py`, then the API suite; ruff, mypy.
- [ ] **Step 5: Commit** `feat(hub): an active match links itself to Pigro, the sweep retries, «Riprova» and the report answer over the API` (`REB-499.`).

### Task B4: The card's sentence, and the member's link

**Files:**
- Modify: `packages/core/src/rebase_core/match_words.py` (`Action` gains `"riprova_pigro"`; `match_words(...)` takes `pigro_stato: str | None` and `pigro_errore: str | None` and appends one sentence to `situazione` for an `attivo` match: «Le ore si consuntivano su Pigro.» for `collegato`, «Pigro non ha ancora il deal: riprova o aspetta lo sweep.» for `da_collegare`, «Pigro non ha risposto: {errore}» for `errore`; `altre_azioni` gains `riprova_pigro` for `da_collegare` and `errore`; `MATCH_ACTION_LABELS`/the web labels gain «Riprova su Pigro» and its pending «Collego a Pigro…»)
- Modify: `packages/core/src/rebase_core/matches.py` (`_match_read`, `_list_item` pass the two fields), `member_contracts.py` and `contract_schemas.py` (`MemberContract.pigro_url: str | None`, set for a letter whose match is `collegato`)
- Test: `packages/core/tests/test_match_words.py`, `test_matches.py`, `test_member_contracts.py` (or where `MemberContractService` is tested), `test_web_labels.py`

- [ ] **Step 1: Failing tests**: one per sentence and the action list; `test_member_letter_carries_the_pigro_url`.
- [ ] **Step 2: Run, fail.** **Step 3: Implement.** **Step 4: Run green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): a match says where its hours are, and the member sees the link` (`REB-500.`).

### Task B5: The two MCP tools

**Files:**
- Modify: `apps/mcp/src/rebase_mcp/server.py` (beside PR #416's match tools: `get_match_report(match_id: str, da: str | None = None, a: str | None = None) -> dict[str, Any]` and `link_match_to_pigro(match_id: str) -> dict[str, Any]`, through `_on_match` where it fits; `create_match` gains `giorni_previsti: int | None = None`; `build_server` takes the `EngagementService` factory the way it takes `contracts`)
- Test: `apps/mcp/tests/test_tools.py` (the tool list, and one test per tool with a recorded `HttpCall`)

- [ ] **Step 1: Failing tests.** **Step 2: Run, fail.** **Step 3: Implement**, descriptions in Italian in the register of the existing tools.
- [ ] **Step 4: Run** the MCP suite; ruff, mypy.
- [ ] **Step 5: Commit** `feat(mcp): an admin reads a match's hours and retries its Pigro link` (`REB-501.`).

### Task B6: The wizard's field and the lists

**Files:**
- Modify: `apps/web/src/lib/api.ts` (`Match`, `MatchListItem`, `MatchCreate` gain the fields; `matches.report(id, {da, a})`, `matches.linkPigro(id)`; `MatchReport` types), `apps/web/src/lib/format.ts` (`PIGRO_STATE_LABELS`: «Collegato», «Da collegare», «Errore»; `ACTION_LABELS.riprova_pigro`), `apps/web/src/lib/contracts.ts` (the form carries `giorni_previsti`)
- Modify: `apps/web/src/pages/admin/crea-match/CondizioniStep.tsx` (after the «Compenso» block: `Label` «Giorni previsti», `Input type="number" inputMode="numeric"` id `lettera-giorni_previsti`, helper text «Per il consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».»), `ControllaStep.tsx` (one line in the summary), `contratti/Cards.tsx` (the `riprova_pigro` handle, and a «Consuntivo» link button to `/admin/matches/$id/report` for a `collegato` match), `lists.tsx` and `Matches.tsx` (a «Pigro» column with the label)
- Test: the `*.test.tsx` beside each: the field round-trips into the request body; the check step shows «Giorni previsti: 40»; the card shows the sentence, the «Riprova su Pigro» item calls `linkPigro`, «Consuntivo» links; the column renders the label.

- [ ] **Step 1: Failing tests.** **Step 2: `pnpm --filter hub test -- <file>`, fail.** **Step 3: Implement.**
- [ ] **Step 4:** `pnpm --filter hub lint && pnpm --filter hub test && pnpm --filter hub build`.
- [ ] **Step 5: Commit** `feat(hub): «Crea match» asks the expected days, the lists say the Pigro link` (`REB-502.`).

### Task B7: «Consuntivo»

**Files:**
- Create: `apps/web/src/pages/admin/Consuntivo.tsx`, `Consuntivo.test.tsx`
- Modify: `apps/web/src/router.tsx` (`adminMatchReport`, `path: '/matches/$id/report'`, `validateSearch` for `mese` as `YYYY-MM` or `tutto`)
- Modify: `apps/web/src/pages/member/Contratti.tsx` (`Letter`: for `lettera.pigro_url`, a `Button asChild variant="outline" size="sm"` «Le tue ore su Pigro» and the line «rebase legge le ore di questo progetto per la rendicontazione al cliente.»), `Contratti.test.tsx`

**The page (spec § 3.5):** heading «Consuntivo», the match's title from `matches.get`
(company, role, letter number) and a link «Apri il deal su Pigro»; a `Select` of the
months between the letter's start and today plus «Tutto l'incarico», default the current
month, kept in the URL; the progress line «96 ore, 12 giorni su 40 previsti (30%)» or
«96 ore, 12 giorni»; a `Table` per day (Data, Ore, Descrizione, Fattura with «da
fatturare» when none); «Per settimana» and «Per mese» as two small tables of the
selected period; «Fatture» (Numero, Data, Stato, Incasso, Ore). Loading, an error
sentence from the API, and the `pigro_stato` sentence when not `collegato`, each as a
paragraph. All data from one `matches.report(id)` call for the whole engagement; the
month filter slices `per_giorno` client-side and recomputes the two small tables from
the sliced days (the pure grouping lives in `lib/report.ts`, tested).

- [ ] **Step 1: Failing tests** with a recorded report: the progress line, the day rows, the invoice number, the month filter, the «Tutto l'incarico» option, the error paragraph, the member button.
- [ ] **Step 2: fail. Step 3: Implement. Step 4: lint, test, build.**
- [ ] **Step 5: Commit** `feat(hub): «Consuntivo» shows a match's hours per day, week and month, and the member finds their deal` (`REB-503.`).

### Task B8: The clause in the letter, and the decision

**Files:**
- Modify: `packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md` (the paragraph of spec § 3.8 after the one on `scadenze-fatturazione`; `text_version` in its front matter bumped)
- Modify: `docs/design/DECISIONS.md` (one row, dated 2026-09-25: the hub reaches into a space only through the CRM's engagements door, with a token of its own; the registry token stays read-only)
- Test: `packages/core/tests/test_contract_render.py` (typesets the letter: `rebase contracts-check` passes), `test_contract_pdf.py` if it pins the page count (update the number and say so)

- [ ] **Step 1:** Read the letter's front matter and how `text_version` is read (`contracts/__init__.py`, `fields.py`). Add the paragraph, bump the version, run `uv run rebase contracts-check`.
- [ ] **Step 2:** Run `test_contract_render.py`, `test_contract_pdf.py`, `test_matches.py` (a letter written from the new text carries the new version; an existing document keeps its own).
- [ ] **Step 3:** The DECISIONS row (expect a merge conflict with every other open PR on that file: resolve by keeping both rows).
- [ ] **Step 4: Commit** `feat(contracts): the letter of engagement carries the reporting clause` (`REB-504.`), a comment on the card asking Ivan to read the wording on the PR.

### Task B9: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main` once PR #416 has merged; resolve, rerun every suite in the foreground, one DB-backed run at a time.
- [ ] **Step 2:** On the preview (Documenso is on there, and A7 set the CRM's token): set `REBASE_PIGRO_ENGAGEMENTS_TOKEN` in the preview hub's `.env`, restart `api` and `sweep`, create a match for a test freelancer of ours with `giorni_previsti`, send it for signature, sign on Documenso, watch the sweep line («1 match collegati a Pigro»), open the preview CRM as that freelancer (the welcome mail's link), log two hours on the deal, open «Consuntivo» on the preview hub and read them.
- [ ] **Step 3:** The before-and-after pairs (the card, the «Match» list, the wizard's conditions step, the member's letter, «Consuntivo») and the video of the flow, with `docs/pr-screenshots/record.mjs` (`docs/pr-screenshots/README.md`; the screenshot stack runs on its own ports, never `:8000`).
- [ ] **Step 4:** `gh pr ready`, the fresh reviewer on the whole diff, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comment on every card B1 to B9 and each moved to `Done` by hand; a project update on P-REB-42 (three sentences, `type: "project"`).
- [ ] **Step 5:** Tell Ivan the production hub `.env` needs the token before the `hub-v*` tag, and that the CRM tag goes first.

## Self-review (done while writing)

- **Spec coverage.** § 2.1 A1; § 2.2 A2; § 2.3 A3, A4; § 2.4 A5; § 2.5 A1; § 2.6 nothing
  to do; § 3.1 B1; § 3.2 B2; § 3.3 B2, B3; § 3.4 B6; § 3.5 B3, B7; § 3.6 B4, B7; § 3.7
  B2; § 3.8 B8; § 3.9 B5; § 3.10 tests in A4 and B2; § 4 A7, B9, B8 (the row); § 5 the
  tests named in each task; § 6 the two milestones.
- **Placeholders.** `00NN` is the one deliberate blank: the number is chosen when B1's
  branch is cut, after `git fetch`, because `0020` belongs to an open PR and the next
  free number depends on which lands first.
- **Type consistency.** `EngagementUpsert` / `EngagementRead` / `EngagementReport` (CRM)
  and `MatchReport` (hub) are named the same in every task that uses them; `HOURS_PER_DAY`
  is a `Decimal(8)` on both sides; `pigro_stato` values are the three strings of B1
  everywhere; the action is `riprova_pigro` in core, MCP and web.
- **Review Focus.** Each of the five has its test in A4 or B2, named above.
