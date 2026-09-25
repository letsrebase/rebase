# Campaigns, phase 1: build a list and send it — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An admin builds a mail list in «Campagne» from a journey state or from the
Talenti and company filters, writes the mail, sends a test to themselves, and sends
it now or at a set time. The hub records per person whether it left, was skipped,
failed, was delivered or bounced. Every mail carries a one-click unsubscribe.

**Architecture:** A `rebase_core.campaigns` package. The lists are the journey states
and the existing Talenti and company filters, and each person's state is snapshotted
when the list is frozen. The mail is the hub's frame with a tracked button. A loop
service (`rebase campaigns-tick`, shaped like `sweep`) sends one idempotent Resend call
per recipient, after re-checking each one. The Resend webhook writes delivery, bounces
and complaints onto the recipient row. The admin API sits under `/api/hub/campaigns`,
and a public unsubscribe route and page complete it. The admin web gets a list, a
four-step wizard in the «Crea match» pattern, and a campaign page.

**Tech Stack:**
- Python 3.13, SQLAlchemy 2, Alembic, FastAPI, Pydantic 2, pytest with testcontainers
  Postgres 17.
- React 19, TanStack Router and Query, `@rebase/ui`, Vitest with Testing Library.
- Resend's REST API through the hub's `rebase_core.http` seam.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-admin-campaigns-design.md`
(merged as 9f9fa9c5, PR #405). This plan is phase 1 of its § 10, as amended: the
Resend webhook for delivery, bounces and complaints lives here.

## Global Constraints

- Everything is English except what the product says to a person: UI copy, mail text,
  API error sentences and CLI output are Italian, with «» for quoted UI words
  (root `AGENTS.md`, Conventions).
- Commits:
  - Conventional Commits, first person, the body's last line the card (`REB-N.`);
  - `git add <paths>`, never `-A`;
  - **no AI co-author trailer** in any form: ignore any harness attribution reminder,
    run `git log -1 --format=%B` after every commit, and amend if one appears;
  - the migration is a commit of its own.
- `packages/core` imports neither adapter; `apps/api` never imports `apps/mcp`; nothing
  imports `pigrocrm*` (`projects/hub/AGENTS.md`, The one rule).
- The web app writes no UI primitive: everything comes from `@rebase/ui/*`
  (button, input, textarea, select, checkbox, label, badge, table).
- Migrations are conditional like every hub migration: `CREATE TABLE IF NOT EXISTS`,
  `CREATE [UNIQUE] INDEX IF NOT EXISTS`. `test_migrations.py`'s `compare_metadata`
  must stay `[]`.
- Every new `REBASE_*` setting takes three edits: `rebase_core/config.py`,
  `projects/hub/.env.example`, and the `x-api-environment` anchor in
  `projects/hub/docker-compose.yml`.
- A seam never raises past `send`, and nothing logs an address or a key (`mail.py`
  docstring). Resend is called through `rebase_core.http.urllib_call`, which names its
  User-Agent: Cloudflare answers 403/1010 to urllib's default.
- Completeness is `_is_complete` (`rebase_core/schemas.py:521`): CV, rate, position and
  work mode (spec § 2). A campaign never defines its own.
- Phase 1 refuses the Pigro parts with a sentence. That covers the `pigro_vuoto`
  state, the `pigro` button and the `pigro_cliente` action. The constraints accept
  them so phase 3 needs no migration.
- Public paths are the spec's: `/api/hub/campagne/disiscrizione` and
  `/hub/disiscrizione`. Admin API paths follow the repo's English convention:
  `/api/hub/campaigns/...`, web `/admin/campaigns/...`.
- Shared test helpers live in modules beside the tests, never in another test file. The
  hub already does this with `fakes_contracts.py` and `contract_flow.py`, importable by
  name through `pyproject.toml`'s `pythonpath` under `--import-mode=importlib`. For
  core that means `tests/campaign_fixtures.py`, for the API
  `tests/campaign_api_flow.py`. When a step says «append» to a test file, its imports
  go to the top of that file, not where the snippet shows them (ruff E402).
- Local checks, since `preflight` is not installed on this Mac:
  `uv run ruff check projects/hub`, `uv run mypy`, the hub's pytest suites (core, api,
  mcp) as separate processes and never two DB-backed runs at once, then
  `pnpm --filter hub lint`, `pnpm --filter hub test` and `pnpm --filter hub build`.

## Review Focus

These are the five failure modes most likely to bite a person using this. The spec
implies them, and the plain feature tests would miss them. Each is pinned by a test in
the task named.

1. **An admin edits the mail after the test.** «Invia» must stay off until a new test
   has left. `TimestampMixin.updated_at` moves on every write, including the one that
   records the test, so the rule uses a column of its own, `contenuto_at`, written only
   by content edits (Task 9, Task 10, Task 20).
2. **«Programma» across a DST change.** A scheduled time is Europe/Rome wall-clock time.
   - 09:30 on 25/10/2026 is 08:30 UTC, and 09:30 on 24/10/2026 is 07:30 UTC.
   - 02:30 on 29/03/2026 does not exist, and the hub refuses it with a sentence.
   - 02:30 on 25/10/2026 exists twice, and the hub takes the first (CEST).
   - A time already past is refused (Task 10).
3. **One address, two identities.**
   - A lead whose address also has a card is not a lead.
   - A referente with two open requests is one row.
   - `Ada@Studio.it` and `ada@studio.it` are one person.
   - A list is never mailed twice to the same address (Task 3, Task 6).
4. **A card deleted or completed between freezing and sending.** The row is skipped with
   «non più in lista» or «ha già fatto l'azione», never sent (Task 11).
5. **A webhook for a mail whose campaign was cancelled mid-send, a test mail, or a
   stranger's mail.**
   - A real mail that left still records its delivery.
   - A `kind=test` event and an untagged unknown id answer 200 and change nothing.
   - A tagged event with no row yet answers 503 so Resend retries (Task 15).

---

## Card groups and order

Each group is one Linear card and one agent run, in the milestone «Send a campaign» of
P-REB-41. They run in this order; a card starts when every card it depends on is merged
into the milestone branch.

| Group | Card | Card title | Tasks | Depends on |
|---|---|---|---|---|
| A | REB-464 | Store campaigns, recipients and opt-outs, and read the journey states | 1, 2, 3 | none |
| B | REB-465 | Build a campaign's list: templates, exclusions, the action already done | 4, 5, 6 | A |
| C | REB-466 | Render a campaign mail and send it through Resend idempotently | 7, 8 | B |
| D | REB-467 | Draft, test, schedule and cancel a campaign | 9, 10 | C |
| E | REB-468 | Send due campaigns from a loop service | 11, 12 | D |
| F | REB-469 | Let a person unsubscribe in one click | 13, 14 | A |
| G | REB-470 | Record delivery, bounces and complaints from Resend's webhook | 15, 16 | A, F |
| H | REB-471 | Serve campaigns to the admin over the API | 17 | D, F |
| I | REB-472 | «Campagne» in the admin: the list and the four-step wizard | 18, 19, 20 | H |
| J | REB-473 | The campaign page, and the video of the whole flow | 21, 22 | E, G, I |

## File structure

Created in `projects/hub/packages/core/src/rebase_core/campaigns/`:
- `__init__.py`: the package docstring (what a campaign is, spec pointer).
- `states.py`: `JOURNEY_STATES`, `Candidate`, `card_state()`,
  `candidates_for_state()`. It has no send logic.
- `templates.py`: `STATE_TEMPLATES`, the second wave's mails as editable starting
  points.
- `actions.py`: `snapshot()` (the state an action is measured against) and `done_at()`
  (has the person done it since, and when).
- `audience.py`: `candidates()` (a state or filters into people), `exclusions()`
  (reasons someone is left out), `build_audience()`. It also holds the reason
  constants.
- `schemas.py`: every Pydantic shape of the feature, including the two filter models.
- `render.py`: `RenderTarget`, `RenderedMail`, `render()`, the tracked URL and the
  unsubscribe URLs.
- `sender.py`: `SendOutcome`, `CampaignSender`, `ResendCampaignSender`,
  `RecordingCampaignSender`, `campaign_sender_from_settings()`.
- `service.py`: `CampaignService`, the admin's verbs (create, update, audience, test,
  schedule, back to draft, cancel, list, detail).
- `tick.py`: `run_tick()`, the loop's one pass.
- `optouts.py`: `OptoutService` (the link, a complaint, «Non scrivere mai»).
- `webhook.py`: `verify_signature()`, `apply_event()`.

Modified in core:
- `rebase_core/models.py`: constants and `Campaign`, `CampaignRecipient`,
  `CampaignOptout`, appended after `UserSession`.
- `migrations/versions/0020_campaigns.py`: new.
- `rebase_core/config.py`: `campaign_from`, `campaign_gap_days`,
  `resend_webhook_secret`.
- `rebase_core/cli.py`: `campaigns_tick()` and its subcommand.

Tests in `projects/hub/packages/core/tests/`: `test_campaign_states.py`,
`test_campaign_audience.py`, `test_campaign_render.py`, `test_campaign_sender.py`,
`test_campaign_service.py`, `test_campaign_tick.py`, `test_campaign_optouts.py`,
`test_campaign_webhook.py`, plus edits to `test_migrations.py`, `test_cli.py` and
`test_documenso_compose.py`.

API in `projects/hub/apps/api/src/rebase_api/`:
- `deps.py`: `get_campaign_sender` and `CampaignSenderDep`.
- `routers/campaigns.py`: new, the admin routes and the public unsubscribe.
- `routers/resend.py`: new, the webhook.
- `main.py`: includes both routers.
- Tests: `tests/test_campaigns_api.py` and `tests/test_resend_webhook_api.py`.

Web in `projects/hub/apps/web/src/`:
- `lib/api.ts`: types, the `admin.*` campaign calls, and `campaigns.unsubscribe`.
- `lib/campaigns.ts`: new, labels and `personalise()`.
- `pages/admin/Campagne.tsx`, `CreaCampagna.tsx` and `Campagna.tsx`: new, each with
  its `.test.tsx`.
- `pages/Disiscrizione.tsx`: new, with its test.
- `router.tsx`: the routes.
- `components/SidebarNav.tsx`: the «Campagne» entry.

Ops and docs:
- `projects/hub/docker-compose.yml`: the `campaigns` service and three variables in the
  anchor.
- `projects/hub/.env.example`: the new variables.
- `projects/hub/AGENTS.md`: a section «Campaigns» covering the loop, the webhook and
  the Resend setup step.
- `docs/design/DECISIONS.md`: one row.

---

## Group A — Store campaigns, recipients and opt-outs, and read the journey states

### Task 1: The three tables and migration 0020

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/models.py` (append after `UserSession`, line ~706 onward)
- Create: `projects/hub/packages/core/migrations/versions/0020_campaigns.py`
- Test: `projects/hub/packages/core/tests/test_migrations.py` (append)

**Interfaces:**
- Produces:
  - Constants: `CAMPAIGN_FONTI`, `CAMPAIGN_STATES`, `CAMPAIGN_ACTIONS`,
    `CAMPAIGN_DESTINATIONS`, `RECIPIENT_STATES`, `RECIPIENT_KINDS`, `OPTOUT_SOURCES`,
    `CAMPAIGN_NAME_MAX_LENGTH = 120`, `CAMPAIGN_SLUG_MAX_LENGTH = 80`,
    `CAMPAIGN_SUBJECT_MAX_LENGTH = 200`, `CAMPAIGN_BUTTON_MAX_LENGTH = 60`,
    `CAMPAIGN_TEXT_MAX_LENGTH = 5000`, `RECIPIENT_REASON_MAX_LENGTH = 200`.
  - Models `Campaign`, `CampaignRecipient` and `CampaignOptout`, with the columns below.

- [ ] **Step 1: Write the failing migration tests**

Append to `tests/test_migrations.py`:

```python
def test_migration_0020_can_run_again_and_roll_back() -> None:
    """A retried deploy runs 0020's statements over tables that already exist, and the
    downgrade leaves 0019's schema: both must work, and the result must be the models'."""
    with PostgresContainer("postgres:17-alpine", driver="psycopg") as container:
        url = container.get_connection_url()
        upgrade_to_head(url)
        config = Config(str(INI_PATH))
        config.set_main_option("sqlalchemy.url", url)
        command.downgrade(config, "0019")
        command.upgrade(config, "head")
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0019'"))
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == head_revision()
            )
            diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert diff == [], diff
        engine.dispose()


def test_the_campaign_constraints_are_installed(hub_engine: Engine) -> None:
    """A campaign's source and its source's field go together, and every enum column
    refuses a value outside its list: proven with raw SQL, rolled back."""
    with hub_engine.connect() as connection:
        outer = connection.begin()
        user_id = connection.execute(
            text(
                "INSERT INTO users (id, email, nome, cognome, role, attivo, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'c@rebase.it', 'C', '', 'admin', true, now(), now()) "
                "RETURNING id"
            )
        ).scalar_one()
        base = (
            "INSERT INTO campaigns (id, created_by, nome, slug, fonte, stato_percorso, filtri, "
            "oggetto, testo, bottone_testo, bottone_meta, azione, stato, contenuto_at, "
            "created_at, updated_at) VALUES (gen_random_uuid(), :u, 'n', :slug, :fonte, :sp, "
            "CAST(:filtri AS JSONB), '', '', '', 'area', :azione, 'bozza', now(), now(), now())"
        )
        bad = (
            {"slug": "a", "fonte": "stato", "sp": None, "filtri": None, "azione": "cv"},
            {"slug": "b", "fonte": "filtri", "sp": None, "filtri": None, "azione": "cv"},
            {"slug": "c", "fonte": "stato", "sp": "lead", "filtri": None, "azione": "vola"},
            {"slug": "d", "fonte": "nuvola", "sp": None, "filtri": None, "azione": "cv"},
        )
        for values in bad:
            savepoint = connection.begin_nested()
            with pytest.raises(IntegrityError):
                connection.execute(text(base), {"u": user_id, **values})
            savepoint.rollback()
        connection.execute(
            text(base),
            {"u": user_id, "slug": "ok", "fonte": "stato", "sp": "lead", "filtri": None, "azione": "cv"},
        )
        outer.rollback()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_migrations.py -k "0020 or campaign_constraints"`
Expected: FAIL: `Can't locate revision identified by '0020'`, or the `campaigns`
relation does not exist.

- [ ] **Step 3: Add the models**

Append to `models.py` (imports already there: `Any`, `JSONB`, `CheckConstraint`,
`Index`, `Integer`, `ForeignKey`, `DateTime`, `String`, `Text`, `func`):

```python
# ---- campaigns: a list, a mail, and what it led to (P-REB-41) ---------------------------

CAMPAIGN_FONTI = ("stato", "filtri", "lista")
CAMPAIGN_STATES = ("bozza", "programmata", "in_invio", "inviata", "annullata")
CAMPAIGN_ACTIONS = (
    "entrato",
    "cv",
    "scheda_completa",
    "profilo_creato",
    "richiesta_aggiornata",
    "pigro_cliente",
)
CAMPAIGN_DESTINATIONS = ("area", "wizard", "pigro", "richiesta")
RECIPIENT_STATES = ("in_coda", "inviata", "saltata", "fallita")
RECIPIENT_KINDS = ("freelancer", "lead", "azienda", "proprietario")
OPTOUT_SOURCES = ("link", "reclamo", "admin")
CAMPAIGN_NAME_MAX_LENGTH = 120
CAMPAIGN_SLUG_MAX_LENGTH = 80
CAMPAIGN_SUBJECT_MAX_LENGTH = 200
CAMPAIGN_BUTTON_MAX_LENGTH = 60
CAMPAIGN_TEXT_MAX_LENGTH = 5000
RECIPIENT_REASON_MAX_LENGTH = 200


class Campaign(Base, PrimaryKeyMixin, TimestampMixin):
    """One list and one mail (spec § 1). `fonte` says where the list comes from: a
    journey state (`stato_percorso`), the Talenti or company filters (`filtri`), or the
    recipients of an earlier campaign who did nothing (`segue_id`, phase 2), and the
    constraints keep each source with its own field. `contenuto_at` moves only when the
    admin edits the list or the mail, never on the writes that record a test or a
    send, because «Invia» compares it with `prova_inviata_at`: `updated_at` would move
    on the test's own write and lock the button for good."""

    __tablename__ = "campaigns"

    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    nome: Mapped[str] = mapped_column(String(CAMPAIGN_NAME_MAX_LENGTH), nullable=False)
    slug: Mapped[str] = mapped_column(String(CAMPAIGN_SLUG_MAX_LENGTH), nullable=False)
    fonte: Mapped[str] = mapped_column(String(10), nullable=False)
    stato_percorso: Mapped[str | None] = mapped_column(String(30), default=None)
    filtri: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    segue_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), default=None)
    oggetto: Mapped[str] = mapped_column(String(CAMPAIGN_SUBJECT_MAX_LENGTH), nullable=False)
    testo: Mapped[str] = mapped_column(Text, nullable=False)
    bottone_testo: Mapped[str] = mapped_column(String(CAMPAIGN_BUTTON_MAX_LENGTH), nullable=False)
    bottone_meta: Mapped[str] = mapped_column(String(10), nullable=False)
    azione: Mapped[str] = mapped_column(String(25), nullable=False)
    stato: Mapped[str] = mapped_column(String(12), nullable=False, default="bozza")
    contenuto_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    programmata_per: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    prova_inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Phase 3 (spec § 6.3): the last good and the last failed read of the CRM's usage.
    pigro_letto_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pigro_errore_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_campaigns_slug", "slug", unique=True),
        Index("ix_campaigns_stato_programmata_per", "stato", "programmata_per"),
        CheckConstraint("fonte IN ('stato', 'filtri', 'lista')", name="ck_campaigns_fonte"),
        CheckConstraint(
            "stato IN ('bozza', 'programmata', 'in_invio', 'inviata', 'annullata')",
            name="ck_campaigns_stato",
        ),
        CheckConstraint(
            "azione IN ('entrato', 'cv', 'scheda_completa', 'profilo_creato', "
            "'richiesta_aggiornata', 'pigro_cliente')",
            name="ck_campaigns_azione",
        ),
        CheckConstraint(
            "bottone_meta IN ('area', 'wizard', 'pigro', 'richiesta')",
            name="ck_campaigns_bottone_meta",
        ),
        CheckConstraint(
            "(fonte = 'stato') = (stato_percorso IS NOT NULL)", name="ck_campaigns_stato_percorso"
        ),
        CheckConstraint("(fonte = 'filtri') = (filtri IS NOT NULL)", name="ck_campaigns_filtri"),
        CheckConstraint("(fonte = 'lista') = (segue_id IS NOT NULL)", name="ck_campaigns_segue"),
    )


class CampaignRecipient(Base, PrimaryKeyMixin):
    """One person of a frozen list (spec § 3). `email` is lowercase and unique within a
    campaign. `prima` is the state the action is measured against, taken when the list
    was frozen. `disiscrizione_token` is kept as it was minted, not hashed: Resend's
    `Idempotency-Key` wants the byte-identical request on a retry (409
    `invalid_idempotent_request` otherwise), so a retried mail must render the same
    link, and what a leaked token can do is only unsubscribe that one address."""

    __tablename__ = "campaign_recipients"

    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nome: Mapped[str | None] = mapped_column(String(NAME_MAX_LENGTH), default=None)
    tipo: Mapped[str] = mapped_column(String(12), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    freelancer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("freelancers.id", ondelete="SET NULL"), default=None
    )
    signup_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("signups.id", ondelete="SET NULL"), default=None
    )
    pigro_slugs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    codice: Mapped[str] = mapped_column(String(8), nullable=False)
    prima: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    stato: Mapped[str] = mapped_column(String(10), nullable=False, default="in_coda")
    motivo: Mapped[str | None] = mapped_column(String(RECIPIENT_REASON_MAX_LENGTH), default=None)
    tentativi: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resend_id: Mapped[str | None] = mapped_column(String(64), default=None)
    disiscrizione_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    inviata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    consegnata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    rimbalzata_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    primo_clic_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    reclamo_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    entrato_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    azione_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("uq_campaign_recipients_campaign_email", "campaign_id", "email", unique=True),
        Index("uq_campaign_recipients_resend_id", "resend_id", unique=True),
        Index("uq_campaign_recipients_token", "disiscrizione_token", unique=True),
        Index("ix_campaign_recipients_email", "email"),
        Index("ix_campaign_recipients_campaign_stato", "campaign_id", "stato"),
        CheckConstraint(
            "stato IN ('in_coda', 'inviata', 'saltata', 'fallita')",
            name="ck_campaign_recipients_stato",
        ),
        CheckConstraint(
            "tipo IN ('freelancer', 'lead', 'azienda', 'proprietario')",
            name="ck_campaign_recipients_tipo",
        ),
    )


class CampaignOptout(Base):
    """An address no campaign may reach (spec § 7): by its own link, by a spam complaint
    Resend reported, or by an admin's «Non scrivere mai». Campaigns only: the magic link,
    the welcome mail and the contracts keep going."""

    __tablename__ = "campaign_optouts"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fonte: Mapped[str] = mapped_column(String(10), nullable=False)
    campaign_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        CheckConstraint(
            "fonte IN ('link', 'reclamo', 'admin')", name="ck_campaign_optouts_fonte"
        ),
    )
```

- [ ] **Step 4: Write migration 0020**

`migrations/versions/0020_campaigns.py`:

```python
"""campaigns, campaign_recipients, campaign_optouts: a list, a mail, and who may not get one

Revision ID: 0020
Revises: 0019

P-REB-41 phase 1 (spec 2026-09-25-admin-campaigns-design.md § 3). Three new tables and
nothing touched elsewhere. Conditional like every migration of this package, so a
retried deploy passes over what the first attempt created. None of the three is read by
PostHog's warehouse (`test_warehouse_contract.py`).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = "TIMESTAMP WITH TIME ZONE"

_TABLES = (
    "CREATE TABLE IF NOT EXISTS campaigns ("
    "id UUID PRIMARY KEY, "
    "created_by UUID NOT NULL REFERENCES users (id), "
    "nome VARCHAR(120) NOT NULL, "
    "slug VARCHAR(80) NOT NULL, "
    "fonte VARCHAR(10) NOT NULL, "
    "stato_percorso VARCHAR(30), "
    "filtri JSONB, "
    "segue_id UUID REFERENCES campaigns (id), "
    "oggetto VARCHAR(200) NOT NULL, "
    "testo TEXT NOT NULL, "
    "bottone_testo VARCHAR(60) NOT NULL, "
    "bottone_meta VARCHAR(10) NOT NULL, "
    "azione VARCHAR(25) NOT NULL, "
    "stato VARCHAR(12) NOT NULL, "
    f"contenuto_at {_TS} NOT NULL, "
    f"programmata_per {_TS}, "
    f"prova_inviata_at {_TS}, "
    f"inviata_at {_TS}, "
    f"pigro_letto_at {_TS}, "
    f"pigro_errore_at {_TS}, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"updated_at {_TS} NOT NULL DEFAULT now(), "
    "CONSTRAINT ck_campaigns_fonte CHECK (fonte IN ('stato', 'filtri', 'lista')), "
    "CONSTRAINT ck_campaigns_stato CHECK "
    "(stato IN ('bozza', 'programmata', 'in_invio', 'inviata', 'annullata')), "
    "CONSTRAINT ck_campaigns_azione CHECK (azione IN ('entrato', 'cv', 'scheda_completa', "
    "'profilo_creato', 'richiesta_aggiornata', 'pigro_cliente')), "
    "CONSTRAINT ck_campaigns_bottone_meta CHECK "
    "(bottone_meta IN ('area', 'wizard', 'pigro', 'richiesta')), "
    "CONSTRAINT ck_campaigns_stato_percorso CHECK "
    "((fonte = 'stato') = (stato_percorso IS NOT NULL)), "
    "CONSTRAINT ck_campaigns_filtri CHECK ((fonte = 'filtri') = (filtri IS NOT NULL)), "
    "CONSTRAINT ck_campaigns_segue CHECK ((fonte = 'lista') = (segue_id IS NOT NULL)))",
    "CREATE TABLE IF NOT EXISTS campaign_recipients ("
    "id UUID PRIMARY KEY, "
    "campaign_id UUID NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE, "
    "email VARCHAR(320) NOT NULL, "
    "nome VARCHAR(120), "
    "tipo VARCHAR(12) NOT NULL, "
    "user_id UUID REFERENCES users (id) ON DELETE SET NULL, "
    "freelancer_id UUID REFERENCES freelancers (id) ON DELETE SET NULL, "
    "signup_id UUID REFERENCES signups (id) ON DELETE SET NULL, "
    "pigro_slugs JSONB NOT NULL, "
    "codice VARCHAR(8) NOT NULL, "
    "prima JSONB NOT NULL, "
    "stato VARCHAR(10) NOT NULL, "
    "motivo VARCHAR(200), "
    "tentativi INTEGER NOT NULL, "
    "resend_id VARCHAR(64), "
    "disiscrizione_token VARCHAR(64) NOT NULL, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    f"inviata_at {_TS}, "
    f"consegnata_at {_TS}, "
    f"rimbalzata_at {_TS}, "
    f"primo_clic_at {_TS}, "
    f"reclamo_at {_TS}, "
    f"entrato_at {_TS}, "
    f"azione_at {_TS}, "
    "CONSTRAINT ck_campaign_recipients_stato CHECK "
    "(stato IN ('in_coda', 'inviata', 'saltata', 'fallita')), "
    "CONSTRAINT ck_campaign_recipients_tipo CHECK "
    "(tipo IN ('freelancer', 'lead', 'azienda', 'proprietario')))",
    "CREATE TABLE IF NOT EXISTS campaign_optouts ("
    "email VARCHAR(320) PRIMARY KEY, "
    f"created_at {_TS} NOT NULL DEFAULT now(), "
    "fonte VARCHAR(10) NOT NULL, "
    "campaign_id UUID REFERENCES campaigns (id) ON DELETE SET NULL, "
    "CONSTRAINT ck_campaign_optouts_fonte CHECK (fonte IN ('link', 'reclamo', 'admin')))",
)

_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaigns_slug ON campaigns (slug)",
    "CREATE INDEX IF NOT EXISTS ix_campaigns_stato_programmata_per "
    "ON campaigns (stato, programmata_per)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_campaign_email "
    "ON campaign_recipients (campaign_id, email)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_resend_id "
    "ON campaign_recipients (resend_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_recipients_token "
    "ON campaign_recipients (disiscrizione_token)",
    "CREATE INDEX IF NOT EXISTS ix_campaign_recipients_email ON campaign_recipients (email)",
    "CREATE INDEX IF NOT EXISTS ix_campaign_recipients_campaign_stato "
    "ON campaign_recipients (campaign_id, stato)",
)


def upgrade() -> None:
    for statement in (*_TABLES, *_INDEXES):
        op.execute(statement)


def downgrade() -> None:
    for table in ("campaign_optouts", "campaign_recipients", "campaigns"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
```

- [ ] **Step 5: Run the migration tests to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_migrations.py`
Expected: PASS. `test_the_migrations_produce_exactly_the_models_schema` included, so
`diff == []`. A diff about a foreign key's `ondelete` means the model and the SQL
disagree: make them agree.

- [ ] **Step 6: Commit the migration and the models (one commit)**

```bash
git add projects/hub/packages/core/src/rebase_core/models.py \
  projects/hub/packages/core/migrations/versions/0020_campaigns.py \
  projects/hub/packages/core/tests/test_migrations.py
git commit -m "feat(hub): the tables a campaign, its recipients and the opt-outs live in" -m "<why, first person>" -m "REB-464."
git log -1 --format=%B   # no Co-Authored-By line; amend if there is one
```

### Task 2: The three settings

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/config.py` (after `contracts_allow_draft`)
- Modify: `projects/hub/.env.example` (a new `# --- campaigns` block after the contracts' one, near line 74)
- Modify: `projects/hub/docker-compose.yml` (the `x-api-environment` anchor, after `REBASE_CONTRACTS_ALLOW_DRAFT`)
- Test: `projects/hub/packages/core/tests/test_campaign_states.py` (created here, first test)

**Interfaces:**
- Produces: `Settings.campaign_from: str`, `Settings.campaign_gap_days: int` and
  `Settings.resend_webhook_secret: str`.

- [ ] **Step 1: Write the failing test**

```python
"""Journey states (spec § 2) and the settings campaigns read."""

from pathlib import Path

from rebase_core.config import Settings

HUB = Path(__file__).resolve().parents[3]


def test_the_campaign_settings_have_their_defaults_and_reach_the_container() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.campaign_from == "Ivan di rebase <ciao@letsrebase.com>"
    assert settings.campaign_gap_days == 3
    assert settings.resend_webhook_secret == ""
    compose = (HUB / "docker-compose.yml").read_text(encoding="utf-8")
    example = (HUB / ".env.example").read_text(encoding="utf-8")
    for name in ("REBASE_CAMPAIGN_FROM", "REBASE_CAMPAIGN_GAP_DAYS", "REBASE_RESEND_WEBHOOK_SECRET"):
        assert f"{name}: ${{{name}" in compose, name
        assert name in example, name
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_states.py`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'campaign_from'`.

- [ ] **Step 3: Add the settings**

`config.py`, after `contracts_allow_draft`:

```python
    # --- campaigns (P-REB-41) --------------------------------------------------------
    # Who a campaign mail is from: a person, as the two September waves were, not the
    # magic link's «Rebase». Its replies land in the same inbox.
    campaign_from: str = "Ivan di rebase <ciao@letsrebase.com>"
    # Nobody gets two campaigns closer than this many days (spec § 5.3). It also makes a
    # wave of several campaigns scheduled for the same minute one mail per person.
    campaign_gap_days: int = 3
    # The `whsec_` secret Resend shows for this environment's webhook (spec § 6.1).
    # Empty: the webhook answers 503 and delivery, bounces and complaints are not read.
    resend_webhook_secret: str = ""
```

In `docker-compose.yml`, add these lines to the anchor after `REBASE_CONTRACTS_ALLOW_DRAFT`:

```yaml
  # Campaigns (P-REB-41): who the mail is from, the minimum gap between two campaigns
  # to one person, and the secret of this environment's Resend webhook. Not an empty
  # default for the gap: an int field fails at start-up on an empty value.
  REBASE_CAMPAIGN_FROM: ${REBASE_CAMPAIGN_FROM:-Ivan di rebase <ciao@letsrebase.com>}
  REBASE_CAMPAIGN_GAP_DAYS: ${REBASE_CAMPAIGN_GAP_DAYS:-3}
  REBASE_RESEND_WEBHOOK_SECRET: ${REBASE_RESEND_WEBHOOK_SECRET:-}
```

In `.env.example`, after the contracts block:

```bash
# --- campaigns (P-REB-41) ----------------------------------------------------------------
# The sender of a campaign mail, the gap in days between two campaigns to one person, and
# the `whsec_` secret Resend shows when this environment's webhook is created (AGENTS.md
# «Campaigns»). Without the secret the webhook answers 503.
REBASE_CAMPAIGN_FROM=Ivan di rebase <ciao@letsrebase.com>
REBASE_CAMPAIGN_GAP_DAYS=3
REBASE_RESEND_WEBHOOK_SECRET=
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_states.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/config.py projects/hub/.env.example \
  projects/hub/docker-compose.yml projects/hub/packages/core/tests/test_campaign_states.py
git commit -m "feat(hub): the sender, the gap and the webhook secret campaigns read" -m "REB-464."
```

### Task 3: The journey states

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/__init__.py`
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/states.py`
- Create: `projects/hub/packages/core/tests/campaign_fixtures.py` (the shared fixtures; later tasks append to it)
- Test: `projects/hub/packages/core/tests/test_campaign_states.py` (append)

**Interfaces:**
- Consumes: the models of Task 1; `Freelancer`, `User`, `Signup`, `Company` and
  `Login` from `models.py`.
- Produces:
  - `JOURNEY_STATES: dict[str, str]`, from key to Italian label.
  - `PHASE_ONE_STATES: tuple[str, ...]`.
  - `OPEN_COMPANY_STATES`.
  - `@dataclass(frozen=True) Candidate(email: str, nome: str | None, tipo: str, user_id: UUID | None = None, freelancer_id: UUID | None = None, signup_id: UUID | None = None, company_ids: tuple[UUID, ...] = (), pigro_slugs: tuple[str, ...] = ())`.
  - `card_state(cv_size, tariffa, posizione, remoto) -> Literal["completo", "manca_cv", "scheda"]`.
  - `candidates_for_state(session, stato) -> list[Candidate]`.
  - `display_name(nome: str | None) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/campaign_fixtures.py`:

```python
"""Rows the campaign tests build on: people in every state, leads, company requests,
and a clean table after each test. Imported by name, like `fakes_contracts.py`."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.campaigns.states import Candidate
from rebase_core.models import Company, Freelancer, Login, Signup, User

CAMPAIGN_TABLES = ("campaign_optouts", "campaign_recipients", "campaigns")
PEOPLE_TABLES = ("logins", "comments", "freelancers", "companies", "signups", "users")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in (*CAMPAIGN_TABLES, *PEOPLE_TABLES):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def person(
    session: Session,
    email: str,
    *,
    nome: str = "Ada",
    cv: bool = True,
    tariffa: bool = True,
    posizione: bool = True,
    remoto: bool = True,
    deleted: bool = False,
    role: str = "member",
    logins: int = 0,
) -> Freelancer:
    user = User(email=email, nome=nome, cognome="Lovelace", role=role)
    session.add(user)
    session.flush()
    card = Freelancer(
        user_id=user.id,
        cv_bytes=b"%PDF" if cv else None,
        cv_filename="cv.pdf" if cv else None,
        cv_mime="application/pdf" if cv else None,
        cv_size=4 if cv else None,
        tariffa_giornaliera=Decimal("450") if tariffa else None,
        posizione="Backend developer" if posizione else None,
        remoto="remoto" if remoto else None,
        links=[],
    )
    if deleted:
        card.deleted_at = datetime.now(UTC)
    session.add(card)
    session.flush()
    for _ in range(logins):
        session.add(Login(user_id=user.id))
    session.commit()
    return card


def lead(session: Session, email: str, nome: str | None = "giulia") -> Signup:
    row = Signup(email=email, nome=nome, cognome="Branda")
    session.add(row)
    session.commit()
    return row


def company(session: Session, email: str, *, stato: str = "nuovo", deleted: bool = False) -> Company:
    user = session.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, nome="Ciro", cognome="Aurelio")
        session.add(user)
        session.flush()
    row = Company(
        user_id=user.id,
        nome_azienda="Block Buy SRL",
        figura_richiesta="Developer",
        progetto="ASP.NET Core e React",
        periodo_da=date(2026, 10, 1),
        durata="12 mesi",
        budget_giornaliero=Decimal("320"),
        remoto="remoto",
        numero_risorse=1,
        stato=stato,
    )
    if deleted:
        row.deleted_at = datetime.now(UTC)
    session.add(row)
    session.commit()
    return row


def emails(candidates: list[Candidate]) -> list[str]:
    return [c.email for c in candidates]
```

Then append to `tests/test_campaign_states.py` (imports to the top):

```python
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from campaign_fixtures import clean, company, emails, lead, person  # noqa: F401  (fixture)
from rebase_core.campaigns.states import candidates_for_state, card_state, display_name
from rebase_core.errors import ValidationFailed


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((4, Decimal("1"), "Dev", "remoto"), "completo"),
        ((None, Decimal("1"), "Dev", "remoto"), "manca_cv"),
        ((4, Decimal("1"), None, "remoto"), "scheda"),  # position missing: not «manca_cv»
        ((None, None, "Dev", "remoto"), "scheda"),
        ((None, None, None, None), "scheda"),
    ],
)
def test_card_state_reads_the_four_fields_is_complete_reads(
    fields: tuple[object, object, object, object], expected: str
) -> None:
    assert card_state(*fields) == expected


def test_each_card_lands_in_exactly_one_state(clean: Session) -> None:
    person(clean, "done@studio.it")
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "nopos@studio.it", posizione=False)
    person(clean, "empty-in@studio.it", cv=False, tariffa=False, logins=1)
    person(clean, "empty-new@studio.it", cv=False, tariffa=False)
    person(clean, "gone@studio.it", cv=False, deleted=True)
    assert emails(candidates_for_state(clean, "completo")) == ["done@studio.it"]
    assert emails(candidates_for_state(clean, "manca_cv")) == ["nocv@studio.it"]
    assert sorted(emails(candidates_for_state(clean, "scheda_vuota_nuovi"))) == [
        "empty-new@studio.it",
        "nopos@studio.it",
    ]
    assert emails(candidates_for_state(clean, "scheda_vuota_entrati")) == ["empty-in@studio.it"]


def test_a_lead_whose_address_has_a_card_is_not_a_lead_in_any_case(clean: Session) -> None:
    person(clean, "ada@studio.it")
    lead(clean, "ADA@Studio.it")
    lead(clean, "giulia@studio.it")
    found = candidates_for_state(clean, "lead")
    assert emails(found) == ["giulia@studio.it"]
    assert found[0].tipo == "lead" and found[0].nome == "Giulia"


def test_a_referente_with_two_open_requests_is_one_row(clean: Session) -> None:
    first = company(clean, "info@block-buy.it")
    second = company(clean, "info@block-buy.it", stato="in_corso")
    company(clean, "closed@acme.it", stato="chiuso")
    company(clean, "deleted@acme.it", deleted=True)
    found = candidates_for_state(clean, "azienda_aperta")
    assert emails(found) == ["info@block-buy.it"]
    assert set(found[0].company_ids) == {first.id, second.id}
    assert found[0].tipo == "azienda"


def test_the_pigro_state_waits_for_phase_three(clean: Session) -> None:
    with pytest.raises(ValidationFailed, match="fase Pigro"):
        candidates_for_state(clean, "pigro_vuoto")


@pytest.mark.parametrize(
    ("raw", "shown"),
    [("giulia", "Giulia"), ("STEFANIA", "Stefania"), ("Mauro Leonardo", "Mauro Leonardo"), ("  ", None), (None, None)],
)
def test_display_name_fixes_only_all_lower_or_all_upper(raw: str | None, shown: str | None) -> None:
    assert display_name(raw) == shown
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_states.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'rebase_core.campaigns'`.

- [ ] **Step 3: Write the package and `states.py`**

`campaigns/__init__.py`:

```python
"""Campaigns (P-REB-41): a list of people, one mail, and what it led to.

Design record: `docs/superpowers/specs/2026-09-25-admin-campaigns-design.md`. Phase 1
builds the list, sends the mail and records delivery; phase 2 reads the actions; phase 3
brings PigroCRM in. Nothing here imports an adapter.
"""
```

`campaigns/states.py`:

```python
"""Journey states (spec § 2): where a person stopped between a sign-up and a used card.

Each state is a query over the hub's own tables, evaluated when the list is shown and
again when a mail is about to leave. A card's state reads the four fields
`_is_complete` reads (`schemas.py`): the CV, the rate, the position and the work mode.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from rebase_core.errors import ValidationFailed
from rebase_core.models import Company, Freelancer, Login, Signup, User

ENTITY = "campagna"

JOURNEY_STATES: dict[str, str] = {
    "lead": "Lead senza profilo",
    "scheda_vuota_nuovi": "Scheda vuota, mai entrati",
    "scheda_vuota_entrati": "Scheda vuota, già entrati",
    "manca_cv": "Manca solo il CV",
    "completo": "Profilo completo",
    "pigro_vuoto": "Spazio Pigro vuoto",
    "azienda_aperta": "Azienda con richiesta aperta",
}
PHASE_ONE_STATES = tuple(key for key in JOURNEY_STATES if key != "pigro_vuoto")
OPEN_COMPANY_STATES = ("nuovo", "contattato", "in_corso")
PIGRO_LATER = "Lo spazio Pigro arriva con la fase Pigro delle campagne."

CardState = Literal["completo", "manca_cv", "scheda"]


@dataclass(frozen=True)
class Candidate:
    """One person a list may reach, before any exclusion. `email` is lowercase."""

    email: str
    nome: str | None
    tipo: str
    user_id: UUID | None = None
    freelancer_id: UUID | None = None
    signup_id: UUID | None = None
    company_ids: tuple[UUID, ...] = ()
    pigro_slugs: tuple[str, ...] = ()


def display_name(nome: str | None) -> str | None:
    """The name as a greeting reads it: stripped, and «giulia» or «STEFANIA» put in
    title case, since that is how the form stored what somebody typed. A name already
    mixed-case is theirs and stays."""
    value = (nome or "").strip()
    if not value:
        return None
    if value.islower() or value.isupper():
        return value.title()
    return value


def card_state(
    cv_size: int | None,
    tariffa: Decimal | None,
    posizione: str | None,
    remoto: str | None,
) -> CardState:
    missing_cv = cv_size is None
    missing_other = tariffa is None or posizione is None or remoto is None
    if not missing_cv and not missing_other:
        return "completo"
    if missing_cv and not missing_other:
        return "manca_cv"
    return "scheda"


def candidates_for_state(session: Session, stato: str) -> list[Candidate]:
    if stato == "lead":
        return _leads(session)
    if stato in ("scheda_vuota_nuovi", "scheda_vuota_entrati", "manca_cv", "completo"):
        return _cards(session, stato)
    if stato == "azienda_aperta":
        return _companies(session)
    if stato == "pigro_vuoto":
        raise ValidationFailed(ENTITY, "stato_percorso", PIGRO_LATER)
    raise ValidationFailed(ENTITY, "stato_percorso", f"Stato sconosciuto: {stato}.")


def _leads(session: Session) -> list[Candidate]:
    """A sign-up whose address has no card at all, the same anti-join Talenti runs
    (`talenti._card_emails`): a card deleted by an admin still says «this person is not
    a lead to chase»."""
    card_emails = select(func.lower(User.email)).join(Freelancer, Freelancer.user_id == User.id)
    signup_user = select(User.id).where(func.lower(User.email) == func.lower(Signup.email))
    rows = session.execute(
        select(Signup, signup_user.scalar_subquery())
        .where(func.lower(Signup.email).not_in(card_emails))
        .order_by(Signup.created_at, Signup.id)
    ).all()
    return [
        Candidate(
            email=row.email.lower(),
            nome=display_name(row.nome),
            tipo="lead",
            user_id=user_id,
            signup_id=row.id,
        )
        for row, user_id in rows
    ]


def _cards(session: Session, stato: str) -> list[Candidate]:
    entered = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
    rows = session.execute(
        select(Freelancer, User, entered.label("entrato"))
        .join(User, User.id == Freelancer.user_id)
        .where(Freelancer.deleted_at.is_(None))
        .order_by(Freelancer.created_at, Freelancer.id)
    ).all()
    found: list[Candidate] = []
    for card, user, entrato in rows:
        state = card_state(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
        key = state if state != "scheda" else ("scheda_vuota_entrati" if entrato else "scheda_vuota_nuovi")
        if key == stato:
            found.append(
                Candidate(
                    email=user.email.lower(),
                    nome=display_name(user.nome),
                    tipo="freelancer",
                    user_id=user.id,
                    freelancer_id=card.id,
                )
            )
    return found


def _companies(session: Session) -> list[Candidate]:
    rows = session.execute(
        select(Company, User)
        .join(User, User.id == Company.user_id)
        .where(Company.deleted_at.is_(None), Company.stato.in_(OPEN_COMPANY_STATES))
        .order_by(Company.created_at, Company.id)
    ).all()
    by_user: dict[UUID, Candidate] = {}
    for request, user in rows:
        known = by_user.get(user.id)
        ids = (*known.company_ids, request.id) if known else (request.id,)
        by_user[user.id] = Candidate(
            email=user.email.lower(),
            nome=display_name(user.nome),
            tipo="azienda",
            user_id=user.id,
            company_ids=ids,
        )
    return list(by_user.values())
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_states.py`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/__init__.py \
  projects/hub/packages/core/src/rebase_core/campaigns/states.py \
  projects/hub/packages/core/tests/test_campaign_states.py
git commit -m "feat(hub): the journey states a campaign's list can start from" -m "REB-464."
```

---

## Group B — Build a campaign's list: templates, exclusions, the action already done

### Task 4: The state templates

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/templates.py`
- Test: `projects/hub/packages/core/tests/test_campaign_audience.py` (created here)

**Interfaces:**
- Produces: `@dataclass(frozen=True) Template(stato_percorso: str, etichetta: str,
  oggetto: str, testo: str, bottone_testo: str, bottone_meta: str, azione: str)`
  and `STATE_TEMPLATES: dict[str, Template]`, keyed by every `PHASE_ONE_STATES` entry.

- [ ] **Step 1: Write the failing test**

```python
"""A campaign's list: templates, candidates, exclusions, the action already done."""

from rebase_core.campaigns.states import JOURNEY_STATES, PHASE_ONE_STATES
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.models import CAMPAIGN_ACTIONS, CAMPAIGN_DESTINATIONS


def test_every_phase_one_state_has_a_template_that_fits_the_columns() -> None:
    assert set(STATE_TEMPLATES) == set(PHASE_ONE_STATES)
    for key, template in STATE_TEMPLATES.items():
        assert template.etichetta == JOURNEY_STATES[key]
        assert template.azione in CAMPAIGN_ACTIONS and template.azione != "pigro_cliente"
        assert template.bottone_meta in CAMPAIGN_DESTINATIONS and template.bottone_meta != "pigro"
        assert template.testo.startswith("Ciao {nome},")
        assert len(template.oggetto) <= 200 and len(template.bottone_testo) <= 60
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.templates`).

- [ ] **Step 3: Write `templates.py`**

These are the second wave's texts (REB-425, sent 25/09), rewritten with `{nome}` and
with the button after the text, since `render` puts it there:

```python
"""The mail each journey state starts from: the second outreach wave's (REB-425, 25/09),
which the admin edits per campaign. Only the starting point lives here; a campaign keeps
its own copy."""

from dataclasses import dataclass

from rebase_core.campaigns.states import JOURNEY_STATES


@dataclass(frozen=True)
class Template:
    stato_percorso: str
    etichetta: str
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str


def _t(key: str, oggetto: str, testo: str, bottone: str, meta: str, azione: str) -> Template:
    return Template(key, JOURNEY_STATES[key], oggetto, testo, bottone, meta, azione)


STATE_TEMPLATES: dict[str, Template] = {
    t.stato_percorso: t
    for t in (
        _t(
            "lead",
            "Chiudo la tua iscrizione a rebase?",
            "Ciao {nome},\n\nhai lasciato la tua email su letsrebase.com per entrare in rebase, "
            "e il profilo non c'è ancora. Te lo chiedo direttamente: ti interessa ancora?\n\n"
            "Se sì, bastano cinque minuti, dal bottone qui sotto.\n\n"
            "Se no, rispondi «no» e non ti scrivo più.",
            "Compila il profilo",
            "wizard",
            "profilo_creato",
        ),
        _t(
            "scheda_vuota_nuovi",
            "La tua scheda su rebase, la compilo io?",
            "Ciao {nome},\n\nla scheda che ti abbiamo aperto su rebase è ancora senza tariffa, "
            "modalità di lavoro e CV, e così non posso proporti a nessuna azienda.\n\n"
            "Facciamo prima così: rispondi a questa mail con il CV in allegato e quanto chiedi "
            "a giornata. Al resto penso io. Se preferisci farlo da te, entri con la sola email "
            "dal bottone qui sotto.\n\n"
            "Se invece non vuoi comparire, rispondi «no» e cancello la scheda.",
            "Completa la scheda",
            "area",
            "scheda_completa",
        ),
        _t(
            "scheda_vuota_entrati",
            "La tua scheda su rebase è rimasta vuota",
            "Ciao {nome},\n\nho visto che hai aperto la tua area su rebase, ma la scheda è "
            "rimasta com'era: senza tariffa, modalità di lavoro e CV. Qualcosa non ha "
            "funzionato, o non era chiaro? Dimmelo in una riga rispondendo a questa mail.\n\n"
            "Se vuoi riprovare, entri con la sola email dal bottone qui sotto. Oppure rispondi "
            "con il CV e quanto chiedi a giornata, e la compilo io.",
            "Completa la scheda",
            "area",
            "scheda_completa",
        ),
        _t(
            "manca_cv",
            "Manca solo il CV",
            "Ciao {nome},\n\nil tuo profilo su rebase è quasi pronto: manca solo il CV. È la "
            "prima cosa che un'azienda ci chiede quando le proponiamo una persona, e senza non "
            "posso proporti.\n\nLo carichi dalla tua area, entri con la sola email dal bottone "
            "qui sotto. Va bene anche il PDF esportato da LinkedIn, oppure rispondi a questa "
            "mail con il CV in allegato e lo carico io.",
            "Carica il CV",
            "area",
            "cv",
        ),
        _t(
            "completo",
            "Il tuo profilo su rebase è completo",
            "Ciao {nome},\n\nil tuo profilo su rebase è completo, grazie. Quando arriva un "
            "progetto adatto a te, ti scrivo io.\n\nIntanto una domanda, rispondi anche in una "
            "riga: da quando hai spazio per un nuovo progetto, e per quanti giorni a settimana?",
            "Entra nella tua area",
            "area",
            "entrato",
        ),
        _t(
            "azienda_aperta",
            "La tua richiesta su rebase è ancora aperta?",
            "Ciao {nome},\n\nti riscrivo per la tua richiesta su rebase. È ancora aperta?\n\n"
            "Se è cambiato qualcosa, quante persone, da quando, con che budget, la aggiorni "
            "dalla tua area con il bottone qui sotto. Se è ancora così, mi bastano dieci minuti "
            "al telefono: rispondi con un numero e un momento comodo e ti chiamo io.",
            "Aggiorna la richiesta",
            "richiesta",
            "richiesta_aggiornata",
        ),
    )
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/templates.py projects/hub/packages/core/tests/test_campaign_audience.py
git commit -m "feat(hub): the mail each journey state starts from" -m "REB-465."
```

### Task 5: The snapshot and «has already done it»

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/actions.py`
- Test: `projects/hub/packages/core/tests/test_campaign_audience.py` (append)

**Interfaces:**
- Consumes: `Candidate` and `card_state` from Task 3, and `CampaignRecipient` from
  Task 1.
- Produces:
  - `snapshot(session, candidate, now) -> dict[str, Any]`, with the keys `t` (ISO),
    `ha_scheda`, `ha_cv`, `completa` and `richieste` (`{company_id: updated_at ISO}`).
  - `done_at(session, recipient, azione) -> datetime | None`.
  - `CV_COMMENT_PREFIX = "CV caricato dalla persona"`.

- [ ] **Step 1: Write the failing tests**

Add `T0 = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)` to `tests/campaign_fixtures.py`.
Then append to `tests/test_campaign_audience.py`, imports to the top:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from campaign_fixtures import T0, clean, company, lead, person  # noqa: F401  (fixture)
from rebase_core.campaigns.actions import done_at, snapshot
from rebase_core.campaigns.states import candidates_for_state
from rebase_core.comments import CommentService
from rebase_core.models import CampaignRecipient, Login


def recipient_for(session: Session, candidate, prima: dict) -> CampaignRecipient:  # type: ignore[no-untyped-def]
    return CampaignRecipient(
        email=candidate.email,
        tipo=candidate.tipo,
        user_id=candidate.user_id,
        freelancer_id=candidate.freelancer_id,
        signup_id=candidate.signup_id,
        codice="00000000",
        prima=prima,
        disiscrizione_token="t",
    )


def test_a_cv_uploaded_after_the_snapshot_counts_and_one_from_before_does_not(clean: Session) -> None:
    card = person(clean, "nocv@studio.it", cv=False)
    candidate = candidates_for_state(clean, "manca_cv")[0]
    prima = snapshot(clean, candidate, T0)
    assert prima["ha_cv"] is False and prima["ha_scheda"] is True
    row = recipient_for(clean, candidate, prima)
    assert done_at(clean, row, "cv") is None
    card.cv_size, card.cv_filename, card.cv_bytes = 4, "cv.pdf", b"%PDF"
    clean.commit()
    CommentService(clean).add("freelancer", card.id, "CV caricato dalla persona", "Ada Lovelace")
    assert done_at(clean, row, "cv") is not None


def test_a_login_after_the_snapshot_is_entered(clean: Session) -> None:
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "entrato") is None
    clean.add(Login(user_id=candidate.user_id, logged_at=T0 + timedelta(minutes=5)))
    clean.commit()
    assert done_at(clean, row, "entrato") == T0 + timedelta(minutes=5)


def test_a_lead_who_makes_a_card_has_created_a_profile(clean: Session) -> None:
    lead(clean, "giulia@studio.it")
    candidate = candidates_for_state(clean, "lead")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0 - timedelta(days=1)))
    assert done_at(clean, row, "profilo_creato") is None
    person(clean, "giulia@studio.it", nome="Giulia")
    assert done_at(clean, row, "profilo_creato") is not None


def test_any_of_a_referentes_open_requests_updated_counts(clean: Session) -> None:
    first = company(clean, "info@block-buy.it")
    company(clean, "info@block-buy.it", stato="contattato")
    candidate = candidates_for_state(clean, "azienda_aperta")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, datetime.now(UTC)))
    assert done_at(clean, row, "richiesta_aggiornata") is None
    first.durata = "18 mesi"
    clean.commit()
    assert done_at(clean, row, "richiesta_aggiornata") is not None


def test_the_pigro_action_is_never_done_in_phase_one(clean: Session) -> None:
    person(clean, "done@studio.it")
    candidate = candidates_for_state(clean, "completo")[0]
    row = recipient_for(clean, candidate, snapshot(clean, candidate, T0))
    assert done_at(clean, row, "pigro_cliente") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.actions`).

- [ ] **Step 3: Write `actions.py`**

```python
"""What an action is measured against, and whether a person has done it since (spec
§ 6.2). Phase 1 uses `done_at` to skip a mail that would ask for something already done
(spec § 5.3); phase 2 stamps its answer on the row."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.states import Candidate, card_state
from rebase_core.models import CampaignRecipient, Comment, Company, Freelancer, Login, User

# What `MemberService.replace_cv` writes on a card's first CV (`members.py:303`).
CV_COMMENT_PREFIX = "CV caricato dalla persona"


def _card(session: Session, email: str) -> Freelancer | None:
    return session.scalar(
        select(Freelancer)
        .join(User, User.id == Freelancer.user_id)
        .where(func.lower(User.email) == email.lower(), Freelancer.deleted_at.is_(None))
    )


def _complete(card: Freelancer) -> bool:
    return (
        card_state(card.cv_size, card.tariffa_giornaliera, card.posizione, card.remoto)
        == "completo"
    )


def snapshot(session: Session, candidate: Candidate, now: datetime) -> dict[str, Any]:
    card = _card(session, candidate.email)
    richieste: dict[str, str] = {}
    if candidate.company_ids:
        rows = session.execute(
            select(Company.id, Company.updated_at).where(Company.id.in_(candidate.company_ids))
        ).all()
        richieste = {str(company_id): updated.isoformat() for company_id, updated in rows}
    return {
        "t": now.isoformat(),
        "ha_scheda": card is not None,
        "ha_cv": card is not None and card.cv_size is not None,
        "completa": card is not None and _complete(card),
        "richieste": richieste,
    }


def done_at(session: Session, recipient: CampaignRecipient, azione: str) -> datetime | None:
    prima = recipient.prima or {}
    since = datetime.fromisoformat(prima["t"])
    if azione == "entrato":
        user_id = recipient.user_id or session.scalar(
            select(User.id).where(func.lower(User.email) == recipient.email)
        )
        if user_id is None:
            return None
        return session.scalar(
            select(func.min(Login.logged_at)).where(Login.user_id == user_id, Login.logged_at > since)
        )
    if azione in ("cv", "scheda_completa", "profilo_creato"):
        card = _card(session, recipient.email)
        if card is None:
            return None
        if azione == "profilo_creato":
            return card.created_at if not prima.get("ha_scheda") and card.created_at > since else None
        if azione == "cv":
            if prima.get("ha_cv") or card.cv_size is None:
                return None
            commented = session.scalar(
                select(func.min(Comment.created_at)).where(
                    Comment.entity_type == "freelancer",
                    Comment.entity_id == card.id,
                    Comment.testo.startswith(CV_COMMENT_PREFIX),
                    Comment.created_at > since,
                )
            )
            return commented or card.updated_at
        return card.updated_at if not prima.get("completa") and _complete(card) else None
    if azione == "richiesta_aggiornata":
        before = prima.get("richieste") or {}
        moments: list[datetime] = []
        for company_id, was in before.items():
            updated = session.scalar(select(Company.updated_at).where(Company.id == company_id))
            if updated is not None and updated > max(datetime.fromisoformat(was), since):
                moments.append(updated)
        return min(moments) if moments else None
    return None  # `pigro_cliente`: phase 3 (spec § 6.3)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/actions.py projects/hub/packages/core/tests/test_campaign_audience.py
git commit -m "feat(hub): the state a campaign's action is measured against" -m "REB-465."
```

### Task 6: Candidates from a state or filters, and the exclusions

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/schemas.py` (the filter models only; Task 9 adds the rest)
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/audience.py`
- Test: `projects/hub/packages/core/tests/test_campaign_audience.py` (append)

**Interfaces:**
- Consumes: `candidates_for_state` and `Candidate` from Task 3;
  `TalentiService.list_recent` (`talenti.py:191`) and `CompanyService.list_recent`
  (`companies.py:160`), paged by `next_cursor`.
- Produces:
  - `TalentiFiltri`, `AziendeFiltri` and `Filtri` (a discriminated union on
    `lista: "talenti" | "aziende"`).
  - `candidates(session, campaign) -> list[Candidate]`.
  - `exclusions(session, emails: list[str], *, campaign_id: UUID | None, now: datetime, gap_days: int) -> dict[str, str]`.
  - `@dataclass(frozen=True) AudienceRow(candidate: Candidate, escluso: str | None)`.
  - `build_audience(session, campaign, *, now, gap_days) -> list[AudienceRow]`.
  - The reason constants `REASON_ADMIN`, `REASON_NEVER`, `REASON_OPTOUT`,
    `REASON_BOUNCED`, `REASON_RECENT`, `REASON_DONE`, `REASON_NOT_LISTED` and
    `REASON_CANCELLED`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/campaign_fixtures.py` (it needs `Campaign` from `rebase_core.models`):

```python
ADMIN_EMAIL = "ivan@rebase.it"


def admin(session: Session) -> User:
    """The admin every campaign test acts as: one row, whoever asks first makes it."""
    user = session.query(User).filter(User.email == ADMIN_EMAIL).one_or_none()
    if user is None:
        user = User(email=ADMIN_EMAIL, nome="Ivan", cognome="Sala", role="admin")
        session.add(user)
        session.commit()
    return user


def campaign_row(session: Session, **fields: object) -> Campaign:
    """A campaign row written straight to the table, for the tests below the service."""
    values: dict[str, object] = {
        "created_by": admin(session).id,
        "nome": "Prova",
        "slug": f"c-prova-{session.query(Campaign).count()}",
        "fonte": "stato",
        "stato_percorso": "manca_cv",
        "oggetto": "o",
        "testo": "Ciao {nome},",
        "bottone_testo": "Vai",
        "bottone_meta": "area",
        "azione": "cv",
        "contenuto_at": T0,
    }
    values.update(fields)
    campaign = Campaign(**values)
    session.add(campaign)
    session.commit()
    return campaign
```

Then append to `tests/test_campaign_audience.py`, imports to the top:

```python
from campaign_fixtures import campaign_row
from rebase_core.campaigns.audience import (
    REASON_ADMIN,
    REASON_BOUNCED,
    REASON_NEVER,
    build_audience,
    candidates,
    exclusions,
)
from rebase_core.models import CampaignOptout


def test_filters_reuse_talenti_and_merge_one_person_across_cases(clean: Session) -> None:
    person(clean, "ada@studio.it", tariffa=False)
    lead(clean, "giulia@studio.it")
    campaign = campaign_row(clean, fonte="filtri", stato_percorso=None, filtri={"lista": "talenti", "has_cv": True})
    assert [c.email for c in candidates(clean, campaign)] == ["ada@studio.it"]
    campaign.filtri = {"lista": "talenti", "stato": "lead"}
    clean.commit()
    assert [c.email for c in candidates(clean, campaign)] == ["giulia@studio.it"]


def test_every_exclusion_names_its_reason(clean: Session) -> None:
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "gone@studio.it", cv=False)
    person(clean, "never@studio.it", cv=False)
    person(clean, "bounce@studio.it", cv=False)
    person(clean, "recent@studio.it", cv=False)
    person(clean, "ok@studio.it", cv=False)
    clean.add(CampaignOptout(email="gone@studio.it", fonte="link"))
    clean.add(CampaignOptout(email="never@studio.it", fonte="admin"))
    earlier = campaign_row(clean)
    clean.add(CampaignRecipient(campaign_id=earlier.id, email="bounce@studio.it", tipo="freelancer",
                                codice="1", prima={}, disiscrizione_token="b", stato="inviata",
                                inviata_at=T0 - timedelta(days=30), rimbalzata_at=T0 - timedelta(days=30)))
    clean.add(CampaignRecipient(campaign_id=earlier.id, email="recent@studio.it", tipo="freelancer",
                                codice="2", prima={}, disiscrizione_token="r", stato="inviata",
                                inviata_at=T0 - timedelta(days=1)))
    clean.commit()
    current = campaign_row(clean)
    reasons = exclusions(
        clean,
        ["boss@rebase.it", "gone@studio.it", "never@studio.it", "bounce@studio.it", "recent@studio.it", "ok@studio.it"],
        campaign_id=current.id,
        now=T0,
        gap_days=3,
    )
    assert reasons["boss@rebase.it"] == REASON_ADMIN
    assert reasons["gone@studio.it"] == "si è disiscritto"
    assert reasons["never@studio.it"] == REASON_NEVER
    assert reasons["bounce@studio.it"] == REASON_BOUNCED
    assert reasons["recent@studio.it"].startswith("ha ricevuto un'altra campagna il ")
    assert "ok@studio.it" not in reasons


def test_the_audience_lists_everyone_and_greys_out_the_excluded(clean: Session) -> None:
    person(clean, "boss@rebase.it", role="admin", cv=False)
    person(clean, "ok@studio.it", cv=False)
    campaign = campaign_row(clean)
    rows = build_audience(clean, campaign, now=T0, gap_days=3)
    assert [(r.candidate.email, r.escluso) for r in rows] == [
        ("boss@rebase.it", REASON_ADMIN),
        ("ok@studio.it", None),
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.audience`).

- [ ] **Step 3: Write the filter schemas**

`campaigns/schemas.py`, first part:

```python
"""Every shape campaigns read and answer, API and service alike."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from rebase_core.search import SEARCH_MAX_LENGTH


class TalentiFiltri(BaseModel):
    """The Talenti list's own filters (`GET /api/hub/talent`, `talenti.py:191`), stored
    on the campaign and replayed through `TalentiService` when the list is shown and when
    a mail is about to leave."""

    lista: Literal["talenti"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    posizione: str | None = Field(default=None, max_length=160)
    remoto: str | None = Field(default=None, max_length=10)
    tariffa_min: Decimal | None = None
    tariffa_max: Decimal | None = None
    origine: str | None = Field(default=None, max_length=40)
    utm_source: str | None = Field(default=None, max_length=200)
    has_cv: bool | None = None
    con_accessi: bool | None = None
    creato_da: datetime | None = None
    creato_a: datetime | None = None


class AziendeFiltri(BaseModel):
    """The company list's own filters (`companies.py:160`)."""

    lista: Literal["aziende"]
    stato: str | None = Field(default=None, max_length=20)
    q: str | None = Field(default=None, max_length=SEARCH_MAX_LENGTH)
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    periodo_da: date | None = None
    origine: str | None = Field(default=None, max_length=40)
    creato_da: datetime | None = None
    creato_a: datetime | None = None


Filtri = Annotated[TalentiFiltri | AziendeFiltri, Field(discriminator="lista")]
```

- [ ] **Step 4: Write `audience.py`**

```python
"""Who a campaign reaches (spec § 2, § 5.3): the candidates of a state or of filters, one
row per lowercase address, and the reasons someone is left out."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import AziendeFiltri, Filtri, TalentiFiltri
from rebase_core.campaigns.states import (
    ENTITY,
    Candidate,
    candidates_for_state,
    display_name,
)
from rebase_core.companies import CompanyService
from rebase_core.errors import ValidationFailed
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient, Company, Freelancer, User
from rebase_core.talenti import LIST_LIMIT_MAX, TalentiService

ROME = ZoneInfo("Europe/Rome")
REASON_ADMIN = "amministratore"
REASON_NEVER = "«Non scrivere mai»"
REASON_OPTOUT = "si è disiscritto"
REASON_BOUNCED = "indirizzo rimbalzato"
REASON_RECENT = "ha ricevuto un'altra campagna il {data}"
REASON_DONE = "ha già fatto l'azione"
REASON_NOT_LISTED = "non più in lista"
REASON_CANCELLED = "campagna annullata"
LISTA_LATER = "Le liste fisse arrivano con «Riscrivi a chi non ha fatto niente»."

_FILTRI: TypeAdapter[TalentiFiltri | AziendeFiltri] = TypeAdapter(Filtri)


@dataclass(frozen=True)
class AudienceRow:
    candidate: Candidate
    escluso: str | None


def candidates(session: Session, campaign: Campaign) -> list[Candidate]:
    if campaign.fonte == "stato":
        found = candidates_for_state(session, campaign.stato_percorso or "")
    elif campaign.fonte == "filtri":
        found = _filtered(session, campaign.filtri or {})
    else:
        raise ValidationFailed(ENTITY, "fonte", LISTA_LATER)
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in found:
        if candidate.email not in seen:
            seen.add(candidate.email)
            unique.append(candidate)
    return unique


def _filtered(session: Session, raw: dict[str, Any]) -> list[Candidate]:
    filtri = _FILTRI.validate_python(raw)
    params = filtri.model_dump(exclude={"lista"}, exclude_none=True)
    if isinstance(filtri, TalentiFiltri):
        return _from_talenti(session, params)
    return _from_aziende(session, params)


def _from_talenti(session: Session, params: dict[str, Any]) -> list[Candidate]:
    service = TalentiService(session)
    rows, cursor = [], None
    while True:
        page = service.list_recent(limit=LIST_LIMIT_MAX, cursor=cursor, **params)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    card_ids = [row.id for row in rows if row.stato != "lead"]
    users = dict(
        session.execute(
            select(Freelancer.id, User).join(User, User.id == Freelancer.user_id).where(Freelancer.id.in_(card_ids))
        ).all()
    ) if card_ids else {}
    found: list[Candidate] = []
    for row in rows:
        if row.stato == "lead":
            found.append(Candidate(email=row.email.lower(), nome=display_name(row.nome), tipo="lead", signup_id=row.id))
        elif row.id in users:
            user = users[row.id]
            found.append(Candidate(email=user.email.lower(), nome=display_name(user.nome), tipo="freelancer",
                                   user_id=user.id, freelancer_id=row.id))
    return found


def _from_aziende(session: Session, params: dict[str, Any]) -> list[Candidate]:
    service = CompanyService(session)
    rows, cursor = [], None
    while True:
        page = service.list_recent(limit=LIST_LIMIT_MAX, cursor=cursor, **params)
        rows.extend(page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    owners = dict(session.execute(select(Company.id, Company.user_id).where(Company.id.in_([r.id for r in rows]))).all()) if rows else {}
    by_email: dict[str, Candidate] = {}
    for row in rows:
        email = row.email.lower()
        known = by_email.get(email)
        by_email[email] = Candidate(
            email=email,
            nome=display_name(row.referente.split(" ")[0] if row.referente else None),
            tipo="azienda",
            user_id=owners.get(row.id),
            company_ids=(*(known.company_ids if known else ()), row.id),
        )
    return list(by_email.values())


def exclusions(
    session: Session,
    emails: list[str],
    *,
    campaign_id: UUID | None,
    now: datetime,
    gap_days: int,
) -> dict[str, str]:
    if not emails:
        return {}
    admins = set(session.scalars(select(func.lower(User.email)).where(User.role == "admin", func.lower(User.email).in_(emails))))
    optouts = dict(session.execute(select(CampaignOptout.email, CampaignOptout.fonte).where(CampaignOptout.email.in_(emails))).all())
    bounced = set(session.scalars(select(CampaignRecipient.email).where(CampaignRecipient.email.in_(emails), CampaignRecipient.rimbalzata_at.is_not(None))))
    recent_q = (
        select(CampaignRecipient.email, func.max(CampaignRecipient.inviata_at))
        .where(
            CampaignRecipient.email.in_(emails),
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.inviata_at >= now - timedelta(days=gap_days),
        )
        .group_by(CampaignRecipient.email)
    )
    if campaign_id is not None:
        recent_q = recent_q.where(CampaignRecipient.campaign_id != campaign_id)
    recent = dict(session.execute(recent_q).all())
    reasons: dict[str, str] = {}
    for email in emails:
        if email in admins:
            reasons[email] = REASON_ADMIN
        elif email in optouts:
            reasons[email] = REASON_NEVER if optouts[email] == "admin" else REASON_OPTOUT
        elif email in bounced:
            reasons[email] = REASON_BOUNCED
        elif email in recent:
            reasons[email] = REASON_RECENT.format(data=recent[email].astimezone(ROME).strftime("%d/%m"))
    return reasons


def build_audience(session: Session, campaign: Campaign, *, now: datetime, gap_days: int) -> list[AudienceRow]:
    found = candidates(session, campaign)
    reasons = exclusions(session, [c.email for c in found], campaign_id=campaign.id, now=now, gap_days=gap_days)
    return [AudienceRow(candidate, reasons.get(candidate.email)) for candidate in found]
```

`CompanyRead.referente` is the referente's full name (`schemas.py:771`). Before using
it for the greeting's first name, check its shape in `companies.py`'s `_read`. If it is
«Nome Cognome», the first token is right; otherwise read the linked `users.nome`
through `owners`.

- [ ] **Step 5: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_audience.py projects/hub/packages/core/tests/test_campaign_states.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/schemas.py \
  projects/hub/packages/core/src/rebase_core/campaigns/audience.py \
  projects/hub/packages/core/tests/test_campaign_audience.py
git commit -m "feat(hub): a campaign's list from a state or filters, with who is left out and why" -m "REB-465."
```

---

## Group C — Render a campaign mail and send it through Resend idempotently

### Task 7: The mail

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/render.py`
- Test: `projects/hub/packages/core/tests/test_campaign_render.py`

**Interfaces:**
- Consumes: `Mail` and the private helpers `_button`, `_frame`, `_quiet_link` and
  `INK_QUIET` from `rebase_core.mail` (`mail.py:131-216`). The September waves' script
  used the same helpers.
- Produces:
  - `@dataclass(frozen=True) RenderTarget(email: str, nome: str | None, codice: str, token: str, recipient_id: str | None = None)`.
  - `@dataclass(frozen=True) RenderedMail(mail: Mail, headers: dict[str, str], tags: dict[str, str])`.
  - `render(campaign, target, settings, *, test: bool = False) -> RenderedMail`.
  - `destination(campaign, settings) -> str`.
  - `unsubscribe_urls(token, settings) -> tuple[str, str]` (page, api).
  - `personalise(testo, nome) -> str`.
  - `person_code(email) -> str`: 8 hex characters of the address's sha1, the waves'
    `utm_term`.

- [ ] **Step 1: Write the failing tests**

```python
"""A campaign mail: the text, the tracked button, the unsubscribe, the tags."""

from datetime import UTC, datetime

import pytest

from rebase_core.campaigns.render import (
    RenderTarget,
    destination,
    person_code,
    personalise,
    render,
    unsubscribe_urls,
)
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.models import Campaign

SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]


def campaign(**fields: object) -> Campaign:
    values: dict[str, object] = {
        "nome": "Manca il CV",
        "slug": "c-2026-09-25-manca-cv",
        "fonte": "stato",
        "stato_percorso": "manca_cv",
        "oggetto": "Manca solo il CV",
        "testo": "Ciao {nome},\n\nmanca il <CV> & poco altro.\nDavvero.",
        "bottone_testo": "Carica il CV",
        "bottone_meta": "area",
        "azione": "cv",
        "contenuto_at": datetime(2026, 9, 25, tzinfo=UTC),
    }
    values.update(fields)
    return Campaign(**values)


TARGET = RenderTarget(email="ada@studio.it", nome="Ada", codice="ab12cd34", token="tok", recipient_id="r-1")


def test_the_name_goes_in_and_a_missing_one_leaves_a_clean_greeting() -> None:
    assert personalise("Ciao {nome}, come va?", "Ada") == "Ciao Ada, come va?"
    assert personalise("Ciao {nome}, come va?", None) == "Ciao, come va?"


def test_the_button_is_tracked_with_the_campaign_the_action_and_the_person() -> None:
    mail = render(campaign(), TARGET, SETTINGS).mail
    url = (
        "https://letsrebase.com/hub/login?utm_source=email&utm_medium=campagna"
        "&utm_campaign=c-2026-09-25-manca-cv&utm_content=cv&utm_term=ab12cd34"
    )
    assert url in mail.text
    assert url.replace("&", "&amp;") in (mail.html or "")


def test_text_is_escaped_in_html_and_kept_in_text() -> None:
    mail = render(campaign(), TARGET, SETTINGS).mail
    assert "manca il <CV> & poco altro.\nDavvero." in mail.text
    assert "manca il &lt;CV&gt; &amp; poco altro.<br>Davvero." in (mail.html or "")
    assert "<CV>" not in (mail.html or "")


def test_every_mail_carries_the_one_click_unsubscribe() -> None:
    rendered = render(campaign(), TARGET, SETTINGS)
    page, api = unsubscribe_urls("tok", SETTINGS)
    assert page == "https://letsrebase.com/hub/disiscrizione?t=tok"
    assert api == "https://letsrebase.com/api/hub/campagne/disiscrizione?t=tok"
    assert rendered.headers == {
        "List-Unsubscribe": f"<{api}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    assert page in rendered.mail.text


def test_tags_name_the_campaign_the_action_the_kind_and_the_row() -> None:
    assert render(campaign(), TARGET, SETTINGS).tags == {
        "campaign": "c-2026-09-25-manca-cv",
        "azione": "cv",
        "kind": "real",
        "r": "r-1",
    }
    test = render(campaign(), RenderTarget("ivan@rebase.it", "Ivan", "00", "prova"), SETTINGS, test=True)
    assert test.tags["kind"] == "test" and "r" not in test.tags
    assert test.mail.subject == "[prova] Manca solo il CV"


@pytest.mark.parametrize(("meta", "path"), [("area", "/login"), ("richiesta", "/login"), ("wizard", "/freelance")])
def test_each_destination_is_a_hub_page(meta: str, path: str) -> None:
    assert destination(campaign(bottone_meta=meta), SETTINGS) == f"https://letsrebase.com/hub{path}"


def test_the_pigro_destination_waits_for_phase_three() -> None:
    with pytest.raises(ValidationFailed, match="fase Pigro"):
        destination(campaign(bottone_meta="pigro"), SETTINGS)


def test_the_person_code_is_the_waves_code() -> None:
    assert person_code("Ada@Studio.it") == person_code("ada@studio.it")
    assert len(person_code("ada@studio.it")) == 8


def test_a_render_is_byte_identical_twice() -> None:
    """Resend's Idempotency-Key refuses a retry whose payload changed (409
    `invalid_idempotent_request`): the same row must render the same bytes."""
    assert render(campaign(), TARGET, SETTINGS) == render(campaign(), TARGET, SETTINGS)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_render.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.render`).

- [ ] **Step 3: Write `render.py`**

```python
"""A campaign mail (spec § 5.1): the admin's text, the tracked button, the signature, the
unsubscribe line, in the hub's own frame. Deterministic on purpose: a retry must send
Resend the same bytes under the same Idempotency-Key."""

import hashlib
import html
import re
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from rebase_core.campaigns.states import ENTITY, PIGRO_LATER
from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.mail import INK_QUIET, Mail, _button, _frame, _quiet_link
from rebase_core.models import Campaign

_PARA = 'style="margin:0 0 24px 0;"'
_AFTER = 'style="margin:24px 0 0 0;"'
_FINE = f'style="margin:24px 0 0 0;font-size:13px;line-height:1.5;color:{INK_QUIET};word-break:break-all;"'
FALLBACK = "Se il bottone non si apre, copia questo indirizzo nel browser:"
UNSUBSCRIBE_LINE = "Non vuoi più ricevere queste mail?"


@dataclass(frozen=True)
class RenderTarget:
    email: str
    nome: str | None
    codice: str
    token: str
    recipient_id: str | None = None


@dataclass(frozen=True)
class RenderedMail:
    mail: Mail
    headers: dict[str, str]
    tags: dict[str, str]


def person_code(email: str) -> str:
    return hashlib.sha1(email.lower().encode()).hexdigest()[:8]


def personalise(testo: str, nome: str | None) -> str:
    if nome:
        return testo.replace("{nome}", nome)
    return testo.replace(" {nome}", "").replace("{nome}", "")


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def destination(campaign: Campaign, settings: Settings) -> str:
    base = settings.hub_url.rstrip("/")
    if campaign.bottone_meta in ("area", "richiesta"):
        return f"{base}/login"
    if campaign.bottone_meta == "wizard":
        return f"{base}/freelance"
    raise ValidationFailed(ENTITY, "bottone_meta", PIGRO_LATER)


def _tracked(url: str, campaign: Campaign, codice: str) -> str:
    query = urlencode(
        {
            "utm_source": "email",
            "utm_medium": "campagna",
            "utm_campaign": campaign.slug,
            "utm_content": campaign.azione,
            "utm_term": codice,
        }
    )
    return f"{url}?{query}"


def unsubscribe_urls(token: str, settings: Settings) -> tuple[str, str]:
    parts = urlsplit(settings.hub_url)
    page = f"{settings.hub_url.rstrip('/')}/disiscrizione?t={token}"
    api = f"{parts.scheme}://{parts.netloc}/api/hub/campagne/disiscrizione?t={token}"
    return page, api


def render(campaign: Campaign, target: RenderTarget, settings: Settings, *, test: bool = False) -> RenderedMail:
    paragraphs = _paragraphs(personalise(campaign.testo, target.nome))
    url = _tracked(destination(campaign, settings), campaign, target.codice)
    page, api = unsubscribe_urls(target.token, settings)
    text = "\n\n".join(
        (*paragraphs, f"{campaign.bottone_testo}: {url}", "Ivan\nrebase", f"{UNSUBSCRIBE_LINE} Disiscriviti: {page}")
    )
    href = html.escape(url, quote=True)
    body = "\n".join(
        (
            *(f"<p {_PARA}>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs),
            _button(href, html.escape(campaign.bottone_testo)),
            f"<p {_FINE}>{FALLBACK}<br>{_quiet_link(href, href)}</p>",
            f"<p {_AFTER}>Ivan<br>rebase</p>",
            f"<p {_FINE}>{UNSUBSCRIBE_LINE} {_quiet_link(html.escape(page, quote=True), 'Disiscriviti')}</p>",
        )
    )
    mail = Mail(
        to=target.email,
        subject=("[prova] " if test else "") + campaign.oggetto,
        text=text + "\n",
        html=_frame(campaign.oggetto, body),
    )
    headers = {"List-Unsubscribe": f"<{api}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    tags = {"campaign": campaign.slug, "azione": campaign.azione, "kind": "test" if test else "real"}
    if target.recipient_id is not None:
        tags["r"] = target.recipient_id
    return RenderedMail(mail, headers, tags)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_render.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/render.py projects/hub/packages/core/tests/test_campaign_render.py
git commit -m "feat(hub): a campaign mail, tracked, signed and one click from unsubscribe" -m "REB-466."
```

### Task 8: The sender

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/sender.py`
- Test: `projects/hub/packages/core/tests/test_campaign_sender.py`

**Interfaces:**
- Consumes: `RenderedMail` (Task 7), `RESEND_URL` (`mail.py:24`), `HttpCall` and
  `urllib_call` (`http.py`).
- Produces:
  - `@dataclass(frozen=True) SendOutcome(esito: Literal["accettata", "riprova", "rifiutata"], resend_id: str | None = None, dettaglio: str = "")`.
  - `class CampaignSender(Protocol): def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome`.
  - `ResendCampaignSender(api_key: str, sender: str, http: HttpCall | None = None)`.
  - `RecordingCampaignSender(outcomes: list[SendOutcome] | None = None)`, with `.sent`
    and `.keys`.
  - `campaign_sender_from_settings(settings) -> CampaignSender | None`.

Resend's reference was checked on 25/09 for this task:
- `Idempotency-Key` takes 1 to 256 characters and is kept for 24 hours.
- The same key with the same payload returns the first response and sends nothing.
- A different payload is 409 `invalid_idempotent_request`.
- A request still in progress is 409 `concurrent_idempotent_requests`.
- The body takes custom `headers` (an object), and `tags` as `[{name, value}]` with
  ASCII letters, digits, `_` and `-`, each at most 256 characters.

- [ ] **Step 1: Write the failing tests**

```python
"""Resend, one call per mail, keyed so a retry never sends twice."""

import json

from rebase_core.campaigns.render import RenderedMail
from rebase_core.campaigns.sender import (
    RecordingCampaignSender,
    ResendCampaignSender,
    SendOutcome,
    campaign_sender_from_settings,
)
from rebase_core.config import Settings
from rebase_core.mail import Mail

MAIL = RenderedMail(
    Mail(to="ada@studio.it", subject="S", text="T", html="<p>H</p>"),
    {"List-Unsubscribe": "<https://x/u>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    {"campaign": "c-1", "azione": "cv", "kind": "real", "r": "r-1"},
)


class FakeHttp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self.body, self.calls = status, body, []

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, json.loads(body)))
        return self.status, self.body


def test_a_mail_goes_with_its_key_headers_and_tags() -> None:
    http = FakeHttp(200, b'{"id": "re_1"}')
    outcome = ResendCampaignSender("key", "Ivan di rebase <ciao@letsrebase.com>", http).send(MAIL, "row-1")
    assert outcome == SendOutcome("accettata", "re_1")
    method, url, headers, body = http.calls[0]
    assert (method, url) == ("POST", "https://api.resend.com/emails")
    assert headers["Idempotency-Key"] == "row-1"
    assert body["from"] == "Ivan di rebase <ciao@letsrebase.com>" and body["to"] == ["ada@studio.it"]
    assert body["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert {"name": "r", "value": "r-1"} in body["tags"]


def test_a_concurrent_key_a_rate_limit_and_a_network_error_are_retried_later() -> None:
    for status, raw in (
        (409, b'{"name": "concurrent_idempotent_requests"}'),
        (429, b"{}"),
        (503, b""),
        (599, b""),
    ):
        assert ResendCampaignSender("k", "s", FakeHttp(status, raw)).send(MAIL, "x").esito == "riprova"


def test_a_changed_payload_under_the_same_key_and_a_bad_request_are_refused() -> None:
    changed = FakeHttp(409, b'{"name": "invalid_idempotent_request"}')
    assert ResendCampaignSender("k", "s", changed).send(MAIL, "x").esito == "rifiutata"
    assert ResendCampaignSender("k", "s", FakeHttp(422, b'{"name": "validation_error"}')).send(MAIL, "x").esito == "rifiutata"


def test_the_seam_never_raises() -> None:
    def boom(*_: object) -> tuple[int, bytes]:
        raise OSError("down")

    assert ResendCampaignSender("k", "s", boom).send(MAIL, "x").esito == "riprova"


def test_recording_sender_answers_in_order_then_accepts() -> None:
    recording = RecordingCampaignSender([SendOutcome("riprova")])
    assert recording.send(MAIL, "a").esito == "riprova"
    assert recording.send(MAIL, "a").esito == "accettata"
    assert recording.keys == ["a", "a"]


def test_no_key_no_sender() -> None:
    assert campaign_sender_from_settings(Settings(_env_file=None)) is None  # type: ignore[call-arg]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_sender.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.sender`).

- [ ] **Step 3: Write `sender.py`**

```python
"""Resend's `POST /emails` for campaigns: `mail.ResendSender` plus the three things a
campaign needs and the member area's mail does not. It keeps the id, carries tags and
custom headers, and sends an `Idempotency-Key` equal to the recipient row's id, so a
tick that dies after Resend accepted a mail re-sends the same key and gets the same
answer instead of a second mail (spec § 5.4). Never raises; never logs an address."""

import json
from dataclasses import dataclass
from typing import Literal, Protocol

from rebase_core.campaigns.render import RenderedMail
from rebase_core.config import Settings
from rebase_core.http import HttpCall, urllib_call
from rebase_core.mail import RESEND_URL

Esito = Literal["accettata", "riprova", "rifiutata"]


@dataclass(frozen=True)
class SendOutcome:
    esito: Esito
    resend_id: str | None = None
    dettaglio: str = ""


class CampaignSender(Protocol):
    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome: ...


def _field(raw: bytes, name: str) -> str:
    try:
        value = json.loads(raw or b"{}").get(name)
    except (ValueError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


class ResendCampaignSender:
    def __init__(self, api_key: str, sender: str, http: HttpCall | None = None) -> None:
        self.api_key, self.sender, self.http = api_key, sender, http or urllib_call

    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome:
        mail = rendered.mail
        body: dict[str, object] = {
            "from": self.sender,
            "to": [mail.to],
            "subject": mail.subject,
            "text": mail.text,
            "headers": rendered.headers,
            "tags": [{"name": k, "value": v} for k, v in rendered.tags.items()],
        }
        if mail.html is not None:
            body["html"] = mail.html
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        try:
            status, raw = self.http("POST", RESEND_URL, headers, json.dumps(body).encode())
        except Exception:  # noqa: BLE001 - the seam's contract is "never raises"
            return SendOutcome("riprova", dettaglio="nessuna risposta da Resend")
        if 200 <= status < 300:
            return SendOutcome("accettata", _field(raw, "id") or None)
        name = _field(raw, "name")
        if status == 409 and name == "concurrent_idempotent_requests":
            return SendOutcome("riprova", dettaglio=name)
        if status == 429 or status >= 500:
            return SendOutcome("riprova", dettaglio=f"Resend {status}")
        return SendOutcome("rifiutata", dettaglio=f"Resend {status} {name}".strip())


class RecordingCampaignSender:
    """Keeps every mail and key; answers the queued outcomes first, then accepts."""

    def __init__(self, outcomes: list[SendOutcome] | None = None) -> None:
        self.sent: list[RenderedMail] = []
        self.keys: list[str] = []
        self._outcomes = list(outcomes or [])

    def send(self, rendered: RenderedMail, idempotency_key: str) -> SendOutcome:
        self.sent.append(rendered)
        self.keys.append(idempotency_key)
        if self._outcomes:
            return self._outcomes.pop(0)
        return SendOutcome("accettata", f"rec-{len(self.sent)}")


def campaign_sender_from_settings(settings: Settings) -> CampaignSender | None:
    if not settings.resend_api_key:
        return None
    return ResendCampaignSender(settings.resend_api_key, settings.campaign_from)
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_sender.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/sender.py projects/hub/packages/core/tests/test_campaign_sender.py
git commit -m "feat(hub): send a campaign mail through Resend, one idempotent call each" -m "REB-466."
```

---

## Group D — Draft, test, schedule and cancel a campaign

### Task 9: The service's drafts, list and detail

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/schemas.py` (append)
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/service.py`
- Test: `projects/hub/packages/core/tests/test_campaign_service.py`

**Interfaces:**
- Consumes: Tasks 3 to 7.
- Produces, in schemas:
  - `CampaignDraft`: `nome`, `fonte: Literal["stato","filtri"]`,
    `stato_percorso: str | None`, `filtri: Filtri | None`, `oggetto`, `testo`,
    `bottone_testo`, `bottone_meta: Literal["area","wizard","richiesta","pigro"]`,
    `azione: Literal[...CAMPAIGN_ACTIONS]`.
  - `CampaignPatch`: every field of `CampaignDraft`, optional.
  - `ScheduleRequest`: `giorno: date | None = None`, `ora: time | None = None`,
    `esclusi: list[EmailStr] = []`.
  - `NeverWriteRequest(email: EmailStr)`.
  - `CampaignCounts`: `destinatari`, `in_coda`, `inviate`, `saltate`, `fallite`,
    `consegnate`, `rimbalzate`, all `int`.
  - `CampaignRead` (from attributes, plus computed `pronta: bool`).
  - `CampaignListItem(CampaignRead)` with `conteggi: CampaignCounts`, and
    `CampaignList(items: list[CampaignListItem])`.
  - `RecipientRead`: `id`, `email`, `nome`, `tipo`, `stato`, `motivo`, `inviata_at`,
    `consegnata_at`, `rimbalzata_at`.
  - `CampaignDetail`: `campagna: CampaignRead`, `conteggi`, `destinatari:
    list[RecipientRead]`.
  - `AudienceRowRead`: `email`, `nome`, `tipo`, `escluso`.
  - `AudiencePreview`: `righe`, `incluse`, `escluse`.
  - `TemplateRead`, matching `Template`.
- Produces, in service: `CampaignService(session, settings, *, clock=lambda:
  datetime.now(UTC))`, with:
  - `templates() -> list[TemplateRead]`;
  - `create(admin_id, data) -> CampaignRead`;
  - `update(campaign_id, data) -> CampaignRead`;
  - `audience(campaign_id) -> AudiencePreview`;
  - `list_all() -> CampaignList`;
  - `detail(campaign_id) -> CampaignDetail`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/campaign_fixtures.py` the helpers the service, the tick and the API
tests share. It needs `CampaignDraft` from `rebase_core.campaigns.schemas`, `AdminRead`
from `rebase_core.admin_tokens` and `Settings` from `rebase_core.config`:

```python
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
NOW = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)


class Clock:
    """A clock a test moves by hand: the service and the tick take one."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def as_admin(user: User) -> AdminRead:
    return AdminRead.model_validate(user)


def draft(**fields: object) -> CampaignDraft:
    values: dict[str, object] = {
        "nome": "Manca il CV",
        "fonte": "stato",
        "stato_percorso": "manca_cv",
        "oggetto": "Manca solo il CV",
        "testo": "Ciao {nome},\n\ntesto.",
        "bottone_testo": "Carica il CV",
        "bottone_meta": "area",
        "azione": "cv",
    }
    values.update(fields)
    return CampaignDraft(**values)  # type: ignore[arg-type]
```

Then `tests/test_campaign_service.py`:

```python
"""CampaignService: the admin's verbs (spec § 4)."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.orm import Session

from campaign_fixtures import NOW, SETTINGS, Clock, admin, as_admin, clean, draft, person  # noqa: F401
from rebase_core.campaigns.schemas import CampaignPatch, CampaignRead, ScheduleRequest
from rebase_core.campaigns.service import CampaignService
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.models import User


def test_a_draft_gets_a_dated_unique_slug(clean: Session) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    first = service.create(who.id, draft())
    second = service.create(who.id, draft())
    assert first.slug == "c-2026-09-25-manca-il-cv"
    assert second.slug == "c-2026-09-25-manca-il-cv-2"
    assert first.stato == "bozza" and first.pronta is False


def test_phase_one_refuses_the_pigro_parts(clean: Session) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    for bad in (draft(stato_percorso="pigro_vuoto"), draft(bottone_meta="pigro"), draft(azione="pigro_cliente")):
        with pytest.raises(ValidationFailed, match="fase Pigro"):
            service.create(who.id, bad)


def test_the_audience_preview_counts_included_and_excluded(clean: Session) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    person(clean, "nocv@studio.it", cv=False)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    created = service.create(who.id, draft())
    preview = service.audience(created.id)
    assert (preview.incluse, preview.escluse) == (1, 1)


def test_an_edit_moves_contenuto_at_and_a_non_draft_cannot_be_edited(clean: Session) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    created = service.create(admin(clean).id, draft())
    clock.at = NOW + timedelta(minutes=5)
    edited = service.update(created.id, CampaignPatch(oggetto="Nuovo oggetto"))
    assert edited.contenuto_at == NOW + timedelta(minutes=5)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_service.py`
Expected: FAIL (`ImportError: cannot import name 'CampaignDraft'`).

- [ ] **Step 3: Append the schemas**

Add to `campaigns/schemas.py`. The imports also need `UUID`, `time`, `EmailStr`,
`ConfigDict` and `computed_field`, plus the constants from `models`:

```python
from datetime import time
from uuid import UUID

from pydantic import ConfigDict, EmailStr, computed_field

from rebase_core.models import (
    CAMPAIGN_BUTTON_MAX_LENGTH,
    CAMPAIGN_NAME_MAX_LENGTH,
    CAMPAIGN_SUBJECT_MAX_LENGTH,
    CAMPAIGN_TEXT_MAX_LENGTH,
)

Azione = Literal["entrato", "cv", "scheda_completa", "profilo_creato", "richiesta_aggiornata", "pigro_cliente"]
Meta = Literal["area", "wizard", "pigro", "richiesta"]


class CampaignDraft(BaseModel):
    nome: str = Field(min_length=1, max_length=CAMPAIGN_NAME_MAX_LENGTH)
    fonte: Literal["stato", "filtri"]
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str = Field(default="", max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str = Field(default="", max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str = Field(default="", max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta
    azione: Azione


class CampaignPatch(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=CAMPAIGN_NAME_MAX_LENGTH)
    fonte: Literal["stato", "filtri"] | None = None
    stato_percorso: str | None = Field(default=None, max_length=30)
    filtri: Filtri | None = None
    oggetto: str | None = Field(default=None, max_length=CAMPAIGN_SUBJECT_MAX_LENGTH)
    testo: str | None = Field(default=None, max_length=CAMPAIGN_TEXT_MAX_LENGTH)
    bottone_testo: str | None = Field(default=None, max_length=CAMPAIGN_BUTTON_MAX_LENGTH)
    bottone_meta: Meta | None = None
    azione: Azione | None = None


class ScheduleRequest(BaseModel):
    """`giorno` and `ora` are Europe/Rome wall-clock time, both or neither; neither is
    «Invia adesso». `esclusi` are the addresses the admin unticked on step 1."""

    giorno: date | None = None
    ora: time | None = None
    esclusi: list[EmailStr] = []


class NeverWriteRequest(BaseModel):
    email: EmailStr


class CampaignCounts(BaseModel):
    destinatari: int = 0
    in_coda: int = 0
    inviate: int = 0
    saltate: int = 0
    fallite: int = 0
    consegnate: int = 0
    rimbalzate: int = 0


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nome: str
    slug: str
    fonte: str
    stato_percorso: str | None
    filtri: dict[str, object] | None
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str
    stato: str
    contenuto_at: datetime
    programmata_per: datetime | None
    prova_inviata_at: datetime | None
    inviata_at: datetime | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pronta(self) -> bool:
        """A test has left since the last edit: what enables «Invia» (Review Focus 1)."""
        return self.prova_inviata_at is not None and self.prova_inviata_at >= self.contenuto_at


class CampaignListItem(CampaignRead):
    conteggi: CampaignCounts


class CampaignList(BaseModel):
    items: list[CampaignListItem]


class RecipientRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nome: str | None
    tipo: str
    stato: str
    motivo: str | None
    inviata_at: datetime | None
    consegnata_at: datetime | None
    rimbalzata_at: datetime | None


class CampaignDetail(BaseModel):
    campagna: CampaignRead
    conteggi: CampaignCounts
    destinatari: list[RecipientRead]


class AudienceRowRead(BaseModel):
    email: str
    nome: str | None
    tipo: str
    escluso: str | None


class AudiencePreview(BaseModel):
    righe: list[AudienceRowRead]
    incluse: int
    escluse: int


class TemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stato_percorso: str
    etichetta: str
    oggetto: str
    testo: str
    bottone_testo: str
    bottone_meta: str
    azione: str
```

- [ ] **Step 4: Write `service.py`, first half**

```python
"""CampaignService: what the admin does to a campaign (spec § 4, § 5). The loop that
actually sends is `tick.py`; this module never calls Resend except for the admin's own
test."""

import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.audience import build_audience
from rebase_core.campaigns.schemas import (
    AudiencePreview,
    AudienceRowRead,
    CampaignCounts,
    CampaignDetail,
    CampaignDraft,
    CampaignList,
    CampaignListItem,
    CampaignPatch,
    CampaignRead,
    RecipientRead,
    TemplateRead,
)
from rebase_core.campaigns.states import ENTITY, JOURNEY_STATES, PIGRO_LATER
from rebase_core.campaigns.templates import STATE_TEMPLATES
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient

NOT_A_DRAFT = "Si modifica solo una bozza: riportala in bozza prima."
_CONTENT_FIELDS = ("fonte", "stato_percorso", "filtri", "oggetto", "testo", "bottone_testo", "bottone_meta", "azione")


def _slugify(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "campagna"


class CampaignService:
    def __init__(self, session: Session, settings: Settings, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.session, self.settings, self.clock = session, settings, clock

    def templates(self) -> list[TemplateRead]:
        return [TemplateRead.model_validate(t) for t in STATE_TEMPLATES.values()]

    def create(self, admin_id: UUID, data: CampaignDraft) -> CampaignRead:
        self._validate(data.fonte, data.stato_percorso, data.filtri is not None, data.bottone_meta, data.azione)
        now = self.clock()
        campaign = Campaign(
            created_by=admin_id,
            nome=data.nome.strip(),
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(data.nome)}"[:70]),
            fonte=data.fonte,
            stato_percorso=data.stato_percorso if data.fonte == "stato" else None,
            filtri=data.filtri.model_dump(mode="json", exclude_none=True) if data.filtri else None,
            oggetto=data.oggetto,
            testo=data.testo,
            bottone_testo=data.bottone_testo,
            bottone_meta=data.bottone_meta,
            azione=data.azione,
            stato="bozza",
            contenuto_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def update(self, campaign_id: UUID, data: CampaignPatch) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        changes = data.model_dump(exclude_unset=True)
        if "filtri" in changes and data.filtri is not None:
            changes["filtri"] = data.filtri.model_dump(mode="json", exclude_none=True)
        for field, value in changes.items():
            setattr(campaign, field, value)
        if campaign.fonte == "filtri":
            campaign.stato_percorso = None
        self._validate(campaign.fonte, campaign.stato_percorso, campaign.filtri is not None, campaign.bottone_meta, campaign.azione)
        if any(field in changes for field in _CONTENT_FIELDS):
            campaign.contenuto_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def audience(self, campaign_id: UUID) -> AudiencePreview:
        campaign = self._require(campaign_id)
        rows = build_audience(self.session, campaign, now=self.clock(), gap_days=self.settings.campaign_gap_days)
        righe = [AudienceRowRead(email=r.candidate.email, nome=r.candidate.nome, tipo=r.candidate.tipo, escluso=r.escluso) for r in rows]
        escluse = sum(1 for r in righe if r.escluso)
        return AudiencePreview(righe=righe, incluse=len(righe) - escluse, escluse=escluse)

    def list_all(self) -> CampaignList:
        campaigns = self.session.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all()
        counts = self._counts([c.id for c in campaigns])
        return CampaignList(
            items=[
                CampaignListItem(**CampaignRead.model_validate(c).model_dump(exclude={"pronta"}), conteggi=counts.get(c.id, CampaignCounts()))
                for c in campaigns
            ]
        )

    def detail(self, campaign_id: UUID) -> CampaignDetail:
        campaign = self._require(campaign_id)
        rows = self.session.scalars(
            select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id).order_by(CampaignRecipient.email)
        ).all()
        return CampaignDetail(
            campagna=CampaignRead.model_validate(campaign),
            conteggi=self._counts([campaign_id]).get(campaign_id, CampaignCounts()),
            destinatari=[RecipientRead.model_validate(r) for r in rows],
        )

    # ---- helpers ------------------------------------------------------------------------

    def _validate(self, fonte: str, stato_percorso: str | None, has_filters: bool, meta: str, azione: str) -> None:
        if fonte == "stato":
            if stato_percorso not in JOURNEY_STATES:
                raise ValidationFailed(ENTITY, "stato_percorso", "Scegli uno stato del percorso.")
            if stato_percorso == "pigro_vuoto":
                raise ValidationFailed(ENTITY, "stato_percorso", PIGRO_LATER)
        elif not has_filters:
            raise ValidationFailed(ENTITY, "filtri", "Scegli i filtri della lista.")
        if meta == "pigro":
            raise ValidationFailed(ENTITY, "bottone_meta", PIGRO_LATER)
        if azione == "pigro_cliente":
            raise ValidationFailed(ENTITY, "azione", PIGRO_LATER)

    def _unique_slug(self, base: str) -> str:
        taken = set(self.session.scalars(select(Campaign.slug).where(Campaign.slug.startswith(base))))
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def _require(self, campaign_id: UUID) -> Campaign:
        campaign = self.session.get(Campaign, campaign_id)
        if campaign is None:
            raise NotFound(ENTITY, campaign_id)
        return campaign

    def _counts(self, ids: list[UUID]) -> dict[UUID, CampaignCounts]:
        if not ids:
            return {}
        r = CampaignRecipient
        rows = self.session.execute(
            select(
                r.campaign_id,
                func.count(),
                func.count(case((r.stato == "in_coda", 1))),
                func.count(case((r.stato == "inviata", 1))),
                func.count(case((r.stato == "saltata", 1))),
                func.count(case((r.stato == "fallita", 1))),
                func.count(r.consegnata_at),
                func.count(r.rimbalzata_at),
            )
            .where(r.campaign_id.in_(ids))
            .group_by(r.campaign_id)
        ).all()
        return {
            row[0]: CampaignCounts(
                destinatari=row[1], in_coda=row[2], inviate=row[3], saltate=row[4],
                fallite=row[5], consegnate=row[6], rimbalzate=row[7],
            )
            for row in rows
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_service.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/schemas.py \
  projects/hub/packages/core/src/rebase_core/campaigns/service.py \
  projects/hub/packages/core/tests/test_campaign_service.py
git commit -m "feat(hub): draft, edit, list and read a campaign" -m "REB-467."
```

### Task 10: Test, schedule, back to draft, cancel

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/service.py` (append methods)
- Test: `projects/hub/packages/core/tests/test_campaign_service.py` (append)

**Interfaces:**
- Consumes: `render` and `RenderTarget` (Task 7), `CampaignSender` (Task 8),
  `snapshot` (Task 5), and `AdminRead` (`admin_tokens.py:41`).
- Produces, on `CampaignService`:
  - `send_test(campaign_id, admin: AdminRead, sender: CampaignSender) -> CampaignRead`;
  - `schedule(campaign_id, data: ScheduleRequest) -> CampaignRead`;
  - `back_to_draft(campaign_id) -> CampaignRead`;
  - `cancel(campaign_id) -> CampaignRead`.

It also produces `ROME = ZoneInfo("Europe/Rome")`.

- [ ] **Step 1: Write the failing tests**

```python
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.models import CampaignRecipient


def ready(service: CampaignService, session: Session, clock: Clock) -> tuple[CampaignRead, User]:
    who = admin(session)
    person(session, "nocv@studio.it", cv=False)
    person(session, "other@studio.it", cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    return service.detail(created.id).campagna, who


def test_the_test_goes_to_the_admin_and_enables_sending(clean: Session) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    created = service.create(who.id, draft())
    recording = RecordingCampaignSender()
    tested = service.send_test(created.id, as_admin(who), recording)
    assert tested.pronta is True
    rendered = recording.sent[0]
    assert rendered.mail.to == "ivan@rebase.it"
    assert rendered.mail.subject.startswith("[prova] ")
    assert rendered.tags["kind"] == "test" and "r" not in rendered.tags


def test_an_edit_after_the_test_blocks_scheduling_until_a_new_test(clean: Session) -> None:
    """Review Focus 1: `updated_at` moves on the test's own write; `contenuto_at` does
    not, so it is what the rule compares."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, who = ready(service, clean, clock)
    clock.at += timedelta(minutes=1)
    service.update(campaign.id, CampaignPatch(testo="Ciao {nome},\n\naltro testo."))
    with pytest.raises(InvalidState, match="Manda una prova"):
        service.schedule(campaign.id, ScheduleRequest())
    clock.at += timedelta(minutes=1)
    service.send_test(campaign.id, as_admin(who), RecordingCampaignSender())
    assert service.schedule(campaign.id, ScheduleRequest()).stato == "programmata"


def test_a_refused_test_does_not_enable_sending(clean: Session) -> None:
    service = CampaignService(clean, SETTINGS, clock=Clock(NOW))
    who = admin(clean)
    created = service.create(who.id, draft())
    with pytest.raises(InvalidState, match="Resend"):
        service.send_test(created.id, as_admin(who), RecordingCampaignSender([SendOutcome("rifiutata", dettaglio="Resend 422")]))
    assert service.detail(created.id).campagna.pronta is False


def test_scheduling_freezes_the_list_minus_the_unticked_and_the_excluded(clean: Session) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    person(clean, "boss@rebase.it", cv=False, role="admin")
    scheduled = service.schedule(campaign.id, ScheduleRequest(esclusi=["Other@Studio.it"]))
    assert scheduled.stato == "programmata" and scheduled.programmata_per == clock.at
    rows = clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).all()
    assert [r.email for r in rows] == ["nocv@studio.it"]
    assert rows[0].stato == "in_coda" and len(rows[0].codice) == 8
    assert rows[0].prima["ha_cv"] is False and len(rows[0].disiscrizione_token) >= 40


@pytest.mark.parametrize(
    ("giorno", "ora", "utc"),
    [
        (date(2026, 10, 24), time(9, 30), datetime(2026, 10, 24, 7, 30, tzinfo=UTC)),  # CEST
        (date(2026, 10, 25), time(9, 30), datetime(2026, 10, 25, 8, 30, tzinfo=UTC)),  # CET
        (date(2026, 10, 25), time(2, 30), datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),  # twice: the first
    ],
)
def test_a_scheduled_time_is_rome_wall_clock_across_dst(clean: Session, giorno: date, ora: time, utc: datetime) -> None:
    """Review Focus 2."""
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    assert service.schedule(campaign.id, ScheduleRequest(giorno=giorno, ora=ora)).programmata_per == utc


def test_a_time_that_does_not_exist_or_has_passed_is_refused(clean: Session) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    with pytest.raises(ValidationFailed, match="cambio d'ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2027, 3, 28), ora=time(2, 30)))
    with pytest.raises(ValidationFailed, match="già passato"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 24), ora=time(9, 30)))
    with pytest.raises(ValidationFailed, match="giorno e ora"):
        service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30)))


def test_back_to_draft_drops_the_frozen_list_and_cancel_skips_what_is_left(clean: Session) -> None:
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    campaign, _ = ready(service, clean, clock)
    service.schedule(campaign.id, ScheduleRequest(giorno=date(2026, 9, 30), ora=time(9, 30)))
    assert service.back_to_draft(campaign.id).stato == "bozza"
    assert clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id).count() == 0
    service.schedule(campaign.id, ScheduleRequest())
    cancelled = service.cancel(campaign.id)
    assert cancelled.stato == "annullata"
    assert {r.motivo for r in clean.query(CampaignRecipient).filter_by(campaign_id=campaign.id)} == {"campagna annullata"}
    with pytest.raises(InvalidState):
        service.cancel(campaign.id)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_service.py`
Expected: FAIL (`AttributeError: 'CampaignService' object has no attribute 'send_test'`).

- [ ] **Step 3: Append the methods to `CampaignService`**

Add these imports: `secrets`; `timedelta`, `date`, `time`; `from zoneinfo import
ZoneInfo`; `from sqlalchemy import delete, update`; `from rebase_core.admin_tokens
import AdminRead`; `from rebase_core.campaigns.actions import snapshot`; `from
rebase_core.campaigns.audience import REASON_CANCELLED`; `from
rebase_core.campaigns.render import RenderTarget, person_code, render`; `from
rebase_core.campaigns.sender import CampaignSender`; and `ScheduleRequest` from the
schemas.

```python
ROME = ZoneInfo("Europe/Rome")
NEED_TEST = "Manda una prova dopo l'ultima modifica, poi invia."
EMPTY_MAIL = "Oggetto, testo e bottone servono prima della prova."
PAST_SLACK = timedelta(minutes=1)

    # (methods of CampaignService)

    def send_test(self, campaign_id: UUID, admin: AdminRead, sender: CampaignSender) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        if not (campaign.oggetto.strip() and campaign.testo.strip() and campaign.bottone_testo.strip()):
            raise ValidationFailed(ENTITY, "testo", EMPTY_MAIL)
        target = RenderTarget(
            email=admin.email,
            nome=(admin.nome or "").split(" ")[0] or None,
            codice=person_code(admin.email),
            token="prova",
        )
        outcome = sender.send(render(campaign, target, self.settings, test=True), idempotency_key=f"prova-{campaign.id}-{self.clock().isoformat()}")
        if outcome.esito != "accettata":
            raise InvalidState(f"Resend non ha accettato la prova ({outcome.dettaglio or outcome.esito}).")
        campaign.prova_inviata_at = self.clock()
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def schedule(self, campaign_id: UUID, data: ScheduleRequest) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato != "bozza":
            raise InvalidState(NOT_A_DRAFT)
        if campaign.prova_inviata_at is None or campaign.prova_inviata_at < campaign.contenuto_at:
            raise InvalidState(NEED_TEST)
        now = self.clock()
        when = self._when(data, now)
        unticked = {str(e).lower() for e in data.esclusi}
        rows = [
            r for r in build_audience(self.session, campaign, now=now, gap_days=self.settings.campaign_gap_days)
            if r.escluso is None and r.candidate.email not in unticked
        ]
        if not rows:
            raise ValidationFailed(ENTITY, "esclusi", "La lista è vuota: nessuno riceverebbe la mail.")
        for row in rows:
            c = row.candidate
            self.session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    email=c.email,
                    nome=c.nome,
                    tipo=c.tipo,
                    user_id=c.user_id,
                    freelancer_id=c.freelancer_id,
                    signup_id=c.signup_id,
                    pigro_slugs=list(c.pigro_slugs),
                    codice=person_code(c.email),
                    prima=snapshot(self.session, c, now),
                    disiscrizione_token=secrets.token_urlsafe(32),
                )
            )
        campaign.stato = "programmata"
        campaign.programmata_per = when
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def back_to_draft(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato != "programmata":
            raise InvalidState("Torna in bozza solo una campagna programmata e non ancora partita.")
        self.session.execute(delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id))
        campaign.stato, campaign.programmata_per = "bozza", None
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def cancel(self, campaign_id: UUID) -> CampaignRead:
        campaign = self._require(campaign_id)
        if campaign.stato not in ("programmata", "in_invio"):
            raise InvalidState("Si annulla solo una campagna programmata o in invio.")
        self.session.execute(
            update(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.stato == "in_coda")
            .values(stato="saltata", motivo=REASON_CANCELLED)
        )
        campaign.stato = "annullata"
        self.session.commit()
        return CampaignRead.model_validate(campaign)

    def _when(self, data: ScheduleRequest, now: datetime) -> datetime:
        if data.giorno is None and data.ora is None:
            return now
        if data.giorno is None or data.ora is None:
            raise ValidationFailed(ENTITY, "ora", "Serve giorno e ora, o nessuno dei due per inviare adesso.")
        wall = datetime.combine(data.giorno, data.ora)
        local = wall.replace(tzinfo=ROME)  # fold=0: the first of an hour that happens twice
        if local.astimezone(UTC).astimezone(ROME).replace(tzinfo=None) != wall:
            raise ValidationFailed(ENTITY, "ora", "Quest'ora non esiste il giorno del cambio d'ora: scegline un'altra.")
        when = local.astimezone(UTC)
        if when < now - PAST_SLACK:
            raise ValidationFailed(ENTITY, "giorno", "È già passato: scegli un momento futuro.")
        return when
```

The test's idempotency key carries the moment. A test is meant to be sent again after
an edit, and Resend would answer a reused key's first response without sending.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_service.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/service.py projects/hub/packages/core/tests/test_campaign_service.py
git commit -m "feat(hub): test, schedule in Rome time, return to draft and cancel a campaign" -m "REB-467."
```

---

## Group E — Send due campaigns from a loop service

### Task 11: The tick

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/tick.py`
- Test: `projects/hub/packages/core/tests/test_campaign_tick.py`

**Interfaces:**
- Consumes: `CampaignService` (for fixtures), `candidates`, `exclusions`, `done_at`,
  `render` and `CampaignSender`.
- Produces:
  - `@dataclass TickResult(campagne: int = 0, inviate: int = 0, saltate: int = 0, fallite: int = 0)`.
  - `run_tick(session, sender, settings, *, clock=..., pause=time.sleep) -> TickResult`.
  - `MAX_ATTEMPTS = 3`, `SEND_INTERVAL_SECONDS = 0.5` and `TICK_LOCK_KEY`.

- [ ] **Step 1: Write the failing tests**

```python
"""The loop's one pass (spec § 5.3, § 5.4)."""

from datetime import timedelta

from sqlalchemy.orm import Session

from datetime import UTC, datetime

from campaign_fixtures import NOW, SETTINGS, Clock, admin, as_admin, clean, draft, person  # noqa: F401
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender, SendOutcome
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import MAX_ATTEMPTS, run_tick
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient, Freelancer, User

NO_PAUSE = lambda _seconds: None  # noqa: E731


def scheduled(session: Session, clock: Clock, *emails: str) -> Campaign:
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        person(session, email, cv=False)
    created = service.create(who.id, draft())
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def rows(session: Session, campaign: Campaign) -> dict[str, CampaignRecipient]:
    session.expire_all()
    return {r.email: r for r in session.query(CampaignRecipient).filter_by(campaign_id=campaign.id)}


def test_a_due_campaign_is_sent_one_keyed_call_per_person(clean: Session) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it", "b@studio.it")
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert (result.campagne, result.inviate) == (1, 2)
    sent = rows(clean, campaign)
    assert {r.stato for r in sent.values()} == {"inviata"}
    assert sorted(recording.keys) == sorted(str(r.id) for r in sent.values())
    assert all(m.tags["r"] in recording.keys for m in recording.sent)
    clean.refresh(campaign)
    assert campaign.stato == "inviata"


def test_a_campaign_scheduled_later_waits(clean: Session) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.programmata_per = clock.at + timedelta(hours=1)
    clean.commit()
    assert run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE).campagne == 0


def card_of(session: Session, email: str) -> Freelancer:
    return session.query(Freelancer).join(User, User.id == Freelancer.user_id).filter(User.email == email).one()


def test_a_card_completed_or_deleted_after_freezing_is_skipped_not_sent(clean: Session) -> None:
    """Review Focus 4."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "done@studio.it", "gone@studio.it", "ok@studio.it")
    done = card_of(clean, "done@studio.it")
    done.cv_size, done.cv_filename, done.cv_bytes = 4, "cv.pdf", b"%PDF"
    card_of(clean, "gone@studio.it").deleted_at = datetime.now(UTC)
    clean.commit()
    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    sent = rows(clean, campaign)
    assert (sent["done@studio.it"].stato, sent["done@studio.it"].motivo) == ("saltata", "ha già fatto l'azione")
    assert (sent["gone@studio.it"].stato, sent["gone@studio.it"].motivo) == ("saltata", "non più in lista")
    assert sent["ok@studio.it"].stato == "inviata"
    assert [m.mail.to for m in recording.sent] == ["ok@studio.it"]


def test_an_opt_out_after_freezing_is_skipped(clean: Session) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    clean.add(CampaignOptout(email="a@studio.it", fonte="link"))
    clean.commit()
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert rows(clean, campaign)["a@studio.it"].motivo == "si è disiscritto"


def test_a_retry_keeps_the_row_until_the_third_failure(clean: Session) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    flaky = RecordingCampaignSender([SendOutcome("riprova")] * MAX_ATTEMPTS)
    for _ in range(MAX_ATTEMPTS - 1):
        run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
        assert rows(clean, campaign)["a@studio.it"].stato == "in_coda"
    run_tick(clean, flaky, SETTINGS, clock=clock, pause=NO_PAUSE)
    row = rows(clean, campaign)["a@studio.it"]
    assert (row.stato, row.tentativi) == ("fallita", MAX_ATTEMPTS)
    assert len(set(flaky.keys)) == 1  # the same key every time


def test_a_send_cut_short_resumes_on_the_next_tick(clean: Session) -> None:
    """A tick that died after moving the campaign to `in_invio`: the next one takes it."""
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    campaign.stato = "in_invio"
    clean.commit()
    assert run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE).inviate == 1


def test_two_campaigns_in_the_same_minute_reach_a_person_once(clean: Session) -> None:
    clock = Clock(NOW)
    first = scheduled(clean, clock, "a@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    second = service.create(who.id, draft(nome="Seconda"))
    service.send_test(second.id, as_admin(who), RecordingCampaignSender())
    service.schedule(second.id, ScheduleRequest())
    recording = RecordingCampaignSender()
    run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert [m.mail.to for m in recording.sent] == ["a@studio.it"]
    assert rows(clean, clean.get(Campaign, second.id))["a@studio.it"].motivo.startswith("ha ricevuto un'altra campagna")  # type: ignore[arg-type]


def test_a_cancelled_campaign_is_never_marked_sent(clean: Session) -> None:
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    CampaignService(clean, SETTINGS, clock=clock).cancel(campaign.id)
    run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    clean.refresh(campaign)
    assert campaign.stato == "annullata"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_tick.py`
Expected: FAIL (`ModuleNotFoundError: rebase_core.campaigns.tick`).

- [ ] **Step 3: Write `tick.py`**

```python
"""One pass of the `campaigns` loop (spec § 5.4): every campaign whose time has come, or
that a previous pass left `in_invio`, sent one recipient at a time with the send-time
checks of § 5.3. A session advisory lock keeps two passes from ever running together;
the per-row `FOR UPDATE SKIP LOCKED` keeps a cancellation from racing a send."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at
from rebase_core.campaigns.audience import REASON_DONE, REASON_NOT_LISTED, candidates, exclusions
from rebase_core.campaigns.render import RenderTarget, render
from rebase_core.campaigns.sender import CampaignSender
from rebase_core.config import Settings
from rebase_core.models import Campaign, CampaignRecipient

MAX_ATTEMPTS = 3
SEND_INTERVAL_SECONDS = 0.5  # Resend's default limit is two requests a second
TICK_LOCK_KEY = 0x72656261  # "reba"


@dataclass
class TickResult:
    campagne: int = 0
    inviate: int = 0
    saltate: int = 0
    fallite: int = 0


def run_tick(
    session: Session,
    sender: CampaignSender,
    settings: Settings,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: Callable[[float], None] = time.sleep,
) -> TickResult:
    result = TickResult()
    if not session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": TICK_LOCK_KEY}).scalar():
        return result
    try:
        now = clock()
        due = session.scalars(
            select(Campaign)
            .where(or_(and_(Campaign.stato == "programmata", Campaign.programmata_per <= now), Campaign.stato == "in_invio"))
            .order_by(Campaign.programmata_per, Campaign.id)
        ).all()
        for campaign in due:
            campaign.stato = "in_invio"
            session.commit()
            result.campagne += 1
            _send(session, campaign, sender, settings, clock, pause, result)
    finally:
        session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": TICK_LOCK_KEY})
        session.commit()
    return result


def _send(
    session: Session,
    campaign: Campaign,
    sender: CampaignSender,
    settings: Settings,
    clock: Callable[[], datetime],
    pause: Callable[[float], None],
    result: TickResult,
) -> None:
    queued = session.scalars(
        select(CampaignRecipient.email).where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.stato == "in_coda")
    ).all()
    excluded = exclusions(session, list(queued), campaign_id=campaign.id, now=clock(), gap_days=settings.campaign_gap_days)
    members = {c.email for c in candidates(session, campaign)} if campaign.fonte in ("stato", "filtri") else None
    while True:
        row = session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign.id, CampaignRecipient.stato == "in_coda")
            .order_by(CampaignRecipient.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            break
        reason = excluded.get(row.email)
        if reason is None and done_at(session, row, campaign.azione) is not None:
            reason = REASON_DONE
        if reason is None and members is not None and row.email not in members:
            reason = REASON_NOT_LISTED
        if reason is not None:
            row.stato, row.motivo = "saltata", reason
            result.saltate += 1
            session.commit()
            continue
        target = RenderTarget(email=row.email, nome=row.nome, codice=row.codice, token=row.disiscrizione_token, recipient_id=str(row.id))
        outcome = sender.send(render(campaign, target, settings), idempotency_key=str(row.id))
        if outcome.esito == "accettata":
            row.stato, row.resend_id, row.inviata_at = "inviata", outcome.resend_id, clock()
            result.inviate += 1
        elif outcome.esito == "riprova":
            row.tentativi += 1
            if row.tentativi >= MAX_ATTEMPTS:
                row.stato, row.motivo = "fallita", (outcome.dettaglio or "Resend non risponde")[:200]
                result.fallite += 1
            else:
                session.commit()
                return  # the next tick tries this row again, same key
        else:
            row.stato, row.motivo = "fallita", (outcome.dettaglio or "rifiutata")[:200]
            result.fallite += 1
        session.commit()
        pause(SEND_INTERVAL_SECONDS)
    session.refresh(campaign)
    if campaign.stato == "in_invio":
        campaign.stato, campaign.inviata_at = "inviata", clock()
        session.commit()
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_tick.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/tick.py projects/hub/packages/core/tests/test_campaign_tick.py
git commit -m "feat(hub): send due campaigns one checked, keyed mail at a time" -m "REB-468."
```

### Task 12: `rebase campaigns-tick`, the `campaigns` service and its runbook

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/cli.py` (a `campaigns_tick()` beside `contracts_sweep()` at line 158, a subparser and a dispatch in `main()` at line 311)
- Modify: `projects/hub/docker-compose.yml` (a `campaigns:` service between `mcp:` and `sweep:`, so `test_documenso_compose.py`'s split on `\n  sweep:` and `\n  web:` keeps working)
- Modify: `projects/hub/AGENTS.md` (a section «Campaigns», after «Contracts are signed on Documenso»)
- Modify: `docs/design/DECISIONS.md` (one row, appended; resolve the usual append conflict by keeping main's rows first)
- Test: `projects/hub/packages/core/tests/test_cli.py` and `test_documenso_compose.py` (append)

**Interfaces:**
- Consumes: `run_tick` and `campaign_sender_from_settings`.
- Produces: the CLI command `rebase campaigns-tick`, and the compose service
  `campaigns`.

- [ ] **Step 1: Write the failing tests**

`test_cli.py`, appended:

```python
from rebase_core.campaigns.tick import TickResult


def test_the_campaigns_tick_command_runs_one_pass_and_prints_what_it_did(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, resend_api_key="k"))  # type: ignore[call-arg]
    monkeypatch.setattr(cli, "session_factory", lambda _engine: (lambda: type("S", (), {"close": lambda self: None})()))
    monkeypatch.setattr(cli, "create_engine_from_settings", lambda _settings: None)
    monkeypatch.setattr(cli, "run_tick", lambda *_a, **_k: TickResult(campagne=1, inviate=2, saltate=1, fallite=0))
    assert main(["campaigns-tick"]) == 0
    assert capsys.readouterr().out.strip() == "1 campagne, 2 inviate, 1 saltate, 0 fallite"


def test_without_a_key_the_tick_sends_nothing_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))  # type: ignore[call-arg]
    assert main(["campaigns-tick"]) == 0
    assert "invio non configurato" in capsys.readouterr().out
```

`test_documenso_compose.py`, appended:

```python
def test_the_hub_compose_file_runs_the_campaigns_loop_every_minute_with_no_port() -> None:
    """`rebase campaigns-tick` (P-REB-41): a loop like the sweep's, every minute, since
    «Invia adesso» promises the mail within one."""
    services = _compose().split("\nservices:", 1)[1]
    loop = services.split("\n  campaigns:", 1)[1].split("\n  sweep:", 1)[0]
    assert "while :; do sleep 60; uv run --no-sync rebase campaigns-tick; done" in loop
    assert "init: true" in loop
    assert "environment: *api-environment" in loop
    assert "ports:" not in loop
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_cli.py projects/hub/packages/core/tests/test_documenso_compose.py`
Expected: FAIL (an unknown command, and no `campaigns:` service).

- [ ] **Step 3: Implement the command, the service and the docs**

`cli.py`: add `from rebase_core.campaigns.sender import campaign_sender_from_settings`
and `from rebase_core.campaigns.tick import run_tick` to the imports, then:

```python
def campaigns_tick() -> int:
    """`rebase campaigns-tick`: one pass of the campaigns loop (P-REB-41). Runs every
    minute from the `campaigns` service in `docker-compose.yml`. Without a Resend key it
    sends nothing and says so, and exits 0 so the loop keeps running."""
    settings = get_settings()
    sender = campaign_sender_from_settings(settings)
    if sender is None:
        print("invio non configurato: nessuna campagna parte senza REBASE_RESEND_API_KEY")
        return 0
    session = session_factory(create_engine_from_settings(settings))()
    try:
        result = run_tick(session, sender, settings)
    finally:
        session.close()
    print(f"{result.campagne} campagne, {result.inviate} inviate, {result.saltate} saltate, {result.fallite} fallite")
    return 0
```

In `main()`:

```python
    sub.add_parser(
        "campaigns-tick",
        help="Invia le campagne arrivate alla loro ora, una mail alla volta",
    )
    # ...
    if args.command == "campaigns-tick":
        return campaigns_tick()
```

In `docker-compose.yml`, between `mcp:` and `sweep:`:

```yaml
  campaigns:
    # `rebase campaigns-tick` (P-REB-41): sends the campaigns whose time has come, one
    # mail at a time, every minute, on both stacks. A loop, never a one-shot, for the
    # same reason as `sweep`: `_deploy-compose.yml` fails a deploy on any container not
    # `running`. Harmless without REBASE_RESEND_API_KEY: it prints why and sends nothing.
    # Read with `docker logs rebase-campaigns-1` (production) or
    # `rebase-preview-campaigns-1` (preview).
    build:
      context: ../..
      dockerfile: projects/hub/Dockerfile.api
    restart: unless-stopped
    init: true
    command: ['sh', '-c', 'while :; do sleep 60; uv run --no-sync rebase campaigns-tick; done']
    depends_on:
      db:
        condition: service_healthy
      api:
        condition: service_started
    environment: *api-environment
```

The `AGENTS.md` section «Campaigns» covers these points:
- The loop, how to read its logs and what each log line means.
- Why the scripts of the September waves must never live under `/opt/hub`: the deploy
  rsyncs with `--delete`, and it wiped `/opt/hub/outreach-r2` on 24/09.
- That campaigns reach addresses only through `CampaignOptout` rules. Admins are
  excluded by role; the team goes in «Non scrivere mai».
- The Resend webhook setup, which Task 16 adds.

The `DECISIONS.md` row, dated 2026-09-25: «A campaign mail is one Resend call per
recipient with `Idempotency-Key` = the recipient row's id, and its unsubscribe token is
stored as minted, not hashed, because a retry must be byte-identical (P-REB-41).»

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_cli.py projects/hub/packages/core/tests/test_documenso_compose.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/cli.py projects/hub/docker-compose.yml \
  projects/hub/AGENTS.md docs/design/DECISIONS.md \
  projects/hub/packages/core/tests/test_cli.py projects/hub/packages/core/tests/test_documenso_compose.py
git commit -m "feat(hub): a loop service sends the campaigns whose time has come" -m "REB-468."
```

---

## Group F — Let a person unsubscribe in one click

### Task 13: The opt-out service

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/optouts.py`
- Test: `projects/hub/packages/core/tests/test_campaign_optouts.py`

**Interfaces:**
- Produces:
  - `OptoutService(session)`.
  - `.unsubscribe(token: str) -> None`: silent on an unknown token.
  - `.never_write(email: str) -> None`.
  - `.record(email: str, fonte: str, campaign_id: UUID | None) -> None`: idempotent,
    the first source stays, and it commits.
  - `TOKEN_MAX_LENGTH = 64`.

- [ ] **Step 1: Write the failing tests**

```python
"""Who no campaign may reach (spec § 7)."""

from sqlalchemy.orm import Session

from campaign_fixtures import campaign_row, clean  # noqa: F401
from rebase_core.campaigns.optouts import OptoutService
from rebase_core.models import CampaignOptout, CampaignRecipient


def test_a_good_token_opts_out_its_address_and_an_unknown_one_does_nothing(clean: Session) -> None:
    campaign = campaign_row(clean)
    clean.add(CampaignRecipient(campaign_id=campaign.id, email="ada@studio.it", tipo="freelancer",
                                codice="1", prima={}, disiscrizione_token="good"))
    clean.commit()
    OptoutService(clean).unsubscribe("nobody")
    assert clean.query(CampaignOptout).count() == 0
    OptoutService(clean).unsubscribe("good")
    OptoutService(clean).unsubscribe("good")  # twice: still one row
    row = clean.get(CampaignOptout, "ada@studio.it")
    assert row is not None and row.fonte == "link" and row.campaign_id == campaign.id


def test_never_write_is_lowercase_and_the_first_source_stays(clean: Session) -> None:
    OptoutService(clean).never_write(" Lorenzo@Studio.it ")
    OptoutService(clean).record("lorenzo@studio.it", "reclamo", None)
    assert clean.get(CampaignOptout, "lorenzo@studio.it").fonte == "admin"  # type: ignore[union-attr]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_optouts.py`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `optouts.py`**

```python
"""An address no campaign may reach (spec § 7): its own link, a complaint Resend reports,
an admin's «Non scrivere mai». The first reason recorded stays; recording it again is a
no-op, so a double click or a webhook retry changes nothing."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from rebase_core.models import CampaignOptout, CampaignRecipient

TOKEN_MAX_LENGTH = 64


class OptoutService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def unsubscribe(self, token: str) -> None:
        if not token or len(token) > TOKEN_MAX_LENGTH:
            return
        row = self.session.scalar(select(CampaignRecipient).where(CampaignRecipient.disiscrizione_token == token))
        if row is not None:
            self.record(row.email, "link", row.campaign_id)

    def never_write(self, email: str) -> None:
        self.record(email, "admin", None)

    def record(self, email: str, fonte: str, campaign_id: UUID | None) -> None:
        statement = (
            insert(CampaignOptout)
            .values(email=email.strip().lower(), fonte=fonte, campaign_id=campaign_id)
            .on_conflict_do_nothing(index_elements=["email"])
        )
        self.session.execute(statement)
        self.session.commit()
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_optouts.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/optouts.py projects/hub/packages/core/tests/test_campaign_optouts.py
git commit -m "feat(hub): record who no campaign may reach" -m "REB-469."
```

### Task 14: The public unsubscribe route and page

**Files:**
- Create: `projects/hub/apps/api/src/rebase_api/routers/campaigns.py`. This task writes
  only the `public` router; Task 17 adds the admin one.
- Modify: `projects/hub/apps/api/src/rebase_api/main.py` (include `campaigns.public` after `members.router`)
- Create: `projects/hub/apps/web/src/pages/Disiscrizione.tsx` and `Disiscrizione.test.tsx`
- Modify: `projects/hub/apps/web/src/router.tsx` (a public route `/disiscrizione`)
- Modify: `projects/hub/apps/web/src/lib/api.ts` (`export const campaigns = { unsubscribe }`)
- Create: `projects/hub/apps/api/tests/campaign_api_flow.py` (the API tests' shared helpers, like `contract_flow.py`)
- Test: `projects/hub/apps/api/tests/test_campaigns_api.py` (created here)

**Interfaces:**
- Produces:
  - `GET /api/hub/campagne/disiscrizione?t=` → 303 to
    `{hub_url}/disiscrizione?t=`. A mail client or scanner that fetches the header URL
    changes nothing.
  - `POST /api/hub/campagne/disiscrizione?t=` → 200 `{"ok": true}` whether the token
    matched or not. It spends from the public rate limit, and it is the RFC 8058
    one-click target.
  - Web: `campaigns.unsubscribe(t: string): Promise<{ ok: boolean }>`, and the page at
    `/hub/disiscrizione`.

- [ ] **Step 1: Write the failing API tests**

`tests/campaign_api_flow.py`:

```python
"""What the campaign API tests share: a clean slate after each test, the admin, and a
campaign with one recipient whose unsubscribe token the test chooses."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.models import Campaign, CampaignRecipient, User

ADMIN_EMAIL = "ivan@rebase.it"
CLEAN = ("campaign_optouts", "campaign_recipients", "campaigns", "sessions", "magic_link_tokens",
         "logins", "comments", "freelancers", "companies", "users", "signups")


@pytest.fixture
def tidy(api_session: Session) -> Iterator[Session]:
    yield api_session
    api_session.rollback()
    for table in CLEAN:
        api_session.execute(text(f"DELETE FROM {table}"))
    api_session.commit()


def admin_user(session: Session) -> User:
    user = session.query(User).filter(User.email == ADMIN_EMAIL).one_or_none()
    if user is None:
        user = User(email=ADMIN_EMAIL, nome="Ivan", cognome="Sala", role="admin")
        session.add(user)
        session.commit()
    return user


def recipient_row(session: Session, token: str) -> None:
    admin = admin_user(session)
    campaign = Campaign(created_by=admin.id, nome="n", slug="c-n", fonte="stato", stato_percorso="manca_cv",
                        oggetto="o", testo="t", bottone_testo="b", bottone_meta="area", azione="cv",
                        contenuto_at=datetime.now(UTC))
    session.add(campaign)
    session.flush()
    session.add(CampaignRecipient(campaign_id=campaign.id, email="ada@studio.it", tipo="freelancer",
                                  codice="1", prima={}, disiscrizione_token=token))
    session.commit()
```

`tests/test_campaigns_api.py`:

```python
"""Campaigns over HTTP: the public unsubscribe (Task 14) and the admin routes (Task 17)."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from campaign_api_flow import recipient_row, tidy  # noqa: F401  (fixture)
from rebase_core.models import CampaignOptout


def test_the_header_url_fetched_by_get_only_redirects_to_the_page(client: TestClient, tidy: Session) -> None:
    recipient_row(tidy, "good")
    answer = client.get("/api/hub/campagne/disiscrizione?t=good", follow_redirects=False)
    assert answer.status_code == 303
    assert answer.headers["location"] == "https://letsrebase.com/hub/disiscrizione?t=good"
    assert tidy.query(CampaignOptout).count() == 0


def test_the_one_click_post_opts_out_and_a_wrong_token_answers_the_same(client: TestClient, tidy: Session) -> None:
    recipient_row(tidy, "good")
    wrong = client.post("/api/hub/campagne/disiscrizione?t=nope", data={"List-Unsubscribe": "One-Click"})
    right = client.post("/api/hub/campagne/disiscrizione?t=good", data={"List-Unsubscribe": "One-Click"})
    assert wrong.status_code == right.status_code == 200
    assert wrong.json() == right.json() == {"ok": True}
    assert tidy.get(CampaignOptout, "ada@studio.it") is not None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_campaigns_api.py`
Expected: FAIL with 404 on both routes.

- [ ] **Step 3: Write the public router**

`routers/campaigns.py`:

```python
"""Campaigns over HTTP (P-REB-41). `public` is the unsubscribe a mail's footer and its
`List-Unsubscribe` header point at; `router` (Task 17) is the admin's."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import RedirectResponse

from rebase_api.deps import SessionDep, SettingsDep
from rebase_api.ratelimit import spend_one
from rebase_core.campaigns.optouts import TOKEN_MAX_LENGTH, OptoutService
from rebase_core.schemas import Ack

public = APIRouter(prefix="/api/hub/campagne", tags=["hub-campaigns-public"])

Token = Annotated[str, Query(min_length=1, max_length=TOKEN_MAX_LENGTH)]


@public.get("/disiscrizione")
def unsubscribe_page(t: Token, settings: SettingsDep) -> RedirectResponse:
    """A GET changes nothing: mail scanners fetch links. It sends the person to the page,
    where a button posts."""
    return RedirectResponse(f"{settings.hub_url.rstrip('/')}/disiscrizione?t={t}", status_code=status.HTTP_303_SEE_OTHER)


@public.post("/disiscrizione", response_model=Ack)
def unsubscribe(t: Token, request: Request, session: SessionDep) -> Ack:
    """The page's button and RFC 8058's one-click POST. The same answer for a token that
    matched and one that did not, so a guess learns nothing."""
    spend_one(request)
    OptoutService(session).unsubscribe(t)
    return Ack()
```

In `main.py`, import `campaigns` with the other routers and add
`app.include_router(campaigns.public)`.

- [ ] **Step 4: Write the failing web test**

`pages/Disiscrizione.test.tsx`:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Disiscrizione } from './Disiscrizione'

function mount(path: string) {
  const root = createRootRoute({ component: () => <Outlet /> })
  const page = createRoute({
    getParentRoute: () => root,
    path: '/disiscrizione',
    component: Disiscrizione,
    validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  })
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Disiscrizione»', () => {
  it('posts the token only on the click, then says it is done', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount('/disiscrizione?t=abc')
    const button = await screen.findByRole('button', { name: 'Non scrivermi più' })
    expect(fetch).not.toHaveBeenCalled()
    await userEvent.click(button)
    expect(await screen.findByText('Fatto: non riceverai più queste mail da rebase.')).toBeInTheDocument()
    expect(fetch.mock.calls[0]![0]).toBe('/api/hub/campagne/disiscrizione?t=abc')
  })

  it('says the link is incomplete without a token', async () => {
    mount('/disiscrizione')
    expect(await screen.findByText(/Il link non è completo/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 5: Write the page, the client call and the route**

`lib/api.ts`, at the end:

```ts
// ---- campaigns, the public part -----------------------------------------------------------

export const campaigns = {
  unsubscribe: (t: string) =>
    request<{ ok: boolean }>(`/api/hub/campagne/disiscrizione?t=${encodeURIComponent(t)}`, { method: 'POST' }),
}
```

`pages/Disiscrizione.tsx`:

```tsx
import { useMutation } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { campaigns } from '@/lib/api'

/** The page a campaign mail's «Disiscriviti» opens (spec § 7): one sentence, one button,
 *  and nothing happens until the click, since mail scanners open links on their own. */
