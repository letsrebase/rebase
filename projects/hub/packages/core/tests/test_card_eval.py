"""REB-835: the cards' evaluation set: its profiles and their lifecycle (`bozza` to
`accettata`), the trail that names fields and never values, the ratings, and that every
row holding a card goes with the person."""

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import CARD, text_pdf
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.card_eval import CardEvalService
from rebase_core.db import Base
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import (
    AdminAction,
    CardEvalProfile,
    CardEvalResult,
    CardEvalRun,
    User,
)
from rebase_core.schemas import FreelancerCreate
from rebase_core.team_schemas import Card

CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS.")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM card_eval_runs"))
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _person(session: Session, email: str = "ada@studio.it", cognome: str = "Lovelace") -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome=cognome,
            email=email,
            tariffa_giornaliera=Decimal("450.00"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        CV,
        "cv.pdf",
        "",
    )
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(**changes: Any) -> Card:
    return Card.model_validate({**CARD, **changes})


def _actions(session: Session) -> list[AdminAction]:
    return list(session.scalars(select(AdminAction).order_by(AdminAction.created_at)))


def _run_with_result(session: Session, freelancer_id: UUID) -> CardEvalRun:
    run = CardEvalRun(
        task="card", provider="anthropic", model="claude-opus-5", prompt_version=1, summary={}
    )
    session.add(run)
    session.flush()
    session.add(
        CardEvalResult(
            run_id=run.id,
            freelancer_id=freelancer_id,
            label="lead",
            anni_ok=True,
            seniority_ok=True,
            guard_clean=True,
            latency_ms=1200,
            cost_eur=Decimal("0.05"),
            card=CARD,
        )
    )
    session.commit()
    return run


def test_add_makes_a_bozza_and_signs_it(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)

    read = CardEvalService(clean).add(admin, person, "senza date")

    assert (read.label, read.status, read.expected, read.nome) == (
        "senza date",
        "bozza",
        None,
        "Ada",
    )
    assert [(a.kind, a.entity_type, a.entity_id) for a in _actions(clean)] == [
        ("eval_add", "card_eval", person)
    ]


def test_a_person_is_in_the_set_once_and_a_label_is_bounded(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    with pytest.raises(InvalidState):
        service.add(admin, person, "lead")
    with pytest.raises(ValidationFailed):
        service.ensure(person, "x" * 41)
    with pytest.raises(ValidationFailed):
        service.ensure(person, "  ")
    with pytest.raises(NotFound):
        service.add(admin, uuid4(), "lead")


def test_accept_requires_expected_card(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    with pytest.raises(InvalidState):
        service.accept(admin, person)

    service.set_expected(admin, person, _card())
    accepted = service.accept(admin, person)
    assert accepted.status == "accettata"
    assert accepted.corrected_by_nome == "Ivan Sala"


def test_any_edit_of_an_accepted_card_makes_it_a_bozza_again(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.accept(admin, person)

    edited = service.set_expected(admin, person, _card(anni=10, seniority="lead"))

    assert edited.status == "bozza"
    assert edited.expected is not None and edited.expected.anni == 10


def test_a_draft_never_overwrites_an_accepted_profile(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.save_draft(person, _card())
    service.accept(admin, person)

    with pytest.raises(InvalidState):
        service.save_draft(person, _card(anni=1))


def test_the_trail_names_fields_and_never_values(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.set_expected(admin, person, _card(ruolo="Data engineer", anni=3))
    service.accept(admin, person)

    trail = _actions(clean)
    assert [a.kind for a in trail] == ["eval_add", "eval_expected", "eval_expected", "eval_accept"]
    assert trail[2].payload == {"changed": ["anni", "ruolo"]}
    dump = json.dumps([a.payload for a in trail])
    for secret in ("Data engineer", "Backend developer", "Python", "Torino", "Lovelace"):
        assert secret not in dump


def test_rate_stores_one_to_three_and_nothing_else(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)
    service = CardEvalService(clean)

    read = service.rate(admin, run.id, {person: {"ruolo": 3, "competenze": 2}})
    assert read.results[0].ratings == {"ruolo": 3, "competenze": 2}
    # A later rating adds to what is there.
    read = service.rate(admin, run.id, {person: {"sintesi": 1}})
    assert read.results[0].ratings == {"ruolo": 3, "competenze": 2, "sintesi": 1}


@pytest.mark.parametrize("value", [0, 4, -1, True, 2.5])
def test_rate_rejects_values_outside_1_to_3(clean: Session, value: Any) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)

    with pytest.raises(ValidationFailed):
        CardEvalService(clean).rate(admin, run.id, {person: {"ruolo": 3, "sintesi": value}})
    clean.expire_all()
    assert clean.get(CardEvalResult, (run.id, person)).ratings is None  # type: ignore[union-attr]


def test_rate_rejects_a_field_nobody_rates_and_an_unknown_run_or_person(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    run = _run_with_result(clean, person)
    service = CardEvalService(clean)

    with pytest.raises(ValidationFailed):
        service.rate(admin, run.id, {person: {"anni": 2}})
    with pytest.raises(NotFound):
        service.rate(admin, uuid4(), {person: {"ruolo": 2}})
    with pytest.raises(NotFound):
        service.rate(admin, run.id, {uuid4(): {"ruolo": 2}})


def test_profile_deleted_with_freelancer(clean: Session) -> None:
    """The person's removal, from the database as an erasure does it, takes the expected
    card and every result written for them; the run, which holds totals only, stays."""
    person, admin = _person(clean), _admin(clean)
    other = _person(clean, "grace@studio.it", "Hopper")
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    service.set_expected(admin, person, _card())
    service.add(admin, other, "designer")
    run = _run_with_result(clean, person)

    clean.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": person})
    clean.commit()
    clean.expire_all()

    assert clean.get(CardEvalProfile, person) is None
    assert clean.get(CardEvalProfile, other) is not None
    assert clean.get(CardEvalResult, (run.id, person)) is None
    assert clean.get(CardEvalRun, run.id) is not None


def test_list_leaves_out_a_soft_deleted_person(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")
    _run_with_result(clean, person)
    FreelancerService(clean).soft_delete(person, admin)

    read = service.list()

    assert read.profiles == []
    assert [run.results for run in read.runs] == [[]]


def test_remove_deletes_the_profile_and_signs_it(clean: Session) -> None:
    person, admin = _person(clean), _admin(clean)
    service = CardEvalService(clean)
    service.add(admin, person, "lead")

    service.remove(admin, person)

    assert service.list().profiles == []
    assert _actions(clean)[-1].kind == "eval_remove"
    with pytest.raises(NotFound):
        service.remove(admin, person)


def test_eval_table_not_in_warehouse_tables() -> None:
    from test_warehouse_contract import WAREHOUSE_TABLES  # noqa: PLC0415

    ours = {name for name in Base.metadata.tables if name.startswith("card_eval_")}
    assert ours == {"card_eval_profiles", "card_eval_runs", "card_eval_results"}
    assert not ours & WAREHOUSE_TABLES
