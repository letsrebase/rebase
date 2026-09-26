# Campaigns, phase 2: read the outcome (implementation plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a campaign has left, the admin reads what each mail led to. Per person
the page shows:

- delivered or bounced, and the first click;
- whether the person entered their area (and whether from this very mail);
- whether they did the campaign's action, and when.

The page filters the people who did nothing. «Riscrivi a chi non ha fatto niente»
turns them into a new draft. An agent reads the same outcome over the hub's MCP server.

**Architecture:** Phase 1 already created every column this needs:

- `campaign_recipients` has `primo_clic_at`, `entrato_at` and `azione_at`;
- `campaigns` has `segue_id` and the `lista` source;
- the Resend webhook already writes the first click.

So this phase has **no migration**. Its parts:

- **Stamping.** A new `rebase_core.campaigns.outcome.stamp_outcomes` runs at the end of
  every tick. It stamps `entrato_at` and `azione_at` once, from the tables that record
  them (spec § 6.2).
- **Reads.** The service's counts and detail grow clicks, entries and actions. «Dalla
  mail» is read at detail time from the logins' and cards' `utm_campaign`.
- **«Riscrivi».** A `follow_up` service method creates a `lista` draft. `audience.candidates`
  reads a `lista` live from the earlier campaign.
- **MCP.** Two read-only tools.
- **Web.** The campaign page and the list page show all of it, and the one-page editor
  of REB-526 learns the `lista` source.