export function Disiscrizione() {
  const { t } = useSearch({ strict: false }) as { t?: string }
  const done = useMutation({ mutationFn: (token: string) => campaigns.unsubscribe(token) })
  if (!t) {
    return <p className="mx-auto max-w-xl text-center text-muted-foreground">Il link non è completo: aprilo di nuovo dalla mail.</p>
  }
  return (
    <div className="mx-auto max-w-xl space-y-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">Non vuoi più ricevere queste mail?</h1>
      {done.isSuccess ? (
        <p role="status">Fatto: non riceverai più queste mail da rebase.</p>
      ) : (
        <>
          <p className="text-muted-foreground">
            Smetti di ricevere le mail di rebase su profilo e novità. I link per entrare nella tua area continuano ad arrivare quando li chiedi.
          </p>
          <Button type="button" disabled={done.isPending} onClick={() => done.mutate(t)}>
            Non scrivermi più
          </Button>
          {done.isError && <p role="alert" className="text-sm text-destructive">Non ci siamo riusciti. Riprova tra un minuto.</p>}
        </>
      )}
    </div>
  )
}
```

`router.tsx`: import it and add to `publicLayout.addChildren([...])`:

```tsx
const unsubscribe = createRoute({
  getParentRoute: () => publicLayout,
  path: '/disiscrizione',
  validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  component: Disiscrizione,
})
```

- [ ] **Step 6: Run everything to verify it passes**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_campaigns_api.py`, then
`pnpm --filter hub test -- Disiscrizione` and `pnpm --filter hub lint`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add projects/hub/apps/api/src/rebase_api/routers/campaigns.py projects/hub/apps/api/src/rebase_api/main.py \
  projects/hub/apps/api/tests/test_campaigns_api.py projects/hub/apps/web/src/pages/Disiscrizione.tsx \
  projects/hub/apps/web/src/pages/Disiscrizione.test.tsx projects/hub/apps/web/src/router.tsx projects/hub/apps/web/src/lib/api.ts
