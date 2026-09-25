"""Creating a reminder creates a draft, and sends nothing.

That is the whole claim of this file, and it is what lets spec 7.3 rely on 6.1's
idempotence instead of reimplementing it: **one send path in the whole slice**. A reminder
service with a send of its own would be the second one, and the second one is where the
double send comes back -- the previous system's `emailSentCount` lived in a JSON file precisely
because sending was open-coded next to the thing that wanted it.

"Sends nothing" is asserted, never trusted. `GmailTransport.json` is the single chokepoint
every Gmail call in this slice goes through -- the sync, the send, the reconciliation --
and `EmailSendService.send` is the only method that may reach `users.messages.send`. Both
are replaced with a failure here, so a `create_reminder` that grew a send of its own lands
in this file rather than in somebody's client's inbox. `FakeGmail.requests` is checked too,
wired to a real send service on the same session, so the absence is proved against a fake
that demonstrably records when something *is* sent (`test_gmail_send.py`).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID

import pytest
from fakes.fake_gmail import FakeGmail
from fakes.gmail_fixtures import (
    actor_for,
    connected_account,
    gmail_settings,
    send_service,
)
from fakes.invoice_fixtures import unpaid_invoice
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.db import session_factory
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, PaymentReminder
from pigrocrm.core.gmail.send import EmailSendService
from pigrocrm.core.gmail.solleciti import SollecitiService
from pigrocrm.core.gmail.solleciti_template import SOLLECITO_TEMPLATE_SOURCE
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.people.models import Person

IBAN = "IT60X0542811101000000123456"


def _service(session: Session) -> SollecitiService:
    return SollecitiService(session, settings=gmail_settings())


def _days_ago(days: int) -> date:
    return oggi_in_italia() - timedelta(days=days)


def _profiles(session: Session, *, iban: str | None = IBAN) -> None:
    """The two singleton rows a reminder reads: who is asking, and where to pay.

    Both are real service data rather than fixtures invented here -- the issuer's name and
    signature come from `emitter_profile`, the IBAN from `fiscal_profile.iban`, which is
    where slice 3 put it (there is no IBAN column on `emitter_profile`).
    """
    session.add(
        EmitterProfile(
            ragione_sociale="Studio Rossi",
            telefono="+39 333 1234567",
            sito_web="https://studiorossi.it",
            firma_email="Mario Rossi\nConsulente",
        )
    )
    session.add(FiscalProfile(codice_regime="RF19", iban=iban))
    session.flush()


def _numero(invoice: Invoice) -> str:
    assert invoice.anno is not None and invoice.numero is not None
    return numero_completo(invoice.anno, invoice.numero)


def _ready(session: Session, *, giorni: int = 30) -> Invoice:
    _profiles(session)
    return unpaid_invoice(session, due=_days_ago(giorni), totale=Decimal("1220.00"))


def _with_pdf(session: Session, invoice: Invoice, *, content_type: str = "application/pdf") -> UUID:
    """Give an invoice the rendered PDF slice 3 would have stored, and return the id of
    its current version -- the one `invoice_pdf_version_ids` is supposed to find.

    Built as rows rather than through `InvoiceService.issue`: nothing here cares about
    the register or the Typst renderer, only about whether a document exists for the
    reminder to attach.
    """
    document = Document(
        customer_id=invoice.customer_id,
        tipo="fattura",
        titolo=f"Fattura {_numero(invoice)}",
        versione_corrente=1,
    )
    session.add(document)
    session.flush()
    version = DocumentVersion(
        document_id=document.id,
        numero=1,
        storage_key=f"invoices/{invoice.id}.pdf",
        content_type=content_type,
        dimensione=1024,
        hash_sha256="0" * 64,
    )
    session.add(version)
    session.flush()
    invoice.pdf_document_id = document.id
    session.flush()
    return version.id


# --- the claim ------------------------------------------------------------------------


def test_a_reminder_with_nothing_to_attach_does_not_promise_an_attachment(
    db_session: Session,
) -> None:
    """The residual B2-9 left open, closed at the only place it can be closed.

    `GmailRepository.invoice_pdf_version_ids` answers `[]` for an invoice whose PDF was
    never rendered or has since been removed -- and the reminder still goes out, because
    a missing file is not a reason to stop chasing a real debt. What must not go out is
    the promise: «in allegato trova copia di cortesia della fattura» with an empty
    `attachment_version_ids` sends a paying client looking for a file that is not there.

    The two halves are asserted together on purpose. Testing the body alone would pass
    with an attachment list that had quietly changed; testing the list alone would pass
    with a body that still promised. What has to hold is that they agree.
    """
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.commit()

    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.attachment_version_ids == []
    assert "In allegato" not in draft.body_markdown
    assert "copia di cortesia" not in draft.body_markdown
    # The invoice's own figures are still there: this drops one sentence, not the letter.
    assert "1.220,00 €" in draft.body_markdown
    assert IBAN in draft.body_markdown


def test_a_reminder_that_does_attach_the_invoice_still_says_so(db_session: Session) -> None:
    """The other half of the same rule, and the reason it is not simply "never promise":
    when the courtesy copy *is* attached, the recipient has to be told it is there --
    otherwise a client who never opens attachments reads a demand for money with no
    document behind it.

    The attached version is the invoice's own current one, through slice 2's document
    layer (spec 6.4), never an upload -- so the id asserted here is the one
    `invoice_pdf_version_ids` resolved, not one this test invented.
    """
    account = connected_account(db_session)
    invoice = _ready(db_session)
    version_id = _with_pdf(db_session, invoice)
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.commit()

    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.attachment_version_ids == [str(version_id)]
    assert "In allegato trova copia di cortesia della fattura." in draft.body_markdown


def test_a_current_version_that_is_not_a_pdf_is_not_attached_as_the_invoice(
    db_session: Session,
) -> None:
    """The invoice's PDF goes out as a PDF or not at all (REB-480). A current version of
    another type, written before `add_version` refused one on that document, is not the
    courtesy copy: the reminder goes without it, and without the sentence promising it."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    _with_pdf(db_session, invoice, content_type="application/xml")
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.commit()

    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.attachment_version_ids == []
    assert "copia di cortesia" not in draft.body_markdown


