"""What the buyer's fiscal pack lets a purchase carry (REB-1023), for the form to hide the rest.

The form shows a control only when the server would accept a value in it, so each flag here
mirrors a refusal `PurchaseService._check_document` really makes and nothing else: a flag is
`False` only where the service refuses the field for that pack, never for what a regime's name
suggests. A rule the service does not enforce is not invented here; if the service starts to
refuse one, the flag changes with it, in this file.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.pack import PACK_IT_FLAT_RATE
from pigrocrm.core.fiscal.repository import FiscalProfileRepository
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.purchases.schemas import PurchaseCapabilitiesRead


@dataclass(frozen=True)
class PurchaseCapabilities:
    """One flag per control; `True` means the pack accepts it (for the kinds that carry it)."""

    vat_recap: bool
    withholding: bool
    reverse_charge: bool
    split_payment: bool


def purchase_capabilities(pack_id: str | None) -> PurchaseCapabilities:
    """The capabilities of a buyer on `pack_id`; `None` is a buyer with no pack on record (a
    shared purchase, or an entity with no profile), for which the service refuses nothing.

    - `vat_recap`: always true. An invoice or a credit note must carry a VAT recap whatever
      the pack (`_check_vat` refuses one without lines, and accepts lines on every pack), so
      hiding it would make the document unregisterable. A receipt or an expense has none,
      by its kind and not by its pack.
    - `withholding`: false only on `it-flat-rate`. A flat-rate taxpayer is not a withholding
      agent (art. 1 c. 69 L. 190/2014), so `_check_withholding` refuses it; every other pack
      accepts it.
    - `reverse_charge`: always true. `_check_vat` accepts the flag on an invoice or a credit
      note of any pack, and `purchase_cost` prices it for each.
    - `split_payment`: always true. `_check_vat` accepts the flag on an invoice or a credit
      note of any pack.
    """
    return PurchaseCapabilities(
        vat_recap=True,
        withholding=pack_id != PACK_IT_FLAT_RATE,
        reverse_charge=True,
        split_payment=True,
    )


def read_purchase_capabilities(
    session: Session, legal_entity_id: UUID | None
) -> PurchaseCapabilitiesRead:
    """The capabilities of the buyer `legal_entity_id` names, which must be one the caller sees
    (`NotFound` otherwise, as a purchase naming it would be). Without one, the buyer of a
    supplier's document that names none (`PurchaseService._only_legal_entity`): the space's only
    active legal entity; when there is none or several, no buyer is resolved and every flag is
    true, since the server is then the judge of what it refuses."""
    entities = LegalEntityService(session)
    if legal_entity_id is not None:
        buyer: UUID | None = entities.resolve(legal_entity_id).id
    else:
        try:
            only = entities.single()
        except NotFound:
            only = None
        buyer = only.id if only is not None else None
    profile = FiscalProfileRepository(session).get(buyer) if buyer is not None else None
    capabilities = purchase_capabilities(profile.pack_id if profile is not None else None)
    return PurchaseCapabilitiesRead(
        legal_entity_id=buyer,
        vat_recap=capabilities.vat_recap,
        withholding=capabilities.withholding,
        reverse_charge=capabilities.reverse_charge,
        split_payment=capabilities.split_payment,
    )
