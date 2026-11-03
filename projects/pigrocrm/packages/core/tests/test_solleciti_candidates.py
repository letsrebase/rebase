"""Which invoices are worth chasing -- and, first of all, which are not.

Four conditions, one test each, plus the signal the previous system could not have had. Each
condition is here because its absence is a message somebody's client receives: chasing a paid
invoice, chasing three days after the due date while the transfer is in flight, chasing twice in
one afternoon because the first one was forgotten, and chasing a disputed invoice forever.

The two conditions the brief's table could not name are in here too, and they are the
ones slice 3's real shape adds: a `bozza` has no number and no legal existence, and an
`annullata` invoice has been withdrawn. Sending a payment demand for either is not a
milder mistake than the four above -- it is the same mistake with a legal document
attached.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fakes.gmail_fixtures import actor_for, connected_account, gmail_settings
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.gmail.models import GmailMessage, PaymentReminder
from pigrocrm.core.gmail.send import EmailSendService
from pigrocrm.core.gmail.solleciti import SollecitiService
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.people.models import Person


def _service(session: Session) -> SollecitiService:
    return SollecitiService(session, settings=gmail_settings())


def _days_ago(days: int) -> date:
    return today_local() - timedelta(days=days)


# --- condition 1: it has been paid ---------------------------------------------------


def test_a_paid_invoice_is_never_a_candidate(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    invoice.stato_pagamento = "incassato"
    invoice.data_incasso = _days_ago(2)
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


# --- condition 2: the due date, plus the grace period --------------------------------


def test_an_invoice_inside_the_grace_period_is_not_yet_a_candidate(
    db_session: Session,
) -> None:
    """Default 7 days. Chasing the day after the due date is aggressive and often wrong:
    the transfer has already left."""
    account = connected_account(db_session)
    unpaid_invoice(db_session, due=_days_ago(3))
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_an_invoice_past_the_grace_period_is_a_candidate(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(10), totale=Decimal("1220.00"))
    db_session.commit()
    candidates = _service(db_session).candidates(actor_for(account))
    assert [candidate.invoice_id for candidate in candidates] == [invoice.id]
    assert candidates[0].giorni_di_ritardo == 10
    assert candidates[0].prossimo_livello == 1
    assert candidates[0].solleciti_inviati == 0
    # The invoice's own frozen figure, exactly. A reminder that names a recomputed
    # amount names a number the client's copy of the invoice does not carry.
    assert candidates[0].importo == Decimal("1220.00")


def test_an_invoice_with_no_due_date_is_never_chased(db_session: Session) -> None:
    """`data_scadenza` is nullable on slice 3's own model, and it is the only thing that
    makes a reminder legitimate. Nothing may substitute for it -- not the emission date
    plus a default, not "old enough". A demand for payment by a date nobody agreed is a
    demand this system has no standing to make."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(90))
    invoice.data_scadenza = None
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_an_unissued_draft_is_never_chased(db_session: Session) -> None:
    """A `bozza` has no number, and slice 3's own `CHECK` says so. It has never left the
    building, so there is nobody to remind."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    invoice.stato = "bozza"
    invoice.anno = None
    invoice.numero = None
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_an_annulled_invoice_is_never_chased(db_session: Session) -> None:
    """`annullata` keeps its number and loses its revenue (slice 3 §4). Demanding payment
    for an invoice the issuer has withdrawn is the worst letter in this whole feature."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    invoice.stato = "annullata"
    invoice.annullata_il = _days_ago(5)
    invoice.motivo_annullamento = "errore di emissione"
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_a_proforma_is_never_chased(db_session: Session) -> None:
    """A proforma is not a fiscal document and carries no number: the four mechanisms of
    slice 3 §4 exist to keep it from being mistaken for one, and this is a fifth."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    invoice.stato = "confermata"
    invoice.tipo = "proforma"
    invoice.anno = None
    invoice.numero = None
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_a_deleted_invoice_is_never_chased(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    invoice.anno = None
    invoice.numero = None
    invoice.stato = "bozza"
    invoice.deleted_at = datetime.now(UTC)
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


# --- condition 3: the minimum interval -----------------------------------------------


def test_a_recent_reminder_takes_it_off_the_list_for_the_minimum_interval(
    db_session: Session,
) -> None:
    """Default 14 days. This is the layer that stops the double send hours apart, after
    the first one has been forgotten."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.add(
        PaymentReminder(
            invoice_id=invoice.id, sequence=1, sent_at=datetime.now(UTC) - timedelta(days=3)
        )
    )
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_a_reminder_prepared_but_not_yet_sent_also_holds_the_interval(
    db_session: Session,
) -> None:
    """A draft written an hour ago is as good a reason not to write a second one as a
    reminder *sent* an hour ago: the double send this layer exists to prevent begins as
    a double draft, and `create_reminder` will happily make one up to the ceiling. So
    the interval is measured over `coalesce(sent_at, created_at)`, which is why an
    unsent row -- `sent_at` NULL, created now -- suppresses the candidate."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.add(PaymentReminder(invoice_id=invoice.id, sequence=1))
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_after_the_minimum_interval_it_returns_at_the_next_level(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(40))
    db_session.add(
        PaymentReminder(
            invoice_id=invoice.id, sequence=1, sent_at=datetime.now(UTC) - timedelta(days=20)
        )
    )
    db_session.commit()
    candidate = _service(db_session).candidates(actor_for(account))[0]
    assert candidate.solleciti_inviati == 1
    assert candidate.prossimo_livello == 2
    assert candidate.ultimo_sollecito_il == (datetime.now(UTC) - timedelta(days=20)).date()


def test_a_reminder_that_was_never_sent_does_not_escalate_the_wording(
    db_session: Session,
) -> None:
    """`prossimo_livello` counts what *left*, not what was written. A second reminder
    opening «nonostante il precedente sollecito» when the first one is still sitting in
    the drafts folder tells a client about a letter they never received."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(60))
    db_session.add(
        PaymentReminder(invoice_id=invoice.id, sequence=1, sent_at=None),
    )
    db_session.commit()
    # Age the unsent row past the interval, so the invoice is a candidate again and the
    # level it comes back at is the thing under test.
    reminder = db_session.query(PaymentReminder).one()
    reminder.created_at = datetime.now(UTC) - timedelta(days=30)
    db_session.commit()

    candidate = _service(db_session).candidates(actor_for(account))[0]
    assert candidate.solleciti_inviati == 0
    assert candidate.prossimo_livello == 1


