"""Asking a client for approval of named days (REB-749): the text, and the one transaction
that records the days as proposed and files the message as a draft.

Sending is `EmailSendService`'s, tested in `test_email_send.py`; nothing here calls Gmail.
"""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fakes.gmail_fixtures import gmail_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.contracts.models import Contract
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import AgentForbidden, Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.models import EmailDraft
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

WRITER = Actor(id=uuid4(), type="user", role="collaboratore")
AGENT = Actor(id=uuid4(), type="mcp", role="admin")


def _contract(session: Session) -> Contract:
    customer = Customer(ragione_sociale=f"ACME {uuid4()}", email="amministrazione@acme.example")
    session.add(customer)
    session.flush()
    contract = Contract(
        customer_id=customer.id,
        titolo="Consulenza architetturale",
        inizio=date(2026, 1, 1),
        tipo_rinnovo="nessuno",
        preavviso_disdetta_giorni=30,
        cadenza_fatturazione="mensile",
        politica_spese={"kind": "non_rimborsabile"},
        requires_prior_approval=True,
    )
    session.add(contract)
    session.flush()
    return contract


def _day(giorno: date, quantita: str = "1", descrizione: str = "Workshop") -> ApprovalRequestDay:
    return ApprovalRequestDay(data=giorno, quantita=Decimal(quantita), descrizione=descrizione)


def _prepare(*days: ApprovalRequestDay) -> ApprovalRequestPrepare:
    return ApprovalRequestPrepare(
        days=list(days),
        to_addresses=["amministrazione@acme.example"],
        oggetto="Richiesta di approvazione",
        corpo="Corpo della richiesta",
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

    assert message.oggetto == "Richiesta di approvazione delle giornate: Consulenza architetturale"
    assert message.corpo.index("lunedì 2 novembre 2026") < message.corpo.index(
        "mercoledì 4 novembre 2026"
    )
    assert "- lunedì 2 novembre 2026: giornata intera, Workshop" in message.corpo
    assert "- mercoledì 4 novembre 2026: mezza giornata, Review" in message.corpo
    assert "entro lunedì 2 novembre 2026" in message.corpo
    assert "«Consulenza architetturale»" in message.corpo


def test_a_date_asked_for_twice_is_refused_by_the_model() -> None:
    with pytest.raises(ValueError, match="due volte"):
        ApprovalRequestCompose(days=[_day(date(2026, 11, 2)), _day(date(2026, 11, 2), "0.5")])


def test_the_body_and_the_recipient_lists_are_bounded() -> None:
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))],
            to_addresses=["a@b.example"],
            oggetto="x",
            corpo="y" * 100_001,
        )
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))],
            to_addresses=[f"a{n}@b.example" for n in range(21)],
            oggetto="x",
            corpo="y",
        )


def test_an_address_that_is_not_one_is_refused_before_any_day_is_written(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    body = _prepare(_day(date(2026, 11, 2))).model_copy(update={"to_addresses": ["pippo"]})

    with pytest.raises(ValidationFailed, match="non e' un indirizzo"):
        _service(db_session).prepare(contract.id, body, WRITER)

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

    assert [(d.data, d.stato) for d in prepared.work_units] == [
        (date(2026, 11, 2), "proposto"),
        (date(2026, 11, 3), "proposto"),
        (date(2026, 11, 4), "proposto"),
    ]
    assert prepared.draft.send_state == "bozza"
    assert prepared.draft.to_addresses == ["amministrazione@acme.example"]
    assert prepared.draft.entity_type == "customer"
    assert db_session.scalar(select(EmailDraft).where(EmailDraft.id == prepared.draft.id))
    steps = WorkUnitService(db_session).transitions_for(prepared.work_units[0].id)
    # A fixed reason: the recipient is on the draft, and the log is immutable.
    assert steps[0].motivo == "Richiesta di approvazione preparata"


def test_a_date_the_contract_already_holds_a_day_for_refuses_the_whole_request(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)

    with pytest.raises(Conflict, match="2026-11-03"):
        _service(db_session).prepare(
            contract.id, _prepare(_day(date(2026, 11, 2)), _day(date(2026, 11, 3))), WRITER
        )

    # Nothing of the refused request was written: only the first request's day exists.
    assert db_session.scalars(select(WorkUnit.data)).all() == [date(2026, 11, 3)]


def test_a_withdrawn_day_frees_its_date_for_a_new_request(db_session: Session) -> None:
    contract = _contract(db_session)
    first = _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)
    WorkUnitService(db_session).transition(first.work_units[0].id, "revocato", WRITER, "ritirata")

    again = _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 3))), WRITER)

    assert again.work_units[0].stato == "proposto"


def test_an_unknown_contract_is_not_found_and_an_agent_is_refused(db_session: Session) -> None:
    with pytest.raises(NotFound):
        _service(db_session).prepare(uuid4(), _prepare(_day(date(2026, 11, 2))), WRITER)
    contract = _contract(db_session)
    with pytest.raises(AgentForbidden):
        _service(db_session).prepare(contract.id, _prepare(_day(date(2026, 11, 2))), AGENT)


def test_a_draft_without_a_recipient_is_refused_before_any_day_is_written(
    db_session: Session,
) -> None:
    contract = _contract(db_session)
    with pytest.raises(ValueError):
        ApprovalRequestPrepare(
            days=[_day(date(2026, 11, 2))], to_addresses=[], oggetto="x", corpo="y"
        )
    with pytest.raises(ValidationFailed):
        # The model accepts an empty-looking address list only through the service's own check.
        _service(db_session).prepare(
            contract.id,
            ApprovalRequestPrepare.model_construct(
                days=[_day(date(2026, 11, 2))],
                to_addresses=[],
                cc_addresses=[],
                oggetto="x",
                corpo="y",
            ),
            WRITER,
        )


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

    assert sent.send_state == "inviato"
    outgoing = [r for r in fake.requests if r.is_messages_send]
    assert len(outgoing) == 1
    import base64
    import json

    raw = base64.urlsafe_b64decode(json.loads(outgoing[0].body)["raw"] + "==").decode()
    message = message_from_string(raw, policy=email_policy)
    assert message["To"] == "amministrazione@acme.example"
    assert "io@example.it" in str(message["From"])
    assert message["Subject"] == "Richiesta di approvazione"
    assert [d.stato for d in db_session.scalars(select(WorkUnit).order_by(WorkUnit.data))] == [
        "proposto"
    ] * 3
