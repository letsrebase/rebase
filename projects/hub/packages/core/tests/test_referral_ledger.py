"""The ledger that shows referrals (P-REB-44): projected then real figures over the hub's
engagements, the referrer named, a cancelled engagement, and the handful of queries a page costs.
"""

from decimal import Decimal

import pytest
from referral_fixtures import (
    _card,
    _engagement_sent,
    _log_in,
    _member,
    _referred_pair,
    _request,
    _sign,
    _statements,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.freelancers import FreelancerService
from rebase_core.models import (
    Freelancer,
    HubEngagement,
    Referral,
    ReferralReward,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referral_schemas import ReferralLedgerItem
from rebase_core.referral_settings import ReferralSettingsService
from rebase_core.referrals import ReferralService


@pytest.mark.parametrize(
    ("state", "mode", "days", "expected"),
    [
        ("draft", "day", 20, Decimal("700.00")),
        ("awaiting_signature", "day", None, Decimal("35.00")),
        ("draft", "flat", 20, Decimal("1555.00")),
        ("draft", "flat", None, None),
        # An hourly fee is eight hours a day: 450 * 8 is above the day's budget, so nothing.
        ("draft", "hour", 20, Decimal("0.00")),
        ("cancelled", "day", 20, None),
        ("handoff", "day", 20, None),
        ("active", "day", 20, None),
        ("concluded", "day", 20, None),
    ],
)
def test_project_reward_follows_reward_base_and_only_while_a_signature_can_still_land(
    state: str, mode: str, days: int | None, expected: Decimal | None
) -> None:
    engagement = HubEngagement(
        state=state,
        mode=mode,
        fee=Decimal("450") if mode != "flat" else Decimal("450"),
        company_daily_budget=Decimal("800"),
        expected_days=Decimal(days) if days is not None else None,
    )
    assert ReferralService.project_reward(engagement, Decimal("0.10")) == expected


def _by_kind(session: Session) -> dict[str, ReferralLedgerItem]:
    return {item.kind: item for item in ReferralLedgerService(session).list_rewards().items}


def test_the_ledger_shows_a_referral_projected_then_real_then_by_state(
    clean_referrals: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)

    before = _by_kind(clean_referrals)
    assert [
        (i.referrer_name, i.reward_id, i.projected_rate, i.projected_amount)
        for i in (before["freelancer"], before["company"])
    ] == [
        ("Mario Rossi", None, Decimal("0.1000"), Decimal("700.00")),
        ("Mario Rossi", None, Decimal("0.3000"), Decimal("2100.00")),
    ]
    assert before["freelancer"].engagement_id == key

    _sign(clean_referrals, key)
    # A rate edited after the signature never moves what the signature already earned.
    ReferralSettingsService(clean_referrals).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    company_reward = clean_referrals.scalar(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.kind == "company")
    )
    assert company_reward is not None
    ReferralLedgerService(clean_referrals).set_state(company_reward.id, "confirmed", admin_id)

    after = _by_kind(clean_referrals)
    assert (after["freelancer"].status, after["freelancer"].rate) == (
        "to_confirm",
        Decimal("0.1000"),
    )
    assert after["freelancer"].reward_amount == Decimal("700.00")
    assert (after["company"].status, after["company"].reward_amount) == (
        "confirmed",
        Decimal("2100.00"),
    )
    assert after["company"].reward_id == company_reward.id
    assert (after["company"].projected_rate, after["company"].projected_amount) == (None, None)


def test_a_referral_that_already_paid_on_another_engagement_projects_nothing_on_a_new_one(
    clean_referrals: Session,
) -> None:
    _, _, freelancer_id, company_id = _referred_pair(clean_referrals)
    first = _engagement_sent(clean_referrals, freelancer_id, company_id)
    _sign(clean_referrals, first)
    other_company = _request(clean_referrals, email="road@runner.it", company_name="Road Runner")
    _engagement_sent(clean_referrals, freelancer_id, other_company)

    items = ReferralLedgerService(clean_referrals).list_rewards().items

    # Both referrals earned on the first engagement; the second one is not shown for either,
    # and the other company was never referred.
    assert {i.kind: (i.status, i.engagement_id) for i in items} == {
        "freelancer": ("to_confirm", first),
        "company": ("to_confirm", first),
    }
    assert all((i.projected_rate, i.projected_amount) == (None, None) for i in items)


def test_an_engagement_with_no_referral_lists_none_and_an_unpriceable_one_projects_nothing(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    plain_id = _card(clean_referrals, email="plain@studio.it")
    referred_id = _card(clean_referrals, email="referred@studio.it", ref=code)
    company_id = _request(clean_referrals)
    _engagement_sent(clean_referrals, plain_id, company_id)
    lump = _engagement_sent(
        clean_referrals, referred_id, company_id, mode="flat", expected_days=None
    )

    (item,) = ReferralLedgerService(clean_referrals).list_rewards().items

    assert item.referred_id == referred_id
    assert item.engagement_id == lump
    assert (item.projected_rate, item.projected_amount) == (Decimal("0.1000"), None)


def test_a_cancelled_engagement_still_names_its_referral_but_promises_no_amount(
    clean_referrals: Session,
) -> None:
    _, _, freelancer_id, company_id = _referred_pair(clean_referrals)
    _engagement_sent(clean_referrals, freelancer_id, company_id, state="cancelled")

    items = ReferralLedgerService(clean_referrals).list_rewards().items

    assert {i.kind for i in items} == {"freelancer", "company"}
    assert all(i.referrer_name == "Mario Rossi" for i in items)
    assert all(
        (i.engagement_id, i.projected_rate, i.projected_amount) == (None, None, None) for i in items
    )


def test_a_referrer_with_a_card_of_his_own_is_named_with_it(clean_referrals: Session) -> None:
    referrer_card = _card(clean_referrals, email="ref@studio.it")
    referrer_user = clean_referrals.scalar(
        select(Freelancer.user_id).where(Freelancer.id == referrer_card)
    )
    assert referrer_user is not None
    code = ReferralService(clean_referrals).code_for(referrer_user)
    _card(clean_referrals, email="referred@studio.it", ref=code)

    (ledger,) = ReferralLedgerService(clean_referrals).list_rewards().items

    assert ledger.referrer_freelancer_id == referrer_card
    assert ledger.referrer_name == "Ada Lovelace"


def test_the_ledger_projects_a_pending_referral_from_its_live_engagement_and_keeps_real_figures(
    clean_referrals: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    _engagement_sent(clean_referrals, freelancer_id, company_id, state="draft", expected_days=5)
    _engagement_sent(clean_referrals, freelancer_id, company_id, state="cancelled")
    live = _engagement_sent(
        clean_referrals, freelancer_id, company_id, state="draft", expected_days=10
    )
    # A newer engagement whose letter is already out of the running cannot mature the reward.
    _engagement_sent(clean_referrals, freelancer_id, company_id, state="active", expected_days=1)

    pending = _by_kind(clean_referrals)

    freelancer, company = pending["freelancer"], pending["company"]
    assert (freelancer.referred_id, company.referred_id) == (freelancer_id, company_id)
    assert freelancer.reward_id is None and freelancer.reward_amount is None
    # The newest engagement that can still earn (not the older one, not the cancelled one in
    # between, not the newer one already active), projected on its own days: 350 * 10.
    assert (freelancer.engagement_id, company.engagement_id) == (live, live)
    assert (freelancer.projected_rate, freelancer.projected_amount) == (
        Decimal("0.1000"),
        Decimal("350.00"),
    )
    assert company.projected_amount == Decimal("1050.00")
    assert (freelancer.engagement_company_name, freelancer.engagement_freelancer_name) == (
        "ACME Srl",
        "Ada Lovelace",
    )
    assert freelancer.engagement_freelancer_id == freelancer_id

    # Once signed the real figures stand, the projection is gone, even at another rate.
    signed = _engagement_sent(clean_referrals, freelancer_id, company_id)
    _sign(clean_referrals, signed)
    ReferralSettingsService(clean_referrals).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    real = _by_kind(clean_referrals)
    assert real["freelancer"].reward_amount == Decimal("700.00")
    assert real["freelancer"].engagement_id == signed
    assert (real["freelancer"].projected_rate, real["freelancer"].projected_amount) == (None, None)


def test_the_ledger_reads_a_page_in_a_row_independent_number_of_queries(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    for index in range(4):
        freelancer_id = _card(clean_referrals, email=f"f{index}@studio.it", ref=code)
        company_id = _request(clean_referrals, email=f"c{index}@acme.it", ref=code)
        _log_in(clean_referrals, f"c{index}@acme.it")
        _engagement_sent(clean_referrals, freelancer_id, company_id, state="draft")

    ReferralLedgerService(clean_referrals).list_rewards(limit=2)  # warm the session's own caches
    seen, stop = _statements(clean_referrals)
    try:
        two = ReferralLedgerService(clean_referrals).list_rewards(limit=2)
        for_two = len(seen)
        seen.clear()
        many = ReferralLedgerService(clean_referrals).list_rewards(limit=100)
        for_many = len(seen)
    finally:
        stop()

    assert {item.kind for item in two.items} == {"freelancer", "company"} and len(many.items) == 8
    assert {(item.kind, item.projected_amount) for item in many.items} == {
        ("freelancer", Decimal("700.00")),
        ("company", Decimal("2100.00")),
    }
    assert for_many == for_two


def test_a_deleted_request_or_card_is_flagged_so_no_page_links_to_it(
    clean_referrals: Session,
) -> None:
    """A deleted card or request answers not found on its own page: the ledger says so
    instead of leaving a name that opens nothing."""
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    other = _card(
        clean_referrals,
        email="grace@studio.it",
        ref=ReferralService(clean_referrals).code_for(_member(clean_referrals, "lia@community.it")),
    )
    _engagement_sent(clean_referrals, freelancer_id, company_id, state="draft")
    CompanyService(clean_referrals).soft_delete(company_id, admin_id)
    FreelancerService(clean_referrals).soft_delete(other, admin_id)

    ledger = {
        item.referred_id: item
        for item in ReferralLedgerService(clean_referrals).list_rewards().items
    }

    assert ledger[company_id].referred_deleted is True
    assert ledger[other].referred_deleted is True
    assert ledger[freelancer_id].referred_deleted is False
    assert ledger[freelancer_id].engagement_freelancer_deleted is False

    FreelancerService(clean_referrals).soft_delete(freelancer_id, admin_id)
    after = {
        item.referred_id: item
        for item in ReferralLedgerService(clean_referrals).list_rewards().items
    }
    assert after[freelancer_id].referred_deleted is True
    assert after[company_id].engagement_freelancer_deleted is True
