from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.deals.repository import DealRepository
from pigrocrm.core.documents.repository import DocumentRepository
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.inbox.filing import file_as_purchase, resolve_original
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.money import ZERO_MONEY, round_money
from pigrocrm.core.organizations.repository import OrganizationRepository
from pigrocrm.core.purchases.cost import VatFigures, purchase_cost
from pigrocrm.core.purchases.models import (
    BASE_CURRENCY,
    VAT_KINDS,
    Purchase,
    PurchaseVatLine,
)
from pigrocrm.core.purchases.repository import PurchaseRepository
from pigrocrm.core.purchases.schemas import (
    PurchaseCreate,
    PurchaseListQuery,
    PurchasePage,
    PurchaseRead,
    PurchaseUpdate,
    VatLine,
)
from pigrocrm.core.schemas import changed_keys, reject_cleared_columns, supplied_changes
from pigrocrm.core.timetracking.categories import CostCategoryService
from pigrocrm.core.timetracking.locks import PeriodLockService

# Typed as the fields module's own EntityType (not a bare `str`): passing a plain `str` into
# `specs_for` fails mypy strict, which requires the narrower Literal type.
ENTITY: EntityType = "purchase"

# A document's total is its recap plus the stamp duty, to the rounding the issuer's software
# did on each line: a cent per group would be exact, five cents is what a document with a
# dozen groups can drift by and still be the document the supplier sent.
TOTAL_TOLERANCE = Decimal("0.05")
# A withholding is its rate on its base, to the cent.
WITHHOLDING_TOLERANCE = Decimal("0.01")
# What an update merges with its changes, and what `_check` reads.
_DOCUMENT_FIELDS = (
    "kind",
    "supplier_id",
    "date",
    "received_on",
    "iva_period",
    "amount",
    "currency",
    "currency_amount",
    "exchange_rate",
    "exchange_rate_date",
    "stamp_duty",
    "withholding_rate",
    "withholding_base",
    "withholding_amount",
    "fund_rate",
    "fund_amount",
    "split_payment",
    "reverse_charge",
    "corrects_purchase_id",
)


def _sign(value: Decimal) -> int:
    return (value > 0) - (value < 0)