# --- condition 4: the ceiling ---------------------------------------------------------


def test_the_cap_stops_it_becoming_automated_harassment(db_session: Session) -> None:
    """Default 3. What stops a disputed invoice from turning into a persecution."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(200))
    for sequence in (1, 2, 3):
        db_session.add(
            PaymentReminder(
                invoice_id=invoice.id,
                sequence=sequence,
                sent_at=datetime.now(UTC) - timedelta(days=60 - sequence * 15),
            )
        )
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


def test_the_ceiling_counts_prepared_reminders_too(db_session: Session) -> None:
    """Three drafts nobody sent still occupy the three positions the sequence has: the
    unique constraint on `(invoice_id, sequence)` is what makes that true, and the list
    has to agree with it or it would offer a fourth that `create_reminder` refuses."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(200))
    for sequence in (1, 2, 3):
        db_session.add(PaymentReminder(invoice_id=invoice.id, sequence=sequence))
    db_session.commit()
    for reminder in db_session.query(PaymentReminder).all():
        reminder.created_at = datetime.now(UTC) - timedelta(days=30)
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account)) == []


# --- the signal the previous system could not have had
# ----------------------------------------------


def test_a_client_who_replied_is_flagged_and_sorted_last_but_not_removed(
    db_session: Session,
) -> None:
    """A reply is not a payment, and sometimes the reply is exactly what needs chasing --
    so it does not suppress the candidate. It goes to the bottom of the list and says
    why. Chasing someone who has already replied is the mistake a CRM that does not read
    email cannot even notice it is making."""
    account = connected_account(db_session)
    quiet = unpaid_invoice(db_session, due=_days_ago(20), email="quiet@quiet.it")
    replied = unpaid_invoice(db_session, due=_days_ago(40), email="info@acme.it")
    db_session.add(
        GmailMessage(
            google_account_id=account.id,
            gmail_message_id="m-reply",
            gmail_thread_id="t-1",
            direction="inbound",
            from_address="info@acme.it",
            to_addresses=["io@example.it"],
            subject="Re: la fattura",
            internal_date=datetime.now(UTC) - timedelta(days=5),
        )
    )
    db_session.commit()

    candidates = _service(db_session).candidates(actor_for(account))
    # Without the reply, `replied` (40 days late) would sort ahead of `quiet` (20).
    assert [candidate.invoice_id for candidate in candidates] == [quiet.id, replied.id]
    assert candidates[0].ultima_risposta_il is None
    assert candidates[1].ultima_risposta_il == (datetime.now(UTC) - timedelta(days=5)).date()


