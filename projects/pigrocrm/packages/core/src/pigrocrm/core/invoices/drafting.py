"""Drafts and proformas: everything that happens to an invoice before a number exists.

A draft and a proforma are ordinary mutable rows; the number and the freezing belong to
`InvoiceEmission`, the artefacts to `InvoiceArtifacts`. Every writer here that decides on
the row's state locks the row first (`InvoiceContext.require_locked`).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.clock import oggi_in_italia
from pigrocrm.core.db.transactions import savepoint
from pigrocrm.core.documents.context import ENTITY as DOCUMENT_ENTITY
from pigrocrm.core.errors import Conflict, ImmutableField, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.invoices.checks import (
    MUTABLE_AFTER_ISSUE,
    check_competenza,
    is_editable,
    require_editable,
)
from pigrocrm.core.invoices.constants import ENTITY, ZERO
from pigrocrm.core.invoices.context import InvoiceContext
from pigrocrm.core.invoices.lines import apply_totals, compute_lines, persist_lines
from pigrocrm.core.invoices.models import Invoice
from pigrocrm.core.invoices.naming import proforma_riferimento
from pigrocrm.core.invoices.ordine import ORDINE_FIELDS, checked_ordine
from pigrocrm.core.invoices.schemas import (
    DIVISA,
    TIPO_DOCUMENTO,
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceRead,
    InvoiceUpdate,
    PaymentState,
)
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.work_units.release import (
    DELETED_DRAFT_REASON,
    count_days_billed_on,
    release_days_of_invoice,
)


class InvoiceDrafting:
    def __init__(self, ctx: InvoiceContext) -> None:
        self.ctx = ctx

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.ctx.fields.specs_for(ENTITY), values)

    def create(
        self,
        data: InvoiceCreate,
        actor: Actor,
        *,
        azienda_id: UUID | None = None,
        commit: bool = True,
    ) -> InvoiceRead:
        """A draft or a proforma. Neither has a number, which is why a failed creation
        cannot burn one -- not as a matter of care, but because there is nothing to
        burn until `issue` runs.

        `azienda_id` names the azienda the draft is issued under when it is not its
        parent's: a contract's days are billed under the contract's own azienda, which a
        customer moved since no longer shares. In-process only, never on `InvoiceCreate`,
        so the REST surface keeps taking its azienda from the customer or the deal.

        `commit=False` only flushes, for a caller that composes the draft with its own
        writes in one transaction (`assembly.assemble_draft`)."""
        actor.require_write("create_invoice")
        ctx = self.ctx
        customer = ctx.check_owner(data.customer_id, data.deal_id)
        azienda_id = (
            ctx.emitter.inherited(azienda_id, ENTITY)
            if azienda_id is not None
            else ctx.azienda_of_parent(customer, data.deal_id)
        )
        strategy, profile = ctx.regime(azienda_id)
        check_competenza(data.competenza_da, data.competenza_a)
        ordine = checked_ordine(
            data.ordine_numero, data.ordine_data, data.ordine_cig, data.ordine_cup
        )
        if data.tipo != "proforma" and data.data_emissione is not None:
            raise ValidationFailed(
                ENTITY,
                "data_emissione",
                "una bozza di fattura non ha data: la prende all'emissione, "
                "quando entra nel registro",
                expected="nessuna data, oppure tipo = 'proforma'",
            )

        invoice = Invoice(
            customer_id=data.customer_id,
            deal_id=data.deal_id,
            azienda_id=azienda_id,
            tipo=data.tipo,
            stato="bozza",
            tipo_documento=TIPO_DOCUMENTO,
            divisa=DIVISA,
            causale=data.causale,
            competenza_da=data.competenza_da,
            competenza_a=data.competenza_a,
            **ordine.columns(),
            note_interne=data.note_interne,
            imponibile=ZERO,
            imposta=ZERO,
            bollo=ZERO,
            totale=ZERO,
            stato_pagamento="da_incassare",
            custom_fields=self._validated_custom(data.custom_fields or {}),
        )
        if data.tipo == "proforma":
            # `oggi_in_italia()`, not `date.today()`: see `clock.py`'s module docstring
            # (this project's `Dockerfile.api` pins no `TZ`, so a bare `date.today()` is
            # the UTC date). Low-stakes here -- `riferimento` is a non-fiscal, internal
            # identifier -- but there is no reason to let the wrong clock answer at all.
            today = oggi_in_italia()
            invoice.riferimento = proforma_riferimento(
                today.year, ctx.repo.next_proforma_sequence()
            )
            # A proforma's own document date (ORB-63): the sender's, stored here so the PDF
            # stops printing the render day. Not a register date -- none of
            # `check_issue_date`'s limits apply, and the fattura born from this row takes
            # its own date at `issue` -- which is why any date is accepted.
            invoice.data_emissione = data.data_emissione or today
        computed = compute_lines(data.righe, profile, strategy, nazione_cliente=customer.nazione)
        apply_totals(invoice, computed, profile, strategy)
        ctx.repo.add(invoice)
        persist_lines(ctx.repo, invoice, computed)
        ctx.activities.record(
            ENTITY,
            invoice.id,
            "created",
            actor,
            {"tipo": invoice.tipo, "righe": len(computed), "totale": str(invoice.totale)},
        )
        if commit:
            ctx.session.commit()
        else:
            ctx.session.flush()
        return ctx.read(invoice)

    def update(self, invoice_id: UUID, data: InvoiceUpdate, actor: Actor) -> InvoiceRead:
        actor.require_write("update_invoice")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Invoice, changes)
        frozen = [key for key in changes if key not in MUTABLE_AFTER_ISSUE]
        if frozen and not is_editable(invoice):
            raise ImmutableField(
                ENTITY,
                sorted(frozen)[0],
                f"campo congelato su un documento in stato '{invoice.stato}'",
            )
        if "data_emissione" in changes:
            # Editable only on a proforma, and only ever to another date: a fattura's date
            # is the register's and `issue` assigns it (spec 6.2), and a proforma without
            # a date would put the render day back on its PDF (ORB-63).
            if invoice.tipo != "proforma":
                raise ValidationFailed(
                    ENTITY,
                    "data_emissione",
                    "la data di una fattura la assegna l'emissione, non una modifica",
                    expected="nessuna data su una bozza di fattura",
                )
            if changes["data_emissione"] is None:
                raise ValidationFailed(
                    ENTITY,
                    "data_emissione",
                    "una proforma porta sempre una data: si sposta, non si toglie",
                    expected="una data",
                )
        if "competenza_da" in changes or "competenza_a" in changes:
            check_competenza(
                changes.get("competenza_da", invoice.competenza_da),
                changes.get("competenza_a", invoice.competenza_a),
            )
        if any(key in changes for key in ORDINE_FIELDS):
            # On the merged row, like the period: a CIG added to a document whose number is
            # stored is one field, and a number cleared while the CIG stays is refused
            # before anything is written (REB-693).
            ordine = checked_ordine(
                changes.get("ordine_numero", invoice.ordine_numero),
                changes.get("ordine_data", invoice.ordine_data),
                changes.get("ordine_cig", invoice.ordine_cig),
                changes.get("ordine_cup", invoice.ordine_cup),
            )
            changes.update(
                {key: value for key, value in ordine.columns().items() if key in changes}
            )
        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY,
                ctx.fields.specs_for(ENTITY),
                invoice.custom_fields,
                data.custom_fields,
            )
        for key, value in changes.items():
            setattr(invoice, key, value)
        ctx.activities.record(ENTITY, invoice.id, "updated", actor, {"changed": sorted(changes)})
        ctx.session.commit()
        return ctx.read(invoice)

    def replace_lines(
        self, invoice_id: UUID, righe: list[InvoiceLineIn], actor: Actor
    ) -> InvoiceRead:
        """The whole list, never a partial patch.

        It is the natural shape of a line editor, and a line's totals are derived from
        the whole list, so patching one line in place would leave the invoice's totals to
        be reconciled separately.
        """
        actor.require_write("replace_invoice_lines")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        require_editable(invoice, "righe")
        billed = count_days_billed_on(ctx.session, invoice.id)
        if billed:
            raise Conflict(
                ENTITY,
                f"questa bozza porta {billed} giornate fatturate: per cambiare le righe "
                "elimina la bozza e rifai la fattura dal contratto",
                giornate=billed,
            )
        customer = ctx.check_owner(invoice.customer_id, None)
        strategy, profile = ctx.regime(invoice.azienda_id)
        computed = compute_lines(righe, profile, strategy, nazione_cliente=customer.nazione)
        apply_totals(invoice, computed, profile, strategy)
        persist_lines(ctx.repo, invoice, computed)
        ctx.activities.record(
            ENTITY,
            invoice.id,
            "lines_replaced",
            actor,
            {"righe": len(computed), "totale": str(invoice.totale)},
        )
        ctx.session.commit()
        return ctx.read(invoice)

    def confirm_proforma(self, invoice_id: UUID, actor: Actor) -> InvoiceRead:
        """`bozza` -> `confermata` on a proforma: the amount is agreed and the document
        is ready to be sent, still without touching the register."""
        actor.require_write("confirm_proforma")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.tipo != "proforma":
            raise Conflict(
                ENTITY, "solo una proforma si conferma", tipo=invoice.tipo, stato=invoice.stato
            )
        if invoice.stato != "bozza":
            raise Conflict(
                ENTITY,
                f"da '{invoice.stato}' non si puo' passare a 'confermata'",
                stato_attuale=invoice.stato,
            )
        if not ctx.repo.lines(invoice.id):
            raise ValidationFailed(
                ENTITY,
                "righe",
                "una proforma senza righe non si conferma",
                expected="almeno una riga",
            )
        invoice.stato = "confermata"
        ctx.activities.record(ENTITY, invoice.id, "confirmed", actor)
        ctx.session.commit()
        return ctx.read(invoice)

    def soft_delete(self, invoice_id: UUID, actor: Actor) -> None:
        """Only what never consumed a number, and never a `consumata` proforma.

        Checked here **and** by `ck_invoices_no_delete_once_consumed`, which is what
        makes the rule true for a psql session too. Without the gap-free register the
        numbering guarantee of spec 3 would be worth nothing: a number that can be
        deleted is a gap with extra steps. The row lock taken first is what makes the
        check here conclusive: an emission that arrives meanwhile waits for it, then
        finds a deleted row.

        The artefact rows go with the invoice (ORB-41). A proforma's PDF is filed among
        the customer's documents by `produce_artifacts`, and `DocumentRepository.list`
        filters on `documents.deleted_at` alone, so until this archived it too the PDF
        stayed listed and downloadable while `get` on its owner answered not found. Done
        here at repository level rather than through `DocumentService.soft_delete`: that
        method commits on its own, and the two rows must fall in one transaction or a
        crash in between leaves exactly the orphan this fixes; and the permission that
        governs is the invoice's, already checked above. A soft delete like the invoice's
        own: the row and the bytes stay, so a restore of the document is still a real
        restore. The table CHECK above guarantees this never reaches a fiscal artefact,
        since a numbered invoice cannot get this far.
        """
        actor.require_write("delete_invoice")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.numero is not None or invoice.stato == "consumata":
            raise Conflict(
                ENTITY,
                "un documento che ha consumato un numero non si elimina: "
                "si annulla, conservando il numero",
                stato=invoice.stato,
                numero=invoice.numero,
            )
        now = datetime.now(UTC)

        def issued_meanwhile(exc: IntegrityError) -> Conflict:
            return Conflict(
                ENTITY, "il documento e' stato emesso nel frattempo e non si elimina piu'"
            )

        # The UPDATE reaches PostgreSQL here, on purpose, and not at whichever later
        # statement happens to autoflush (`DocumentRepository.get` and
        # `ActivityService.record` both flush): the CHECK is the net under the row lock for
        # a write that did not take it, and a refusal has to be this `Conflict`, with only
        # this SAVEPOINT undone.
        with savepoint(ctx.session, issued_meanwhile):
            invoice.deleted_at = now
        for document_id in (invoice.pdf_document_id, invoice.xml_document_id):
            if document_id is None:
                continue
            document = ctx.documents.repo.get(document_id)
            if document is None:
                # Already archived, by hand or by an earlier attempt: nothing to redo.
                continue
            document.deleted_at = now
            ctx.activities.record(DOCUMENT_ENTITY, document.id, "deleted", actor)
        ctx.activities.record(ENTITY, invoice.id, "deleted", actor)
        # The days billed on it are billable again (REB-751): the draft that held them is gone.
        release_days_of_invoice(ctx.session, invoice.id, actor, DELETED_DRAFT_REASON)
        ctx.session.commit()

    def set_payment_state(self, invoice_id: UUID, data: PaymentState, actor: Actor) -> InvoiceRead:
        """Collection is a subsequent fact, not part of the document (spec 4), so this
        is the one invoice write a collaborator -- and an agent -- may perform."""
        actor.require_write("set_payment_state")
        ctx = self.ctx
        invoice = ctx.require_locked(invoice_id)
        if invoice.stato != "emessa":
            raise Conflict(
                ENTITY,
                "solo una fattura emessa ha un incasso da registrare",
                stato=invoice.stato,
            )
        if data.stato_pagamento == "incassato" and data.data_incasso is None:
            raise ValidationFailed(
                ENTITY,
                "data_incasso",
                "un incasso senza data non e' un incasso",
                expected="la data in cui il pagamento e' arrivato",
            )
        invoice.stato_pagamento = data.stato_pagamento
        # Cleared rather than left dangling: the table's own
        # `ck_invoices_incasso_requires_state` would refuse the inconsistent pair anyway,
        # and a stale date on an uncollected invoice is a lie either way.
        invoice.data_incasso = data.data_incasso if data.stato_pagamento == "incassato" else None
        ctx.activities.record(
            ENTITY,
            invoice.id,
            "payment_state_changed",
            actor,
            {"stato_pagamento": invoice.stato_pagamento},
        )
        ctx.session.commit()
        return ctx.read(invoice)
