"""REB-1087: the household the taxpayer states, on the fiscal profile.

The model refuses what the law cannot read (a child with no birth date, two spouses, a share
the detraction does not take), the profile keeps it when a save leaves it out, and only the
ordinary regime's person carries one."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.household import Household, HouseholdMember, age_on, months_in_age_band
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")

STATED = {
    "year": 2026,
    "members": [
        {"kind": "spouse", "months": 8},
        {"kind": "child", "birth_date": "2001-06-15", "disabled": True, "share_percent": 50},
        {"kind": "ascendant", "share_percent": 40},
    ],
    "taxpayer_disabled": False,
    "other_supporters_income": "12000.50",
}


def test_a_child_needs_a_birth_date_and_a_share_of_100_or_50() -> None:
    with pytest.raises(ValidationError, match="data di nascita"):
        HouseholdMember(kind="child")
    with pytest.raises(ValidationError, match="100 o il 50"):
        HouseholdMember(kind="child", birth_date=date(2001, 1, 1), share_percent=60)
    assert HouseholdMember(kind="child", birth_date=date(2001, 1, 1), share_percent=50)


def test_a_spouse_is_one_and_not_shared_and_an_ascendant_takes_any_share() -> None:
    with pytest.raises(ValidationError, match="non si ripartisce"):
        HouseholdMember(kind="spouse", share_percent=50)
    spouse = {"kind": "spouse"}
    with pytest.raises(ValidationError, match="uno solo"):
        Household.model_validate({"year": 2026, "members": [spouse, spouse]})
    assert HouseholdMember(kind="ascendant", share_percent=33).share_percent == 33


def test_a_person_born_after_the_year_is_not_dependent_in_it() -> None:
    with pytest.raises(ValidationError, match="nata dopo l'anno"):
        Household.model_validate(
            {"year": 2026, "members": [{"kind": "child", "birth_date": "2027-01-02"}]}
        )


def test_the_months_are_one_to_twelve_and_the_members_at_most_twenty() -> None:
    for months in (0, 13):
        with pytest.raises(ValidationError):
            HouseholdMember(kind="spouse", months=months)
    with pytest.raises(ValidationError):
        Household.model_validate({"year": 2026, "members": [{"kind": "ascendant"}] * 21})


def test_the_age_is_read_on_a_day_and_the_band_counts_the_months_from_its_start() -> None:
    assert age_on(date(2005, 3, 10), date(2026, 3, 9)) == 20
    assert age_on(date(2005, 3, 10), date(2026, 3, 10)) == 21
    assert age_on(date(2026, 3, 10), date(2026, 3, 1)) == -1
    # 21 on 10 March: March counts, being the month the age is reached, so ten months.
    assert months_in_age_band(date(2005, 3, 10), 2026, minimum=21, below=30) == 10
    # Under 18 read on the 15th: a birthday on the 20th of July leaves seven months, on the
    # 15th six.
    assert months_in_age_band(date(2008, 7, 20), 2026, minimum=0, below=18, day_of_month=15) == 7
    assert months_in_age_band(date(2008, 7, 15), 2026, minimum=0, below=18, day_of_month=15) == 6


def test_the_household_is_stored_as_json_and_kept_when_a_save_leaves_it_out(
    db_session: Session,
) -> None:
    service = FiscalProfileService(db_session)
    saved = service.upsert(FiscalProfileUpsert(pack_id="it-standard", household=STATED), ADMIN)
    assert saved.household is not None and saved.household.year == 2026
    assert [member.kind for member in saved.household.members] == ["spouse", "child", "ascendant"]
    assert saved.household.other_supporters_income == Decimal("12000.50")
    child = saved.household.members[1]
    assert child.birth_date == date(2001, 6, 15) and child.disabled and child.share_percent == 50
    kept = service.upsert(FiscalProfileUpsert(pack_id="it-standard", due_days=45), ADMIN)
    assert kept.household == saved.household
    cleared = service.upsert(FiscalProfileUpsert(pack_id="it-standard", household=None), ADMIN)
    assert cleared.household is None


def test_only_the_ordinary_regimes_person_has_a_household(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    for body in (
        {"regime_code": "RF19"},
        {"pack_id": "it-company"},
        {"pack_id": "non-it", "default_vat_nature": "N2.2"},
    ):
        with pytest.raises(ValidationFailed) as refusal:
            service.upsert(FiscalProfileUpsert(household=STATED, **body), ADMIN)
        assert refusal.value.details["field"] == "household"
    service.upsert(FiscalProfileUpsert(pack_id="it-standard", household=STATED), ADMIN)
    # Moving to a pack that has none clears it, as the INPS facts are.
    moved = service.upsert(FiscalProfileUpsert(regime_code="RF19"), ADMIN)
    assert moved.household is None