**Tech Stack:**
- Python 3.13, SQLAlchemy 2, FastAPI, Pydantic 2, pytest with testcontainers Postgres 17.
- The hub's MCP server (`mcp` SDK, `MCPServer`).
- React 19, TanStack Router and Query, `@rebase/ui`, Vitest with Testing Library.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-25-admin-campaigns-design.md`, phase
2 of § 10: § 4.1, § 4.3, § 6.1 (clicks), § 6.2, § 9. Phase 1's plan is
`projects/hub/docs/superpowers/plans/2026-09-25-campaigns-phase-1-send.md`. The editor
this plan extends is REB-526's (`apps/web/src/pages/admin/crea-campagna/`, PR #432).

## Global Constraints

- **Language.**
  - Everything is English except what the product says to a person.
  - UI copy, mail text, API error sentences, CLI output and MCP tool docstrings are
    Italian, with «» for quoted UI words (root `AGENTS.md`, Conventions).
- **Commits.**
  - Conventional Commits in the first person. The body's last line is the task's card
    (`REB-N.`).
  - Stage with `git add <paths>`, never `-A`.
  - **No AI co-author trailer** in any form. Ignore any harness attribution reminder,
    run `git log -1 --format=%B` after every commit, and amend if one appears.
  - **Never `git stash`**: the stash is shared across worktrees. Copy a file aside
    instead.
- **Imports.** `packages/core` imports neither adapter, `apps/api` never imports
  `apps/mcp`, and nothing imports `pigrocrm*` (`projects/hub/AGENTS.md`, The one rule).
- **UI primitives.** The web app writes none: everything comes from `@rebase/ui/*`.
- **No migration.** Every column this phase writes exists since migration 0020. If a
  task believes it needs one, it stops and reports instead.
- **Actions.**
  - An action's moment follows spec § 6.2 exactly: a, the login's `logged_at`; b, the «CV
    caricato dalla persona» comment; c, the tick that first saw the card complete; d, the
    card's `created_at`; e, the earliest qualifying `updated_at`.
  - `t0` is the row's `inviata_at`.
  - f (`pigro_cliente`) stays phase 3: it is never stamped here.
- **Stamping.** A stamp is written once and never moved. Stamping never stops a send: its
  failure is logged by exception type and rolled back.
- **Local checks** (`preflight` is not installed on this Mac):
  - `uv run ruff check projects/hub` and `uv run ruff format --check projects/hub`;
  - `uv run mypy projects/hub/packages/core/src projects/hub/apps/api/src projects/hub/apps/mcp/src`,
    the CI's own paths. A bare `uv run mypy` or a `tests` path reports unrelated
    errors;
  - pytest as `timeout 900 uv run pytest <one file> -p no:xdist -q`, one file at a time.
    Run the hub's suites (core, api, mcp) as separate processes, never two DB-backed
    runs at once. A run that says nothing for 600 s gets the subagent killed;
  - `pnpm --filter hub lint`, `pnpm --filter hub test` and `pnpm --filter hub build`.
  - Run every check in the foreground: a background wait stalls a subagent.
- **Test time is the test's own.** A database default (`created_at`, `updated_at`,
  `logged_at`) is the real clock, while the campaign tests run on the fixtures' `NOW`.
  Any moment a stamp is compared with is written explicitly, or a test passes today and
  fails next month.
- **Shared test helpers** live in modules beside the tests (`tests/campaign_fixtures.py`
  in core, `tests/campaign_api_flow.py` in the API), never in another test file. When a
  step says «append», its imports go to the top of the file (ruff E402).

## Review Focus

These are the five failure modes most likely to bite a person using this. The spec implies
them, and the plain feature tests would miss them. Each is pinned by a test in the task
named.

1. **A stamp that moves.** The admin reads «entrato il 26/09». A login on 28/09 must
   not turn it into 28/09, and neither must a card edited again. A stamp is the first
   moment, written once (Task 1).
2. **«Riscrivi» reaching someone who has already acted.** The tick stamps once a minute
   and only for 30 days, so `azione_at IS NULL` alone is not proof of nothing done. The
   `lista` reads each person's action live before listing them (Task 3).
3. **«Riscrivi» inside the gap.** A follow-up drafted the day after the send lists
   everyone as excluded, «ha ricevuto un'altra campagna il …». That is the gap rule, and
   the page says so. Once the gap has passed, the same draft lists them (Task 3).
4. **A stamping error during a send.** A broken `prima` on an old row must not stop
   today's campaign from leaving (Task 1).
5. **«Dalla mail» on a forwarded mail.** A login with this campaign's slug but another
   person's code (`utm_term`) came from a forwarded mail. It is not «dalla mail» for
   this row (Task 2).

---

### Task 1: Stamp entries and actions from the tick

**Card:** REB-533.

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/actions.py`
- Create: `projects/hub/packages/core/src/rebase_core/campaigns/outcome.py`
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/tick.py`
- Modify: `projects/hub/packages/core/src/rebase_core/cli.py` (the `campaigns_tick` print line)
- Modify: `projects/hub/AGENTS.md` (the campaigns runbook, one paragraph)
- Test: `projects/hub/packages/core/tests/test_campaign_outcome.py` (create)
- Test: `projects/hub/packages/core/tests/test_campaign_tick.py` (append one test)
- Test: `projects/hub/packages/core/tests/test_cli.py` (the tick line)

**Interfaces:**
- Consumes: `CampaignRecipient`, `Campaign`, `Login`, `Comment` (`rebase_core.models`); `run_tick`, `TickResult` (`tick.py`).
- Produces:
  - `entered_at(session: Session, recipient: CampaignRecipient, *, since: datetime) -> datetime | None` in `actions.py`;
  - `done_at(session, recipient, azione, *, since: datetime | None = None, now: datetime | None = None) -> datetime | None` in `actions.py`, where `since` defaults to `prima["t"]` as before;
  - `STAMP_WINDOW: timedelta` and `stamp_outcomes(session: Session, *, now: datetime) -> int` in `outcome.py`;
  - `TickResult.stampate: int`.

- [ ] **Step 1: Write the failing tests**

Create `projects/hub/packages/core/tests/test_campaign_outcome.py`:

```python
"""What a sent mail led to, stamped once by the tick (spec § 6.2)."""

from datetime import datetime, timedelta

from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    clean,
    company,
    draft,
    lead,
    person,
)
from sqlalchemy import update
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import CV_COMMENT_PREFIX, done_at
from rebase_core.campaigns.outcome import STAMP_WINDOW, stamp_outcomes
from rebase_core.campaigns.schemas import ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import CampaignService
from rebase_core.campaigns.tick import run_tick
from rebase_core.models import Campaign, CampaignRecipient, Comment, Company, Freelancer, Login, User

NO_PAUSE = lambda _seconds: None  # noqa: E731


def sent(session: Session, clock: Clock, **fields: object) -> Campaign:
    """A campaign created, tested, scheduled for now and sent by one tick, the people
    already in the tables."""
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    created = service.create(who.id, draft(**fields))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    run_tick(session, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def only_row(session: Session, campaign: Campaign) -> CampaignRecipient:
    session.expire_all()
    return session.query(CampaignRecipient).filter_by(campaign_id=campaign.id).one()


def test_a_login_after_the_mail_stamps_the_entry_and_the_entrato_action(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, stato_percorso="completo", azione="entrato")
    row = only_row(clean, campaign)
    assert row.stato == "inviata" and row.entrato_at is None
    at = clock.at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=at))
    clean.commit()
    assert stamp_outcomes(clean, now=at + timedelta(minutes=1)) == 1
    row = only_row(clean, campaign)
    assert (row.entrato_at, row.azione_at) == (at, at)


def test_a_stamp_is_written_once_and_never_moves(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 1: a second login does not move «entrato» to its own day."""
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, stato_percorso="completo", azione="entrato")
    first = clock.at + timedelta(hours=2)
    clean.add(Login(user_id=card.user_id, logged_at=first))
    clean.commit()
    stamp_outcomes(clean, now=first)
    clean.add(Login(user_id=card.user_id, logged_at=first + timedelta(days=2)))
    clean.commit()
    assert stamp_outcomes(clean, now=first + timedelta(days=2)) == 0
    assert only_row(clean, campaign).entrato_at == first


def test_a_cv_is_stamped_at_the_comment_the_member_service_writes(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it", cv=False)
    campaign = sent(clean, clock)  # `manca_cv`, action `cv`
    at = clock.at + timedelta(hours=3)
    clean.execute(update(Freelancer).where(Freelancer.id == card.id).values(cv_size=4, cv_bytes=b"%PDF"))
    clean.add(
        Comment(
            entity_type="freelancer",
            entity_id=card.id,
            testo=f"{CV_COMMENT_PREFIX}: cv.pdf",
            autore="Ada",
            created_at=at,
        )
    )
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(hours=1))
    assert only_row(clean, campaign).azione_at == at


def test_a_card_that_became_complete_is_stamped_at_the_tick_that_saw_it(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it", cv=False, tariffa=False)
    campaign = sent(clean, clock, stato_percorso="scheda_vuota_nuovi", azione="scheda_completa")
    clean.execute(
        update(Freelancer)
        .where(Freelancer.id == card.id)
        .values(cv_size=4, cv_bytes=b"%PDF", tariffa_giornaliera=450)
    )
    clean.commit()
    seen = clock.at + timedelta(days=1)
    stamp_outcomes(clean, now=seen)
    assert only_row(clean, campaign).azione_at == seen


def test_a_card_created_after_the_mail_is_stamped_at_its_creation(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    lead(clean, "giulia@studio.it")
    campaign = sent(
        clean, clock, stato_percorso="lead", azione="profilo_creato", bottone_meta="wizard"
    )
    at = clock.at + timedelta(hours=5)
    card = person(clean, "giulia@studio.it", nome="Giulia")
    clean.execute(update(Freelancer).where(Freelancer.id == card.id).values(created_at=at))
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(minutes=5))
    assert only_row(clean, campaign).azione_at == at


def test_an_updated_request_is_stamped_at_its_update(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    row_company = company(clean, "ciro@block.it")
    # Every moment here is the test's own: a database default is the real clock, and a
    # real «now» far from `NOW` would put the mail outside `STAMP_WINDOW`.
    clean.execute(
        update(Company).where(Company.id == row_company.id).values(updated_at=NOW - timedelta(days=1))
    )
    clean.commit()
    campaign = sent(
        clean,
        clock,
        stato_percorso="azienda_aperta",
        azione="richiesta_aggiornata",
        bottone_meta="richiesta",
    )
    at = clock.at + timedelta(days=2)
    clean.execute(update(Company).where(Company.id == row_company.id).values(updated_at=at))
    clean.commit()
    stamp_outcomes(clean, now=at + timedelta(minutes=1))
    assert only_row(clean, campaign).azione_at == at


def test_a_mail_older_than_the_window_is_never_stamped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, stato_percorso="completo", azione="entrato")
    long_ago = clock.at - STAMP_WINDOW - timedelta(days=1)
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(inviata_at=long_ago)
    )
    clean.add(Login(user_id=card.user_id, logged_at=clock.at))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at) == 0
    assert only_row(clean, campaign).entrato_at is None


def test_a_skipped_row_is_never_stamped(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    card = person(clean, "ada@studio.it")
    campaign = sent(clean, clock, stato_percorso="completo", azione="entrato")
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == campaign.id)
        .values(stato="saltata")
    )
    clean.add(Login(user_id=card.user_id, logged_at=clock.at + timedelta(hours=1)))
    clean.commit()
    assert stamp_outcomes(clean, now=clock.at + timedelta(hours=2)) == 0


def test_a_person_without_a_user_has_no_entry_to_stamp(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    lead(clean, "giulia@studio.it")
    campaign = sent(
        clean, clock, stato_percorso="lead", azione="profilo_creato", bottone_meta="wizard"
    )
    assert stamp_outcomes(clean, now=clock.at + timedelta(hours=1)) == 0
    row = only_row(clean, campaign)
    assert (row.entrato_at, row.azione_at) == (None, None)
    assert clean.query(User).filter(User.email == "giulia@studio.it").count() == 0


def test_the_pigro_action_is_left_to_phase_3(clean: Session) -> None:  # noqa: F811  (fixture)
    """`pigro_cliente` needs the CRM's usage endpoint (spec § 6.3): never stamped here."""
    row = CampaignRecipient(email="a@b.it", tipo="freelancer", codice="1", prima={"t": NOW.isoformat()})
    assert done_at(clean, row, "pigro_cliente", since=NOW) is None
```

Append to `projects/hub/packages/core/tests/test_campaign_tick.py` (its imports go to the
top of the file):

```python
def test_a_stamping_error_is_logged_and_the_send_still_happens(
    clean: Session,  # noqa: F811  (fixture)
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review Focus 4: the outcome never stops a send."""
    import rebase_core.campaigns.tick as tick_module

    def broken(_session: Session, *, now: datetime) -> int:
        raise KeyError("t")

    monkeypatch.setattr(tick_module, "stamp_outcomes", broken)
    clock = Clock(NOW)
    campaign = scheduled(clean, clock, "a@studio.it")
    with caplog.at_level(logging.ERROR):
        result = run_tick(clean, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.inviate == 1 and result.stampate == 0
    assert rows(clean, campaign)["a@studio.it"].stato == "inviata"
    assert "outcome stamping failed this tick: KeyError" in caplog.text
    assert "a@studio.it" not in caplog.text
```

In `projects/hub/packages/core/tests/test_cli.py`, change the tick test's fake and expected
line:

```python
    monkeypatch.setattr(
        cli,
        "run_tick",
        lambda *_a, **_k: TickResult(campagne=1, inviate=2, saltate=1, fallite=0, stampate=3),
    )
    assert main(["campaigns-tick"]) == 0
    assert (
        capsys.readouterr().out.strip()
        == "1 campagne, 2 inviate, 1 saltate, 0 fallite, 3 esiti registrati"
    )
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest projects/hub/packages/core/tests/test_campaign_outcome.py projects/hub/packages/core/tests/test_cli.py -p no:xdist -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'rebase_core.campaigns.outcome'`, and the CLI test on `stampate`.

- [ ] **Step 3: Implement**

In `actions.py`, replace the module docstring's last sentence and the `done_at`
function. Add `entered_at`. The rest of the file stays as it is:

```python
"""What an action is measured against, and whether a person has done it since (spec
§ 6.2). The send-time check (§ 5.3) asks `done_at` whether a mail would ask for
something already done; the tick's `stamp_outcomes` (`outcome.py`) asks it when."""
```

```python
def entered_at(session: Session, recipient: CampaignRecipient, *, since: datetime) -> datetime | None:
    """Action a: the recipient's first login after `since`. A lead has no user until it
    makes a card, and a person with no user has not entered."""
    user_id = recipient.user_id or session.scalar(
        select(User.id).where(func.lower(User.email) == recipient.email)
    )
    if user_id is None:
        return None
    return session.scalar(
        select(func.min(Login.logged_at)).where(Login.user_id == user_id, Login.logged_at > since)
    )


def done_at(
    session: Session,
    recipient: CampaignRecipient,
    azione: str,
    *,
    since: datetime | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """When the recipient did `azione`, or `None`. `since` is what the action must
    follow: the list's snapshot (`prima["t"]`) when omitted, which is what the
    send-time check wants, and the mail's own `inviata_at` when the tick stamps the
    outcome. Completing a card has no moment of its own (spec § 6.2), so `c` answers
    `now`, the tick that saw it, when given, and the card's `updated_at` otherwise."""
    prima = recipient.prima or {}
    moment = since if since is not None else datetime.fromisoformat(prima["t"])
    if azione == "entrato":
        return entered_at(session, recipient, since=moment)
    if azione in ("cv", "scheda_completa", "profilo_creato"):
        card = _card(session, recipient.email)
        if card is None:
            return None
        if azione == "profilo_creato":
            created = not prima.get("ha_scheda") and card.created_at > moment
            return card.created_at if created else None
        if azione == "cv":
            if prima.get("ha_cv") or card.cv_size is None:
                return None
            commented = session.scalar(
                select(func.min(Comment.created_at)).where(
                    Comment.entity_type == "freelancer",
                    Comment.entity_id == card.id,
                    Comment.testo.startswith(CV_COMMENT_PREFIX),
                    Comment.created_at > moment,
                )
            )
            return commented or card.updated_at
        if prima.get("completa") or not _complete(card):
            return None
        return now or card.updated_at
    if azione == "richiesta_aggiornata":
        before = prima.get("richieste") or {}
        moments: list[datetime] = []
        for company_id, was in before.items():
            updated = session.scalar(select(Company.updated_at).where(Company.id == company_id))
            if updated is not None and updated > max(datetime.fromisoformat(was), moment):
                moments.append(updated)
        return min(moments) if moments else None
    return None  # `pigro_cliente`: phase 3 (spec § 6.3)
```

Create `outcome.py`:

```python
"""What a sent mail led to (spec § 6.2): when the person entered their area and when they
did the campaign's action, each stamped once on the recipient row. The tick calls
`stamp_outcomes` after sending. The campaign page and the MCP tools read the stamped
columns and run no action query of their own.

Only rows sent in the last `STAMP_WINDOW` are read, and only the ones still missing a
stamp, so a pass costs a few queries per open row, not per row ever sent. A stamp is
written once and never moved: a second login does not turn «entrato il 26/09» into the
28th."""

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from rebase_core.campaigns.actions import done_at, entered_at
from rebase_core.models import Campaign, CampaignRecipient

STAMP_WINDOW = timedelta(days=30)


def stamp_outcomes(session: Session, *, now: datetime) -> int:
    """Stamps every open sent row once, commits, and answers how many rows got a new
    stamp. `now` is the moment a card seen complete is stamped with."""
    pending = session.execute(
        select(CampaignRecipient, Campaign.azione)
        .join(Campaign, Campaign.id == CampaignRecipient.campaign_id)
        .where(
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.inviata_at >= now - STAMP_WINDOW,
            or_(CampaignRecipient.entrato_at.is_(None), CampaignRecipient.azione_at.is_(None)),
        )
        .order_by(CampaignRecipient.inviata_at, CampaignRecipient.id)
    ).all()
    stamped = 0
    for row, azione in pending:
        t0 = row.inviata_at
        if t0 is None:  # excluded by the query; here for the type checker
            continue
        changed = False
        if row.entrato_at is None:
            entered = entered_at(session, row, since=t0)
            if entered is not None:
                row.entrato_at, changed = entered, True
        if row.azione_at is None:
            done = row.entrato_at if azione == "entrato" else done_at(session, row, azione, since=t0, now=now)
            if done is not None:
                row.azione_at, changed = done, True
        if changed:
            stamped += 1
    session.commit()
    return stamped
```

In `tick.py`:

- import `from rebase_core.campaigns.outcome import stamp_outcomes`;
- add `stampate: int = 0` to `TickResult`;
- in `run_tick`, after the `for campaign_id in due_ids:` loop and still inside the
  `try:` that holds the advisory lock, add:

```python
            try:
                result.stampate = stamp_outcomes(session, now=clock())
            except Exception as exc:  # the outcome must never stop a send (R14)
                session.rollback()
                _log.error("outcome stamping failed this tick: %s", type(exc).__name__)
```

Then add one sentence to the module docstring's last paragraph: «After the sends, the
same pass stamps the outcome of the last 30 days' mails (`outcome.py`); a failure there is
logged by type and rolled back, and never undoes a send.»

In `cli.py`'s `campaigns_tick`, the print becomes:

```python
    print(
        f"{result.campagne} campagne, {result.inviate} inviate, "
        f"{result.saltate} saltate, {result.fallite} fallite, "
        f"{result.stampate} esiti registrati"
    )
```

In `projects/hub/AGENTS.md`, after the paragraph that starts «Since P-REB-41 «Invia una
campagna» the `campaigns` service…», add:

```markdown
**The same pass stamps what each mail led to** (P-REB-41 phase 2, `rebase_core.campaigns.outcome`).
For every row sent in the last 30 days and still missing a stamp, it writes `entrato_at`
(the first login after the mail) and `azione_at` (the campaign's action, from the table
that records it: the CV comment, the card's `created_at`, the request's `updated_at`, or
the tick itself for a card that became complete). A stamp is written once. The log line
ends with `N esiti registrati`. Without a Resend key the tick does not run, so the preview
stamps nothing.
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest projects/hub/packages/core/tests/test_campaign_outcome.py projects/hub/packages/core/tests/test_campaign_tick.py projects/hub/packages/core/tests/test_campaign_audience.py projects/hub/packages/core/tests/test_cli.py -p no:xdist -q`
Expected: all pass. The tick and audience files prove `done_at`'s default `since` still
serves the send-time check.

Then ruff check, ruff format --check and mypy, as the Global Constraints say.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/actions.py \
  projects/hub/packages/core/src/rebase_core/campaigns/outcome.py \
  projects/hub/packages/core/src/rebase_core/campaigns/tick.py \
  projects/hub/packages/core/src/rebase_core/cli.py projects/hub/AGENTS.md \
  projects/hub/packages/core/tests/test_campaign_outcome.py \
  projects/hub/packages/core/tests/test_campaign_tick.py \
  projects/hub/packages/core/tests/test_cli.py
git commit -m "feat(hub): the tick stamps when each person entered and did the campaign's action" -m "<body: why, what stays phase 3>" -m "REB-533."
```

---

### Task 2: Count clicks, entries and actions, and say which came from the mail

**Card:** REB-534.

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/schemas.py`
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/service.py` (`_counts`, `detail`, a new `_from_mail`)
- Test: `projects/hub/packages/core/tests/test_campaign_service.py` (append)

**Interfaces:**
- Consumes: the stamped `entrato_at`/`azione_at` of Task 1 (the tests write them directly).
- Produces:
  - `CampaignCounts.cliccate`, `.entrate`, `.azioni` (int, default 0);
  - `RecipientRead.primo_clic_at`, `.reclamo_at`, `.entrato_at`, `.azione_at` (`datetime | None`);
  - `RecipientRead.entrato_dalla_mail`, `.azione_dalla_mail` (`bool`, default `False`);
  - `CampaignRead.segue_id: UUID | None`.

- [ ] **Step 1: Write the failing tests**

Append to `projects/hub/packages/core/tests/test_campaign_service.py`. Its imports go to the
top: `campaign_row`, `T0` from `campaign_fixtures`; `Login`, `Freelancer`, `User`,
`CampaignRecipient` from `rebase_core.models`; `timedelta` from `datetime`.

```python
def _recipient(session: Session, campaign_id: object, email: str, **fields: object) -> CampaignRecipient:
    values: dict[str, object] = {
        "campaign_id": campaign_id,
        "email": email,
        "tipo": "freelancer",
        "codice": "c0de0001",
        "prima": {"t": T0.isoformat()},
        "disiscrizione_token": f"tok-{email}",
        "stato": "inviata",
        "inviata_at": T0,
    }
    values.update(fields)
    row = CampaignRecipient(**values)
    session.add(row)
    session.commit()
    return row


def test_the_counts_add_clicks_entries_and_actions(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(clean, stato="inviata", inviata_at=T0)
    later = T0 + timedelta(hours=1)
    _recipient(clean, campaign.id, "a@studio.it", primo_clic_at=later, entrato_at=later, azione_at=later)
    _recipient(clean, campaign.id, "b@studio.it", primo_clic_at=later, entrato_at=later)
    _recipient(clean, campaign.id, "c@studio.it", primo_clic_at=later)
    _recipient(clean, campaign.id, "d@studio.it", stato="saltata", inviata_at=None)
    counts = CampaignService(clean, SETTINGS).detail(campaign.id).conteggi
    assert (counts.inviate, counts.cliccate, counts.entrate, counts.azioni) == (3, 3, 2, 1)
    listed = CampaignService(clean, SETTINGS).list_all().items[0].conteggi
    assert listed == counts


def test_dalla_mail_needs_this_campaigns_slug_and_this_persons_code(clean: Session) -> None:  # noqa: F811  (fixture)
    """Review Focus 5: a forwarded mail carries the slug with somebody else's code."""
    campaign = campaign_row(clean, stato="inviata", inviata_at=T0, azione="entrato")
    later = T0 + timedelta(hours=1)
    ada = User(email="ada@studio.it", nome="Ada", cognome="L")
    bob = User(email="bob@studio.it", nome="Bob", cognome="L")
    clean.add_all([ada, bob])
    clean.flush()
    clean.add(Login(user_id=ada.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa"))
    clean.add(Login(user_id=bob.id, logged_at=later, utm_campaign=campaign.slug, utm_term="c0de00aa"))
    clean.commit()
    _recipient(clean, campaign.id, "ada@studio.it", codice="c0de00aa", entrato_at=later, azione_at=later)
    _recipient(clean, campaign.id, "bob@studio.it", codice="c0de00bb", entrato_at=later, azione_at=later)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert (rows["ada@studio.it"].entrato_dalla_mail, rows["ada@studio.it"].azione_dalla_mail) == (True, True)
    assert (rows["bob@studio.it"].entrato_dalla_mail, rows["bob@studio.it"].azione_dalla_mail) == (False, False)


def test_a_card_that_carries_the_slug_was_created_from_the_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    campaign = campaign_row(
        clean,
        stato="inviata",
        inviata_at=T0,
        stato_percorso="lead",
        azione="profilo_creato",
        bottone_meta="wizard",
    )
    later = T0 + timedelta(hours=1)
    giulia = User(email="giulia@studio.it", nome="Giulia", cognome="B")
    clean.add(giulia)
    clean.flush()
    clean.add(Freelancer(user_id=giulia.id, links=[], utm_campaign=campaign.slug))
    clean.commit()
    _recipient(clean, campaign.id, "giulia@studio.it", tipo="lead", azione_at=later)
    _recipient(clean, campaign.id, "nina@studio.it", tipo="lead", azione_at=later)
    rows = {r.email: r for r in CampaignService(clean, SETTINGS).detail(campaign.id).destinatari}
    assert rows["giulia@studio.it"].azione_dalla_mail is True
    assert rows["nina@studio.it"].azione_dalla_mail is False
    assert rows["giulia@studio.it"].entrato_dalla_mail is False
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest projects/hub/packages/core/tests/test_campaign_service.py -p no:xdist -q`
Expected: FAIL on `CampaignCounts` having no `cliccate` and `RecipientRead` having no `entrato_dalla_mail`.

- [ ] **Step 3: Implement**

In `schemas.py`:

```python
class CampaignCounts(BaseModel):
    destinatari: int = 0
    in_coda: int = 0
    inviate: int = 0
    saltate: int = 0
    fallite: int = 0
    consegnate: int = 0
    rimbalzate: int = 0
    cliccate: int = 0
    entrate: int = 0
    azioni: int = 0
```

Add `segue_id: UUID | None` to `CampaignRead`, after `filtri`. In `RecipientRead`, after
`rimbalzata_at`, add:

```python
    primo_clic_at: datetime | None
    reclamo_at: datetime | None
    entrato_at: datetime | None
    azione_at: datetime | None
    # Read at detail time, never stored: the login or the card carries this campaign's
    # link (REB-426), so the mail was the door (spec § 4.3).
    entrato_dalla_mail: bool = False
    azione_dalla_mail: bool = False
```

In `service.py`, `_counts` selects three more columns and fills the new fields:

```python
                func.count(r.rimbalzata_at),
                func.count(r.primo_clic_at),
                func.count(r.entrato_at),
                func.count(r.azione_at),
```

```python
                rimbalzate=row[7],
                cliccate=row[8],
                entrate=row[9],
                azioni=row[10],
```

Replace `detail` and add `_from_mail` beside it (imports: `Freelancer`, `Login`, `User` from
`rebase_core.models`; `Sequence` from `collections.abc`):

```python
    def detail(self, campaign_id: UUID) -> CampaignDetail:
        campaign = self._require(campaign_id)
        rows = self.session.scalars(
            select(CampaignRecipient)
            .where(CampaignRecipient.campaign_id == campaign_id)
            .order_by(CampaignRecipient.email)
        ).all()
        entered, acted = self._from_mail(campaign, rows)
        return CampaignDetail(
            campagna=CampaignRead.model_validate(campaign),
            conteggi=self._counts([campaign_id]).get(campaign_id, CampaignCounts()),
            destinatari=[
                RecipientRead.model_validate(r).model_copy(
                    update={"entrato_dalla_mail": r.id in entered, "azione_dalla_mail": r.id in acted}
                )
                for r in rows
            ],
        )

    def _from_mail(
        self, campaign: Campaign, rows: Sequence[CampaignRecipient]
    ) -> tuple[set[UUID], set[UUID]]:
        """Which stamped rows came from this very mail (spec § 4.3, § 6.2). A login counts
        when it carries the campaign's slug and the row's own code: the slug alone is also
        on a forwarded mail. A card created for `profilo_creato` counts when it stored the
        slug. Two queries for the whole list."""
        pairs = set(
            self.session.execute(
                select(func.lower(User.email), Login.utm_term)
                .join(User, User.id == Login.user_id)
                .where(Login.utm_campaign == campaign.slug)
            ).all()
        )
        entered = {r.id for r in rows if r.entrato_at is not None and (r.email, r.codice) in pairs}
        if campaign.azione == "entrato":
            return entered, entered
        if campaign.azione != "profilo_creato":
            return entered, set()
        cards = set(
            self.session.scalars(
                select(func.lower(User.email))
                .join(Freelancer, Freelancer.user_id == User.id)
                .where(Freelancer.utm_campaign == campaign.slug, Freelancer.deleted_at.is_(None))
            )
        )
        return entered, {r.id for r in rows if r.azione_at is not None and r.email in cards}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest projects/hub/packages/core/tests/test_campaign_service.py projects/hub/packages/core/tests/test_campaign_tick.py -p no:xdist -q`
Expected: PASS. Then run ruff, ruff format --check and mypy as in the Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/schemas.py \
  projects/hub/packages/core/src/rebase_core/campaigns/service.py \
  projects/hub/packages/core/tests/test_campaign_service.py
git commit -m "feat(hub): a campaign counts clicks, entries and actions, and marks the ones from the mail" -m "<body>" -m "REB-534."
```

---

### Task 3: «Riscrivi a chi non ha fatto niente»: the `lista` draft and its list

**Card:** REB-535.

**Files:**
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/audience.py` (`candidates` for `lista`, a new `_not_done`; drop `LISTA_LATER`)
- Modify: `projects/hub/packages/core/src/rebase_core/campaigns/service.py` (`follow_up`, `_validate`, `update`)
- Modify: `projects/hub/apps/api/src/rebase_api/routers/campaigns.py` (`POST /{campaign_id}/follow-up`)
- Modify: `projects/hub/AGENTS.md` (one paragraph in the campaigns runbook)
- Test: `projects/hub/packages/core/tests/test_campaign_follow_up.py` (create)
- Test: `projects/hub/apps/api/tests/test_campaigns_api.py` (append)

**Interfaces:**
- Consumes: `done_at(..., since=...)` of Task 1; `CampaignRead.segue_id` of Task 2.
- Produces:
  - `CampaignService.follow_up(campaign_id: UUID, admin_id: UUID) -> CampaignRead`;
  - the constants `FOLLOW_UP_SUFFIX = " · riscrivi"`, `ONLY_SENT`, `NOTHING_TO_FOLLOW` and `LIST_IS_FIXED` in `service.py`;
  - `POST /api/hub/campaigns/{campaign_id}/follow-up`, which answers 201 with a `CampaignRead`, or 409 with the sentence.

- [ ] **Step 1: Write the failing tests**

Create `projects/hub/packages/core/tests/test_campaign_follow_up.py`:

```python
"""«Riscrivi a chi non ha fatto niente» (spec § 4.3): a `lista` draft that follows a sent
campaign, and its list."""

from datetime import timedelta

import pytest
from campaign_fixtures import (  # noqa: F401  (fixture)
    NOW,
    SETTINGS,
    Clock,
    admin,
    as_admin,
    clean,
    draft,
    person,
)
from sqlalchemy import update
from sqlalchemy.orm import Session

from rebase_core.campaigns.schemas import CampaignPatch, ScheduleRequest
from rebase_core.campaigns.sender import RecordingCampaignSender
from rebase_core.campaigns.service import (
    LIST_IS_FIXED,
    NOTHING_TO_FOLLOW,
    ONLY_SENT,
    CampaignService,
)
from rebase_core.campaigns.tick import run_tick
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.models import Campaign, CampaignRecipient, Login, User

NO_PAUSE = lambda _seconds: None  # noqa: E731
GAP = timedelta(days=SETTINGS.campaign_gap_days, hours=1)


def sent_to(session: Session, clock: Clock, *emails: str, azione: str = "entrato") -> Campaign:
    """A `completo` campaign sent by one tick to complete cards at `emails`."""
    service = CampaignService(session, SETTINGS, clock=clock)
    who = admin(session)
    for email in emails:
        person(session, email)
    created = service.create(who.id, draft(stato_percorso="completo", azione=azione))
    clock.at += timedelta(minutes=1)
    service.send_test(created.id, as_admin(who), RecordingCampaignSender())
    service.schedule(created.id, ScheduleRequest())
    run_tick(session, RecordingCampaignSender(), SETTINGS, clock=clock, pause=NO_PAUSE)
    return session.get(Campaign, created.id)  # type: ignore[return-value]


def test_a_follow_up_is_a_lista_draft_with_the_same_action_and_mail(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    assert (follow.fonte, follow.segue_id, follow.stato) == ("lista", parent.id, "bozza")
    assert (follow.azione, follow.oggetto, follow.testo) == (parent.azione, parent.oggetto, parent.testo)
    assert follow.nome == f"{parent.nome} · riscrivi" and follow.slug != parent.slug


def test_only_a_sent_campaign_with_someone_waiting_is_followed_up(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    service = CampaignService(clean, SETTINGS, clock=clock)
    draft_only = service.create(admin(clean).id, draft())
    with pytest.raises(InvalidState, match=ONLY_SENT):
        service.follow_up(draft_only.id, admin(clean).id)
    parent = sent_to(clean, clock, "ada@studio.it")
    clean.execute(
        update(CampaignRecipient)
        .where(CampaignRecipient.campaign_id == parent.id)
        .values(azione_at=clock.at)
    )
    clean.commit()
    with pytest.raises(InvalidState, match=NOTHING_TO_FOLLOW):
        service.follow_up(parent.id, admin(clean).id)


def test_the_list_is_who_did_nothing_read_live_and_the_gap_shows_as_a_reason(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    """Review Focus 2 and 3."""
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it", "bob@studio.it", "cleo@studio.it")
    stamped = clock.at + timedelta(hours=1)
    rows = {r.email: r for r in clean.query(CampaignRecipient).filter_by(campaign_id=parent.id)}
    rows["ada@studio.it"].azione_at = stamped  # stamped by the tick
    bob = clean.query(User).filter(User.email == "bob@studio.it").one()
    clean.add(Login(user_id=bob.id, logged_at=stamped))  # did it, not stamped yet
    clean.commit()
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    preview = service.audience(follow.id)
    assert [(r.email, r.escluso) for r in preview.righe] == [
        ("cleo@studio.it", f"ha ricevuto un'altra campagna il {clock.at:%d/%m}")
    ]
    clock.at += GAP
    preview = CampaignService(clean, SETTINGS, clock=clock).audience(follow.id)
    assert [(r.email, r.escluso) for r in preview.righe] == [("cleo@studio.it", None)]


def test_a_follow_up_is_scheduled_and_sent_like_any_campaign(clean: Session) -> None:  # noqa: F811  (fixture)
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    clock.at += GAP
    service = CampaignService(clean, SETTINGS, clock=clock)
    who = admin(clean)
    follow = service.follow_up(parent.id, who.id)
    clock.at += timedelta(minutes=1)
    service.send_test(follow.id, as_admin(who), RecordingCampaignSender())
    service.schedule(follow.id, ScheduleRequest())
    recording = RecordingCampaignSender()
    result = run_tick(clean, recording, SETTINGS, clock=clock, pause=NO_PAUSE)
    assert result.inviate == 1 and recording.sent[0].mail.to == "ada@studio.it"


def test_a_follow_up_keeps_its_list_and_action_but_its_mail_is_rewritten(
    clean: Session,  # noqa: F811  (fixture)
) -> None:
    clock = Clock(NOW)
    parent = sent_to(clean, clock, "ada@studio.it")
    service = CampaignService(clean, SETTINGS, clock=clock)
    follow = service.follow_up(parent.id, admin(clean).id)
    edited = service.update(follow.id, CampaignPatch(oggetto="Ti scrivo di nuovo"))
    assert edited.oggetto == "Ti scrivo di nuovo" and edited.fonte == "lista"
    with pytest.raises(ValidationFailed, match=LIST_IS_FIXED):
        service.update(follow.id, CampaignPatch(azione="cv"))
    with pytest.raises(ValidationFailed, match=LIST_IS_FIXED):
        service.update(follow.id, CampaignPatch(fonte="stato", stato_percorso="completo"))
```

Append to `projects/hub/apps/api/tests/test_campaigns_api.py`. It reuses the module's
`login_admin` and `a_draft`. Add to the file's top the imports it needs: `datetime`,
`UTC`, `UUID`, `update` from `sqlalchemy`, and `CampaignRecipient`. The test writes the
sent row by hand, because `run_tick` is core's:

```python
def test_riscrivi_opens_a_lista_draft_and_refuses_a_draft(
    client: TestClient,
    tidy: Session,  # noqa: F811  (fixture)
    sender: RecordingSender,
) -> None:
    login_admin(client, sender, tidy)
    draft_id = a_draft(client)
    refused = client.post(f"/api/hub/campaigns/{draft_id}/follow-up")
    assert refused.status_code == 409 and "già inviata" in refused.json()["detail"]
    tidy.execute(update(Campaign).where(Campaign.id == UUID(draft_id)).values(stato="inviata"))
    campaign = tidy.get(Campaign, UUID(draft_id))
    assert campaign is not None
    tidy.add(
        CampaignRecipient(
            campaign_id=campaign.id,
            email="ada@studio.it",
            tipo="freelancer",
            codice="1",
            prima={},
            disiscrizione_token="t-riscrivi",
            stato="inviata",
            inviata_at=datetime.now(UTC),
        )
    )
    tidy.commit()
    created = client.post(f"/api/hub/campaigns/{draft_id}/follow-up")
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["fonte"], body["segue_id"], body["stato"]) == ("lista", draft_id, "bozza")
    detail = client.get(f"/api/hub/campaigns/{draft_id}").json()
    assert detail["conteggi"]["azioni"] == 0
    assert detail["destinatari"][0]["entrato_dalla_mail"] is False
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest projects/hub/packages/core/tests/test_campaign_follow_up.py -p no:xdist -q`
Expected: FAIL with `ImportError: cannot import name 'LIST_IS_FIXED'`.

- [ ] **Step 3: Implement**

In `audience.py`, remove `LISTA_LATER` and add `from uuid import UUID` if it is not
imported yet, plus `from rebase_core.campaigns.actions import done_at`. Then `candidates`
becomes:

```python
def candidates(session: Session, campaign: Campaign) -> list[Candidate]:
    if campaign.fonte == "stato":
        found = candidates_for_state(session, campaign.stato_percorso or "")
    elif campaign.fonte == "filtri":
        found = _filtered(session, campaign.filtri or {})
    else:
        found = _not_done(session, campaign)
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in found:
        if candidate.email not in seen:
            seen.add(candidate.email)
            unique.append(candidate)
    return unique


def _not_done(session: Session, campaign: Campaign) -> list[Candidate]:
    """A `lista` (spec § 4.3): whom the earlier campaign reached and who has not done its
    action. Read live, not from `azione_at` alone: the tick stamps once a minute and
    only for 30 days after a mail, and whoever acted since must not be written to
    again (Review Focus 2). Each person keeps the snapshot links the earlier row
    froze: their user, card, lead and open requests."""
    parent = session.get(Campaign, campaign.segue_id) if campaign.segue_id else None
    if parent is None:
        return []
    rows = session.scalars(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.campaign_id == parent.id,
            CampaignRecipient.stato == "inviata",
            CampaignRecipient.azione_at.is_(None),
        )
        .order_by(CampaignRecipient.email)
    ).all()
    return [
        Candidate(
            email=row.email,
            nome=row.nome,
            tipo=row.tipo,
            user_id=row.user_id,
            freelancer_id=row.freelancer_id,
            signup_id=row.signup_id,
            company_ids=tuple(UUID(key) for key in (row.prima or {}).get("richieste", {})),
            pigro_slugs=tuple(row.pigro_slugs),
        )
        for row in rows
        if done_at(session, row, parent.azione, since=row.inviata_at) is None
    ]
```

In `service.py`:

- Add the constants next to `NOT_A_DRAFT`:

```python
FOLLOW_UP_SUFFIX = " · riscrivi"
ONLY_SENT = "Si riscrive solo a chi ha ricevuto una campagna già inviata."
NOTHING_TO_FOLLOW = "Hanno fatto tutti l'azione: non c'è nessuno a cui riscrivere."
LIST_IS_FIXED = (
    "Chi riceve una «Riscrivi» e cosa misura li decide la campagna da cui viene: "
    "si cambia solo la mail."
)
```

- `_validate`: the `stato` branch stays the same; the filters branch becomes
  `elif fonte == "filtri" and not has_filters:`, so a `lista` needs neither a state nor
  filters. Its list is the earlier campaign's, which `ck_campaigns_segue` enforces.
- `update`: right after `changes = data.model_dump(exclude_unset=True)` and the `filtri`
  conversion, before the `nome` handling, add:

```python
        if campaign.fonte == "lista":
            fixed = [
                field
                for field in ("fonte", "stato_percorso", "filtri", "azione")
                if field in changes and changes[field] != getattr(campaign, field)
            ]
            if fixed:
                raise ValidationFailed(ENTITY, fixed[0], LIST_IS_FIXED)
```

- Add `follow_up` after `cancel`, with `CAMPAIGN_NAME_MAX_LENGTH` imported from
  `rebase_core.models`:

```python
    def follow_up(self, campaign_id: UUID, admin_id: UUID) -> CampaignRead:
        """«Riscrivi a chi non ha fatto niente» (spec § 4.3): a `bozza` with `fonte =
        lista` that follows `campaign_id`, with the same action and a copy of its mail to
        rewrite. Its list is the sent rows with no action, read when shown and frozen
        when scheduled, like any other."""
        parent = self._require(campaign_id)
        if parent.stato != "inviata":
            raise InvalidState(ONLY_SENT)
        waiting = self.session.scalar(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == parent.id,
                CampaignRecipient.stato == "inviata",
                CampaignRecipient.azione_at.is_(None),
            )
        )
        if not waiting:
            raise InvalidState(NOTHING_TO_FOLLOW)
        now = self.clock()
        nome = f"{parent.nome}{FOLLOW_UP_SUFFIX}"[:CAMPAIGN_NAME_MAX_LENGTH]
        campaign = Campaign(
            created_by=admin_id,
            nome=nome,
            slug=self._unique_slug(f"c-{now:%Y-%m-%d}-{_slugify(nome)}"[:70]),
            fonte="lista",
            segue_id=parent.id,
            oggetto=parent.oggetto,
            testo=parent.testo,
            bottone_testo=parent.bottone_testo,
            bottone_meta=parent.bottone_meta,
            azione=parent.azione,
            stato="bozza",
            contenuto_at=now,
        )
        self.session.add(campaign)
        self.session.commit()
        return CampaignRead.model_validate(campaign)
```

In `routers/campaigns.py`, after `cancel`:

```python
@router.post(
    "/{campaign_id}/follow-up", response_model=CampaignRead, status_code=status.HTTP_201_CREATED
)
def follow_up(
    admin: AdminDep, session: SessionDep, settings: SettingsDep, campaign_id: UUID
) -> CampaignRead:
    """«Riscrivi a chi non ha fatto niente»: the draft the admin then edits and sends."""
    return CampaignService(session, settings).follow_up(campaign_id, admin.id)
```

In `projects/hub/AGENTS.md`'s campaigns runbook, add:

```markdown
**«Riscrivi a chi non ha fatto niente»** (`POST /api/hub/campaigns/{id}/follow-up`) makes a
`bozza` with `fonte = lista` and `segue_id`. It keeps the earlier campaign's action and a
copy of its mail, and only the mail can change (`LIST_IS_FIXED`). Its list is the earlier
campaign's sent rows with no action, each checked live with `done_at`. The gap rule
applies, so a follow-up drafted within `REBASE_CAMPAIGN_GAP_DAYS` of the send lists
everyone as excluded, with the date.
```

- [ ] **Step 4: Run the tests to see them pass**

Run the core suite: `uv run pytest projects/hub/packages/core/tests/test_campaign_follow_up.py projects/hub/packages/core/tests/test_campaign_service.py projects/hub/packages/core/tests/test_campaign_audience.py -p no:xdist -q`

Then, as its own process, the API suite: `uv run pytest projects/hub/apps/api/tests/test_campaigns_api.py -p no:xdist -q`

Expected: PASS. Then run ruff, ruff format --check and mypy.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/packages/core/src/rebase_core/campaigns/audience.py \
  projects/hub/packages/core/src/rebase_core/campaigns/service.py \
  projects/hub/apps/api/src/rebase_api/routers/campaigns.py projects/hub/AGENTS.md \
  projects/hub/packages/core/tests/test_campaign_follow_up.py \
  projects/hub/apps/api/tests/test_campaigns_api.py
git commit -m "feat(hub): «Riscrivi a chi non ha fatto niente» drafts a campaign to who did nothing" -m "<body>" -m "REB-535."
```

---

### Task 4: Two read-only MCP tools, `list_campagne` and `get_campagna`

**Card:** REB-536.

**Files:**
- Modify: `projects/hub/apps/mcp/src/rebase_mcp/server.py`
- Modify: `projects/hub/AGENTS.md` (the MCP section, one bullet list)
- Test: `projects/hub/apps/mcp/tests/test_campaign_tools.py` (create)

**Interfaces:**
- Consumes: `CampaignService.list_all()` and `.detail()`, with the fields of Task 2.
- Produces: the MCP tools `list_campagne()` and `get_campagna(campagna_id: str)`, answering the `CampaignList` and `CampaignDetail` JSON.

- [ ] **Step 1: Write the failing test**

Create `projects/hub/apps/mcp/tests/test_campaign_tools.py`:

```python
"""The campaigns, read over the hub's MCP server (P-REB-41 phase 2): the list with its
numbers, one campaign with each person's outcome, and nothing that sends."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mcp import Client
from sqlalchemy import delete
from sqlalchemy.orm import Session, sessionmaker

from rebase_core.admin_tokens import AdminRead
from rebase_core.models import Campaign, CampaignRecipient, User
from rebase_mcp.server import build_server

IVAN = AdminRead(
    id=UUID("01a00000-0000-7000-8000-000000000001"),
    email="ivan@rebase.it",
    nome="Ivan",
    attivo=True,
    created_at=datetime(2026, 9, 10, tzinfo=UTC),
)
SENT = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)


def _payload(result: Any) -> dict[str, Any]:
    return result.structured_content or json.loads(result.content[0].text)


def _seed(factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    session = factory()
    try:
        owner = User(email="campagne-mcp@rebase.it", nome="Ivan", cognome="S", role="admin")
        session.add(owner)
        session.flush()
        campaign = Campaign(
            created_by=owner.id,
            nome="Manca il CV",
            slug="c-mcp-manca-il-cv",
            fonte="stato",
            stato_percorso="manca_cv",
            oggetto="o",
            testo="t",
            bottone_testo="b",
            bottone_meta="area",
            azione="cv",
            stato="inviata",
            contenuto_at=SENT,
            inviata_at=SENT,
        )
        session.add(campaign)
        session.flush()
        later = SENT + timedelta(hours=2)
        for email, done in (("ada@studio.it", later), ("bob@studio.it", None)):
            session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    email=email,
                    tipo="freelancer",
                    codice="1",
                    prima={},
                    disiscrizione_token=f"t-{email}",
                    stato="inviata",
                    inviata_at=SENT,
                    primo_clic_at=later,
                    entrato_at=later,
                    azione_at=done,
                )
            )
        session.commit()
        return campaign.id, owner.id
    finally:
        session.close()


def _drop(factory: sessionmaker[Session], campaign_id: UUID, owner_id: UUID) -> None:
    session = factory()
    try:
        session.execute(delete(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign_id))
        session.execute(delete(Campaign).where(Campaign.id == campaign_id))
        session.execute(delete(User).where(User.id == owner_id))
        session.commit()
    finally:
        session.close()


async def test_an_agent_reads_the_campaigns_and_each_persons_outcome(
    factory: sessionmaker[Session],
) -> None:
    campaign_id, owner_id = _seed(factory)
    try:
        async with Client(build_server(factory, lambda: IVAN)) as client:
            listed = _payload(await client.call_tool("list_campagne", {}))
            one = _payload(await client.call_tool("get_campagna", {"campagna_id": str(campaign_id)}))
            names = {tool.name for tool in (await client.list_tools()).tools}
    finally:
        _drop(factory, campaign_id, owner_id)
    item = next(i for i in listed["items"] if i["id"] == str(campaign_id))
    counts = item["conteggi"]
    assert (counts["inviate"], counts["cliccate"], counts["entrate"], counts["azioni"]) == (2, 2, 2, 1)
    people = {p["email"]: p for p in one["destinatari"]}
    assert people["ada@studio.it"]["azione_at"] is not None
    assert people["bob@studio.it"]["azione_at"] is None
    assert one["campagna"]["nome"] == "Manca il CV"
    # Read-only: nothing here sends, schedules or cancels a campaign.
    assert not [n for n in names if "campagn" in n and n not in ("list_campagne", "get_campagna")]


async def test_an_unknown_campaign_is_a_sentence(factory: sessionmaker[Session]) -> None:
    async with Client(build_server(factory, lambda: IVAN)) as client:
        result = await client.call_tool(
            "get_campagna", {"campagna_id": "01a00000-0000-7000-8000-00000000dead"}
        )
    assert result.is_error
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest projects/hub/apps/mcp/tests/test_campaign_tools.py -p no:xdist -q`
Expected: FAIL, `Unknown tool: list_campagne`.

- [ ] **Step 3: Implement**

In `server.py`, import `from rebase_core.campaigns.service import CampaignService`. Inside
`build_server`, after `login_stats`, add:

```python
    campaign_settings = settings if settings is not None else Settings(_env_file=None)  # type: ignore[call-arg]

    @mcp.tool()
    def list_campagne() -> dict[str, Any]:
        """Le campagne di mail della sezione «Campagne», dalla più recente. Per ognuna:

        - nome, oggetto e stato (bozza, programmata, in_invio, inviata, annullata);
        - quando parte o è partita (`programmata_per`, `inviata_at`);
        - l'azione che misura: entrato, cv, scheda_completa, profilo_creato o
          richiesta_aggiornata;
        - in `conteggi` quante sono state inviate, saltate, fallite, consegnate,
          rimbalzate e cliccate, quante persone sono entrate nella loro area dopo la mail
          (`entrate`) e quante hanno fatto l'azione (`azioni`).

        Solo lettura: una campagna si prepara e si invia dall'area admin."""
        return _run(lambda s: CampaignService(s, campaign_settings).list_all())

    @mcp.tool()
    def get_campagna(campagna_id: str) -> dict[str, Any]:
        """Una campagna, per id, con i suoi numeri e l'esito di ogni persona in
        `destinatari`:

        - lo stato (in_coda, inviata, saltata con il motivo, fallita);
        - quando la mail è stata inviata, consegnata o è rimbalzata, e il primo clic;
        - quando la persona è entrata nella sua area (`entrato_dalla_mail` vero se dal
          link di questa mail);
        - quando ha fatto l'azione (`azione_dalla_mail` per un profilo creato dal link).

        `campagna.segue_id` è la campagna da cui viene una «Riscrivi a chi non ha fatto
        niente». Solo lettura."""
        return _run(lambda s: CampaignService(s, campaign_settings).detail(UUID(campagna_id)))
```

Extend `INSTRUCTIONS` with one sentence before «Sono dati di altre persone»: «Si leggono
anche le campagne di mail della sezione «Campagne» e, per ogni persona, cosa ha fatto
dopo la mail.»

In `projects/hub/AGENTS.md`, under «The MCP server is an admin's, by token», after the
match tools list, add:

```markdown
The campaigns are read-only over MCP (P-REB-41 phase 2), so an agent reports on a
campaign without a terminal:

- `list_campagne`: every campaign, newest first, with its `conteggi`, clicks, entries and
  actions included.
- `get_campagna`: one campaign, with each person's outcome, and `entrato_dalla_mail` /
  `azione_dalla_mail` when the mail's own link was the door.
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest projects/hub/apps/mcp/tests -p no:xdist -q`
Expected: PASS, every existing MCP test included. Then run ruff, ruff format --check and mypy.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/mcp/src/rebase_mcp/server.py projects/hub/AGENTS.md \
  projects/hub/apps/mcp/tests/test_campaign_tools.py
git commit -m "feat(hub): an agent reads the campaigns and their outcome over MCP" -m "<body>" -m "REB-536."
```

---

### Task 5: The campaign page and the list read the outcome

**Card:** REB-537.

**Files:**
- Modify: `projects/hub/apps/web/src/lib/api.ts` (types, `admin.followUpCampaign`)
- Modify: `projects/hub/apps/web/src/lib/campaigns.ts` (`AZIONE_FATTA_LABELS`, `share`, `outcomeLine`)
- Modify: `projects/hub/apps/web/src/pages/admin/Campagna.tsx`
- Modify: `projects/hub/apps/web/src/pages/admin/Campagne.tsx`
- Test: `projects/hub/apps/web/src/lib/campaigns.test.ts` (append)
- Test: `projects/hub/apps/web/src/pages/admin/Campagna.test.tsx`
- Test: `projects/hub/apps/web/src/pages/admin/Campagne.test.tsx`

**Interfaces:**
- Consumes: the API fields of Task 2, and `POST …/follow-up` of Task 3.
- Produces:
  - `Campaign.segue_id: string | null`;
  - `CampaignCounts.cliccate | entrate | azioni: number`;
  - the six new `CampaignRecipient` fields;
  - `admin.followUpCampaign(id: string): Promise<Campaign>`;
  - `share(part: number, whole: number): string | undefined`;
  - `outcomeLine(conteggi: CampaignCounts, azione: CampaignAzione): string`;
  - `AZIONE_FATTA_LABELS: Record<CampaignAzione, string>`.

- [ ] **Step 1: Write the failing tests**

Append to `lib/campaigns.test.ts` (add `AZIONE_FATTA_LABELS`, `outcomeLine`, `share` to its import):

```ts
describe('the outcome in words', () => {
  it('gives a share of the mails sent, and nothing while none has left', () => {
    expect(share(3, 8)).toBe('38%')
    expect(share(0, 8)).toBe('0%')
    expect(share(2, 0)).toBeUndefined()
  })

  it('names every action once it is done', () => {
    expect(Object.keys(AZIONE_FATTA_LABELS).sort()).toEqual(Object.keys(AZIONE_LABELS).sort())
  })

  it('says what left and what it led to, and what went wrong only when something did', () => {
    const counts = { destinatari: 10, in_coda: 0, inviate: 8, saltate: 1, fallite: 0, consegnate: 8, rimbalzate: 0, cliccate: 4, entrate: 3, azioni: 2 }
    expect(outcomeLine(counts, 'cv')).toBe('8 inviate · 8 consegnate · 4 clic · 3 entrati · 2 CV caricati · 1 saltate')
    expect(outcomeLine({ ...counts, saltate: 0 }, 'entrato')).toBe('8 inviate · 8 consegnate · 4 clic · 3 entrati')
  })
})
```

In `Campagna.test.tsx`, the `DETAIL` fixture's rows gain the new fields (`primo_clic_at:
null, reclamo_at: null, entrato_at: null, azione_at: null, entrato_dalla_mail: false,
azione_dalla_mail: false`). `campagna` gains `segue_id: null`, and `conteggi` gains
`cliccate: 0, entrate: 0, azioni: 0`. Then add this `describe`:

```tsx
const SENT = {
  campagna: { ...DETAIL.campagna, stato: 'inviata', inviata_at: '2026-09-26T07:31:00Z', azione: 'cv' },
  conteggi: { destinatari: 3, in_coda: 0, inviate: 3, saltate: 0, fallite: 0, consegnate: 3, rimbalzate: 0, cliccate: 2, entrate: 2, azioni: 1 },
  destinatari: [
    { id: 'r1', email: 'ada@studio.it', nome: 'Ada', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: '2026-09-26T08:00:00Z', reclamo_at: null, entrato_at: '2026-09-26T08:01:00Z', azione_at: '2026-09-26T08:10:00Z', entrato_dalla_mail: true, azione_dalla_mail: false },
    { id: 'r2', email: 'bob@studio.it', nome: 'Bob', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: '2026-09-26T09:00:00Z', reclamo_at: null, entrato_at: '2026-09-26T09:01:00Z', azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
    { id: 'r3', email: 'cleo@studio.it', nome: 'Cleo', tipo: 'freelancer', stato: 'inviata', motivo: null, inviata_at: '2026-09-26T07:31:00Z', consegnata_at: '2026-09-26T07:32:00Z', rimbalzata_at: null, primo_clic_at: null, reclamo_at: null, entrato_at: null, azione_at: null, entrato_dalla_mail: false, azione_dalla_mail: false },
  ],
}

describe('the outcome of a sent campaign (phase 2)', () => {
  it('shows each figure with its share of the mails sent', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const clicks = (await screen.findByText('Cliccate')).closest('div')!
    expect(clicks).toHaveTextContent('2')
    expect(clicks).toHaveTextContent('67%')
    const done = screen.getByText('Hanno caricato il CV').closest('div')!
    expect(done).toHaveTextContent('1')
    expect(done).toHaveTextContent('33%')
  })

  it('says «dalla mail» where the mail was the door', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    const ada = (await screen.findByText('ada@studio.it')).closest('tr')!
    expect(within(ada).getAllByText('dalla mail')).toHaveLength(1)
  })

  it('filters who did the action and who did nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(SENT))
    mount()
    await screen.findByText('ada@studio.it')
    await userEvent.click(screen.getByRole('button', { name: 'Non ha fatto niente (2)' }))
    expect(screen.queryByText('ada@studio.it')).not.toBeInTheDocument()
    expect(screen.getByText('bob@studio.it')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Ha fatto l’azione (1)' }))
    expect(screen.getByText('ada@studio.it')).toBeInTheDocument()
    expect(screen.queryByText('cleo@studio.it')).not.toBeInTheDocument()
  })

  it('«Riscrivi a chi non ha fatto niente» opens the new draft', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) =>
      String(input).endsWith('/follow-up') && init?.method === 'POST'
        ? json({ ...SENT.campagna, id: 'c2', fonte: 'lista', segue_id: 'c1', stato: 'bozza' }, 201)
        : json(SENT),
    )
    mount()
    await userEvent.click(await screen.findByRole('button', { name: 'Riscrivi a chi non ha fatto niente (2)' }))
    expect(await screen.findByText('modifica')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/api/hub/campaigns/c1/follow-up'))).toBe(true)
  })

  it('offers no «Riscrivi» before the send or once everyone acted', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(DETAIL))
    mount()
    await screen.findByText('ada@studio.it')
    expect(screen.queryByRole('button', { name: /Riscrivi/ })).not.toBeInTheDocument()
  })
})
```

`json` in this file takes one argument. Give it the same `status = 200` parameter
`CreaCampagna.test.tsx`'s has.

In `Campagne.test.tsx`, `ITEM.conteggi` gains `cliccate: 4, entrate: 3, azioni: 2` and the
test asserts the line:

```tsx
    expect(row).toHaveTextContent('8 inviate · 8 consegnate · 4 clic · 3 entrati · 2 CV caricati · 1 saltate · 2 fallite')
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `pnpm --filter hub exec vitest run src/lib/campaigns.test.ts src/pages/admin/Campagna.test.tsx src/pages/admin/Campagne.test.tsx`
Expected: FAIL on the missing exports and texts.

- [ ] **Step 3: Implement**

`lib/api.ts`:

- `Campaign`: add `segue_id: string | null` after `filtri`.
- `CampaignCounts`: add `cliccate: number`, `entrate: number` and `azioni: number`.
- `CampaignRecipient`: after `rimbalzata_at`, add `primo_clic_at: string | null`,
  `reclamo_at: string | null`, `entrato_at: string | null`, `azione_at: string | null`,
  `entrato_dalla_mail: boolean` and `azione_dalla_mail: boolean`.
- In `admin`, after `cancelCampaign`:
  `followUpCampaign: (id: string) => request<Campaign>(`/api/hub/campaigns/${id}/follow-up`, { method: 'POST' }),`

`lib/campaigns.ts` (import `CampaignCounts` with the other types):

```ts
/** The campaign page's figure for the action, once done. */
export const AZIONE_FATTA_LABELS: Record<CampaignAzione, string> = {
  entrato: 'Sono entrati',
  cv: 'Hanno caricato il CV',
  scheda_completa: 'Hanno completato la scheda',
  profilo_creato: 'Hanno creato il profilo',
  richiesta_aggiornata: 'Hanno aggiornato la richiesta',
  pigro_cliente: 'Primo cliente in Pigro',
}

