"""What the CRM tells the hub about an engagement and what it asks it (REB-1106, collaborators
design § 5 and § 8): a notice written in the transaction of each change with its sequence, the
retry sweep that delivers it with a backoff, the bearer that travels only where it may, and the
referrer's name a letter prints, «nessuno» whenever the hub does not say.
"""

import datetime as dt
import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.letter_world import ADMIN, World, needs_binaries
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_signing import SIGNED_AT, Desk

from pigrocrm.core.cli import cron
from pigrocrm.core.collaborators import letter_service
from pigrocrm.core.collaborators.hub_notices import (
    NOTICES_PATH,
    REFERRER_PATH,
    HubLink,
    HubNoticeSweep,
    retry_delay,
    speaks_https,
)
from pigrocrm.core.collaborators.models import Engagement, EngagementNotice
from pigrocrm.core.collaborators.outbox import (
    REQUEST_REF_PREFIX,
    NoticeOutbox,
    hub_company_of,
)
from pigrocrm.core.collaborators.schemas import EngagementUpdate
from pigrocrm.core.config import Settings
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import Document
from pigrocrm.core.engagements.leads import lead_marker

TOKEN = "un-token-lungo-per-gli-avvisi-verso-il-hub"
HUB = "https://hub.example.test"
# A little after the database's own clock, which stamps a notice's first turn when it is written.
NOW = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5)

Call = tuple[str, str, dict[str, str], bytes]


class FakeHub:
    """The hub's two routes behind the CRM's HTTP seam: it records every call and answers what
    the test queued (a status per call, then `default`)."""

    def __init__(self, default: tuple[int, dict[str, Any]] = (200, {"applied": True})) -> None:
        self.calls: list[Call] = []
        self.queue: list[tuple[int, dict[str, Any]] | Exception] = []
        self.default = default
        self.referrers: dict[str, str | None] = {"freelancer": None, "company": None}

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        if url.endswith(REFERRER_PATH):
            return 200, json.dumps(self.referrers).encode()
        step = self.queue.pop(0) if self.queue else self.default
        if isinstance(step, Exception):
            raise step
        status, payload = step
        return status, json.dumps(payload).encode()

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(call[3]) for call in self.calls if call[1].endswith(NOTICES_PATH)]


def settings(**change: object) -> Settings:
    values: dict[str, object] = {"hub_url": HUB, "hub_notices_token": TOKEN}
    values.update(change)
    return Settings(**values)  # type: ignore[arg-type]


def notices(session: Session) -> list[EngagementNotice]:
    session.expire_all()
    return list(
        session.scalars(
            select(EngagementNotice).order_by(
                EngagementNotice.engagement_id, EngagementNotice.sequence
            )
        )
    )


@pytest.fixture
def desk(db_session: Session, tmp_path: Path) -> Desk:
    return Desk(World(db_session, tmp_path))


# ---- the outbox: a row with each change, in its transaction ----------------------------------


def test_an_engagement_creation_writes_notice_one_with_the_read_model(desk: Desk) -> None:
    (notice,) = notices(desk.session)

    assert (notice.engagement_id, notice.sequence, notice.kind, notice.state) == (
        desk.engagement.id,
        1,
        "created",
        "pending",
    )
    payload = notice.payload
    assert payload["hub_freelancer_id"] == str(desk.who.hub_freelancer_id)
    assert payload["engagement_id"] == str(desk.engagement.id)
    assert payload["letter_number"] == desk.engagement.letter_number
    assert (payload["mode"], payload["fee"], payload["role"], payload["state"]) == (
        "day",
        "450.00",
        "Backend developer",
        "draft",
    )
    assert (payload["starts_on"], payload["ends_on"]) == ("2026-11-02", "2026-11-29")
    assert payload["client_name"] == "Acme S.r.l."
    assert payload["signed_at"] is None and payload["letter_id"] is None
    # What rebase agreed with the client is not the freelancer's, and never reaches the hub.
    assert not any("price" in key or "budget" in key for key in payload)
    assert "7331" not in json.dumps(payload)


