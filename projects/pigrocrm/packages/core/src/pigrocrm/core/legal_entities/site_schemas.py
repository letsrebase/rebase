"""The shapes of a legal entity's sites (REB-1092): places of business beside its registered
office, which decide how the IRAP base is split between regions."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.validation import SafeStr

REGION_MAX_LENGTH = 2
CITY_MAX_LENGTH = 120
ADDRESS_MAX_LENGTH = 255


class LegalEntitySiteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: SafeStr = Field(
        max_length=REGION_MAX_LENGTH,
        description=(
            "The key of the region in the IRAP rate table: ISTAT's two-digit region code, or "
            "`BZ` or `TN` for the two autonomous provinces (`GET /api/legal-entities/regions`)"
        ),
    )
    city: SafeStr | None = Field(
        default=None, max_length=CITY_MAX_LENGTH, description="The municipality, for the reader"
    )
    address: SafeStr | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)


class LegalEntitySiteUpdate(BaseModel):
    """What changes; `null` empties the city or the address, and the region cannot be emptied."""

    model_config = ConfigDict(extra="forbid")

    region: SafeStr | None = Field(default=None, max_length=REGION_MAX_LENGTH)
    city: SafeStr | None = Field(default=None, max_length=CITY_MAX_LENGTH)
    address: SafeStr | None = Field(default=None, max_length=ADDRESS_MAX_LENGTH)


class LegalEntitySiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    legal_entity_id: UUID
    region: str
    region_name: str | None = Field(
        default=None,
        description="The region's name as the IRAP rate table writes it; `null` for an unknown key",
    )
    city: str | None
    address: str | None
    staff_count: int = Field(
        default=0, description="How many staff members are assigned to this site"
    )
    created_at: datetime
    updated_at: datetime


class IrapRegionOption(BaseModel):
    """One region a site can be in."""

    key: str = Field(description="`LegalEntitySiteCreate.region` for this region")
    name: str


__all__ = [
    "IrapRegionOption",
    "LegalEntitySiteCreate",
    "LegalEntitySiteRead",
    "LegalEntitySiteUpdate",
]
