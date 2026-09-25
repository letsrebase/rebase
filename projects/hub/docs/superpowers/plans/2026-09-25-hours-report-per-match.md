# Hours per match: the CRM door and the hub report. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a match turns active the hub gets the freelancer a PigroCRM space with
rebase as the customer and the letter as a deal; the freelancer logs hours there; the
admin reads them per day, week and month against the letter's expected days, invoices
included, on the match and over MCP.

**Architecture:** Two milestones, one per project, the CRM's first. In
`projects/pigrocrm` a service-token door on the root installation:
`PUT /api/rebase/engagements/{match_id}` sets up space, customer «rebase» and deal
idempotently (a registry table `rebase_engagements` keys it, a session-level advisory
lock on the address serialises it), and `GET /api/rebase/engagements/{match_id}/report`
answers the deal's hours per entry with the invoice each sits on. In `projects/hub` a
`rebase_core.engagements` service calls that door through the existing `HttpCall` seam
(with a longer timeout) when a match turns active, retries from `contracts-sweep`,
stores slug, deal id and state on the match, groups the report by ISO week and month,
and hands it to the admin API, the «Consuntivo» page and two MCP tools.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic (hub only; the CRM registry is
`create_all`), FastAPI, Pydantic 2, pytest with testcontainers Postgres; React 19,
TanStack Router and Query, `@rebase/ui`, Vitest. pandoc and Typst for the letter.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-hours-report-per-match-design.md`
(REB-489). Section numbers below (§) are the spec's. Amended on 2026-09-25 after the
independent review of the text against the code; the amendments are folded in below.

## Global Constraints

- Everything is English except what the product says to a person: UI copy, mail text,
  API error sentences, OpenAPI and MCP descriptions, CLI output are Italian (root
  `AGENTS.md`, Conventions). Column names are English like the hub's (`sent_at`,
  `signed_at`); state values stay Italian like `stato`'s.
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
  (`deals.tariffa_oraria`), hours two, a fee two.
- Status codes on the hub: a `DomainError` maps as `rebase_core/errors.py` says
  (`InvalidState` 409, `ValidationFailed` 422, `NotFound` 404); `PigroUnavailable` is
  caught by the route and answered as `routers/pigro.py` does (`502`, or `503` when the
  CRM is not configured) and by an MCP tool as `ToolError(str(exc))`.
- Local checks (`preflight` is not installed on this Mac): `uv run ruff check
  projects/<name>` and mypy exactly as CI runs it, `uv run mypy
  projects/<name>/packages/core/src projects/<name>/apps/api/src
  projects/<name>/apps/mcp/src` (a bare `uv run mypy` or a path with `tests` answers
  hundreds of unrelated errors); the pytest suites as separate processes in the
  **foreground** and never two DB-backed runs at once in one worktree; then `pnpm
  --filter <hub|web> lint`, `test`, `build`. Never use ports 55432 or 55433 for a test
  database (they are ssh tunnels to production). Check `df -h /` before building an
  image; stop under 3 GB free.
- Shared test helpers live in modules beside the tests (the hub's `fakes_contracts.py`),
  importable by name; imports go at the top of a test file (ruff E402).

## Review Focus

Five failure modes the spec implies and the plain feature tests would miss. Each is
pinned by a test in the task named.

1. **Two letters of one freelancer activating in the same minute.** One space, two
   deals: the session-level advisory lock on the lowercased address held for the whole
   call, tested with two threads on two match ids (Task A4,
   `test_two_matches_one_email_share_one_space`).
2. **A crash between creating the deal and recording it.** The next call finds the deal
   by the marker in its note (`rebase:match=<match_id>`) under the customer, through an
   exact repository query and never the paginated trigram list, and completes the row
   instead of creating a twin; a same-named deal without the marker is left alone; a row
   with `deal_id` NULL resumes at the customer step (Task A4,
   `test_retry_after_deal_created_but_unrecorded_reuses_it`,
   `test_same_named_deal_without_marker_is_not_reused`, `test_half_written_row_is_completed`).
3. **Case and spaces in the address.** `Ada@Studio.it ` owns the space registered as
   `ada@studio.it`; the lock and the lookup both lowercase and strip (Task A4,
   `test_owner_lookup_is_case_insensitive`).
4. **A week that straddles two months or two years.** Per-week totals use ISO weeks
   (`2026-12-31` is `2026-W53`, `2027-01-01` too), per-month totals the calendar month,
   and both sum to the same total (Task B2, `test_report_groups_by_iso_week_and_month`).
5. **The freelancer deletes the deal.** The CRM answers `409`, the hub writes
   `rifiutato` with the CRM's sentence, the sweep leaves it alone, «Riprova» does not
   recreate anything (Task A4, `test_deleted_deal_answers_409`; Task B2,
   `test_link_writes_the_crm_sentence_on_409_as_rifiutato`,
   `test_link_pending_skips_rifiutato`).

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
| B1 | REB-497 | Carry the expected days, the letter's numbers and the Pigro link on a match | none |
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
- `packages/core/src/pigrocrm/core/actor.py`: `ActorType` gains `"rebase"`, `Actor.rebase()`;
  `apps/web/src/components/Timeline.tsx`: the `rebase` entry of `ACTOR_META`;
  `packages/core/src/pigrocrm/core/work_units/service.py`: `actor_to_transition_json`
  names the new type instead of folding it into «agent».
- `packages/core/src/pigrocrm/core/engagements/__init__.py` (empty), `models.py` (the
  registry row `RebaseEngagement`), `schemas.py` (`EngagementUpsert`, `EngagementRead`,
  `EngagementReport` and its parts), `service.py` (`EngagementService`).
- `packages/core/src/pigrocrm/core/tenants/database.py`: imports
  `pigrocrm.core.engagements.models` by its full path.
- `packages/core/src/pigrocrm/core/tenants/welcome.py`: the post-provision step the
  signup route and the door share.
- `apps/api/src/pigrocrm_api/service_token.py`: `require_service_token`.
- `apps/api/src/pigrocrm_api/routers/engagements.py`: the two routes; `main.py` lists it.
- Tests: `packages/core/tests/test_engagements.py` (new), `test_actor.py` (new, A1),
  `test_hub_lookup.py` (the config case), `apps/api/tests/test_engagements_api.py`,
  `test_tenants_api.py` (unchanged behaviour).

**Hub (`projects/hub`)**

- `packages/core/migrations/versions/0021_match_pigro_link.py` (`down_revision`
  `0019`; re-pointed at `0020` before the merge if `0020_campaigns.py` is on `main`).
- `packages/core/src/rebase_core/models.py`: twelve columns on `Match`, `PIGRO_STATES`.
- `packages/core/src/rebase_core/contract_schemas.py`: `MatchCreate.giorni_previsti`,
  the `lettera_*` and `pigro_*` fields on `MatchRead` and `MatchListItem`,
  `MemberContract.pigro_url`, `MatchReport` and its parts.
- `packages/core/src/rebase_core/config.py`: `pigro_engagements_token`.
- `packages/core/src/rebase_core/http.py`: `urllib_engagements_call` (90 s).
- `packages/core/src/rebase_core/pigro.py`: the four sentences as constants.
- `packages/core/src/rebase_core/contracts/fields.py`: `parse_italian_date`.
- `packages/core/src/rebase_core/engagements.py`: `EngagementService`, `group_report`.
- `packages/core/src/rebase_core/mail.py`: `engagement_ready_mail`.
- `packages/core/src/rebase_core/signing.py`: `da_collegare` on activation; `finish` and
  `sweep` call the link; `signing_from_settings` builds the service.
- `packages/core/src/rebase_core/match_words.py`: the link sentences and `riprova_pigro`.
- `packages/core/src/rebase_core/cli.py`: the sweep's line.
- `packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md`: the clause.
- `apps/api/src/rebase_api/deps.py`: `EngagementsDep`, `get_signing_factory` gains the
  service; `routers/matches.py`: two routes; `routers/members.py`: `pigro_url` rides the
  existing read.
- `apps/mcp/src/rebase_mcp/server.py`: two tools; `create_match` gains `giorni_previsti`.
- `apps/web/src/lib/api.ts`, `format.ts`, `contracts.ts`, new `lib/report.ts`: types,
  client, labels, the client-side grouping.
- `apps/web/src/pages/admin/crea-match/CondizioniStep.tsx`, `ControllaStep.tsx`;
  `pages/admin/Contratti.tsx` (the handles) and `contratti/Cards.tsx` (the «Consuntivo»
  link); `lists.tsx`; `Matches.tsx`; new `pages/admin/Consuntivo.tsx`; `router.tsx`;
  `pages/member/Contratti.tsx`.
- Monorepo root: `docs/design/DECISIONS.md` (one row); `docs/pr-screenshots/record.mjs`
  (the video, B9).
- Tests: `packages/core/tests/test_engagements.py` (new), `test_matches.py`,
  `test_signing.py`, `test_migrations.py`, `test_match_words.py`, `test_cli.py`,
  `test_contract_render.py`, `test_web_labels.py`, `test_member_contracts.py` (or where
  `MemberContractService` is tested); `apps/api/tests/test_matches_api.py`,
  `test_members_api.py`; `apps/mcp/tests/test_tools.py`; the web `*.test.tsx` beside
  each page.

---

## Milestone A: Open a door in the CRM for rebase's engagements

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-crm-engagements-door ../pigrocrm-crm-door origin/main`, `uv sync
--frozen && pnpm install --frozen-lockfile --prefer-offline`, and `gh pr create
--draft` with the milestone's name and the checklist of cards A1 to A7
(`.claude/skills/pr-creation`, § Before the branch exists, step 3). Read
`projects/pigrocrm/AGENTS.md` first.

