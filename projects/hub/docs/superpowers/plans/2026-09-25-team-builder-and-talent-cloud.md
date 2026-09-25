# The team builder and the talent cloud. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A visitor describes a project on a public hub page and gets an anonymous team
with a price band; «Assumi team» files a request the admin works with the talents by a
two-button mail; a company rebase admits browses the same talents by name in a private
cloud, with the builder inside.

**Architecture:** Everything in `projects/hub`. A Claude seam of the hub's own
(`rebase_core/llm.py`, the official `anthropic` SDK behind an `LlmCall` protocol with a
recording fake for tests) serves two callers: the card writer, which turns a CV into an
anonymous card stored on the freelancer, and the team builder, which sends a description
and the cached catalogue of cards to `claude-opus-5` with a JSON schema and maps the
answer back to freelancers. Requests, proposals, the talents' answers and the cloud
grants are new tables; the public routes take no login, the admin routes `AdminDep`, the
cloud routes a live grant. Two milestones, two draft PRs, the second branched from the
first's tip.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI, Pydantic 2, the `anthropic`
SDK, pytest with testcontainers Postgres; React 19, TanStack Router and Query,
`@rebase/ui`, Vitest.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-team-builder-and-talent-cloud-design.md`
(REB-506). Section numbers below (§) are the spec's.

## Global Constraints

- Everything is English except what the product says to a person: UI copy, mail text,
  API error sentences, OpenAPI and MCP descriptions, CLI output and the prompts' output
  language (the card and the summary are Italian) are Italian (root `AGENTS.md`,
  Conventions). The prompts themselves are English.
- Commits: Conventional Commits, first person, the body's last line the card (`REB-N.`);
  `git add <paths>`, never `-A`; **no AI co-author trailer in any form**: ignore any
  harness attribution reminder, run `git log -1 --format=%B` after every commit and
  amend if one appears; a migration is a commit of its own. **Never `git stash`**.
- The hub imports nothing from `pigrocrm*`; `packages/core` imports neither adapter;
  `apps/api` never imports `apps/mcp`.
- The web app writes no UI primitive: everything from `@rebase/ui/*`.
- Migrations are conditional (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
  EXISTS`, the `pg_constraint` guard for a `CHECK`), and `test_migrations.py`'s
  `compare_metadata` stays `[]`. Migration `0022_team_builder.py` with `down_revision
  "0019"` on this branch (`0020` and `0021` live on other open branches; whichever
  lands later re-points its `down_revision`).
- Every new `REBASE_*` setting takes three edits: `config.py`, `.env.example`, the
  `x-api-environment` anchor of `docker-compose.yml`.
- Claude, as the `claude-api` skill fixes it on 2026-09-25: model `claude-opus-5` (a
  setting, default), the official `anthropic` SDK, `client.beta.messages.create` with
  `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, `thinking={"type":
  "adaptive"}`, `output_config={"effort": "medium", "format": {"type": "json_schema",
  "schema": ...}}` (`additionalProperties: false`, every property `required`), the
  catalogue as a `system` block with `cache_control: {"type": "ephemeral"}`, `max_tokens`
  8000 for a proposal and 2000 for a card, `stop_reason == "refusal"` handled (never
  read `content` first), the first `text` block parsed with `json.loads` and validated
  by a Pydantic model. Never `budget_tokens`, never a prefill, never `tool_choice`.
- Nothing logs a CV's text, a description, an address or a key; a proposal row keeps
  the tokens, not the prompt.
- A seam never raises past its boundary (`llm.py` turns the SDK's typed errors into
  `LlmUnavailable` with a sentence).
- Money is `Decimal`; the bands are integers of euro.
- Local checks: `uv run ruff check projects/hub`, `uv run mypy
  projects/hub/packages/core/src projects/hub/apps/api/src projects/hub/apps/mcp/src`,
  the pytest suites in the foreground one at a time, `pnpm --filter hub lint`, `test`,
  `build`. Never ports 55432 or 55433. `df -h /` before an image build.

## Review Focus

1. **A short id the model invents or repeats.** The engine drops an id the catalogue
   does not hold, dedupes a person named twice, logs both, and never answers a
   freelancer the catalogue did not offer (Task C4, `test_engine_drops_unknown_and_repeated_ids`).
2. **A summary that names the company.** The description says «per Acme S.r.l.», the
   summary must not; the writer checks the summary against the request's `azienda`
   words when the request arrives and logs a hit, and the prompt forbids it (Task C5,
   `test_request_logs_a_summary_that_names_the_company`).
3. **Bands at the boundaries and without a rate.** 299, 300, 499, 500, 800, no rate;
   the team's band with one person without a rate says so (Task C4,
   `test_bands_at_every_boundary`).
4. **A CV that changes, and one that goes.** The card is rewritten only when the SHA-256
   differs; deleting the CV deletes the card; a refusal keeps the previous card and
   writes `error` (Task C3, `test_card_follows_the_cv`).
5. **The two buttons, twice.** A token answers once; the second click, either button,
   lands on «non più valido»; a token older than thirty days does the same (Task D1,
   `test_availability_token_is_one_use_and_expires`).

---

## Card groups and order

Milestone C is on the draft PR of branch `ivansala/milestone-team-builder` (from
`origin/main`); milestone D on `ivansala/milestone-talent-cloud`, created from C's tip
once C1 to C5 are on it (its tables and services), and merged after C.

| Task | Card | Title | Depends on |
|---|---|---|---|
| C1 | REB-508 | Add the Claude seam and its settings to the hub | none |
| C2 | REB-509 | Store cards, proposals, requests and grants | none |
| C3 | REB-510 | Write an anonymous card for every freelancer with a CV | C1, C2 |
| C4 | REB-511 | Propose a team from a description with a price band | C3 |
| C5 | REB-512 | File a team request and serve it to the admin over the API | C4 |
| C6 | REB-513 | Build the public team page | C5 |
| C7 | REB-514 | Show «Richieste team» and the talent's card in the admin | C5 |
| C8 | REB-515 | Say on the privacy page what goes to Anthropic | none |
| C9 | REB-516 | Prove a public proposal on the preview and land the milestone | C6, C7, C8 |
| D1 | REB-517 | Ask each talent with a two-button mail and record the answer | C5 |
| D2 | REB-518 | Mark a talent vetted and open the cloud to a company | C5 |
| D3 | REB-519 | Let an admitted company browse the talents by name and ask | D2 |
| D4 | REB-520 | Give the hub MCP server the team, card, vetted and cloud actions | D1, D2, D3 |
| D5 | REB-521 | Prove the cloud on the preview and land the milestone | D4 |

## File structure

- `packages/core/src/rebase_core/config.py`: `anthropic_api_key`, `team_builder_model`,
  `team_builder_enabled`; `.env.example`, `docker-compose.yml`.
- `packages/core/pyproject.toml`: `anthropic` pinned; `uv.lock` updated.
- `packages/core/src/rebase_core/llm.py`: `LlmRequest`, `LlmResponse`, `LlmCall`,
  `LlmUnavailable`, `AnthropicCall`, `call_from_settings`.
- `packages/core/migrations/versions/0022_team_builder.py`; `models.py`: `FreelancerCard`,
  `TeamProposal`, `TeamRequest`, `TeamRequestTalent`, `TalentCloudGrant`,
  `Freelancer.vetted_at`, `vetted_by`.
- `packages/core/src/rebase_core/team_schemas.py`: every read and write model of the
  feature (`FreelancerCardRead`, `Card`, `TeamProposalCreate`, `TeamProposalRead`,
  `TeamRequestCreate`, `TeamRequestRead`, `TeamRequestListItem`, `CloudTalentRead`,
  `TalentCloudGrantRead`, the bands).
- `packages/core/src/rebase_core/cards.py`: `CardWriter`, `card_prompt`, `CARD_SCHEMA`.
- `packages/core/src/rebase_core/bands.py`: `band_for(rate) -> Band | None`, `team_bands`.
- `packages/core/src/rebase_core/team_builder.py`: `TeamBuilder`, `catalogue_lines`,
  `PROPOSAL_SCHEMA`, `proposal_prompt`.
- `packages/core/src/rebase_core/team_requests.py`: `TeamRequestService`, the tokens,
  the answers, the grants (`TalentCloudService` in the same module or `cloud.py`).
- `packages/core/src/rebase_core/mail.py`: `team_request_mail` (to ciao@),
  `team_availability_mail`, `talent_cloud_opened_mail`.
- `packages/core/src/rebase_core/analytics.py`: `Tracker.team_event(name, properties)`.
- `packages/core/src/rebase_core/cli.py`: `cards-refresh`.
- `apps/api/src/rebase_api/deps.py`: `LlmDep`, `CloudDep` (the grant guard);
  `routers/team.py` (public and admin), `routers/cloud.py` (member), `routers/freelancers.py`
  (card, vetted), `routers/companies.py` (grants), `routers/members.py` (`talent_cloud`).
- `apps/mcp/src/rebase_mcp/server.py`: the tools of § 8.
- `apps/web/src/pages/Team.tsx`, `TeamRisposta.tsx` (public); `pages/admin/RichiesteTeam.tsx`,
  `RichiestaTeam.tsx`; `pages/member/Cloud.tsx`; `lib/api.ts`, `lib/format.ts`,
  `lib/bands.ts`; `router.tsx`; `components/SidebarNav.tsx`; `pages/CompanyWizard.tsx`
  (the origin box); `pages/admin/lists.tsx` (vetted action, «da team builder», the
  grant action); the talent detail page (the card, «Rigenera scheda»).
- `projects/website/src/privacy.html`: the Anthropic paragraph.
- Tests beside each: `packages/core/tests/test_llm.py`, `test_cards.py`, `test_bands.py`,
  `test_team_builder.py`, `test_team_requests.py`, `test_cloud.py`, `test_migrations.py`;
  `apps/api/tests/test_team_api.py`, `test_cloud_api.py`; `apps/mcp/tests/test_tools.py`;
  the web `*.test.tsx`.

---

## Milestone C: Describe every talent anonymously and propose a team in public

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-team-builder ../pigrocrm-team-builder origin/main`, `uv sync
--frozen && pnpm install --frozen-lockfile --prefer-offline`, `gh pr create --draft` with
the milestone's name and the checklist C1 to C9 after the first commit. Read
`projects/hub/AGENTS.md` first.

### Task C1: The Claude seam and its settings

**Files:**
- Modify: `packages/core/pyproject.toml` (`"anthropic==<the current release on PyPI, pinned>"`, with a comment naming the two callers), the root `uv.lock` (`uv lock`; the workspace lock lives at the repository root)
- Modify: `packages/core/src/rebase_core/config.py`, `projects/hub/.env.example`, `projects/hub/docker-compose.yml`
- Create: `packages/core/src/rebase_core/llm.py`
- Test: `packages/core/tests/test_llm.py`

**Interfaces:**

```python
class LlmRequest(BaseModel):
    system: list[dict[str, Any]]        # text blocks, the last may carry cache_control
    messages: list[dict[str, Any]]
    schema: dict[str, Any]               # the output_config.format json_schema
    max_tokens: int

class LlmResponse(BaseModel):
    text: str | None                     # the first text block, None on a refusal
    stop_reason: str
    refusal_category: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int

class LlmUnavailable(Exception):
    """The provider did not answer: the sentence the page shows."""

class LlmCall(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...

class AnthropicCall:
    def __init__(self, api_key: str, model: str) -> None: ...   # anthropic.Anthropic(api_key=...)
    def complete(self, request: LlmRequest) -> LlmResponse: ...

def call_from_settings(settings: Settings) -> LlmCall | None:
    """None without a key: the callers then refuse with their own sentence."""
```

  `complete` calls `self.client.beta.messages.create(model=self.model,
  max_tokens=request.max_tokens, betas=["server-side-fallback-2026-07-01"],
  fallbacks="default", thinking={"type": "adaptive"}, output_config={"effort":
  "medium", "format": {"type": "json_schema", "schema": request.schema}},
  system=request.system, messages=request.messages)`, reads `stop_reason`,
  `stop_details.category` when the reason is `refusal`, the first `text` block
  otherwise, and `usage.input_tokens`, `usage.output_tokens`,
  `usage.cache_read_input_tokens or 0`. `anthropic.APIConnectionError`,
  `anthropic.RateLimitError` and `anthropic.APIStatusError` become `LlmUnavailable("Non
  riesco a proporre un team adesso: riprova tra poco.")`, most specific first, logged
  with the class name and status and never the request. Settings: `anthropic_api_key:
  str = Field(default="", repr=False)`, `team_builder_model: str = "claude-opus-5"`,
  `team_builder_enabled: bool = True`, each with a comment in the file's voice.
  The tests build `AnthropicCall` over a fake `client` object (a stub with
  `beta.messages.create` recording the kwargs and answering a canned message object with
  `content`, `stop_reason`, `stop_details`, `usage`, `model`): the kwargs carry exactly
  the fields above; a refusal answers `text=None` and the category; a connection error
  becomes `LlmUnavailable`.

- [ ] **Step 1: Failing tests** (`test_complete_sends_the_documented_request`, `test_refusal_answers_no_text`, `test_provider_errors_become_unavailable`, `test_settings_default`).
- [ ] **Step 2: fail. Step 3: implement** (`uv add --package rebase-core anthropic==<version>` or the manual pin plus `uv lock`; check `uv sync --frozen` passes after). **Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): the hub speaks to Claude through a seam of its own` (`REB-508.`). The lock change goes in the same commit.

### Task C2: The tables

**Files:**
- Create: `packages/core/migrations/versions/0022_team_builder.py`
- Modify: `packages/core/src/rebase_core/models.py`
- Create: `packages/core/src/rebase_core/team_schemas.py` (the read models the later tasks fill; here the `Card` model and the constants)
- Test: `packages/core/tests/test_migrations.py`

**Interfaces:** the tables of spec § 2, as SQLAlchemy models with these names and
columns:

```python
CARD_SENIORITIES = ("junior", "mid", "senior", "lead")
TEAM_REQUEST_STATES = ("nuova", "contattata", "chiusa")
TEAM_ORIGINS = ("pubblico", "cloud")
TALENT_ANSWERS = ("si", "no")

class FreelancerCard(Base, TimestampMixin):          # freelancer_id is the PK (FK freelancers.id, ondelete CASCADE)
class TeamProposal(Base, PrimaryKeyMixin):           # descrizione, nota, previous_id, riassunto, luogo JSONB, team JSONB, economia JSONB, model, input_tokens, output_tokens, cache_read_tokens, origine, user_id, client_hash, created_at
class TeamRequest(Base, PrimaryKeyMixin, TimestampMixin)   # proposal_id, origine, azienda, email, telefono, user_id, company_id, stato, note, contacted_at, closed_at
class TeamRequestTalent(Base, PrimaryKeyMixin)       # request_id, freelancer_id, ruolo, token_hash (unique), mail_sent_at, risposta, risposta_at; unique (request_id, freelancer_id)
class TalentCloudGrant(Base, PrimaryKeyMixin)        # company_id, user_id, granted_by, granted_at, revoked_by, revoked_at; partial unique index on user_id where revoked_at IS NULL
# Freelancer: vetted_at, vetted_by

class Card(BaseModel):                                # team_schemas.py, spec § 2.1
    model_config = ConfigDict(extra="forbid")
    ruolo: SafeStr = Field(min_length=1, max_length=120)
    seniority: Literal["junior", "mid", "senior", "lead"]
    anni: int = Field(ge=0, le=60)
    competenze: list[SafeStr] = Field(max_length=20)
    settori: list[SafeStr] = Field(max_length=10)
    lingue: list[SafeStr] = Field(max_length=8)
    modalita: Literal["remoto", "ibrido", "in_sede"]
    luogo: SafeStr | None = Field(default=None, max_length=120)
    sintesi: SafeStr = Field(min_length=1, max_length=400)
```

  Checks as constraints (`ck_team_requests_stato`, `ck_team_proposals_origine`,
  `ck_team_request_talents_risposta`), indexes on `team_requests (stato, created_at)`,
  `team_proposals (created_at)`, `talent_cloud_grants (user_id)`.

- [ ] **Step 1:** `test_migrations.py` fails until the migration exists. **Step 2: implement** the models and schemas, then the migration in its own commit, in `0019`'s docstring style and with `IF NOT EXISTS` everywhere.
- [ ] **Step 3:** `test_migrations.py` green; ruff, mypy.
- [ ] **Step 4: Commits** `feat(core): the hub stores cards, team proposals, requests and cloud grants` and `feat(core): migration 0022 creates the team builder's tables` (`REB-509.`).

### Task C3: The card writer

**Files:**
- Create: `packages/core/src/rebase_core/cards.py`
- Modify: `packages/core/src/rebase_core/members.py` (`replace_cv`: after the commit, the caller's background task writes the card), `freelancers.py` (`create_from_signup` path: same), `cli.py` (`cards-refresh --limit N`), `team_schemas.py` (`FreelancerCardRead`)
- Modify: `apps/api/src/rebase_api/deps.py` (`LlmDep = Annotated[LlmCall | None, Depends(get_llm)]`), `routers/members.py` and `routers/freelancers.py` (the background task after a CV write; `GET /api/hub/freelancers/{id}/card`, `POST /api/hub/freelancers/{id}/card` to regenerate, admin)
- Test: `packages/core/tests/test_cards.py`, `test_cli.py`, `apps/api/tests/test_freelancers_api.py` (or where the CV routes are tested)

**Interfaces:**

```python
CARD_MAX_TOKENS = 2000

class FreelancerCardRead(BaseModel):
    freelancer_id: UUID
    card: Card | None
    cv_sha256: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None

def card_prompt(cv: CvText, posizione: str | None, remoto: str | None) -> LlmRequest:
    """System: what an anonymous card is, the rules (no name, no employer, no link,
    Italian, two sentences of sintesi at most, `modalita` copied from the profile,
    `luogo` the city or region the CV names or null); user: the profile fields and the
    CV's text. Schema: `Card`'s JSON schema with additionalProperties false."""

class CardWriter:
    def __init__(self, session: Session, llm: LlmCall | None, *, now: Callable[[], datetime] = utcnow) -> None: ...
    def write(self, freelancer_id: UUID) -> FreelancerCardRead: ...
    def refresh_stale(self, limit: int = 50) -> int: ...
    def delete(self, freelancer_id: UUID) -> None: ...
```

  `write`: no CV → delete the row and answer an empty read; no `llm` → answer the
  current row untouched; else the text through `FreelancerService.cv_text`, the
  prompt, `llm.complete`, a refusal or `LlmUnavailable` → keep the row, write `error`,
  answer; a valid card → upsert with the SHA-256 of `cv_bytes`, the model, the tokens,
  `generated_at`, `error = None`. `modalita` is overwritten from `Freelancer.remoto`
  whatever the model wrote. `refresh_stale`: every live freelancer with a CV whose card
  is missing or whose `cv_sha256` differs, `limit` at a time, oldest first; answers how
  many were written. `MemberService.replace_cv` and the CV removal call `delete` or
  leave the write to the route's background task (`BackgroundTasks` as the wizard's
  routes use it), so the member's response never waits on Claude.

- [ ] **Step 1: Failing tests**: `test_card_from_a_cv` (a `RecordingCall` answering a canned card: the row, the hash, the tokens; `modalita` forced from the profile), `test_card_follows_the_cv` (same hash → no call; new bytes → rewritten; CV removed → row gone), `test_refusal_keeps_the_card_and_writes_error`, `test_no_key_writes_nothing`, `test_refresh_stale_limits_and_counts`, `test_cards_refresh_prints_the_count` (CLI), the API: the two admin routes and that a CV upload schedules the write.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): every freelancer with a CV gets an anonymous card` (`REB-510.`).

### Task C4: The bands and the engine

**Files:**
- Create: `packages/core/src/rebase_core/bands.py`, `team_builder.py`
- Modify: `team_schemas.py` (`Band`, `TeamMemberRead`, `TeamProposalRead`, `TeamProposalCreate`), `analytics.py` (`Tracker.team_event`)
- Test: `packages/core/tests/test_bands.py`, `test_team_builder.py`

**Interfaces:**

```python
CLIENT_MARKUP = Decimal("1.4")
DAYS_PER_MONTH = 22
BANDS: tuple[tuple[int, int | None], ...] = ((0, 300), (300, 400), (400, 500), (500, 650), (650, 800), (800, None))

class Band(BaseModel):
    min: int
    max: int | None            # None: «oltre»
    def label(self) -> str      # «400–500 € al giorno», «oltre 800 € al giorno»

def band_for(rate: Decimal | None) -> Band | None
def team_bands(members: list[Band | None]) -> tuple[Band | None, Band | None]   # per day, per month; None when any member has none

class TeamProposalCreate(BaseModel):
    descrizione: SafeStr = Field(min_length=40, max_length=4000)
    nota: SafeStr | None = Field(default=None, max_length=500)
    previous_id: UUID | None = None

class TeamMemberRead(BaseModel):
    freelancer_id: UUID
    ruolo: str
    motivazione: str
    giorni_settimana: int | None
    scheda: Card                 # sintesi and the rest; `luogo` stripped to None on the public face
    fascia: Band | None

class TeamProposalRead(BaseModel):
    id: UUID
    riassunto: str
    luogo: dict[str, Any]        # {"locale": bool, "dove": str | None}
    team: list[TeamMemberRead]
    economia: dict[str, Any]     # {"giorno": Band | None, "mese": Band | None, "giorni_mese": 22}
    previous_id: UUID | None
    created_at: datetime

PROPOSAL_MAX_TOKENS = 8000

def catalogue_lines(session: Session) -> tuple[str, dict[str, UUID]]:
    """One JSON line per live freelancer with a card (deleted_at IS NULL, stato != 'scartato'):
    {"id": <first 8 hex of the id>, "ruolo", "seniority", "anni", "competenze", "settori",
    "lingue", "modalita", "luogo", "fascia": <label or null>}, sorted by id so the text
    is stable across requests (the cache prefix), and the map from short id to id."""

def proposal_prompt(catalogue: str, data: TeamProposalCreate, previous: TeamProposal | None) -> LlmRequest

class TeamBuilder:
    def __init__(self, session: Session, llm: LlmCall | None, settings: Settings, *, tracker: Tracker | None = None, now=utcnow) -> None: ...
    def propose(self, data: TeamProposalCreate, *, origine: str, user_id: UUID | None, client_hash: str | None) -> TeamProposalRead: ...
    def get(self, proposal_id: UUID) -> TeamProposalRead: ...
```

  `propose`: `settings.team_builder_enabled` false or `llm` None → `InvalidState("team",
  "Il team builder è spento.")` (the route maps it to `503`); `previous_id` must exist
  and be younger than a day; the prompt of § 3.4 (the system block one, the rules; the
  system block two, the catalogue, with `cache_control`); a refusal → `LlmUnavailable`
  with the page's sentence and a log line with the category; the answer validated by a
  Pydantic model of `{riassunto, luogo, team: [{id, ruolo, motivazione, giorni_settimana}]}`;
  ids mapped back, unknown or repeated ones dropped and logged; the bands from each
  freelancer's rate; the row written with the tokens; `tracker.team_event("team_proposta_generata",
  {"origine", "persone", "input_tokens", "output_tokens"})`; the read answered with
  `luogo` stripped from every `scheda` when `origine == "pubblico"`.

- [ ] **Step 1: Failing tests**: `test_bands_at_every_boundary`, `test_team_bands_with_a_missing_rate`, `test_catalogue_is_stable_and_anonymous` (no name, no link, sorted, the map), `test_engine_maps_the_answer_to_freelancers`, `test_engine_drops_unknown_and_repeated_ids`, `test_engine_refuses_when_off`, `test_engine_turns_a_refusal_into_unavailable`, `test_regenerate_carries_the_previous_team_and_note`, `test_public_proposal_hides_luogo`, `test_proposal_row_keeps_the_tokens`.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): a project description becomes an anonymous team with a price band` (`REB-511.`).

### Task C5: The request, and the routes

**Files:**
- Create: `packages/core/src/rebase_core/team_requests.py`
- Modify: `team_schemas.py` (`TeamRequestCreate`, `TeamRequestRead`, `TeamRequestListItem`, `TeamRequestList`, `TeamRequestTalentRead`, `StatusChange` reuse), `mail.py` (`team_request_mail(to, *, azienda, riassunto, url)`)
- Create: `apps/api/src/rebase_api/routers/team.py` (public: `POST /api/hub/team/proposals`, `POST /api/hub/team/requests`; admin: `GET /api/hub/team/requests?stato&origine&limit&offset`, `GET /api/hub/team/requests/{id}`, `POST /api/hub/team/requests/{id}/status`, `PATCH /api/hub/team/requests/{id}/note`); `main.py` lists it; `deps.py` (`TeamBuilderDep`)
- Test: `packages/core/tests/test_team_requests.py`, `apps/api/tests/test_team_api.py`

**Interfaces:**

```python
class TeamRequestCreate(BaseModel):
    proposal_id: UUID
    azienda: SafeStr = Field(min_length=1, max_length=200)
    email: EmailStr
    telefono: SafeStr = Field(min_length=6, max_length=40)

class TeamRequestTalentRead(BaseModel):
    freelancer_id: UUID
    nome: str
    cognome: str
    ruolo: str
    tariffa_giornaliera: Decimal | None
    fascia: Band | None
    mail_sent_at: datetime | None
    risposta: str | None
    risposta_at: datetime | None

class TeamRequestRead(BaseModel):
    id: UUID
    proposal: TeamProposalRead   # the admin's: luogo kept
    descrizione: str
    origine: str
    azienda: str
    email: str
    telefono: str
    user_id: UUID | None
    company_id: UUID | None
    stato: str
    note: str | None
    talenti: list[TeamRequestTalentRead]
    contacted_at: datetime | None
    closed_at: datetime | None
    created_at: datetime

class TeamRequestService:
    def __init__(self, session: Session, *, sender: EmailSender | None = None, settings: Settings, tracker: Tracker | None = None, now=utcnow) -> None: ...
    def create(self, data: TeamRequestCreate, *, origine: str, user_id: UUID | None, company_id: UUID | None) -> TeamRequestRead: ...
    def list_recent(self, *, stato: str | None, origine: str | None, limit: int, offset: int) -> TeamRequestList: ...
    def get(self, request_id: UUID) -> TeamRequestRead: ...
    def set_status(self, request_id: UUID, stato: str, admin_id: UUID) -> TeamRequestRead: ...
    def set_note(self, request_id: UUID, note: str | None, admin_id: UUID) -> TeamRequestRead: ...
```

  `create`: a proposal already requested → `Conflict`; older than a day → `ValidationFailed`;
  one `TeamRequestTalent` per member of the proposal's team; the mail to
  `settings.contracts_mail` («Nuova richiesta team da Acme S.r.l.», the summary, the
  link `{hub_url}/admin/team/{id}`); the summary checked against the `azienda` words
  (each word of three letters or more, case-insensitive) and a hit logged at warning;
  `tracker.team_event("team_richiesta_inviata", {"origine"})`. Public routes: the
  builder's `503`/`422`/`502` mapping of spec § 3.2 (`InvalidState` from `propose` → `503`
  here, deliberately, since it is the installation's state; `LlmUnavailable` → `502`);
  `client_hash = sha256(client_key(request))`. Admin routes behind `AdminDep`.

- [ ] **Step 1: Failing tests**: `test_request_from_a_proposal_files_the_talents_and_mails`, `test_request_refuses_a_second_time_and_an_old_proposal`, `test_request_logs_a_summary_that_names_the_company`, `test_list_filters_and_counts`, `test_status_and_note_record_the_admin`; the API: `test_public_proposal_answers_the_team`, `test_public_proposal_is_503_when_off`, `test_public_proposal_is_502_when_claude_is_down`, `test_public_request_is_201`, `test_admin_routes_need_an_admin`.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(hub): a proposal becomes a team request the admin reads over the API` (`REB-512.`).

### Task C6: The public page

**Files:**
- Create: `apps/web/src/pages/Team.tsx`, `Team.test.tsx`, `apps/web/src/lib/bands.ts` (the label), `bands.test.ts`
- Modify: `apps/web/src/lib/api.ts` (`team.propose`, `team.request`, the types), `router.tsx` (`/team` under `publicLayout`), `pages/Chooser.tsx` (one line linking «Cerca un team» to `/team`, beside the two wizard entries)
- Test: beside each

**The page (spec § 3.1):** heading «Descrivi il progetto, ti proponiamo il team», three
example buttons (a web app for a fintech, a data pipeline on site in Milan, a mobile app
with a designer), the `Textarea`, «Proponi il team» with the pending label «Sto leggendo
i profili…», the result (summary, a `Card` per member with role, reason, `sintesi`,
seniority, years, skills as `Badge`s, mode, the band), the team's bands per day and per
month with «22 giorni al mese», the note `Input` and «Rigenera», «Assumi team» opening
the form (three `Input`s, the wizard's own validation words), «Invia la richiesta», the
thanks paragraph; the error paragraphs for `503`, `502`, `422`; the beta box with the
button to `/companies?origine=team-builder`. Loading and errors as the wizards do them.

- [ ] **Step 1: Failing tests**: the examples fill the box; the run calls `team.propose` with the description; the result renders bands and cards without a name; «Rigenera» sends `previous_id` and the note; the form sends the three fields and shows the thanks; each error paragraph; the beta link.
- [ ] **Step 2: fail. Step 3: implement. Step 4: `pnpm --filter hub lint && test && build`.**
- [ ] **Step 5: Commit** `feat(hub): the public team page proposes a team and files the request` (`REB-513.`).

### Task C7: «Richieste team» and the talent's card in the admin

**Files:**
- Create: `apps/web/src/pages/admin/RichiesteTeam.tsx`, `RichiestaTeam.tsx`, tests
- Modify: `router.tsx` (`/admin/team`, `/admin/team/$id`), `components/SidebarNav.tsx` («Richieste team» between «Match» and «Aziende», icon `Users`), `lib/api.ts`, `lib/format.ts` (`TEAM_REQUEST_STATE_LABELS`: «Nuova», «Contattata», «Chiusa»; `TEAM_ORIGIN_LABELS`: «Pubblico», «Cloud»), the admin talent detail page (`AdminFreelancerDetail`: a «Scheda anonima» section with the card's fields, `generated_at`, `error`, and «Rigenera scheda» calling `POST /card`)
- Test: beside each

**The screens (spec § 3.5):** the list with the state filter pills of `lists.tsx`'s
shape (company, origin, date, state, «N sì su M»); the page with the description, the
summary, the place, the team table (name linking to `/admin/freelance/$id`, role, the
freelancer's rate, the band, the answer column empty for now), the contacts, the state
with «Segna come contattata» and «Chiudi», the note `Textarea` with «Salva la nota».
«Contatta i talenti» is Task D1's and does not appear here.

- [ ] **Step 1: Failing tests. Step 2: fail. Step 3: implement. Step 4: lint, test, build.**
- [ ] **Step 5: Commit** `feat(hub): the admin reads team requests and each talent's anonymous card` (`REB-514.`).

### Task C8: The privacy paragraph

**Files:**
- Modify: `projects/website/src/privacy.html` (a paragraph after the PostHog one, and the sentence «Non trasferiamo nulla a terzi» amended to name the two processors), `projects/website/src/landing-pages.test.ts` if it pins the page's sections
- Test: `pnpm --filter website test`

The paragraph, for Ivan's review on the PR: since September 2026 the text of a
freelancer's CV is read once by Anthropic's API to write an anonymous description of
the profile (role, seniority, skills), and the project description a visitor types on
the team builder is sent to the same API to propose a team; Anthropic processes the
data on rebase's behalf and does not train on it; a link to Anthropic's privacy policy;
how to ask for the description to be deleted (write to ciao@).

- [ ] **Step 1:** Write it, run the website tests, `pnpm --filter website build`.
- [ ] **Step 2: Commit** `docs(website): the privacy page says what the team builder sends to Anthropic` (`REB-515.`), and a comment on the card asking Ivan to read the wording.

### Task C9: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main`; every suite green in the foreground.
- [ ] **Step 2:** On the preview: `REBASE_ANTHROPIC_API_KEY` in the preview hub's `.env` (Ivan holds the key: ask him for it on the card and wait), restart `api`, `rebase cards-refresh` in the container until it prints 0, open `/hub/team`, describe a project, read the team, regenerate, file a request with a test address, read it in «Richieste team».
- [ ] **Step 3:** The pairs (the public page, the chooser line, «Richieste team», the request page, the talent's card) and the video, with the monorepo's `docs/pr-screenshots/record.mjs`.
- [ ] **Step 4:** `gh pr ready`, the fresh reviewer, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comments on C1 to C9, each moved to `Done`; a project update on P-REB-43.
- [ ] **Step 5:** Tell Ivan production needs the key before the `hub-v*` tag, and that `rebase cards-refresh` runs once after it.

---

## Milestone D: Work a team request with the talents, and open the cloud to a company

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-talent-cloud ../pigrocrm-talent-cloud origin/ivansala/milestone-team-builder`
(C's tip, once C5 is on it), the installs, the draft PR with D1 to D5; `gh pr create
--base ivansala/milestone-team-builder` so the diff shows D alone, and GitHub retargets
it to `main` when C merges.

### Task D1: The availability mail and the answer

**Files:**
- Modify: `packages/core/src/rebase_core/team_requests.py` (`contact_talents(request_id, admin_id, *, only_silent=False) -> TeamRequestRead`, `answer(raw_token, risposta) -> str` answering `si`, `no` or `invalid`), `mail.py` (`team_availability_mail(to, *, nome, ruolo, riassunto, tariffa, yes_url, no_url)` with a second button style, the site's green for yes, `WATERMELON` for no), `team_schemas.py`
- Modify: `apps/api/src/rebase_api/routers/team.py` (`POST /api/hub/team/requests/{id}/contact?only_silent=` admin; `GET /api/hub/team/availability?t=&r=` public, redirecting to `{hub_url}/team/risposta?esito=si|no|invalid`)
- Create: `apps/web/src/pages/TeamRisposta.tsx` (public, the three sentences of spec § 3.2), `router.tsx` (`/team/risposta`)
- Modify: `apps/web/src/pages/admin/RichiestaTeam.tsx` («Contatta i talenti», then «Rimanda a chi non ha risposto»; the answer column with «Sì» / «No» / «In attesa» and the time)
- Test: `test_team_requests.py`, `test_team_api.py`, the web tests

**Interfaces:** `contact_talents`: for each talent (all, or those with `risposta` NULL when
`only_silent`), a fresh `token_urlsafe(32)`, its SHA-256 stored, the mail through the
sender with `yes_url = f"{settings.hub_url.replace('/hub', '')}/api/hub/team/availability?t={raw}&r=si"`
(the API's public origin: read how `magic_link_mail`'s links are built and use the same
base), `mail_sent_at` on acceptance; the request's `stato` becomes `contattata` and
`contacted_at` is set on the first send. `answer`: the hash looked up; unknown, already
answered, or `mail_sent_at` older than thirty days → `invalid`; else `risposta`,
`risposta_at`, `tracker.team_event("team_talento_risposta", {"risposta"})`.

- [ ] **Step 1: Failing tests**: `test_contact_mails_every_talent_once`, `test_contact_only_silent`, `test_availability_records_yes_and_no`, `test_availability_token_is_one_use_and_expires`, the routes, the answer page's three sentences, the request page's buttons and column.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy; lint, test, build.**
- [ ] **Step 5: Commit** `feat(hub): each talent of a request answers with one click` (`REB-517.`), a comment on the card asking Ivan to read the mail's wording.

### Task D2: Vetted, and the cloud's door

**Files:**
- Modify: `packages/core/src/rebase_core/freelancers.py` (`set_vetted(freelancer_id, vetted: bool, admin_id) -> FreelancerRead`, an `AdminAction` of kind `vetted`), `talenti.py` and `schemas.py` (`vetted_at`, `ha_scheda_anonima` on the talent reads), `team_requests.py` or new `cloud.py` (`TalentCloudService`: `grant(company_id, admin_id)`, `revoke(company_id, admin_id)`, `list()`, `for_user(user_id) -> TalentCloudGrant | None`), `mail.py` (`talent_cloud_opened_mail(to, *, nome, azienda, url)`), `members.py` (`MeRead.talent_cloud: bool`)
- Modify: `apps/api/src/rebase_api/routers/freelancers.py` (`POST /api/hub/freelancers/{id}/vetted` `{vetted: bool}`), `routers/companies.py` (`POST /api/hub/companies/{id}/cloud`, `DELETE /api/hub/companies/{id}/cloud`, `GET /api/hub/cloud/grants`), `routers/members.py` (`talent_cloud` on `/me`)
- Modify: `apps/web/src/pages/admin/lists.tsx` («Segna come verificato» / «Togli la verifica» on the talent row's menu, a «Verificato» pill; «Apri il talent cloud» / «Revoca il talent cloud» on the company row's menu; «da team builder» on a company row whose `origine` is `team-builder`), `pages/CompanyWizard.tsx` (the box when `resolveAttribution(search).origine === 'team-builder'`: «Stai chiedendo l'accesso al talent cloud: compila la richiesta e ti ricontattiamo noi.»), the talent detail page (the vetted state)
- Test: core, API and web tests beside each

- [ ] **Step 1: Failing tests**: `test_set_vetted_records_the_admin`, `test_grant_is_one_live_per_user_and_mails`, `test_revoke_closes_it`, `test_me_carries_talent_cloud`, the routes, the row actions, the wizard's box, «da team builder».
- [ ] **Step 2: fail. Step 3: implement. Step 4: green everywhere.**
- [ ] **Step 5: Commit** `feat(hub): an admin marks a talent vetted and opens the talent cloud to a company` (`REB-518.`).

### Task D3: The cloud

**Files:**
- Modify: `packages/core/src/rebase_core/cloud.py` (`CloudTalentService.list(query) -> CloudTalentPage` with filters `ruolo`, `seniority`, `competenza` (ILIKE over the card's `competenze`), `modalita`, `fascia_min`, `fascia_max`, cursor-paginated with `rebase_core.pagination`, vetted first then name; `cv(freelancer_id)`), `team_schemas.py` (`CloudTalentRead`: `freelancer_id`, `nome`, `cognome`, `links`, `vetted`, `card` with `luogo` stripped, `fascia`, `ha_cv`)
- Create: `apps/api/src/rebase_api/routers/cloud.py` (`GET /api/hub/me/cloud/talents`, `GET /api/hub/me/cloud/talents/{id}/cv`, `POST /api/hub/me/cloud/proposals`, `POST /api/hub/me/cloud/requests` with `{proposal_id}` or `{freelancer_id}`), `deps.py` (`CloudDep`: `MeDep` plus a live grant, else `403` «Il talent cloud non è aperto per questo account.»)
- Create: `apps/web/src/pages/member/Cloud.tsx`, `Cloud.test.tsx`; `router.tsx` (`/me/cloud`); `components/SidebarNav.tsx` («Talent cloud» when `me.talent_cloud`); `lib/api.ts`
- Test: `packages/core/tests/test_cloud.py`, `apps/api/tests/test_cloud_api.py`, the web tests

**The page (spec § 4.2):** the builder box on top (the same component as the public
page, in a mode with no contact form: «Assumi team» files at once), then the filters
(`Select`s and one `Input`), the cards with name, surname, the links, «Apri il CV»,
`sintesi`, the badges, «Verificato da rebase», the band, «Richiedi»; a thanks toast on a
request. Never the rate, the email, the phone, the notes, the state.

- [ ] **Step 1: Failing tests**: the filters and the order, the guard, the CV route, a request from a card and from the builder (`origine = cloud`, `user_id`, `company_id`), and that the read carries no rate, email, phone, note or state.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green everywhere.**
- [ ] **Step 5: Commit** `feat(hub): an admitted company browses the talents by name and asks for them` (`REB-519.`).

### Task D4: Over MCP

**Files:**
- Modify: `apps/mcp/src/rebase_mcp/server.py` (the tools of spec § 8, each calling the core service with the admin behind the token; `build_server` takes an `llm: LlmCall | None` and the settings the way it takes `signing`), `__main__.py`, `http.py`; `talenti` tools carry `vetted_at` and `ha_scheda_anonima`
- Test: `apps/mcp/tests/test_tools.py`

- [ ] **Step 1: Failing tests** (one per tool, a `RecordingCall` for `propose_team` and `regenerate_freelancer_card`). **Step 2: fail. Step 3: implement**, descriptions in Italian. **Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(mcp): an admin works team requests, cards, the vetted flag and the cloud over MCP` (`REB-520.`).

### Task D5: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main` once C has merged; every suite green.
- [ ] **Step 2:** On the preview: a request's «Contatta i talenti» to a test talent of ours, both buttons clicked, the answers read; a talent marked vetted; a grant to a test company; the cloud page with a filter, a card's «Richiedi», the builder inside; the wizard's box at `/companies?origine=team-builder`.
- [ ] **Step 3:** The pairs and the video.
- [ ] **Step 4:** `gh pr ready`, the fresh reviewer, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comments on D1 to D5; a project update on P-REB-43.

## Self-review (done while writing)

- **Spec coverage.** § 2 C2; § 2.1 C2, C3; § 3.1 C6; § 3.2 C5, D1; § 3.3 C4; § 3.4 C4;
  § 3.5 C5, C7, D1; § 3.6 D1; § 4.1 D2; § 4.2 D3; § 4.3 C6, D2; § 5 C1; § 5.1 C3; § 6
  C1, C8, C9; § 7 the tests named per task; § 8 D4; § 9 the two milestones.
- **Placeholders.** The `anthropic` pin is «the current release on PyPI» at C1's run,
  the one true unknown; every other value is written.
- **Type consistency.** `LlmCall`, `LlmRequest`, `LlmResponse`, `LlmUnavailable` (C1) are
  what C3, C4 and D4 consume; `Card` (C2) is what C3 writes, C4 reads and D3 strips;
  `Band`, `band_for`, `team_bands` (C4) are what C5, C7, D3 render; `TeamRequestService`
  (C5) is what D1 and D4 extend; `TalentCloudService` (D2) is what D3's `CloudDep` reads.
- **Review Focus.** Each of the five has its test in C3, C4, C5 or D1, named above.
