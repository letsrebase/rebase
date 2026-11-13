"""Which cards are no longer what the inputs would write, and who regenerates them (REB-839).

The hourly loop writes a card for a new CV and for nothing else. A newer prompt version, a
change of the hub's facts, of the position or of a source only **marks** the card: it is
listed with an estimated cost and written again when an admin starts it. No model is
called by anything else, which these tests hold with a `RecordingCall` that has no
answers left (calling it raises `IndexError`).
"""

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID, uuid4

import pytest
from admins import an_admin
from fakes_cards import CARD, Scripted, card_response, text_pdf
from freelancer_fixtures import (
    LINKS,
    apply_numbered,
    backdate_prompt,
    card_writer,
    enable_sources,
    written_cards,
)
from sources_fixtures import FakeFetcher, FakeGithub, snapshot_of
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session
from test_hub_facts import _match

from rebase_core import card_staleness
from rebase_core.card_prompt import PROMPT_VERSION
from rebase_core.card_provenance import stale_reason
from rebase_core.card_staleness import ESTIMATE_CALLS
from rebase_core.card_writer import CardWriter
from rebase_core.db import session_factory
from rebase_core.errors import LlmCapReached
from rebase_core.hub_facts import hub_facts, hub_facts_many
from rebase_core.llm import LlmResponse, RecordingCall
from rebase_core.models import AdminAction, AiUsage, Freelancer, FreelancerCard
from rebase_core.sources import SourceService
from rebase_core.team_schemas import CardsRefreshed

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
NO_ANSWERS = RecordingCall([])


_apply = apply_numbered


def _write(session: Session, person: UUID, service: SourceService | None = None) -> None:
    llm = RecordingCall([card_response()])
    card_writer(session, llm, service, now=NOW).write(person, force=True)


_cards = partial(written_cards, now=NOW)


def _reasons(session: Session) -> dict[UUID, str]:
    return {c.freelancer_id: c.reason for c in CardWriter(session, None).stale_cards()}


_backdate = backdate_prompt


def _service(session: Session, github: FakeGithub | None = None) -> SourceService:
    enable_sources(session, "github", "links")
    return SourceService(session, FakeFetcher(), github or FakeGithub(), lambda: NOW)


def _answers(count: int) -> RecordingCall:
    return RecordingCall([card_response() for _ in range(count)])


def test_a_fresh_card_is_not_stale(clean_hub: Session) -> None:
    person = _cards(clean_hub, 1)[0]

    assert CardWriter(clean_hub, None).stale_of(person) is None
    assert CardWriter(clean_hub, None).stale_cards() == []


def test_prompt_version_bump_alone_not_regenerated_by_refresh_stale(clean_hub: Session) -> None:
    """Every card written by version 1, and the prompt is now version 2: the hourly loop
    does not call the model for one of them, and the list shows all of them."""
    people = _cards(clean_hub)
    _backdate(clean_hub, PROMPT_VERSION - 1)

    result = CardWriter(clean_hub, NO_ANSWERS).refresh_stale(limit=50)

    assert result == CardsRefreshed(written=0, failed=0)
    assert NO_ANSWERS.requests == []
    assert set(_reasons(clean_hub).values()) == {"prompt"}
    assert set(_reasons(clean_hub)) == set(people)


