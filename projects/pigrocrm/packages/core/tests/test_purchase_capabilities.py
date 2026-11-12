"""What each pack lets a purchase carry (REB-1023): a flag is false only where the service
refuses the field, so the form never hides what the server would accept."""

import pytest

from pigrocrm.core.purchases.capabilities import purchase_capabilities


@pytest.mark.parametrize("pack_id", ["it-standard", "it-company", "non-it", None])
def test_every_pack_but_the_flat_rate_one_accepts_everything(pack_id: str | None) -> None:
    capabilities = purchase_capabilities(pack_id)

    assert (
        capabilities.vat_recap,
        capabilities.withholding,
        capabilities.reverse_charge,
        capabilities.split_payment,
    ) == (True, True, True, True)


def test_a_flat_rate_buyer_is_refused_a_withholding_and_nothing_else() -> None:
    capabilities = purchase_capabilities("it-flat-rate")

    assert capabilities.withholding is False
    assert (capabilities.vat_recap, capabilities.reverse_charge, capabilities.split_payment) == (
        True,
        True,
        True,
    )


def test_only_a_pack_with_income_variations_reads_the_deductibility_facts() -> None:
    """REB-1089: the deductibility kind and the asset group are read by the company pack's law;
    a buyer with no pack on record is not judged."""
    assert purchase_capabilities("it-company").deductibility is True
    assert purchase_capabilities(None).deductibility is True
    for pack_id in ("it-flat-rate", "it-standard", "non-it"):
        assert purchase_capabilities(pack_id).deductibility is False