const AZIONE_BREVE: Record<CampaignAzione, string> = {
  entrato: 'entrati',
  cv: 'CV caricati',
  scheda_completa: 'schede completate',
  profilo_creato: 'profili creati',
  richiesta_aggiornata: 'richieste aggiornate',
  pigro_cliente: 'primi clienti',
}

/** A part of the mails sent, as the campaign page's figures say it: «38%», and nothing
 *  while none has left. */
export function share(part: number, whole: number): string | undefined {
  if (whole <= 0) return undefined
  return `${Math.round((part / whole) * 100)}%`
}

/** The list page's «Esito»: what left and what it led to, then what went wrong only when
 *  something did. For `entrato`, entering is the action, so it is said once. */
export function outcomeLine(c: CampaignCounts, azione: CampaignAzione): string {
  const parts = [`${c.inviate} inviate`, `${c.consegnate} consegnate`, `${c.cliccate} clic`, `${c.entrate} entrati`]
  if (azione !== 'entrato') parts.push(`${c.azioni} ${AZIONE_BREVE[azione]}`)
  if (c.rimbalzate) parts.push(`${c.rimbalzate} rimbalzate`)
  if (c.saltate) parts.push(`${c.saltate} saltate`)
  if (c.fallite) parts.push(`${c.fallite} fallite`)
  return parts.join(' · ')
}
```

`Campagne.tsx`: the «Esito» cell becomes `{outcomeLine(item.conteggi, item.azione)}`.

`Campagna.tsx`. `ConfirmAction` and `NeverWriteCell` stay as they are. Change the
following:

- **Imports.** `useNavigate` joins `Link` and `useParams`. `AZIONE_FATTA_LABELS` and
  `share` join the `@/lib/campaigns` import.
- **Helpers.** Above `RecipientRow`, add:

```tsx
type Filtro = 'tutti' | 'azione' | 'niente'

