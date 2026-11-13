"""P-REB-44: a member's own code, and who it brings in through a sign-up or a freelancer's
card: the referral each rif makes and the login that verifies it. The path from a signed
notice to the reward is in `test_referral_rewards.py`.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from referral_fixtures import (
    _card,
    _draft_for,
    _member,
    _signup_with,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.models import (
    Freelancer,
    Referral,
    Signup,
    User,
)
from rebase_core.referrals import ReferralService

# ---- the member's own code --------------------------------------------------------


def test_code_for_is_issued_once_and_stable(clean_referrals: Session) -> None:
    user_id = _member(clean_referrals)
    service = ReferralService(clean_referrals)

    first = service.code_for(user_id)
    second = service.code_for(user_id)

    assert first == second
    assert 6 <= len(first) <= 10
    assert set(first) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_a_rolled_back_transaction_drops_the_code_it_issued(clean_referrals: Session) -> None:
    """`code_for` stages the code in the caller's transaction (REB-892): the caller's
    commit persists it, and a rollback leaves the member without one."""
    user_id = _member(clean_referrals)

    ReferralService(clean_referrals).code_for(user_id)
    clean_referrals.rollback()

    user = clean_referrals.get(User, user_id)
    assert user is not None and user.referral_code is None


def test_a_code_clash_retries_without_losing_the_callers_staged_work(
    clean_referrals: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    taken = ReferralService(clean_referrals).code_for(
        _member(clean_referrals, "taken@community.it")
    )
    clean_referrals.commit()
    user_id = _member(clean_referrals, "second@community.it")
    staged = User(email="staged@community.it", first_name="Staged", last_name="Row", role="member")
    clean_referrals.add(staged)
    offered = iter([taken, "FRESHCODE2"])
    monkeypatch.setattr("rebase_core.referrals._generate_code", lambda: next(offered))

    code = ReferralService(clean_referrals).code_for(user_id)
    clean_referrals.commit()

    assert code == "FRESHCODE2"
    assert (
        clean_referrals.scalar(select(User.id).where(User.email == "staged@community.it"))
        is not None
    )


def test_resolve_referrer_is_case_insensitive_and_mute_on_unknown(clean_referrals: Session) -> None:
    user_id = _member(clean_referrals)
    service = ReferralService(clean_referrals)
    code = service.code_for(user_id)

    assert service.resolve_referrer(code.lower()) is not None
    assert service.resolve_referrer(code.upper()).id == user_id  # type: ignore[union-attr]
    assert service.resolve_referrer(None) is None
    assert service.resolve_referrer("") is None
    assert service.resolve_referrer("NOSUCHCODE") is None


def test_link_signup_is_idempotent_and_refuses_self_referral(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    service = ReferralService(clean_referrals)
    code = service.code_for(referrer_id)
    freelancer_id = _card(clean_referrals)

    service.link_signup("freelancer", freelancer_id, code, new_user_id=UUID(int=0))
    service.link_signup("freelancer", freelancer_id, code, new_user_id=UUID(int=0))
    clean_referrals.commit()  # `link_signup` stages; the caller's unit of work commits
    rows = clean_referrals.scalars(
        select(Referral).where(Referral.entity_id == freelancer_id)
    ).all()
    assert len(rows) == 1
    assert rows[0].referrer_user_id == referrer_id

    other_freelancer_id = _card(clean_referrals, email="grace@studio.it")
    service.link_signup(
        "freelancer", other_freelancer_id, code, new_user_id=referrer_id
    )  # a member's own code, used for themself
    clean_referrals.commit()
    assert (
        clean_referrals.scalar(select(Referral).where(Referral.entity_id == other_freelancer_id))
        is None
    )


def test_to_freelancer_application_with_ref_links_the_referral(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)

    freelancer_id = _card(clean_referrals, ref=code)

    row = clean_referrals.scalar(select(Referral).where(Referral.entity_id == freelancer_id))
    assert row is not None
    assert (row.kind, row.referrer_user_id) == ("freelancer", referrer_id)


def test_to_signups_ref_is_stored_on_the_signup_and_credits_nobody_yet(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))

    signup_id = _signup_with(clean_referrals, ref=code)

    assert clean_referrals.get(Signup, signup_id).ref == code  # type: ignore[union-attr]
    assert clean_referrals.scalar(select(Referral)) is None


def test_drafting_to_card_from_to_signup_with_ref_makes_to_pending_referral(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    signup_id = _signup_with(clean_referrals, ref=code.lower())

    card_id = _draft_for(clean_referrals, signup_id)
    _draft_for(clean_referrals, signup_id)  # a second research adds no second referral

    rows = clean_referrals.scalars(select(Referral)).all()
    assert len(rows) == 1
    assert (rows[0].kind, rows[0].entity_id, rows[0].referrer_user_id) == (
        "freelancer",
        card_id,
        referrer_id,
    )
    assert (rows[0].status, rows[0].verified_at) == ("to_verify", None)


def test_the_referred_person_first_login_verifies_a_signup_referral(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _draft_for(clean_referrals, _signup_with(clean_referrals, ref=code))
    owner = clean_referrals.scalar(select(Freelancer.user_id).where(Freelancer.id == card_id))
    assert owner is not None

    ReferralService(clean_referrals).verify_on_login(
        owner, datetime.now(UTC) + timedelta(minutes=1)
    )
    clean_referrals.commit()

    row = clean_referrals.scalar(select(Referral))
    assert row is not None and (row.status, row.verified_via) == ("verified", "login")


def test_to_signup_without_ref_drafts_to_card_with_no_referral(clean_referrals: Session) -> None:
    _draft_for(clean_referrals, _signup_with(clean_referrals))

    assert clean_referrals.scalar(select(Referral)) is None


def test_to_garbage_or_unknown_ref_never_breaks_the_signup_or_the_draft(
    clean_referrals: Session,
) -> None:
    _member(clean_referrals)  # codes exist; this one is not among them
    garbage = _signup_with(clean_referrals, email="a@studio.it", ref="not a code!!")
    unknown = _signup_with(clean_referrals, email="b@studio.it", ref="ZZZZZZZZZZ")

    assert clean_referrals.get(Signup, garbage).ref is None  # type: ignore[union-attr]
    _draft_for(clean_referrals, garbage)
    _draft_for(clean_referrals, unknown)

    assert clean_referrals.scalar(select(Referral)) is None


def test_a_member_drafted_from_their_own_signup_code_refers_nobody(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals, "cold@studio.it"))

    _draft_for(clean_referrals, _signup_with(clean_referrals, ref=code))

    assert clean_referrals.scalar(select(Referral)) is None


def test_to_repeat_signup_never_adds_or_replaces_to_ref(clean_referrals: Session) -> None:
    service = ReferralService(clean_referrals)
    first, second = (
        service.code_for(_member(clean_referrals)),
        service.code_for(_member(clean_referrals, "b@c.it")),
    )
    bare = _signup_with(clean_referrals)
    kept = _signup_with(clean_referrals, email="kept@studio.it", ref=first)

    _signup_with(clean_referrals, ref=second)
    _signup_with(clean_referrals, email="kept@studio.it", ref=second)

    assert clean_referrals.get(Signup, bare).ref is None  # type: ignore[union-attr]
    assert clean_referrals.get(Signup, kept).ref == first  # type: ignore[union-attr]