### Task A1: The engagements token and the «rebase» actor

**Files:**
- Modify: `packages/core/src/pigrocrm/core/config.py` (after `registry_token`)
- Modify: `projects/pigrocrm/.env.example` (after `PIGROCRM_REGISTRY_TOKEN`)
- Modify: `projects/pigrocrm/docker-compose.yml` (`x-api-environment`, after the registry line)
- Modify: `packages/core/src/pigrocrm/core/actor.py`
- Modify: `apps/web/src/components/Timeline.tsx` (`ACTOR_META`: a `rebase` entry beside
  `system`, label «rebase», the lowercase wordmark)
- Test: `packages/core/tests/test_actor.py` (new), `packages/core/tests/test_hub_lookup.py`
  (where `registry_token` is already covered)

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
  `PIGROCRM_ENGAGEMENTS_TOKEN: ${PIGROCRM_ENGAGEMENTS_TOKEN:-}`. `Timeline.tsx`: the
  `rebase` entry of `ACTOR_META`.
- [ ] **Step 4: Run** `uv run pytest projects/pigrocrm/packages/core/tests/test_actor.py projects/pigrocrm/packages/core/tests/test_hub_lookup.py projects/pigrocrm/packages/core/tests/test_activities.py -q`, then `pnpm --filter web lint && pnpm --filter web test -- Timeline && pnpm --filter web build`. Expected: pass.
- [ ] **Step 5: Commit** `feat(core): the engagements token and the rebase actor exist` with `REB-490.` as the last line.

### Task A2: The registry table `rebase_engagements`

**Files:**
- Create: `packages/core/src/pigrocrm/core/engagements/__init__.py` (empty: a docstring
  and nothing else; see below), `models.py`
- Modify: `packages/core/src/pigrocrm/core/tenants/database.py` (`import
  pigrocrm.core.engagements.models  # noqa: F401` beside the `identity.models` import)
- Test: `packages/core/tests/test_engagements.py` (new; the registry fixture of
  `packages/core/tests/test_tenants.py` is the model: read how it builds `Settings` on
  the container and calls `ensure_tenants_database`)

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

  (Use the same `PrimaryKeyMixin` and column idioms `identity/models.py` uses.)
  **The import trap:** `tenants/database.py` importing `pigrocrm.core.engagements.models`
  runs `engagements/__init__.py` first; if that ever re-exports `service`, the chain
  `service → tenants.service → tenants.database` is a circular import against a
  half-initialised module. The package `__init__` therefore holds a docstring saying so
  and no import; A4's service is imported by its full path by its callers.

- [ ] **Step 1: Failing test** `test_registry_has_the_engagements_table`: `ensure_tenants_database(settings)` then `inspect(engine).has_table("rebase_engagements")`, plus `test_engagements_package_exports_nothing` (`import pigrocrm.core.engagements as p; assert not [n for n in dir(p) if not n.startswith("_")]`).
- [ ] **Step 2: Run, fails** (`uv run pytest projects/pigrocrm/packages/core/tests/test_engagements.py -q`).
- [ ] **Step 3: Implement** the model and the import line in `tenants/database.py` with a comment: same reason as `identity.models`.
- [ ] **Step 4: Run** the new tests and `test_tenants.py`. Expected: pass.
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
- Modify: `packages/core/src/pigrocrm/core/customers/repository.py`
  (`find_by_name(ragione_sociale) -> Customer | None`: exact, live rows only) and
  `deals/repository.py` (`find_by_marker(customer_id, marker) -> Deal | None`: the live
  deal of that customer whose `note` contains the marker, exact substring, at most one
  by construction)
