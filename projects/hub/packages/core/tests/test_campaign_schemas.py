"""The shapes campaigns read, where a shape itself is the rule (no database)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rebase_core.campaigns.schemas import (
    NAME_IS_BLANK,
    AziendeFiltri,
    CampaignDraft,
    CampaignPatch,
    ScheduleRequest,
    TalentiFiltri,
)

# What «Nuova campagna» sends with every filter filled: the same literals
# `apps/web/src/pages/admin/CreaCampagna.test.tsx` expects the page to post
# (`TALENTI_SENT`, `AZIENDE_SENT`, amounts with two decimals since REB-485). A field
# renamed or retyped on either side fails one of the two tests.
TALENTI_FILTRI = {
    "lista": "talenti",
    "stato": "attivo",
    "q": "react",
    "posizione": "Frontend",
    "remoto": "ibrido",
    "tariffa_min": "300.00",
    "tariffa_max": "500.00",
    "origine": "home",
    "utm_source": "linkedin",
    "has_cv": True,
    "con_accessi": False,
    "creato_da": "2026-01-01",
    "creato_a": "2026-09-01",
}
AZIENDE_FILTRI = {
    "lista": "aziende",
    "stato": "in_corso",
    "q": "block",
    "budget_min": "200.00",
    "budget_max": "400.00",
    "periodo_da": "2026-10-01",
    "origine": "home",
    "creato_da": "2026-01-01",
    "creato_a": "2026-09-01",
}


def test_esclusi_are_plain_addresses_stripped_and_lowercased() -> None:
    """An address the list holds but `EmailStr` would reject (a legacy sign-up with two
    dots in a row) must not 422 the whole send: the unticked are compared, never mailed."""
    request = ScheduleRequest(esclusi=[" Other@Studio.IT ", "odd..legacy@studio.it"])
    assert request.esclusi == ["other@studio.it", "odd..legacy@studio.it"]


def test_the_wizards_talenti_filters_are_the_servers_fields_and_types() -> None:
    assert set(TALENTI_FILTRI) == set(TalentiFiltri.model_fields)
    filtri = TalentiFiltri.model_validate(TALENTI_FILTRI)
    assert (filtri.tariffa_min, filtri.tariffa_max) == (Decimal("300"), Decimal("500"))
    assert filtri.creato_da == datetime(2026, 1, 1) and filtri.has_cv is True


def test_the_wizards_company_filters_are_the_servers_fields_and_types() -> None:
    assert set(AZIENDE_FILTRI) == set(AziendeFiltri.model_fields)
    filtri = AziendeFiltri.model_validate(AZIENDE_FILTRI)
    assert filtri.budget_min == Decimal("200") and filtri.periodo_da == date(2026, 10, 1)
    assert filtri.creato_a == datetime(2026, 9, 1)


@pytest.mark.parametrize("amount", ["12.345", "-1", "1e3.5"])
def test_a_filter_amount_keeps_cents_at_most_and_is_never_negative(amount: str) -> None:
    """The editor reads a stored amount back with two decimals: a third would come back
    rounded, and the list with it (REB-526)."""
    with pytest.raises(ValidationError):
        TalentiFiltri.model_validate({"lista": "talenti", "tariffa_min": amount})
    with pytest.raises(ValidationError):
        AziendeFiltri.model_validate({"lista": "aziende", "budget_max": amount})


def test_a_filter_amount_with_cents_is_kept_as_it_is_however_large() -> None:
    """No ceiling the editor's field does not have: an amount it sends is one this
    takes (CodeRabbit on #432)."""
    filtri = TalentiFiltri.model_validate({"lista": "talenti", "tariffa_min": "12.30"})
    assert filtri.tariffa_min == Decimal("12.30")
    huge = AziendeFiltri.model_validate({"lista": "aziende", "budget_max": "123456789012.00"})
    assert huge.budget_max == Decimal("123456789012.00")


@pytest.mark.parametrize(
    "field",
    ["nome", "fonte", "oggetto", "testo", "bottone_testo", "bottone_meta", "azione"],
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
    patch = CampaignPatch.model_validate({"oggetto": "Nuovo oggetto"})
    assert patch.nome is None and patch.fonte is None and patch.azione is None
    assert patch.testo is None and patch.bottone_testo is None and patch.bottone_meta is None


def test_a_patch_still_allows_an_explicit_null_on_a_field_the_row_may_hold_null() -> None:
    """`stato_percorso` and `filtri` are legitimately `NULL` depending on `fonte`, so an
    explicit `null` on either must still pass."""
    patch = CampaignPatch.model_validate({"stato_percorso": None, "filtri": None})
    assert patch.stato_percorso is None and patch.filtri is None


DRAFT = {
    "fonte": "stato",
    "stato_percorso": "manca_cv",
    "bottone_meta": "area",
    "azione": "cv",
}


@pytest.mark.parametrize("nome", ["", "   ", "\t \n"])
def test_a_blank_name_is_refused_on_create_and_on_update_with_a_sentence(nome: str) -> None:
    """REB-524: `min_length=1` ran before the service stripped the name, so a name of
    spaces was stored empty. The name is stripped first and then checked, and the
    refusal is an Italian sentence naming the field, with `nome` last in `loc` as the
    web reads it (`detail[].loc[-1]`)."""
    for shape, body in ((CampaignDraft, {**DRAFT, "nome": nome}), (CampaignPatch, {"nome": nome})):
        with pytest.raises(ValidationError) as refused:
            shape.model_validate(body)
        (error,) = refused.value.errors()
        assert error["loc"][-1] == "nome"
        assert error["msg"] == NAME_IS_BLANK
        assert "nome" in NAME_IS_BLANK


def test_a_name_is_stored_without_its_surrounding_spaces() -> None:
    assert CampaignDraft.model_validate({**DRAFT, "nome": "  Manca il CV "}).nome == "Manca il CV"
    assert CampaignPatch.model_validate({"nome": " Autunno  "}).nome == "Autunno"