git commit -m "feat(hub): a person leaves every campaign in one click" -m "REB-469."
```

---

## Group G — Record delivery, bounces and complaints from Resend's webhook

### Task 15: Verify and apply an event

**Files:**
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/webhook.py`
- Test: `projects/hub/packages/core/tests/test_campaign_webhook.py`

**Interfaces:**
- Consumes: `OptoutService.record` (Task 13).
- Produces:
  - `verify_signature(secret, svix_id, svix_timestamp, svix_signature, body: bytes, now: float) -> bool`.
  - `apply_event(session, event: Mapping[str, Any]) -> Literal["applicato", "ignorato", "da_riprovare"]`.
  - `TOLERANCE_SECONDS = 300`.

Resend's reference was checked on 25/09.

The signature is Svix's:
- the headers are `svix-id`, `svix-timestamp` (seconds) and `svix-signature`;
- the content is `"{id}.{timestamp}.{raw body}"`;
- the key is the base64 after `whsec_`, used with HMAC-SHA256, and the result is
  base64;
- the header holds space-separated `v1,<sig>` entries.

The payloads:
- they are `{"type", "created_at", "data": {...}}`;
- `data.tags` **is present, as an object map** `{"campaign": "..."}`, so the spec
  § 6.1 fallback table is not needed;
