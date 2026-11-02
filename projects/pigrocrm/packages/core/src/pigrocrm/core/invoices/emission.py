"""Emission: the one irreversible creation in the product, and the two facts that follow it."""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
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
    DIVISA,
    SNAPSHOT_VERSIONE,
    TIPO_DOCUMENTO,
    InvoiceAnnul,
    InvoiceIssue,
    InvoiceRead,
    InvoiceSnapshot,
    InvoiceTransmitted,
)
from pigrocrm.core.invoices.snapshot import build_snapshot
from pigrocrm.core.invoices.totals import ComputedLine, build_riepilogo, sum_totals
from pigrocrm.core.work_units.release import ANNULLED_INVOICE_REASON, release_days_of_invoice


class InvoiceEmission:
    def __init__(self, ctx: InvoiceContext, register: RegisterGaps) -> None:
        self.ctx = ctx
        self.register = register

    def issue(self, invoice_id: UUID, data: InvoiceIssue, actor: Actor) -> InvoiceRead:
        """Consume a register number. **One transaction, in this exact order.**

        `invoice_id` names either a `bozza` **fattura**, issued in place, or a
        `confermata` **proforma**, in which case a *new* `emessa` row is created with
        the proforma's lines copied and `origine_proforma_id` pointing back at it, and
        the proforma is marked `consumata` (spec 5). One method, because emitting from
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
        rollback returns `ultimo_numero` to its previous value and nothing was
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
        `uq_invoices_azienda_anno_numero` cannot see it, because the row simply carries a
        different number. That is a permanent gap in the register. The state is checked
        twice: once before the counter lock, to refuse an obviously-doomed request
        without serialising on it, and once *after* the counter and the row lock, which
        is the authoritative one. `lock_counter` blocks until the other emission's
        transaction ends, so by then the competing commit is visible and the re-read sees
        `emessa`.

        Every other writer of the row (`update`, `replace_lines`, `confirm_proforma`,
        `annul`, ...) takes the same row lock before it reads the state, and takes no
        counter lock, so the order stays counter-then-row for everybody and the deadlock
        argument in `lock_counter`'s own docstring is unchanged.
        """
        actor.require_admin("issue_invoice")
        ctx = self.ctx
        today = oggi_in_italia()
        source = ctx.require(invoice_id)
        ctx.writing_azienda(source.azienda_id)
        data_emissione = data.data_emissione or today
        anno = data_emissione.year
        check_issue_date(data_emissione, today)
        from_proforma = source.tipo == "proforma"
        check_issuable(source, from_proforma=from_proforma)

        # Step 2. From here on, every other emission of this azienda for this year
        # waits; another azienda's register has a lock of its own (REB-619).
        counter = ctx.repo.lock_counter(source.azienda_id, anno)
        # Native issuing must not resume on top of a silent gap in an imported register.
        # Checked inside the lock that protects the increment below, so a concurrent
        # import cannot close the gap and let this call through on a stale read.
        self.register.refuse_undeclared_gaps(anno, source.azienda_id)
        # And only now is the state answer trustworthy. The check above ran against a read
        # taken before any lock: a competing `issue()` on this same row could have been
        # between its own check and its own commit. Acquiring the counter lock orders the
        # two transactions, and the row lock re-reads whatever that emission did.
        source = ctx.require_locked(invoice_id)
        check_issuable(source, from_proforma=from_proforma)

        # Step 3. Validations and totals, after the locks and before the increment.
        strategy, profile = ctx.regime(source.azienda_id)
        snapshot = build_snapshot(ctx, source.customer_id, profile, actor, source.azienda_id)
        abroad = ctx.issues_abroad(profile)
        # The SdI's own rules on the two parties, and below on the text: what the XML
        # writer would refuse after the number is spent. A foreign azienda issues no XML
        # (REB-619), so none of them applies to its document.
        if not abroad:
            check_party_exportable(snapshot.emittente, "emitter_profile")
            check_party_exportable(snapshot.cliente, "customer")
            check_recipient_routing(snapshot.cliente)
            check_recipient_identity(snapshot.cliente)
        righe = self._issuable_lines(source, snapshot, profile, strategy, abroad=abroad)
        computed = computed_from_stored(righe)
        riepilogo = build_riepilogo(computed)
        imponibile, imposta, totale = sum_totals(riepilogo)
        check_positive_total(totale)
        self._check_monotonic(source, anno, data_emissione)

        # Step 4. Increment, write, freeze. One SAVEPOINT: a unique-index hit (see
        # `concurrent_number`) undoes the increment and the writes, not the caller's work.
        numero = counter.ultimo_numero + 1

        def concurrent_number(exc: IntegrityError) -> Conflict:
            # `uq_invoices_azienda_anno_numero` is the net under the row lock, not the
            # mechanism (spec 3). Reaching it means something wrote a number without
            # taking the lock -- an importer, a direct INSERT, a second service -- and
            # this is what makes that failure observable instead of a silent duplicate.
            return Conflict(
                ENTITY,
                "un altro processo ha scritto lo stesso numero senza passare dal "
                "contatore: riprova e verifica il registro",
                anno=anno,
                numero=numero,
            )

        with savepoint(ctx.session, concurrent_number):
            counter.ultimo_numero = numero
            target = source
            if from_proforma:
                target = self._fattura_from_proforma(source, computed)
                source.stato = "consumata"
            target.stato = "emessa"
            target.anno = anno
            target.numero = numero
            target.data_emissione = data_emissione
            # A date written on the draft (or on the proforma this fattura is born from)
            # is what the person meant; otherwise the customer's own terms, the profile's
            # days as the fallback (REB-326). Read here, at emission, and never again.
            target.data_scadenza = source.data_scadenza or ctx.due_date_from_terms(
                data_emissione, source.customer_id, profile.giorni_scadenza
            )
            target.imponibile = imponibile
            target.imposta = imposta
            target.totale = totale
            target.bollo = strategy.bollo(riepilogo, profile)
            target.snapshot = snapshot.model_dump(mode="json")
            target.snapshot_versione = SNAPSHOT_VERSIONE
            ctx.activities.record(
                ENTITY,
                target.id,
                "issued",
                actor,
                {
                    "anno": anno,
                    "numero": target.numero,
                    "totale": str(target.totale),
                    "origine_proforma_id": str(source.id) if from_proforma else None,
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
        righe = self.ctx.repo.lines(source.id)
        if not righe:
            raise ValidationFailed(
                ENTITY,
                "righe",
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
        for r in righe:
            _, natura_attesa, riferimento_atteso = strategy.resolve_line_vat(
                r.aliquota_iva, profile, nazione_cliente=snapshot.cliente.nazione
            )
            if (r.natura, r.riferimento_normativo) != (natura_attesa, riferimento_atteso):
                raise ValidationFailed(
                    ENTITY,
                    "righe",
                    f"la riga {r.numero_linea} porta natura {r.natura} e un riferimento "
                    "normativo che non corrispondono al cliente e al profilo fiscale "
                    "attuali: sostituisci le righe prima di emettere",
                    expected=f"natura {natura_attesa} con il riferimento {riferimento_atteso!r}",
                )
        # The document's own text, by the writer's rules, before the counter moves: an em
        # dash in a causale used to spend a number whose XML was then refused on every
        # export (ORB-140). What the check accepts here is what `export_xml` serialises.
        if not abroad:
            check_document_text_exportable(
                source.causale,
                righe,
                ordine_numero=source.ordine_numero,
                ordine_cig=source.ordine_cig,
                ordine_cup=source.ordine_cup,
            )
        return righe

    def _check_monotonic(self, source: Invoice, anno: int, data_emissione: date) -> None:
        previous = self.ctx.repo.last_issued_date(source.azienda_id, anno)
        if previous is not None and data_emissione < previous:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "il registro deve restare cronologicamente monotono rispetto al numero: "
                f"l'ultima fattura del {anno} porta la data {previous.isoformat()}",
                expected=f"una data dal {previous.isoformat()} in poi",
            )

    def _fattura_from_proforma(self, source: Invoice, computed: Sequence[ComputedLine]) -> Invoice:
        """The `bozza` fattura a confirmed proforma becomes, with the proforma's lines."""
        target = self.ctx.repo.add(
            Invoice(
                customer_id=source.customer_id,
                deal_id=source.deal_id,
                azienda_id=source.azienda_id,
                tipo="fattura",
                stato="bozza",
                tipo_documento=TIPO_DOCUMENTO,
                divisa=DIVISA,
                causale=source.causale,
                # The period is a fact about the work and travels with it. The date does
                # not: the proforma's `data_emissione` is the sender's (ORB-63), and the
                # fattura takes the register's.
                competenza_da=source.competenza_da,
                competenza_a=source.competenza_a,
                # The order is the customer's and the fattura answers it, so it travels
                # with the work like the period (REB-693).
                ordine_numero=source.ordine_numero,
                ordine_data=source.ordine_data,
                ordine_cig=source.ordine_cig,
                ordine_cup=source.ordine_cup,
                note_interne=source.note_interne,
                imponibile=ZERO,
                imposta=ZERO,
                bollo=ZERO,
                totale=ZERO,
                stato_pagamento="da_incassare",
                origine_proforma_id=source.id,
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
        pass their checks and leave it both `annullata` and delivered.
        """
        actor.require_admin("annul_invoice")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "si annulla solo una fattura emessa: una bozza si elimina, "
                "una fattura gia' annullata non si annulla due volte",
                stato_attuale=invoice.stato,
            )
        if invoice.trasmessa_esternamente_il is not None:
            raise Conflict(
                ENTITY,
                "la fattura e' stata consegnata all'intermediario il "
                f"{invoice.trasmessa_esternamente_il.isoformat()}: da questo punto la "
                "correzione richiede una nota di credito, che PigroCRM non emette. "
                "Va fatta dal tuo intermediario o dal portale dell'Agenzia delle Entrate.",
                trasmessa_esternamente_il=invoice.trasmessa_esternamente_il.isoformat(),
            )
        invoice.stato = "annullata"
        # `oggi_in_italia()`, never a bare `date.today()`: the date printed on an
        # annulment record is subject to the same "issuer's own calendar" requirement as
        # `data_emissione` (see `clock.py`).
        invoice.annullata_il = oggi_in_italia()
        invoice.motivo_annullamento = data.motivo
        ctx.activities.record(
            ENTITY,
            invoice.id,
            "annulled",
            actor,
            {"numero": invoice.numero, "anno": invoice.anno, "motivo": data.motivo},
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
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "solo una fattura emessa si consegna a un intermediario",
                stato_attuale=invoice.stato,
            )
        if invoice.trasmessa_esternamente_il is not None:
            raise ImmutableField(
                ENTITY,
                "trasmessa_esternamente_il",
                "la consegna si registra una volta sola: e' il fatto su cui si decide "
                "se un annullamento e' ancora possibile",
            )
        check_transmission_date(data.data, invoice.data_emissione, oggi_in_italia())
        invoice.trasmessa_esternamente_il = data.data
        ctx.activities.record(
            ENTITY, invoice.id, "transmitted_externally", actor, {"data": data.data.isoformat()}
        )
        ctx.session.commit()
        return ctx.read(invoice)
