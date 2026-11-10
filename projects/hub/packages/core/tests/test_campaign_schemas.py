"""The shapes campaigns read, where a shape itself is the rule (no database)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rebase_core.campaigns.schemas import (
    NAME_IS_BLANK,
    CampaignDraft,
    CampaignPatch,
    CompaniesFilters,
    ScheduleRequest,
    TalentsFilters,
)

# What «Nuova campagna» sends with every filter filled: the same literals
# `apps/web/src/pages/admin/CreaCampagna.test.tsx` expects the page to post
# (`TALENTS_SENT`, `LEGAL_ENTITIES_SENT`, amounts with two decimals since REB-485). A field
# renamed or retyped on either side fails one of the two tests.
TALENTS_FILTERS = {
    "list": "talents",
    "status": "active",
    "q": "react",
    "job_title": "Frontend",
    "remote": "hybrid",
    "min_rate": "300.00",
    "max_rate": "500.00",
    "origin_page": "home",
    "utm_source": "linkedin",
    "has_cv": True,
    "with_logins": False,
    "created_from": "2026-01-01",
    "created_to": "2026-09-01",
}
LEGAL_ENTITIES_FILTERS = {
    "list": "companies",
    "status": "in_progress",
    "q": "block",
    "budget_min": "200.00",
    "budget_max": "400.00",
    "period_from": "2026-10-01",
    "origin": "home",
    "created_from": "2026-01-01",
    "created_to": "2026-09-01",
}


def test_excluded_are_plain_addresses_stripped_and_lowercased() -> None:
    """An address the list holds but `EmailStr` would reject (a legacy sign-up with two
    dots in a row) must not 422 the whole send: the unticked are compared, never mailed."""
    request = ScheduleRequest(excluded=[" Other@Studio.IT ", "odd..legacy@studio.it"])
    assert request.excluded == ["other@studio.it", "odd..legacy@studio.it"]


def test_the_wizards_talents_filters_are_the_servers_fields_and_types() -> None:
    assert set(TALENTS_FILTERS) == set(TalentsFilters.model_fields)
    filters = TalentsFilters.model_validate(TALENTS_FILTERS)
    assert (filters.min_rate, filters.max_rate) == (Decimal("300"), Decimal("500"))
    assert filters.created_from == datetime(2026, 1, 1) and filters.has_cv is True


def test_the_wizards_company_filters_are_the_servers_fields_and_types() -> None:
    assert set(LEGAL_ENTITIES_FILTERS) == set(CompaniesFilters.model_fields)
    filters = CompaniesFilters.model_validate(LEGAL_ENTITIES_FILTERS)
    assert filters.budget_min == Decimal("200") and filters.period_from == date(2026, 10, 1)
    assert filters.created_to == datetime(2026, 9, 1)


@pytest.mark.parametrize("amount", ["12.345", "-1", "1e3.5"])
def test_a_filter_amount_keeps_cents_at_most_and_is_never_negative(amount: str) -> None:
    """The editor reads a stored amount back with two decimals: a third would come back
    rounded, and the list with it (REB-526)."""
    with pytest.raises(ValidationError):
        TalentsFilters.model_validate({"list": "talents", "min_rate": amount})
    with pytest.raises(ValidationError):
        CompaniesFilters.model_validate({"list": "companies", "budget_max": amount})


def test_a_filter_amount_with_cents_is_kept_as_it_is_however_large() -> None:
    """No ceiling the editor's field does not have: an amount it sends is one this
    takes (CodeRabbit on #432)."""
    filters = TalentsFilters.model_validate({"list": "talents", "min_rate": "12.30"})
    assert filters.min_rate == Decimal("12.30")
    huge = CompaniesFilters.model_validate({"list": "companies", "budget_max": "123456789012.00"})
    assert huge.budget_max == Decimal("123456789012.00")


@pytest.mark.parametrize(
    "field",
    ["name", "source", "subject", "text", "button_text", "button_meta", "action"],
)
def test_a_patch_refuses_an_explicit_null_on_a_not_nullable_field_naming_it(field: str) -> None:
    """Every field of `_NOT_NULLABLE` is `NOT NULL` in `models.py`'s `Campaign` (REB-550,
    Part 1): an explicit `null` reaching `CampaignService.update`'s blanket `setattr`
    used to raise an `IntegrityError`, a 500, instead of a sentence naming the field."""
    with pytest.raises(ValidationError, match=field):
        CampaignPatch.model_validate({field: None})


def test_a_patch_leaving_a_field_out_is_not_the_same_as_sending_it_null() -> None:
    """Omitting a field is «no change», which every other field of an untouched patch
    already relies on: only an explicit `null` is refused. Pydantic skips a default
    value's own validators (`validate_default` is off, the default), so
    `_no_explicit_null_on_a_required_field` never runs for a field this patch leaves
    out -- if it did, every one of the seven fields below would raise, since none of
    them is set. That the patch validates at all pins it."""
    patch = CampaignPatch.model_validate({"subject": "Nuovo oggetto"})
    assert patch.name is None and patch.source is None and patch.action is None
    assert patch.text is None and patch.button_text is None and patch.button_meta is None


def test_a_patch_still_allows_an_explicit_null_on_a_field_the_row_may_hold_null() -> None:
    """`journey_status` and `filters` are legitimately `NULL` depending on `source`, so an
    explicit `null` on either must still pass."""
    patch = CampaignPatch.model_validate({"journey_status": None, "filters": None})
    assert patch.journey_status is None and patch.filters is None


DRAFT = {
    "source": "status",
    "journey_status": "missing_cv",
    "button_meta": "area",
    "action": "cv",
}


@pytest.mark.parametrize("name", ["", "   ", "\t \n"])
def test_a_blank_name_is_refused_on_create_and_on_update_with_a_sentence(name: str) -> None:
    """REB-524: `min_length=1` ran before the service stripped the name, so a name of
    spaces was stored empty. The name is stripped first and then checked, and the
    refusal is an Italian sentence naming the field, with `name` last in `loc` as the
    web reads it (`detail[].loc[-1]`)."""
    for shape, body in ((CampaignDraft, {**DRAFT, "name": name}), (CampaignPatch, {"name": name})):
        with pytest.raises(ValidationError) as refused:
            shape.model_validate(body)
        (error,) = refused.value.errors()
        assert error["loc"][-1] == "name"
        assert error["msg"] == NAME_IS_BLANK
        assert "nome" in NAME_IS_BLANK


def test_a_name_is_stored_without_its_surrounding_spaces() -> None:
    assert CampaignDraft.model_validate({**DRAFT, "name": "  Manca il CV "}).name == "Manca il CV"
    assert CampaignPatch.model_validate({"name": " Autunno  "}).name == "Autunno"
