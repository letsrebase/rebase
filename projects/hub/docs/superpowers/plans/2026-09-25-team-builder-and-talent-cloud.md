# The team builder and the talent cloud. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A visitor describes a project on a public hub page and gets an anonymous team
with a price band; «Assumi team» files a request the admin works with the talents by a
two-button mail; a company rebase admits browses the same talents by name in a private
cloud, with the builder inside.

**Architecture:** Everything in `projects/hub`. A Claude seam of the hub's own
(`rebase_core/llm.py`, the official `anthropic` SDK behind an `LlmCall` protocol with a
recording fake beside it) serves two callers: the card writer, which turns a CV into an
anonymous card stored on the freelancer, and the team builder, which sends a description
and the cached catalogue of cards to `claude-opus-5` with a JSON schema and maps the
answer back to freelancers by position. Requests, proposals, the talents' answers and
the cloud grants are new tables; the public routes take no login, the admin routes
`AdminDep`, the cloud routes a live grant. Two milestones, two draft PRs, the second
branched from the first's tip.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI, Pydantic 2, the `anthropic`
SDK, pytest with testcontainers Postgres; React 19, TanStack Router and Query,
`@rebase/ui`, Vitest.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-team-builder-and-talent-cloud-design.md`
(REB-506), amended on 2026-09-25 after the independent review of the text against the
code; the amendments are folded in below. Section numbers (§) are the spec's.

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
  EXISTS`, the `pg_constraint` guard for a `CHECK`, `CREATE UNIQUE INDEX IF NOT
  EXISTS`), and `test_migrations.py`'s `compare_metadata` stays `[]`. **The number**:
  `0020` is on the campaigns branch and `0021` on the hours-report branch, both
  revising `0019`, so this one is written as `0022` revising `0019` today, and C9 and
  D5 re-point `down_revision` at `main`'s head and rename the file to the next free
  number after `git merge origin/main`, before `gh pr ready`: three heads make
  `alembic upgrade head` refuse and the API restart in a loop.
- Every new `REBASE_*` setting takes three edits: `config.py`, `.env.example`, the
  `x-api-environment` anchor of `docker-compose.yml` (a bool's compose default is
  `:-true`, as the file already writes them).
- Errors: every failure a route answers is a `DomainError` mapped in
  `apps/api/src/rebase_api/main.py::domain_error_handler` (`NotFound` 404,
  `ValidationFailed` 422, `InvalidState` 409, and the two this plan adds:
  `TeamBuilderOff` 503, `LlmUnavailable` 502). The base class takes one message and
  keyword details (`InvalidState("…", stato=…)`), never two positional strings. There
  is no `Conflict` class: a duplicate is `InvalidState` with its sentence.
- Admin routes live in `routers/admin.py` (or a new admin router registered beside
  it), never in `routers/freelancers.py` or `routers/companies.py`, which hold the
  public wizard writes.
- Work after the response never borrows the request's session: a `BackgroundTasks`
  step opens its own through `SessionOpenerDep` (`deps.py`), as the Documenso
  webhook's `_finish` does.
- Lists are cursor-paginated with `rebase_core.pagination` (`limit` and `cursor`),
  never `offset`; a list this plan caps instead says so.
- The words for states, origins and answers live in core (beside `match_words.py`)
  and the web copies them; `packages/core/tests/test_web_labels.py` holds the copy
  equal, so every label lands in core and in `format.ts` in the same task.
- The wizard reads its origin from `?da=`, never `?origine=`
  (`apps/web/src/lib/utm.ts::readOrigin`).