const FILTERS: [Filtro, string][] = [
  ['tutti', 'Tutti'],
  ['azione', 'Ha fatto l’azione'],
  ['niente', 'Non ha fatto niente'],
]

/** Whom the mail reached with no action after it: the list «Riscrivi a chi non ha fatto
 *  niente» starts from (spec § 4.3). */
function didNothing(recipient: CampaignRecipient): boolean {
  return recipient.stato === 'inviata' && recipient.azione_at === null
}

function matches(recipient: CampaignRecipient, filtro: Filtro): boolean {
  if (filtro === 'azione') return recipient.azione_at !== null
  if (filtro === 'niente') return didNothing(recipient)
  return true
}

function Moment({ at, fromMail = false }: { at: string | null; fromMail?: boolean }) {
  if (!at) return null
  return (
    <>
      <p>{formatDateTime(at)}</p>
      {fromMail && <p className="text-xs">dalla mail</p>}
    </>
  )
}
```

- **`RecipientRow`.** Its cells become:
  - Persona;
  - Stato, with the sending time and the reason as small lines;
  - Consegna, with «Rimbalzata» and its time, or the delivery time;
  - Clic;
  - Entrata, via `Moment` with `fromMail={recipient.entrato_dalla_mail}`;
  - Azione, via `Moment` with `fromMail={recipient.azione_dalla_mail}`;
  - the `NeverWriteCell`.

```tsx
function RecipientRow({ recipient }: { recipient: CampaignRecipient }) {
  return (
    <TableRow>
      <TableCell>
        <p className="font-medium">{recipient.nome ?? '—'}</p>
        <p className="text-xs text-muted-foreground">{recipient.email}</p>
      </TableCell>
      <TableCell>
        <p>{RECIPIENT_STATE_LABELS[recipient.stato]}</p>
        {recipient.inviata_at && <p className="text-xs text-muted-foreground">{formatDateTime(recipient.inviata_at)}</p>}
        {recipient.motivo && <p className="text-xs text-muted-foreground">{recipient.motivo}</p>}
      </TableCell>
      <TableCell className="text-muted-foreground">
        {recipient.rimbalzata_at ? (
          <>
            <p className="text-destructive">Rimbalzata</p>
            <p className="text-xs">{formatDateTime(recipient.rimbalzata_at)}</p>
          </>
        ) : (
          <Moment at={recipient.consegnata_at} />
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.primo_clic_at} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.entrato_at} fromMail={recipient.entrato_dalla_mail} />
      </TableCell>
      <TableCell className="text-muted-foreground">
        <Moment at={recipient.azione_at} fromMail={recipient.azione_dalla_mail} />
      </TableCell>
      <TableCell>
        <NeverWriteCell email={recipient.email} />
      </TableCell>
    </TableRow>
  )
}
```

- **`AdminCampagna` state and mutation.** At the top, with the other hooks and before the
  early returns, add:

```tsx
  const navigate = useNavigate()
  const [filtro, setFiltro] = useState<Filtro>('tutti')
  const followUp = useMutation({
    mutationFn: () => admin.followUpCampaign(id),
    onSuccess: (draft) => void navigate({ to: '/admin/campaigns/$id/edit', params: { id: draft.id } }),
  })