- a bounce carries `data.bounce.type` (`"Permanent"` is a hard bounce);
- a click carries `data.click.timestamp`.

Retries run immediately, then after 5 s, 5 min, 30 min, 2 h, 5 h, 10 h and 10 h.

- [ ] **Step 1: Write the failing tests**

```python
"""Resend's webhook (spec § 6.1), core side."""

import base64
import hashlib
import hmac
import json
import time

from sqlalchemy.orm import Session

from campaign_fixtures import campaign_row, clean  # noqa: F401
from rebase_core.campaigns.webhook import apply_event, verify_signature
from rebase_core.models import Campaign, CampaignOptout, CampaignRecipient

SECRET = "whsec_" + base64.b64encode(b"k" * 24).decode()


def signed(body: bytes, *, svix_id: str = "msg_1", at: int | None = None) -> tuple[str, str, str]:
    stamp = str(at if at is not None else int(time.time()))
    mac = hmac.new(base64.b64decode(SECRET.removeprefix("whsec_")), f"{svix_id}.{stamp}.".encode() + body, hashlib.sha256)
    return svix_id, stamp, "v1,bogus v1," + base64.b64encode(mac.digest()).decode()


def test_a_good_signature_passes_and_a_changed_body_or_a_stale_stamp_does_not() -> None:
    body = b'{"type":"email.delivered"}'
    sid, stamp, sig = signed(body)
    now = time.time()
    assert verify_signature(SECRET, sid, stamp, sig, body, now)
    assert not verify_signature(SECRET, sid, stamp, sig, body + b" ", now)
    old_id, old_stamp, old_sig = signed(body, at=int(now) - 600)
    assert not verify_signature(SECRET, old_id, old_stamp, old_sig, body, now)
    assert not verify_signature("", sid, stamp, sig, body, now)


def row(session: Session, **fields: object) -> CampaignRecipient:
    campaign = campaign_row(session)
    recipient = CampaignRecipient(campaign_id=campaign.id, email="ada@studio.it", tipo="freelancer",
                                  codice="1", prima={}, disiscrizione_token="tok", **fields)
    session.add(recipient)
    session.commit()
    return recipient


def event(kind: str, recipient: CampaignRecipient | None, **data: object) -> dict[str, object]:
    tags = {"campaign": "c-prova-0", "kind": "real"}
    if recipient is not None:
        tags["r"] = str(recipient.id)
    return {"type": kind, "created_at": "2026-09-25T07:31:00.000Z", "data": {"email_id": "re_1", "tags": tags, **data}}


def test_delivery_is_found_by_the_tag_before_the_id_is_committed_and_written_once(clean: Session) -> None:
    target = row(clean)
    assert apply_event(clean, event("email.delivered", target)) == "applicato"
    first = clean.get(CampaignRecipient, target.id).consegnata_at  # type: ignore[union-attr]
    later = event("email.delivered", target)
    later["created_at"] = "2026-09-25T09:00:00.000Z"
    apply_event(clean, later)
    assert clean.get(CampaignRecipient, target.id).consegnata_at == first  # type: ignore[union-attr]


def test_only_a_permanent_bounce_marks_the_address(clean: Session) -> None:
    target = row(clean)
    assert apply_event(clean, event("email.bounced", target, bounce={"type": "Transient"})) == "ignorato"
    assert apply_event(clean, event("email.bounced", target, bounce={"type": "Permanent"})) == "applicato"
    assert clean.get(CampaignRecipient, target.id).rimbalzata_at is not None  # type: ignore[union-attr]


def test_a_complaint_opts_the_address_out(clean: Session) -> None:
    target = row(clean)
    apply_event(clean, event("email.complained", target))
    assert clean.get(CampaignOptout, "ada@studio.it").fonte == "reclamo"  # type: ignore[union-attr]


def test_a_click_keeps_the_first_moment(clean: Session) -> None:
    target = row(clean)
    apply_event(clean, event("email.clicked", target, click={"timestamp": "2026-09-25T08:00:00.000Z"}))
    apply_event(clean, event("email.clicked", target, click={"timestamp": "2026-09-25T07:40:00.000Z"}))
    assert clean.get(CampaignRecipient, target.id).primo_clic_at.isoformat() == "2026-09-25T07:40:00+00:00"  # type: ignore[union-attr]


def test_tests_strangers_and_early_events(clean: Session) -> None:
    """Review Focus 5."""
    test_event = event("email.delivered", None)
    test_event["data"]["tags"] = {"campaign": "c-1", "kind": "test"}  # type: ignore[index]
    assert apply_event(clean, test_event) == "ignorato"
    stranger = {"type": "email.delivered", "created_at": "2026-09-25T07:31:00Z", "data": {"email_id": "re_x", "tags": {}}}
    assert apply_event(clean, stranger) == "ignorato"
    early = event("email.delivered", None)
    early["data"]["tags"]["r"] = "00000000-0000-0000-0000-000000000000"  # type: ignore[index]
    assert apply_event(clean, early) == "da_riprovare"


def test_a_delivery_for_a_cancelled_campaign_still_counts(clean: Session) -> None:
    target = row(clean, stato="inviata")
    clean.get(Campaign, target.campaign_id).stato = "annullata"  # type: ignore[union-attr]
    clean.commit()
    assert apply_event(clean, event("email.delivered", target)) == "applicato"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_webhook.py`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `webhook.py`**

