"""Which cards are no longer what the inputs would write, and who regenerates them (REB-839).

The hourly loop writes a card for a new CV and for nothing else. A newer prompt version, a
change of the hub's facts, of the position or of a source only **marks** the card: it is
listed with an estimated cost and written again when an admin starts it. No model is
called by anything else, which these tests hold with a `RecordingCall` that has no
answers left (calling it raises `IndexError`).
"""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, card_response, text_pdf
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from test_hub_facts import _match
from test_sources import FakeFetcher, FakeGithub, snapshot_of

from rebase_core.cards import ESTIMATE_CALLS, PROMPT_VERSION, CardWriter, stale_reason
from rebase_core.db import session_factory
from rebase_core.errors import LlmCapReached
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmResponse, RecordingCall
from rebase_core.models import AdminAction, AiUsage, Freelancer, FreelancerCard, User
from rebase_core.schemas import FreelancerCreate
from rebase_core.sources import SourceService
from rebase_core.team_schemas import CardsRefreshed

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
NO_ANSWERS = RecordingCall([])


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("UPDATE ai_sources SET enabled = false, updated_by = NULL"))
    for table in (
        "admin_actions",
        "ai_usage",
        "comments",
        "matches",
        "companies",
        "freelancers",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _apply(session: Session, n: int = 1, links: list[str] | None = None) -> UUID:
    data = FreelancerCreate(
        nome=f"Persona{n}",
        cognome=f"Cognome{n}",
        email=f"persona{n}@studio.it",
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
        links=links or [],
    )
    cv = text_pdf(f"Persona{n}, backend developer da nove anni: Python, FastAPI.")
    read, _ = FreelancerService(session).apply(data, cv, "cv.pdf", "")
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _write(session: Session, person: UUID, service: SourceService | None = None) -> None:
    llm = RecordingCall([card_response()])
    CardWriter(session, llm, now=lambda: NOW, sources=service).write(person, force=True)


def _cards(session: Session, n: int = 3) -> list[UUID]:
    people = [_apply(session, i) for i in range(1, n + 1)]
    for person in people:
        _write(session, person)
    return people


def _reasons(session: Session) -> dict[UUID, str]:
    return {c.freelancer_id: c.reason for c in CardWriter(session, None).stale_cards()}


def _backdate(session: Session, version: int | None) -> None:
    session.execute(text("UPDATE freelancer_cards SET prompt_version = :v"), {"v": version})
    session.commit()


def _service(session: Session, github: FakeGithub | None = None) -> SourceService:
    for kind in ("github", "links"):
        session.execute(text("UPDATE ai_sources SET enabled = true WHERE kind = :k"), {"k": kind})
    session.commit()
    return SourceService(session, FakeFetcher(), github or FakeGithub(), lambda: NOW)


def _answers(count: int) -> RecordingCall:
    return RecordingCall([card_response() for _ in range(count)])


LINKS = ["https://github.com/adadev", "https://ada.example/portfolio"]


def test_a_fresh_card_is_not_stale(clean: Session) -> None:
    person = _cards(clean, 1)[0]

    assert CardWriter(clean, None).stale_of(person) is None
    assert CardWriter(clean, None).stale_cards() == []


def test_prompt_version_bump_alone_not_regenerated_by_refresh_stale(clean: Session) -> None:
    """Every card written by version 1, and the prompt is now version 2: the hourly loop
    does not call the model for one of them, and the list shows all of them."""
    people = _cards(clean)
    _backdate(clean, PROMPT_VERSION - 1)

    result = CardWriter(clean, NO_ANSWERS).refresh_stale(limit=50)

    assert result == CardsRefreshed(written=0, failed=0)
    assert NO_ANSWERS.requests == []
    assert set(_reasons(clean).values()) == {"prompt"}
    assert set(_reasons(clean)) == set(people)


def test_card_with_null_prompt_version_counts_as_version_1(clean: Session) -> None:
    [person] = _cards(clean, 1)
    _backdate(clean, None)

    assert CardWriter(clean, None).stale_of(person) == "prompt"
    stored = clean.get(FreelancerCard, person)
    assert stored is not None
    assert stale_reason(stored, stored.cv_sha256, {}) == "prompt"


def test_a_cv_change_is_the_hourly_loops_not_the_lists(clean: Session) -> None:
    [person] = _cards(clean, 1)
    row = clean.get(Freelancer, person)
    assert row is not None
    row.cv_bytes = text_pdf("Una persona che ha cambiato CV: Rust, sistemi embedded.")
    clean.commit()

    assert CardWriter(clean, None).stale_of(person) == "cv"
    assert CardWriter(clean, None).stale_cards() == []
    llm = _answers(1)
    assert CardWriter(clean, llm, now=lambda: NOW).refresh_stale(limit=5).written == 1


def test_source_hash_change_marks_the_card_and_waits_for_an_admin(clean: Session) -> None:
    person = _apply(clean, 1, LINKS)
    github = FakeGithub()
    service = _service(clean, github)
    _write(clean, person, service)
    assert CardWriter(clean, None, sources=service).stale_of(person) is None

    github.snapshot = snapshot_of("adadev", description="a ledger for the whole of Europe")
    service.refetch(person)

    assert CardWriter(clean, None, sources=service).stale_of(person) == "source"
    assert _reasons(clean) == {person: "source"}
    # The hourly loop reads sources and calls no model for it.
    assert CardWriter(clean, NO_ANSWERS, sources=service).refresh_stale() == CardsRefreshed(0, 0)
    assert NO_ANSWERS.requests == []
    # An admin's start writes it again from the new source.
    admin = _admin(clean)
    llm = _answers(1)
    done = CardWriter(clean, llm, now=lambda: NOW, sources=service).regenerate_stale(admin, 5)
    assert done == CardsRefreshed(written=1, failed=0)
    assert "a ledger for the whole of Europe" in llm.requests[0].messages[0].text
    assert _reasons(clean) == {}


def test_volatile_source_change_does_not(clean: Session) -> None:
    person = _apply(clean, 1, LINKS)
    github = FakeGithub()
    service = _service(clean, github)
    _write(clean, person, service)

    github.snapshot = snapshot_of(
        "adadev", stars=900
    )  # stars, language bytes: what moves by itself
    service.refetch(person)

    assert CardWriter(clean, None, sources=service).stale_of(person) is None


def test_a_removed_link_marks_the_card(clean: Session) -> None:
    person = _apply(clean, 1, LINKS)
    service = _service(clean)
    _write(clean, person, service)
    row = clean.get(Freelancer, person)
    assert row is not None
    row.links = ["https://github.com/adadev"]
    clean.commit()

    service.refetch(person)

    assert CardWriter(clean, None, sources=service).stale_of(person) == "source"


def test_completed_match_with_unchanged_cv_marks_the_card(clean: Session) -> None:
    [person] = _cards(clean, 1)
    row = clean.get(Freelancer, person)
    assert row is not None

    _match(clean, row, inizio=date(2025, 1, 1), fine=date(2025, 6, 30))
    clean.commit()

    assert _reasons(clean) == {person: "source"}


def test_months_of_a_running_engagement_do_not_mark_the_card(clean: Session) -> None:
    [person] = _cards(clean, 1)
    row = clean.get(Freelancer, person)
    assert row is not None
    match = _match(clean, row, stato="attivo", inizio=date(2026, 1, 1), fine=None)
    clean.commit()
    _write(clean, person)  # written with the engagement in view
    assert _reasons(clean) == {}

    match.lettera_data_inizio = date(2025, 12, 1)  # the same engagement, one month longer
    clean.commit()

    # `hash_material` leaves out what grows on its own, but not a changed start.
    assert CardWriter(clean, None).stale_of(person) == "source"


def test_wizard_edit_with_unchanged_cv_marks_the_card(clean: Session) -> None:
    [person] = _cards(clean, 1)
    row = clean.get(Freelancer, person)
    assert row is not None

    row.tariffa_giornaliera = Decimal("900.00")  # the rate and the mode are not read
    row.remoto = "ibrido"
    clean.commit()
    assert CardWriter(clean, None).stale_of(person) is None

    row.posizione = "Data engineer"
    clean.commit()
    assert CardWriter(clean, None).stale_of(person) == "source"


def test_stale_cards_lists_oldest_first_with_the_estimate(clean: Session) -> None:
    people = _cards(clean)
    _backdate(clean, 1)
    for n, person in enumerate(people):
        stored = clean.get(FreelancerCard, person)
        assert stored is not None
        stored.generated_at = datetime(2026, 10, 20 + 2 - n, tzinfo=UTC)
    clean.commit()

    writer = CardWriter(clean, None)
    waiting = writer.stale_cards()

    assert [item.freelancer_id for item in waiting] == people[::-1]
    assert waiting[0].nome == "Persona3 Cognome3"
    assert writer.estimate_cost(len(waiting)) is None  # nothing recorded: not «free»

    def usage(cost: str | None, purpose: str = "run") -> AiUsage:
        return AiUsage(
            task="card",
            purpose=purpose,
            provider="anthropic",
            model="claude-opus-5",
            priced=cost is not None,
            cost_eur=Decimal(cost) if cost is not None else None,
        )

    clean.add_all([usage("0.10"), usage("0.30"), usage(None), usage("9.00", "eval")])
    clean.commit()
    assert writer.estimate_cost(3) == Decimal("0.6000")  # the average of the real, priced ones

    # Only the latest calls count: fifty at 1.00 push the early, cheaper ones out.
    clean.add_all([usage("1.00") for _ in range(ESTIMATE_CALLS)])
    clean.commit()
    assert writer.estimate_cost(2) == Decimal("2.0000")


def test_regenerate_requires_admin_action_and_respects_limit_and_cap(clean: Session) -> None:
    people = _cards(clean)
    _backdate(clean, 1)
    admin = _admin(clean)
    llm = _answers(2)

    done = CardWriter(clean, llm, now=lambda: NOW).regenerate_stale(admin, 2)

    assert done == CardsRefreshed(written=2, failed=0)
    assert len(llm.requests) == 2
    [action] = clean.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert (action.admin_id, action.payload) == (admin, {"limit": 2, "queued": 2})
    assert len(_reasons(clean)) == 1
    # The written ones are version 2 with their inputs; the third is still waiting.
    fresh = [p for p in people if p not in _reasons(clean)]
    assert all(clean.get(FreelancerCard, p).prompt_version == PROMPT_VERSION for p in fresh)  # type: ignore[union-attr]

    # A start with nothing to write calls nothing, and a second click on the same cards
    # pays once: each is read again before its turn.
    again = CardWriter(clean, _answers(1), now=lambda: NOW).regenerate_stale(admin, 5)
    assert again == CardsRefreshed(written=1, failed=0)
    nothing = CardWriter(clean, NO_ANSWERS, now=lambda: NOW).regenerate_stale(admin, 5)
    assert nothing == CardsRefreshed(written=0, failed=0) and NO_ANSWERS.requests == []


def test_cap_reached_stops_batch_with_reason(clean: Session) -> None:
    people = _cards(clean)
    _backdate(clean, 1)
    admin = _admin(clean)
    reason = "Tetto giornaliero raggiunto"
    answers: list[LlmResponse | BaseException] = [LlmCapReached(reason)]
    llm = _Scripted(answers)
    first_in_line = CardWriter(clean, None).stale_cards()[0].freelancer_id

    done = CardWriter(clean, llm, now=lambda: NOW).regenerate_stale(admin, 3)

    assert done == CardsRefreshed(written=0, failed=1, stopped=True)
    assert len(llm.requests) == 1  # the next call would be refused the same
    first = clean.get(FreelancerCard, first_in_line, populate_existing=True)
    assert first is not None
    assert first.error == reason and first.card == CARD  # the previous card stays
    assert set(_reasons(clean)) == set(people)


def test_without_a_model_regenerating_does_nothing_and_records_nothing(clean: Session) -> None:
    _cards(clean, 1)
    _backdate(clean, 1)

    assert CardWriter(clean, None).regenerate_stale(_admin(clean), 5) == CardsRefreshed(0, 0)
    assert (
        clean.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate")).all() == []
    )


def test_a_person_turned_down_or_deleted_is_not_listed(clean: Session) -> None:
    kept, turned_down, gone = _cards(clean)
    _backdate(clean, 1)
    clean.execute(
        text("UPDATE freelancers SET stato = 'scartato' WHERE id = :id"), {"id": turned_down}
    )
    clean.execute(text("UPDATE freelancers SET deleted_at = now() WHERE id = :id"), {"id": gone})
    clean.commit()

    assert set(_reasons(clean)) == {kept}


def test_weekly_refetch_makes_no_model_call(clean: Session) -> None:
    """The pass that reads the sources again is the loop's first step and a command of its
    own; `NO_ANSWERS` raises if anything asks it for a card."""
    person = _apply(clean, 1, LINKS)
    github = FakeGithub()
    service = _service(clean, github)
    _write(clean, person, service)
    github.snapshot = snapshot_of("adadev", description="rewritten")
    # A week and a day later the rows are due.
    later = SourceService(clean, FakeFetcher(), github, lambda: NOW + timedelta(days=8))

    refetched = later.refetch_due(limit=10)

    assert refetched == 1 and NO_ANSWERS.requests == []
    assert CardWriter(clean, NO_ANSWERS, sources=service).stale_of(person) == "source"


def test_a_second_start_while_one_runs_pays_for_nothing(clean: Session, hub_engine: Engine) -> None:
    """Two sessions, as two processes would be: while the first start is inside its model
    call, a second start, and an admin's «Rigenera scheda» on the card being written, find
    it taken and make no call. The first click is then the only one that paid."""
    people = _cards(clean)
    _backdate(clean, 1)
    admin = _admin(clean)
    second_llm = _answers(5)
    seen: dict[str, Any] = {}

    def meanwhile() -> None:
        other = session_factory(hub_engine)()
        try:
            writer = CardWriter(other, second_llm, now=lambda: NOW)
            seen["running"] = writer.regeneration_running()
            seen["start"] = writer.regenerate_stale(admin, 5)
            first = other.scalars(select(FreelancerCard.freelancer_id)).first()
            assert first is not None
            writer.write(first, force=True)
        finally:
            other.close()

    llm = _Scripted([card_response(), card_response(), card_response()], during=meanwhile)

    done = CardWriter(clean, llm, now=lambda: NOW).regenerate_stale(admin, 3)

    assert seen["running"] is True
    assert seen["start"] == CardsRefreshed(written=0, failed=0)
    assert second_llm.requests == []
    assert done == CardsRefreshed(written=3, failed=0)
    assert len(llm.requests) == 3
    assert CardWriter(clean, None).regeneration_running() is False
    assert {p for p in people if p not in _reasons(clean)} == set(people)
    actions = clean.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert len(actions.all()) == 1


def test_a_card_that_keeps_failing_does_not_take_every_start(clean: Session) -> None:
    """Its last try failed on its own account, so it goes last: the other cards get the
    start's limited calls first, and it is tried once they are done."""
    people = _cards(clean)
    _backdate(clean, 1)
    failing = people[0]
    clean.execute(
        text(
            "UPDATE freelancer_cards SET generated_at = '2026-01-01', error = 'no', "
            "error_cv_sha256 = cv_sha256 WHERE freelancer_id = :i"
        ),
        {"i": failing},
    )
    clean.commit()

    waiting = CardWriter(clean, None).stale_cards()
    assert waiting[-1].freelancer_id == failing

    llm = _answers(2)
    done = CardWriter(clean, llm, now=lambda: NOW).regenerate_stale(_admin(clean), 2)

    assert done == CardsRefreshed(written=2, failed=0)
    assert _reasons(clean) == {failing: "prompt"}


class _Scripted:
    """`RecordingCall` that can raise."""

    def __init__(self, answers: list[Any], during: Callable[[], None] | None = None) -> None:
        self._answers = list(answers)
        self._during = during
        self.requests: list[Any] = []

    def complete(self, request: Any) -> LlmResponse:
        self.requests.append(request)
        if self._during is not None and len(self.requests) == 1:
            self._during()
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer  # type: ignore[no-any-return]