@needs_binaries
def test_every_change_the_hub_must_hear_of_writes_the_next_notice(desk: Desk) -> None:
    desk.service.send_engagement(desk.engagement.id, ADMIN)  # the framework agreement leaves
    framework = desk.document("framework_agreement")
    desk.sign(framework)  # and the letter is released
    desk.sign(desk.document("engagement_letter"))
    desk.service.record_framework_notice(framework.id, ADMIN)
    desk.world.engagements.close(desk.engagement.id, ADMIN, dt.date(2026, 11, 20))

    rows = notices(desk.session)

    assert [(row.sequence, row.kind) for row in rows] == [
        (1, "created"),
        (2, "sent"),
        (3, "sent"),
        (4, "signed"),
        (5, "notice_served"),
        (6, "closed"),
    ]
    letter = desk.document("engagement_letter")
    awaiting, released, signed, noticed, closed = (row.payload for row in rows[1:])
    assert (awaiting["state"], awaiting["letter_id"]) == ("awaiting_signature", str(letter.id))
    assert released["state"] == "awaiting_signature" and released["signed_at"] is None
    assert signed["state"] == "active"
    assert signed["signed_at"] == SIGNED_AT.isoformat()
    assert signed["letter_number"] == desk.engagement.letter_number
    assert noticed["state"] == "active"
    assert (closed["state"], closed["ends_on"], closed["signed_at"]) == (
        "concluded",
        "2026-11-20",
        SIGNED_AT.isoformat(),
    )


def test_a_cancellation_is_told_and_a_notice_is_never_written_for_nobody(desk: Desk) -> None:
    desk.service.engagement_service().cancel(desk.engagement.id, ADMIN)
    assert [(row.sequence, row.kind) for row in notices(desk.session)] == [
        (1, "created"),
        (2, "cancelled"),
    ]
    assert notices(desk.session)[1].payload["state"] == "cancelled"

    # A freelancer the hub never saw has nobody to tell.
    stranger = desk.world.freelancer(hub_freelancer_id=None, legal_name="Senza hub")
    desk.world.engagement(stranger)
    assert len(notices(desk.session)) == 2


def test_a_draft_that_changes_what_the_hub_shows_tells_it_again_and_other_edits_do_not(
    desk: Desk,
) -> None:
    desk.world.engagements.update(
        desk.engagement.id, EngagementUpdate(days_per_week=4), ADMIN
    )  # not in the read model
    assert len(notices(desk.session)) == 1

    desk.world.engagements.update(
        desk.engagement.id,
        EngagementUpdate(role="Tech lead", fee="500"),
        ADMIN,  # type: ignore[arg-type]
    )

    rows = notices(desk.session)
    assert [(row.sequence, row.kind) for row in rows] == [(1, "created"), (2, "updated")]
    assert (rows[1].payload["role"], rows[1].payload["fee"]) == ("Tech lead", "500.00")


def test_the_notice_leaves_with_the_transaction_that_rolls_back(desk: Desk) -> None:
    row = desk.session.get(Engagement, desk.engagement.id)
    assert row is not None
    NoticeOutbox(desk.session).emit(row, "updated")
    assert len(notices(desk.session)) == 2

    desk.session.rollback()

    assert [notice.sequence for notice in notices(desk.session)] == [1]


def test_the_hub_company_is_the_one_on_the_deals_request_reference(desk: Desk) -> None:
    company = uuid4()
    deal = desk.session.get(Deal, desk.world.deal_id)
    assert deal is not None
    deal.rebase_refs = [lead_marker(company)]
    desk.session.flush()

    other = desk.world.engagement(desk.world.freelancer(legal_name="Grace Hopper"))
    (created,) = [row for row in notices(desk.session) if row.engagement_id == other.id]

    assert created.payload["hub_company_id"] == str(company)
    assert hub_company_of([lead_marker(company)]) == company
    assert hub_company_of(["rebase:match=1", "altro"]) is None
    assert hub_company_of([f"{REQUEST_REF_PREFIX}non-un-uuid"]) is None
    # The outbox reads the reference `lead_marker` writes: the two must not drift apart.
    assert lead_marker(UUID(int=7)).startswith(REQUEST_REF_PREFIX)


# ---- the sweep: delivered on a 2xx, retried with a backoff, refused for good ----------------