def test_creating_a_reminder_creates_a_draft_and_sends_nothing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 8.3: POST /api/payment-reminders does not send.

    Three assertions of the absence, because it is the one thing here that cannot be
    walked back: no request reached the fake, no call reached the transport, and the
    draft is still `bozza` -- a state `EmailSendService` alone can leave.
    """
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()

    fake = FakeGmail()
    # Wired to a real send service on this session, so the fake is the same object a
    # send would record into -- not an unattached one that records nothing by
    # construction.
    send_service(db_session, fake)

    def refuse(*args: object, **kwargs: object) -> None:
        pytest.fail("preparare un sollecito non deve chiamare Gmail, e non deve inviare")

    monkeypatch.setattr(GmailTransport, "json", refuse)
    monkeypatch.setattr(EmailSendService, "send", refuse)

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.commit()

    assert read.sequence == 1
    assert read.email_draft_id is not None
    assert read.sent_at is None
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.send_state == "bozza"
    assert draft.google_account_id is None, "nothing has been sent, so no mailbox is involved"
    assert "Sollecito" in draft.subject
    assert _numero(invoice) in draft.subject
    # Not one HTTP call. Nothing was sent, by anyone, anywhere.
    assert fake.requests == []


def test_the_reminder_row_is_committed_before_anything_could_send_it(
    db_session: Session,
) -> None:
    """The ordering that makes the ceiling and the interval mean anything: the row and
    the draft exist, durably, while `sent_at` is still NULL. A reminder recorded only
    once the mail has left would leave the window in which pressing the button twice
    produces two letters."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))

    row = db_session.get(PaymentReminder, read.id)
    assert row is not None
    assert row.sent_at is None
    assert row.email_draft_id == read.email_draft_id
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    # Both directions of the link, so neither side has to be reconstructed by a scan.
    assert draft.payment_reminder_id == read.id