def test_a_reply_from_a_person_on_the_customer_counts_as_the_customer_replying(
    db_session: Session,
) -> None:
    """On a small company the person who answers is rarely the address on the customer
    row. Reading only `customers.email` would report "nobody replied" about a
    conversation that is already open."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.add(
        Person(customer_id=invoice.customer_id, nome="Ada", email="ada@acme.it"),
    )
    db_session.add(
        GmailMessage(
            google_account_id=account.id,
            gmail_message_id="m-ada",
            gmail_thread_id="t-2",
            direction="inbound",
            from_address="ada@acme.it",
            to_addresses=["io@example.it"],
            subject="Re: la fattura",
            internal_date=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account))[0].ultima_risposta_il is not None


def test_another_mailbox_reply_is_not_this_installation_reply(db_session: Session) -> None:
    """`last_inbound_from` is scoped to one account, and this is what that scoping buys:
    a message stored under somebody else's mailbox must not answer this user's question
    about whether the client wrote back."""
    account = connected_account(db_session)
    other = connected_account(db_session, email_address="altro@example.it")
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    customer = db_session.get(Customer, invoice.customer_id)
    assert customer is not None
    db_session.add(
        GmailMessage(
            google_account_id=other.id,
            gmail_message_id="m-other",
            gmail_thread_id="t-3",
            direction="inbound",
            from_address=(customer.email or "").lower(),
            to_addresses=["altro@example.it"],
            subject="Re: la fattura",
            internal_date=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account))[0].ultima_risposta_il is None


def test_an_outbound_message_to_the_client_is_not_a_reply(db_session: Session) -> None:
    """Our own chasing email sitting in the thread is not the client answering. Without
    the `direction` predicate the list would report every invoice we have ever written
    about as "already replied to" and sort the whole thing upside down."""
    account = connected_account(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    customer = db_session.get(Customer, invoice.customer_id)
    assert customer is not None
    db_session.add(
        GmailMessage(
            google_account_id=account.id,
            gmail_message_id="m-ours",
            gmail_thread_id="t-4",
            direction="outbound",
            from_address=(customer.email or "").lower(),
            to_addresses=["info@acme.it"],
            subject="Fattura",
            internal_date=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db_session.commit()
    assert _service(db_session).candidates(actor_for(account))[0].ultima_risposta_il is None


# --- the honest degradation -----------------------------------------------------------


def test_the_list_still_works_with_no_mailbox_connected(db_session: Session) -> None:
    """An installation that never connected Gmail still has invoices to chase. It simply
    carries no reply signal, which is the honest degradation rather than an empty list."""
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.commit()
    candidates = _service(db_session).candidates(Actor.system())
    assert [candidate.invoice_id for candidate in candidates] == [invoice.id]
    assert candidates[0].ultima_risposta_il is None


def test_candidates_asks_gmail_for_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 7.1: "this call sends nothing. It prepares the list."

    Asserted by making every Gmail call in this slice fail loudly rather than by
    trusting a comment: `GmailTransport.json` is the single chokepoint through which the
    sync, the send and the reconciliation all reach Google, so a `candidates` that grew
    an HTTP call of any kind -- a send most of all -- lands here instead of in somebody's
    client's inbox. The whole product argument is that the boring part is *building the
    list*; a list-builder that talks to Google is a different feature.
    """
    account = connected_account(db_session)
    unpaid_invoice(db_session, due=_days_ago(30))
    db_session.commit()

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("candidates() reached Google: it must send nothing and ask nothing")

    monkeypatch.setattr(GmailTransport, "json", refuse)
    monkeypatch.setattr(EmailSendService, "send", refuse)

    assert len(_service(db_session).candidates(actor_for(account))) == 1
