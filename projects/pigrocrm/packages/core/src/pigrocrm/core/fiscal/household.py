"""The household of an ordinary-regime taxpayer for one tax year (REB-1087, taxes design § 1).

The family detractions of art. 12 TUIR and the regional ones that depend on the household need
facts the profile did not hold: who is fiscally dependent on the taxpayer, since when and for what
share, and whether a person is disabled under art. 3 L. 104/1992. They are stated once per tax
year on the fiscal profile (`fiscal_profile.household`, a JSON document of this model) and read by
`law_irpef.FamilyDetractions` and `RegionalSurchargeLaw`.

What the model says, and what it leaves to the person
-----------------------------------------------------
A member is listed only if fiscally dependent on the taxpayer in the year: the person's own total
income, gross of deductible charges, is at most 2,840.51 euro, or 4,000 euro for a child of 24 or
less (art. 12 c. 2 TUIR). The form says so; the estimate does not ask for the income, since the
person who lists a member has already decided that. `share_percent` is the share of the detraction
the taxpayer is entitled to (a child's is split 50/50 between the parents, or goes whole to the
parent with the higher income by agreement: 100 or 50; an ascendant's is shared pro rata among
those entitled) and `months` the months of the year the person was dependent (art. 12 c. 3).

A household states one tax year (`year`). The estimate for another year does not use it: the ages,
the months and the people change, so a statement of 2026 says nothing about 2027.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemberKind = Literal["spouse", "child", "ascendant"]

HOUSEHOLD_YEAR_MIN = 2000
HOUSEHOLD_YEAR_MAX = 2200
HOUSEHOLD_MEMBERS_MAX = 20
MONTHS_IN_YEAR = 12


class HouseholdMember(BaseModel):
    """One person fiscally dependent on the taxpayer in the year: own total income, gross of
    deductible charges, within 2,840.51 euro, or 4,000 euro for a child of 24 or less (art. 12
    c. 2 TUIR)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemberKind
    # Needed for a child, whose age decides which detraction and which regional rule applies;
    # optional for the others, for which age plays no part.
    birth_date: date | None = Field(default=None, description="Required for a child.")
    disabled: bool = Field(default=False, description="Disabled under art. 3 L. 104/1992.")
    share_percent: int = Field(
        default=100,
        ge=1,
        le=100,
        description=(
            "The share of the detraction the taxpayer is entitled to: 100 or 50 for a child "
            "(split between the parents, or whole to the higher income by agreement), 100 for "
            "the spouse, pro rata among those entitled for an ascendant."
        ),
    )
    months: int = Field(
        default=MONTHS_IN_YEAR,
        ge=1,
        le=MONTHS_IN_YEAR,
        description="The months of the year the person was dependent.",
    )

    @model_validator(mode="after")
    def _shape(self) -> HouseholdMember:
        if self.kind == "child":
            if self.birth_date is None:
                raise ValueError("indica la data di nascita del figlio")
            if self.share_percent not in (50, 100):
                raise ValueError("per un figlio la quota è il 100 o il 50 per cento")
        elif self.kind == "spouse" and self.share_percent != 100:
            raise ValueError("la detrazione per il coniuge non si ripartisce")
        return self


class Household(BaseModel):
    """The people fiscally dependent on the taxpayer in tax year `year`, which the IRPEF's family
    detractions and some regions' surcharge rules read. A statement of one year says nothing
    about another."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int = Field(ge=HOUSEHOLD_YEAR_MIN, le=HOUSEHOLD_YEAR_MAX)
    members: list[HouseholdMember] = Field(default_factory=list, max_length=HOUSEHOLD_MEMBERS_MAX)
    # Art. 3 L. 104/1992 for the taxpayer: the Veneto's reduced regional rate reads it.
    taxpayer_disabled: bool = Field(
        default=False, description="The taxpayer is disabled under art. 3 L. 104/1992."
    )
    other_supporters_income: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
        description=(
            "The sum of the taxable incomes, for the regional surcharge, of everyone else a "
            "disabled dependant is also dependent on: Veneto and Marche test it with the "
            "taxpayer's own. Null is not stated, 0 is nobody else."
        ),
    )

    @model_validator(mode="after")
    def _shape(self) -> Household:
        if sum(member.kind == "spouse" for member in self.members) > 1:
            raise ValueError("il coniuge è uno solo")
        for member in self.members:
            if member.birth_date is not None and member.birth_date.year > self.year:
                raise ValueError("una persona nata dopo l'anno non è a carico in quell'anno")
        return self

    @property
    def spouse(self) -> HouseholdMember | None:
        return next((member for member in self.members if member.kind == "spouse"), None)

    @property
    def children(self) -> list[HouseholdMember]:
        return [member for member in self.members if member.kind == "child"]

    @property
    def ascendants(self) -> list[HouseholdMember]:
        return [member for member in self.members if member.kind == "ascendant"]

    @property
    def has_disabled_dependant(self) -> bool:
        return any(member.disabled for member in self.members)


def age_on(birth: date, day: date) -> int:
    """Whole years of age on `day`, negative before the birth."""
    years = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    return years if day >= birth else -1


def months_in_age_band(
    birth: date, year: int, *, minimum: int, below: int | None, day_of_month: int | None = None
) -> int:
    """The months of `year` in which the person is at least `minimum` and under `below` years
    old (`None`: no upper limit), the age being read on the last day of the month or, with
    `day_of_month`, on that day of it (capped at the month's length). A month the condition
    starts in counts, as art. 12 c. 3 TUIR says the detractions run «dal mese in cui si sono
    verificate» the conditions; the month a limit is reached in does not count for the upper
    limit, which is the reading the estimate takes (the A5 register names it)."""
    months = 0
    for month in range(1, MONTHS_IN_YEAR + 1):
        last = calendar.monthrange(year, month)[1]
        day = date(year, month, last if day_of_month is None else min(day_of_month, last))
        age = age_on(birth, day)
        if age >= minimum and (below is None or age < below):
            months += 1
    return months


__all__ = [
    "Household",
    "HouseholdMember",
    "MemberKind",
    "age_on",
    "months_in_age_band",
]