- Modify: `packages/core/src/pigrocrm/core/work_units/service.py`
  (`actor_to_transition_json`: a `rebase` actor is recorded as `{"kind": "rebase"}`,
  never folded into `agent`; read the function's docstring and keep its shape)
- Test: `packages/core/tests/test_engagements.py`, `packages/core/tests/test_work_units*.py`
  (the transition json of a rebase actor), the repository tests beside the two new
  methods

**Interfaces:**
- Consumes: `TenantService.provision`, `TenantService.availability`, `slugify`,
  `SLUG_MAX` (`tenants/schemas.py`), `welcome` (A3), `CustomerService.create`,
  `CustomerRepository.match_by_fiscal_id`, the two new exact repository methods
  (never `CustomerService.list` or `DealService.list`: those are paginated trigram
  searches, and a first page is not the set), `DealService.create`,
  `PipelineService.default_stage`, `UserRepository.get_by_email`, `Actor.rebase()`,
  `RebaseEngagement` (A2), `tenant_database_url`, `tenant_database_name`,
  `session_factory`, `Settings.public_url`.
- Produces:

```python
NAME_MAX_LENGTH = 120        # the hub's NAME_MAX_LENGTH
SPACE_NAME_MAX_LENGTH = 200  # TenantSignup.nome
ROLE_IN_DEAL_NAME = 80
COMPANY_IN_DEAL_NAME = 100
HOURS_PER_DAY = Decimal(8)

class EngagementFreelancer(BaseModel):
    email: EmailStr
    nome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    cognome: SafeStr = Field(min_length=1, max_length=NAME_MAX_LENGTH)

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
    partita_iva: str | None = Field(default=None, pattern=r"^\d{11}$")
    codice_fiscale: SafeStr | None = Field(default=None, max_length=16)
    indirizzo: SafeStr | None = Field(default=None, max_length=255)
    pec: EmailStr | None = None
    codice_sdi: str | None = Field(default=None, min_length=7, max_length=7)

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

def deal_name(numero: str, ruolo: str, azienda: str) -> str:
    """`Lettera n. 3/2026 · Backend developer per Acme S.r.l.`, the role cut to 80 and
    the company to 100 characters so the longest inputs stay under deals.nome's 255.
    A label: the marker below is what finds a deal again."""

def deal_marker(match_id: UUID) -> str:
    """`rebase:match=<match_id>`, the last line of the deal's note and the key a retry
    recovers the deal by (`DealRepository.find_by_marker`)."""

def space_url(settings: Settings, slug: str) -> str      # f"{public_url}/{slug}/app/"
def deal_url(settings: Settings, slug: str, deal_id: UUID) -> str  # ... + f"deal/{deal_id}"

class EngagementService:
    def __init__(self, registry_engine: Engine, settings: Settings, sender: EmailSender | None = None) -> None: ...
    def ensure(self, match_id: UUID, data: EngagementUpsert) -> EngagementRead: ...
```

  `ensure` refuses at once with `ValidationFailed("engagement", "public_url",
  "PIGROCRM_PUBLIC_URL non configurato")` when `settings.public_url` is blank (the
  router maps it to `503`, see A6). Then § 2.3 steps 1 to 6:

  - **The lock (step 1).** A dedicated connection from `registry_engine`
    (`registry_engine.connect()`), `SELECT pg_advisory_lock(hashtext(:email))` with the
    address lowercased and stripped, kept open for the whole call, and `SELECT
    pg_advisory_unlock(hashtext(:email))` in a `finally` before the connection closes.
    The registry `Session` used for the rows is a separate session on the same engine;
    its commits do not release the lock, which is the point (a `pg_advisory_xact_lock`
    would go at `TenantService.provision`'s own commit).
  - **Step 2.** `row = registry.scalar(select(RebaseEngagement).where(match_id == ...))`.
    Row with `deal_id`: open the space, `DealService.get` (not found or soft-deleted →
    `Conflict("engagement", "Il deal di questa lettera è stato eliminato nello spazio.",
    match_id=str(match_id))`), answer the row with `creato=False`,
    `spazio_creato=False`. Row without `deal_id`: skip step 3, continue at step 4 in
    that row's tenant.
  - **Step 3.** `_owned_space(email) -> Tenant | None` (`func.lower(Tenant.owner_email)
    == email`, `order_by(Tenant.created_at)`, first); none → `_provision(freelancer)`:
    the base `slugify(f"{nome} {cognome}")`, candidates `base`, then
    `f"{base[:SLUG_MAX - len(suffix)]}{suffix}"` with `suffix = f"-{n}"` for n from 2
    while `availability(candidate).disponibile` is false; `TenantSignup(slug, nome=
    f"{nome} {cognome}"[:SPACE_NAME_MAX_LENGTH], email, membro=True)`;
    `TenantService(registry, settings).provision`; then `welcome(space, settings,
    sender, email, slug, membro=True)` and `sender.send(mail)` when both exist (no
    request to background it). Write `RebaseEngagement(match_id, tenant_id)` and
    commit.
  - **Step 4.** In the space (`_space_session(tenant)`: an engine from
    `tenant_database_url`, disposed in `finally`): the customer by
    `CustomerRepository.match_by_fiscal_id(partita_iva)` when the body carries one, else
    `CustomerRepository.find_by_name(data.rebase.ragione_sociale)`; missing →
    `CustomerService.create` with `ragione_sociale`, `partita_iva`, `codice_fiscale`,
    `indirizzo`, `pec`, `codice_sdi` and the note of § 2.3 step 4, as `Actor.rebase()`.
  - **Step 5.** `DealRepository.find_by_marker(customer_id, deal_marker(match_id))`,
    else `DealService.create(DealCreate(nome=deal_name(...), customer_id,
    tariffa_oraria=(compenso / HOURS_PER_DAY).quantize(Decimal("0.000001")),
    ore_preventivate=giorni_previsti * HOURS_PER_DAY or None,
    data_chiusura_prevista=data_fine, owner_id=<the admin whose email is the
    freelancer's, via UserRepository.get_by_email>, note=<§ 2.3 step 5's sentence,
    a newline, deal_marker(match_id)>), Actor.rebase())` (the default open stage comes
    from `pipeline_stage_id=None`). A deal with the same name and no marker is not ours.
  - **Step 6.** `row.customer_id`, `row.deal_id`, commit, answer `EngagementRead(...,
    spazio_creato=<step 3 provisioned>, creato=True)`.

- [ ] **Step 1: Write the failing tests** (each on the container; the fixture, shaped like `test_tenants_api.py`'s `_serving` but on core alone, reads the registry at teardown and drops every space it finds there, so a slug the test did not choose is dropped too and nothing else is; `public_url="https://pigro.test"` in the settings):

```python
def test_first_call_creates_space_customer_and_deal(...): ...   # creato and spazio_creato True; the space's customer «rebase» with the VAT number; the deal's name, rate 50.000000 for a 400.00 fee, 320.00 hours for 40 days, the note, the timeline's actor_type "rebase"; url and deal_url shaped https://pigro.test/ada-lovelace/app/ and .../app/deal/<id>
def test_second_call_answers_the_same_ids_and_creates_nothing(...): ...
def test_owned_space_is_reused_oldest_first(...): ...
def test_owner_lookup_is_case_insensitive(...): ...              # "Ada@Studio.it " finds "ada@studio.it"
def test_slug_collision_takes_a_suffix(...): ...                 # "ada-lovelace" taken -> "ada-lovelace-2"
def test_two_matches_one_email_share_one_space(...): ...         # two threads, two match ids, one address: one tenant row, two deals, no error
def test_half_written_row_is_completed(...): ...                 # a row with deal_id NULL: ensure() creates customer and deal in that tenant and fills the row
def test_retry_after_deal_created_but_unrecorded_reuses_it(...): ...  # a deal with the marker exists, the row has no deal_id: reused, count stays 1
def test_same_named_deal_without_marker_is_not_reused(...): ...       # a freelancer's own deal with the same name: ours is created beside it
def test_longest_role_and_company_still_make_a_valid_name(...): ...  # 200-char role, 255-char company: deal_name under 255, create succeeds
def test_deleted_deal_answers_409(...): ...                      # Conflict with the sentence
def test_refuses_without_public_url(...): ...                    # ValidationFailed on public_url
def test_welcome_mail_is_sent_once_for_a_new_space(...): ...     # RecordingSender: one mail, none on the second call
```

- [ ] **Step 2: Run, all fail** (`uv run pytest projects/pigrocrm/packages/core/tests/test_engagements.py -q`).
- [ ] **Step 3: Implement** `schemas.py`, `service.py` and the `work_units` branch as above.
- [ ] **Step 4: Run** the file until green, then ruff and CI's mypy invocation.
- [ ] **Step 5: Commit** `feat(core): the engagements door sets up a freelancer's space, customer and deal for a match` (`REB-493.`).

### Task A5: `EngagementService.report`

**Files:**
- Modify: `packages/core/src/pigrocrm/core/engagements/schemas.py`, `service.py`
- Test: `packages/core/tests/test_engagements.py`

**Interfaces:**
- Consumes: `TimeEntryService.list(TimeEntryListQuery, actor)` paged with `cursor`
  until `next_cursor` is None; `TimeEntryService.deal_summary(deal_id, actor)`;
  `billed_entry_ids` (`timetracking/service.py`, the CRM's own definition of billed: on
  a line of a `fattura` that is `emessa` and not deleted); `InvoiceLine`, `Invoice`
  (`invoices/models.py`) read in one query for the `invoice_line_id`s of the report;
  `pigrocrm.core.db.today_local()` for today.
- Produces:

```python
REPORT_MAX_DAYS = 800

class ReportInvoice(BaseModel):
    id: UUID
    tipo: str            # "fattura" | "proforma"
    anno: int | None
    numero: int | None
    stato: str
    stato_pagamento: str
    data: date | None    # data_emissione
    ore: Decimal | None = None   # only on the `fatture` list: the hours of this report on it

class ReportEntry(BaseModel):
    data: date
    ore: Decimal
    descrizione: str
    fatturabile: bool
    fattura: ReportInvoice | None   # `ore` is None here

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

  `da` defaults to the deal's `created_at` date, `a` to `today_local()`; `a < da` or a
  span over `REPORT_MAX_DAYS` raises `ValidationFailed("engagement", "periodo", "al
  massimo 800 giorni")`. Missing row: `NotFound("engagement", str(match_id))`; deal
  gone: the `Conflict` of A4. Entries sorted by `data` then `created_at`.
  `ore_fatturate` sums the entries whose id is in `billed_entry_ids`; `fatture` is one
  row per distinct invoice among the entries' lines with the summed hours, newest first.

- [ ] **Step 1: Failing tests**: `test_report_lists_entries_with_their_invoice` (three entries, two on one issued invoice, one free: totals 24/16/8, one invoice with 16 hours), `test_report_counts_only_issued_invoices_as_billed` (an entry on a proforma line shows the proforma in `fattura` and counts as not billed), `test_report_defaults_and_caps_the_period` (801 days: `ValidationFailed`), `test_report_of_unknown_match_is_not_found`, `test_report_of_deleted_deal_answers_409`.
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
  authorization: Annotated[str | None, Header()] = None`; the service is built on the
  registry session's engine (`registry.get_bind()`). Descriptions in Italian, one
  sentence each: what the route does and who holds the token. Errors map through the
  existing problem handlers (`Conflict` → 409, `NotFound` → 404); `ValidationFailed`
  maps to 422 except the `public_url` one, which the route turns into `503` with its
  sentence (a missing setting is the installation's fault, not the caller's).

- [ ] **Step 1: Failing tests** with a `_serving`-style client whose teardown drops every space the registry lists (never a fixed slug) and `engagements_token="un-token-per-la-porta"` in the settings: `test_door_is_absent_without_the_token` (404), `test_wrong_bearer_is_401`, `test_registry_token_does_not_open_the_door` (the registry token → 401), `test_put_creates_then_answers_200`, `test_put_refuses_an_unknown_field` (422), `test_put_without_public_url_is_503`, `test_report_answers_the_hours` (two entries logged in the space through `TimeEntryService` as `Actor.system()`: the space's admin has no password, so the service is the way in), `test_report_of_deleted_deal_is_409`.
- [ ] **Step 2: Run, fail.** **Step 3: Implement**, and switch `list_spaces` to `require_service_token(settings.registry_token, authorization)`.
- [ ] **Step 4: Run** `test_engagements_api.py`, then `test_tenants_api.py`; ruff and mypy on `apps/api`.
- [ ] **Step 5: Commit** `feat(api): rebase's engagements door answers under its own token` (`REB-495.`).

### Task A7: Ship the door to the preview and exercise it

**Files:** none in the repository beyond the PR body; the host `.env` of the preview CRM
(`ssh orbiters`, the preview's compose directory) gains `PIGROCRM_ENGAGEMENTS_TOKEN`.
The preview CRM answers at `https://preview.pigro.letsrebase.com`
(`projects/pigrocrm/deploy/nginx/preview.pigro.letsrebase.conf`).

- [ ] **Step 1:** `gh pr ready` on the milestone's draft PR, then the review loop of `.claude/skills/pr-creation` (fresh reviewer before ready, Greptile and CodeRabbit to 5/5 and clean), merge with a merge commit.
- [ ] **Step 2:** The preview deploys from `main`. Generate the token straight into the preview CRM's `.env` without printing it (`printf 'PIGROCRM_ENGAGEMENTS_TOKEN=%s\n' "$(openssl rand -hex 32)" >> .env`, the file already `600`), restart the `api` service (`docker compose up -d api` in the preview's compose directory), and read the boot log for the registry table (`ensure-space-defaults` runs `create_all`).
- [ ] **Step 3:** The token never appears in a terminal or in `argv`: write `Authorization: Bearer <token>` into a `600` file on the host and call `curl -H @auth.txt -X PUT https://preview.pigro.letsrebase.com/api/rebase/engagements/<uuid4> -H "Content-Type: application/json" -d '<a body with a fresh disposable address of ours, one that owns no space>'`: `201` with a slug; the same call again `200`; the report the empty deal; a wrong token `401`. Afterwards drop only that space, by the slug the `201` answered, the way `test_tenants_api.py` does (`drop_database`): a fresh address is what makes it certain that no pre-existing space of a real freelancer was reused, and no backup is needed for a database this run created. Delete `auth.txt`.
- [ ] **Step 4:** Closing comment on each card A1 to A7 with the evidence (run ids, the curl lines and their answers), the milestone's cards moved to `Done` by hand (the milestone branch carries no id).
- [ ] **Step 5:** Tell Ivan the production `.env` needs the same variable before the `pigrocrm-v*` tag that ships this (a production deploy is his call).

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

### Task B1: The expected days, the letter's numbers and the link on a match

**Files:**
- Create: `packages/core/migrations/versions/0021_match_pigro_link.py`
- Modify: `packages/core/src/rebase_core/models.py` (`Match`), `contract_schemas.py` (`MatchCreate`, `MatchRead`, `MatchListItem`), `matches.py` (`create` stores `giorni_previsti` and the three `lettera_*` values; `_match_read` and `_list_item` carry the new fields)
- Test: `packages/core/tests/test_migrations.py` (`compare_metadata` stays `[]`), `test_matches.py`

**Interfaces:**
- Produces, on `Match`:

```python
    giorni_previsti: Mapped[int | None] = mapped_column(Integer, default=None)
    lettera_data_inizio: Mapped[date | None] = mapped_column(Date, default=None)
    lettera_data_fine: Mapped[date | None] = mapped_column(Date, default=None)
    lettera_compenso: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), default=None)
    pigro_stato: Mapped[str | None] = mapped_column(String(20), default=None)
    pigro_slug: Mapped[str | None] = mapped_column(String(32), default=None)
    pigro_deal_id: Mapped[UUID | None] = mapped_column(default=None)
    pigro_url: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_errore: Mapped[str | None] = mapped_column(Text, default=None)
    pigro_mail_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
```

  with `CheckConstraint("giorni_previsti IS NULL OR giorni_previsti BETWEEN 1 AND 366", name="ck_matches_giorni_previsti")`
  and `CheckConstraint("pigro_stato IS NULL OR pigro_stato IN ('da_collegare', 'collegato', 'errore', 'rifiutato')", name="ck_matches_pigro_stato")`;
  `PIGRO_STATES = ("da_collegare", "collegato", "errore", "rifiutato")` in `models.py`.
  `MatchCreate.giorni_previsti: int | None = Field(default=None, ge=1, le=366)`.
  `MatchService.create` writes `giorni_previsti`, `lettera_data_inizio =
  data.lettera.data_inizio`, `lettera_data_fine = data.lettera.data_fine`,
  `lettera_compenso = data.lettera.compenso`.
  `MatchRead` gains `giorni_previsti`, `lettera_data_inizio: date | None`,
  `lettera_data_fine: date | None`, `lettera_compenso: Decimal | None`, and the eight
  `pigro_*` fields (`pigro_stato`, `pigro_slug`, `pigro_deal_id`, `pigro_url`,
  `pigro_linked_at`, `pigro_attempted_at`, `pigro_errore`, `pigro_mail_sent_at`);
  `MatchListItem` gains `giorni_previsti`, `pigro_stato`, `pigro_url`.
  The migration: twelve `ADD COLUMN IF NOT EXISTS`, the two `CHECK`s through the
  `pg_constraint` guard, and `UPDATE matches SET pigro_stato = 'da_collegare' WHERE
  stato = 'attivo' AND pigro_stato IS NULL` (§ 3.1's backfill), in the docstring style of
  `0019_documenso_envelopes.py`; `down_revision = "0019"` (re-pointed at `0020` before
  the merge if `0020_campaigns.py` has reached `main` by then).

- [ ] **Step 1: Failing tests**: `test_create_stores_the_expected_days` (a `MatchCreate` with `giorni_previsti=40` reads back 40; `None` reads back None; 0 and 367 are `ValidationError`), `test_create_stores_the_letters_dates_and_fee` (the `lettera_*` columns equal `data.lettera`'s values), `test_migrations.py` runs as is (fails until the migration exists because `compare_metadata` sees the columns).
- [ ] **Step 2: Run, fail.** **Step 3: Implement** the model and schemas, then the migration **in its own commit**.
- [ ] **Step 4: Run** `test_migrations.py` and `test_matches.py`; ruff, mypy.
- [ ] **Step 5: Commits** `feat(core): a match carries its expected days, its letter's numbers and its Pigro link` and `feat(core): migration 0021 adds the expected days, the letter's numbers and the Pigro link to matches` (`REB-497.`).

### Task B2: The hub's `EngagementService`

**Files:**
- Modify: `packages/core/src/rebase_core/config.py` (`pigro_engagements_token`), `projects/hub/.env.example`, `projects/hub/docker-compose.yml` (`x-api-environment`)
- Modify: `packages/core/src/rebase_core/http.py` (`ENGAGEMENTS_TIMEOUT_SECONDS = 90`; `_open` takes the timeout as a parameter; `urllib_engagements_call(method, url, headers, body)` is `urllib_call` with that timeout, the way `urllib_download_call` varies the byte cap)
- Modify: `packages/core/src/rebase_core/pigro.py` (the four sentences of `_fetch_rows` become module constants `NOT_ANSWERING`, `ANSWERED_STATUS` (a format), `TOO_LONG`, `NOT_THE_SHAPE`, and `_fetch_rows` uses them)
- Modify: `packages/core/src/rebase_core/contracts/fields.py` (`parse_italian_date(text: str) -> date`, the inverse of `italian_date` over `MONTHS`: «1° ottobre 2026» and «12 ottobre 2026»; `ContractFailed` on anything else)
- Create: `packages/core/src/rebase_core/engagements.py`
- Modify: `packages/core/src/rebase_core/contract_schemas.py` (`MatchReport` and parts), `mail.py` (`engagement_ready_mail`)
- Test: `packages/core/tests/test_engagements.py` (new; a recorded `HttpCall` the way `test_freelancers_companies.py`'s `fake_http` does), `test_contract_fields.py` (or where `italian_date` is tested: `parse_italian_date` round-trips every month and the first of a month)

**Interfaces:**
- Consumes: `MatchService.lock_match`, `MatchService.get`, `ContractDocument` (the
  match's letter: `numero`, `data["ruolo"]`, `data["data-inizio"]`, `data["compenso"]`),
  `User` of the freelancer, `Company.nome_azienda`, `signer_data(settings.signer_json)`
  and `amount(data, FEE)` (`contracts/fields.py`), `AdminActionService.record`,
  `EmailSender`, `HttpCall`, `InvalidState`.
- Produces:

```python
PIGRO_NOT_CONFIGURED = "Consuntivo non configurato su questo ambiente."
HOURS_PER_DAY = Decimal(8)
REPORT_MAX_DAYS = 800   # the CRM's cap

class PigroLinkResult(NamedTuple):
    linked: int
    failed: int

class ReportDay(BaseModel):
    data: date
    ore: Decimal
    descrizioni: list[str]
    fatture: list[str]           # distinct: ["12/2026"], ["12/2026", "proforma 3/2026"], or []

class ReportWeek(BaseModel):
    settimana: str               # "2026-W40"
    da: date
    a: date
    ore: Decimal

class ReportMonth(BaseModel):
    mese: str                    # "2026-10"
    ore: Decimal

class ReportInvoice(BaseModel):
    numero: str
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

def group_report(crm: dict[str, Any], giorni_previsti: int | None) -> MatchReport fields:
    """Pure: the CRM's `giorni` rows into per_giorno, per_settimana (ISO), per_mese and
    the totals; `avanzamento = totale_ore / (giorni_previsti * 8) * 100` at two places.
    Tested on its own."""

def normalise_vat(value: str | None) -> str | None:
    """`IT 0123 456 7890` -> `01234567890`; None unless exactly eleven digits remain."""

class EngagementService:
    def __init__(self, session: Session, settings: Settings, http: HttpCall, *, sender: EmailSender | None = None, now: Callable[[], datetime] = utcnow, today: Callable[[], date] = rome_today) -> None: ...
    def payload(self, match: Match, letter: ContractDocument, user: User, company: Company) -> dict[str, Any]: ...
    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead: ...
    def link_pending(self) -> PigroLinkResult: ...
    def report(self, match_id: UUID, da: date | None = None, a: date | None = None) -> MatchReport: ...
```

  `payload`: `freelancer` from the user; `lettera.numero = letter.numero`,
  `ruolo = letter.data["ruolo"]`, `azienda = company.nome_azienda`,
  `data_inizio`/`data_fine`/`compenso` from `match.lettera_*` when set, else
  `parse_italian_date(letter.data["data-inizio"])`, the same for `data-fine` when
  present, and `amount(letter.data, FEE)`; `giorni_previsti`; `rebase` from the signer
  mapping: `ragione_sociale`, `partita_iva = normalise_vat(...)`, `codice_fiscale`,
  `indirizzo = sede[:255]`, `pec`, `codice_sdi` only when seven characters. A letter not
  `firmato` → `InvalidState("match", "La lettera non è firmata.")`.
  `HTTPS_ONLY = "Pigro è raggiungibile solo su https."`: before any call, `link` and
  `report` check `settings.pigro_api_url` starts with `https://`, or with `http://`
  on `localhost` / `127.0.0.1` only; anything else is `PigroUnavailable(HTTPS_ONLY)`
  (for `link`: the match gets `errore` with that sentence), so the token never leaves
  in clear.
  `link`, in this order: lock the match (`lock_match`), refuse not-`attivo` with
  `InvalidState("match", "Si collega a Pigro solo un match attivo.")`; token empty →
  `pigro_stato = 'da_collegare'` if `None`, log once at info, commit, answer; else
  build the payload, `pigro_attempted_at = now()`, commit (the lock is released with
  it). Call `PUT {pigro_api_url}/api/rebase/engagements/{match_id}` (headers
  `Authorization: Bearer`, `Content-Type: application/json`, `Accept:
  application/json`) with no row lock held. Lock and re-read the match; if it is
  already `collegato`, leave the state (another caller won); else write: `201`/`200` →
  `collegato`, `pigro_slug`, `pigro_deal_id`, `pigro_url = deal_url`,
  `pigro_linked_at = now()`, `pigro_errore = None`; `409`/`422` → `rifiutato`,
  `pigro_errore = body["detail"]` (or the seam's status sentence when the body has
  none); anything else, an exception from `http`, a body that is not the shape →
  `errore` with the seam's sentence. In the same locked write, when the match is
  `collegato` and `pigro_mail_sent_at is None` and `sender` is set, stamp
  `pigro_mail_sent_at = now()` as a claim (`claimed = True`). Commit. Only when
  `claimed`: `sender.send(engagement_ready_mail(...))`; a refusal re-locks, sets
  `pigro_mail_sent_at = None`, commits. Two `link` calls racing on one match send one
  mail: the second sees the claim under the lock.
  With `admin_id`: `AdminActionService(session).record(entity_type="match",
  entity_id=match_id, kind="pigro_link", admin_id=admin_id, payload={"esito":
  stato, "errore": pigro_errore})`. Answer `MatchService(session).get(match_id)`.
  `link_pending`: every `attivo` match with `pigro_stato in ('da_collegare',
  'errore')` or (`collegato` and `pigro_mail_sent_at IS NULL`), `link` on each in its
  own try/except (a failure is logged, counted in `failed`, the loop goes on).
  `report`: not `collegato` → `InvalidState("match", <the state's sentence from
  match_words>)`; `da = lettera_data_inizio`, or `parse_italian_date(letter.data["data-inizio"])`
  when the column is NULL (a match older than 0021), or the match's `created_at` date
  when the letter has no start either, never today; `a = today()`; the span is walked
  in consecutive windows of at most `REPORT_MAX_DAYS` days (`[da, da+800]`,
  `[da+801, ...]`, up to `a`), one `GET .../report?da=&a=` each, the `giorni` rows
  concatenated and the `deal` taken from the last answer; any non-200 →
  `PigroUnavailable` with the seam's sentence; then `group_report`.

```python
def engagement_ready_mail(to: str, *, nome: str, numero: str, azienda: str, deal_url: str, spazio_creato: bool) -> Mail:
    """Subject «La tua lettera n. {numero} è attiva: le ore si registrano su Pigro»; the
    paragraphs of spec § 3.7, the hub's frame and button."""
```

- [ ] **Step 1: Failing tests**: `test_parse_italian_date_round_trips` (fields), `test_payload_from_a_match_with_the_columns`, `test_payload_from_an_older_match_parses_the_printed_letter`, `test_payload_normalises_rebase_fiscal_data` («IT 0123 456 7890» → `01234567890`; a six-character SDI → None; a 300-char address cut to 255), `test_link_refuses_a_match_that_is_not_active` (409 `InvalidState`), `test_link_without_token_marks_da_collegare_and_calls_nothing`, `test_link_refuses_plain_http` (`pigro_api_url = "http://pigro.example"` → `errore` with `HTTPS_ONLY`, `http` never called; `http://localhost:8000` allowed), `test_link_on_201_is_collegato_and_mails_once` (a second `link` with a recorded `200` sends no second mail), `test_two_links_racing_send_one_mail` (the recorded `http` of the first call runs a second `link` on the same match re-entrantly before answering; one mail leaves), `test_link_holds_no_lock_during_the_call` (the recorded `http` opens a second session and updates `pigro_attempted_at` without blocking), `test_link_writes_the_crm_sentence_on_409_as_rifiutato`, `test_link_on_422_is_rifiutato`, `test_link_on_refused_connection_is_errore_with_the_sentence`, `test_link_retries_a_refused_mail` (sender refuses once: `pigro_mail_sent_at` back to NULL; second `link` sends and stamps), `test_link_records_an_admin_action_when_an_admin_asked`, `test_link_pending_counts`, `test_link_pending_skips_rifiutato`, `test_report_groups_by_iso_week_and_month` (`group_report` with entries on 2026-12-28, 2026-12-31, 2027-01-01, 2027-01-04: weeks `2026-W53` and `2027-W01`, months `2026-12` and `2027-01`, both summing to the same total; `avanzamento` `"30.00"` for 96 hours of 40 days; `None` without days; a day with two entries on two invoices lists both in `fatture`), `test_report_refuses_a_match_not_linked` (409), `test_report_default_start_for_an_older_match` (the printed letter's start, then the match's creation date, never today), `test_report_walks_800_day_windows` (a 1000-day engagement: two calls, `da`/`a` contiguous, rows concatenated).
- [ ] **Step 2: Run, fail.** **Step 3: Implement** (settings first: the three edits, one line each with the comment in `config.py`'s voice).
- [ ] **Step 4: Run green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): the hub links a match to its Pigro deal and reads its hours` (`REB-498.`).

### Task B3: On activation, from the sweep, on «Riprova», over the API

**Files:**
- Modify: `packages/core/src/rebase_core/signing.py`: `SigningService.__init__` gains
  `engagements: EngagementService | None = None`; `_confirm_completion` sets
  `match.pigro_stato = "da_collegare"` beside `match.stato = "attivo"`; `_finish_outcome`,
  after the confirmation step, re-reads the document's match and, when it is `attivo`
  with `pigro_stato == "da_collegare"` and `self.engagements` is set, calls
  `self.engagements.link(match.id)` in the same try/except shape as the signed-copy
  step (a failure is logged and left for the sweep); `sweep` calls
  `self.engagements.link_pending()` after its loop and `SweepResult` gains `linked` and
  `link_failed`; `signing_from_settings(settings, renderer, *, documenso, sender)` gains
  `http: HttpCall = urllib_engagements_call` and builds the `EngagementService`
- Modify: `packages/core/src/rebase_core/cli.py` (`contracts_sweep` prints `, N match collegati a Pigro` and `, M non collegati` when not zero)
- Modify: `apps/api/src/rebase_api/deps.py` (`get_engagements(session, settings, http: HttpCallDep, sender) -> EngagementService` built with `urllib_engagements_call`, `EngagementsDep`; `get_signing_factory` hands the same service to `SigningService`), `apps/api/src/rebase_api/routers/matches.py` (`POST /api/hub/matches/{match_id}/pigro/link` → `MatchRead`; `GET /api/hub/matches/{match_id}/report` → `MatchReport`; both `AdminDep`; both catch `PigroUnavailable` → `HTTPException(502, str(exc))`, and answer `503` with `PIGRO_NOT_CONFIGURED` when the token is empty, the split `routers/pigro.py` makes)
- Test: `packages/core/tests/test_signing.py`, `test_cli.py`, `apps/api/tests/test_matches_api.py`

**Interfaces:**
- Consumes: B2's service. Produces: the two routes; `SweepResult(touched, unconfirmed, linked, link_failed)`.

- [ ] **Step 1: Failing tests**: `test_confirm_completion_marks_the_match_da_collegare`, `test_finish_links_a_match_that_just_turned_active` (a recorded `201`: `collegato` after `finish`), `test_finish_does_not_link_a_refused_letter` (a `REJECTED` envelope: the match stays `in_firma`, `link` never called), `test_sweep_retries_errore_matches` (an `errore` match and a recorded `201`: `linked == 1`), `test_sweep_without_engagements_links_nothing`; `test_contracts_sweep_prints_the_pigro_counts` (test_cli); `test_post_pigro_link_answers_the_match`, `test_post_pigro_link_of_a_draft_is_409`, `test_get_report_answers_the_grouped_report`, `test_get_report_of_unlinked_match_is_409`, `test_get_report_when_pigro_is_down_is_502`, `test_routes_answer_503_without_the_token` (API).
- [ ] **Step 2: Run, fail.** **Step 3: Implement.** The webhook route already returns before `finish` runs (background); nothing new waits on the CRM.
- [ ] **Step 4: Run** `test_signing.py`, `test_cli.py`, then the API suite; ruff, mypy.
- [ ] **Step 5: Commit** `feat(hub): an active match links itself to Pigro, the sweep retries, «Riprova» and the report answer over the API` (`REB-499.`).

### Task B4: The card's sentence, the member's link, and the action's labels

**Files:**
- Modify: `packages/core/src/rebase_core/match_words.py` (`Action` gains `"riprova_pigro"`; `match_words(...)` takes `pigro_stato: str | None` and `pigro_errore: str | None` and, for an `attivo` match, appends one sentence to `situazione`: «Le ore si consuntivano su Pigro.» for `collegato`, «Pigro non ha ancora il deal: riprova o aspetta lo sweep.» for `da_collegare`, «Pigro non ha risposto: {errore}» for `errore`, «Pigro ha rifiutato il collegamento: {errore}» for `rifiutato`; `altre_azioni` gains `riprova_pigro` for `da_collegare`, `errore` and `rifiutato`; `pigro_state_sentence(pigro_stato, pigro_errore) -> str` exported for B2's `report` refusal)
- Modify: `packages/core/src/rebase_core/matches.py` (`_match_read`, `_list_item` pass the two fields), `member_contracts.py` and `contract_schemas.py` (`MemberContract.pigro_url: str | None`, set for a letter whose match is `collegato`)
- Modify: `apps/web/src/lib/api.ts` (the `Action` union gains `'riprova_pigro'`) and `apps/web/src/lib/format.ts` (`ACTION_LABELS.riprova_pigro = 'Riprova su Pigro'`, `ACTION_PENDING_LABELS.riprova_pigro = 'Collego a Pigro…'`): `test_web_labels.py` holds the two maps equal to core's `Action` literal, so the label edits land in this task, not B6
- Test: `packages/core/tests/test_match_words.py`, `test_matches.py`, `test_member_contracts.py` (or where `MemberContractService` is tested), `test_web_labels.py`

- [ ] **Step 1: Failing tests**: one per sentence and the action list; `test_member_letter_carries_the_pigro_url`; `test_web_labels.py` as is.
- [ ] **Step 2: Run, fail.** **Step 3: Implement.** **Step 4: Run green; ruff, mypy; `pnpm --filter hub lint && pnpm --filter hub test -- format`.**
- [ ] **Step 5: Commit** `feat(core): a match says where its hours are, and the member sees the link` (`REB-500.`).

### Task B5: The two MCP tools

**Files:**
- Modify: `apps/mcp/src/rebase_mcp/server.py` (`build_server` takes `engagements: Callable[[Session], EngagementService] | None = None` the way it takes `signing`; beside PR #416's match tools: `get_match_report(match_id: str, da: str | None = None, a: str | None = None) -> dict[str, Any]` and `link_match_to_pigro(match_id: str) -> dict[str, Any]`, through `_on_match` where it fits, both turning `PigroUnavailable` into `ToolError(str(exc))` as `list_pigro_spaces` does; `create_match` gains `giorni_previsti: int | None = None`), `apps/mcp/src/rebase_mcp/__main__.py` and `http.py` (build the factory with `urllib_engagements_call`)
- Test: `apps/mcp/tests/test_tools.py` (the tool list, and one test per tool with a recorded `HttpCall`)

- [ ] **Step 1: Failing tests.** **Step 2: Run, fail.** **Step 3: Implement**, descriptions in Italian in the register of the existing tools.
- [ ] **Step 4: Run** the MCP suite; ruff, mypy.
- [ ] **Step 5: Commit** `feat(mcp): an admin reads a match's hours and retries its Pigro link` (`REB-501.`).

### Task B6: The wizard's field and the lists

**Files:**
- Modify: `apps/web/src/lib/api.ts` (`Match`, `MatchListItem`, `MatchCreate` gain the fields; `matches.report(id)`, `matches.linkPigro(id)`; `MatchReport` types), `apps/web/src/lib/format.ts` (`PIGRO_STATE_LABELS`: «Collegato», «Da collegare», «Errore», «Rifiutato»), `apps/web/src/lib/contracts.ts` (the wizard's form state carries `giorni_previsti` beside, not inside, `LetteraForm`: `toLettera(form)` must not leak it, since `LetteraFields` forbids unknown keys; it goes on `MatchCreate`)
- Modify: `apps/web/src/pages/admin/crea-match/CondizioniStep.tsx` (after the «Compenso» block: `Label` «Giorni previsti», `Input type="number" inputMode="numeric"` id `match-giorni_previsti`, helper text «Per il consuntivo: 8 ore al giorno. Il testo della lettera resta quello di «Impegno».»), `ControllaStep.tsx` (one line in the summary), `pages/admin/Contratti.tsx` (`matchHandles` gains the `riprova_pigro` handle calling `matches.linkPigro`), `contratti/Cards.tsx` (a «Consuntivo» link button to `/admin/matches/$id/report` beside the document links, for a `collegato` match), `lists.tsx` and `Matches.tsx` (a «Pigro» column with the label)
- Test: the `*.test.tsx` beside each: the field round-trips into the request body's `giorni_previsti` and not into `lettera`; the check step shows «Giorni previsti: 40»; the card shows the sentence, the «Riprova su Pigro» item calls `linkPigro`, «Consuntivo» links; the column renders the label.

- [ ] **Step 1: Failing tests.** **Step 2: `pnpm --filter hub test -- <file>`, fail.** **Step 3: Implement.**
- [ ] **Step 4:** `pnpm --filter hub lint && pnpm --filter hub test && pnpm --filter hub build`.
- [ ] **Step 5: Commit** `feat(hub): «Crea match» asks the expected days, the lists say the Pigro link` (`REB-502.`).

### Task B7: «Consuntivo»

**Files:**
- Create: `apps/web/src/pages/admin/Consuntivo.tsx`, `Consuntivo.test.tsx`, `apps/web/src/lib/report.ts` (the pure slicing and regrouping by month), `report.test.ts`
- Modify: `apps/web/src/router.tsx` (`adminMatchReport`, `path: '/matches/$id/report'`, `validateSearch` for `mese` as `YYYY-MM` or `tutto`)
- Modify: `apps/web/src/pages/member/Contratti.tsx` (`Letter`: for `lettera.pigro_url`, a `Button asChild variant="outline" size="sm"` «Le tue ore su Pigro» and the line «rebase legge le ore di questo progetto per la rendicontazione al cliente.»), `Contratti.test.tsx`

**The page (spec § 3.5):** heading «Consuntivo», the match's title from `matches.get`
(company, role, letter number) and a link «Apri il deal su Pigro»; a `Select` of the
months between the letter's start and today plus «Tutto l'incarico», default the current
month, kept in the URL; the progress line «96 ore, 12 giorni su 40 previsti (30%)» or
«96 ore, 12 giorni»; a `Table` per day (Data, Ore, Descrizione, Fatture, the day's
list joined with a comma, «da fatturare» when empty); «Per settimana» and «Per mese» as
two small tables of the selected period; «Fatture» (Numero, Data, Stato, Incasso, Ore)
scoped to the period: with a month selected, only the invoices the shown days sit on,
each with the hours of those days on it; with «Tutto l'incarico», every invoice with all
its hours (the day rows carry their `fatture`, so `lib/report.ts` recomputes the
invoice hours per period from the days alone, and an invoice spanning two months shows
in both with each month's hours; tested on a two-month invoice). Loading, an error
sentence from the API (a `409` shows the state's sentence, a `502` «Pigro non
risponde», a `503` the not-configured sentence), each as a paragraph. All data from one
`matches.report(id)` call for the whole engagement; the month filter slices `per_giorno`
client-side and recomputes the two small tables from the sliced days (`lib/report.ts`).

- [ ] **Step 1: Failing tests** with a recorded report: the progress line, the day rows, the invoice number, the month filter, the «Tutto l'incarico» option, the three error paragraphs, the member button.
- [ ] **Step 2: fail. Step 3: Implement. Step 4: lint, test, build.**
- [ ] **Step 5: Commit** `feat(hub): «Consuntivo» shows a match's hours per day, week and month, and the member finds their deal` (`REB-503.`).

### Task B8: The clause in the letter, and the decision

**Files:**
- Modify: `packages/core/src/rebase_core/contracts/texts/lettera-di-incarico.md` (the paragraph of spec § 3.8 after the one on `scadenze-fatturazione`; `text_version` in its front matter bumped)
- Test: `packages/core/tests/test_contract_render.py` (typesets the letter: `rebase contracts-check` passes), `test_matches.py` (a letter written now carries the new `text_version`)

The DECISIONS row of spec § 4 is already on `main` with the design record (PR #424);
nothing to add here.

- [ ] **Step 1:** Read the letter's front matter and how `text_version` is read (`contracts/__init__.py`, `fields.py`). Add the paragraph, bump the version, run `uv run rebase contracts-check`.
- [ ] **Step 2:** Run `test_contract_render.py`, `test_contract_pdf.py`, `test_matches.py` (a letter written from the new text carries the new version; an existing document keeps its own).
- [ ] **Step 3: Commit** `feat(contracts): the letter of engagement carries the reporting clause` (`REB-504.`), and a comment on REB-504 asking Ivan to read the wording on the PR: his «ok» on that card is the gate B9 waits for before `gh pr ready`.

### Task B9: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main` once PR #416 has merged; if `0020_campaigns.py` is on `main`, re-point `0021`'s `down_revision` to `0020`; resolve, rerun every suite in the foreground, one DB-backed run at a time.
- [ ] **Step 2:** On the preview (Documenso is on there, and A7 set the CRM's token): set `REBASE_PIGRO_ENGAGEMENTS_TOKEN` in the preview hub's `.env`, restart `api` and `sweep`, create a match for a test freelancer of ours with `giorni_previsti`, send it for signature, sign on Documenso, watch the sweep line («1 match collegati a Pigro»), open the preview CRM as that freelancer (the welcome mail's link), log two hours on the deal, open «Consuntivo» on the preview hub and read them.
- [ ] **Step 3:** The before-and-after pairs (the card, the «Match» list, the wizard's conditions step, the member's letter, «Consuntivo») and the video of the flow, with the monorepo's `docs/pr-screenshots/record.mjs` (`docs/pr-screenshots/README.md`; the screenshot stack runs on its own ports, never `:8000`).
- [ ] **Step 4:** Not before Ivan's «ok» on the clause's wording sits on REB-504 (B8): the PR stays a draft until then, whatever else is done. Then `gh pr ready`, the fresh reviewer on the whole diff, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comment on every card B1 to B9 and each moved to `Done` by hand; a project update on P-REB-42 (three sentences, `type: "project"`).
- [ ] **Step 5:** Tell Ivan the production hub `.env` needs the token before the `hub-v*` tag, and that the CRM tag goes first.

## Self-review (done while writing, redone after the review of 2026-09-25)

- **Spec coverage.** § 2.1 A1, A6 (the public URL refusal); § 2.2 A2; § 2.3 A3, A4; § 2.4
  A5; § 2.5 A1, A4 (the work-units branch); § 2.6 nothing to do; § 3.1 B1; § 3.2 B2, B3
  (https only in B2); § 3.3 B2, B3; § 3.4 B6; § 3.5 B3, B7; § 3.6 B4, B7; § 3.7 B2; § 3.8
  B8; § 3.9 B5; § 3.10 tests in A4, B2 and B3; § 4 A7, B9 (the DECISIONS row rides the
  design record's own PR); § 5 the tests named in each task; § 6 the two milestones.
- **The PR reviewers' findings (2026-09-25, PR #424).** Folded in: the deal recovered by
  its note marker through exact repository queries (A4), the fixtures and the preview
  run dropping only what they created and keeping the token out of `argv` (A4, A6,
  A7), the report's start for an older match and its 800-day windows (B2), a day's
  invoices as a list (B2, B7), the mail claimed under the lock (B2), https only (B2),
  the invoice table scoped to the period (B7), the wording gate before `gh pr ready`
  (B8, B9), the deleted deal's recovery documented (spec § 3.10, § 7), the DECISIONS
  row in the design PR.
- **Placeholders.** None left: the migration is `0021` with its re-pointing rule, the
  preview host is named, the test files exist under the names given, the lookups name
  the repository methods, today is `today_local()`.
- **Type consistency.** `EngagementUpsert` / `EngagementRead` / `EngagementReport` (CRM)
  and `MatchReport` (hub) are named the same in every task that uses them; `HOURS_PER_DAY`
  is a `Decimal(8)` on both sides; `pigro_stato` values are the four strings of B1
  everywhere; the action is `riprova_pigro` in core, MCP and web; the constructor is
  `EngagementService(session, settings, http, *, sender, now, today)` in B2, B3 and B5;
  the seam function is `urllib_engagements_call` in B2, B3 and B5.
- **Review Focus.** Each of the five has its test in A4, B2 or B3, named above.