class PurchaseService:
    """Everything the space pays for, with its document and its supplier.

    A purchase is not a time entry even though both reduce the margin (slice 4 §4.2): it is
    real money out with a document behind it, while an hour's cost is an internal, notional
    figure derived from a rate you chose; an hour is also potential revenue and a purchase
    never is. The labour cost never produces a row here and is never counted twice: an
    external consultant who invoices you their hours is a purchase in the category «Consulenza
    esterna», and their hours, if you record them at all, carry `applied_cost = NULL`.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PurchaseRepository(session)
        self.deals = DealRepository(session)
        self.legal_entities = LegalEntityService(session)
        self.documents = DocumentRepository(session)
        self.organizations = OrganizationRepository(session)
        self.fiscal = FiscalProfileRepository(session)
        self.categories = CostCategoryService(session)
        self.locks = PeriodLockService(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    # --- references ---------------------------------------------------------------------

    def _check_amount(self, amount: Decimal | None) -> None:
        """Zero is refused because it is neither a purchase nor a correction. Checked here as
        well as by `ck_purchases_amount_not_zero`, not instead of it: this raises the project's
        own `ValidationFailed` naming the field, the CHECK covers every other write path.

        `None` is refused separately: an `Update` schema can spell "clear this column", and
        `purchases.amount` is `NOT NULL`; an emptied amount is not a zero amount."""
        if amount is None:
            raise ValidationFailed(
                ENTITY, "amount", "l'importo non può essere svuotato", expected="un importo"
            )
        if amount == 0:
            raise ValidationFailed(
                ENTITY, "amount", "importo nullo", expected="un importo diverso da zero"
            )

    def _check_refs(
        self, deal_id: UUID | None, document_id: UUID | None, inbox_item_id: UUID | None
    ) -> None:
        """Every foreign key validated in both create and update, optional ones included --
        skipped only when the caller supplies nothing, never when they supply a value. Without
        this any syntactically valid UUID reaches `flush()` and comes back as a raw
        `ForeignKeyViolation`."""
        if deal_id is not None and self.deals.get(deal_id) is None:
            raise NotFound("deal", deal_id)
        if document_id is not None and self.documents.get(document_id) is None:
            raise NotFound("document", document_id)
        if inbox_item_id is not None and self.session.get(InboxItem, inbox_item_id) is None:
            raise NotFound("inbox_item", inbox_item_id)

    def _check_supplier(self, supplier_id: UUID | None) -> None:
        if supplier_id is None:
            return
        supplier = self.organizations.get(supplier_id)
        if supplier is None:
            raise NotFound("organization", supplier_id)
        if "supplier" not in supplier.roles:
            raise ValidationFailed(
                ENTITY,
                "supplier_id",
                "l'anagrafica non ha il ruolo di fornitore",
                expected="un'anagrafica con il ruolo di fornitore",
            )

    def _check_corrected(
        self, purchase_id: UUID | None, supplier_id: UUID | None, own_id: UUID | None
    ) -> None:
        """The invoice a credit note corrects: an invoice of the same supplier, not itself."""
        if purchase_id is None:
            return
        corrected = self.repo.get(purchase_id, include_deleted=True)
        if corrected is None or purchase_id == own_id:
            raise NotFound(ENTITY, purchase_id)
        if corrected.kind != "invoice" or corrected.supplier_id != supplier_id:
            raise ValidationFailed(
                ENTITY,
                "corrects_purchase_id",
                "una nota di credito corregge una fattura dello stesso fornitore",
                expected="l'id di una fattura dello stesso fornitore",
            )

    def _legal_entity_for(
        self, deal_id: UUID | None, wanted: UUID | None, *, kept: UUID | None = None
    ) -> UUID | None:
        """A purchase's legal entity (REB-623, spec §1.7): the deal's when it has a deal, and a
        different one named beside it is refused rather than overruled in silence; without a
        deal, the one named, which must exist and be active, or `None`, a shared purchase.
        `kept` is the legal entity the purchase already has: an edit that leaves it where it is
        passes even once that legal entity is deactivated, so the history of a closed legal
        entity stays editable while nothing new is filed under it (spec §3)."""
        if deal_id is not None:
            # The deal a purchase already hangs on may be archived since: its legal entity
            # still answers, and an update of such a purchase is a domain error at worst,
            # never an assertion.
            deal = self.deals.get(deal_id, include_deleted=True)
            if deal is None:
                raise NotFound("deal", deal_id)
            if wanted is not None and wanted != deal.legal_entity_id:
                raise ValidationFailed(
                    ENTITY,
                    "legal_entity_id",
                    "un acquisto su un deal prende l'azienda del deal",
                    expected=f"nessuna azienda, oppure {deal.legal_entity_id}",
                )
            # The deal's legal entity may be closed since the deal was created: a purchase
            # already there stays editable, a new one is refused like a deal or a document.
            if deal.legal_entity_id == kept:
                return deal.legal_entity_id
            return self.legal_entities.inherited(deal.legal_entity_id, ENTITY)
        if wanted is None:
            return None
        legal_entity = self.legal_entities.resolve(wanted)
        if not legal_entity.active and wanted != kept:
            raise ValidationFailed(
                ENTITY,
                "legal_entity_id",
                "l'azienda non e' attiva: una spesa si assegna a un'azienda attiva",
                expected="l'id di un'azienda attiva, oppure nessuna per una spesa condivisa",
            )
        return legal_entity.id

    def _only_legal_entity(self) -> UUID | None:
        """The buyer of a supplier's document that names none: the space's only active legal
        entity. A shared purchase has no pack, so it costs its total (§ 2), and in a space with
        one legal entity «shared» is that entity: without this, an ordinary-regime solo
        professional's invoices would all cost their VAT too. A space with several, or none,
        leaves the document shared until somebody names the buyer."""
        try:
            legal_entity = self.legal_entities.single()
        except NotFound:
            return None
        return legal_entity.id if legal_entity is not None else None

    def _buyer_pack(
        self, legal_entity_id: UUID | None
    ) -> tuple[str | None, str | None, str | None]:
        """The pack, its version and the regime code of the buyer, as they are now: what a
        registration freezes. A shared purchase and a legal entity with no profile have none."""
        if legal_entity_id is None:
            return None, None, None
        profile = self.fiscal.get(legal_entity_id)
        if profile is None:
            return None, None, None
        return profile.pack_id, profile.pack_version, profile.regime_code

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _require(self, purchase_id: UUID, *, include_deleted: bool = False) -> Purchase:
        purchase = self.repo.get(purchase_id, include_deleted=include_deleted)
        if purchase is None:
            raise NotFound(ENTITY, purchase_id)
        return purchase

    # --- the document -------------------------------------------------------------------

    def _check_document(
        self,
        values: dict[str, Any],
        lines: list[VatLine],
        *,
        pack_id: str | None,
        own_id: UUID | None,
        kept_supplier: UUID | None = None,
    ) -> None:
        """What a supplier's document has to say about itself, whatever its kind, on the values
        the purchase would hold after the write."""
        kind: str = values["kind"]
        amount: Decimal = values["amount"]
        self._check_amount(amount)
        sign = _sign(amount)
        vat_kind = kind in VAT_KINDS

        if kind == "invoice" and sign < 0:
            raise ValidationFailed(
                ENTITY,
                "amount",
                "il totale di una fattura è positivo: un rimborso è una nota di credito",
                expected="un importo positivo",
            )
        if kind == "credit_note" and sign > 0:
            raise ValidationFailed(
                ENTITY,
                "amount",
                "il totale di una nota di credito è negativo",
                expected="un importo negativo",
            )
        if vat_kind and values["supplier_id"] is None:
            raise ValidationFailed(
                ENTITY,
                "supplier_id",
                "una fattura o una nota di credito indica il fornitore",
                expected="l'id di un fornitore",
            )
        if values["supplier_id"] != kept_supplier:
            self._check_supplier(values["supplier_id"])
        if kind == "credit_note":
            self._check_corrected(values["corrects_purchase_id"], values["supplier_id"], own_id)
        elif values["corrects_purchase_id"] is not None:
            raise ValidationFailed(
                ENTITY,
                "corrects_purchase_id",
                "solo una nota di credito corregge un'altra fattura",
            )

        self._check_vat(values, lines, vat_kind=vat_kind, sign=sign)
        self._check_currency(values, sign)
        self._check_withholding(values, pack_id=pack_id)

    def _check_vat(
        self, values: dict[str, Any], lines: list[VatLine], *, vat_kind: bool, sign: int
    ) -> None:
        if not vat_kind:
            if lines:
                raise ValidationFailed(
                    ENTITY,
                    "vat_lines",
                    "una ricevuta o una spesa ha solo il totale: l'IVA di uno scontrino non "
                    "si detrae senza fattura",
                    expected="nessuna riga IVA",
                )
            for flag in ("split_payment", "reverse_charge"):
                if values[flag]:
                    raise ValidationFailed(
                        ENTITY, flag, "solo una fattura o una nota di credito ha un regime IVA"
                    )
            if values["fund_rate"] is not None:
                raise ValidationFailed(
                    ENTITY, "fund_rate", "solo una fattura o una nota di credito ha una cassa"
                )
            if values["iva_period"] is not None:
                raise ValidationFailed(
                    ENTITY,
                    "iva_period",
                    "solo una fattura o una nota di credito ha un periodo IVA",
                )
            return
        if not lines:
            raise ValidationFailed(
                ENTITY,
                "vat_lines",
                "una fattura o una nota di credito ha almeno una riga di riepilogo IVA",
                expected="almeno una riga",
            )
        stamp: Decimal = values["stamp_duty"] or ZERO_MONEY
        if stamp * sign < 0:
            raise ValidationFailed(ENTITY, "stamp_duty", "il bollo ha il segno del totale")
        for line in lines:
            if line.taxable_amount * sign < 0 or line.vat_amount * sign < 0:
                raise ValidationFailed(
                    ENTITY, "vat_lines", "gli importi di una riga hanno il segno del totale"
                )
            if line.rate == 0 and (line.nature is None or line.vat_amount != 0):
                raise ValidationFailed(
                    ENTITY,
                    "vat_lines",
                    "una riga con aliquota zero ha la natura e nessuna imposta",
                    expected="la natura (N1, N2.1, ...) e IVA zero",
                )
            if line.rate > 0 and line.nature is not None:
                raise ValidationFailed(
                    ENTITY, "vat_lines", "la natura si indica solo su una riga con aliquota zero"
                )
        taxable = sum((line.taxable_amount for line in lines), start=ZERO_MONEY)
        vat = sum((line.vat_amount for line in lines), start=ZERO_MONEY)
        # Under reverse charge the VAT is owed by the buyer and is not in what is paid.
        expected = taxable + stamp + (ZERO_MONEY if values["reverse_charge"] else vat)
        if abs(values["amount"] - expected) > TOTAL_TOLERANCE:
            raise ValidationFailed(
                ENTITY,
                "amount",
                "il totale non torna con il riepilogo IVA",
                expected=f"{round_money(expected)} (imponibile, bollo e, se è a carico del "
                "fornitore, IVA)",
            )

    def _check_currency(self, values: dict[str, Any], sign: int) -> None:
        foreign = values["currency"] != BASE_CURRENCY
        given = [
            values["currency_amount"],
            values["exchange_rate"],
            values["exchange_rate_date"],
        ]
        if foreign and any(item is None for item in given):
            raise ValidationFailed(
                ENTITY,
                "currency",
                "un acquisto in valuta indica l'importo in valuta, il cambio e la sua data",
                expected="currency_amount, exchange_rate ed exchange_rate_date",
            )
        if not foreign and any(item is not None for item in given):
            raise ValidationFailed(
                ENTITY,
                "currency",
                "un acquisto in euro non ha importo in valuta né cambio",
                expected="nessun importo in valuta",
            )
        if foreign and values["currency_amount"] * sign < 0:
            raise ValidationFailed(
                ENTITY, "currency_amount", "l'importo in valuta ha il segno del totale"
            )

    def _check_withholding(self, values: dict[str, Any], *, pack_id: str | None) -> None:
        given = [
            values["withholding_rate"],
            values["withholding_base"],
            values["withholding_amount"],
        ]
        if all(item is None for item in given):
            return
        if any(item is None for item in given):
            raise ValidationFailed(
                ENTITY,
                "withholding_rate",
                "la ritenuta ha aliquota, base e importo insieme",
                expected="withholding_rate, withholding_base e withholding_amount",
            )
        if values["kind"] == "expense":
            raise ValidationFailed(
                ENTITY,
                "withholding_rate",
                "una spesa non ha ritenuta: serve una fattura o una ricevuta",
            )
        if pack_id == PACK_IT_FLAT_RATE:
            # A flat-rate taxpayer is not a withholding agent (art. 1 c. 69 L. 190/2014, A5).
            raise ValidationFailed(
                ENTITY,
                "withholding_rate",
                "chi è in regime forfettario non è sostituto d'imposta: nessuna ritenuta",
            )
        computed = round_money(values["withholding_base"] * values["withholding_rate"] / 100)
        if abs(computed - values["withholding_amount"]) > WITHHOLDING_TOLERANCE:
            raise ValidationFailed(
                ENTITY,
                "withholding_amount",
                "la ritenuta non è l'aliquota sulla base",
                expected=str(computed),
            )

    def _cost(
        self, values: dict[str, Any], lines: list[VatLine], *, pack_id: str | None
    ) -> Decimal:
        return purchase_cost(
            pack_id=pack_id,
            amount=values["amount"],
            lines=[VatFigures(line.vat_amount, line.deductible_share) for line in lines],
            reverse_charge=values["reverse_charge"],
        )

    @staticmethod
    def _rows(lines: list[VatLine]) -> list[PurchaseVatLine]:
        return [
            PurchaseVatLine(
                position=position,
                rate=line.rate,
                nature=line.nature,
                taxable_amount=line.taxable_amount,
                vat_amount=line.vat_amount,
                deductible_share=line.deductible_share,
            )
            for position, line in enumerate(lines)
        ]

    @staticmethod
    def _default_iva_period(values: dict[str, Any]) -> None:
        """An invoice or a credit note is registered in the period it was received in unless a
        person chose another. Art. 25 c. 1 DPR 633/72 lets the register be annotated after the
        receipt, up to a term that D.Lgs. 148/2026 art. 12 moved (A5): the day of receipt is a
        default and the term is not enforced here, since which term governs an invoice received
        before 12 August 2026 is left to the accountant (REB-975). The IVA register card checks
        the term."""
        if values["kind"] in VAT_KINDS and values["iva_period"] is None:
            values["iva_period"] = values["received_on"] or values["date"]

    # --- writes -------------------------------------------------------------------------

    def create(self, data: PurchaseCreate, actor: Actor) -> PurchaseRead:
        actor.require_write("create_purchase")
        self._check_refs(data.deal_id, data.document_id, data.inbox_item_id)
        inbox_item_id = (
            resolve_original(self.session, data.inbox_item_id, actor)
            if data.inbox_item_id is not None
            else None
        )
        self.categories.require_active(data.category_id)
        self.locks.assert_writable(ENTITY, "date", data.date)

        legal_entity_id = self._legal_entity_for(data.deal_id, data.legal_entity_id)
        if legal_entity_id is None and data.kind in VAT_KINDS:
            legal_entity_id = self._only_legal_entity()
        pack_id, pack_version, regime_code = self._buyer_pack(legal_entity_id)
        values = {name: getattr(data, name) for name in _DOCUMENT_FIELDS}
        self._default_iva_period(values)
        self._check_document(values, data.vat_lines, pack_id=pack_id, own_id=None)

        purchase = self.repo.add(
            Purchase(
                deal_id=data.deal_id,
                legal_entity_id=legal_entity_id,
                category_id=data.category_id,
                merchant_name=data.merchant_name,
                number=data.number,
                description=data.description,
                inbox_item_id=inbox_item_id,
                document_id=data.document_id,
                buyer_pack_id=pack_id,
                buyer_pack_version=pack_version,
                buyer_regime_code=regime_code,
                cost_amount=self._cost(values, data.vat_lines, pack_id=pack_id),
                custom_fields=self._validated_custom(data.custom_fields or {}),
                vat_lines=self._rows(data.vat_lines),
                **values,
            )
        )
        self.activities.record(
            ENTITY,
            purchase.id,
            "created",
            actor,
            {
                "kind": purchase.kind,
                "amount": str(purchase.amount),
                "deal_id": str(purchase.deal_id) if purchase.deal_id else None,
            },
        )
        if purchase.inbox_item_id is not None:
            file_as_purchase(
                self.session,
                purchase.inbox_item_id,
                purchase.id,
                actor,
                purchase.legal_entity_id,
            )
        self.session.commit()
        return PurchaseRead.model_validate(purchase)

    def update(self, purchase_id: UUID, data: PurchaseUpdate, actor: Actor) -> PurchaseRead:
        actor.require_write("update_purchase")
        purchase = self._require(purchase_id)
        if purchase.status == "cancelled":
            raise Conflict(ENTITY, "un acquisto annullato non si modifica", id=str(purchase_id))
        changes = supplied_changes(data, exclude={"custom_fields", "vat_lines"})
        # Before the generic guard, deliberately: `amount` is the one `NOT NULL` column on this
        # table a caller plausibly tries to empty, and it deserves the message that names it
        # rather than the catch-all one.
        if "amount" in changes:
            self._check_amount(changes["amount"])
        reject_cleared_columns(ENTITY, Purchase, changes)
        # `_check_refs` already reads values rather than keys, so an explicit `deal_id: null`
        # passes through it as "clear it" instead of becoming a lookup for `None`.
        self._check_refs(
            changes.get("deal_id"), changes.get("document_id"), changes.get("inbox_item_id")
        )
        legal_entity_id = purchase.legal_entity_id
        if "deal_id" in changes or "legal_entity_id" in changes:
            # Moved to another deal, off a deal, or given a legal entity of its own: the legal
            # entity follows the deal when there is one and the request otherwise.
            deal_id = changes.get("deal_id", purchase.deal_id)
            # A move is a `deal_id` that differs from the purchase's: one sent back unchanged,
            # as a client echoing the row does, is no move, and neither is `null` echoed on a
            # purchase that had no deal.
            moved = "deal_id" in changes and changes["deal_id"] != purchase.deal_id
            # Off a deal with no legal entity named: shared, not the old deal's. Not moved and
            # nothing named: the legal entity it has.
            wanted = changes.get("legal_entity_id", None if moved else purchase.legal_entity_id)
            # And the allowance for a closed legal entity holds only while the purchase stays
            # where it is: a move is a new assignment, and a closed legal entity takes none.
            legal_entity_id = self._legal_entity_for(
                deal_id, wanted, kept=None if moved else purchase.legal_entity_id
            )
            changes["legal_entity_id"] = legal_entity_id
        if (
            legal_entity_id is None
            and changes.get("kind", purchase.kind) in VAT_KINDS
            and ({"kind", "deal_id", "legal_entity_id"} & changes.keys())
        ):
            # A purchase that becomes an invoice or a credit note, or leaves its deal, buys as
            # the space's only legal entity, as a new one would (`create`).
            legal_entity_id = self._only_legal_entity()
            if legal_entity_id is not None:
                changes["legal_entity_id"] = legal_entity_id
        if changes.get("category_id") is not None:
            self.categories.require_active(changes["category_id"])
        self.locks.assert_writable(ENTITY, "date", purchase.date, changes.get("date"))

        # The buyer's pack is frozen at registration: it moves only with the legal entity, and is
        # taken the first time a row that was a cost (which had no pack decision) is edited.
        pack = (
            self._buyer_pack(legal_entity_id)
            if legal_entity_id != purchase.legal_entity_id or purchase.buyer_pack_id is None
            else (
                purchase.buyer_pack_id,
                purchase.buyer_pack_version,
                purchase.buyer_regime_code,
            )
        )
        values = {name: getattr(purchase, name) for name in _DOCUMENT_FIELDS}
        values.update({name: value for name, value in changes.items() if name in values})
        lines = (
            data.vat_lines
            if data.vat_lines is not None
            else [VatLine.model_validate(line, from_attributes=True) for line in purchase.vat_lines]
        )
        self._default_iva_period(values)
        self._check_document(
            values, lines, pack_id=pack[0], own_id=purchase.id, kept_supplier=purchase.supplier_id
        )
        changes.update({name: value for name, value in values.items() if name not in changes})
        changes["iva_period"] = values["iva_period"]
        changes["cost_amount"] = self._cost(values, lines, pack_id=pack[0])
        changes["buyer_pack_id"], changes["buyer_pack_version"], changes["buyer_regime_code"] = pack

        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY, self.fields.specs_for(ENTITY), purchase.custom_fields, data.custom_fields
            )
        # The id the item resolves to, before the diff: a duplicate that resolves to the item
        # already linked is no change, and the timeline must not say it was.
        linked: UUID | None = changes.get("inbox_item_id")
        if linked is not None:
            linked = resolve_original(self.session, linked, actor)
            changes["inbox_item_id"] = linked
        changed = changed_keys(purchase, changes)
        if data.vat_lines is not None:
            current = [
                VatLine.model_validate(row, from_attributes=True) for row in purchase.vat_lines
            ]
            if current != data.vat_lines:
                changed.append("vat_lines")
                purchase.vat_lines = self._rows(data.vat_lines)
        newly_linked = linked if linked != purchase.inbox_item_id else None
        for key, value in changes.items():
            setattr(purchase, key, value)
        if newly_linked is not None:
            file_as_purchase(
                self.session, newly_linked, purchase.id, actor, purchase.legal_entity_id
            )
        self.activities.record(
            ENTITY,
            purchase.id,
            "updated",
            actor,
            {"changed": sorted(set(changed) - {"cost_amount", *_FROZEN_BY_THE_SERVICE})},
        )
        self.session.commit()
        return PurchaseRead.model_validate(purchase)

    def cancel(self, purchase_id: UUID, actor: Actor) -> PurchaseRead:
        """A registered document that turns out not to count: the row stays, with its history,
        and every reader leaves it out. An archive is for a row entered by mistake; this is
        for a document that was void."""
        actor.require_write("cancel_purchase")
        purchase = self._require(purchase_id)
        if purchase.status == "cancelled":
            raise Conflict(ENTITY, "l'acquisto è già annullato", id=str(purchase_id))
        self.locks.assert_writable(ENTITY, "date", purchase.date)
        purchase.status = "cancelled"
        self.activities.record(ENTITY, purchase.id, "cancelled", actor)
        self.session.commit()
        return PurchaseRead.model_validate(purchase)

    def soft_delete(self, purchase_id: UUID, actor: Actor) -> None:
        actor.require_write("delete_purchase")
        purchase = self._require(purchase_id)
        self.locks.assert_writable(ENTITY, "date", purchase.date)
        purchase.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, purchase.id, "deleted", actor)
        self.session.commit()

    def restore(self, purchase_id: UUID, actor: Actor) -> PurchaseRead:
        actor.require_write("restore_purchase")
        purchase = self._require(purchase_id, include_deleted=True)
        self.locks.assert_writable(ENTITY, "date", purchase.date)
        was_deleted = purchase.deleted_at is not None
        purchase.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, purchase.id, "restored", actor)
        self.session.commit()
        return PurchaseRead.model_validate(purchase)

    def get(self, purchase_id: UUID, actor: Actor) -> PurchaseRead:
        return PurchaseRead.model_validate(self._require(purchase_id))

    # `list` stays the last method in this class -- the unconditional project rule.
    def list(self, query: PurchaseListQuery, actor: Actor) -> PurchasePage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        total_count, total_amount = self.repo.totals(query)
        return PurchasePage(
            items=[PurchaseRead.model_validate(row) for row in items],
            next_cursor=items[-1].id if has_more and items else None,
            total_count=total_count,
            total_amount=total_amount,
        )


# Columns the service derives and an update reports under the field the caller changed.
_FROZEN_BY_THE_SERVICE = ("buyer_pack_id", "buyer_pack_version", "buyer_regime_code")