```

- **`AdminCampagna` counts and failure.** After the data is read, compute
  `const waiting = destinatari.filter(didNothing).length` and a `followUpFailure` in the
  same shape as `cancelFailure`: «Non riesco a preparare la bozza.» when the error is not
  an `ApiError`. Show it in the same alert paragraph as the other two failures.
- **`AdminCampagna` header.** Before the «Modifica» link, add:

```tsx
          {campagna.stato === 'inviata' && waiting > 0 && (
            <Button type="button" size="sm" onClick={() => followUp.mutate()} disabled={followUp.isPending}>
              {followUp.isPending ? 'Preparo la bozza…' : `Riscrivi a chi non ha fatto niente (${waiting})`}
            </Button>
          )}
```

- **`AdminCampagna` follow-up line.** Under the moment line, a campaign that follows
  another says so:

```tsx
      {campagna.segue_id && (
        <p className="px-6 pt-2 text-sm text-muted-foreground">
          Riscrive a chi non aveva fatto niente dopo{' '}
          <Link to="/admin/campaigns/$id" params={{ id: campagna.segue_id }} className="underline">
            un'altra campagna
          </Link>
          .
        </p>
      )}
```

- **`AdminCampagna` figures.** The `<dl>` becomes the figures with their shares:

```tsx
      <dl className="grid grid-cols-2 gap-6 border-b px-6 py-6 sm:grid-cols-3 lg:grid-cols-5">
        <Figure label="Inviate" value={conteggi.inviate} note={`su ${conteggi.destinatari}`} />
        <Figure label="Consegnate" value={conteggi.consegnate} note={share(conteggi.consegnate, conteggi.inviate)} />
        <Figure label="Cliccate" value={conteggi.cliccate} note={share(conteggi.cliccate, conteggi.inviate)} />
        <Figure label="Entrate nell’area" value={conteggi.entrate} note={share(conteggi.entrate, conteggi.inviate)} />
        <Figure label={AZIONE_FATTA_LABELS[campagna.azione]} value={conteggi.azioni} note={share(conteggi.azioni, conteggi.inviate)} />
        <Figure label="Rimbalzate" value={conteggi.rimbalzate} note={share(conteggi.rimbalzate, conteggi.inviate)} />
        <Figure label="Saltate" value={conteggi.saltate} />
        <Figure label="Fallite" value={conteggi.fallite} />
        {conteggi.in_coda > 0 && <Figure label="In coda" value={conteggi.in_coda} />}
      </dl>
