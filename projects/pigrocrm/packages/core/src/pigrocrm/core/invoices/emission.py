"""Emission: the one irreversible creation in the product, and the two facts that follow it."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.errors import Conflict, ImmutableField, ValidationFailed
from pigrocrm.core.fiscal.regime import RegimeStrategy
from pigrocrm.core.fiscal.schemas import FiscalSnapshot
from pigrocrm.core.invoices.checks import (
    check_issuable,
    check_issue_date,
    check_positive_total,
    check_transmission_date,
)
from pigrocrm.core.invoices.constants import ENTITY, ZERO
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.fatturapa.checks import (
    check_document_text_exportable,
    check_party_exportable,
    check_recipient_identity,
    check_recipient_routing,
)
from pigrocrm.core.invoices.lines import computed_from_stored, persist_lines
from pigrocrm.core.invoices.models import Invoice, InvoiceLine
from pigrocrm.core.invoices.register import RegisterGaps
from pigrocrm.core.invoices.schemas import (
    CURRENCY,
    SDI_DOCUMENT_TYPE,
    SNAPSHOT_VERSION,
    InvoiceAnnul,
    InvoiceIssue,
    InvoiceRead,
    InvoiceSnapshot,
    InvoiceTransmitted,
)
from pigrocrm.core.invoices.snapshot import build_snapshot
from pigrocrm.core.invoices.totals import ComputedLine, build_recap, sum_totals
from pigrocrm.core.work_units.release import ANNULLED_INVOICE_REASON, release_days_of_invoice


class InvoiceEmission:
    def __init__(self, ctx: InvoiceContext, register: RegisterGaps) -> None:
        self.ctx = ctx
        self.register = register

    def issue(self, invoice_id: UUID, data: InvoiceIssue, actor: Actor) -> InvoiceRead:
        """Consume a register number. **One transaction, in this exact order.**

        `invoice_id` names either a `draft` **fattura**, issued in place, or a
        `confirmed` **proforma**, in which case a *new* `issued` row is created with
        the proforma's lines copied and `origin_proforma_id` pointing back at it, and
        the proforma is marked `consumed` (spec 5). One method, because emitting from
        scratch and from a proforma share the lock, the validations, the freezing and
        the numbering, and splitting them would mean two paths to keep aligned on
        exactly the part that must not diverge.

        Ordering, and why each step is where it is:

        1. resolve the source row and the issue date, and check the date against
           "not in the future, not before 1 January of the current year". No lock yet:
           these are pure checks on the caller's own input;
        2. `lock_counter(azienda_id, anno)` -- the **first** row lock this transaction
           takes -- then the invoice row's own lock, and only then the authoritative
           state check;
        3. every fiscal validation, the totals, and the chronological-monotonicity
           check. All of it after the lock, so "the date of the previous number" is a
           safe thing to read, and all of it *before* the counter is touched, so a
           refusal never even reaches the increment;
        4. increment the counter, write the row with `(anno, numero)`, write the
           frozen `snapshot`, write the activity;
        5. `COMMIT`.

        **The number does not exist before the commit.** If any step fails, the
        rollback returns `last_number` to its previous value and nothing was
        consumed -- the property a `SEQUENCE` does not have.

        The PDF and the XML are produced **after** this commit, in a second
        transaction, from the snapshot (`InvoiceService.issue_and_render`). Holding a
        row lock for the duration of a Typst subprocess would serialise every emission
        on PDF compile time, and an invoice is a legal fact independent of its printout:
        if the render fails, the invoice exists with its number and its artefacts
        regenerate deterministically. That is the one documented exception to "one
        service method = one transaction", and spec 3 mandates it.

        Two concurrent `issue()` calls on the same `invoice_id` are **in** scope: the
        loser would consume a register number and then overwrite the winner's number on
        the very same row, leaving the first number owned by no invoice.
        `uq_invoices_legal_entity_year_number` cannot see it, because the row simply carries a
        different number. That is a permanent gap in the register. The state is checked
        twice: once before the counter lock, to refuse an obviously-doomed request
        without serialising on it, and once *after* the counter and the row lock, which
        is the authoritative one. `lock_counter` blocks until the other emission's
        transaction ends, so by then the competing commit is visible and the re-read sees
        `issued`.

        Every other writer of the row (`update`, `replace_lines`, `confirm_proforma`,
        `annul`, ...) takes the same row lock before it reads the state, and takes no
        counter lock, so the order stays counter-then-row for everybody and the deadlock
        argument in `lock_counter`'s own docstring is unchanged.
        """
        actor.require_admin("issue_invoice")
        ctx = self.ctx
        today = today_local()
        source = ctx.require(invoice_id)
        ctx.writing_legal_entity(source.legal_entity_id)
        issue_date = data.issue_date or today
        year = issue_date.year
        check_issue_date(issue_date, today)
        from_proforma = source.type == "proforma"
        check_issuable(source, from_proforma=from_proforma)

        # Step 2. From here on, every other emission of this azienda for this year
        # waits; another azienda's register has a lock of its own (REB-619).
        counter = ctx.repo.lock_counter(source.legal_entity_id, year)
        # Native issuing must not resume on top of a silent gap in an imported register.
        # Checked inside the lock that protects the increment below, so a concurrent
        # import cannot close the gap and let this call through on a stale read.
        self.register.refuse_undeclared_gaps(year, source.legal_entity_id)
        # And only now is the state answer trustworthy. The check above ran against a read
        # taken before any lock: a competing `issue()` on this same row could have been
        # between its own check and its own commit. Acquiring the counter lock orders the
        # two transactions, and the row lock re-reads whatever that emission did.
        source = ctx.require_locked(invoice_id)
        check_issuable(source, from_proforma=from_proforma)

        # Step 3. Validations and totals, after the locks and before the increment.
        strategy, profile = ctx.regime(source.legal_entity_id)
        snapshot = build_snapshot(ctx, source.customer_id, profile, actor, source.legal_entity_id)
        abroad = ctx.issues_abroad(profile)
        # The SdI's own rules on the two parties, and below on the text: what the XML
        # writer would refuse after the number is spent. A foreign azienda issues no XML
        # (REB-619), so none of them applies to its document.
        if not abroad:
            check_party_exportable(snapshot.legal_entity, "legal_entities")
            check_party_exportable(snapshot.customer, "customer")
            check_recipient_routing(snapshot.customer)
            check_recipient_identity(snapshot.customer)
        lines = self._issuable_lines(source, snapshot, profile, strategy, abroad=abroad)
        computed = computed_from_stored(lines)
        recap = build_recap(computed)
        taxable_amount, vat_amount, total = sum_totals(recap)
        check_positive_total(total)
        self._check_monotonic(source, year, issue_date)

        # Step 4. Increment, write, freeze. One SAVEPOINT: a unique-index hit (see
        # `concurrent_number`) undoes the increment and the writes, not the caller's work.
        number = counter.last_number + 1

        def concurrent_number(exc: IntegrityError) -> Conflict:
            # `uq_invoices_legal_entity_year_number` is the net under the row lock, not the
            # mechanism (spec 3). Reaching it means something wrote a number without
            # taking the lock -- an importer, a direct INSERT, a second service -- and
            # this is what makes that failure observable instead of a silent duplicate.
            return Conflict(
                ENTITY,
                "un altro processo ha scritto lo stesso numero senza passare dal "
                "contatore: riprova e verifica il registro",
                year=year,
                number=number,
            )

        with savepoint(ctx.session, concurrent_number):
            counter.last_number = number
            target = source
            if from_proforma:
                target = self._invoice_from_proforma(source, computed)
                source.status = "consumed"
            target.status = "issued"
            target.year = year
            target.number = number
            target.issue_date = issue_date
            # A date written on the draft (or on the proforma this fattura is born from)
            # is what the person meant; otherwise the customer's own terms, the profile's
            # days as the fallback (REB-326). Read here, at emission, and never again.
            target.due_date = source.due_date or ctx.due_date_from_terms(
                issue_date, source.customer_id, profile.due_days
            )
            target.taxable_amount = taxable_amount
            target.vat_amount = vat_amount
            target.total = total
            target.stamp_duty = strategy.stamp_duty(recap, profile)
            target.snapshot = snapshot.model_dump(mode="json")
            target.snapshot_version = SNAPSHOT_VERSION
            ctx.activities.record(
                ENTITY,
                target.id,
                "issued",
                actor,
                {
                    "year": year,
                    "number": target.number,
                    "total": str(target.total),
                    "origin_proforma_id": str(source.id) if from_proforma else None,
                },
            )
        ctx.session.commit()
        # The artefacts belong to the caller's second transaction, not to this method:
        # when `issue` rendered them itself it stopped being one transaction, and the
        # artefact commit survived a test fixture's rollback (see `issue_and_render`).
        return ctx.read(target)

    def _issuable_lines(
        self,
        source: Invoice,
        snapshot: InvoiceSnapshot,
        profile: FiscalSnapshot,
        strategy: RegimeStrategy,
        *,
        abroad: bool,
    ) -> list[InvoiceLine]:
        """The stored lines, once they are known to be what the fiscal regime and the
        document's text allow; every refusal comes before the counter moves."""
        lines = self.ctx.repo.lines(source.id)
        if not lines:
            raise ValidationFailed(
                ENTITY,
                "lines",
                "una fattura senza righe non si emette",
                expected="almeno una riga",
            )
        # The stored lines were computed when they were written, against the customer and
        # the profile of that moment, and `issue` copies them rather than recomputing them
        # because the amounts the customer agreed to must not move here. The `Natura`
        # pair is different: it is a statement about the customer's country and the
        # issuer's regime as they are *now*, and a draft or a proforma written before the
        # customer's country was corrected (ORB-32) would otherwise consume a register
        # number with the wrong one. So the regime is asked again and any disagreement
        # refuses; `replace_lines` is the remedy and the message says so. `import_issued`
        # is exempt by construction: its lines are declared, not computed.
        for r in lines:
            _, vat_nature_pending, reference_expected = strategy.resolve_line_vat(
                r.vat_rate, profile, country_customer=snapshot.customer.country
            )
            if (r.vat_nature, r.legal_reference) != (vat_nature_pending, reference_expected):
                raise ValidationFailed(
                    ENTITY,
                    "lines",
                    f"la riga {r.line_number} porta natura {r.vat_nature} e un riferimento "
                    "normativo che non corrispondono al cliente e al profilo fiscale "
                    "attuali: sostituisci le righe prima di emettere",
                    expected=f"natura {vat_nature_pending} con il riferimento {reference_expected!r}",
                )
        # The document's own text, by the writer's rules, before the counter moves: an em
        # dash in a causale used to spend a number whose XML was then refused on every
        # export (ORB-140). What the check accepts here is what `export_xml` serialises.
        if not abroad:
            check_document_text_exportable(
                source.memo,
                lines,
                purchase_order_number=source.purchase_order_number,
                purchase_order_tender_code=source.purchase_order_tender_code,
                purchase_order_project_code=source.purchase_order_project_code,
            )
        return lines

    def _check_monotonic(self, source: Invoice, year: int, issue_date: date) -> None:
        previous = self.ctx.repo.last_issued_date(source.legal_entity_id, year)
        if previous is not None and issue_date < previous:
            raise ValidationFailed(
                ENTITY,
                "issue_date",
                "il registro deve restare cronologicamente monotono rispetto al numero: "
                f"l'ultima fattura del {year} porta la data {previous.isoformat()}",
                expected=f"una data dal {previous.isoformat()} in poi",
            )

    def _invoice_from_proforma(self, source: Invoice, computed: Sequence[ComputedLine]) -> Invoice:
        """The `draft` fattura a confirmed proforma becomes, with the proforma's lines."""
        target = self.ctx.repo.add(
            Invoice(
                customer_id=source.customer_id,
                deal_id=source.deal_id,
                legal_entity_id=source.legal_entity_id,
                type="invoice",
                status="draft",
                sdi_document_type=SDI_DOCUMENT_TYPE,
                currency=CURRENCY,
                memo=source.memo,
                # The period is a fact about the work and travels with it. The date does
                # not: the proforma's `issue_date` is the sender's (ORB-63), and the
                # fattura takes the register's.
                accrual_from=source.accrual_from,
                accrual_to=source.accrual_to,
                # The order is the customer's and the fattura answers it, so it travels
                # with the work like the period (REB-693).
                purchase_order_number=source.purchase_order_number,
                purchase_order_date=source.purchase_order_date,
                purchase_order_tender_code=source.purchase_order_tender_code,
                purchase_order_project_code=source.purchase_order_project_code,
                note_interne=source.note_interne,
                taxable_amount=ZERO,
                vat_amount=ZERO,
                stamp_duty=ZERO,
                total=ZERO,
                payment_status="unpaid",
                origin_proforma_id=source.id,
                custom_fields=dict(source.custom_fields),
            )
        )
        persist_lines(self.ctx.repo, target, computed)
        return target

    def annul(self, invoice_id: UUID, data: InvoiceAnnul, actor: Actor) -> InvoiceRead:
        """Strike the page through; keep the number.

        The number **stays consumed** and the row stays readable: that is what
        preserves the gap-free property of spec 3, which would otherwise be worth
        nothing -- a number that can disappear is a gap with extra steps.

        Refused once the file has been handed to the intermediary. From that point the
        correction requires a credit note (`TD04`), which this slice does not produce
        for a structural reason rather than as a deferral: a credit note corrects an
        invoice **already accepted by the SdI**, and nothing here is transmitted. So
        the application says where the correction has to happen, instead of offering a
        button that pretends to solve it.

        The row is locked before its state is read: an `annul` and a
        `mark_transmitted_externally` racing on the same invoice would otherwise both
        pass their checks and leave it both `cancelled` and delivered.
        """
        actor.require_admin("annul_invoice")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.status != "issued":
            raise Conflict(
                ENTITY,
                "si annulla solo una fattura emessa: una bozza si elimina, "
                "una fattura gia' annullata non si annulla due volte",
                status_current=invoice.status,
            )
        if invoice.transmitted_externally_at is not None:
            raise Conflict(
                ENTITY,
                "la fattura e' stata consegnata all'intermediario il "
                f"{invoice.transmitted_externally_at.isoformat()}: da questo punto la "
                "correzione richiede una nota di credito, che PigroCRM non emette. "
                "Va fatta dal tuo intermediario o dal portale dell'Agenzia delle Entrate.",
                transmitted_externally_at=invoice.transmitted_externally_at.isoformat(),
            )
        invoice.status = "cancelled"
        # `today_local()`, never a bare `date.today()`: the date printed on an
        # annulment record is subject to the same "issuer's own calendar" requirement as
        # `issue_date` (see `db/clock.py`).
        invoice.cancelled_at = today_local()
        invoice.cancellation_reason = data.reason
        ctx.activities.record(
            ENTITY,
            invoice.id,
            "annulled",
            actor,
            {"number": invoice.number, "year": invoice.year, "reason": data.reason},
        )
        # The days billed on it are billable again, as the hours are: an annulled invoice
        # keeps its number but not its revenue (REB-751).
        release_days_of_invoice(ctx.session, invoice.id, actor, ANNULLED_INVOICE_REASON)
        ctx.session.commit()
        return ctx.read(invoice)

    def mark_transmitted_externally(
        self, invoice_id: UUID, data: InvoiceTransmitted, actor: Actor
    ) -> InvoiceRead:
        """Record that the XML has been handed to the intermediary. Settable **once**.

        This is the column that makes annulment safe rather than optimistic: without
        it the system could not distinguish an invoice that never left -- annullable --
        from one already deposited with the Agenzia delle Entrate, and would treat the
        two the same. Freezing it after the first write is what stops that distinction
        from being editable away.
        """
        actor.require_admin("mark_transmitted_externally")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.status != "issued":
            raise Conflict(
                ENTITY,
                "solo una fattura emessa si consegna a un intermediario",
                status_current=invoice.status,
            )
        if invoice.transmitted_externally_at is not None:
            raise ImmutableField(
                ENTITY,
                "transmitted_externally_at",
                "la consegna si registra una volta sola: e' il fatto su cui si decide "
                "se un annullamento e' ancora possibile",
            )
        check_transmission_date(data.data, invoice.issue_date, today_local())
        invoice.transmitted_externally_at = data.data
        ctx.activities.record(
            ENTITY, invoice.id, "transmitted_externally", actor, {"data": data.data.isoformat()}
        )
        ctx.session.commit()
        return ctx.read(invoice)
