"""REB-1070: the taxpayer facts the INPS and flat-rate law read, on the fiscal profile.

Each is nullable (`None` = not stated), kept when a save leaves it out, and only a pack that reads
it may carry a value."""

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fiscal.schemas import (
    FLAT_RATE_ONLY,
    KEPT_WHEN_OMITTED,
    TAXPAYER_PARAMETERS,
    FiscalProfileUpsert,
)
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")

FULL = {
    "inps_category": "artigiani_commercianti",
    "inps_trade": "commercianti",
    "inps_before_1996": True,
    "flat_rate_inps_reduction": True,
    "ateco_group": "wholesale_and_retail_trade",
    "flat_rate_start_year": 2024,
    "startup_no_prior_activity": True,
    "startup_not_a_continuation": True,
    "startup_predecessor_within_limit": False,
}


def test_every_fact_is_kept_when_a_save_omits_it() -> None:
    assert set(TAXPAYER_PARAMETERS) <= set(KEPT_WHEN_OMITTED)
    stored = FiscalProfileUpsert(regime_code="RF19").stored_fields()
    assert not set(TAXPAYER_PARAMETERS) & set(stored)


def test_a_flat_rate_profile_persists_the_facts_and_a_later_save_keeps_them(
    db_session: Session,
) -> None:
    service = FiscalProfileService(db_session)
    saved = service.upsert(FiscalProfileUpsert(regime_code="RF19", **FULL), ADMIN)
    for key, value in FULL.items():
        assert getattr(saved, key) == value
    assert [group.key for group in saved.flat_rate_groups][:2] == [
        "food_and_beverage_industries",
        "wholesale_and_retail_trade",
    ]
    again = service.upsert(FiscalProfileUpsert(regime_code="RF19", due_days=45), ADMIN)
    assert again.due_days == 45
    for key, value in FULL.items():
        assert getattr(again, key) == value
    cleared = service.upsert(
        FiscalProfileUpsert(regime_code="RF19", ateco_group=None, flat_rate_start_year=None), ADMIN
    )
    assert cleared.ateco_group is None and cleared.flat_rate_start_year is None
    assert cleared.inps_trade == "commercianti"


def test_an_unknown_group_or_trade_is_refused() -> None:
    with pytest.raises(ValidationError):
        FiscalProfileUpsert(regime_code="RF19", ateco_group="nope")
    with pytest.raises(ValidationError):
        FiscalProfileUpsert(regime_code="RF19", inps_trade="liberi")


def test_the_ordinary_regime_takes_the_inps_facts_and_refuses_the_flat_rate_ones(
    db_session: Session,
) -> None:
    service = FiscalProfileService(db_session)
    saved = service.upsert(
        FiscalProfileUpsert(
            pack_id="it-standard",
            inps_category="gestione_separata",
            inps_covered_elsewhere=True,
        ),
        ADMIN,
    )
    assert saved.inps_covered_elsewhere is True
    for key, value in (
        ("ateco_group", "wholesale_and_retail_trade"),
        ("flat_rate_start_year", 2020),
    ):
        with pytest.raises(ValidationFailed) as refusal:
            service.upsert(FiscalProfileUpsert(pack_id="it-standard", **{key: value}), ADMIN)
        assert refusal.value.details["field"] == key
    assert "flat_rate_inps_reduction" in FLAT_RATE_ONLY


@pytest.mark.parametrize("pack_id", ["it-company", "non-it"])
def test_an_srl_and_a_company_abroad_have_none_of_them(db_session: Session, pack_id: str) -> None:
    service = FiscalProfileService(db_session)
    for key in ("inps_trade", "inps_before_1996", "ateco_group", "startup_no_prior_activity"):
        value = {
            "inps_trade": "artigiani",
            "inps_before_1996": True,
            "ateco_group": "wholesale_and_retail_trade",
            "startup_no_prior_activity": True,
        }[key]
        body = {"pack_id": pack_id, key: value}
        if pack_id == "non-it":
            body["default_vat_nature"] = "N2.2"
        with pytest.raises(ValidationFailed) as refusal:
            service.upsert(FiscalProfileUpsert(**body), ADMIN)
        assert refusal.value.details["field"] == key
    # Moving a profile that carried them to such a pack clears them.
    service.upsert(FiscalProfileUpsert(regime_code="RF19", **FULL), ADMIN)
    moved = service.upsert(
        FiscalProfileUpsert(
            pack_id=pack_id, **({"default_vat_nature": "N2.2"} if pack_id == "non-it" else {})
        ),
        ADMIN,
    )
    assert all(getattr(moved, key) is None for key in TAXPAYER_PARAMETERS)


def test_the_laws_own_pairings_are_enforced(db_session: Session) -> None:
    service = FiscalProfileService(db_session)
    for body, field in (
        ({"inps_category": "gestione_separata", "inps_trade": "artigiani"}, "inps_trade"),
        ({"inps_category": "gestione_separata", "inps_before_1996": True}, "inps_before_1996"),
        (
            {"inps_category": "artigiani_commercianti", "inps_covered_elsewhere": True},
            "inps_covered_elsewhere",
        ),
    ):
        with pytest.raises(ValidationFailed) as refusal:
            service.upsert(FiscalProfileUpsert(regime_code="RF19", **body), ADMIN)
        assert refusal.value.details["field"] == field


