"""One description of an entity's shape, for every adapter.

Lives in core rather than in an adapter because both the REST API and the MCP
server must answer "what fields does a customer have?" with the same answer.
Nothing in core imports this module, so pulling in the entity schemas here
creates no cycle.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from pigrocrm.core.contracts.schemas import ContractCreate
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.fields.dynamic import describe_specs
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.invoices.schemas import InvoiceCreate
from pigrocrm.core.organizations.schemas import OrganizationCreate
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.purchases.schemas import PurchaseCreate
from pigrocrm.core.tasks.schemas import TaskCreate
from pigrocrm.core.timetracking.schemas import TimeEntryCreate

ENTITY_TYPES: tuple[EntityType, ...] = (
    "organization",
    "person",
    "deal",
    "document",
    "invoice",
    "time_entry",
    "purchase",
    "task",
    "contract",
)

CREATE_MODELS: dict[str, type[BaseModel]] = {
    "organization": OrganizationCreate,
    "person": PersonCreate,
    "deal": DealCreate,
    "document": DocumentCreate,
    "invoice": InvoiceCreate,
    "time_entry": TimeEntryCreate,
    "purchase": PurchaseCreate,
    "task": TaskCreate,
    "contract": ContractCreate,
}

# Native columns an entity has that its Create schema does *not* declare, because they
# are derived or set only by a dedicated method. Empty for the entities whose
# writable surface is their whole surface; non-empty for `invoice`, whose fiscal
# columns are computed at emission and are exactly the names an administrator would
# slugify into by accident ("Totale" -> `total`), and for `time_entry`/`cost`,
# whose billing-state columns (Task 4A-5's plan: `applied_rate`,
# `applied_cost`, `rate_source`, `cost_source`, `invoice_line_id`,
# `document_id`) are set only when a line is applied or a receipt attached, never on
# create -- A13's own residual note for this slice: check whether the guard needs
# the same explicit supplement `invoice` already required, and it does.
#
# `FieldDefinitionService.create` (Task 4A-2) reads this function before writing a
# definition, closing A13 for every entity listed here, including the fiscal columns
# `InvoiceCreate` itself never declares.
EXTRA_NATIVE_FIELDS: dict[str, tuple[str, ...]] = {
    "organization": (),
    "person": (),
    # Derived by the service from `value_lines`: `DealCreate` no longer takes it, but the name
    # is still a column no custom field may take.
    "deal": ("expected_value",),
    "document": (),
    "invoice": (
        "year",
        "number",
        "reference",
        "status",
        "issue_date",
        "due_date",
        "accrual_from",
        "accrual_to",
        "sdi_document_type",
        "currency",
        "taxable_amount",
        "vat_amount",
        "stamp_duty",
        "total",
        "payment_status",
        "payment_date",
        "transmitted_externally_at",
        "cancelled_at",
        "cancellation_reason",
        "origin_proforma_id",
    ),
    "time_entry": (
        "applied_rate",
        "applied_cost",
        "rate_source",
        "cost_source",
        "invoice_line_id",
    ),
    # Set by the service and never taken on create: the state, the frozen cost and the buyer's
    # pack and regime frozen at registration.
    "purchase": (
        "status",
        "cost_amount",
        "buyer_pack_id",
        "buyer_pack_version",
        "buyer_regime_code",
    ),
    # `status`, `completed_at`, `origin` and `rule` are columns `TaskCreate`
    # deliberately does not declare: the state moves only through `complete`, `cancel`
    # and `reopen`, and `origin`/`rule` say who created the row -- a caller that
    # could set them could claim to be an automation. They are listed here so a
    # custom-field definition cannot be slugified onto one of them.
    "task": ("status", "completed_at", "origin", "rule"),
    # `status` moves only through a dedicated method (none exists yet in this
    # spike) and `previous_contract_id` is the renewal chain, deliberately
    # left unset here (REB-358) -- neither is on `ContractCreate`, so both must
    # be reserved here or an administrator could slugify a custom field onto
    # either native column.
    "contract": ("status", "previous_contract_id"),
}


# What a Create schema accepts without it being a column of the entity: `note` on a deal,
# a customer and a person is the text of the record's first note (REB-879), a row of the
# `notes` thread. It is not listed as a native field, but it is reserved: no custom field
# may be called `note` (`reserved_field_names`), since a form's first-note control and the
# MCP's `create_*` read that name.
NOT_COLUMNS: dict[str, tuple[str, ...]] = {
    "organization": ("note",),
    "person": ("note",),
    "deal": ("note",),
}


def reserved_field_names(entity_type: str) -> list[str]:
    """What a custom field may not be called: the native columns and what the Create schema
    takes without a column of its own (`note`), which a form's first-note control and the
    MCP's `create_*` read by that name."""
    return [*native_fields(entity_type), *NOT_COLUMNS.get(entity_type, ())]


def native_fields(entity_type: str) -> list[str]:
    """Derived from the Pydantic model, never hand-listed -- plus the columns that
    model cannot declare (see EXTRA_NATIVE_FIELDS). Order is stable: derived first, in
    field-definition order, then the extras in declaration order, so a caller can
    diff two runs."""
    skipped = ("custom_fields", *NOT_COLUMNS.get(entity_type, ()))
    derived = [name for name in CREATE_MODELS[entity_type].model_fields if name not in skipped]
    extra = [name for name in EXTRA_NATIVE_FIELDS.get(entity_type, ()) if name not in derived]
    return derived + extra


def describe_entity(session: Session, entity_type: EntityType) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "native_fields": native_fields(entity_type),
        "custom_fields": describe_specs(FieldDefinitionService(session).specs_for(entity_type)),
    }