```python
"""Resend's webhook, core side (spec § 6.1): a Svix signature, then the first delivery,
the first click, a hard bounce or a complaint on the recipient row. The row is found by
the `r` tag the mail left with, so an event that beats the tick's commit still lands;
`resend_id` is the fallback. Every write keeps the first moment, so a repeated event
changes nothing."""

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.campaigns.optouts import OptoutService
from rebase_core.models import CampaignRecipient

TOLERANCE_SECONDS = 300
HANDLED = ("email.delivered", "email.bounced", "email.clicked", "email.complained")
Outcome = Literal["applicato", "ignorato", "da_riprovare"]


def verify_signature(secret: str, svix_id: str, svix_timestamp: str, svix_signature: str, body: bytes, now: float) -> bool:
    if not (secret.startswith("whsec_") and svix_id and svix_timestamp and svix_signature):
        return False
    try:
        stamp = int(svix_timestamp)
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (ValueError, binascii.Error):
        return False
    if abs(now - stamp) > TOLERANCE_SECONDS:
        return False
    expected = base64.b64encode(hmac.new(key, f"{svix_id}.{svix_timestamp}.".encode() + body, hashlib.sha256).digest())
    for entry in svix_signature.split(" "):
        version, _, signature = entry.partition(",")
        if version == "v1" and hmac.compare_digest(signature.encode(), expected):
            return True
    return False


def _moment(raw: object) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def _find(session: Session, tag: object, email_id: object) -> CampaignRecipient | None:
    if isinstance(tag, str):
        try:
            found = session.get(CampaignRecipient, UUID(tag))
        except ValueError:
            found = None
        if found is not None:
            return found
    if isinstance(email_id, str) and email_id:
        return session.scalar(select(CampaignRecipient).where(CampaignRecipient.resend_id == email_id))
    return None


def apply_event(session: Session, event: Mapping[str, Any]) -> Outcome:
    kind = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    tags = data.get("tags") if isinstance(data.get("tags"), Mapping) else {}
    if tags.get("kind") == "test" or kind not in HANDLED:
        return "ignorato"
    row = _find(session, tags.get("r"), data.get("email_id"))
    if row is None:
        return "da_riprovare" if tags.get("campaign") else "ignorato"
    at = _moment(event.get("created_at"))
    if kind == "email.delivered":
        row.consegnata_at = row.consegnata_at or at
    elif kind == "email.bounced":
        bounce = data.get("bounce") if isinstance(data.get("bounce"), Mapping) else {}
        if bounce.get("type") != "Permanent":
            return "ignorato"
        row.rimbalzata_at = row.rimbalzata_at or at
    elif kind == "email.clicked":
        click = data.get("click") if isinstance(data.get("click"), Mapping) else {}
        clicked = _moment(click.get("timestamp"))
        row.primo_clic_at = min(row.primo_clic_at, clicked) if row.primo_clic_at else clicked
    else:
        row.reclamo_at = row.reclamo_at or at
    if row.resend_id is None and isinstance(data.get("email_id"), str):
        row.resend_id = data["email_id"]
    session.commit()
    if kind == "email.complained":
        OptoutService(session).record(row.email, "reclamo", row.campaign_id)
    return "applicato"
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/packages/core/tests/test_campaign_webhook.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/webhook.py projects/hub/packages/core/tests/test_campaign_webhook.py
git commit -m "feat(hub): read delivery, bounces, clicks and complaints from Resend's events" -m "REB-470."
```