# --- the text -------------------------------------------------------------------------


def test_the_draft_body_is_the_template_and_not_a_string_in_the_source(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()
    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert "Fattura:" in draft.body_markdown
    assert "IBAN:" in draft.body_markdown
    assert IBAN in draft.body_markdown
    # De-personalised in B2-7: the previous system duplicated one freelancer's signature
    # verbatim in both of its builders. It is data now, so the name a client reads comes
    # from `emitter_profile`, and the template source carries no name at all. Asserting
    # the absence on the *body* would be asserting against the fixture that has to be
    # there, which is what a global rename briefly turned this into.
    assert "Mario Rossi" in draft.body_markdown
    assert "Studio Rossi" in draft.body_markdown
    assert "Mario Rossi" not in SOLLECITO_TEMPLATE_SOURCE
    assert "Studio Rossi" not in SOLLECITO_TEMPLATE_SOURCE


def test_the_body_renders_in_the_plain_context_so_a_number_is_not_escaped(
    db_session: Session,
) -> None:
    """`render_sollecito_body` exists because the default "markdown" context escapes the
    full ASCII punctuation class, and `2026/14` would reach a paying client as
    `2026\\/14`. Open-coding `render_template` here is the mistake that produces it."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()
    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert f"Fattura: {_numero(invoice)}" in draft.body_markdown
    assert "\\" not in draft.body_markdown


def test_the_amount_is_the_invoices_own_frozen_figure(db_session: Session) -> None:
    """Never a sum recomputed from the lines. A demand naming a figure the client's copy
    of the invoice does not carry is a demand they are right to ignore -- and the previous system
    applied a percentage to a total it had read back out of a formatted string."""
    account = connected_account(db_session)
    _profiles(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30), totale=Decimal("1234.50"))
    db_session.commit()
    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert "1.234,50" in draft.body_markdown


def test_the_dates_are_written_the_way_an_italian_client_reads_them(
    db_session: Session,
) -> None:
    account = connected_account(db_session)
    _profiles(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.commit()
    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert invoice.data_scadenza is not None
    assert f"Scadenza: {invoice.data_scadenza.strftime('%d/%m/%Y')}" in draft.body_markdown


def test_the_second_reminder_wears_the_second_wording(db_session: Session) -> None:
    """The level is a variable, not three templates. It escalates on what a client
    actually received, which is why the first row here carries a `sent_at`."""
    account = connected_account(db_session)
    invoice = _ready(db_session, giorni=60)
    db_session.add(
        PaymentReminder(
            invoice_id=invoice.id, sequence=1, sent_at=datetime.now(UTC) - timedelta(days=20)
        )
    )
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    assert read.sequence == 2
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert "nonostante il precedente sollecito" in draft.body_markdown


def test_a_reminder_that_was_never_sent_does_not_escalate_the_next_one(
    db_session: Session,
) -> None:
    """The row occupies position 2 in the sequence -- the unique constraint says so -- but
    the *wording* stays a first reminder, because «nonostante il precedente sollecito»
    about a letter still sitting in the drafts folder tells a client about something that
    never reached them."""
    account = connected_account(db_session)
    invoice = _ready(db_session, giorni=60)
    db_session.add(PaymentReminder(invoice_id=invoice.id, sequence=1, sent_at=None))
    db_session.commit()
    # Aged past the minimum interval, so what is under test is the wording and not the
    # interval refusing a second reminder written minutes after the first.
    stale = db_session.execute(select(PaymentReminder)).scalar_one()
    stale.created_at = datetime.now(UTC) - timedelta(days=30)
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    assert read.sequence == 2
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert "nonostante il precedente sollecito" not in draft.body_markdown
    assert "non ancora saldata" in draft.body_markdown


# --- who it goes to, and what it carries ----------------------------------------------


def test_it_goes_to_the_people_on_the_customer_when_there_is_no_generic_address(
    db_session: Session,
) -> None:
    """On a small company the person who pays is rarely a generic mailbox."""
    account = connected_account(db_session)
    _profiles(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30), email=None)
    db_session.add(Person(customer_id=invoice.customer_id, nome="Ada", email="Ada@Acme.IT"))
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.to_addresses == ["ada@acme.it"]


def test_a_customer_with_nowhere_to_write_is_refused_before_a_row_exists(
    db_session: Session,
) -> None:
    """Refused, not sent to nobody. And nothing is left behind: a reminder row for a
    letter that could never be written would burn a position in the sequence."""
    account = connected_account(db_session)
    _profiles(db_session)
    invoice = unpaid_invoice(db_session, due=_days_ago(30), email=None)
    db_session.commit()

    with pytest.raises(Conflict):
        _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.rollback()
    assert db_session.execute(select(func.count()).select_from(PaymentReminder)).scalar_one() == 0
    assert db_session.execute(select(func.count()).select_from(EmailDraft)).scalar_one() == 0


def test_a_missing_iban_is_refused_and_never_becomes_a_placeholder(
    db_session: Session,
) -> None:
    """The previous system's `normalizeIban(iban) || 'IBAN_PAGAMENTO'` shipped the placeholder to
    the client whenever the value was missing, which is worse than refusing: the client reads
    an instruction to pay into a string."""
    account = connected_account(db_session)
    _profiles(db_session, iban=None)
    invoice = unpaid_invoice(db_session, due=_days_ago(30))
    db_session.commit()

    with pytest.raises(Conflict, match="IBAN"):
        _service(db_session).create_reminder(invoice.id, actor_for(account))
    db_session.rollback()
    assert db_session.execute(select(func.count()).select_from(EmailDraft)).scalar_one() == 0


def test_a_reminder_inside_an_existing_thread_replies_to_the_original_send(
    db_session: Session,
) -> None:
    """Spec 13, criterion 13, end to end: the reminder threads onto the invoice's own
    covering email, so the recipient can see the invoice above it. Without it the
    reminder arrives detached and the first thing they do is ask for the invoice
    again."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    original = GmailMessage(
        google_account_id=account.id,
        gmail_message_id="m-orig",
        gmail_thread_id="t-invoice",
        message_id_header="<invio.originale@crm.example.it>",
        direction="outbound",
        from_address="io@example.it",
        to_addresses=["info@acme.it"],
        subject=f"Fattura {_numero(invoice)}",
        internal_date=datetime.now(UTC),
    )
    db_session.add(original)
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.in_reply_to_message_id == original.id


