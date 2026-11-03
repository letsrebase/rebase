"""Names every module of the invoice domain shares."""

from decimal import Decimal

from pigrocrm.core.fields.schemas import EntityType

ENTITY: EntityType = "invoice"
ZERO = Decimal("0.00")