def sweep(desk: Desk, hub: FakeHub, *, now: dt.datetime = NOW, **change: object) -> Any:
    return HubNoticeSweep(desk.session, settings(**change), http=hub, now=lambda: now).run()


def test_a_pending_notice_is_posted_with_its_own_bearer_and_marked_delivered(desk: Desk) -> None:
    hub = FakeHub()

    report = sweep(desk, hub)

    assert (report.delivered, report.retrying, report.rejected) == (1, 0, 0)
    (method, url, headers, _body) = hub.calls[0]
    assert (method, url) == ("POST", f"{HUB}{NOTICES_PATH}")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    (body,) = hub.bodies()
    assert (body["sequence"], body["kind"]) == (1, "created")
    assert body["hub_freelancer_id"] == str(desk.who.hub_freelancer_id)
    (row,) = notices(desk.session)
    assert (row.state, row.attempts, row.last_error) == ("delivered", 1, None)
    assert row.delivered_at == NOW

    # Delivered once: the next pass finds nothing to send.
    assert sweep(desk, hub).delivered == 0
    assert len(hub.calls) == 1


def test_a_failed_delivery_is_retried_with_a_growing_backoff_until_the_hub_answers(
    desk: Desk,
) -> None:
    hub = FakeHub()
    hub.queue = [(503, {}), OSError("connection refused"), (429, {})]

    first = sweep(desk, hub)
    (row,) = notices(desk.session)
    assert (first.delivered, first.retrying) == (0, 1)
    assert (row.state, row.attempts, row.last_error) == ("pending", 1, "HTTP 503")
    assert row.next_attempt_at == NOW + retry_delay(1)

    # Not due yet: nothing is sent before the backoff has passed.
    assert sweep(desk, hub, now=NOW + dt.timedelta(minutes=1)).retrying == 0
    assert len(hub.calls) == 1

    second = sweep(desk, hub, now=NOW + retry_delay(1))
    assert second.retrying == 1
    (row,) = notices(desk.session)
    assert (row.attempts, row.last_error) == (2, "OSError")
    assert retry_delay(2) == 2 * retry_delay(1)

    sweep(desk, hub, now=NOW + dt.timedelta(hours=1))
    assert [notice.attempts for notice in notices(desk.session)] == [3]
    final = sweep(desk, hub, now=NOW + dt.timedelta(days=1))
    assert final.delivered == 1
    (row,) = notices(desk.session)
    assert (row.state, row.attempts) == ("delivered", 4)


def test_the_backoff_doubles_and_stops_at_six_hours() -> None:
    assert [retry_delay(n) for n in (1, 2, 3)] == [
        dt.timedelta(minutes=5),
        dt.timedelta(minutes=10),
        dt.timedelta(minutes=20),
    ]
    assert retry_delay(30) == dt.timedelta(hours=6)


def test_a_notice_waits_for_the_one_before_it_of_the_same_engagement(desk: Desk) -> None:
    desk.world.engagements.update(desk.engagement.id, EngagementUpdate(role="Tech lead"), ADMIN)
    hub = FakeHub()
    hub.queue = [(503, {})]

    report = sweep(desk, hub)

    # The first failed, so the second is not sent over it: they go in sequence order.
    assert (report.delivered, report.retrying) == (0, 1)
    assert [body["sequence"] for body in hub.bodies()] == [1]
    later = sweep(desk, hub, now=NOW + dt.timedelta(hours=1))
    assert later.delivered == 2
    assert [body["sequence"] for body in hub.bodies()] == [1, 1, 2]


def test_an_answer_the_hub_will_give_again_is_refused_for_good_and_reported(desk: Desk) -> None:
    hub = FakeHub()
    hub.queue = [(422, {"detail": "x"})]

    report = sweep(desk, hub)

    assert (report.delivered, report.retrying, report.rejected) == (0, 0, 1)
    (row,) = notices(desk.session)
    assert (row.state, row.last_error) == ("rejected", "HTTP 422")
    assert sweep(desk, hub, now=NOW + dt.timedelta(days=2)).rejected == 0
    assert len(hub.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 408, 429, 500, 502])
