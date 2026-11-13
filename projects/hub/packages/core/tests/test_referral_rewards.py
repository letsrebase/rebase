"""The reward a referral matures into (P-REB-44, REB-658): the base formula, the first signed
notice that writes it once, the login or the signature that verifies, and the referrer's name
the hub gives the CRM for a contract.
"""

from decimal import Decimal

import pytest
from referral_fixtures import (
    SIGNED_AT,
    _card,
    _engagement_sent,
    _log_in,
    _member,
    _referral_of,
    _request,
    _sign,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.models import (
    HubEngagement,
    Referral,
    ReferralReward,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referrals import DAY_RATE, LUMP_SUM, ReferralService, reward_base

# ---- the reward's base -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "days", "expected"),
    [
        # A day rate, projected over the admin's own estimate.
        ("to_work_unit", 20, Decimal("7000")),
        # No estimate: one day's margin, the conservative floor.
        ("to_work_unit", None, Decimal("350")),
        # A lump sum compared against the client's own projected total.
        ("lump_sum", 20, Decimal("7000")),
        # A lump sum with nothing to project against: no reliable base.
        ("lump_sum", None, None),
        # A fee above the client's budget is rebase's own loss, never a negative reward.
        ("to_work_unit", 1, Decimal("0")),
    ],
)
def test_reward_base_formula(mode: str, days: int | None, expected: Decimal | None) -> None:
    budget = Decimal("800")
    fee = Decimal("450") if mode == "to_work_unit" or days is None else Decimal("9000")
    if mode == "to_work_unit" and days == 1:
        fee = Decimal("900")  # above the day's budget, on purpose
    assert reward_base(budget, fee, mode, days) == expected


def test_reward_base_compares_the_two_modes_it_stores() -> None:
    # Day rate: margin 800 - 450 over 20 days, not the lump-sum reading (800 * 20 - 450).
    assert reward_base(Decimal("800"), Decimal("450"), DAY_RATE, 20) == Decimal("7000")
    assert reward_base(Decimal("800"), Decimal("450"), DAY_RATE, None) == Decimal("350")
    # Lump sum: the fee against the client's projected total, none without an estimate.
    assert reward_base(Decimal("800"), Decimal("9000"), LUMP_SUM, 20) == Decimal("7000")
    assert reward_base(Decimal("800"), Decimal("9000"), LUMP_SUM, None) is None


def test_a_referred_freelancer_and_a_referred_company_each_earn_a_reward_once(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _log_in(clean_referrals, "wile@acme.it")
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)

    assert _sign(clean_referrals, key) is True

    rewards = clean_referrals.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
        .order_by(Referral.kind)
    ).all()
    by_kind = {clean_referrals.get(Referral, r.referral_id).kind: r for r in rewards}  # type: ignore[union-attr]
    assert set(by_kind) == {"company", "freelancer"}
    # Margin: (800 - 450) * 20 expected days = 7000.
    assert by_kind["freelancer"].rate == Decimal("0.1000")
    assert by_kind["freelancer"].base_amount == Decimal("7000.00")
    assert by_kind["freelancer"].reward_amount == Decimal("700.00")
    assert by_kind["company"].rate == Decimal("0.3000")
    assert by_kind["company"].base_amount == Decimal("7000.00")
    assert by_kind["company"].reward_amount == Decimal("2100.00")
    assert by_kind["freelancer"].status == "to_confirm"

    # Telling the hub again (a retried notice, a later one of the same signed engagement) never
    # doubles the reward.
    assert _sign(clean_referrals, key, sequence=2) is False
    assert _sign(clean_referrals, key, sequence=3) is True
    ReferralService(clean_referrals).record_reward_if_signed(
        clean_referrals.get(HubEngagement, key)  # type: ignore[arg-type]
    )
    clean_referrals.commit()
    again = clean_referrals.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
    ).all()
    assert len(again) == 2


