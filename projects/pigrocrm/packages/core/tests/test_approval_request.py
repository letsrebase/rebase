"""Asking a client for approval of named days (REB-749): the text, and the one transaction
that records the days as proposed and files the message as a draft.

Sending is `EmailSendService`'s, tested in `test_email_send.py`; nothing here calls Gmail.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fakes.contacts import points
from fakes.gmail_fixtures import gmail_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.errors import AgentForbidden, Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.models import EmailDraft
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.work_units.approval_request import (
    ApprovalRequestCompose,
    ApprovalRequestDay,
    ApprovalRequestPrepare,
    ApprovalRequestService,
    compose,
    italian_date,
)
from pigrocrm.core.work_units.models import WorkUnit
from pigrocrm.core.work_units.service import WorkUnitService

WRITER = Actor(id=uuid4(), type="user", role="collaborator")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")


def _contract(session: Session) -> Contract:
    customer = Organization(
        legal_name=f"ACME {uuid4()}", contact_points=points(email="amministrazione@acme.example")
    )
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        title="Consulenza architetturale",
        start=date(2026, 1, 1),
        renewal_type="none",
        termination_notice_days=30,
        billing_cadence="monthly",
        expense_policy={"kind": "not_reimbursable"},
        requires_prior_approval=True,
    )
    session.add(contract)
    session.flush()
    return contract


def _day(day: date, quantity: str = "1", description: str = "Workshop") -> ApprovalRequestDay:
    return ApprovalRequestDay(date=day, quantity=Decimal(quantity), description=description)


def _prepare(*days: ApprovalRequestDay) -> ApprovalRequestPrepare:
    return ApprovalRequestPrepare(
        days=list(days),
        to_addresses=["amministrazione@acme.example"],
        subject="Richiesta di approvazione",
        body="Corpo della richiesta",
    )


def _service(session: Session) -> ApprovalRequestService:
    return ApprovalRequestService(session, gmail_settings())


def test_dates_are_spelled_in_italian_whatever_the_machines_locale() -> None:
    assert italian_date(date(2026, 11, 2)) == "lunedì 2 novembre 2026"
    assert italian_date(date(2026, 3, 1)) == "domenica 1 marzo 2026"


def test_the_request_lists_the_dates_oldest_first_and_asks_for_a_reply_before_the_first() -> None:
    message = compose(
        "Consulenza architetturale",
        [_day(date(2026, 11, 4), "0.5", "Review"), _day(date(2026, 11, 2), "1", "Workshop")],
    )

    assert message.subject == "Richiesta di approvazione delle giornate: Consulenza architetturale"
    assert message.body.index("lunedì 2 novembre 2026") < message.body.index(
        "mercoledì 4 novembre 2026"
    )
    assert "- lunedì 2 novembre 2026: giornata intera, Workshop" in message.body
    assert "- mercoledì 4 novembre 2026: mezza giornata, Review" in message.body
    assert "entro lunedì 2 novembre 2026" in message.body
    assert "«Consulenza architetturale»" in message.body


def test_a_date_asked_for_twice_is_refused_by_the_model() -> None:
    with pytest.raises(ValueError, match="due volte"):
        ApprovalRequestCompose(days=[_day(date(2026, 11, 2)), _day(date(2026, 11, 2), "0.5")])


def test_the_body_and_the_recipient_lists_are_bounded() -> None:
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))],
            to_addresses=["a@b.example"],
            subject="x",
            body="y" * 100_001,
        )
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))],
            to_addresses=[f"a{n}@b.example" for n in range(21)],
            subject="x",
            body="y",
        )


def test_an_address_that_is_not_one_is_refused_before_any_day_is_written(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    body = _prepare(_day(date(2026, 11, 2))).model_copy(update={"to_addresses": ["pippo"]})

    with pytest.raises(ValidationFailed) as refused:
        _service(db_session).prepare(contract.id, body, WRITER)
    assert refused.value.details["field"] == "to_addresses"

    assert db_session.scalars(select(WorkUnit)).all() == []


def test_a_day_asked_for_by_date_is_whole_or_half() -> None:
    with pytest.raises(ValueError, match="intera"):
        _day(date(2026, 11, 2), "0.75")
    with pytest.raises(ValueError):
        ApprovalRequestCompose(days=[])


def test_preparing_records_the_days_as_proposed_and_files_the_draft_in_one_go(
    db_session: Session,
) -> None:
    contract = _contract(db_session)

    prepared = _service(db_session).prepare(
        contract.id,
        _prepare(_day(date(2026, 11, 4), "0.5"), _day(date(2026, 11, 2)), _day(date(2026, 11, 3))),
        WRITER,
    )

    assert [(d.date, d.status) for d in prepared.work_units] == [
        (date(2026, 11, 2), "proposed"),
        (date(2026, 11, 3), "proposed"),
        (date(2026, 11, 4), "proposed"),
    ]
    assert prepared.draft.send_state == "draft"
    assert prepared.draft.to_addresses == ["amministrazione@acme.example"]
    assert prepared.draft.entity_type == "organization"
    assert db_session.scalar(select(EmailDraft).where(EmailDraft.id == prepared.draft.id))
    steps = WorkUnitService(db_session).transitions_for(prepared.work_units[0].id)
    # A fixed reason: the recipient is on the draft, and the log is immutable.
    assert steps[0].reason == "Richiesta di approvazione preparata"


def test_a_date_the_contract_already_holds_a_day_for_refuses_the_whole_request(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)

    with pytest.raises(Conflict) as refused:
        _service(db_session).prepare(
            contract.id, _prepare(_day(date(2026, 11, 2)), _day(date(2026, 11, 3))), WRITER
        )
    assert refused.value.details["date"] == ["2026-11-03"]

    # Nothing of the refused request was written: only the first request's day exists.
    assert db_session.scalars(select(WorkUnit.date)).all() == [date(2026, 11, 3)]


def test_a_withdrawn_day_frees_its_date_for_a_new_request(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)
    WorkUnitService(db_session).transition(first.work_units[0].id, "revoked", WRITER, "ritirata")

    again = _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)

    assert again.work_units[0].status == "proposed"


def test_an_unknown_contract_is_not_found_and_an_agent_is_refused(db_session: Session) -> None:
    with pytest.raises(NotFound):
        _service(db_session).prepare(uuid4(), _prepare(_day(date(2026, 11, 2))), WRITER)
    contract = _contract(db_session)
    with pytest.raises(AgentForbidden):
        _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 2))), AGENT)


def test_a_draft_that_cannot_be_filed_takes_its_proposed_days_with_it(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))], to_addresses=[], subject="x", body="y"
        )
    with pytest.raises(ValidationFailed):
        # `model_construct` skips the model's own bound; the draft service refuses the empty
        # list after the days are flushed, and `prepare` rolls them back.
        _service(db_session).prepare(
            contract.id,
            ApprovalRequestPrepare.model_construct(
                days=[_day(date(2026, 11, 2))],
                to_addresses=[],
                cc_addresses=[],
                subject="x",
                body="y",
            ),
            WRITER,
        )

    assert db_session.scalars(select(WorkUnit)).all() == []


def test_the_prepared_request_leaves_from_the_owners_mailbox_and_the_days_stay_proposed(
    db_session: Session,
) -> None:
    """The whole of REB-749's done-when that a test can reach: three dates are asked for,
    the request goes out through the one send path from the owner's connected mailbox, and
    the contract holds the three days as proposed. The recipient's side is the fake mailbox."""
    from email import message_from_string
    from email.policy import default as email_policy

    from fakes.fake_gmail import FakeGmail
    from fakes.gmail_fixtures import actor_for, connected_account, send_service

    contract = _contract(db_session)
    account = connected_account(db_session)
    owner = actor_for(account)
    prepared = ApprovalRequestService(db_session, gmail_settings()).prepare(
        contract.id,
        _prepare(_day(date(2026, 11, 2)), _day(date(2026, 11, 3)), _day(date(2026, 11, 4), "0.5")),
        owner,
    )
    fake = FakeGmail()

    sent = send_service(db_session, fake).send(
        prepared.draft.id, owner, expected_updated_at=prepared.draft.updated_at
    )

    assert sent.send_state == "sent"
    outgoing = [r for r in fake.requests if r.is_messages_send]
    assert len(outgoing) == 1
    import base64
    import json

    raw = base64.urlsafe_b64decode(json.loads(outgoing[0].body)["raw"] + "==").decode()
    message = message_from_string(raw, policy=email_policy)
    assert message["To"] == "amministrazione@acme.example"
    assert "io@example.it" in str(message["From"])
    assert message["Subject"] == "Richiesta di approvazione"
    assert [d.status for d in db_session.scalars(select(WorkUnit).order_by(WorkUnit.date))] == [
        "proposed"
    ] * 3