def test_a_wrong_token_a_missing_route_and_a_hub_in_trouble_are_asked_again(
    desk: Desk, status: int
) -> None:
    hub = FakeHub()
    hub.queue = [(status, {})]

    report = sweep(desk, hub)

    assert (report.retrying, report.rejected) == (1, 0)
    (row,) = notices(desk.session)
    assert row.state == "pending"


def test_without_the_token_the_sweep_is_inert_and_keeps_its_rows(desk: Desk) -> None:
    hub = FakeHub()

    report = sweep(desk, hub, hub_notices_token="")

    assert (report.delivered, report.waiting) == (0, 1)
    assert hub.calls == []
    (row,) = notices(desk.session)
    assert (row.state, row.attempts) == ("pending", 0)


@pytest.mark.parametrize(
    ("url", "travels"),
    [
        ("https://letsrebase.com", True),
        ("http://localhost:8084", True),
        ("http://127.0.0.1:8084", True),
        ("http://letsrebase.com", False),
        ("http://hub.internal:8084", False),
        ("ftp://letsrebase.com", False),
        ("https://", False),
    ],
)
def test_the_bearer_travels_over_https_or_to_this_machine_only(
    desk: Desk, url: str, travels: bool
) -> None:
    assert speaks_https(url) is travels
    hub = FakeHub()
    report = sweep(desk, hub, hub_url=url)
    assert (len(hub.calls) == 1) is travels
    assert report.delivered == (1 if travels else 0)


def test_a_rehearsal_counts_what_is_due_and_calls_nothing(desk: Desk) -> None:
    hub = FakeHub()

    report = HubNoticeSweep(desk.session, settings(), http=hub, now=lambda: NOW).run(dry_run=True)

    assert report.waiting == 1 and hub.calls == []
    assert [row.state for row in notices(desk.session)] == ["pending"]