```

- **`AdminCampagna` filters.** Above the table, and only when there are recipients, the
  filters go in a group. The table maps
  `destinatari.filter((r) => matches(r, filtro))`, and adds «Clic», «Entrata» and
  «Azione» to its header in the order of the cells:

```tsx
          <div className="mb-4 flex flex-wrap gap-2" role="group" aria-label="Chi mostrare">
            {FILTERS.map(([value, label]) => (
              <Button
                key={value}
                type="button"
                size="sm"
                variant={filtro === value ? 'default' : 'outline'}
                aria-pressed={filtro === value}
                onClick={() => setFiltro(value)}
              >
                {label} ({destinatari.filter((r) => matches(r, value)).length})
              </Button>
            ))}
          </div>
```

Header cells: `Persona`, `Stato`, `Consegna`, `Clic`, `Entrata`, `Azione`, and the
sr-only `Azioni`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: PASS. Every existing Campagna and Campagne test is updated, none deleted.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/lib/api.ts projects/hub/apps/web/src/lib/campaigns.ts \
  projects/hub/apps/web/src/lib/campaigns.test.ts \
  projects/hub/apps/web/src/pages/admin/Campagna.tsx projects/hub/apps/web/src/pages/admin/Campagna.test.tsx \
  projects/hub/apps/web/src/pages/admin/Campagne.tsx projects/hub/apps/web/src/pages/admin/Campagne.test.tsx
git commit -m "feat(hub): the campaign page reads clicks, entries and actions, and «Riscrivi» opens a draft" -m "<body>" -m "REB-537."
```