def test_it_does_not_thread_onto_a_message_about_a_different_invoice(
    db_session: Session,
) -> None:
    """The subject match is on this invoice's own number. Threading a reminder for
    2026/14 onto the covering email of 2026/1 would put the wrong document above it."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.add(
        GmailMessage(
            google_account_id=account.id,
            gmail_message_id="m-other",
            gmail_thread_id="t-other",
            message_id_header="<altra@crm.example.it>",
            direction="outbound",
            from_address="io@example.it",
            to_addresses=["info@acme.it"],
            subject="Fattura di qualcun altro",
            internal_date=datetime.now(UTC),
        )
    )
    db_session.commit()

    read = _service(db_session).create_reminder(invoice.id, actor_for(account))
    draft = db_session.get(EmailDraft, read.email_draft_id)
    assert draft is not None
    assert draft.in_reply_to_message_id is None


# --- the three layers of spec 7.3 -----------------------------------------------------


def test_creating_a_reminder_beyond_the_cap_is_refused(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = _ready(db_session, giorni=200)
    for sequence in (1, 2, 3):
        db_session.add(PaymentReminder(invoice_id=invoice.id, sequence=sequence))
    db_session.commit()
    with pytest.raises(Conflict, match="massimo"):
        _service(db_session).create_reminder(invoice.id, actor_for(account))


def test_a_second_reminder_inside_the_minimum_interval_is_refused(
    db_session: Session,
) -> None:
    """The layer that stops the double send hours apart, after the first one has been
    forgotten. `candidates()` already applies it, and the create endpoint is reachable
    by invoice id without going through the list -- so a create that did not apply it
    would disagree with the list about the same invoice, which is the shape of every
    "the CRM believes something different from what happened" defect in this slice."""
    account = connected_account(db_session)
    invoice = _ready(db_session, giorni=60)
    db_session.add(
        PaymentReminder(
            invoice_id=invoice.id, sequence=1, sent_at=datetime.now(UTC) - timedelta(days=3)
        )
    )
    db_session.commit()
    with pytest.raises(Conflict, match="giorni"):
        _service(db_session).create_reminder(invoice.id, actor_for(account))


def test_an_invoice_that_is_not_a_candidate_is_not_a_reminder_either(
    db_session: Session,
) -> None:
    """The list and the create must not disagree. An `annullata` invoice never appears in
    `candidates()`, and asking for it by id -- which the REST endpoint allows -- must be
    refused for the same reason: the issuer has withdrawn the document."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    invoice.stato = "annullata"
    invoice.annullata_il = _days_ago(1)
    invoice.motivo_annullamento = "errore di emissione"
    db_session.commit()
    with pytest.raises(Conflict, match="annullata"):
        _service(db_session).create_reminder(invoice.id, actor_for(account))


