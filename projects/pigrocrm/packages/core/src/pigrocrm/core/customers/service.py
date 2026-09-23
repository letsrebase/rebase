import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.repository import CustomerRepository
from pigrocrm.core.customers.schemas import (
    CUSTOMER_SORTS,
    CustomerCreate,
    CustomerListQuery,
    CustomerPage,
    CustomerRead,
    CustomerUpdate,
)
from pigrocrm.core.db import encode_cursor
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.fields.validator import validate_custom_fields
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes

# Typed as the fields module's own EntityType (not a bare `str`) so that passing it
# straight into `specs_for` type-checks under mypy strict -- a plain `ENTITY = "customer"`
# widens to `str` at the module level, which `specs_for(entity_type: EntityType)` then
# rejects. Every other call site here (NotFound, ValidationFailed, Conflict,
# ActivityService.record, validate_custom_fields) only asks for `str`, so the narrower
# type costs nothing there.
ENTITY: EntityType = "customer"
# `.fullmatch()`, not `.match()`, is load-bearing: `.match()` with a `$`-anchored
# pattern accepts a trailing "\n" -- `$` matches just before a final newline, not only
# at the true end of the string -- so "12345678901\n" (12 characters, one more than
# the `String(11)` column) used to pass this check and reach `flush()` as a raw,
# session-poisoning `DataError`. `.fullmatch()` requires the *entire* string to be
# consumed, which has no such exception. A pasted VAT number or an MCP agent's tool
# call carrying a trailing newline is not a lab-only case.
PARTITA_IVA_RE = re.compile(r"^\d{11}$")
CODICE_SDI_LENGTH = 7


def _check_fiscal(data: dict[str, Any], nazione: str) -> None:
    """Mutates `data` in place: an empty string is normalized to `None` for both
    fiscal fields before either is checked. Without this, `if piva`/`if sdi` below are
    falsy on "", so an empty string skipped the check entirely and was stored as "" --
    a different thing from "not provided" that would, for instance, wrongly satisfy a
    future "has a VAT number" filter. Shared by `create` and `update`, so the
    normalization applies equally to a brand-new row and to a patch that clears the
    field with "".
    """
    if data.get("partita_iva") == "":
        data["partita_iva"] = None
    if data.get("codice_sdi") == "":
        data["codice_sdi"] = None

    piva = data.get("partita_iva")
    # The eleven-digit rule is Italian, so it is applied to Italian customers and to
    # nobody else. It used to apply to everyone, which made a foreign customer
    # unrepresentable: a UK company's VAT number ("123456789", nine digits, and GB VATs
    # are not always numeric at all) was refused, so the only way to record it was the
    # `codice_fiscale` field, which is not validated -- a workaround that stores the
    # right value under the wrong name and then writes it into the wrong XML element.
    #
    # `nazione` is passed in rather than read from `data`, because on an update the
    # caller may be patching the VAT number without mentioning the country: the check
    # has to run against the country the row will actually have, not against the subset
    # of fields this request happened to name.
    if piva and nazione.upper() == "IT" and not PARTITA_IVA_RE.fullmatch(piva):
        raise ValidationFailed(
            ENTITY, "partita_iva", "deve essere di 11 cifre", expected="11 cifre numeriche"
        )
    sdi = data.get("codice_sdi")
    if sdi and len(sdi) != CODICE_SDI_LENGTH:
        raise ValidationFailed(
            ENTITY, "codice_sdi", "deve essere di 7 caratteri", expected="7 caratteri"
        )