def test_card_with_null_prompt_version_counts_as_version_1(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    _backdate(clean_hub, None)

    assert CardWriter(clean_hub, None).stale_of(person) == "prompt"
    stored = clean_hub.get(FreelancerCard, person)
    assert stored is not None
    assert stale_reason(stored, stored.cv_sha256, {}) == "prompt"


def test_a_cv_change_is_the_hourly_loops_not_the_lists(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    row = clean_hub.get(Freelancer, person)
    assert row is not None
    row.cv_bytes = text_pdf("Una persona che ha cambiato CV: Rust, sistemi embedded.")
    clean_hub.commit()

    assert CardWriter(clean_hub, None).stale_of(person) == "cv"
    assert CardWriter(clean_hub, None).stale_cards() == []
    llm = _answers(1)
    assert CardWriter(clean_hub, llm, now=lambda: NOW).refresh_stale(limit=5).written == 1


def test_source_hash_change_marks_the_card_and_waits_for_an_admin(clean_hub: Session) -> None:
    person = _apply(clean_hub, 1, LINKS)
    github = FakeGithub()
    service = _service(clean_hub, github)
    _write(clean_hub, person, service)
    assert CardWriter(clean_hub, None, sources=service).stale_of(person) is None

    github.snapshot = snapshot_of("adadev", description="a ledger for the whole of Europe")
    service.refetch(person)

    assert CardWriter(clean_hub, None, sources=service).stale_of(person) == "source"
    assert _reasons(clean_hub) == {person: "source"}
    # The hourly loop reads sources and calls no model for it.
    assert CardWriter(clean_hub, NO_ANSWERS, sources=service).refresh_stale() == CardsRefreshed(
        0, 0
    )
    assert NO_ANSWERS.requests == []
    # An admin's start writes it again from the new source.
    admin = an_admin(clean_hub)
    llm = _answers(1)
    done = CardWriter(clean_hub, llm, now=lambda: NOW, sources=service).regenerate_stale(
        admin, 5, source="web"
    )
    assert done == CardsRefreshed(written=1, failed=0)
    assert "a ledger for the whole of Europe" in llm.requests[0].messages[0].text
    assert _reasons(clean_hub) == {}


def test_volatile_source_change_does_not(clean_hub: Session) -> None:
    person = _apply(clean_hub, 1, LINKS)
    github = FakeGithub()
    service = _service(clean_hub, github)
    _write(clean_hub, person, service)

    github.snapshot = snapshot_of(
        "adadev", stars=900
    )  # stars, language bytes: what moves by itself
    service.refetch(person)

    assert CardWriter(clean_hub, None, sources=service).stale_of(person) is None


def test_a_removed_link_marks_the_card(clean_hub: Session) -> None:
    person = _apply(clean_hub, 1, LINKS)
    service = _service(clean_hub)
    _write(clean_hub, person, service)
    row = clean_hub.get(Freelancer, person)
    assert row is not None
    row.links = ["https://github.com/adadev"]
    clean_hub.commit()

    service.refetch(person)

    assert CardWriter(clean_hub, None, sources=service).stale_of(person) == "source"


def test_completed_match_with_unchanged_cv_marks_the_card(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    row = clean_hub.get(Freelancer, person)
    assert row is not None

    _match(clean_hub, row, start=date(2025, 1, 1), fine=date(2025, 6, 30))
    clean_hub.commit()

    assert _reasons(clean_hub) == {person: "source"}


def test_months_of_a_running_engagement_do_not_mark_the_card(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    row = clean_hub.get(Freelancer, person)
    assert row is not None
    match = _match(clean_hub, row, status="active", start=date(2026, 1, 1), fine=None)
    clean_hub.commit()
    _write(clean_hub, person)  # written with the engagement in view
    assert _reasons(clean_hub) == {}

    match.starts_on = date(2025, 12, 1)  # the same engagement, one month longer
    clean_hub.commit()

    # `hash_material` leaves out what grows on its own, but not a changed start.
    assert CardWriter(clean_hub, None).stale_of(person) == "source"


def test_wizard_edit_with_unchanged_cv_marks_the_card(clean_hub: Session) -> None:
    [person] = _cards(clean_hub, 1)
    row = clean_hub.get(Freelancer, person)
    assert row is not None

    row.daily_rate = Decimal("900.00")  # the rate and the mode are not read
    row.remote = "hybrid"
    clean_hub.commit()
    assert CardWriter(clean_hub, None).stale_of(person) is None

    row.job_title = "Data engineer"
    clean_hub.commit()
    assert CardWriter(clean_hub, None).stale_of(person) == "source"


def test_stale_cards_lists_oldest_first_with_the_estimate(clean_hub: Session) -> None:
    people = _cards(clean_hub)
    _backdate(clean_hub, 1)
    for n, person in enumerate(people):
        stored = clean_hub.get(FreelancerCard, person)
        assert stored is not None
        stored.generated_at = datetime(2026, 10, 20 + 2 - n, tzinfo=UTC)
    clean_hub.commit()

    writer = CardWriter(clean_hub, None)
    waiting = writer.stale_cards()

    assert [item.freelancer_id for item in waiting] == people[::-1]
    assert waiting[0].first_name == "Persona3 Cognome3"
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

    clean_hub.add_all([usage("0.10"), usage("0.30"), usage(None), usage("9.00", "eval")])
    clean_hub.commit()
    assert writer.estimate_cost(3) == Decimal("0.6000")  # the average of the real, priced ones

    # Only the latest calls count: fifty at 1.00 push the early, cheaper ones out.
    clean_hub.add_all([usage("1.00") for _ in range(ESTIMATE_CALLS)])
    clean_hub.commit()
    assert writer.estimate_cost(2) == Decimal("2.0000")


def test_regenerate_requires_admin_action_and_respects_limit_and_cap(clean_hub: Session) -> None:
    people = _cards(clean_hub)
    _backdate(clean_hub, 1)
    admin = an_admin(clean_hub)
    llm = _answers(2)

    done = CardWriter(clean_hub, llm, now=lambda: NOW).regenerate_stale(admin, 2, source="web")

    assert done == CardsRefreshed(written=2, failed=0)
    assert len(llm.requests) == 2
    [action] = clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert (action.admin_id, action.payload) == (
        admin,
        {"limit": 2, "queued": 2, "source": "web", "batch": False},
    )
    assert len(_reasons(clean_hub)) == 1
    # The written ones are version 2 with their inputs; the third is still waiting.
    fresh = [p for p in people if p not in _reasons(clean_hub)]
    assert all(clean_hub.get(FreelancerCard, p).prompt_version == PROMPT_VERSION for p in fresh)  # type: ignore[union-attr]

    # A start with nothing to write calls nothing, and a second click on the same cards
    # pays once: each is read again before its turn.
    again = CardWriter(clean_hub, _answers(1), now=lambda: NOW).regenerate_stale(
        admin, 5, source="web"
    )
    assert again == CardsRefreshed(written=1, failed=0)
    nothing = CardWriter(clean_hub, NO_ANSWERS, now=lambda: NOW).regenerate_stale(
        admin, 5, source="web"
    )
    assert nothing == CardsRefreshed(written=0, failed=0) and NO_ANSWERS.requests == []


def test_cap_reached_stops_batch_with_reason(clean_hub: Session) -> None:
    people = _cards(clean_hub)
    _backdate(clean_hub, 1)
    admin = an_admin(clean_hub)
    reason = "Tetto giornaliero raggiunto"
    answers: list[LlmResponse | BaseException] = [LlmCapReached(reason)]
    llm = _Scripted(answers)
    first_in_line = CardWriter(clean_hub, None).stale_cards()[0].freelancer_id

    done = CardWriter(clean_hub, llm, now=lambda: NOW).regenerate_stale(admin, 3, source="web")

    assert done == CardsRefreshed(written=0, failed=1, stopped=True)
    assert len(llm.requests) == 1  # the next call would be refused the same
    first = clean_hub.get(FreelancerCard, first_in_line, populate_existing=True)
    assert first is not None
    assert first.error == reason and first.card == CARD  # the previous card stays
    assert set(_reasons(clean_hub)) == set(people)


def test_without_a_model_regenerating_does_nothing_and_records_nothing(clean_hub: Session) -> None:
    _cards(clean_hub, 1)
    _backdate(clean_hub, 1)

    assert CardWriter(clean_hub, None).regenerate_stale(
        an_admin(clean_hub), 5, source="web"
    ) == CardsRefreshed(0, 0)
    assert (
        clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate")).all()
        == []
    )


def test_a_person_turned_down_or_deleted_is_not_listed(clean_hub: Session) -> None:
    kept, turned_down, gone = _cards(clean_hub)
    _backdate(clean_hub, 1)
    clean_hub.execute(
        text("UPDATE freelancers SET status = 'discarded' WHERE id = :id"), {"id": turned_down}
    )
    clean_hub.execute(
        text("UPDATE freelancers SET deleted_at = now() WHERE id = :id"), {"id": gone}
    )
    clean_hub.commit()

    assert set(_reasons(clean_hub)) == {kept}


def test_weekly_refetch_makes_no_model_call(clean_hub: Session) -> None:
    """The pass that reads the sources again is the loop's first step and a command of its
    own; `NO_ANSWERS` raises if anything asks it for a card."""
    person = _apply(clean_hub, 1, LINKS)
    github = FakeGithub()
    service = _service(clean_hub, github)
    _write(clean_hub, person, service)
    github.snapshot = snapshot_of("adadev", description="rewritten")
    # A week and a day later the rows are due.
    later = SourceService(clean_hub, FakeFetcher(), github, lambda: NOW + timedelta(days=8))

    refetched = later.refetch_due(limit=10)

    assert refetched == 1 and NO_ANSWERS.requests == []
    assert CardWriter(clean_hub, NO_ANSWERS, sources=service).stale_of(person) == "source"


def test_a_second_start_while_one_runs_pays_for_nothing(
    clean_hub: Session, hub_engine: Engine
) -> None:
    """Two sessions, as two processes would be: while the first start is inside its model
    call, a second start, and an admin's «Rigenera scheda» on the card being written, find
    it taken and make no call. The first click is then the only one that paid. (An earlier
    version of this test chose the second write's card with an unordered `SELECT`, which is
    the card in flight only when the heap happens to be in the list's order: on another run
    it was a card still waiting, whose write the claim rightly lets through.)"""
    people = _cards(clean_hub)
    _backdate(clean_hub, 1)
    admin = an_admin(clean_hub)
    second_llm = _answers(5)
    seen: dict[str, Any] = {}
    # The card the first start is writing when `meanwhile` runs: the first of the list, in
    # the list's own order. Not "any card": the claim is per person, and «Rigenera scheda»
    # on another card that is still waiting is a different, legitimate write.
    in_flight = CardWriter(clean_hub, None).stale_cards()[0].freelancer_id

    def meanwhile() -> None:
        other = session_factory(hub_engine)()
        try:
            writer = CardWriter(other, second_llm, now=lambda: NOW)
            seen["running"] = writer.regeneration_running()
            seen["start"] = writer.regenerate_stale(admin, 5, source="web")
            writer.write(in_flight, force=True)
        finally:
            other.close()

    llm = _Scripted([card_response(), card_response(), card_response()], during=meanwhile)

    done = CardWriter(clean_hub, llm, now=lambda: NOW).regenerate_stale(admin, 3, source="web")

    assert seen["running"] is True
    assert seen["start"] == CardsRefreshed(written=0, failed=0)
    assert second_llm.requests == []
    assert done == CardsRefreshed(written=3, failed=0)
    assert len(llm.requests) == 3
    assert CardWriter(clean_hub, None).regeneration_running() is False
    assert {p for p in people if p not in _reasons(clean_hub)} == set(people)
    actions = clean_hub.scalars(select(AdminAction).where(AdminAction.kind == "cards_regenerate"))
    assert len(actions.all()) == 1


def test_a_card_that_keeps_failing_does_not_take_every_start(clean_hub: Session) -> None:
    """Its last try failed on its own account, so it goes last: the other cards get the
    start's limited calls first, and it is tried once they are done."""
    people = _cards(clean_hub)
    _backdate(clean_hub, 1)
    failing = people[0]
    clean_hub.execute(
        text(
            "UPDATE freelancer_cards SET generated_at = '2026-01-01', error = 'no', "
            "error_cv_sha256 = cv_sha256 WHERE freelancer_id = :i"
        ),
        {"i": failing},
    )
    clean_hub.commit()

    waiting = CardWriter(clean_hub, None).stale_cards()
    assert waiting[-1].freelancer_id == failing

    llm = _answers(2)
    done = CardWriter(clean_hub, llm, now=lambda: NOW).regenerate_stale(
        an_admin(clean_hub), 2, source="web"
    )

    assert done == CardsRefreshed(written=2, failed=0)
    assert _reasons(clean_hub) == {failing: "prompt"}


def _statements(engine: Engine, work: Callable[[], Any]) -> tuple[Any, int]:
    """What `work` returns and how many statements it sent to the database."""
    sent: list[str] = []

    def count(*args: Any) -> None:
        sent.append(args[2])

    event.listen(engine, "before_cursor_execute", count)
    try:
        return work(), len(sent)
    finally:
        event.remove(engine, "before_cursor_execute", count)


def test_batched_hashes_and_facts_equal_the_per_person_ones(clean_hub: Session) -> None:
    """`stale_cards` reads the inputs of a page of people by `IN (...)`; the hashes it
    compares are the ones `input_hashes` gives person by person, sources and the hub's facts
    included (REB-891)."""
    people = [_apply(clean_hub, n, LINKS) for n in (1, 2, 3)]
    github = FakeGithub()
    service = _service(clean_hub, github)
    for person in people[:2]:  # the third has no source stored: an empty set, not an absence
        service.refetch(person)
    row = clean_hub.get(Freelancer, people[0])
    assert row is not None
    _match(clean_hub, row, start=date(2025, 1, 1), fine=date(2025, 6, 30))
    clean_hub.commit()
    writer = CardWriter(clean_hub, None, sources=service)

    batched = writer.input_hashes_many([*people, uuid4(), people[0]])

    assert set(batched) == set(people)
    assert batched == {person: writer.input_hashes(person) for person in people}
    assert "hub" in batched[people[0]] and "hub" not in batched[people[1]]
    assert "github" in batched[people[1]] and "github" not in batched[people[2]]
    assert hub_facts_many(clean_hub, [*people, uuid4()]) == {
        person: hub_facts(clean_hub, person) for person in people
    }


def test_the_stale_list_costs_the_same_statements_for_any_number_of_cards(
    clean_hub: Session, hub_engine: Engine
) -> None:
    people = _cards(clean_hub, 6)
    _backdate(clean_hub, PROMPT_VERSION)  # the current version: every card is hashed
    writer = CardWriter(clean_hub, None)

    _, few = _statements(hub_engine, lambda: writer.stale_cards(2))
    _, many = _statements(hub_engine, writer.stale_cards)

    assert few == many
    assert few <= 8  # the page, the people, their sources, the hub's three: not 4 per card
    assert len(people) == 6


def test_the_stale_list_is_read_by_pages_in_the_same_order_and_a_limit_stops_it(
    clean_hub: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = _cards(clean_hub, 5)
    _backdate(clean_hub, 1)
    for n, person in enumerate(people):
        clean_hub.execute(
            text("UPDATE freelancer_cards SET generated_at = :at WHERE freelancer_id = :i"),
            {"at": datetime(2026, 10, 1 + n, tzinfo=UTC), "i": person},
        )
    clean_hub.commit()
    whole = CardWriter(clean_hub, None).stale_cards()
    assert [item.freelancer_id for item in whole] == people

    monkeypatch.setattr(card_staleness, "STALE_PAGE", 2)  # three pages: 2, 2, 1

    assert CardWriter(clean_hub, None).stale_cards() == whole
    assert CardWriter(clean_hub, None).stale_cards(3) == whole[:3]
    assert CardWriter(clean_hub, None).stale_cards(0) == []
    assert CardWriter(clean_hub, None).stale_cards(99) == whole


def test_starts_of_a_few_cards_reach_every_stale_card_across_passes(
    clean_hub: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = _cards(clean_hub, 5)
    _backdate(clean_hub, 1)
    monkeypatch.setattr(card_staleness, "STALE_PAGE", 2)
    admin = an_admin(clean_hub)
    written = 0

    for _ in range(3):
        done = CardWriter(clean_hub, _answers(2), now=lambda: NOW).regenerate_stale(
            admin, 2, source="web"
        )
        written += done.written

    assert written == len(people)
    assert _reasons(clean_hub) == {}


def test_a_card_whose_cv_changed_is_stale_by_the_stored_digest(clean_hub: Session) -> None:
    """The CV is compared by `freelancers.cv_sha256`, which the database generates: a new
    file moves it, the card's own digest does not follow, and no bytes are hashed in Python."""
    [person] = _cards(clean_hub, 1)
    before = clean_hub.scalar(select(Freelancer.cv_sha256).where(Freelancer.id == person))
    assert before == clean_hub.get(FreelancerCard, person).cv_sha256  # type: ignore[union-attr]
    clean_hub.execute(
        text("UPDATE freelancers SET cv_bytes = :b WHERE id = :i"),
        {"b": text_pdf("Un altro CV, scritto altrove."), "i": person},
    )
    clean_hub.commit()

    assert clean_hub.scalar(select(Freelancer.cv_sha256).where(Freelancer.id == person)) != before
    assert CardWriter(clean_hub, None).stale_of(person) == "cv"
    assert CardWriter(clean_hub, _answers(1), now=lambda: NOW).refresh_stale(limit=5).written == 1
    assert CardWriter(clean_hub, None).stale_of(person) is None


_Scripted = partial(Scripted, once=True)