@pytest.mark.parametrize(
    ("mode", "fee", "days", "base"),
    [
        # A day rate over the admin's estimate: (800 - 450) * 20.
        ("day", 450, 20, Decimal("7000.00")),
        # No estimate: one day's margin.
        ("day", 450, None, Decimal("350.00")),
        # An hourly fee is eight hours a day: (800 - 40 * 8) * 20.
        ("hour", 40, 20, Decimal("9600.00")),
        # A lump sum against the client's projected total: 800 * 20 - 9000.
        ("flat", 9000, 20, Decimal("7000.00")),
        # A lump sum with no estimate has no base: priced by hand on the ledger.
        ("flat", 9000, None, None),
        # A fee above the budget is rebase's own loss, never a negative reward.
        ("day", 900, 1, Decimal("0.00")),
    ],
)
def test_the_reward_written_at_the_signed_notice_takes_the_base_of_its_mode(
    clean_referrals: Session, mode: str, fee: int, days: int | None, base: Decimal | None
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    key = _engagement_sent(
        clean_referrals, freelancer_id, company_id, mode=mode, fee=fee, expected_days=days
    )

    _sign(clean_referrals, key)

    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    assert reward.engagement_id == key
    assert reward.base_amount == base
    assert reward.reward_amount == (
        None if base is None else (base * Decimal("0.1")).quantize(Decimal("0.01"))
    )


def test_a_referral_is_pending_when_it_is_made_and_has_nothing_to_confirm(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)

    for entity_id in (card_id, company_id):
        referral = _referral_of(clean_referrals, entity_id)
        assert (referral.status, referral.verified_at, referral.verified_via) == (
            "to_verify",
            None,
            None,
        )
    items = ReferralLedgerService(clean_referrals).list_rewards().items
    assert {(i.kind, i.referral_status, i.reward_id, i.status) for i in items} == {
        ("freelancer", "to_verify", None, None),
        ("company", "to_verify", None, None),
    }


def test_the_first_login_after_the_referral_verifies_it_and_only_the_loggers_own(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    ada = _card(clean_referrals, ref=code)
    grace = _card(clean_referrals, email="grace@studio.it", ref=code)
    wile = _request(clean_referrals, ref=code)

    _log_in(clean_referrals, "ada@studio.it")

    verified = _referral_of(clean_referrals, ada)
    assert (verified.status, verified.verified_via) == ("verified", "login")
    assert verified.verified_at is not None and verified.verified_at >= verified.created_at
    assert _referral_of(clean_referrals, grace).status == "to_verify"
    assert _referral_of(clean_referrals, wile).status == "to_verify"

    # A second login changes nothing: the proof is the first one.
    first = verified.verified_at
    _log_in(clean_referrals, "ada@studio.it")
    assert _referral_of(clean_referrals, ada).verified_at == first

    _log_in(clean_referrals, "wile@acme.it")
    assert _referral_of(clean_referrals, wile).status == "verified"


def test_a_login_from_before_the_referral_proves_nothing_about_it(clean_referrals: Session) -> None:
    """Someone posts the address of a member who already logged in, with an accomplice's
    code: the old login must not count the new referral at once."""
    _request(clean_referrals, email="ada@studio.it")
    _log_in(clean_referrals, "ada@studio.it")
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))

    card_id = _card(clean_referrals, email="ada@studio.it", ref=code)

    assert _referral_of(clean_referrals, card_id).status == "to_verify"
    _log_in(clean_referrals, "ada@studio.it")
    assert _referral_of(clean_referrals, card_id).status == "verified"


def test_to_squatted_company_earns_nothing_until_its_contact_person_logs_in_then_pays_the_squatter(
    clean_referrals: Session,
) -> None:
    """The squatting case, end to end. Someone posts a prospect's address with an
    accomplice's code. A freelancer is engaged with that company and signs; the real
    referente has never logged in, so nothing is owed. When they do, the attribution
    they never made counts, for the accomplice and for nobody else: the first writer wins
    an address's attribution (REB-646), the proof here is of the person, not of the code."""
    accomplice = ReferralService(clean_referrals).code_for(
        _member(clean_referrals, "accomplice@community.it")
    )
    honest = ReferralService(clean_referrals).code_for(
        _member(clean_referrals, "honest@community.it")
    )
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals, email="prospect@acme.it", ref=accomplice)
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)

    (ledger,) = ReferralLedgerService(clean_referrals).list_rewards().items
    assert (ledger.referral_status, ledger.engagement_id, ledger.projected_amount) == (
        "to_verify",
        None,
        None,
    )

    _sign(clean_referrals, key)
    assert clean_referrals.scalar(select(ReferralReward)) is None

    # The real person comes back with the code of someone who did refer them: no change.
    _request(clean_referrals, email="prospect@acme.it", ref=honest)
    assert clean_referrals.scalar(select(ReferralReward)) is None

    _log_in(clean_referrals, "prospect@acme.it")

    (item,) = ReferralLedgerService(clean_referrals).list_rewards().items
    assert item.referral_status == "verified" and item.verified_via == "login"
    assert item.referrer_email == "accomplice@community.it"
    assert (item.status, item.base_amount, item.reward_amount) == (
        "to_confirm",
        Decimal("7000.00"),
        Decimal("2100.00"),
    )
    assert item.engagement_id == key
    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    assert reward.engagement_id == key


