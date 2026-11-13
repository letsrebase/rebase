"""The ledger and the match list that show referrals (P-REB-44): projected then real figures,
the referrer named, a cancelled match, and the handful of queries a page costs.
"""

from decimal import Decimal

import pytest
from admins import an_admin
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import SIGNER, TODAY, _fiscal
from referral_fixtures import (
    _card,
    _log_in,
    _match_body,
    _match_list,
    _member,
    _referred_pair,
    _request,
    _sent_with_body,
    _sign_the_letter,
    _statements,
    _tell_the_hub,
    clean_referrals,  # noqa: F401  (fixture)
)
from signing_fixtures import (
    _active_framework,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService
from rebase_core.models import (
    Freelancer,
    HubEngagement,
    Match,
    Referral,
    ReferralReward,
)
from rebase_core.referral_ledger import ReferralLedgerService
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


def test_the_match_list_shows_a_referral_projected_then_real_then_by_state(
    clean_referrals: Session,
) -> None:
    referrer_id, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)

    (before,) = _match_list(clean_referrals)
    assert before.company_id == company_id
    assert (before.letter_fee, before.unit_letter) == (Decimal("450.00"), "to_work_unit")
    assert [
        (r.kind, r.referrer_name, r.status, r.rate, r.amount, r.reward_id) for r in before.referrals
    ] == [
        ("freelancer", "Mario Rossi", "expected", Decimal("0.1000"), Decimal("700.00"), None),
        ("company", "Mario Rossi", "expected", Decimal("0.3000"), Decimal("2100.00"), None),
    ]

    _sign_the_letter(clean_referrals, renderer, fake, match)
    # A rate edited after the signature never moves what the signature already earned.
    ReferralSettingsService(clean_referrals).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    service = ReferralLedgerService(clean_referrals)
    company_reward = clean_referrals.scalar(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.kind == "company")
    )
    assert company_reward is not None
    service.set_state(company_reward.id, "confirmed", admin_id)

    (after,) = _match_list(clean_referrals)
    by_kind = {r.kind: r for r in after.referrals}
    assert (by_kind["freelancer"].status, by_kind["freelancer"].rate) == (
        "to_confirm",
        Decimal("0.1000"),
    )
    assert by_kind["freelancer"].amount == Decimal("700.00")
    assert (by_kind["company"].status, by_kind["company"].amount) == (
        "confirmed",
        Decimal("2100.00"),
    )
    assert by_kind["company"].reward_id == company_reward.id


def test_to_referral_that_already_paid_on_another_match_is_already_accrued_here(
    clean_referrals: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    first = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean_referrals, renderer, fake, first)
    other_company = _request(clean_referrals, email="road@runner.it", company_name="Road Runner")
    second = MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(other_company), admin_id
    )

    listed = {item.id: item for item in _match_list(clean_referrals)}

    # The company side of the first match earned there; the freelancer side of the second
    # match belongs to a referral that already paid, and the other company was never referred.
    assert [r.kind for r in listed[first.id].referrals] == ["freelancer", "company"]
    assert {r.kind: r.status for r in listed[first.id].referrals} == {
        "freelancer": "to_confirm",
        "company": "to_confirm",
    }
    (elsewhere,) = listed[second.id].referrals
    assert (elsewhere.kind, elsewhere.status) == ("freelancer", "already_accrued")
    assert (elsewhere.rate, elsewhere.amount, elsewhere.reward_id) == (None, None, None)


def test_a_match_with_no_referral_lists_none_and_an_unpriceable_one_projects_nothing(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    plain_id = _card(clean_referrals, email="plain@studio.it")
    referred_id = _card(clean_referrals, email="referred@studio.it", ref=code)
    company_id = _request(clean_referrals)
    for freelancer_id in (plain_id, referred_id):
        _fiscal(clean_referrals, freelancer_id, admin_id)
        _active_framework(clean_referrals, freelancer_id, admin_id)
    service = MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    plain = service.create(plain_id, _match_body(company_id), admin_id)
    body = _match_body(company_id, expected_days=None)
    body.letter.mode, body.letter.unit = "lump_sum", "a corpo"
    lump = service.create(referred_id, body, admin_id)

    listed = {item.id: item for item in _match_list(clean_referrals)}

    assert listed[plain.id].referrals == []
    (side,) = listed[lump.id].referrals
    assert (side.status, side.amount) == ("expected", None)
    assert listed[lump.id].unit_letter == "lump_sum"


def test_a_cancelled_match_still_names_its_referral_but_promises_no_amount(
    clean_referrals: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    service = MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    match = service.create(freelancer_id, _match_body(company_id), admin_id)
    service.cancel(match.id, admin_id)

    (row,) = _match_list(clean_referrals)

    assert [(r.status, r.amount) for r in row.referrals] == [("expected", None), ("expected", None)]


def test_a_referrer_with_a_card_of_his_own_is_named_with_it(clean_referrals: Session) -> None:
    referrer_card = _card(clean_referrals, email="ref@studio.it")
    referrer_user = clean_referrals.scalar(
        select(Freelancer.user_id).where(Freelancer.id == referrer_card)
    )
    assert referrer_user is not None
    code = ReferralService(clean_referrals).code_for(referrer_user)
    admin_id = an_admin(clean_referrals)
    referred_id = _card(clean_referrals, email="referred@studio.it", ref=code)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, referred_id, admin_id)
    _active_framework(clean_referrals, referred_id, admin_id)
    MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY).create(
        referred_id, _match_body(company_id), admin_id
    )

    (row,) = _match_list(clean_referrals)
    (side,) = row.referrals
    (ledger,) = ReferralLedgerService(clean_referrals).list_rewards().items

    assert side.referrer_freelancer_id == referrer_card
    assert ledger.referrer_freelancer_id == referrer_card
    assert ledger.referrer_name == "Ada Lovelace"


