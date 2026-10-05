"""Which invoices are worth chasing.

A query, not an event. And **this call sends nothing** -- it prepares the list, which is
the part that was actually laborious: crossing due dates against payments against what
has already gone out. Pressing a button was never the work.

The comparison with the previous system justifies each of the conditions. There, the reminder was
chosen by `emailSentCount > 0` -- "is this the second email" -- with no due date, no
interval and no ceiling anywhere. Here the due date is the only thing that makes a
reminder legitimate, the interval is the only thing that makes it bearable, and the
ceiling is what stops a disputed invoice becoming an automated persecution.

Two further conditions come from slice 3's real shape rather than from the brief's
table, and they are not milder: an invoice that has not been *issued* has no number and
no legal existence, and one that has been `annullata` has been withdrawn. A payment
demand for either is the same mistake as chasing a paid invoice, with a fiscal document
attached to it.

Nothing here logs, and nothing that leaves this module carries a body or a recipient.
The candidate carries the client's *name*, which is what a list of invoices to chase has
to show; the addresses it will be sent to are resolved at composition time and stay
there.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.gmail.drafts import EmailDraftService
from pigrocrm.core.gmail.models import EmailDraft, GoogleAccount, PaymentReminder
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import (
    EmailDraftCreate,
    PaymentReminderRead,
    SollecitoCandidate,
)
from pigrocrm.core.gmail.solleciti_template import render_sollecito_body
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import numero_completo
from pigrocrm.core.money import round_money
from pigrocrm.core.people.models import Person

# What `_query` yields per row: the invoice, its customer, how many reminder rows exist,
# how many of them were actually sent, when the last one left, and when the last one was
# either sent or prepared.
CandidateRow = tuple[Invoice, Customer, int, int, datetime | None, datetime | None]

# The one state in `invoices.stato_pagamento` that means the money arrived. Compared
# positively -- `!= 'incassato'` -- rather than against a list of the others, so a value
# added to `StatoPagamento` later defaults to *chaseable* and shows up in a test instead
# of silently disappearing from the list.
INCASSATO = "incassato"

# An invoice is chaseable only from here. `tipo` and `stato` together, because slice 3
# runs two state machines through one column: `emessa` is a fattura that consumed a
# register number, while `confermata` on the same column is a proforma -- not a fiscal
# document, and not something anybody owes money against.
CHASEABLE_TIPO = "fattura"
CHASEABLE_STATO = "emessa"

ENTITY = "payment_reminder"
INVOICE_ENTITY = "invoice"
_PREPARE_ACTION = "preparare un sollecito"


def _data_italiana(value: date | None) -> str:
    """`31/07/2026`. The way the recipient of this letter reads a date.

    Not `invoices.totals`' formatters and not `.isoformat()`: those two are the *fiscal*
    representations, one for FPR12's schema and one for the PDF, and neither is what an
    Italian client expects in the body of an email. `%d/%m/%Y` matches the sentence
    `gmail/account.py` already shows people about their consent expiry.
    """
    return value.strftime("%d/%m/%Y") if value is not None else ""


def _euro(value: Decimal) -> str:
    """`1.220,00 €`. Italian grouping and decimal separators, both.

    Deliberately **not** `invoices.totals.format_amount_2`, which is FPR12's
    `Amount2DecimalType` -- `1220.00`, a schema format for a machine at the Agenzia delle
    Entrate. Printing that in a demand for payment shows an Italian reader a number
    written the wrong way round.

    `round_money` is `money.py`'s, so the one place rounding happens stays the one place:
    the column is already `Numeric(12, 2)`, so this quantizes nothing in practice, and it
    is here so that a future caller handing this a wider `Decimal` cannot invent a third
    rounding rule.
    """
    quantized = round_money(value)
    # `f"{...:,.2f}"` gives `1,220.00`; the swap turns it into Italian in one pass, with
    # the placeholder step so the two separators cannot overwrite each other.
    return f"{quantized:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".") + " €"


class SollecitiService:
    """Payment reminders: the list, and the preparation of one.

    Takes no transport and no `GoogleTokenClient`, and that absence is the design rather
    than an omission: nothing in this class may reach Google. The list is a database
    query, and preparing a reminder writes a row and a draft -- the draft then leaves
    through `EmailSendService.send`, which is the one send path in the slice.
    """

    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repo = GmailRepository(session)
        self.activities = ActivityService(session)

    def candidates(self, actor: Actor) -> list[SollecitoCandidate]:
        """Every invoice that may legitimately be chased, worst first.

        Reading the list is not role-gated -- a `readonly` user may see which invoices are
        late, and that is a plain read of their own register. What is gated is preparing
        the reminder and sending it.
        """
        today = oggi_in_italia()
        grace_cutoff = today - timedelta(days=self.settings.solleciti_grace_days)
        interval_cutoff = datetime.now(UTC) - timedelta(
            days=self.settings.solleciti_min_interval_days
        )

        account = self._mailbox_of(actor)
        candidates: list[SollecitoCandidate] = []
        for invoice, customer, righe, inviati, ultimo, ultima_attivita in self.session.execute(
            self._query(grace_cutoff)
        ).all():
            # Condition 3: no reminder, sent *or merely prepared*, inside the minimum
            # interval. `ultima_attivita` is `max(coalesce(sent_at, created_at))`, and the
            # coalesce is the point: `create_reminder` writes a row before anything
            # leaves, so measuring only over `sent_at` would leave the invoice on the list
            # with a draft already waiting in it -- and the double send this layer exists
            # to prevent begins as a double draft.
            if ultima_attivita is not None and ultima_attivita >= interval_cutoff:
                continue
            # Condition 4: below the ceiling. Rows, not sends: three prepared reminders
            # occupy the three positions `(invoice_id, sequence)` has, and offering a
            # fourth here that `create_reminder` would refuse is a list that lies.
            if righe >= self.settings.solleciti_max_reminders:
                continue

            scadenza = invoice.data_scadenza
            if scadenza is None:  # pragma: no cover - the query's own WHERE excludes it
                continue
            candidates.append(
                SollecitoCandidate(
                    invoice_id=invoice.id,
                    numero=numero_completo(invoice.anno or 0, invoice.numero or 0),
                    data_fattura=invoice.data_emissione or scadenza,
                    data_scadenza=scadenza,
                    giorni_di_ritardo=(today - scadenza).days,
                    # The invoice's own frozen figure, straight off the column. Never a
                    # sum recomputed from the lines: a demand naming an amount the
                    # client's copy does not carry is a demand they are right to ignore.
                    importo=invoice.totale,
                    cliente=customer.ragione_sociale,
                    customer_id=customer.id,
                    solleciti_inviati=int(inviati),
                    ultimo_sollecito_il=ultimo.date() if ultimo is not None else None,
                    # The wording escalates on what *left*, not on how many rows exist:
                    # «nonostante il precedente sollecito» about a letter still sitting in
                    # the drafts folder describes something that never happened.
                    prossimo_livello=int(inviati) + 1,
                    ultima_risposta_il=self._replied_on(account, invoice, customer),
                )
            )

        # Repliers last, then most overdue first. Ordered here rather than in SQL because
        # the reply signal is not a column: it is the result of the per-customer lookup
        # above, and pushing it into the query would mean a join on `gmail_messages` that
        # says nothing clearer.
        candidates.sort(
            key=lambda candidate: (
                candidate.ultima_risposta_il is not None,
                -candidate.giorni_di_ritardo,
            )
        )
        return candidates

    # ---- preparing one ---------------------------------------------------------------

    def create_reminder(self, invoice_id: UUID, actor: Actor) -> PaymentReminderRead:
        """Creates the `payment_reminders` row **and** its draft. Sends nothing.

        Spec 8.3 is explicit that this endpoint does not send: the draft goes out through
        `/api/email-drafts/{id}/send` like any other email. That is what lets spec 7.3
        rely on 6.1's idempotence instead of reimplementing it -- **one send path in the
        whole slice** -- and it is why `sent_at` is filled by
        `EmailSendService._record_sent` rather than here. A reminder service with a send
        of its own would be the second path, and the second path is where the double send
        comes back.

        **The order of the two writes is deliberate.** The reminder row is reserved first,
        inside a SAVEPOINT, and the draft is written only once the position is ours.
        `EmailDraftService.create` commits -- durability is its whole point -- so writing
        the draft first would leave the loser of a race with a committed draft nobody
        asked for and a `Conflict` in its hand. This way a refused reminder leaves nothing
        behind at all.

        The refusals here are the same three conditions `candidates()` applies, and that
        is not duplication for its own sake: this endpoint is reachable by invoice id
        without going through the list, so a create that skipped them would disagree with
        the list about the same invoice -- which is the shape of every "il CRM crede una
        cosa diversa da quella che è successa" defect this slice exists to remove.
        """
        actor.require_write(_PREPARE_ACTION)

        invoice = self.session.get(Invoice, invoice_id)
        if invoice is None or invoice.deleted_at is not None:
            raise NotFound(INVOICE_ENTITY, invoice_id)
        self._require_chaseable(invoice)

        customer = self.session.get(Customer, invoice.customer_id)
        if customer is None or customer.deleted_at is not None:
            raise NotFound("customer", invoice.customer_id)
        recipients = self._recipients(customer)
        if not recipients:
            raise Conflict(
                ENTITY,
                f"{customer.ragione_sociale} non ha un indirizzo email a cui scrivere",
                customer_id=str(customer.id),
            )

        righe, inviati, ultima_attivita = self.repo.reminder_rows(invoice_id)
        self._require_room(invoice_id, righe, ultima_attivita)
        # `livello` is what the *client* has already received, so an unsent draft cannot
        # make the next letter open with «nonostante il precedente sollecito»; `sequence`
        # is the position in the register, which an unsent draft does occupy.
        livello = inviati + 1
        sequence = self._next_sequence(invoice_id)

        # Everything the body needs, resolved before anything is written: a reminder with
        # no IBAN is a reminder nobody can act on, and discovering that after the row
        # exists would burn a position in the sequence.
        # The invoice's azienda, not the default (REB-627, spec §1.8): the letter signs
        # as whoever issued and names that azienda's IBAN.
        emittente = self._emitter_scope(actor, invoice.azienda_id)
        # Resolved before the body is rendered, because the body's own wording depends on
        # it: `invoice_pdf_version_ids` legitimately answers `[]` for an invoice whose PDF
        # was never rendered or has since been removed, and a reminder that promises «in
        # allegato trova copia di cortesia della fattura» with nothing attached asks a
        # paying client to look for a file that does not exist. One read, two uses -- a
        # second call here is how the sentence and the attachment list drift apart.
        allegati = self.repo.invoice_pdf_version_ids(invoice_id)
        body = render_sollecito_body(
            {
                **emittente,
                "cliente": customer.ragione_sociale,
                "numero_fattura": self._numero(invoice),
                "data_fattura": _data_italiana(invoice.data_emissione),
                "scadenza": _data_italiana(invoice.data_scadenza),
                "importo": _euro(invoice.totale),
                "iban": self._iban(invoice.azienda_id),
                # The free-text block. Read from the same scope rather than from a second
                # query, and offered at the top level too because the template puts it
                # above the company line -- one source, two names, no second copy to
                # diverge.
                "firma_email": emittente["emittente"].get("firma_email") or "",
            },
            livello=livello,
            con_allegato=bool(allegati),
        )

        reminder = PaymentReminder(invoice_id=invoice_id, sequence=sequence)
        try:
            # A SAVEPOINT and not a bare flush, so the expected refusal costs exactly the
            # failed statement. `Session.rollback()` here would discard whatever the
            # caller had already done in this transaction -- and on this path the caller
            # is a REST handler whose session may carry more than this one row.
            with self.session.begin_nested():
                self.session.add(reminder)
                self.session.flush()
        except IntegrityError as clash:
            # `uq_payment_reminders_invoice_sequence` is the first of the three layers of
            # spec 7.3, and the database is what guarantees it: two concurrent callers
            # both pass the count above, and only the constraint stops the second.
            raise Conflict(
                ENTITY,
                f"un sollecito numero {sequence} per questa fattura esiste già",
                invoice_id=str(invoice_id),
                sequence=sequence,
            ) from clash

        draft = EmailDraftService(self.session, settings=self.settings).create(
            EmailDraftCreate(
                entity_type="customer",
                entity_id=customer.id,
                to_addresses=recipients,
                subject=f"Sollecito pagamento – {self._numero(invoice)}",
                body_markdown=body,
                # The invoice's own PDF, through slice 2's document layer -- never an
                # arbitrary upload (spec 6.4).
                attachment_version_ids=allegati,
                # Threads the reminder onto the original covering email, so the recipient
                # sees the invoice above it.
                in_reply_to_message_id=self._thread_of(invoice, actor),
            ),
            actor,
            # The invoice's azienda, which the body and the IBAN already speak for.
            azienda_id=invoice.azienda_id,
        )

        # Both directions of the link. The reminder needs the draft so the caller can send
        # it through the one send path; the draft needs the reminder so `_record_sent` can
        # stamp `sent_at` without scanning for "the newest reminder of this invoice",
        # which would let an unrelated email mark a reminder as sent.
        reminder.email_draft_id = draft.id
        row = self.session.get(EmailDraft, draft.id)
        if row is not None:
            row.payment_reminder_id = reminder.id

        # Last before the commit, per `ActivityService.record`. The payload names the
        # invoice and the position, never the recipient and never the body.
        self.activities.record(
            "customer",
            customer.id,
            "gmail.sollecito_preparato",
            actor,
            {
                "invoice_id": str(invoice_id),
                "numero": self._numero(invoice),
                "sequence": sequence,
                "livello": livello,
                "email_draft_id": str(draft.id),
                # Deliberately not "inviato": this records that a reminder was *prepared*.
                # The send has its own entry, written by the send path.
                "inviato": False,
            },
        )
        self.session.commit()
        return PaymentReminderRead.model_validate(reminder)

    # ---- internals ------------------------------------------------------------------

    def _require_chaseable(self, invoice: Invoice) -> None:
        """The same conditions the list applies, in the language of a refusal.

        Each names what is wrong rather than answering "not a candidate", because the
        person pressing this button is looking at one invoice and needs to know which of
        five different facts about it is in the way.
        """
        if invoice.tipo != CHASEABLE_TIPO or invoice.stato == "bozza":
            raise Conflict(ENTITY, "si può sollecitare solo una fattura già emessa")
        if invoice.stato == "annullata":
            raise Conflict(ENTITY, "questa fattura è stata annullata: non si può sollecitare")
        if invoice.stato != CHASEABLE_STATO:
            raise Conflict(ENTITY, "si può sollecitare solo una fattura già emessa")
        if invoice.stato_pagamento == INCASSATO:
            raise Conflict(ENTITY, "questa fattura risulta già incassata")
        if invoice.data_scadenza is None:
            raise Conflict(
                ENTITY,
                "questa fattura non ha una data di scadenza: senza una scadenza "
                "concordata non c'è nulla da sollecitare",
            )

    def _require_room(self, invoice_id: UUID, righe: int, ultima_attivita: datetime | None) -> None:
        """The ceiling and the interval, in that order.

        The ceiling first because it is the permanent answer: an invoice at the maximum
        will never accept another reminder, and telling the person to wait fourteen days
        for something that will still be refused is worse than telling them the truth.
        """
        if righe >= self.settings.solleciti_max_reminders:
            raise Conflict(
                ENTITY,
                "questa fattura ha già il numero massimo di solleciti "
                f"({self.settings.solleciti_max_reminders})",
                invoice_id=str(invoice_id),
                solleciti=righe,
            )
        giorni = self.settings.solleciti_min_interval_days
        if ultima_attivita is not None and datetime.now(UTC) - ultima_attivita < timedelta(
            days=giorni
        ):
            raise Conflict(
                ENTITY,
                f"un sollecito per questa fattura è già stato preparato negli ultimi "
                f"{giorni} giorni",
                invoice_id=str(invoice_id),
            )

    def _next_sequence(self, invoice_id: UUID) -> int:
        """The next free position in this invoice's reminder sequence.

        `max + 1` and not `count + 1`: a reminder deleted from the middle would otherwise
        hand the next caller a number that is already taken, and the constraint would
        refuse a request that is perfectly legitimate.

        This is a *read*, and the constraint is what actually arbitrates -- see
        `create_reminder`. Named rather than inlined so the concurrency test has a seam to
        put both threads on the same answer.
        """
        highest = self.session.execute(
            select(func.max(PaymentReminder.sequence)).where(
                PaymentReminder.invoice_id == invoice_id
            )
        ).scalar_one()
        return int(highest or 0) + 1

    @staticmethod
    def _numero(invoice: Invoice) -> str:
        """`{anno}/{numero}` -- the number printed on the document the client is holding.

        Through `invoices.naming`, never rebuilt here: the previous system recovered the fiscal
        progressive from a display title with a regex, which made the number on a legal
        document a derivative of a caption someone could rename.
        """
        if invoice.anno is None or invoice.numero is None:  # pragma: no cover - `emessa`
            raise Conflict(ENTITY, "si può sollecitare solo una fattura già emessa")
        return numero_completo(invoice.anno, invoice.numero)

    def _emitter_scope(self, actor: Actor, azienda_id: UUID) -> dict[str, Any]:
        """`{"emittente": {...}}`, the same scope every other template in this project
        renders against.

        Through `LegalEntityService.as_template_values` rather than by reading three
        columns here: slice 2 built that method for exactly this, and a second assembly of
        the issuer's identity is how the phone number in a letter starts disagreeing with
        the one on the invoice.

        A missing profile becomes a `Conflict` and not the underlying `NotFound`: an
        installation that has not filled the issuer in cannot sign a letter in anybody's
        name, and «compila il profilo» is an instruction while «emitter_profile singleton
        non trovato» is a puzzle.
        """
        try:
            return LegalEntityService(self.session).as_template_values(actor, azienda_id)
        except NotFound as missing:
            raise Conflict(
                ENTITY,
                "manca il profilo dell'emittente: compilalo prima di sollecitare",
            ) from missing

    def _iban(self, azienda_id: UUID) -> str:
        """Where the client is being asked to pay, from `fiscal_profile.iban`.

        Never a literal and never a placeholder: the previous system's `normalizeIban(iban) ||
        'IBAN_PAGAMENTO'` shipped the placeholder to the client whenever the value was
        missing, so the letter told somebody to transfer money to a string. Refusing is
        worse for the sender and far better for the recipient.

        `fiscal_profile` and not `emitter_profile`, because that is where slice 3 put it:
        `emitter_profile` holds the issuer's identity, `fiscal_profile` the numbers and
        codes, and the invoice's own XML already reads the IBAN from there.
        """
        profile = FiscalProfileRepository(self.session).get(azienda_id)
        iban = (profile.iban or "").strip() if profile is not None else ""
        if not iban:
            raise Conflict(
                ENTITY,
                "manca l'IBAN: compilalo nel profilo fiscale prima di sollecitare",
            )
        return iban

    def _recipients(self, customer: Customer) -> list[str]:
        """The customer's own address if it has one; otherwise every live person on it.

        An invoice reminder goes to whoever pays, and on a small company that is often a
        named person rather than a generic mailbox.
        """
        if customer.email:
            return [customer.email.strip().lower()]
        return self._addresses_of(customer)

    def _thread_of(self, invoice: Invoice, actor: Actor) -> UUID | None:
        """The covering email this invoice already went out on, if there is one."""
        account = self._mailbox_of(actor)
        if account is None:
            return None
        return self.repo.last_outbound_about(account.id, self._numero(invoice))

    def _query(self, grace_cutoff: date) -> Select[CandidateRow]:
        """The invoice/customer/reminder join, with conditions 1 and 2 in the `WHERE`.

        One aggregate over `payment_reminders` rather than N follow-up queries, and it is
        an `OUTER JOIN` on a grouped subquery so that an invoice with no reminders at all
        -- the common case, and the one the whole feature starts from -- still appears.
        """
        reminders = (
            select(
                PaymentReminder.invoice_id.label("invoice_id"),
                func.count().label("righe"),
                # Counted separately from `righe` because they answer different questions:
                # how many positions are occupied, and how many letters a client actually
                # received. See `PaymentReminder`'s own docstring.
                func.count(PaymentReminder.sent_at).label("inviati"),
                func.max(PaymentReminder.sent_at).label("ultimo"),
                func.max(func.coalesce(PaymentReminder.sent_at, PaymentReminder.created_at)).label(
                    "ultima_attivita"
                ),
            )
            .group_by(PaymentReminder.invoice_id)
            .subquery()
        )

        return (
            select(
                Invoice,
                Customer,
                func.coalesce(reminders.c.righe, 0),
                func.coalesce(reminders.c.inviati, 0),
                reminders.c.ultimo,
                reminders.c.ultima_attivita,
            )
            .join(Customer, Customer.id == Invoice.customer_id)
            .outerjoin(reminders, reminders.c.invoice_id == Invoice.id)
            .where(
                # Only an issued fiscal invoice. A `bozza` never left the building and a
                # proforma is not a document anybody owes against; `annullata` is the one
                # that would be actively harmful, because the issuer has withdrawn it.
                Invoice.tipo == CHASEABLE_TIPO,
                Invoice.stato == CHASEABLE_STATO,
                Invoice.deleted_at.is_(None),
                # 1. not collected
                Invoice.stato_pagamento != INCASSATO,
                # 2. overdue by more than the grace period -- chasing the day after the
                #    due date is aggressive and often wrong: the transfer has left.
                #    `data_scadenza` is nullable, and `NULL < date` is NULL rather than
                #    true, so this predicate already excludes an invoice with no agreed
                #    due date. Stated explicitly beside it anyway: it is the only thing
                #    that makes a reminder legitimate, and relying on three-valued logic
                #    to enforce the load-bearing condition is how it goes missing in a
                #    later edit.
                Invoice.data_scadenza.is_not(None),
                Invoice.data_scadenza < grace_cutoff,
                Customer.deleted_at.is_(None),
            )
            .order_by(Invoice.data_scadenza)
        )

    def _mailbox_of(self, actor: Actor) -> GoogleAccount | None:
        """The mailbox whose correspondence answers "did the client write back".

        The asker's own first, because a reply lives in the mailbox that received it and
        another user's inbox cannot answer this user's question. `any_account()` is the
        fallback for the two callers that have no user behind them -- the cron and a
        system actor -- and `None` is a supported answer: an installation with no Gmail
        connected still has invoices to chase, it simply carries no reply signal.
        """
        if actor.id is not None:
            mine = self.repo.account_for_user(actor.id)
            if mine is not None:
                return mine
        return self.repo.any_account()

    def _replied_on(
        self, account: GoogleAccount | None, invoice: Invoice, customer: Customer
    ) -> date | None:
        """When this client last wrote back about this invoice, or `None`.

        The signal the previous system could not have had: from the moment the CRM reads the mail,
        the list can say "the client replied on 12 August". It does not suppress the candidate
        -- a reply is not a payment, and sometimes the reply is exactly what needs chasing
        -- but it sorts last and it says so.

        The window opens at the invoice's own emission date, so a conversation from
        before the invoice existed cannot be mistaken for an answer to it.
        """
        if account is None:
            return None
        since = invoice.data_emissione or invoice.data_scadenza
        if since is None:  # pragma: no cover - the query's own WHERE excludes it
            return None
        reply = self.repo.last_inbound_from(
            account.id,
            self._addresses_of(customer),
            datetime.combine(since, datetime.min.time(), tzinfo=UTC),
        )
        return reply.internal_date.date() if reply is not None else None

    def _addresses_of(self, customer: Customer) -> list[str]:
        """The customer's own address plus every live person on it.

        Both, and not just the first: on a small company the person who answers is rarely
        the generic address on the customer row, so reading only `customers.email` would
        report "nobody replied" about a conversation that is already open.
        """
        addresses = [customer.email.lower()] if customer.email else []
        addresses.extend(
            address.lower()
            for address in self.session.execute(
                select(Person.email).where(
                    Person.customer_id == customer.id,
                    Person.email.is_not(None),
                    Person.deleted_at.is_(None),
                )
            )
            .scalars()
            .all()
            if address
        )
        return list(dict.fromkeys(addresses))


__all__ = ["SollecitiService"]