### Task 16: The webhook route and its setup

**Files:**
- Create: `projects/hub/apps/api/src/rebase_api/routers/resend.py`
- Modify: `projects/hub/apps/api/src/rebase_api/main.py` (include it)
- Modify: `projects/hub/AGENTS.md` («Campaigns»: the webhook setup)
- Test: `projects/hub/apps/api/tests/test_resend_webhook_api.py`

**Interfaces:**
- Produces: `POST /api/hub/webhooks/resend`, answering as follows.
  - 503 when `REBASE_RESEND_WEBHOOK_SECRET` is empty.
  - 401 on a missing, stale or bad signature.
  - 400 on a body that is not JSON.
  - 503 on `da_riprovare`, so Resend retries.
  - 200 `{"ok": true}` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
"""Resend's webhook over HTTP (spec § 6.1)."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from campaign_api_flow import tidy  # noqa: F401  (fixture)
from rebase_core.config import Settings, get_settings

SECRET = "whsec_" + base64.b64encode(b"s" * 24).decode()


@pytest.fixture
def armed(client: TestClient) -> Iterator[TestClient]:
    client.app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, resend_webhook_secret=SECRET)  # type: ignore[attr-defined,call-arg]
    yield client


def post(client: TestClient, payload: dict[str, object], *, secret: str = SECRET) -> int:
    body = json.dumps(payload).encode()
    stamp = str(int(time.time()))
    mac = hmac.new(base64.b64decode(secret.removeprefix("whsec_")), f"msg_1.{stamp}.".encode() + body, hashlib.sha256)
    headers = {"svix-id": "msg_1", "svix-timestamp": stamp, "svix-signature": "v1," + base64.b64encode(mac.digest()).decode()}
    return client.post("/api/hub/webhooks/resend", content=body, headers=headers).status_code


def test_without_the_secret_the_webhook_is_off(client: TestClient) -> None:
    assert client.post("/api/hub/webhooks/resend", content=b"{}").status_code == 503


def test_a_bad_signature_is_refused(armed: TestClient) -> None:
    other = "whsec_" + base64.b64encode(b"x" * 24).decode()
    assert post(armed, {"type": "email.delivered", "data": {}}, secret=other) == 401


def test_a_stranger_is_acknowledged_and_an_early_tagged_event_is_retried(armed: TestClient, tidy: Session) -> None:
    assert post(armed, {"type": "email.delivered", "data": {"email_id": "re_x", "tags": {}}}) == 200
    early = {"type": "email.delivered", "data": {"email_id": "re_y", "tags": {"campaign": "c-n", "r": "00000000-0000-0000-0000-000000000000"}}}
    assert post(armed, early) == 503
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_resend_webhook_api.py`
Expected: FAIL (404).

- [ ] **Step 3: Write the router**

```python
"""Resend's webhook (P-REB-41, spec § 6.1). No cookie: Resend signs with Svix, verified
over the raw body against REBASE_RESEND_WEBHOOK_SECRET. A tagged event whose row is not
found yet answers 503, the one answer that makes Resend retry (immediately, 5 s, 5 min,
30 min, 2 h, 5 h, 10 h, 10 h): the tick may not have committed it. Everything else that
verified answers 200."""

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from rebase_api.deps import SessionDep, SettingsDep
from rebase_core.campaigns.webhook import apply_event, verify_signature
from rebase_core.schemas import Ack

router = APIRouter(prefix="/api/hub/webhooks", tags=["hub-resend"])


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/resend", response_model=Ack)
def resend_webhook(
    settings: SettingsDep,
    session: SessionDep,
    body: Annotated[bytes, Depends(raw_body)],
    svix_id: Annotated[str | None, Header()] = None,
    svix_timestamp: Annotated[str | None, Header()] = None,
    svix_signature: Annotated[str | None, Header()] = None,
) -> Ack:
    if not settings.resend_webhook_secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Il webhook di Resend non è attivo su questo ambiente.")
    if not verify_signature(settings.resend_webhook_secret, svix_id or "", svix_timestamp or "", svix_signature or "", body, time.time()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "firma non valida")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo non valido") from None
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "corpo non valido")
    if apply_event(session, event) == "da_riprovare":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "riprova")
    return Ack()
```

In `AGENTS.md` «Campaigns», add the setup, which Ivan does once per environment:
1. In Resend, go to Webhooks, then «Add endpoint».
   - Production: `https://letsrebase.com/api/hub/webhooks/resend`.
   - Preview: `https://preview.letsrebase.com/api/hub/webhooks/resend`, only once the
     preview has a Resend key.
2. Select the events `email.delivered`, `email.bounced`, `email.clicked` and
   `email.complained`.
3. Copy the `whsec_…` value into that environment's `${DEPLOY_PATH}/.env` as
   `REBASE_RESEND_WEBHOOK_SECRET`.
4. Recreate the api container with the next deploy, or with
   `docker compose -p rebase --env-file ... up -d api` from `projects/hub`.

Resend's webhook covers every mail of the domain, magic links included. Those arrive
untagged and are acknowledged without effect.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_resend_webhook_api.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/api/src/rebase_api/routers/resend.py projects/hub/apps/api/src/rebase_api/main.py \
  projects/hub/apps/api/tests/test_resend_webhook_api.py projects/hub/AGENTS.md
git commit -m "feat(hub): Resend's signed webhook writes delivery, bounces and complaints" -m "REB-470."
```

---

## Group H — Serve campaigns to the admin over the API

### Task 17: The admin routes

**Files:**
- Modify: `projects/hub/apps/api/src/rebase_api/deps.py` (append `get_campaign_sender` and `CampaignSenderDep` after `SenderDep`, line ~93)
- Modify: `projects/hub/apps/api/src/rebase_api/routers/campaigns.py` (the admin `router`)
- Modify: `projects/hub/apps/api/src/rebase_api/main.py` (include `campaigns.router`)
- Test: `projects/hub/apps/api/tests/test_campaigns_api.py` (append)

**Interfaces:**
- Consumes: `CampaignService` (Tasks 9 and 10), `OptoutService.never_write`, and
  `AdminDep`.
- Produces, under `/api/hub/campaigns`, all behind the admin cookie:

  | Method and path | Returns |
  |---|---|
  | `GET ""` | `CampaignList` |
  | `GET "/templates"` | `list[TemplateRead]` |
  | `POST ""` | 201 `CampaignRead` |
  | `GET "/{id}"` | `CampaignDetail` |
  | `PATCH "/{id}"` | `CampaignRead` |
  | `GET "/{id}/audience"` | `AudiencePreview` |
  | `POST "/{id}/test"` | `CampaignRead`, or 503 without a key |
  | `POST "/{id}/schedule"` | `CampaignRead` |
  | `POST "/{id}/draft"` | `CampaignRead` |
  | `POST "/{id}/cancel"` | `CampaignRead` |
  | `POST "/never-write"` | `Ack` |

- [ ] **Step 1: Write the failing tests** (append to `test_campaigns_api.py`)

```python
import re

from campaign_api_flow import admin_user
from rebase_api.deps import get_campaign_sender
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.mail import RecordingSender
from rebase_core.models import Freelancer, User


def login_admin(client: TestClient, sender: RecordingSender, session: Session) -> None:
    admin_user(session)
    assert client.post("/api/hub/auth/link", json={"email": "ivan@rebase.it"}).status_code == 202
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", sender.sent[-1].text).group(1)  # type: ignore[union-attr]
    assert client.post("/api/hub/auth/enter", json={"token": token}).status_code == 200


def a_card_without_cv(session: Session, email: str) -> None:
    from decimal import Decimal

    user = User(email=email, nome="Ada", cognome="L")
    session.add(user)
    session.flush()
    session.add(Freelancer(user_id=user.id, tariffa_giornaliera=Decimal("400"), posizione="Dev", remoto="remoto", links=[]))
    session.commit()


def test_every_campaign_route_wants_an_admin(client: TestClient, tidy: Session) -> None:
    assert client.get("/api/hub/campaigns").status_code == 401
    assert client.post("/api/hub/campaigns/never-write", json={"email": "a@b.it"}).status_code == 401


def test_the_admin_drafts_tests_and_schedules_a_campaign(
    client: TestClient, tidy: Session, sender: RecordingSender
) -> None:
    login_admin(client, sender, tidy)
    recording = RecordingCampaignSender()
    client.app.dependency_overrides[get_campaign_sender] = lambda: recording  # type: ignore[attr-defined]
    a_card_without_cv(tidy, "ada@studio.it")
    templates = client.get("/api/hub/campaigns/templates").json()
    cv = next(t for t in templates if t["stato_percorso"] == "manca_cv")
    created = client.post("/api/hub/campaigns", json={"nome": "CV", "fonte": "stato", **{k: cv[k] for k in ("stato_percorso", "oggetto", "testo", "bottone_testo", "bottone_meta", "azione")}})
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    assert client.get(f"/api/hub/campaigns/{campaign_id}/audience").json()["incluse"] == 1
    refused = client.post(f"/api/hub/campaigns/{campaign_id}/schedule", json={"esclusi": []})
    assert refused.status_code == 409 and "prova" in refused.json()["detail"]
    tested = client.post(f"/api/hub/campaigns/{campaign_id}/test").json()
    assert tested["pronta"] is True and recording.sent[0].mail.to == "ivan@rebase.it"
    scheduled = client.post(f"/api/hub/campaigns/{campaign_id}/schedule", json={"giorno": "2030-01-10", "ora": "09:30", "esclusi": []})
    assert scheduled.status_code == 200 and scheduled.json()["programmata_per"] == "2030-01-10T08:30:00Z"
    detail = client.get(f"/api/hub/campaigns/{campaign_id}").json()
    assert detail["conteggi"]["in_coda"] == 1 and detail["destinatari"][0]["email"] == "ada@studio.it"


def test_without_a_resend_key_the_test_is_a_503_sentence(client: TestClient, tidy: Session, sender: RecordingSender) -> None:
    login_admin(client, sender, tidy)
    client.app.dependency_overrides[get_campaign_sender] = lambda: None  # type: ignore[attr-defined]
    created = client.post("/api/hub/campaigns", json={"nome": "X", "fonte": "stato", "stato_percorso": "completo",
                                                      "oggetto": "o", "testo": "t", "bottone_testo": "b",
                                                      "bottone_meta": "area", "azione": "entrato"}).json()
    answer = client.post(f"/api/hub/campaigns/{created['id']}/test")
    assert answer.status_code == 503 and "non è configurato" in answer.json()["detail"]


def test_never_write_records_the_address(client: TestClient, tidy: Session, sender: RecordingSender) -> None:
    login_admin(client, sender, tidy)
    assert client.post("/api/hub/campaigns/never-write", json={"email": "Lorenzo@Studio.it"}).status_code == 200
    assert tidy.get(CampaignOptout, "lorenzo@studio.it").fonte == "admin"  # type: ignore[union-attr]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_campaigns_api.py`
Expected: FAIL (`ImportError: get_campaign_sender`, then 404s).

- [ ] **Step 3: Add the dependency and the routes**

`deps.py`, after `SenderDep`:

```python
from rebase_core.campaigns.sender import CampaignSender, campaign_sender_from_settings


def get_campaign_sender(settings: SettingsDep) -> CampaignSender | None:
    """Campaigns' own Resend seam (P-REB-41): the member area's plus the id, the tags,
    the headers and the idempotency key. `None` without a key."""
    return campaign_sender_from_settings(settings)


CampaignSenderDep = Annotated[CampaignSender | None, Depends(get_campaign_sender)]
```

`routers/campaigns.py`, appended:

```python
from uuid import UUID

from rebase_api.deps import AdminDep, CampaignSenderDep
from rebase_core.campaigns.schemas import (
    AudiencePreview,
    CampaignDetail,
    CampaignDraft,
    CampaignList,
    CampaignPatch,
    CampaignRead,
    NeverWriteRequest,
    ScheduleRequest,
    TemplateRead,
)
from rebase_core.campaigns.service import CampaignService

router = APIRouter(prefix="/api/hub/campaigns", tags=["hub-admin"])
NO_SENDER = "L'invio di mail non è configurato su questo ambiente."


@router.get("", response_model=CampaignList)
def list_campaigns(_: AdminDep, session: SessionDep, settings: SettingsDep) -> CampaignList:
    return CampaignService(session, settings).list_all()


@router.get("/templates", response_model=list[TemplateRead])
def templates(_: AdminDep, session: SessionDep, settings: SettingsDep) -> list[TemplateRead]:
    return CampaignService(session, settings).templates()


@router.post("", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
def create(admin: AdminDep, session: SessionDep, settings: SettingsDep, data: CampaignDraft) -> CampaignRead:
    return CampaignService(session, settings).create(admin.id, data)


@router.post("/never-write", response_model=Ack)
def never_write(_: AdminDep, session: SessionDep, data: NeverWriteRequest) -> Ack:
    OptoutService(session).never_write(str(data.email))
    return Ack()


@router.get("/{campaign_id}", response_model=CampaignDetail)
def detail(_: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID) -> CampaignDetail:
    return CampaignService(session, settings).detail(campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignRead)
def update(_: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID, data: CampaignPatch) -> CampaignRead:
    return CampaignService(session, settings).update(campaign_id, data)


@router.get("/{campaign_id}/audience", response_model=AudiencePreview)
def audience(_: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID) -> AudiencePreview:
    return CampaignService(session, settings).audience(campaign_id)


@router.post("/{campaign_id}/test", response_model=CampaignRead)
def send_test(admin: AdminDep, session: SessionDep, settings: SettingsDep, sender: CampaignSenderDep, campaign_id: UUID) -> CampaignRead:
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    return CampaignService(session, settings).send_test(campaign_id, admin, sender)


@router.post("/{campaign_id}/schedule", response_model=CampaignRead)
def schedule(_: AdminDep, session: SessionDep, settings: SettingsDep, sender: CampaignSenderDep, campaign_id: UUID, data: ScheduleRequest) -> CampaignRead:
    if sender is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_SENDER)
    return CampaignService(session, settings).schedule(campaign_id, data)


@router.post("/{campaign_id}/draft", response_model=CampaignRead)
def back_to_draft(_: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID) -> CampaignRead:
    return CampaignService(session, settings).back_to_draft(campaign_id)


@router.post("/{campaign_id}/cancel", response_model=CampaignRead)
def cancel(_: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID) -> CampaignRead:
    return CampaignService(session, settings).cancel(campaign_id)
```

Add `HTTPException` to the `fastapi` import. In `main.py`, add
`app.include_router(campaigns.router)`. `/never-write` and `/templates` are declared
before `/{campaign_id}`, so FastAPI does not read them as an id.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest -q -p no:xdist projects/hub/apps/api/tests/test_campaigns_api.py`
Expected: PASS. `InvalidState` becomes 409 through `main.domain_error_handler`, and
`ValidationFailed` becomes 422.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/api/src/rebase_api/deps.py projects/hub/apps/api/src/rebase_api/routers/campaigns.py \
  projects/hub/apps/api/src/rebase_api/main.py projects/hub/apps/api/tests/test_campaigns_api.py
git commit -m "feat(hub): the admin's campaign routes" -m "REB-471."
```

---

## Group I — «Campagne» in the admin: the list and the four-step wizard

### Task 18: The client, the labels, `personalise`

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts` (types after `MatchesFilters`, and the calls inside `export const admin = {…}`, line 716)
- Create: `projects/hub/apps/web/src/lib/campaigns.ts`, `projects/hub/apps/web/src/lib/campaigns.test.ts`

**Interfaces:**
- Produces:
  - The types `CampaignStato`, `CampaignAzione`, `CampaignMeta`, `Campaign`,
    `CampaignCounts`, `CampaignListItem`, `CampaignList`, `CampaignRecipient`,
    `CampaignDetail`, `AudienceRow`, `AudiencePreview`, `CampaignTemplate`,
    `CampaignDraft` and `ScheduleRequest`.
  - On `admin`: `campaigns()`, `campaignTemplates()`, `campaign(id)`,
    `createCampaign(d)`, `updateCampaign(id, d)`, `campaignAudience(id)`,
    `testCampaign(id)`, `scheduleCampaign(id, d)`, `campaignToDraft(id)`,
    `cancelCampaign(id)` and `neverWrite(email)`.
  - In `lib/campaigns.ts`: `CAMPAIGN_STATE_LABELS`, `AZIONE_LABELS`, `META_LABELS`,
    `RECIPIENT_STATE_LABELS`, `personalise(testo, nome)` (the server's rule) and
    `romeToday(): { giorno: string; ora: string }`.

- [ ] **Step 1: Write the failing test**

```ts
import { describe, expect, it } from 'vitest'
import { AZIONE_LABELS, CAMPAIGN_STATE_LABELS, personalise } from './campaigns'

describe('campaigns', () => {
  it('puts the name in, or leaves a clean greeting like the server', () => {
    expect(personalise('Ciao {nome}, come va?', 'Ada')).toBe('Ciao Ada, come va?')
    expect(personalise('Ciao {nome}, come va?', null)).toBe('Ciao, come va?')
  })
  it('labels every state and action in Italian', () => {
    expect(CAMPAIGN_STATE_LABELS.programmata).toBe('Programmata')
    expect(AZIONE_LABELS.cv).toBe('Ha caricato il CV')
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm --filter hub test -- campaigns.test`
Expected: FAIL (the module is not found).

- [ ] **Step 3: Write the types, the calls and `lib/campaigns.ts`**

In `api.ts`, after `MatchesFilters`:

```ts
// ---- campaigns (P-REB-41) -------------------------------------------------------------

export type CampaignStato = 'bozza' | 'programmata' | 'in_invio' | 'inviata' | 'annullata'
export type CampaignAzione = 'entrato' | 'cv' | 'scheda_completa' | 'profilo_creato' | 'richiesta_aggiornata' | 'pigro_cliente'
export type CampaignMeta = 'area' | 'wizard' | 'pigro' | 'richiesta'
export type RecipientStato = 'in_coda' | 'inviata' | 'saltata' | 'fallita'

export interface Campaign {
  id: string
  nome: string
  slug: string
  fonte: 'stato' | 'filtri' | 'lista'
  stato_percorso: string | null
  filtri: Record<string, unknown> | null
  oggetto: string
  testo: string
  bottone_testo: string
  bottone_meta: CampaignMeta
  azione: CampaignAzione
  stato: CampaignStato
  contenuto_at: string
  programmata_per: string | null
  prova_inviata_at: string | null
  inviata_at: string | null
  created_at: string
  /** A test has left since the last edit: «Invia» is enabled only then. */
  pronta: boolean
}

export interface CampaignCounts {
  destinatari: number
  in_coda: number
  inviate: number
  saltate: number
  fallite: number
  consegnate: number
  rimbalzate: number
}

export interface CampaignListItem extends Campaign {
  conteggi: CampaignCounts
}

export interface CampaignList {
  items: CampaignListItem[]
}

export interface CampaignRecipient {
  id: string
  email: string
  nome: string | null
  tipo: string
  stato: RecipientStato
  motivo: string | null
  inviata_at: string | null
  consegnata_at: string | null
  rimbalzata_at: string | null
}

export interface CampaignDetail {
  campagna: Campaign
  conteggi: CampaignCounts
  destinatari: CampaignRecipient[]
}

export interface AudienceRow {
  email: string
  nome: string | null
  tipo: string
  escluso: string | null
}

export interface AudiencePreview {
  righe: AudienceRow[]
  incluse: number
  escluse: number
}

export interface CampaignTemplate {
  stato_percorso: string
  etichetta: string
  oggetto: string
  testo: string
  bottone_testo: string
  bottone_meta: CampaignMeta
  azione: CampaignAzione
}

export interface CampaignDraft {
  nome: string
  fonte: 'stato' | 'filtri'
  stato_percorso?: string | null
  filtri?: Record<string, unknown> | null
  oggetto: string
  testo: string
  bottone_testo: string
  bottone_meta: CampaignMeta
  azione: CampaignAzione
}

export interface ScheduleRequest {
  giorno?: string | null
  ora?: string | null
  esclusi: string[]
}
```

Inside `export const admin = {`:

```ts
  campaigns: () => request<CampaignList>('/api/hub/campaigns'),
  campaignTemplates: () => request<CampaignTemplate[]>('/api/hub/campaigns/templates'),
  campaign: (id: string) => request<CampaignDetail>(`/api/hub/campaigns/${id}`),
  createCampaign: (data: CampaignDraft) => request<Campaign>('/api/hub/campaigns', json(data)),
  updateCampaign: (id: string, data: Partial<CampaignDraft>) =>
    request<Campaign>(`/api/hub/campaigns/${id}`, { ...json(data), method: 'PATCH' }),
  campaignAudience: (id: string) => request<AudiencePreview>(`/api/hub/campaigns/${id}/audience`),
  testCampaign: (id: string) => request<Campaign>(`/api/hub/campaigns/${id}/test`, { method: 'POST' }),
  scheduleCampaign: (id: string, data: ScheduleRequest) => request<Campaign>(`/api/hub/campaigns/${id}/schedule`, json(data)),
  campaignToDraft: (id: string) => request<Campaign>(`/api/hub/campaigns/${id}/draft`, { method: 'POST' }),
  cancelCampaign: (id: string) => request<Campaign>(`/api/hub/campaigns/${id}/cancel`, { method: 'POST' }),
  neverWrite: (email: string) => request<{ ok: boolean }>('/api/hub/campaigns/never-write', json({ email })),
```

`lib/campaigns.ts`:

```ts
import type { CampaignAzione, CampaignMeta, CampaignStato, RecipientStato } from './api'

export const CAMPAIGN_STATE_LABELS: Record<CampaignStato, string> = {
  bozza: 'Bozza',
  programmata: 'Programmata',
  in_invio: 'In invio',
  inviata: 'Inviata',
  annullata: 'Annullata',
}

export const AZIONE_LABELS: Record<CampaignAzione, string> = {
  entrato: 'È entrato nell’area',
  cv: 'Ha caricato il CV',
  scheda_completa: 'Ha completato la scheda',
  profilo_creato: 'Ha creato il profilo',
  richiesta_aggiornata: 'Ha aggiornato la richiesta',
  pigro_cliente: 'Primo cliente in Pigro',
}

/** Phase 1 offers three: the Pigro button arrives with phase 3. */
export const META_LABELS: Partial<Record<CampaignMeta, string>> = {
  area: 'La sua area',
  wizard: 'Il wizard del profilo',
  richiesta: 'La richiesta dell’azienda',
}

export const RECIPIENT_STATE_LABELS: Record<RecipientStato, string> = {
  in_coda: 'In coda',
  inviata: 'Inviata',
  saltata: 'Saltata',
  fallita: 'Fallita',
}

/** The server's rule (`campaigns/render.py:personalise`), for the preview on screen. */
export function personalise(testo: string, nome: string | null): string {
  if (nome) return testo.replaceAll('{nome}', nome)
  return testo.replaceAll(' {nome}', '').replaceAll('{nome}', '')
}

/** Today and the next quarter hour in Europe/Rome, as the «Programma» inputs want them. */
export function romeToday(now = new Date()): { giorno: string; ora: string } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Europe/Rome',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  )
  return { giorno: `${parts.year}-${parts.month}-${parts.day}`, ora: `${parts.hour}:${parts.minute}` }
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pnpm --filter hub test -- campaigns.test`, then `pnpm --filter hub lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/lib/api.ts projects/hub/apps/web/src/lib/campaigns.ts projects/hub/apps/web/src/lib/campaigns.test.ts
git commit -m "feat(hub): the web client and labels for campaigns" -m "REB-472."
```

### Task 19: «Campagne», the list, the menu entry, the routes

**Files:**
- Create: `projects/hub/apps/web/src/pages/admin/Campagne.tsx`, `Campagne.test.tsx`
- Modify: `projects/hub/apps/web/src/components/SidebarNav.tsx` (`ADMIN_NAV`, after «Match»; the icon is `Send` from `lucide-react`)
- Modify: `projects/hub/apps/web/src/router.tsx`. Add four routes under `adminArea`:
  - `/campaigns`: `AdminCampagne`;
  - `/campaigns/new`: `AdminCreaCampagna`;
  - `/campaigns/$id`: `AdminCampagna`;
  - `/campaigns/$id/edit`: `AdminCreaCampagna`.

  Task 19 wires `new` and `edit` to a stub until Task 20.

**Interfaces:**
- Consumes: `admin.campaigns()` and `CAMPAIGN_STATE_LABELS`.
- Produces: `AdminCampagne` (named export).

- [ ] **Step 1: Write the failing test**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampagne } from './Campagne'

const ITEM = {
  id: 'c1',
  nome: 'Manca il CV',
  slug: 'c-2026-09-25-manca-il-cv',
  fonte: 'stato',
  stato_percorso: 'manca_cv',
  filtri: null,
  oggetto: 'Manca solo il CV',
  testo: 'Ciao {nome},',
  bottone_testo: 'Carica il CV',
  bottone_meta: 'area',
  azione: 'cv',
  stato: 'inviata',
  contenuto_at: '2026-09-25T07:00:00Z',
  programmata_per: '2026-09-25T07:30:00Z',
  prova_inviata_at: '2026-09-25T07:10:00Z',
  inviata_at: '2026-09-25T07:32:00Z',
  created_at: '2026-09-25T07:00:00Z',
  pronta: true,
  conteggi: { destinatari: 9, in_coda: 0, inviate: 8, saltate: 1, fallite: 0, consegnate: 8, rimbalzate: 0 },
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const list = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns', component: AdminCampagne })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>campagna</p> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/new', component: () => <p>nuova</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([list, one, fresh])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Campagne»', () => {
  it('lists each campaign with its state and numbers, and links to it', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [ITEM] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    const row = (await screen.findByRole('link', { name: 'Manca il CV' })).closest('tr')!
    expect(within(row).getByText('Inviata')).toBeInTheDocument()
    expect(row).toHaveTextContent('8 inviate')
    expect(row).toHaveTextContent('1 saltate')
    expect(screen.getByRole('link', { name: 'Nuova campagna' })).toHaveAttribute('href', expect.stringMatching(/\/admin\/campaigns\/new$/))
  })

  it('says so when there is none', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    mount()
    expect(await screen.findByText('Ancora nessuna campagna.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm --filter hub test -- Campagne.test`
Expected: FAIL (the module is not found).

- [ ] **Step 3: Write the page, the nav entry and the routes**

`pages/admin/Campagne.tsx`:

```tsx
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type CampaignListItem } from '@/lib/api'
import { CAMPAIGN_STATE_LABELS } from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { Empty, Header } from './lists'

function when(item: CampaignListItem): string {
  const moment = item.inviata_at ?? item.programmata_per
  return moment ? formatDateTime(moment) : ''
}

/** «Campagne» (P-REB-41): every campaign, newest first, with what left and what came back. */
export function AdminCampagne() {
  const list = useQuery({ queryKey: ['campaigns'], queryFn: admin.campaigns })
  const items = list.data?.items ?? []
  return (
    <div>
      <Header title="Campagne" count={list.data ? items.length : undefined}>
        <Button asChild>
          <Link to="/admin/campaigns/new">Nuova campagna</Link>
        </Button>
      </Header>
      {list.isError && <Empty>Non riesco a leggere le campagne. Riprova tra poco.</Empty>}
      {list.data && items.length === 0 && <Empty>Ancora nessuna campagna.</Empty>}
      {items.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Campagna</TableHead>
              <TableHead>Stato</TableHead>
              <TableHead>Quando</TableHead>
              <TableHead>Esito</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <Link to="/admin/campaigns/$id" params={{ id: item.id }} className="font-medium hover:underline">
                    {item.nome}
                  </Link>
                  <p className="text-xs text-muted-foreground">{item.oggetto}</p>
                </TableCell>
                <TableCell>
                  <Badge variant="pill">{CAMPAIGN_STATE_LABELS[item.stato]}</Badge>
                </TableCell>
                <TableCell className="text-muted-foreground">{when(item)}</TableCell>
                <TableCell className="text-sm">
                  {item.conteggi.inviate} inviate · {item.conteggi.consegnate} consegnate · {item.conteggi.rimbalzate} rimbalzate ·{' '}
                  {item.conteggi.saltate} saltate
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
```

`SidebarNav.tsx`: import `Send` and add
`{ to: '/admin/campaigns', label: 'Campagne', icon: Send },` after the «Match» entry.
Update the docstring above `ADMIN_NAV` with one sentence on «Campagne».

`router.tsx`, under `adminArea`:

```tsx
const adminCampaigns = createRoute({ getParentRoute: () => adminArea, path: '/campaigns', component: AdminCampagne })
const adminCampaignNew = createRoute({ getParentRoute: () => adminArea, path: '/campaigns/new', component: AdminCreaCampagna })
const adminCampaign = createRoute({ getParentRoute: () => adminArea, path: '/campaigns/$id', component: AdminCampagna })
const adminCampaignEdit = createRoute({ getParentRoute: () => adminArea, path: '/campaigns/$id/edit', component: AdminCreaCampagna })
```

Add the four to `adminArea.addChildren([...])`, with `adminCampaignNew` before
`adminCampaign`: TanStack ranks a static segment over `$id` either way, but the list
reads in the order a person follows. Until Task 20 and Task 21 land, `AdminCreaCampagna`
and `AdminCampagna` are one-line components in their own files that say «In arrivo».
Those tasks replace them.

- [ ] **Step 4: Run it to verify it passes**

Run: `pnpm --filter hub test -- Campagne.test`, then `pnpm --filter hub lint` and `pnpm --filter hub build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/pages/admin/Campagne.tsx projects/hub/apps/web/src/pages/admin/Campagne.test.tsx \
  projects/hub/apps/web/src/pages/admin/CreaCampagna.tsx projects/hub/apps/web/src/pages/admin/Campagna.tsx \
  projects/hub/apps/web/src/components/SidebarNav.tsx projects/hub/apps/web/src/router.tsx
git commit -m "feat(hub): «Campagne» in the admin menu, with the list of campaigns" -m "REB-472."
```

### Task 20: «Nuova campagna», four steps

**Files:**
- Replace: `projects/hub/apps/web/src/pages/admin/CreaCampagna.tsx`
- Create: `projects/hub/apps/web/src/pages/admin/CreaCampagna.test.tsx`

**Interfaces:**
- Consumes: the `admin.*` campaign calls (Task 18), `useMe` (`lib/me.tsx:24`) for the
  test's address, `personalise` and `romeToday`.
- Produces: `AdminCreaCampagna`, mounted at `/admin/campaigns/new` and
  `/admin/campaigns/$id/edit`.

The behaviour, step by step (`STEPS = ['Chi', 'Cosa', 'Prova', 'Quando']`), follows
the `CreaMatch.tsx` shape: step state, an ordered `<ol>` of step names with
`aria-current="step"`, and a footer with «Indietro» and «Avanti».

1. **Chi.** Two buttons choose «Uno stato del percorso» or «Filtri».
   - A state is a `Select` of the templates' `etichetta`. Choosing one fills oggetto,
     testo, bottone_testo, bottone_meta and azione from the template, unless the
     admin already wrote in them.
   - Filters are a `Select` for the list (Talenti or Aziende) plus the Talenti fields
     `stato`, `q`, `has_cv` and `con_accessi`, or the company fields `stato` and `q`.
   - «Avanti» creates the draft (`createCampaign`), or saves it with `updateCampaign`
     when it has an id. It then loads `campaignAudience(id)` and shows a table: name,
     address, a checkbox (checked, and disabled with the reason as text when
     `escluso`), and the counts «N incluse, M escluse».
   - Unticked addresses are kept in state as `esclusi: string[]`.
2. **Cosa.** Nome, Oggetto, Testo (a `Textarea` with the hint «`{nome}` diventa il
   nome della persona»), Testo del bottone, and Dove porta (a `Select` of
   `META_LABELS`).
   - The action is a `Select` of `AZIONE_LABELS` without `pigro_cliente`, disabled
     when the source is a state.
   - «Avanti» saves with `updateCampaign`.
3. **Prova.** A preview card:
   - «Oggetto:», and the text through `personalise(testo, firstIncluded?.nome ?? null)`;
   - the button's label as a disabled `Button`, and the unsubscribe line as plain
     text. It links nowhere.
   - «Mandami una prova» calls `testCampaign`, then shows «Prova inviata a
     <me.email>».
4. **Quando.** Two buttons choose «Invia adesso» or «Programma».
   - «Programma» shows a date input and a time input, prefilled from `romeToday()`,
     with the note «ora di Roma».
   - The submit reads «Invia» or «Programma». It is `disabled={!campaign.pronta}`, with
     the sentence «Hai modificato la campagna dopo la prova: mandane un'altra dal
     passo Prova.» whenever `pronta` is false.
   - On success it navigates to `/admin/campaigns/$id`.
   - An `ApiError`'s message shows in a `role="alert"` paragraph.

- [ ] **Step 1: Write the failing tests**

These pin the flow and Review Focus 1:

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCreaCampagna } from './CreaCampagna'

const TEMPLATE = {
  stato_percorso: 'manca_cv',
  etichetta: 'Manca solo il CV',
  oggetto: 'Manca solo il CV',
  testo: 'Ciao {nome},\n\nmanca il CV.',
  bottone_testo: 'Carica il CV',
  bottone_meta: 'area',
  azione: 'cv',
}
const DRAFT = {
  id: 'c1', nome: 'Manca solo il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null,
  oggetto: TEMPLATE.oggetto, testo: TEMPLATE.testo, bottone_testo: TEMPLATE.bottone_testo, bottone_meta: 'area', azione: 'cv',
  stato: 'bozza', contenuto_at: '2026-09-25T07:00:00Z', programmata_per: null, prova_inviata_at: null, inviata_at: null,
  created_at: '2026-09-25T07:00:00Z', pronta: false,
}
const AUDIENCE = {
  righe: [
    { email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', escluso: null },
    { email: 'ivan@rebase.it', nome: 'Ivan', tipo: 'freelancer', escluso: 'amministratore' },
  ],
  incluse: 1,
  escluse: 1,
}
const ME = { email: 'ivan@rebase.it', nome: 'Ivan', role: 'admin' }

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Answers by method and path, and records every call for the assertions. */
function api(routes: Record<string, () => Response>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const key = `${init?.method ?? 'GET'} ${String(input)}`
    const match = Object.keys(routes).find((prefix) => key.startsWith(prefix))
    return match ? routes[match]!() : json({ detail: `unexpected ${key}` }, 500)
  })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const fresh = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/new', component: AdminCreaCampagna })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: () => <p>pagina campagna</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([fresh, one])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns/new'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('«Nuova campagna»', () => {
  it('fills the mail from the state, shows who is left out and why, and sends after a test', async () => {
    let tested = false
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns/c1/test': () => ((tested = true), json({ ...DRAFT, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' })),
      'POST /api/hub/campaigns/c1/schedule': () => json({ ...DRAFT, stato: 'programmata' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, pronta: tested }),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Manca solo il CV' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    expect(await screen.findByText('amministratore')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'ivan@rebase.it' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Cosa
    expect(screen.getByLabelText('Oggetto')).toHaveValue('Manca solo il CV')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Prova
    expect(await screen.findByText(/Ciao Ada,/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Mandami una prova' }))
    expect(await screen.findByText('Prova inviata a ivan@rebase.it')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // to Quando
    await userEvent.click(screen.getByRole('button', { name: 'Invia' }))
    expect(await screen.findByText('pagina campagna')).toBeInTheDocument()
    const schedule = calls.mock.calls.find(([url, init]) => String(url).endsWith('/schedule') && init?.method === 'POST')!
    expect(JSON.parse(String(schedule[1]!.body))).toEqual({ esclusi: [] })
  })

  it('keeps «Invia» off after an edit that follows the test', async () => {
    api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'POST /api/hub/campaigns/c1/test': () => json({ ...DRAFT, pronta: true, prova_inviata_at: '2026-09-25T07:05:00Z' }),
      'POST /api/hub/campaigns': () => json(DRAFT, 201),
      'PATCH /api/hub/campaigns/c1': () => json({ ...DRAFT, pronta: false }),
      'GET /api/hub/campaigns/c1/audience': () => json(AUDIENCE),
    })
    mount()
    await userEvent.click(await screen.findByRole('combobox', { name: 'Stato del percorso' }))
    await userEvent.click(await screen.findByRole('option', { name: 'Manca solo il CV' }))
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' }))
    await screen.findByText('amministratore')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Cosa
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Prova
    await userEvent.click(await screen.findByRole('button', { name: 'Mandami una prova' }))
    await screen.findByText('Prova inviata a ivan@rebase.it')
    await userEvent.click(screen.getByRole('button', { name: 'Indietro' })) // back to Cosa
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // saves: pronta false
    await userEvent.click(screen.getByRole('button', { name: 'Avanti' })) // Quando
    expect(screen.getByRole('button', { name: 'Invia' })).toBeDisabled()
    expect(screen.getByText(/Hai modificato la campagna dopo la prova/)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pnpm --filter hub test -- CreaCampagna.test`
Expected: FAIL. The stub renders «In arrivo»: there is no combobox «Stato del percorso».

- [ ] **Step 3: Write `CreaCampagna.tsx`**

Follow the behaviour above. Take these names from the imports already listed:
- `Select`, `SelectContent`, `SelectItem`, `SelectTrigger` and `SelectValue` from
  `@rebase/ui/select`;
- `Checkbox` from `@rebase/ui/checkbox`;
- `Input`, `Label`, `Textarea`, `Button` and `cn`;
- `Header` from `./lists`.

Each labelled control has `id` plus `<Label htmlFor>`, so the tests' `getByLabelText`
and `getByRole('combobox', { name })` resolve. Specifically:
- «Stato del percorso» is the `SelectTrigger`'s `aria-label`;
- the audience checkbox has `aria-label={row.email}`.

The edit route reads `id` from `useParams({ strict: false })` and loads
`admin.campaign(id)` into the form state before step 1.

- [ ] **Step 4: Run them to verify they pass**

Run: `pnpm --filter hub test -- CreaCampagna.test`, then `pnpm --filter hub lint` and `pnpm --filter hub build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/pages/admin/CreaCampagna.tsx projects/hub/apps/web/src/pages/admin/CreaCampagna.test.tsx
git commit -m "feat(hub): «Nuova campagna», from a list to a scheduled send in four steps" -m "REB-472."
```

---

## Group J — The campaign page, and the video of the whole flow

### Task 21: The campaign page

**Files:**
- Replace: `projects/hub/apps/web/src/pages/admin/Campagna.tsx`
- Create: `projects/hub/apps/web/src/pages/admin/Campagna.test.tsx`

**Interfaces:**
- Consumes: `admin.campaign`, `admin.campaignToDraft`, `admin.cancelCampaign`,
  `admin.neverWrite`, `Figure` (`lists.tsx:201`) and `RECIPIENT_STATE_LABELS`.
- Produces: `AdminCampagna`.

The behaviour:
- The heading is the campaign's name, with the state badge beside it.
- The `<dl>` of `Figure`s shows: Destinatari, In coda, Inviate, Consegnate, Rimbalzate,
  Saltate, Fallite.
- The actions depend on the state.
  - `bozza`: «Modifica» (a link to `/admin/campaigns/$id/edit`).
  - `programmata`: «Riporta in bozza» and «Annulla».
  - `in_invio`: «Annulla».
- The table has one row per recipient:
  - Persona: the name and the address;
  - Stato: the label, with the `motivo` under it when there is one;
  - Inviata, Consegnata and Rimbalzata: `formatDateTime`, or empty;
  - an actions cell with «Non scrivere mai» (`admin.neverWrite(email)`), which turns
    into the text «Non riceverà più campagne».
- The query refetches every 10 s while the state is `programmata` or `in_invio`
  (`refetchInterval`), so a send in progress fills in on its own.

- [ ] **Step 1: Write the failing tests**

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminCampagna } from './Campagna'

const DETAIL = {
  campagna: {
    id: 'c1', nome: 'Manca il CV', slug: 's', fonte: 'stato', stato_percorso: 'manca_cv', filtri: null, oggetto: 'o', testo: 't',
    bottone_testo: 'b', bottone_meta: 'area', azione: 'cv', stato: 'programmata', contenuto_at: '2026-09-25T07:00:00Z',
    programmata_per: '2026-09-26T07:30:00Z', prova_inviata_at: '2026-09-25T07:05:00Z', inviata_at: null,
    created_at: '2026-09-25T07:00:00Z', pronta: true,
  },
  conteggi: { destinatari: 2, in_coda: 1, inviate: 0, saltate: 1, fallite: 0, consegnate: 0, rimbalzate: 0 },
  destinatari: [
    { id: 'r1', email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', stato: 'in_coda', motivo: null, inviata_at: null, consegnata_at: null, rimbalzata_at: null },
    { id: 'r2', email: 'bob@studio.it', nome: 'Bob', tipo: 'freelancer', stato: 'saltata', motivo: 'ha già fatto l’azione', inviata_at: null, consegnata_at: null, rimbalzata_at: null },
  ],
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function mount() {
  const root = createRootRoute({ component: () => <Outlet /> })
  const signedIn = createRoute({ getParentRoute: () => root, id: 'signedIn', component: () => <Outlet /> })
  const one = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id', component: AdminCampagna })
  const edit = createRoute({ getParentRoute: () => signedIn, path: '/admin/campaigns/$id/edit', component: () => <p>modifica</p> })
  const router = createRouter({
    routeTree: root.addChildren([signedIn.addChildren([one, edit])]),
    history: createMemoryHistory({ initialEntries: ['/admin/campaigns/c1'] }),
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the campaign page', () => {
  it('shows the numbers, each person with the reason a row was skipped, and the scheduled actions', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    const bob = (await screen.findByText('bob@studio.it')).closest('tr')!
    expect(within(bob).getByText('Saltata')).toBeInTheDocument()
    expect(within(bob).getByText('ha già fatto l’azione')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Riporta in bozza' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Annulla' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Modifica' })).not.toBeInTheDocument()
  })

  it('adds a person to «Non scrivere mai» from their row', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      String(input).endsWith('/never-write') ? json({ ok: true }) : json(DETAIL),
    )
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    await userEvent.click(within(ada).getByRole('button', { name: 'Non scrivere mai' }))
    expect(await within(ada).findByText('Non riceverà più campagne')).toBeInTheDocument()
    const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/never-write'))!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ email: 'ada@studio.it' })
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pnpm --filter hub test -- Campagna.test`
Expected: FAIL (the stub).

- [ ] **Step 3: Write `Campagna.tsx`** following the behaviour above: `useParams({
  from: '/signedIn/admin/campaigns/$id' })` in the app, and `strict: false` in a way the
  test's tree also satisfies, the way `Contratti.tsx` reads its id. Use `useMutation`
  with `queryClient.invalidateQueries({ queryKey: ['campaign', id] })` after
  «Riporta in bozza» and «Annulla».

- [ ] **Step 4: Run them to verify they pass**

Run: `pnpm --filter hub test -- Campagna.test`, then `pnpm --filter hub lint` and `pnpm --filter hub build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/pages/admin/Campagna.tsx projects/hub/apps/web/src/pages/admin/Campagna.test.tsx
git commit -m "feat(hub): the campaign page: numbers, each person, and what can still be undone" -m "REB-473."
```

### Task 22: The milestone's evidence

**Files:**
- No product code. The PR body gets the screenshots and the video, following
  `docs/pr-screenshots/README.md` and the pr-creation skill.

- [ ] **Step 1: Run the whole hub suite and the web gates**, each as its own process:

  ```bash
  uv run ruff check projects/hub && uv run mypy
  uv run pytest -q -p no:xdist projects/hub/packages/core/tests
  uv run pytest -q -p no:xdist projects/hub/apps/api/tests
  uv run pytest -q -p no:xdist projects/hub/apps/mcp/tests
  pnpm --filter hub lint && pnpm --filter hub test && pnpm --filter hub build
  ```

  Expected: everything green. Record the counts for the PR body.

- [ ] **Step 2: The stack for the pictures.** Follow the recipe in memory
  `pigrocrm-repo-location-and-local-checks`:
  - a throwaway Postgres on its own port, and `upgrade_to_head`;
  - seed an admin, three cards (one without CV, one empty, one complete) and a lead;
  - run the API with `REBASE_COOKIE_SECURE=false` and a launcher script kept outside
    the repository (`scratchpad/campaigns_api.py`). It builds `create_app()`, sets
    `app.dependency_overrides[get_campaign_sender] = lambda: shared` to one
    module-level `RecordingCampaignSender`, and runs uvicorn. «Mandami una prova» then
    succeeds without Resend.
  - The send in the video is a second launcher (`scratchpad/campaigns_tick.py`). It
    calls `run_tick(session, RecordingCampaignSender(), settings)` once against the same
    database, so the campaign page fills in without a key.

- [ ] **Step 3: The pairs and the video.**
  - Pairs: the admin sidebar before and after («Campagne» appears), and the new pages.
    For the pages the before is «no such page», so say so rather than inventing one.
  - The video, with `docs/pr-screenshots/record.mjs`, 10 to 40 s:
    1. «Nuova campagna»;
    2. «Manca solo il CV»;
    3. the list with the admin greyed out;
    4. «Cosa»;
    5. «Mandami una prova»;
    6. «Invia»;
    7. the campaign page, filling in as `rebase campaigns-tick` runs once against the
       stub.
  - Attach with `gh pr edit --add-attachment` or at creation, per the skill.

- [ ] **Step 4: Update the milestone's draft PR**: tick the cards, write the body's
  sections, and move it out of draft only when every card is merged into the branch.
  Then run the Greptile loop to 5/5 and `ci` green (memory
  `greptile-loop-until-5-of-5`).

---

## Self-review (done while writing)

- **Spec coverage, phase 1:**

  | Spec section | Tasks |
  |---|---|
  | § 2 states, except Pigro | 3 |
  | § 2 filters | 6 |
  | § 3 tables | 1 |
  | § 4.1 list | 19 |
  | § 4.2 four steps | 20 |
  | § 4.3 page and «Non scrivere mai» | 21 |
  | § 5.1 mail | 7 |
  | § 5.2 test | 10 |
  | § 5.3 checks | 6, 11 |
  | § 5.4 loop | 11, 12 |
  | § 6.1 webhook | 15, 16 |
  | § 7 unsubscribe | 13, 14 |
  | § 8 settings and deployment | 2, 12, 16 |
  | § 9 testing | every task |

  Deliberately left to phase 2: «Riscrivi a chi non ha fatto niente», the action
  stamping in the tick, clicks on the page, and the MCP tools. Phase 3 takes Pigro.
- **Deviations from the spec, each said where it happens:**
  - `contenuto_at` replaces the spec's «`prova_inviata_at` later than `updated_at`»
    (Task 1, Review Focus 1).
  - The unsubscribe token is stored as minted, not hashed, for Resend idempotency
    (Task 1, `DECISIONS.md` row).
  - Admin API paths are English (`/api/hub/campaigns`), while the public paths stay
    as the spec wrote them.
  - A lead is a sign-up with no card at all, a deleted one included, which is Talenti's
    rule, where the spec's § 2 said «no non-deleted card».
- **Placeholders:** none. Shared helpers live in `campaign_fixtures.py` and
  `campaign_api_flow.py`, so no test imports another test file.
- **Type consistency:** these names are used identically across tasks:
  - `Candidate`, `RenderTarget`, `RenderedMail`, `SendOutcome.esito`;
  - `CampaignService.schedule/send_test/back_to_draft/cancel`;
  - `run_tick(session, sender, settings, *, clock, pause)`;
  - `OptoutService.record/never_write/unsubscribe`;
  - `apply_event` and the outcomes `"applicato" | "ignorato" | "da_riprovare"`.
- **Review Focus:** each of the five has its test.
  1. Task 10's `test_an_edit_after_the_test_blocks_scheduling_until_a_new_test`, and
     Task 20's second test.
  2. Task 10's parametrised DST test, and the nonexistent and past times.
  3. Tasks 3 and 6: case, lead against card, one referente.
  4. Task 11's completed and deleted card.
  5. Task 15's `test_tests_strangers_and_early_events` and the delivery for a
     cancelled campaign.