- Claude, as the `claude-api` skill fixes it on 2026-09-25: model `claude-opus-5` (a
  setting, default), the official `anthropic` SDK, `client.beta.messages.create` with
  `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`, `thinking={"type":
  "adaptive"}`, `output_config={"effort": "medium", "format": {"type": "json_schema",
  "schema": ...}}` (`additionalProperties: false`, every property `required`, a nullable
  one as `["string", "null"]`), `inference_geo="eu"` as a top-level keyword, a request
  timeout of forty seconds per attempt with one retry (the client's own backoff between
  the two keeps the worst case under the host vhost's ninety seconds), the catalogue as a `system` block with `cache_control:
  {"type": "ephemeral"}`, `max_tokens` 8000 for a proposal and 2000 for a card,
  `stop_reason` read before `content` (`refusal`, `max_tokens`), the first `text` block
  parsed with `json.loads` and validated by a Pydantic model, any failure of those a
  `LlmUnavailable`. Never `budget_tokens`, never a prefill, never `tool_choice`.
- Nothing logs a CV's text, a description, an address or a key; a proposal row keeps
  the tokens, not the prompt.
- Money is `Decimal`; the bands are integers of euro.
- Local checks: `uv run ruff check projects/hub`, `uv run mypy
  projects/hub/packages/core/src projects/hub/apps/api/src projects/hub/apps/mcp/src`,
  the pytest suites in the foreground one at a time, `pnpm --filter hub lint`, `test`,
  `build`. Never ports 55432 or 55433. `df -h /` before an image build.

## Review Focus

1. **A position the model invents or repeats.** The engine drops a position the
   catalogue does not hold, dedupes a person named twice, logs both, and never answers a
   freelancer the catalogue did not offer; two freelancers signed up within a minute
   (adjacent UUIDv7 prefixes) are two positions (Task C4,
   `test_engine_drops_unknown_and_repeated_positions`, `test_catalogue_positions_are_unique`).
2. **A summary that names the company.** «Contatta i talenti» refuses it with a
   sentence; the admin edits the summary and sends (Task D1,
   `test_contact_refuses_a_summary_that_names_the_company`).
3. **Bands at the boundaries and without a rate.** 299, 300, 499, 500, 800, no rate;
   the team's band with one person without a rate says so (Task C4,
   `test_bands_at_every_boundary`).
4. **A CV that changes, one that fails, one that goes.** The card is rewritten only when
   the SHA-256 differs; a failed CV is not retried until it changes; a scanned CV with
   no text makes no call; `clear_cv` deletes the card (Task C3,
   `test_card_follows_the_cv`, `test_failed_cv_is_not_retried`).
5. **The two buttons, twice, and a scanner.** A GET on the answer page records nothing;
   the confirm post answers once; the second post, either answer, is `invalid`; a token
   older than thirty days is `invalid`; a double click on «Assumi team» files one request
   (Task D1, `test_availability_token_is_one_use_and_expires`; Task C5,
   `test_request_is_unique_per_proposal`).

---

## Card groups and order

Milestone C is on the draft PR of branch `ivansala/milestone-team-builder` (from
`origin/main`); milestone D on `ivansala/milestone-talent-cloud`, created from C's tip
once C1 to C5 are on it (its tables and services), and merged after C (GitHub retargets
D's PR at `main` only when C's branch is deleted after the merge, so delete it).

| Task | Card | Title | Depends on |
|---|---|---|---|
| C1 | REB-508 | Add the Claude seam and its settings to the hub | none |
| C2 | REB-509 | Store cards, proposals, requests and grants | none |
| C3 | REB-510 | Write an anonymous card for every freelancer with a CV | C1, C2 |
| C4 | REB-511 | Propose a team from a description with a price band | C3 |
| C5 | REB-512 | File a team request and serve it to the admin over the API | C4 |
| C6 | REB-513 | Build the public team page | C5 |
| C7 | REB-514 | Show «Richieste team» and the talent's card in the admin | C5 |
| C8 | REB-515 | Say on the privacy page what goes to Anthropic and who sees a profile | none |
| C9 | REB-516 | Prove a public proposal on the preview and land the milestone | C6, C7, C8 |
| D1 | REB-517 | Ask each talent with a two-button mail and record the answer | C5 |
| D2 | REB-518 | Mark a talent vetted and open the cloud to a company | C5 |
| D3 | REB-519 | Let an admitted company browse the talents by name and ask | D2 |
| D4 | REB-520 | Give the hub MCP server the team, card, vetted and cloud actions | D1, D2, D3 |
| D5 | REB-521 | Prove the cloud on the preview and land the milestone | D4 |

## File structure

- `packages/core/src/rebase_core/config.py`: `anthropic_api_key`, `team_builder_model`,
  `team_builder_enabled`, `team_builder_concurrency`, `team_builder_daily_cap`;
  `.env.example`, `docker-compose.yml`.
- `packages/core/pyproject.toml`: `anthropic` pinned; the root `uv.lock` updated.
- `packages/core/src/rebase_core/errors.py`: `LlmUnavailable`, `TeamBuilderOff`;
  `apps/api/src/rebase_api/main.py`: their status codes.
- `packages/core/src/rebase_core/llm.py`: `LlmRequest`, `LlmResponse`, `LlmCall`,
  `AnthropicCall`, `RecordingCall`, `call_from_settings`.
- `packages/core/migrations/versions/0022_team_builder.py`; `models.py`: `FreelancerCard`,
  `TeamProposal`, `TeamRequest`, `TeamRequestTalent`, `TalentCloudGrant`,
  `Freelancer.vetted_at`, `vetted_by`; `ADMIN_ACTION_KINDS` gains `vetted`.
- `packages/core/src/rebase_core/team_schemas.py`: every read and write model of the
  feature; `team_words.py`: the labels (states, origins, answers) the web copies.
- `packages/core/src/rebase_core/cards.py`: `CardWriter`, `card_prompt`, `CARD_SCHEMA`.
- `packages/core/src/rebase_core/bands.py`: `band_for`, `team_bands`, `Band`.
- `packages/core/src/rebase_core/team_builder.py`: `TeamBuilder`, `catalogue_lines`,
  `PROPOSAL_SCHEMA`, `proposal_prompt`, `cloud_visible`, `NO_CALL_MODEL`.
- `packages/core/src/rebase_core/team_caps.py`: `require_daily_room`, `proposals_today`,
  `BUSY_SENTENCE`, `CAPPED_ORIGINS`.
- `packages/core/src/rebase_core/team_requests.py`: `TeamRequestService`, the tokens,
  the answers; `cloud.py`: `TalentCloudService` (grants) and `CloudTalentService` (the
  list, the CV).
- `packages/core/src/rebase_core/mail.py`: `team_request_mail`, `team_availability_mail`,
  `talent_cloud_opened_mail`; `_button` gains a colour.
- `packages/core/src/rebase_core/analytics.py`: `Tracker.team_event`.
- `packages/core/src/rebase_core/cli.py`: `cards-refresh`.
- `packages/core/src/rebase_core/freelancers.py`: `apply` and `clear_cv` touch the card;
  `set_vetted`; `members.py`: `replace_cv` and `MeRead.talent_cloud`.
- `apps/api/src/rebase_api/deps.py`: `LlmDep`, `TeamBuilderDep`, `CloudDep`;
  `routers/team.py` (public), `routers/admin_team.py` (admin, registered beside
  `admin.py`), `routers/cloud.py` (member); `routers/freelancers.py` and `members.py`
  schedule the card write; `routers/admin.py` gets the card, vetted and grant routes.
- `apps/mcp/src/rebase_mcp/server.py`: the tools of § 8.
- `apps/web/src/components/TeamBuilder.tsx` (the box, the result, «Rigenera», the two
  modes: public with the form, cloud without), `pages/Team.tsx`, `TeamRisposta.tsx`
  (public); `pages/admin/RichiesteTeam.tsx`, `RichiestaTeam.tsx`; `pages/member/Cloud.tsx`;
  `lib/api.ts`, `lib/format.ts`, `lib/bands.ts`; `router.tsx`; `components/SidebarNav.tsx`;
  `pages/CompanyWizard.tsx` (the origin box); `pages/admin/lists.tsx` (vetted action,
  «da team builder»); the admin talent page (the card, «Rigenera scheda»);
  `AdminCompanyDetail` (the grant action).
- `projects/website/src/privacy.html`: the new section; `landing-pages.test.ts`: the
  Anthropic host in the allowlist.
- Root `docs/design/DECISIONS.md`: two rows, in the design record's PR.
- Tests beside each: `packages/core/tests/test_llm.py`, `test_cards.py`, `test_bands.py`,
  `test_team_builder.py`, `test_team_requests.py`, `test_cloud.py`, `test_migrations.py`,
  `test_web_labels.py`; `apps/api/tests/test_team_api.py`, `test_cloud_api.py`,
  `test_admin_api.py`, `test_hub_api.py`, `test_member_api.py`; `apps/mcp/tests/test_tools.py`;
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
- Modify: `packages/core/pyproject.toml` (`"anthropic==<the current release on PyPI, pinned>"`, with a comment naming the two callers), the root `uv.lock` (`uv lock`)
- Modify: `packages/core/src/rebase_core/config.py`, `projects/hub/.env.example`, `projects/hub/docker-compose.yml`
- Modify: `packages/core/src/rebase_core/errors.py` (`LlmUnavailable(DomainError)`), `apps/api/src/rebase_api/main.py` (mapped to 502)
- Create: `packages/core/src/rebase_core/llm.py`
- Test: `packages/core/tests/test_llm.py`, `apps/api/tests/test_hub_api.py` (the 502 mapping, one test)

**Interfaces:**

```python
class LlmRequest(BaseModel):
    system: list[dict[str, Any]]        # text blocks, the last may carry cache_control
    messages: list[dict[str, Any]]
    json_schema: dict[str, Any]          # the output_config.format schema; not `schema`, which shadows BaseModel.schema and warns at every boot
    max_tokens: int

class LlmResponse(BaseModel):
    text: str | None                     # the first text block, None on a refusal
    stop_reason: str                     # passed through: "end_turn", "max_tokens", "refusal", ...
    refusal_category: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int

class LlmUnavailable(DomainError):       # errors.py; the API answers 502
    """«Non riesco a proporre un team adesso: riprova tra poco.»"""

class LlmCall(Protocol):
    def complete(self, request: LlmRequest) -> LlmResponse: ...

class AnthropicCall:
    def __init__(self, api_key: str, model: str, *, client: Any | None = None) -> None: ...  # anthropic.Anthropic(api_key=..., max_retries=1, timeout=40.0) by default, built on first use
    def complete(self, request: LlmRequest) -> LlmResponse: ...

class RecordingCall:
    def __init__(self, responses: list[LlmResponse]) -> None: ...   # answers them in order, keeps .requests
    def complete(self, request: LlmRequest) -> LlmResponse: ...

def call_from_settings(settings: Settings) -> LlmCall | None:
    """None without a key: the callers then refuse with their own sentence."""
```

  `complete` passes `request.json_schema` through the SDK's own `anthropic.transform_schema`
  first: it strips the length, range and list-size keywords the structured-output API
  refuses (`maxLength`, `minimum`, `maxItems`, ...), moving them into each property's
  description and closing every object, `$defs` included, since both `Card` and the
  proposal schema carry `Field` limits the API would otherwise reject; the same Pydantic
  model still enforces those limits when the answer is validated. It then calls
  `self.client.beta.messages.create(model=self.model,
  max_tokens=request.max_tokens, betas=["server-side-fallback-2026-07-01"],
  fallbacks="default", thinking={"type": "adaptive"}, output_config={"effort":
  "medium", "format": {"type": "json_schema", "schema": <the transformed schema>}},
  inference_geo="eu", system=request.system, messages=request.messages)`, reads
  `stop_reason`, `stop_details.category` when the reason is `refusal`, the first `text`
  block otherwise (also on `max_tokens`), and `usage.input_tokens +
  (usage.cache_creation_input_tokens or 0)` as `input_tokens` (a cache write is paid for
  too, so the row says what was spent), `usage.output_tokens`,
  `usage.cache_read_input_tokens or 0`. `anthropic.RateLimitError`,
  `anthropic.APIStatusError`, `anthropic.APIConnectionError` (most specific first)
  become `LlmUnavailable`, logged with the class name and status and never the request.
  Settings: `anthropic_api_key: str = Field(default="", repr=False)`,
  `team_builder_model: str = "claude-opus-5"`, `team_builder_enabled: bool = True`,
  `team_builder_concurrency: int = 4`, `team_builder_daily_cap: int = 300`, each with a comment in the file's voice. The tests
  build `AnthropicCall` over a stub `client` (a class with `beta.messages.create`
  recording the kwargs and answering a canned message with `content`, `stop_reason`,
  `stop_details`, `usage`, `model`).

- [ ] **Step 1: Failing tests** (`test_complete_sends_the_documented_request` (every kwarg above, `inference_geo` included, the schema sent through `transform_schema`), `test_refusal_answers_no_text`, `test_request_carries_eu_geo_and_leaves_the_timeout_to_the_client`, `test_input_tokens_count_the_cache_writes`, `test_the_schema_sent_carries_only_what_the_api_takes` (a model with a `Field` limit that would otherwise reach the API unstripped), `test_the_sdk_client_is_built_on_first_use` (`max_retries=1`, `timeout=40.0`, and that one retry at that timeout, plus the SDK's own backoff, stays under ninety seconds), `test_provider_errors_are_domain_errors`, `test_recording_call_keeps_requests`, `test_settings_default`, the API's 502).
- [ ] **Step 2: fail. Step 3: implement** (`uv add --package rebase-core anthropic==<version>` or the manual pin plus `uv lock`; `uv sync --frozen` passes after). **Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): the hub speaks to Claude through a seam of its own` (`REB-508.`). The lock change goes in the same commit.

### Task C2: The tables

**Files:**
- Create: `packages/core/migrations/versions/0022_team_builder.py`
- Modify: `packages/core/src/rebase_core/models.py`, `errors.py` (`TeamBuilderOff(DomainError)`, «Il team builder è spento.»), `apps/api/src/rebase_api/main.py` (503)
- Create: `packages/core/src/rebase_core/team_schemas.py` (the `Card` model and the constants; the read models come with their tasks), `team_words.py` (the labels)
- Test: `packages/core/tests/test_migrations.py`, `test_web_labels.py` (the new maps, once `format.ts` copies them in C7 and D2: this task adds the core side and the test entries that C7 and D2 make pass; until then the entries are marked `xfail(strict=True)` with the card that removes the mark)

**Interfaces:** the tables of spec § 2, as SQLAlchemy models with these names and
columns:

```python
CARD_SENIORITIES = ("junior", "mid", "senior", "lead")
TEAM_REQUEST_STATES = ("nuova", "contattata", "chiusa")
TEAM_PROPOSAL_ORIGINS = ("pubblico", "cloud", "admin")
TEAM_REQUEST_ORIGINS = ("pubblico", "cloud")
TALENT_ANSWERS = ("si", "no")

class FreelancerCard(Base, TimestampMixin):          # freelancer_id PK (FK freelancers.id, ondelete CASCADE); cv_sha256, card JSONB nullable, model, input_tokens, output_tokens, generated_at nullable, error nullable, error_cv_sha256 nullable
class TeamProposal(Base, PrimaryKeyMixin):           # descrizione, nota, previous_id, riassunto, luogo JSONB, team JSONB, economia JSONB, model, input_tokens, output_tokens, cache_read_tokens, origine, user_id nullable, created_at
class TeamRequest(Base, PrimaryKeyMixin, TimestampMixin)   # proposal_id nullable, origine, azienda, email, telefono nullable, user_id nullable, company_id nullable, stato, note, contacted_at, closed_at
class TeamRequestTalent(Base, PrimaryKeyMixin)       # request_id, freelancer_id, ruolo, token_hash nullable unique, mail_sent_at, risposta, risposta_at; unique (request_id, freelancer_id)
class TalentCloudGrant(Base, PrimaryKeyMixin)        # user_id, company_id, granted_by, granted_at, revoked_by, revoked_at; partial unique index on (user_id, company_id) where revoked_at IS NULL
# Freelancer: vetted_at, vetted_by; ADMIN_ACTION_KINDS += ("vetted",)

class Card(BaseModel):                                # team_schemas.py, spec § 2.1; no modalita here
    model_config = ConfigDict(extra="forbid")
    ruolo: SafeStr = Field(min_length=1, max_length=120)
    seniority: Literal["junior", "mid", "senior", "lead"]
    anni: int = Field(ge=0, le=60)
    competenze: list[SafeStr] = Field(max_length=20)
    settori: list[SafeStr] = Field(max_length=10)
    lingue: list[SafeStr] = Field(max_length=8)
    luogo: SafeStr | None = Field(max_length=120)     # required, nullable: no default, so the schema keeps it in `required`
    sintesi: SafeStr = Field(min_length=1, max_length=400)

# team_words.py
TEAM_REQUEST_STATE_LABELS = {"nuova": "Nuova", "contattata": "Contattata", "chiusa": "Chiusa"}
TEAM_ORIGIN_LABELS = {"pubblico": "Pubblico", "cloud": "Cloud", "admin": "Admin"}
TALENT_ANSWER_LABELS = {"si": "Sì", "no": "No"}
```

  Checks as constraints (`ck_team_requests_stato`, `ck_team_proposals_origine`,
  `ck_team_requests_origine`, `ck_team_request_talents_risposta`), the partial unique
  index `uq_team_requests_proposal_id` on `proposal_id WHERE proposal_id IS NOT NULL`,
  indexes on `team_requests (stato, created_at)`, `team_proposals (created_at)`,
  `talent_cloud_grants (user_id)`; `ADMIN_ACTION_KINDS` gains `vetted` (read what
  enforces that list; if a `CHECK` does, the migration extends it through the
  `pg_constraint` guard).

- [ ] **Step 1:** `test_migrations.py` fails until the migration exists. **Step 2: implement** the models, the errors, the schemas and the words, then the migration in its own commit, in `0019`'s docstring style and with `IF NOT EXISTS` everywhere.
- [ ] **Step 3:** `test_migrations.py` green; ruff, mypy.
- [ ] **Step 4: Commits** `feat(core): the hub stores cards, team proposals, requests and cloud grants` and `feat(core): migration 0022 creates the team builder's tables` (`REB-509.`).

### Task C3: The card writer

**Files:**
- Create: `packages/core/src/rebase_core/cards.py`
- Modify: `packages/core/src/rebase_core/freelancers.py` (`clear_cv` calls `CardWriter.delete` in the same transaction), `cli.py` (`cards-refresh --limit N`), `team_schemas.py` (`FreelancerCardRead`, `CardsRefreshed`)
- Modify: `apps/api/src/rebase_api/deps.py` (`LlmDep = Annotated[LlmCall | None, Depends(get_llm)]`), `routers/freelancers.py` (`POST /api/hub/freelancers`, the wizard: after the response, a background task opens a session through `SessionOpenerDep` and runs `CardWriter.write`), `routers/members.py` (`PUT /me/cv`: the same), `routers/admin.py` (`GET /api/hub/freelancers/{id}/card`, `POST /api/hub/freelancers/{id}/card` to regenerate, ignoring the failed hash once)
- Test: `packages/core/tests/test_cards.py`, `test_cli.py`, `apps/api/tests/test_hub_api.py` (the wizard schedules the write), `test_member_api.py` (the CV replace does), `test_admin_api.py` (the two card routes, and `clear_cv` dropping the card)

**Interfaces:**

```python
CARD_MAX_TOKENS = 2000
NO_TEXT = "Il CV non ha testo leggibile."

Failure = Literal["no_text", "refusal", "max_tokens", "shape", "identifying", "unavailable"]

def _identifies(card: Card, cognome: str) -> bool:
    """Whether the card names the person: the surname as a whole word written with a
    capital (`\bSURNAME\b`, case-sensitive on the first letter so «Conti» in «i conti
    del cliente» is not the person) in `ruolo`, `sintesi`, `competenze` or `settori`
    (never `luogo` or `lingue`: Messina, Russo and Tedesco are a city and two
    languages the CV may legitimately carry), or `http://`, `https://`, `www.` or `@`
    anywhere (the bare word "HTTP" is a skill, not a link)."""

class FreelancerCardRead(BaseModel):
    freelancer_id: UUID
    card: Card | None
    modalita: str | None          # Freelancer.remoto, read now
    cv_sha256: str | None
    model: str | None
    generated_at: datetime | None
    error: str | None

class CardsRefreshed(NamedTuple):
    written: int
    failed: int

def card_prompt(cv: CvText, posizione: str | None) -> LlmRequest:
    """System: what an anonymous card is, the rules (no name, no employer, no link,
    Italian, two sentences of sintesi at most, `luogo` the city or region the CV names
    or null); user: the profile's position and the CV's text. Schema: `Card`'s JSON
    schema with additionalProperties false and every property required."""

class CardWriter:
    def __init__(self, session: Session, llm: LlmCall | None, *, now: Callable[[], datetime] = utcnow) -> None: ...
    def write(self, freelancer_id: UUID, *, force: bool = False) -> FreelancerCardRead: ...
    def refresh_stale(self, limit: int = 50) -> CardsRefreshed: ...
    def delete(self, freelancer_id: UUID) -> None: ...
    def read(self, freelancer_id: UUID) -> FreelancerCardRead: ...
```

  `write`: no CV → `delete` and answer an empty read; no `llm` → answer the current
  row untouched; the CV's hash equal to `cv_sha256` and not `force` → untouched; equal
  to `error_cv_sha256` and not `force` → untouched (a failed CV is not retried until it
  changes); the text through `FreelancerService.cv_text`, empty → `error = NO_TEXT`,
  `error_cv_sha256 = hash`, no call; else the prompt, `llm.complete`, and `stop_reason`
  `refusal` or `max_tokens`, a body that is not JSON or does not validate as `Card`, or a
  `Card` that validates but `_identifies` it as the person (the surname, a link, an
  email or `www.`) → the CV's own failure, one of these kinds, never the model's words:
  a card of another, older CV is retired with SQL `NULL` (never Python's `None`, which
  the JSONB column would still bind as the JSON value `null`, so `card IS NOT NULL`, the
  catalogue's filter, would still count it), and `error` and `error_cv_sha256` are
  written so the same broken CV is not sent and paid for again until it changes, or
  until «Rigenera scheda» (`force`) asks anyway; `LlmUnavailable` (a provider outage,
  not the CV's fault) → keep the previous card untouched, write `error` alone with no
  `error_cv_sha256`, so the next `write` or `cards-refresh` retries it, and stop the
  batch; a valid, non-identifying card → upsert `card`, `cv_sha256`, `model`, the tokens, `generated_at`, `error =
  None`, `error_cv_sha256 = None`. `refresh_stale`: every live freelancer with a CV whose
  hash differs from both `cv_sha256` and `error_cv_sha256`, `limit` at a time, oldest
  first; answers written and failed. `read` fills `modalita` from `Freelancer.remoto`.
  The CLI prints «N schede scritte, M non riuscite».

- [ ] **Step 1: Failing tests**: `test_card_from_a_cv` (a `RecordingCall` answering a canned card: the row, the hash, the tokens; `modalita` read from the profile), `test_card_follows_the_cv` (same hash → no call; new bytes → rewritten; `clear_cv` → row gone), `test_failed_cv_is_not_retried` (a refusal writes `error` and the hash; a second `write` makes no call; `force` does), `test_scanned_cv_makes_no_call`, `test_max_tokens_and_bad_json_are_errors`, `test_an_outage_on_a_new_cv_keeps_the_old_card` (`LlmUnavailable`: the previous card stays, no `error_cv_sha256`), `test_a_scan_replacing_a_cv_retires_its_card` (a failed new CV: `card` goes to SQL `NULL`, `card IS NOT NULL` no longer finds it), `test_the_same_cv_refused_on_regenerate_keeps_its_card`, `test_a_surname_that_is_a_word_is_the_person_only_with_a_capital`, `test_a_surname_inside_a_longer_word_is_not_the_person`, `test_a_surname_that_is_a_place_or_a_language_is_the_cvs_own` (`luogo`, `lingue` exempt), `test_http_as_a_skill_is_not_a_link`, `test_no_key_writes_nothing`, `test_refresh_stale_limits_and_counts`, `test_cards_refresh_prints_the_counts` (CLI); the API: the wizard and the CV replace schedule the write (assert on the recorded call after the response), the two admin routes, `clear_cv` drops the card.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): every freelancer with a CV gets an anonymous card` (`REB-510.`).

### Task C4: The bands and the engine

**Files:**
- Create: `packages/core/src/rebase_core/bands.py`, `team_builder.py`
- Modify: `team_schemas.py` (`Band`, `TeamMemberRead`, `TeamProposalRead`, `TeamProposalCreate`), `analytics.py` (`Tracker.team_event(name: str, properties: dict[str, Any]) -> bool`)
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
    posizione: int
    freelancer_id: UUID | None   # None on the public read
    ruolo: str
    motivazione: str
    giorni_settimana: int | None
    scheda: Card                 # `luogo` None on the public read
    modalita: str | None         # Freelancer.remoto
    fascia: Band | None

class TeamProposalRead(BaseModel):
    id: UUID
    riassunto: str
    luogo: dict[str, Any]        # {"locale": bool, "dove": str | None}
    team: list[TeamMemberRead]
    economia: dict[str, Any]     # {"giorno": Band | None, "mese": Band | None, "giorni_mese": 22}
    previous_id: UUID | None
    origine: str
    created_at: datetime

PROPOSAL_MAX_TOKENS = 8000
NO_CALL_MODEL = ""   # `TeamProposal.model` for a row that made no call (an empty catalogue): `team_caps` (C5) does not count it as a paid proposal.

def cloud_visible(stmt: Select) -> Select:
    """The one filter of who is in the catalogue and the cloud: Freelancer.deleted_at IS NULL,
    stato != 'scartato', a FreelancerCard row with a card. Shared with D3."""

def catalogue_lines(session: Session) -> tuple[str, list[UUID]]:
    """One JSON line per `cloud_visible` freelancer, sorted by Freelancer.id so the text
    is stable across requests (the cache prefix): {"id": "t1", "ruolo", "seniority",
    "anni", "competenze", "settori", "lingue", "luogo", "modalita", "fascia": <label or null>}
    where "t<n>" is the line's 1-based position; no `sintesi`; and the positions' ids in
    order. Positions never collide, unlike a slice of a UUIDv7."""

def proposal_prompt(catalogue: str, data: TeamProposalCreate, previous: TeamProposal | None) -> LlmRequest

class TeamBuilder:
    def __init__(self, session: Session, llm: LlmCall | None, settings: Settings, *, tracker: Tracker | None = None, now=utcnow) -> None: ...
    def propose(self, data: TeamProposalCreate, *, origine: str, user_id: UUID | None) -> TeamProposalRead: ...
    def get(self, proposal_id: UUID, *, public: bool) -> TeamProposalRead: ...
```

  `propose`: `settings.team_builder_enabled` false or `llm` None → `TeamBuilderOff`;
  `previous_id` must exist, be younger than a day, carry the caller's `origine` and, on
  the cloud, the caller's `user_id` (`ValidationFailed`); the prompt
  of § 3.4 (the system block one, the rules; the system block two, the catalogue, with
  `cache_control`); `stop_reason` `refusal` (logged with the category) or `max_tokens`,
  a body that is not JSON or does not validate → `LlmUnavailable`; the answer validated
  by a Pydantic model of `{riassunto, luogo, team: [{id, ruolo, motivazione,
  giorni_settimana}]}`; ids mapped back by position, unknown or repeated ones dropped
  and logged, and so is, when `luogo.locale` is true, a member whose `modalita` is
  `remoto` or unknown; the bands from each freelancer's rate; the row written with the tokens
  and `origine`; `tracker.team_event("team_proposta_generata", {"origine", "persone",
  "input_tokens", "output_tokens"})`; the read answered with `public = origine ==
  "pubblico"`, which drops `freelancer_id` and `luogo` from every member.

- [ ] **Step 1: Failing tests**: `test_bands_at_every_boundary`, `test_team_bands_with_a_missing_rate`, `test_catalogue_is_stable_and_anonymous` (no name, no link, no sintesi, sorted, the positions), `test_catalogue_positions_are_unique` (two freelancers created in the same second), `test_engine_maps_the_answer_to_freelancers`, `test_engine_drops_unknown_and_repeated_positions`, `test_engine_drops_remote_members_on_a_local_need` (`luogo.locale` true, a member whose `modalita` is `remoto` or unknown dropped and logged, the rest kept), `test_engine_refuses_when_off`, `test_engine_turns_a_refusal_and_max_tokens_and_bad_json_into_unavailable`, `test_previous_id_must_be_the_callers_and_younger_than_a_day` (another origin, another user on the cloud, a day old, unknown: the same `ValidationFailed`), `test_previous_id_of_the_same_cloud_user_within_the_day`, `test_regenerate_carries_the_previous_team_and_note`, `test_public_read_hides_ids_and_luogo`, `test_proposal_row_keeps_the_tokens_and_origin`.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(core): a project description becomes an anonymous team with a price band` (`REB-511.`).

### Task C5: The request, and the routes

**Files:**
- Create: `packages/core/src/rebase_core/team_requests.py`, `team_caps.py`
- Modify: `team_schemas.py` (`TeamRequestCreate`, `TeamRequestRead`, `TeamRequestListItem`, `TeamRequestList`, `TeamRequestTalentRead`), `mail.py` (`team_request_mail(to, *, azienda, riassunto: str | None, talento: str | None, url)`)
- Create: `apps/api/src/rebase_api/routers/team.py` (public: `POST /api/hub/team/proposals`, `POST /api/hub/team/requests` behind `spend_one`), `routers/admin_team.py` (admin: `GET /api/hub/team/requests?stato&origine&limit&cursor`, `GET /api/hub/team/requests/{id}`, `POST /api/hub/team/requests/{id}/status`, `PATCH /api/hub/team/requests/{id}/note`, `PATCH /api/hub/team/requests/{id}/summary`); `main.py` lists both; `deps.py` (`TeamBuilderDep`, and a module-level `threading.BoundedSemaphore(settings.team_builder_concurrency)` the proposal route acquires with `blocking=False`, answering `503` «Troppe richieste in questo momento: riprova tra un minuto.» when it cannot; once a slot is held, `team_caps.require_daily_room(session, settings)` answers the same `503` when the day's proposals of `pubblico` and `cloud` origin together reach `settings.team_builder_daily_cap`, so the cap holds whichever door a stranger comes through, D3's cloud route (§ 5) shares it)
- Test: `packages/core/tests/test_team_requests.py` (`team_caps.py`'s tests live beside it, not in a file of their own), `apps/api/tests/test_team_api.py`

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
    proposal: TeamProposalRead | None   # the admin's read: ids and luogo kept; None for a single-talent request
    riassunto: str | None               # the editable copy on the request's proposal
    descrizione: str | None
    origine: str
    azienda: str
    email: str
    telefono: str | None
    user_id: UUID | None
    company_id: UUID | None
    stato: str
    note: str | None
    talenti: list[TeamRequestTalentRead]
    contacted_at: datetime | None
    closed_at: datetime | None
    created_at: datetime

class TeamRequestListItem(BaseModel): ...   # id, azienda, origine, stato, created_at, contacted_at, talenti_totale, talenti_si
class TeamRequestList(BaseModel): ...       # items, next_cursor

def names_the_company(riassunto: str, azienda: str) -> bool:
    """The whole `azienda` (legal forms dropped), as a phrase between word boundaries,
    whatever its length, when one of its tokens is an initialism in capitals («HP» names
    HP, «chip» does not; «Di Più» in ordinary text names nobody), or any word of four
    letters or more of it that is neither a legal form
    (`srl`, `srls`, `spa`, `snc`, `sas`, with or without their dots) nor a generic word
    of a company's kind the prompt itself asks the summary to use instead (`logistica`,
    `software`, `studio`, `servizi` and their kind), case-insensitively, inside the
    summary as a whole word: «Acme S.r.l.» is named by «ACME rifà il gestionale», but
    «Logistica Veneta S.r.l.» is not named by «un'azienda di logistica». Tests: «HP»
    flagged for HP, «chip» not; «IBM Italia» flagged for «IBM Italia S.r.l.»."""

class TeamRequestService:
    def __init__(self, session: Session, *, settings: Settings, sender: EmailSender | None = None, tracker: Tracker | None = None, now=utcnow) -> None: ...
    def create(self, data: TeamRequestCreate, *, origine: str, user_id: UUID | None, company_id: UUID | None, telefono: str | None = None) -> TeamRequestRead: ...
    def create_for_talent(self, freelancer_id: UUID, *, azienda: str, email: str, telefono: str | None, user_id: UUID, company_id: UUID) -> TeamRequestRead: ...   # cloud only, D3
    def list_recent(self, *, stato: str | None, origine: str | None, limit: int, cursor: str | None) -> TeamRequestList: ...
    def get(self, request_id: UUID) -> TeamRequestRead: ...
    def set_status(self, request_id: UUID, stato: str, admin_id: UUID) -> TeamRequestRead: ...
    def set_note(self, request_id: UUID, note: str | None, admin_id: UUID) -> TeamRequestRead: ...
    def set_summary(self, request_id: UUID, riassunto: str, admin_id: UUID) -> TeamRequestRead: ...
```

  `create`: a private `_requestable` reads the proposal (`ValidationFailed`,
  `PROPOSAL_REFUSED`, one sentence so the answer does not say which proposals exist)
  when it does not exist, its `origine` is not the caller's own, on the cloud its
  `user_id` is not the caller's, or it is older than a day, so a public or cloud caller
  can never hire off someone else's proposal; then that a member of its team is still
  there (`NOBODY_TO_HIRE` when none is); the insert relies on
  `uq_team_requests_proposal_id`, and an `IntegrityError` on it becomes
  `InvalidState("Questa proposta è già stata richiesta.", proposal_id=...)`
  (409), so two clicks in two sessions file one request; one `TeamRequestTalent` per
  member of the proposal's team; the mail to `settings.contracts_mail` («Nuova
  richiesta team da Acme S.r.l.», the summary, the link `{hub_url}/admin/team/{id}`);
  `names_the_company` on the summary logged at warning (the refusal is D1's and
  `set_summary`'s, at the send and at the edit); `tracker.team_event("team_richiesta_inviata", {"origine"})`.
  `set_summary` refuses, before writing anything, a `riassunto` that `names_the_company`
  with the same `InvalidState` sentence D1's `contact_talents` refuses it with (409:
  «Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti.»), a request
  with no proposal (`NO_SUMMARY`) likewise; else it writes `team_proposals.riassunto`
  and records an `AdminAction` (`kind="overridden"`
  on `entity_type="team_request"`). Public routes: `TeamBuilderOff` → 503,
  `LlmUnavailable` → 502 through the handler; the semaphore's 503; `team_caps.require_daily_room`'s
  503 (§ 5); `spend_one` on both public routes (proposals and requests), as § 3.2 says.
  The cap is a spend guard, not a ledger: it is checked while the semaphore's slot is
  held, so concurrent calls can exceed it by at most the concurrency minus one, and a
  paid call that fails writes no row and is not counted; `spend_one` and the semaphore
  bound what a flood of failing descriptions can cost (a stated limit).

- [ ] **Step 1: Failing tests**: `test_request_from_a_proposal_files_the_talents_and_mails`, `test_request_is_unique_per_proposal` (two sessions, one wins, the other 409), `test_request_refuses_an_old_proposal`, `test_request_refuses_a_proposal_of_another_origin_or_none` (a cloud or admin proposal, another cloud user's, an unknown id, all `PROPOSAL_REFUSED`), `test_request_refuses_a_proposal_with_nobody`, `test_request_logs_a_summary_that_names_the_company`, `test_names_the_company_words` (a legal form and a generic word of the kind exempt, four letters the floor), `test_summary_of_a_request_with_no_proposal_is_refused`, `test_list_filters_and_pages_by_cursor`, `test_status_note_and_summary_record_the_admin`, `test_the_daily_cap_counts_paid_proposals_since_midnight_in_rome` (a `NO_CALL_MODEL` row not counted, an admin's not counted, yesterday's not counted), `test_the_caps_have_their_defaults_and_reach_the_container`; the API: `test_public_proposal_answers_the_team_without_ids`, `test_public_proposal_is_503_when_off`, `test_public_proposal_is_503_when_the_cap_is_full` (a stub `LlmCall` that blocks on an event while a second request arrives), `test_public_proposal_is_503_when_the_daily_cap_is_reached`, `test_public_proposal_is_502_when_claude_is_down`, `test_public_request_is_201_then_409`, `test_public_request_refuses_an_old_a_cloud_or_an_empty_proposal`, `test_public_request_is_throttled`, `test_admin_routes_need_an_admin`.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(hub): a proposal becomes a team request the admin reads over the API` (`REB-512.`).

### Task C6: The public page

**Files:**
- Create: `apps/web/src/components/TeamBuilder.tsx` (the box, the examples, the run, the result, «Rigenera»; a `mode` prop: `'public'` renders «Assumi team» with the three-field form and the thanks, `'cloud'` renders «Assumi team» that files at once and is D3's), `TeamBuilder.test.tsx`, `apps/web/src/pages/Team.tsx`, `Team.test.tsx`, `apps/web/src/lib/bands.ts` (the label), `bands.test.ts`
- Modify: `apps/web/src/lib/api.ts` (`team.propose`, `team.request`, the types: the public `TeamMember` has no `freelancer_id`), `router.tsx` (`/team` under `publicLayout`), `pages/Chooser.tsx` (one line linking «Cerca un team» to `/team`, beside the two wizard entries)
- Test: beside each

**The page (spec § 3.1):** heading «Descrivi il progetto, ti proponiamo il team», three
example buttons (a web app for a fintech, a data pipeline on site in Milan, a mobile app
with a designer), the `Textarea`, «Proponi il team» with the pending label «Sto leggendo
i profili…», the result (summary, a `Card` per member with role, reason, `sintesi`,
seniority, years, skills as `Badge`s, mode, the band), the team's bands per day and per
month with «22 giorni al mese», the note `Input` and «Rigenera», «Assumi team» opening
the form (three `Input`s, the wizard's own validation words), «Invia la richiesta», the
thanks paragraph; the error paragraphs for `503` (both sentences, as the API answers
them), `502`, `422`, `409`; the beta box with the button to `/companies?da=team-builder`.
Loading and errors as the wizards do them.

- [ ] **Step 1: Failing tests**: the examples fill the box; the run calls `team.propose` with the description; the result renders bands and cards without a name or an id; «Rigenera» sends `previous_id` and the note; the form sends the three fields and shows the thanks; each error paragraph; the beta link carries `da=team-builder`.
- [ ] **Step 2: fail. Step 3: implement. Step 4: `pnpm --filter hub lint && test && build`.**
- [ ] **Step 5: Commit** `feat(hub): the public team page proposes a team and files the request` (`REB-513.`).

### Task C7: «Richieste team» and the talent's card in the admin

**Files:**
- Create: `apps/web/src/pages/admin/RichiesteTeam.tsx`, `RichiestaTeam.tsx`, `RichiesteTeam.test.tsx`, `RichiestaTeam.test.tsx`
- Modify: `router.tsx` (`/admin/team`, `/admin/team/$id`), `components/SidebarNav.tsx` («Richieste team» between «Match» and «Aziende», icon `Users`), `lib/api.ts`, `lib/format.ts` (`TEAM_REQUEST_STATE_LABELS`, `TEAM_ORIGIN_LABELS`, `TALENT_ANSWER_LABELS`, copied from `team_words.py`; the `xfail` marks of C2's `test_web_labels.py` entries removed), the admin talent detail page (`AdminFreelancerDetail`: a «Scheda anonima» section with the card's fields, the mode, `generated_at`, `error`, and «Rigenera scheda» calling `POST /card`)
- Test: beside each, and `test_web_labels.py` green

**The screens (spec § 3.5):** the list with the state filter pills of `lists.tsx`'s
shape (company, origin, date, state, «N sì su M»), paged with «Altri» on the cursor; the
page with the description, the summary in a `Textarea` with «Salva il riassunto», the
place, the team table (name linking to `/admin/freelance/$id`, role, the freelancer's
rate, the band, the answer column reading «In attesa» until D1), the contacts, the state
with «Segna come contattata» and «Chiudi», the note `Textarea` with «Salva la nota».
«Contatta i talenti» is Task D1's and does not appear here.

- [ ] **Step 1: Failing tests**: the list renders rows and pills and pages; the page renders the team with names and rates, saves the summary and the note, moves the state; the talent page renders the card and regenerates it.
- [ ] **Step 2: fail. Step 3: implement. Step 4: lint, test, build; `uv run pytest projects/hub/packages/core/tests/test_web_labels.py`.**
- [ ] **Step 5: Commit** `feat(hub): the admin reads team requests and each talent's anonymous card` (`REB-514.`).

### Task C8: The privacy section

**Files:**
- Modify: `projects/website/src/privacy.html` (a new section «La tua scheda e il team builder» between «L'iscrizione a rebase» and «Il tuo spazio PigroCRM»; the CRM section's «Non trasferiamo nulla a terzi» is about the CRM and stays), `projects/website/src/landing-pages.test.ts` (the Anthropic host, `www.anthropic.com` or the host of its privacy page, added to the external-link allowlist in the same commit), `landing-pages.test.ts` if it pins the page's headings
- Test: `pnpm --filter website test`, `build`

The section, for Ivan's review on the PR, says (in the page's voice): since September
2026 the text of a freelancer's CV is sent to Anthropic's API, with inference in the
European Union, to write an anonymous description of the profile (role, seniority,
skills, sectors, languages), every time the CV changes or an admin asks for it again;
the description a visitor types on the team builder is sent to the same API together
with the anonymous descriptions of every profile, to propose a team; Anthropic processes
the data on rebase's behalf and does not train on it (a link to its privacy page); a
visitor of the public page sees the description without the name; a company rebase
admits to the talent cloud sees the profile by name with the CV and its links, and the
CV carries what the freelancer wrote in it; to have the description deleted, write to
the address the page already gives.

- [ ] **Step 1:** Write it, run the website tests, `pnpm --filter website build`.
- [ ] **Step 2: Commit** `docs(website): the privacy page says what the team builder sends to Anthropic and who sees a profile` (`REB-515.`), and a comment on the card asking Ivan to read the wording.

### Task C9: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main`; if `0020_campaigns.py` or `0021_match_pigro_link.py` is on `main`, rename this branch's migration to the next free number and point its `down_revision` at `main`'s head (`alembic heads` on the branch answers one head); every suite green in the foreground.
- [ ] **Step 2:** On the preview: `REBASE_ANTHROPIC_API_KEY` in the preview hub's `.env` (Ivan holds the key: ask him for it on the card and wait), restart `api`, `rebase cards-refresh` in the container until it prints «0 schede scritte, 0 non riuscite», open `/hub/team`, describe a project, read the team, regenerate, file a request with a test address, read it in «Richieste team».
- [ ] **Step 3:** The pairs (the public page, the chooser line, «Richieste team», the request page, the talent's card) and the video, with the monorepo's `docs/pr-screenshots/record.mjs`.
- [ ] **Step 4:** Not before Ivan's «ok» on the privacy section sits on REB-515: then `gh pr ready`, the fresh reviewer, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comments on C1 to C9, each moved to `Done`; a project update on P-REB-43.
- [ ] **Step 5:** Tell Ivan the `website-v*` tag carrying the privacy section goes out before the `hub-v*` tag that brings the key (or CVs reach Anthropic before letsrebase.com/privacy says so), that production needs the key before the `hub-v*` tag, that the talents' mail of spec § 4.4 goes out before the production `rebase cards-refresh` (the mail is his send; the card for it is in Backlog), and that the host vhost needs `proxy_read_timeout 90s` on `/api/hub/team/proposals`.

---

## Milestone D: Work a team request with the talents, and open the cloud to a company

Before the first task: `git fetch origin && git worktree add -b
ivansala/milestone-talent-cloud ../pigrocrm-talent-cloud origin/ivansala/milestone-team-builder`
(C's tip, once C5 is on it), the installs, the draft PR with D1 to D5; `gh pr create
--base ivansala/milestone-team-builder` so the diff shows D alone; when C merges and its
branch is deleted, GitHub retargets it to `main`.

### Task D1: The availability mail and the answer

**Files:**
- Modify: `packages/core/src/rebase_core/team_requests.py` (`contact_talents(request_id, admin_id, *, only_silent: bool) -> TeamRequestRead`, `answer(raw_token, risposta) -> Literal["si", "no", "invalid"]`), `mail.py` (`team_availability_mail(to, *, nome, ruolo, riassunto, tariffa, yes_url, no_url)`; `_button(href, label, colour=CTA)`), `team_schemas.py`, `analytics.py` (`team_talento_risposta`)
- Modify: `apps/api/src/rebase_api/routers/admin_team.py` (`POST /api/hub/team/requests/{id}/contact?only_silent=` admin), `routers/team.py` (`POST /api/hub/team/availability` public, `{t, risposta}` → `{esito}`)
- Create: `apps/web/src/pages/TeamRisposta.tsx` (public: reads `t` and `r` from the URL, shows the question and «Conferma», posts, shows the three sentences of spec § 3.2), `TeamRisposta.test.tsx`; `router.tsx` (`/team/risposta`)
- Modify: `apps/web/src/pages/admin/RichiestaTeam.tsx` («Contatta i talenti» the first time, then «Rimanda a chi non ha risposto»; the answer column with «Sì» / «No» / «In attesa» and the time; the refusal sentence shown when the summary names the company)
- Test: `test_team_requests.py`, `test_team_api.py`, the web tests

**Interfaces:** `contact_talents`: refuse with `InvalidState("Il riassunto nomina
l'azienda: correggilo prima di scrivere ai talenti.")` when `names_the_company` (C5:
a distinctive word of the company's name, four letters or more, legal forms and generic
company words such as «logistica» or «software» exempt, since the prompt itself
describes a company that way); for
each talent (all when `only_silent` is false, those with `risposta` NULL when true), a
fresh `token_urlsafe(32)`, its SHA-256 stored (replacing the old one), the mail through
the sender with `yes_url = f"{settings.hub_url}/team/risposta?t={raw}&r=si"` and the
`no` twin (the hub's own SPA origin, as the magic link's `/entra` link is built in
`users.py`), `mail_sent_at` on acceptance; the request's `stato` becomes `contattata`
and `contacted_at` is set on the first send. The admin screen offers «Contatta i
talenti» while nobody was mailed and «Rimanda a chi non ha risposto» afterwards, so a
talent who answered is never mailed again. `answer`: the hash looked up; unknown,
already answered, or `mail_sent_at` older than thirty days → `invalid`; else `risposta`,
`risposta_at`, `tracker.team_event("team_talento_risposta", {"risposta"})`. The mail's
buttons: «Sono disponibile» on `#2b8a3e` (white text, contrast 4.7:1), «Non sono
disponibile» on `CTA`; Ivan picks otherwise on the PR.

- [ ] **Step 1: Failing tests**: `test_contact_mails_every_talent_once`, `test_contact_only_silent`, `test_contact_refuses_a_summary_that_names_the_company`, `test_availability_records_yes_and_no`, `test_availability_token_is_one_use_and_expires`, the routes (a GET on `/api/hub/team/availability` is 405, the post answers `{esito}`), the answer page's question, confirm and three sentences, the request page's buttons and column.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green; ruff, mypy; lint, test, build.**
- [ ] **Step 5: Commit** `feat(hub): each talent of a request answers with one click` (`REB-517.`), a comment on the card asking Ivan to read the mail's wording and pick the colours.

### Task D2: Vetted, and the cloud's door

**Files:**
- Modify: `packages/core/src/rebase_core/freelancers.py` (`set_vetted(freelancer_id, vetted: bool, admin_id) -> FreelancerRead`, an `AdminAction` of kind `vetted`), `talenti.py` and `schemas.py` (`vetted_at`, `ha_scheda_anonima` on the talent reads), new `cloud.py` (`TalentCloudService`: `grant(company_id, admin_id) -> TalentCloudGrantRead` resolving the request's user and answering the live grant of that user for that company when one exists, `revoke(company_id, admin_id)` closing that company's live grant, `list()`, `for_user(user_id) -> TalentCloudGrant | None`, the newest live grant of the user across companies), `mail.py` (`talent_cloud_opened_mail(to, *, nome, azienda, url)`), `members.py` (`MeRead.talent_cloud: bool`)
- Modify: `apps/api/src/rebase_api/routers/admin.py` (`POST /api/hub/freelancers/{id}/vetted` `{vetted: bool}`; `POST /api/hub/companies/{id}/cloud`, `DELETE /api/hub/companies/{id}/cloud`, `GET /api/hub/cloud/grants`), `routers/members.py` (`talent_cloud` on `/me`)
- Modify: `apps/web/src/pages/admin/lists.tsx` («Segna come verificato» / «Togli la verifica» on the talent row's menu, a «Verificato» pill; «da team builder» on a company row whose `origine` is `team-builder`), `AdminCompanyDetail` («Apri il talent cloud» / «Revoca il talent cloud», the request page has the room the row lacks), `pages/CompanyWizard.tsx` (the box when `resolveAttribution(search).origine === 'team-builder'`: «Stai chiedendo l'accesso al talent cloud: compila la richiesta e ti ricontattiamo noi.»), the talent detail page (the vetted state), `lib/format.ts` (`da team builder`, the vetted words)
- Test: core, API and web tests beside each

- [ ] **Step 1: Failing tests**: `test_set_vetted_records_the_admin`, `test_grant_is_one_live_per_user_and_company_and_mails` (a second request of the same referente for the same company answers the live grant, no 500; for another company a second grant), `test_a_grant_that_loses_the_race_to_the_index_answers_the_winner`, `test_revoke_closes_it` (only that company's grant; another company's stays live), `test_for_user_answers_the_newest_live_grant_across_companies` (a referente of companies A and B: the cloud attributes a proposal or a request to whichever grant is newest and live, never a fixed one), `test_me_carries_talent_cloud`, the routes, the row actions, the detail page's actions, the wizard's box on `?da=team-builder`, «da team builder».
- [ ] **Step 2: fail. Step 3: implement. Step 4: green everywhere.**
- [ ] **Step 5: Commit** `feat(hub): an admin marks a talent vetted and opens the talent cloud to a company` (`REB-518.`).

### Task D3: The cloud

**Files:**
- Modify: `packages/core/src/rebase_core/cloud.py` (`CloudTalentService.list(query) -> list[CloudTalentRead]` over `cloud_visible`, filters `ruolo`, `seniority`, `competenza` (ILIKE over the card's `competenze`), `modalita`, `fascia_min`, `fascia_max`, sorted vetted first then name, capped at 200; `cv(freelancer_id) -> FreelancerFile` only for a `cloud_visible` freelancer, `NotFound` otherwise), `team_schemas.py` (`CloudTalentRead`: `freelancer_id`, `nome`, `cognome`, `links`, `vetted`, `card` with `luogo` None, `modalita`, `fascia`, `ha_cv`; never the rate, the state, the notes), `team_requests.py` (`create_for_talent`)
- Create: `apps/api/src/rebase_api/routers/cloud.py` (`GET /api/hub/me/cloud/talents`, `GET /api/hub/me/cloud/talents/{id}/cv` (a route of its own, not the member's), `POST /api/hub/me/cloud/proposals` (origine `cloud`), `POST /api/hub/me/cloud/requests` with `{proposal_id}` or `{freelancer_id}`), `deps.py` (`CloudDep`: `MeDep` plus `TalentCloudService.for_user`, else `403` «Il talent cloud non è aperto per questo account.»)
- Create: `apps/web/src/pages/member/Cloud.tsx`, `Cloud.test.tsx`; `router.tsx` (`/me/cloud`); `components/SidebarNav.tsx` («Talent cloud» when `me.talent_cloud`); `lib/api.ts`; `components/TeamBuilder.tsx` in `mode='cloud'`
- Test: `packages/core/tests/test_cloud.py`, `apps/api/tests/test_cloud_api.py`, the web tests

**The page (spec § 4.2):** the builder box on top in cloud mode («Assumi team» files
at once), then the filters (`Select`s and one `Input`), the cards with name, surname,
the links, «Apri il CV», `sintesi`, the badges, the mode, «Verificato da rebase», the
band, «Richiedi»; a thanks toast on a request. Never the rate, the state, the notes.

- [ ] **Step 1: Failing tests**: the filters, the order and the cap; the guard; the CV route refusing a freelancer outside `cloud_visible`; a request from a card and from the builder (`origine = cloud`, `user_id`, `company_id`, the user's phone or none); and that the read carries no rate, state or note.
- [ ] **Step 2: fail. Step 3: implement. Step 4: green everywhere.**
- [ ] **Step 5: Commit** `feat(hub): an admitted company browses the talents by name and asks for them` (`REB-519.`).

### Task D4: Over MCP

**Files:**
- Modify: `apps/mcp/src/rebase_mcp/server.py` (the tools of spec § 8, `set_team_request_summary` included, each calling the core service with the admin behind the token; `build_server` takes `llm: LlmCall | None` and the settings the way it takes `signing`), `__main__.py`, `http.py`; the `talenti` tools carry `vetted_at` and `ha_scheda_anonima`
- Test: `apps/mcp/tests/test_tools.py`

- [ ] **Step 1: Failing tests** (one per tool, a `RecordingCall` for `propose_team` and `regenerate_freelancer_card`). **Step 2: fail. Step 3: implement**, descriptions in Italian. **Step 4: green; ruff, mypy.**
- [ ] **Step 5: Commit** `feat(mcp): an admin works team requests, cards, the vetted flag and the cloud over MCP` (`REB-520.`).

### Task D5: The milestone's evidence

- [ ] **Step 1:** `git merge origin/main` once C has merged; the migration re-pointed as C9 did if anything else landed; every suite green.
- [ ] **Step 2:** On the preview: a request's «Contatta i talenti» to a test talent of ours, both answers confirmed on the page and read; a talent marked vetted; a grant to a test company; the cloud page with a filter, a card's «Richiedi», the builder inside; the wizard's box at `/companies?da=team-builder`.
- [ ] **Step 3:** The pairs and the video.
- [ ] **Step 4:** Not before Ivan's «ok» on the mail's wording and colours sits on REB-517: then `gh pr ready`, the fresh reviewer, Greptile and CodeRabbit to 5/5 and clean, a merge commit; the closing comments on D1 to D5; a project update on P-REB-43.
- [ ] **Step 5:** Tell Ivan the talents' mail of spec § 4.4 goes out before the first grant in production.

## Self-review (done while writing, redone after the review of 2026-09-25)

- **Spec coverage.** § 2 C2; § 2.1 C2, C3; § 3.1 C6; § 3.2 C5, D1; § 3.3 C4; § 3.4 C4;
  § 3.5 C5, C7, D1; § 3.6 D1; § 4.1 D2; § 4.2 D3; § 4.3 C6, D2; § 4.4 C9, D5 (Ivan's
  send); § 5 C1, C5 (the cap); § 5.1 C3; § 6 C1, C8, C9; § 7 the tests named per task;
  § 8 D4; § 9 the two milestones.
- **The review's findings, folded in.** The CV hooks on `apply`, `replace_cv` and
  `clear_cv` (C3); the answer as a post from a page (D1); the privacy section and what
  the talents are told (C8, C9, D5, spec § 4.4); the migration re-pointed at merge (C9,
  D5); `?da=` (C6, D2); positional catalogue ids (C4); the errors as domain errors
  (C1, C2); the background session (C3); no ids on the public read (C4); the refusal
  at the send and the editable summary (C5, D1); the unique proposal (C2, C5); the
  single-talent request (C5, D3); `admin` as an origin (C2); grants by user and one
  `cloud_visible` filter (D2, D3); the failure paths of the structured output (C1, C3,
  C4); `modalita` read at display, the scanned CV, the failed hash (C3); the button
  colours (D1); cursor pagination and the cap (C5, D3); labels in core (C2, C7, D2); the
  cap on concurrent proposals and the request throttle (C5); the hidden list (spec
  § 4.2); the detail page for the grant (D2); the signatures aligned; `RecordingCall`
  in `llm.py` (C1); `client_hash` dropped; admin routes in the admin routers; the
  compose default; `token_hash` nullable; «Rimanda» only to the silent; the test file
  names; the DECISIONS rows in the design record's PR.
- **Placeholders.** The `anthropic` pin is «the current release on PyPI» at C1's run;
  the migration's final number is chosen at C9, by the rule above.
- **Type consistency.** `LlmCall`, `LlmRequest`, `LlmResponse`, `LlmUnavailable`,
  `RecordingCall` (C1) are what C3, C4 and D4 consume; `Card` (C2) is what C3 writes,
  C4 reads and D3 strips; `Band`, `band_for`, `team_bands`, `cloud_visible` (C4) are
  what C5, C7, D3 use; `TeamRequestService` (C5) is what D1, D3 and D4 extend;
  `TalentCloudService.for_user` (D2) is what D3's `CloudDep` reads.
- **Review Focus.** Each of the five has its test in C3, C4, C5 or D1, named above.