def test_the_reminder_pass_reports_what_the_sweep_did_and_stops_on_a_refusal(
    desk: Desk, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = FakeHub()
    monkeypatch.setattr(
        cron, "HubNoticeSweep", lambda session, cfg: HubNoticeSweep(session, cfg, http=hub)
    )
    # The test's own session stands in for the space's, so the pass sees the rows the desk wrote.
    monkeypatch.setattr(cron, "session_factory", lambda _engine: lambda: nullcontext(desk.session))
    engine: Any = None
    desk.session.commit()

    assert cron._sweep_hub_notices(settings(), engine, "root", dry_run=False) is False
    assert "root: hub notices sent 1, to try again 0" in capsys.readouterr().out

    desk.world.engagements.update(desk.engagement.id, EngagementUpdate(role="Tech lead"), ADMIN)
    hub.default = (422, {})
    assert cron._sweep_hub_notices(settings(), engine, "root", dry_run=False) is True
    assert "root: hub notices refused 1" in capsys.readouterr().err

    # Nothing to say: no line at all.
    assert cron._sweep_hub_notices(settings(), engine, "root", dry_run=False) is False
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# ---- the referrer's name on a letter ----------------------------------------------------------


@pytest.fixture
def named(desk: Desk, monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """The letter service asking a fake hub, with the notices token set."""
    hub = FakeHub()
    desk.world.letters = letter_service.LetterService(desk.session, desk.world.storage, settings())
    monkeypatch.setattr(
        letter_service,
        "HubLink",
        lambda cfg: HubLink(cfg, hub),  # type: ignore[arg-type]
    )
    deal = desk.session.get(Deal, desk.world.deal_id)
    assert deal is not None
    deal.rebase_refs = [lead_marker(uuid4())]
    desk.session.flush()
    return hub


@needs_binaries
def test_a_verified_referrer_is_printed_on_the_letter_and_the_agreement(
    desk: Desk, named: FakeHub
) -> None:
    named.referrers = {"freelancer": "Mario Rossi", "company": "Lia Neri"}

    letter = desk.world.letters.preview_letter(desk.engagement.id, {}, ADMIN)
    agreement = desk.world.letters.preview_framework_agreement(desk.who.id, None, ADMIN)

    assert "Lia Neri" in letter.markdown
    assert "Mario Rossi" in agreement.markdown
    asked = [json.loads(call[3]) for call in named.calls if call[1].endswith(REFERRER_PATH)]
    assert asked[0]["hub_freelancer_id"] == str(desk.who.hub_freelancer_id)
    assert asked[0]["hub_company_id"] is not None
    # A framework agreement has no engagement: it asks for the freelancer's referrer alone.
    assert asked[-1]["hub_company_id"] is None


@needs_binaries
@pytest.mark.parametrize("answer", ["unverified", "failed", "garbled"])
def test_the_referrer_is_none_when_the_referral_is_unverified_or_the_call_fails(
    desk: Desk, named: FakeHub, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    if answer == "failed":

        def refuse(*_args: object) -> tuple[int, bytes]:
            raise OSError("connection refused")

        monkeypatch.setattr(
            letter_service,
            "HubLink",
            lambda cfg: HubLink(cfg, refuse),  # type: ignore[arg-type]
        )
    elif answer == "garbled":
        monkeypatch.setattr(
            letter_service,
            "HubLink",
            lambda cfg: HubLink(cfg, lambda *_a: (200, b"not json")),  # type: ignore[arg-type]
        )
    # `unverified`: the hub answers null on both sides.

    letter = desk.world.letters.preview_letter(desk.engagement.id, {}, ADMIN)
    agreement = desk.world.letters.preview_framework_agreement(desk.who.id, None, ADMIN)

    assert "**Segnalato da:** nessuno" in letter.markdown
    assert "**Segnalato da:** nessuno" in agreement.markdown


@needs_binaries
def test_without_the_token_no_call_is_made_and_the_letter_names_nobody(
    desk: Desk, named: FakeHub
) -> None:
    desk.world.letters = letter_service.LetterService(
        desk.session, desk.world.storage, settings(hub_notices_token="")
    )
    named.referrers = {"freelancer": "Mario Rossi", "company": "Lia Neri"}

    letter = desk.world.letters.preview_letter(desk.engagement.id, {}, ADMIN)

    assert "**Segnalato da:** nessuno" in letter.markdown
    assert named.calls == []


@needs_binaries
def test_a_letter_keeps_the_referrer_it_was_written_with_and_asks_again_only_for_none(
    desk: Desk, named: FakeHub
) -> None:
    named.referrers = {"freelancer": None, "company": None}
    # The desk's own engagement already has its letter: this one is another freelancer's.
    other = desk.world.engagement(desk.world.freelancer(legal_name="Grace Hopper"))
    stored = desk.world.letters.create_letter(other.id, {"attivita": "Sviluppo"}, ADMIN)
    document = desk.session.get(Document, stored.document.id)
    assert document is not None
    copy = desk.world.letters.render_for_signing(document, ADMIN, framework_signed_on=None)
    assert copy.variables["azienda_segnalata_da"] == "nessuno"

    # The referral was verified before the letter leaves: the nobody stored at creation is asked
    # again, and the name printed is the one the new version stores.
    named.referrers = {"freelancer": "Mario Rossi", "company": "Lia Neri"}
    copy = desk.world.letters.render_for_signing(document, ADMIN, framework_signed_on=None)
    assert copy.variables["azienda_segnalata_da"] == "Lia Neri"
    assert "Lia Neri" in copy.rendered.markdown

    # A name stored when the letter was written stays, whatever the hub says later.
    named.referrers = {"freelancer": None, "company": "Lia Neri"}
    third = desk.world.engagement(desk.world.freelancer(legal_name="Alan Turing"))
    kept = desk.session.get(
        Document, desk.world.letters.create_letter(third.id, {}, ADMIN).document.id
    )
    assert kept is not None
    named.referrers = {"freelancer": None, "company": "Qualcun Altro"}
    again = desk.world.letters.render_for_signing(kept, ADMIN, framework_signed_on=None)
    assert again.variables["azienda_segnalata_da"] == "Lia Neri"


def test_an_administrator_cannot_type_who_referred_the_parties(desk: Desk, named: FakeHub) -> None:
    from pigrocrm.core.errors import ValidationFailed

    for field in ("segnalato_da", "azienda_segnalata_da"):
        with pytest.raises(ValidationFailed, match="hub"):
            desk.world.letters.preview_letter(desk.engagement.id, {field: "Chiunque"}, ADMIN)
