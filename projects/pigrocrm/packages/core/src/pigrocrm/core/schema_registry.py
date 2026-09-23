"""One description of an entity's shape, for every adapter.

Lives in core rather than in an adapter because both the REST API and the MCP
server must answer "what fields does a customer have?" with the same answer.
Nothing in core imports this module, so pulling in the entity schemas here
creates no cycle.
"""

from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from pigrocrm.core.attivita.schemas import AttivitaCreate
from pigrocrm.core.contracts.schemas import ContractCreate
from pigrocrm.core.customers.schemas import CustomerCreate
from pigrocrm.core.deals.schemas import DealCreate
from pigrocrm.core.documents.schemas import DocumentCreate
from pigrocrm.core.fields.dynamic import describe_specs
from pigrocrm.core.fields.schemas import EntityType
from pigrocrm.core.fields.service import FieldDefinitionService
from pigrocrm.core.invoices.schemas import InvoiceCreate
from pigrocrm.core.people.schemas import PersonCreate
from pigrocrm.core.timetracking.schemas import CostCreate, TimeEntryCreate

ENTITY_TYPES: tuple[EntityType, ...] = (
    "customer",
    "person",
    "deal",
    "document",
    "invoice",
    "time_entry",
    "cost",
    "attivita",
    "contract",
)

CREATE_MODELS: dict[str, type[BaseModel]] = {
    "customer": CustomerCreate,
    "person": PersonCreate,
    "deal": DealCreate,
    "document": DocumentCreate,
    "invoice": InvoiceCreate,
    "time_entry": TimeEntryCreate,
    "cost": CostCreate,
    "attivita": AttivitaCreate,
    "contract": ContractCreate,
}

# Native columns an entity has that its Create schema does *not* declare, because they
# are derived or set only by a dedicated method. Empty for the entities whose
# writable surface is their whole surface; non-empty for `invoice`, whose fiscal
# columns are computed at emission and are exactly the names an administrator would
# slugify into by accident ("Totale" -> `totale`), and for `time_entry`/`cost`,
# whose billing-state columns (Task 4A-5's plan: `tariffa_applicata`,
# `costo_applicato`, `tariffa_origine`, `costo_origine`, `invoice_line_id`,
# `document_id`) are set only when a line is applied or a receipt attached, never on
# create -- A13's own residual note for this slice: check whether the guard needs
# the same explicit supplement `invoice` already required, and it does.
#
# `FieldDefinitionService.create` (Task 4A-2) reads this function before writing a
# definition, closing A13 for every entity listed here, including the fiscal columns
# `InvoiceCreate` itself never declares.
EXTRA_NATIVE_FIELDS: dict[str, tuple[str, ...]] = {
    "customer": (),
    "person": (),
    "deal": (),
    "document": (),
    "invoice": (
        "anno",
        "numero",
        "riferimento",
        "stato",
        "data_emissione",
        "data_scadenza",
        "competenza_da",
        "competenza_a",
        "tipo_documento",
        "divisa",
        "imponibile",
        "imposta",
        "bollo",
        "totale",
        "stato_pagamento",
        "data_incasso",
        "trasmessa_esternamente_il",
        "annullata_il",
        "motivo_annullamento",
        "origine_proforma_id",
    ),
    "time_entry": (
        "tariffa_applicata",
        "costo_applicato",
        "tariffa_origine",
        "costo_origine",
        "invoice_line_id",
    ),
    "cost": ("document_id",),
    # `stato`, `completata_il`, `origine` and `regola` are columns `AttivitaCreate`
    # deliberately does not declare: the state moves only through `complete`, `cancel`
    # and `reopen`, and `origine`/`regola` say who created the row -- a caller that
    # could set them could claim to be an automation. They are listed here so a
    # custom-field definition cannot be slugified onto one of them.
    "attivita": ("stato", "completata_il", "origine", "regola"),
    "contract": (),
}


def native_fields(entity_type: str) -> list[str]:
    """Derived from the Pydantic model, never hand-listed -- plus the columns that
    model cannot declare (see EXTRA_NATIVE_FIELDS). Order is stable: derived first, in
    field-definition order, then the extras in declaration order, so a caller can
    diff two runs."""
    derived = [name for name in CREATE_MODELS[entity_type].model_fields if name != "custom_fields"]
    extra = [name for name in EXTRA_NATIVE_FIELDS.get(entity_type, ()) if name not in derived]
    return derived + extra


def describe_entity(session: Session, entity_type: EntityType) -> dict[str, Any]:
    return {
        "entity_type": entity_type,
        "native_fields": native_fields(entity_type),
        "custom_fields": describe_specs(FieldDefinitionService(session).specs_for(entity_type)),
    }