def test_a_pairing_is_read_on_the_row_a_save_leaves_alone(db_session: Session) -> None:
    """A save that names only the category is checked against the facts the row keeps."""
    service = FiscalProfileService(db_session)
    service.upsert(
        FiscalProfileUpsert(
            regime_code="RF19", inps_category="gestione_separata", inps_covered_elsewhere=True
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed) as refusal:
        service.upsert(
            FiscalProfileUpsert(regime_code="RF19", inps_category="artigiani_commercianti"), ADMIN
        )
    assert refusal.value.details["field"] == "inps_covered_elsewhere"
    with pytest.raises(ValidationFailed) as refusal:
        service.upsert(
            FiscalProfileUpsert(
                regime_code="RF19", inps_category="artigiani_commercianti", inps_trade="artigiani"
            ),
            ADMIN,
        )
    assert refusal.value.details["field"] == "inps_covered_elsewhere"
    # Both sides named together is a consistent save.
    saved = service.upsert(
        FiscalProfileUpsert(
            regime_code="RF19",
            inps_category="artigiani_commercianti",
            inps_trade="artigiani",
            inps_covered_elsewhere=None,
        ),
        ADMIN,
    )
    assert saved.inps_trade == "artigiani" and saved.inps_covered_elsewhere is None


def test_the_income_kind_is_asked_of_the_ordinary_person_only_and_kept(db_session: Session) -> None:
    """REB-1099: `income_kind` belongs to `it-standard`, is kept when a save leaves it out,
    and moving to another pack leaves no answer behind."""
    service = FiscalProfileService(db_session)
    saved = service.upsert(
        FiscalProfileUpsert(pack_id="it-standard", income_kind="business"), ADMIN
    )
    assert saved.income_kind == "business"
    assert service.snapshot().income_kind == "business"
    kept = service.upsert(FiscalProfileUpsert(pack_id="it-standard"), ADMIN)
    assert kept.income_kind == "business"
    others = (
        {"pack_id": "it-flat-rate", "regime_code": "RF19"},
        {"pack_id": "it-company"},
        {"pack_id": "non-it", "regime_code": None, "default_vat_rate": "22.00"},
    )
    for pack in others:
        with pytest.raises(ValidationFailed) as refusal:
            service.upsert(FiscalProfileUpsert(**pack, income_kind="business"), ADMIN)
        assert refusal.value.details["field"] == "income_kind"
    with pytest.raises(ValidationError):
        FiscalProfileUpsert(pack_id="it-standard", income_kind="freelance")
    moved = service.upsert(FiscalProfileUpsert(pack_id="it-company"), ADMIN)
    assert moved.income_kind is None


def test_the_reduction_facts_are_stored_with_the_month_as_its_first_day(
    db_session: Session,
) -> None:
    """REB-1097: any day of the month of the first enrolment names it, the first is stored."""
    service = FiscalProfileService(db_session)
    saved = service.upsert(
        FiscalProfileUpsert(
            pack_id="it-standard",
            inps_category="artigiani_commercianti",
            inps_trade="artigiani",
            inps_new_enrolment_reduction=True,
            inps_first_enrolment_month="2025-03-17",
        ),
        ADMIN,
    )
    assert saved.inps_new_enrolment_reduction is True
    assert str(saved.inps_first_enrolment_month) == "2025-03-01"
    again = service.upsert(FiscalProfileUpsert(pack_id="it-standard", due_days=45), ADMIN)
    assert str(again.inps_first_enrolment_month) == "2025-03-01"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        # The over-65 reduction is not for the flat-rate regime's 35% (art. 1 c. 80 L. 190/2014).
        (
            {"flat_rate_inps_reduction": True, "inps_over_65_reduction": True},
            "inps_over_65_reduction",
        ),
        (
            {
                "flat_rate_inps_reduction": True,
                "inps_new_enrolment_reduction": True,
                "inps_first_enrolment_month": "2025-05-01",
            },
            "inps_new_enrolment_reduction",
        ),
        (
            {
                "inps_over_65_reduction": True,
                "inps_new_enrolment_reduction": True,
                "inps_first_enrolment_month": "2025-05-01",
            },
            "inps_new_enrolment_reduction",
        ),
        # The month counts the 36 months, so the reduction needs it.
        ({"inps_new_enrolment_reduction": True}, "inps_first_enrolment_month"),
    ],
)
def test_the_three_reductions_exclude_each_other_and_the_enrolment_needs_its_month(
    db_session: Session, body: dict[str, object], field: str
) -> None:
    with pytest.raises(ValidationFailed) as refusal:
        FiscalProfileService(db_session).upsert(
            FiscalProfileUpsert(
                regime_code="RF19",
                inps_category="artigiani_commercianti",
                **body,  # type: ignore[arg-type]
            ),
            ADMIN,
        )
    assert refusal.value.details["field"] == field


@pytest.mark.parametrize(
    "key", ["inps_over_65_reduction", "inps_new_enrolment_reduction", "inps_first_enrolment_month"]
)
def test_the_reductions_are_for_artisans_and_traders_and_for_the_packs_that_read_them(
    db_session: Session, key: str
) -> None:
    service = FiscalProfileService(db_session)
    value: object = "2025-05-01" if key == "inps_first_enrolment_month" else True
    with pytest.raises(ValidationFailed) as refusal:
        service.upsert(
            FiscalProfileUpsert(
                regime_code="RF19",
                inps_category="gestione_separata",
                **{key: value},  # type: ignore[arg-type]
            ),
            ADMIN,
        )
    assert refusal.value.details["field"] == key
    with pytest.raises(ValidationFailed) as refusal:
        service.upsert(
            FiscalProfileUpsert(pack_id="it-company", **{key: value}),  # type: ignore[arg-type]
            ADMIN,
        )
    assert refusal.value.details["field"] == key