class CustomerService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CustomerRepository(session)
        self.fields = FieldDefinitionService(session)
        self.activities = ActivityService(session)

    def _validated_custom(self, values: dict[str, Any]) -> dict[str, Any]:
        """Used by `create` only: `values` is the *complete* desired set of custom
        fields for a brand-new row, so it is validated against every active
        definition -- a required-but-absent field is genuinely missing here, not
        merely untouched, unlike on a partial `update` (see `_update_custom_fields`)."""
        return validate_custom_fields(ENTITY, self.fields.specs_for(ENTITY), values)

    def _update_custom_fields(self, customer: Customer, provided: dict[str, Any]) -> dict[str, Any]:
        """Validates only the keys the caller is touching, against active definitions
        -- never the union with what is already stored on `customer`. An earlier
        version merged `customer.custom_fields` with `provided` and validated the
        result, which broke Task 7's contract for archiving a field ("hide it, keep
        the data readable"): any archived key still present in storage failed the
        "campo non definito" check on *every* future update, even one that never
        mentions that key, and there was no way to clear it because the same check ran
        before any notion of removal.

        A key supplied with `None` removes that entry from the stored dict -- the one
        way left to clear an obsolete value once its definition is archived -- unless
        the key currently belongs to an active, `required=True` definition, in which
        case it raises the same "campo obbligatorio" `ValidationFailed` that
        `validate_custom_fields` would raise for `""` below. `None` and `""` are two
        spellings of "this field has no value"; a first version of this method treated
        them differently -- `""` reached `validate_custom_fields` and was correctly
        rejected by its own is_blank/required check, while `None` went straight into
        `to_remove` and never reached any required check at all, silently stripping a
        required value with no error. On a *required* field the two spellings must be
        rejected identically, or a caller strips the value just by choosing the other
        one. This check only fires for a key that is currently active and required:
        archived, undefined, or non-required keys keep the round 1 behavior above --
        clearing an archived field's stored value must stay possible even if the
        definition was required back when it was active.

        A key supplied with any other (non-blank) value must belong to a currently
        active definition: setting a *new* value on an archived key is refused, the
        same as on an undefined one -- archiving means "closed to new input," not
        "gone." Keys already stored that `provided` does not mention -- archived or
        not, required or not -- are carried over untouched.

        Filtering `specs` down to only the touched keys before calling
        `validate_custom_fields` is what keeps this partial-update-shaped: that
        function's own second pass walks every spec it is given and fails a blank
        *required* one, which is correct when `values` is meant to be a complete set
        (`_validated_custom`, above) but would wrongly fail an active required field
        that this update never mentions at all if the full active spec list were
        passed here unfiltered.
        """
        active_by_key = {spec.key: spec for spec in self.fields.specs_for(ENTITY)}

        to_remove: set[str] = set()
        for key, value in provided.items():
            if value is not None:
                continue
            spec = active_by_key.get(key)
            if spec is not None and spec.required:
                raise ValidationFailed(
                    ENTITY, key, "campo obbligatorio", expected="un valore non vuoto"
                )
            to_remove.add(key)

        to_set = {key: value for key, value in provided.items() if value is not None}
        touched_specs = [spec for spec in active_by_key.values() if spec.key in to_set]
        validated = validate_custom_fields(ENTITY, touched_specs, to_set)

        merged = {k: v for k, v in customer.custom_fields.items() if k not in to_remove}
        merged.update(validated)
        return merged

    def _insert(self, data: CustomerCreate, actor: Actor) -> Customer:
        """The write half of `create`, without the commit (design record
        `2026-09-23-mastro-invoice-import-onto-pigrocrm-design.md` §7 item 5):
        flushes the row and records its own "created" activity, on `self.session`,
        exactly as `create` does, but leaves the commit to the caller.

        This is the shape mastro's own `confirmClientContractProposal` takes an
        optional `tx: DbExecutor` for instead of opening its own transaction --
        `InvoiceService.confirm_import` is the caller that needs it: a `Customer`
        this method inserts must not outlive a register write that fails a moment
        later, so both have to land in one commit or neither does. `create` is
        `self.session` already; a caller sharing that same session (`InvoiceService`
        composes its own `CustomerService(self.session)`) inserts the customer into
        the exact transaction its own write is about to join.
        """
        actor.require_write("create_customer")
        payload = data.model_dump()
        _check_fiscal(payload, payload.get("nazione") or "IT")
        payload["custom_fields"] = self._validated_custom(payload.get("custom_fields") or {})

        customer = self.repo.add(Customer(**payload))
        self.activities.record(
            ENTITY, customer.id, "created", actor, {"ragione_sociale": customer.ragione_sociale}
        )
        return customer

    def create(self, data: CustomerCreate, actor: Actor) -> CustomerRead:
        customer = self._insert(data, actor)
        self.session.commit()
        return CustomerRead.model_validate(customer)

    def update(self, customer_id: UUID, data: CustomerUpdate, actor: Actor) -> CustomerRead:
        actor.require_write("update_customer")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        # custom_fields is handled separately from the rest of the payload, reading
        # `data.custom_fields` directly rather than through `model_dump`: this method
        # must see a caller-supplied `None` *inside* the dict (e.g. {"settore": None},
        # meaning "remove this key") exactly as given, with no risk of it being
        # confused with the field itself being absent -- see `_update_custom_fields`.
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Customer, changes)
        # The country after this patch, not merely the one it mentions.
        _check_fiscal(changes, changes.get("nazione") or customer.nazione)
        if data.custom_fields is not None:
            changes["custom_fields"] = self._update_custom_fields(customer, data.custom_fields)
        for key, value in changes.items():
            setattr(customer, key, value)

        self.activities.record(ENTITY, customer.id, "updated", actor, {"changed": sorted(changes)})
        self.session.commit()
        return CustomerRead.model_validate(customer)

    def get(self, customer_id: UUID, actor: Actor) -> CustomerRead:
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        return CustomerRead.model_validate(customer)

    def soft_delete(self, customer_id: UUID, actor: Actor) -> None:
        """Sets deleted_at. No physical delete exists in this slice: a misread
        instruction from an agent must be reversible."""
        actor.require_write("delete_customer")
        customer = self.repo.get(customer_id)
        if customer is None:
            raise NotFound(ENTITY, customer_id)

        active_deals = self.repo.count_active_deals(customer_id)
        if active_deals:
            raise Conflict(
                ENTITY,
                "il cliente ha deal attivi: archivia prima i deal",
                active_deals=active_deals,
            )

        customer.deleted_at = datetime.now(UTC)
        self.activities.record(ENTITY, customer.id, "deleted", actor)
        self.session.commit()

    def restore(self, customer_id: UUID, actor: Actor) -> CustomerRead:
        actor.require_write("restore_customer")
        customer = self.repo.get(customer_id, include_deleted=True)
        if customer is None:
            raise NotFound(ENTITY, customer_id)
        # Recorded only when the customer really was deleted: unconditionally logging
        # "restored" here -- even for a customer that was never soft-deleted -- would
        # write a timeline entry claiming a recovery that never happened.
        was_deleted = customer.deleted_at is not None
        customer.deleted_at = None
        if was_deleted:
            self.activities.record(ENTITY, customer.id, "restored", actor)
        self.session.commit()
        return CustomerRead.model_validate(customer)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (see `FieldDefinitionService.specs_for`'s docstring and
    # `PipelineService`'s own ordering for the two other places it already applies):
    # defining a method named `list` rebinds that name in the *class* namespace, so
    # any later method whose own return annotation is a bare `list[...]` would resolve
    # `list` to this method instead of the builtin and fail at import time. No method
    # in this class has that shape today, but the rule does not have a "only when it
    # would currently break" exception -- this is the file People and Deals copy.
    def list(self, query: CustomerListQuery, actor: Actor) -> CustomerPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # The cursor encodes `(sort value, id)` of the last returned row, not the bare
        # id: a scan ordered by a non-unique column cannot resume from an id alone.
        # `getattr(…, spec.key)` is safe precisely because `spec` came out of the
        # whitelist -- the key is one of three literals declared in this package, never
        # a caller-supplied string.
        spec = CUSTOMER_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return CustomerPage(
            items=[CustomerRead.model_validate(c) for c in items],
            next_cursor=next_cursor,
        )