---

### Task 6: The one-page editor takes a `lista` draft

**Card:** REB-538.

**Files:**
- Modify: `projects/hub/apps/web/src/pages/admin/crea-campagna/form.ts`
- Modify: `projects/hub/apps/web/src/pages/admin/crea-campagna/Destinatari.tsx`
- Modify: `projects/hub/apps/web/src/pages/admin/crea-campagna/Messaggio.tsx`
- Modify: `projects/hub/apps/web/src/pages/admin/CreaCampagna.tsx`
- Test: `projects/hub/apps/web/src/pages/admin/CreaCampagna.test.tsx` (append)

**Interfaces:**
- Consumes: `Campaign.segue_id` and `admin.campaign(id)` (the parent's name), both of Task 5.
- Produces:
  - `Fonte = 'stato' | 'filtri' | 'lista'`;
  - `CampaignForm.segueId: string | null`;
  - `payloadOf` answers a `lista` form as a body without `fonte`, `stato_percorso`,
    `filtri` or `azione`.

- [ ] **Step 1: Write the failing test**

Append to `CreaCampagna.test.tsx`:

```tsx
describe('a «Riscrivi» draft (phase 2)', () => {
  const PARENT = { ...TESTED, id: 'c0', nome: 'Manca il CV', stato: 'inviata' }
  const LISTA = {
    ...DRAFT,
    id: 'c9',
    nome: 'Manca il CV · riscrivi',
    fonte: 'lista',
    stato_percorso: null,
    segue_id: 'c0',
    azione: 'cv',
  }

  it('shows whom it follows, keeps the list, and saves only the mail', async () => {
    const calls = api({
      'GET /api/hub/me': () => json(ME),
      'GET /api/hub/campaigns/templates': () => json([TEMPLATE]),
      'GET /api/hub/campaigns/c9/audience': () => json(AUDIENCE),
      'GET /api/hub/campaigns/c9': () => json({ campagna: LISTA, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'GET /api/hub/campaigns/c0': () => json({ campagna: PARENT, conteggi: COUNTS_EMPTY, destinatari: [] }),
      'PATCH /api/hub/campaigns/c9': () => json({ ...LISTA, oggetto: 'Manca solo il CV!' }),
    })
    mountEdit('c9')
    expect(await screen.findByRole('heading', { name: 'Riscrivi a chi non ha fatto niente' })).toBeInTheDocument()
    expect(await screen.findByText(/Chi ha ricevuto «Manca il CV» e non ha ancora fatto l’azione/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filtri' })).not.toBeInTheDocument()
    expect(screen.getByText('Ha caricato il CV')).toBeInTheDocument()
    expect(await screen.findByText('riceverà la mail', { exact: false }, SAVED)).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Oggetto'), '!')
    await vi.waitFor(() => expect(bodies(calls, 'PATCH', /\/campaigns\/c9$/)).toHaveLength(1), SAVED)
    const body = lastSaved(calls)
    expect(body.oggetto).toBe('Manca solo il CV!')
    expect(Object.keys(body).sort()).toEqual(['bottone_meta', 'bottone_testo', 'nome', 'oggetto', 'testo'])
  })
})
```

- [ ] **Step 2: Run the test to see it fail**

Run: `pnpm --filter hub exec vitest run src/pages/admin/CreaCampagna.test.tsx`
Expected: FAIL. The page reads the `lista` draft as a state with no state picked, so no
heading and no list.

- [ ] **Step 3: Implement**

`form.ts`:

- `export type Fonte = 'stato' | 'filtri' | 'lista'`.
- `CampaignForm` gains `segueId: string | null`, and `EMPTY_FORM` gains `segueId: null`.
- `formFromCampaign`: `fonte: c.fonte` (all three kept as they are) and `segueId: c.segue_id`.
- A new exported type, and `payloadOf` answering it for a `lista`:

```ts
/** What a `lista` saves: the mail alone. Whom it reaches and what it measures come from
 *  the campaign it follows, and the API refuses a change to either (`LIST_IS_FIXED`). */
export type ListaPatch = Pick<CampaignDraft, 'nome' | 'oggetto' | 'testo' | 'bottone_testo' | 'bottone_meta'>

export function payloadOf(form: CampaignForm): CampaignDraft | ListaPatch | null {
  if (form.fonte === 'lista') {
    return {
      nome: form.nome.trim() || defaultNome(form.fonte),
      oggetto: form.oggetto,
      testo: form.testo,
      bottone_testo: form.bottoneTesto,
      bottone_meta: form.bottoneMeta,
    }
  }
  // (the stato/filtri body as before)
}
```

- `keyOf` takes `CampaignDraft | ListaPatch | null`.
- `audienceSource`'s parameter gets `fonte?: string`. A `lista` body then keys as
  `[null, null, null]`, which never changes, so its list never goes stale.

`useAutosave.ts` parses the key as `Partial<CampaignDraft>` where it now says
`CampaignDraft`. A `lista` always starts on the edit route with a stored campaign, so it
is only ever patched. `createCampaign` keeps its `CampaignDraft`: keep a cast there, with
a comment saying why.

`Destinatari.tsx`: a new prop `segue: { id: string; nome: string } | null`. When
`form.fonte === 'lista'`, the section renders its heading, then instead of the source
toggle and the source fields:

```tsx
        <p className="text-sm">
          Chi ha ricevuto «{segue?.nome ?? '…'}» e non ha ancora fatto l’azione.{' '}
          {segue && (
            <Link to="/admin/campaigns/$id" params={{ id: segue.id }} className="underline">
              Vedi la campagna
            </Link>
          )}
        </p>
```

followed by the same audience block as the other sources. For a `lista`, the empty-state
sentence is «Carico l’elenco…».

`Messaggio.tsx`: the action sentence shows for a `lista` too:
`form.fonte === 'filtri' ? (select) : (form.fonte === 'lista' || form.statoPercorso !== null) && (sentence)`.

`CreaCampagna.tsx`:

- A query for the parent's name, when the form is a `lista`:

```tsx
  const parent = useQuery({
    queryKey: ['campaign', form.segueId],
    queryFn: () => admin.campaign(form.segueId!),
    enabled: form.fonte === 'lista' && form.segueId !== null,
  })
  const segue = parent.data ? { id: parent.data.campagna.id, nome: parent.data.campagna.nome } : null
```

  It is passed to `Destinatari` as `segue`.
- The `<h1>` reads «Riscrivi a chi non ha fatto niente» for a `lista`, «Modifica campagna»
  on the edit route otherwise, and «Nuova campagna» when new.
- The `stale` comparison needs no change: `audienceSource` accepts the `lista` body.

- [ ] **Step 4: Run the tests to see them pass**

Run: `pnpm --filter hub test && pnpm --filter hub lint && pnpm --filter hub build`
Expected: PASS. Every REB-526 test still passes.

- [ ] **Step 5: Commit**

```bash
git add projects/hub/apps/web/src/pages/admin/crea-campagna/form.ts \
  projects/hub/apps/web/src/pages/admin/crea-campagna/useAutosave.ts \
  projects/hub/apps/web/src/pages/admin/crea-campagna/Destinatari.tsx \
  projects/hub/apps/web/src/pages/admin/crea-campagna/Messaggio.tsx \
  projects/hub/apps/web/src/pages/admin/CreaCampagna.tsx \
  projects/hub/apps/web/src/pages/admin/CreaCampagna.test.tsx
git commit -m "feat(hub): the campaign editor takes a «Riscrivi» draft and saves only its mail" -m "<body>" -m "REB-538."
```

---

### Task 7: The record

**Card:** REB-539.

**Files:**
- Modify: `docs/design/DECISIONS.md` (one row)
- Modify: `projects/hub/docs/superpowers/specs/2026-09-25-admin-campaigns-design.md` (a note under § 10, phase 2)

The row says:

- no migration;
- «dalla mail» is read at detail time from the logins' and cards' `utm_campaign`, with the
  person's code, and never stored;
- the tick stamps every open row of the last 30 days on every pass;
- a `lista` checks each person's action live, and the gap rule applies to it.

**What it costs if wrong:** a pass costs a few queries per open row. If campaigns grow to
thousands of rows a month, stamping moves to every tenth pass.

The spec note: «Phase 2 shipped as the milestone «Read the outcome» (<PR>), plan
`projects/hub/docs/superpowers/plans/2026-09-26-campaigns-phase-2-outcome.md`.»

- [ ] **Step 1:** Write both. No em dashes in English prose.
- [ ] **Step 2:** Commit: `docs(hub): record how phase 2 reads a campaign's outcome` with `REB-539.` as the last line.
