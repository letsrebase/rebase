"""The hub's read model of the CRM's engagements, kept by its notices (REB-1106): the upsert, a
notice that arrives out of order, the budget snapshot a handed-off row keeps, and the referral
reward written once, in the same transaction, when a signed letter lands."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from referral_fixtures import (
    _card,
    _handoff,
    _member,
    _notice,
    _referral_of,
    _request,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.engagement_notices import EngagementNoticeService
from rebase_core.engagement_schemas import EngagementNotice
from rebase_core.errors import ValidationFailed
from rebase_core.models import HubEngagement, ReferralReward
from rebase_core.referrals import ReferralService

SIGNED = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def _apply(session: Session, notice: EngagementNotice) -> bool:
    return EngagementNoticeService(session).apply(notice)


def _row(session: Session, key: UUID) -> HubEngagement:
    session.expire_all()
    row = session.get(HubEngagement, key)
    assert row is not None
    return row


def _referred(session: Session) -> tuple[UUID, UUID]:
    """A freelancer card and a company request brought in by one member's code."""
    code = ReferralService(session).code_for(_member(session))
    return _card(session, ref=code), _request(session, ref=code)


def test_a_notice_opens_the_row_and_a_newer_one_replaces_it(clean_referrals: Session) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = uuid4()

    assert _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=1)) is True
    first = _row(clean_referrals, key)
    assert (first.state, first.sequence, first.company_id) == ("draft", 1, company_id)
    assert (first.mode, first.fee, first.role) == ("day", Decimal("450.00"), "Backend developer")

    assert (
        _apply(
            clean_referrals,
            _notice(
                key,
                freelancer_id,
                company_id,
                sequence=2,
                kind="sent",
                state="awaiting_signature",
                fee=Decimal("500"),
            ),
        )
        is True
    )
    second = _row(clean_referrals, key)
    assert (second.state, second.sequence, second.fee) == (
        "awaiting_signature",
        2,
        Decimal("500.00"),
    )


def test_a_notice_older_than_the_one_held_changes_nothing(clean_referrals: Session) -> None:
    """A late delivery, or a retry after the answer was lost, cannot move the state back."""
    freelancer_id, company_id = _referred(clean_referrals)
    key = uuid4()
    _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=1))
    closed = _notice(
        key,
        freelancer_id,
        company_id,
        sequence=3,
        kind="closed",
        state="concluded",
        signed_at=SIGNED,
        ends_on=date(2026, 11, 1),
    )
    assert _apply(clean_referrals, closed) is True

    late = _notice(
        key, freelancer_id, company_id, sequence=2, kind="signed", state="active", fee=Decimal("1")
    )
    assert _apply(clean_referrals, late) is False
    assert _apply(clean_referrals, closed) is False  # the same notice again: a retry

    row = _row(clean_referrals, key)
    assert (row.state, row.sequence, row.fee, row.ends_on) == (
        "concluded",
        3,
        Decimal("450.00"),
        date(2026, 11, 1),
    )


def test_the_reward_is_written_once_when_the_signed_notice_lands(
    clean_referrals: Session,
) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = _handoff(clean_referrals, freelancer_id, company_id, Decimal("800"))
    _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=1, state="draft"))
    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=2,
            kind="sent",
            state="awaiting_signature",
        ),
    )
    assert clean_referrals.scalar(select(ReferralReward)) is None

    signed = _notice(
        key,
        freelancer_id,
        company_id,
        sequence=3,
        kind="signed",
        state="active",
        signed_at=SIGNED,
    )
    assert _apply(clean_referrals, signed) is True

    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    # The freelancer's own signature verifies them; the company's referral, never logged in,
    # earns nothing yet. Margin: (800 - 450) * 20 expected days = 7000, at 10%.
    freelancer_referral = _referral_of(clean_referrals, freelancer_id)
    assert (freelancer_referral.status, freelancer_referral.verified_via) == ("verified", "letter")
    assert freelancer_referral.verified_at == SIGNED
    assert reward.referral_id == freelancer_referral.id
    assert (reward.engagement_id, reward.base_amount, reward.reward_amount) == (
        key,
        Decimal("7000.00"),
        Decimal("700.00"),
    )

    # A retry, a later notice of the same signed engagement and a rate edited meanwhile never
    # write a second reward.
    assert _apply(clean_referrals, signed) is False
    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=4,
            kind="closed",
            state="concluded",
            signed_at=SIGNED,
            ends_on=date(2026, 12, 1),
        ),
    )
    assert len(clean_referrals.scalars(select(ReferralReward)).all()) == 1


