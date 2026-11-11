"""What an address says about where it is: the region and the municipality codes (REB-1069).

Derived at read, never stored. The address's own text (`city`, `province`) stays the one
fact, so there is nothing to keep in step with it; the list of municipalities is a file
that a release replaces when ISTAT publishes a change, and every address is read against
the new list with no migration. The save never depends on the answer: an address that no
municipality matches is a legal entity like any other, and its read says `unknown`.
"""

from typing import Literal

from pydantic import BaseModel, Field

from pigrocrm.core.places.municipalities import municipality_list

PlaceStatus = Literal["matched", "unknown", "not_italian"]


class Place(BaseModel):
    """Where an Italian address is, as ISTAT's list of municipalities names it.

    `matched`: the city is a municipality of the province; every field is filled. `unknown`:
    it is not (or no city or province was given); the region is still filled when the
    province is one ISTAT lists, since a province belongs to one region, and the rest is
    `null`. `not_italian`: the country is not Italy, so none of this applies.
    """

    status: PlaceStatus = Field(
        description=(
            "`matched` when the city is a municipality of the province, `unknown` when it is "
            "not, `not_italian` when the country is not IT"
        )
    )
    municipality_code: str | None = Field(
        default=None, description="ISTAT's six-digit code of the municipality"
    )
    cadastral_code: str | None = Field(
        default=None, description="The municipality's cadastral (Belfiore) code, such as F205"
    )
    municipality_name: str | None = Field(
        default=None, description="The municipality's name as ISTAT writes it"
    )
    region_code: str | None = Field(
        default=None, description="ISTAT's two-digit code of the region, 01 to 20"
    )
    region_name: str | None = Field(
        default=None, description="The region's name as ISTAT writes it"
    )


def locate(city: str | None, province: str | None, country: str | None) -> Place:
    """The place a legal entity's address names. Case, accents and apostrophes of the city do
    not matter and the province is its two-letter abbreviation."""
    if (country or "IT").strip().upper() != "IT":
        return Place(status="not_italian")
    places = municipality_list()
    found = places.match(city, province)
    if found is not None:
        return Place(
            status="matched",
            municipality_code=found.istat_code,
            cadastral_code=found.cadastral_code,
            municipality_name=found.name,
            region_code=found.region_code,
            region_name=found.region_name,
        )
    region = places.region_of(province)
    return Place(
        status="unknown",
        region_code=region.code if region else None,
        region_name=region.name if region else None,
    )


class MunicipalityOption(BaseModel):
    """One municipality of a province, as the picker beside an address offers it."""

    municipality_code: str = Field(description="ISTAT's six-digit code of the municipality")
    cadastral_code: str = Field(description="The municipality's cadastral (Belfiore) code")
    name: str = Field(
        description="The name to write in the address: it matches this municipality exactly"
    )


def municipalities_of(province: str) -> list[MunicipalityOption]:
    """The municipalities of a province by name; empty for an abbreviation ISTAT's list does
    not carry."""
    return [
        MunicipalityOption(
            municipality_code=m.istat_code, cadastral_code=m.cadastral_code, name=m.name
        )
        for m in municipality_list().in_province(province)
    ]