def test_a_referred_freelancers_own_signature_verifies_them_and_writes_the_reward(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)
    # Before the signature the pending freelancer's reward is still projected: signing
    # is what verifies them. The pending company's is not.
    by_kind = {i.kind: i for i in ReferralLedgerService(clean_referrals).list_rewards().items}
    assert by_kind["freelancer"].projected_amount == Decimal("700.00")
    assert by_kind["company"].projected_amount is None and by_kind["company"].engagement_id is None

    _sign(clean_referrals, key)

    freelancer = _referral_of(clean_referrals, freelancer_id)
    assert (freelancer.status, freelancer.verified_via) == ("verified", "letter")
    assert freelancer.verified_at == SIGNED_AT
    assert _referral_of(clean_referrals, company_id).status == "to_verify"
    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    assert reward.referral_id == freelancer.id and reward.reward_amount == Decimal("700.00")


def test_a_login_after_the_signature_does_not_write_a_second_reward(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _sign(clean_referrals, _engagement_sent(clean_referrals, freelancer_id, company_id))

    _log_in(clean_referrals, "ada@studio.it")
    _log_in(clean_referrals, "wile@acme.it")

    assert len(clean_referrals.scalars(select(ReferralReward)).all()) == 2
    assert _referral_of(clean_referrals, freelancer_id).verified_via == "letter"
    assert _referral_of(clean_referrals, company_id).verified_via == "login"


def test_the_database_refuses_a_referral_half_verified(clean_referrals: Session) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)

    for statement in (
        "UPDATE referrals SET status = 'verified'",
        "UPDATE referrals SET verified_at = now()",
        "UPDATE referrals SET status = 'verified', verified_at = now()",
        "UPDATE referrals SET status = 'verified', verified_at = now(), verified_via = 'x'",
    ):
        with pytest.raises(IntegrityError):
            clean_referrals.execute(text(statement))
        clean_referrals.rollback()
    assert _referral_of(clean_referrals, card_id).status == "to_verify"


def test_an_unreferred_engagement_earns_no_reward(clean_referrals: Session) -> None:
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals)

    _sign(clean_referrals, _engagement_sent(clean_referrals, freelancer_id, company_id))

    assert clean_referrals.scalar(select(ReferralReward)) is None


def test_the_referrer_is_named_only_once_the_referral_is_verified(
    clean_referrals: Session,
) -> None:
    """REB-664: a `to_verify` referral may be squatted, so a contract must not state it:
    `referrer_name` answers `None` until the referred person is verified."""
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    service = ReferralService(clean_referrals)
    assert service.referrer_name("freelancer", freelancer_id) is None
    assert service.referrer_name("company", company_id) is None

    _log_in(clean_referrals, "ada@studio.it")

    assert service.referrer_name("freelancer", freelancer_id) == "Mario Rossi"
    assert service.referrer_name("company", company_id) is None

    _log_in(clean_referrals, "wile@acme.it")

    assert service.referrer_name("company", company_id) == "Mario Rossi"


def test_an_unreferred_freelancer_and_company_have_no_referrer_name(
    clean_referrals: Session,
) -> None:
    service = ReferralService(clean_referrals)

    assert service.referrer_name("freelancer", _card(clean_referrals)) is None
    assert service.referrer_name("company", _request(clean_referrals)) is None