def test_an_invoice_already_collected_is_refused(db_session: Session) -> None:
    account = connected_account(db_session)
    invoice = _ready(db_session)
    invoice.stato_pagamento = "incassato"
    invoice.data_incasso = _days_ago(1)
    db_session.commit()
    with pytest.raises(Conflict, match="incassata"):
        _service(db_session).create_reminder(invoice.id, actor_for(account))


def test_an_invoice_that_does_not_exist_is_a_not_found(db_session: Session) -> None:
    account = connected_account(db_session)
    _profiles(db_session)
    db_session.commit()
    with pytest.raises(NotFound):
        _service(db_session).create_reminder(UUID(int=0), actor_for(account))


def test_a_readonly_actor_may_read_the_list_but_not_prepare_a_reminder(
    db_session: Session,
) -> None:
    """Reading which invoices are late is a plain read of the owner's own register.
    Writing a letter that goes out in their name is not."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()
    reader = Actor(id=account.user_id, type="user", role="readonly")

    assert [c.invoice_id for c in _service(db_session).candidates(reader)] == [invoice.id]
    with pytest.raises(PermissionDenied):
        _service(db_session).create_reminder(invoice.id, reader)


# --- the round trip -------------------------------------------------------------------


def test_the_candidate_leaves_the_list_once_the_reminder_has_been_sent(
    db_session: Session,
) -> None:
    """And `sent_at` is stamped by the send path, not by the creation: the two are
    different facts and the whole escalation depends on telling them apart."""
    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.commit()
    service = _service(db_session)
    read = service.create_reminder(invoice.id, actor_for(account))
    db_session.commit()

    fake = FakeGmail()
    send_service(db_session, fake).send(read.email_draft_id, actor_for(account))
    db_session.commit()

    assert len([r for r in fake.requests if r.is_messages_send]) == 1
    assert service.candidates(actor_for(account)) == []
    row = db_session.get(PaymentReminder, read.id)
    assert row is not None
    assert row.sent_at is not None


def test_an_ordinary_email_that_is_not_a_reminder_stamps_nothing(
    db_session: Session,
) -> None:
    """The send path looks the reminder up by the draft, and a draft that is not one
    must leave `payment_reminders` untouched. Without the lookup being keyed on the
    draft, the newest reminder in the database would be stamped by an unrelated email."""
    from pigrocrm.core.gmail.drafts import EmailDraftService
    from pigrocrm.core.gmail.schemas import EmailDraftCreate

    account = connected_account(db_session)
    invoice = _ready(db_session)
    db_session.add(PaymentReminder(invoice_id=invoice.id, sequence=1))
    db_session.commit()

    draft = EmailDraftService(db_session, settings=gmail_settings()).create(
        EmailDraftCreate(
            entity_type="customer",
            entity_id=invoice.customer_id,
            to_addresses=["ada@acme.it"],
            subject="Buongiorno",
            body_markdown="Niente a che vedere con un sollecito.",
        ),
        actor_for(account),
    )
    send_service(db_session, FakeGmail()).send(draft.id, actor_for(account))
    db_session.commit()

    reminder = db_session.execute(select(PaymentReminder)).scalar_one()
    assert reminder.sent_at is None


# --- the database arbitrates ----------------------------------------------------------


def test_two_concurrent_creations_produce_one_row_and_one_conflict(db_engine: Engine) -> None:
    """Spec 13, criterion 14. Two real connections meeting at a barrier placed between
    the count and the insert, so **both** have already read "zero reminders" before
    either writes. That barrier is the test: without it one thread routinely finishes
    first and the count alone would look sufficient. Only
    `uq_payment_reminders_invoice_sequence` can arbitrate, and dropping it reports
    `["created", "created"]` -- two letters of demand for one invoice.
    """
    factory = session_factory(db_engine)
    invoice_id: UUID | None = None
    customer_id: UUID | None = None
    user_id: UUID | None = None
    try:
        with factory() as setup:
            account = connected_account(setup)
            user_id = account.user_id
            invoice = _ready(setup)
            invoice_id, customer_id = invoice.id, invoice.customer_id
            setup.commit()
            actor = actor_for(account)

        both_counted = Barrier(2, timeout=30)
        original = SollecitiService._next_sequence

        def count_at_the_same_moment(self: SollecitiService, target: UUID) -> int:
            sequence = original(self, target)
            both_counted.wait()
            return sequence

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(SollecitiService, "_next_sequence", count_at_the_same_moment)

            def attempt(_: int) -> str:
                with factory() as session:
                    try:
                        SollecitiService(session, settings=gmail_settings()).create_reminder(
                            invoice_id or UUID(int=0), actor
                        )
                    except Conflict:
                        return "conflict"
                    return "created"

            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = sorted(
                    future.result(timeout=60)
                    for future in [pool.submit(attempt, index) for index in range(2)]
                )

        assert outcomes == ["conflict", "created"], "one invoice got two reminders"
        with factory() as check:
            assert (
                check.execute(select(func.count()).select_from(PaymentReminder)).scalar_one() == 1
            )
    finally:
        _cleanup(db_engine, invoice_id=invoice_id, customer_id=customer_id, user_id=user_id)


def _cleanup(
    engine: Engine, *, invoice_id: UUID | None, customer_id: UUID | None, user_id: UUID | None
) -> None:
    """Everything the concurrency test committed, in the order the foreign keys allow.
    `payment_reminders` cascades from `invoices`, and `google_accounts` from `users`; the
    drafts have to go first, because `payment_reminders.email_draft_id` points at them.
    """
    with session_factory(engine)() as session:
        if invoice_id is not None:
            drafts = [
                draft_id
                for draft_id in session.execute(
                    select(PaymentReminder.email_draft_id).where(
                        PaymentReminder.invoice_id == invoice_id
                    )
                )
                .scalars()
                .all()
                if draft_id is not None
            ]
            session.execute(delete(PaymentReminder).where(PaymentReminder.invoice_id == invoice_id))
            session.execute(delete(EmailDraft).where(EmailDraft.id.in_(drafts)))
            session.execute(delete(Invoice).where(Invoice.id == invoice_id))
        if customer_id is not None:
            session.execute(delete(Person).where(Person.customer_id == customer_id))
            session.execute(delete(Customer).where(Customer.id == customer_id))
        session.execute(delete(EmitterProfile))
        session.execute(delete(FiscalProfile))
        if user_id is not None:
            session.execute(delete(User).where(User.id == user_id))
        session.commit()
