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
