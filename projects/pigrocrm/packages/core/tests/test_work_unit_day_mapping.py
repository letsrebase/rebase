"""REB-369: `work_units.day_mapping.propose_day_mapping`, pure and tested with
hand-built rate cards and candidate days rather than a real database -- mirroring
mastro's own `day-mapping.test.ts`, which draws the same line: the repository layer
(`invoices.import_review._propose_day_mappings`) is what actually reads eligible
days and rate cards, this module is exercised through `db_session`-free contract
and day models built once, never added or flushed.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from pigrocrm.core.contracts.models import RateCard
from pigrocrm.core.work_units.day_mapping import propose_day_mapping, resolve_rate_card
from pigrocrm.core.work_units.models import WorkUnit

CONTRACT_ID = uuid4()

DAILY_CARD = RateCard(
    contract_id=CONTRACT_ID,
    valido_da=date(2024, 1, 1),
    valido_a=None,
    tipo="giornaliero",
    importo=Decimal("600.00"),
    unita="giorno",
    frazioni_ammesse=[Decimal("1"), Decimal("0.5")],
)

HOURLY_CARD = RateCard(
    contract_id=CONTRACT_ID,
    valido_da=date(2024, 1, 1),
    valido_a=None,
    tipo="orario",
    importo=Decimal("90.00"),
    unita="ora",
    frazioni_ammesse=[Decimal("1")],
)


def test_an_unpriceable_picked_day_contributes_zero_and_importi_coincidono_is_false() -> None:
    """The design's own edge case: a picked day dated before the rate card's own
    `valido_da` prices as unknown, not zero-but-billable -- `sum_money` skips it
    rather than raising, so the proposal still stands (a complete quantity match),
    but `importo_proposto` undercounts and `importi_coincidono` is honestly `False`."""
    late_start_card = RateCard(
        contract_id=CONTRACT_ID,
        valido_da=date(2024, 2, 1),
        valido_a=None,
        tipo="giornaliero",
        importo=Decimal("600.00"),
        unita="giorno",
        frazioni_ammesse=[Decimal("1")],
    )
    unpriceable_day = uuid4()
    days = [_day(unpriceable_day, date(2024, 1, 25))]

    proposal = propose_day_mapping(
        Decimal("1"), Decimal("600.00"), date(2024, 3, 15), days, [late_start_card]
    )

    assert proposal is not None
    assert proposal.work_unit_ids == [unpriceable_day]
    assert proposal.importo_proposto == Decimal("0")
    assert proposal.importo_riga == Decimal("600.00")
    assert proposal.importi_coincidono is False


def _day(day_id: UUID, data: date, quantita: Decimal = Decimal("1")) -> WorkUnit:
    return WorkUnit(
        id=day_id,
        contract_id=CONTRACT_ID,
        data=data,
        quantita=quantita,
        descrizione="lavoro",
    )


D1, D2, D3 = uuid4(), uuid4(), uuid4()


def test_picks_the_oldest_eligible_days_first_up_to_the_line_quantity() -> None:
    days = [_day(D1, date(2024, 3, 1)), _day(D2, date(2024, 3, 4)), _day(D3, date(2024, 3, 10))]

    proposal = propose_day_mapping(
        Decimal("2"), Decimal("1200.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is not None
    assert proposal.work_unit_ids == [D1, D2]
    assert proposal.periodo_da == date(2024, 3, 1)
    assert proposal.periodo_a == date(2024, 3, 4)
    assert proposal.numero_giorni == 2
    assert proposal.importo_proposto == Decimal("1200.00")
    assert proposal.importo_riga == Decimal("1200.00")
    assert proposal.importi_coincidono is True


def test_importi_coincidono_is_false_when_the_rate_card_price_disagrees() -> None:
    days = [_day(D1, date(2024, 3, 1))]

    proposal = propose_day_mapping(
        Decimal("1"), Decimal("550.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is not None
    assert proposal.importo_proposto == Decimal("600.00")
    assert proposal.importo_riga == Decimal("550.00")
    assert proposal.importi_coincidono is False


def test_half_day_and_full_day_quantities_combine_to_an_exact_match() -> None:
    days = [_day(D1, date(2024, 3, 1), Decimal("0.5")), _day(D2, date(2024, 3, 2), Decimal("1"))]

    proposal = propose_day_mapping(
        Decimal("1.5"), Decimal("900.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is not None
    assert proposal.work_unit_ids == [D1, D2]
    assert proposal.numero_giorni == 2


def test_proposes_nothing_when_there_are_not_enough_eligible_days_to_reach_the_line_quantity() -> (
    None
):
    days = [_day(D1, date(2024, 3, 1))]

    proposal = propose_day_mapping(
        Decimal("3"), Decimal("1800.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is None


def test_proposes_nothing_when_the_running_total_overshoots_the_line_quantity() -> None:
    # No combination of two whole days sums to 1.5.
    days = [_day(D1, date(2024, 3, 1)), _day(D2, date(2024, 3, 2))]

    proposal = propose_day_mapping(
        Decimal("1.5"), Decimal("900.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is None


def test_never_picks_a_day_dated_after_the_invoice_issue_date() -> None:
    days = [_day(D1, date(2024, 3, 1)), _day(D2, date(2024, 4, 1))]

    proposal = propose_day_mapping(
        Decimal("2"), Decimal("1200.00"), date(2024, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is None


def test_proposes_nothing_for_a_contract_whose_rate_card_in_force_is_not_daily() -> None:
    days = [_day(D1, date(2024, 3, 1))]

    proposal = propose_day_mapping(
        Decimal("1"), Decimal("600.00"), date(2024, 3, 15), days, [HOURLY_CARD]
    )

    assert proposal is None


def test_proposes_nothing_when_no_rate_card_covers_the_issue_date_at_all() -> None:
    days = [_day(D1, date(2023, 3, 1))]

    proposal = propose_day_mapping(
        Decimal("1"), Decimal("600.00"), date(2023, 3, 15), days, [DAILY_CARD]
    )

    assert proposal is None


def test_resolves_the_rate_card_in_force_on_the_issue_date_not_the_first_one_in_the_list() -> None:
    old_daily_card = RateCard(
        contract_id=CONTRACT_ID,
        valido_da=date(2023, 1, 1),
        valido_a=date(2023, 12, 31),
        tipo="giornaliero",
        importo=Decimal("600.00"),
        unita="giorno",
        frazioni_ammesse=[Decimal("1")],
    )
    days = [_day(D1, date(2024, 3, 1))]

    # The card in force on 2024-03-15 is the hourly one, not the expired daily one.
    proposal = propose_day_mapping(
        Decimal("1"), Decimal("600.00"), date(2024, 3, 15), days, [old_daily_card, HOURLY_CARD]
    )

    assert proposal is None


# ---- resolve_rate_card -----------------------------------------------------------


def test_resolve_rate_card_is_none_when_no_card_covers_the_date() -> None:
    assert resolve_rate_card([DAILY_CARD], date(2023, 12, 31)) is None


def test_resolve_rate_card_treats_a_null_valido_a_as_still_open() -> None:
    assert resolve_rate_card([DAILY_CARD], date(2099, 1, 1)) is DAILY_CARD