def test_the_match_list_reads_its_referrals_in_a_page_wide_handful_of_queries(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    service = MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    for index in range(4):
        freelancer_id = _card(clean_referrals, email=f"f{index}@studio.it", ref=code)
        company_id = _request(clean_referrals, email=f"c{index}@acme.it", ref=code)
        _fiscal(clean_referrals, freelancer_id, admin_id)
        _active_framework(clean_referrals, freelancer_id, admin_id)
        service.create(freelancer_id, _match_body(company_id), admin_id)

    service.reads.list_all(status=None, q=None, limit=1, offset=0)  # warm the session's own caches
    seen, stop = _statements(clean_referrals)
    try:
        one = service.reads.list_all(status=None, q=None, limit=1, offset=0)
        for_one = len(seen)
        seen.clear()
        four = service.reads.list_all(status=None, q=None, limit=100, offset=0)
        for_four = len(seen)
    finally:
        stop()

    assert len(one.items) == 1 and len(four.items) == 4
    assert all(len(item.referrals) == 2 for item in four.items)
    assert for_four == for_one


def test_the_ledger_projects_a_pending_referral_from_its_live_match_and_keeps_real_figures(
    clean_referrals: Session,
) -> None:
    referrer_id, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    service = MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY)
    older = service.create(freelancer_id, _match_body(company_id, expected_days=5), admin_id)
    _tell_the_hub(clean_referrals, clean_referrals.get(Match, older.id), "draft", sequence=1)  # type: ignore[arg-type]
    cancelled = service.create(freelancer_id, _match_body(company_id), admin_id)
    service.cancel(cancelled.id, admin_id)
    _tell_the_hub(
        clean_referrals, clean_referrals.get(Match, cancelled.id), "cancelled", sequence=1
    )  # type: ignore[arg-type]
    live = service.create(freelancer_id, _match_body(company_id, expected_days=10), admin_id)
    _tell_the_hub(clean_referrals, clean_referrals.get(Match, live.id), "draft", sequence=1)  # type: ignore[arg-type]
    # A newer engagement whose letter is already out of the running cannot mature the reward.
    past = service.create(freelancer_id, _match_body(company_id, expected_days=1), admin_id)
    clean_referrals.get(Match, past.id).status = "active"  # type: ignore[union-attr]
    clean_referrals.commit()
    _tell_the_hub(clean_referrals, clean_referrals.get(Match, past.id), "active", sequence=1)  # type: ignore[arg-type]

    pending = {
        item.kind: item for item in ReferralLedgerService(clean_referrals).list_rewards().items
    }

    freelancer, company = pending["freelancer"], pending["company"]
    assert (freelancer.referred_id, company.referred_id) == (freelancer_id, company_id)
    assert freelancer.reward_id is None and freelancer.reward_amount is None
    # The newest match that can still earn (not the older one, not the cancelled one in
    # between, not the newer one already active), projected on its own days: 350 * 10.
    assert (freelancer.engagement_id, company.engagement_id) == (live.id, live.id)
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
    signed = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean_referrals, renderer, fake, signed)
    ReferralSettingsService(clean_referrals).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    real = {item.kind: item for item in ReferralLedgerService(clean_referrals).list_rewards().items}
    assert real["freelancer"].reward_amount == Decimal("700.00")
    assert real["freelancer"].engagement_id == signed.id
    assert (real["freelancer"].projected_rate, real["freelancer"].projected_amount) == (None, None)


def test_the_ledger_reads_a_page_in_a_row_independent_number_of_queries(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    service = MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    for index in range(4):
        freelancer_id = _card(clean_referrals, email=f"f{index}@studio.it", ref=code)
        company_id = _request(clean_referrals, email=f"c{index}@acme.it", ref=code)
        _log_in(clean_referrals, f"c{index}@acme.it")
        _fiscal(clean_referrals, freelancer_id, admin_id)
        _active_framework(clean_referrals, freelancer_id, admin_id)
        match = service.create(freelancer_id, _match_body(company_id), admin_id)
        _tell_the_hub(clean_referrals, clean_referrals.get(Match, match.id), "draft", sequence=1)  # type: ignore[arg-type]

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
    """A deleted card or request answers not found on its own page: both lists say so
    instead of leaving a name that opens nothing."""
    _, admin_id, freelancer_id, company_id = _referred_pair(clean_referrals)
    other = _card(
        clean_referrals,
        email="grace@studio.it",
        ref=ReferralService(clean_referrals).code_for(_member(clean_referrals, "lia@community.it")),
    )
    service = MatchService(clean_referrals, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    match = service.create(freelancer_id, _match_body(company_id), admin_id)
    _tell_the_hub(clean_referrals, clean_referrals.get(Match, match.id), "draft", sequence=1)  # type: ignore[arg-type]
    CompanyService(clean_referrals).soft_delete(company_id, admin_id)
    FreelancerService(clean_referrals).soft_delete(other, admin_id)

    (row,) = _match_list(clean_referrals)
    ledger = {
        item.referred_id: item
        for item in ReferralLedgerService(clean_referrals).list_rewards().items
    }

    assert row.company_deleted is True
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