def test_a_later_notice_that_still_carries_the_signature_writes_the_reward_the_lost_one_owed(
    clean_referrals: Session,
) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = _handoff(clean_referrals, freelancer_id, company_id, Decimal("800"))
    _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=1))

    # The `signed` notice never arrived; the `closed` one that follows says it was signed.
    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=3,
            kind="closed",
            state="concluded",
            signed_at=SIGNED,
            ends_on=date(2026, 11, 1),
        ),
    )

    assert len(clean_referrals.scalars(select(ReferralReward)).all()) == 1


def test_a_row_the_hub_did_not_open_takes_the_companys_budget_as_its_snapshot(
    clean_referrals: Session,
) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = uuid4()  # no handoff row: the notice creates it

    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=1,
            kind="signed",
            state="active",
            signed_at=SIGNED,
        ),
    )

    assert _row(clean_referrals, key).company_daily_budget == Decimal("800.00")
    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    # Margin: (800 - 450) * 20 expected days = 7000, at 10%.
    assert (reward.base_amount, reward.reward_amount) == (Decimal("7000.00"), Decimal("700.00"))


def test_the_snapshot_is_never_overwritten_by_a_later_notice_or_a_budget_edit(
    clean_referrals: Session,
) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = uuid4()
    _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=1))
    clean_referrals.execute(text("UPDATE companies SET daily_budget = 900"))
    clean_referrals.commit()

    _apply(clean_referrals, _notice(key, freelancer_id, company_id, sequence=2))

    assert _row(clean_referrals, key).company_daily_budget == Decimal("800.00")


def test_an_engagement_with_no_company_leaves_the_reward_to_be_priced_by_hand(
    clean_referrals: Session,
) -> None:
    freelancer_id, _ = _referred(clean_referrals)

    _apply(
        clean_referrals,
        _notice(
            uuid4(),
            freelancer_id,
            None,
            sequence=1,
            kind="signed",
            state="active",
            signed_at=SIGNED,
        ),
    )

    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    assert (reward.base_amount, reward.reward_amount) == (None, None)


def test_the_handoff_rows_budget_and_company_survive_the_notices(clean_referrals: Session) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = _handoff(clean_referrals, freelancer_id, company_id, Decimal("800"))
    crm_id = uuid4()

    _apply(
        clean_referrals,
        _notice(key, freelancer_id, None, sequence=1, engagement_id=crm_id),
    )
    # The next notice carries no `hub_match_id`: the CRM's own id finds the row.
    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            None,
            sequence=2,
            engagement_id=crm_id,
            hub_match_id=None,
            state="awaiting_signature",
            kind="sent",
        ),
    )

    row = _row(clean_referrals, key)
    assert (row.company_daily_budget, row.company_id, row.crm_engagement_id) == (
        Decimal("800.00"),
        company_id,
        crm_id,
    )
    assert (row.state, row.sequence) == ("awaiting_signature", 2)
    assert len(clean_referrals.scalars(select(HubEngagement)).all()) == 1


def test_an_hourly_fee_is_eight_hours_of_the_day_against_the_budget(
    clean_referrals: Session,
) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = _handoff(clean_referrals, freelancer_id, company_id, Decimal("800"))

    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=1,
            kind="signed",
            state="active",
            signed_at=SIGNED,
            mode="hour",
            fee=Decimal("50"),
            expected_days=Decimal("10"),
        ),
    )

    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    # (800 - 50 * 8) * 10 days = 4000, at 10%.
    assert (reward.base_amount, reward.reward_amount) == (Decimal("4000.00"), Decimal("400.00"))


def test_closing_early_is_told_from_running_to_the_end(clean_referrals: Session) -> None:
    freelancer_id, company_id = _referred(clean_referrals)
    key = uuid4()
    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=1,
            state="active",
            kind="signed",
            signed_at=SIGNED,
            ends_on=date(2026, 12, 31),
        ),
    )

    _apply(
        clean_referrals,
        _notice(
            key,
            freelancer_id,
            company_id,
            sequence=2,
            state="concluded",
            kind="closed",
            signed_at=SIGNED,
            ends_on=date(2026, 11, 15),
        ),
    )

    row = _row(clean_referrals, key)
    assert (row.ends_on, row.planned_ends_on) == (date(2026, 11, 15), date(2026, 12, 31))


def test_a_notice_for_a_freelancer_the_hub_does_not_know_is_refused(
    clean_referrals: Session,
) -> None:
    with pytest.raises(ValidationFailed):
        _apply(clean_referrals, _notice(uuid4(), uuid4(), None, sequence=1))
    assert clean_referrals.scalar(select(HubEngagement)) is None
