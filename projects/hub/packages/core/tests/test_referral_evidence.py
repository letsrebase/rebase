"""The evidence an admin reads beside a referral (REB-658): the code and the date of the
sign-up, the shared company domain, and whether the referred person ever logged in.
"""

from datetime import UTC, datetime

import pytest
from referral_fixtures import (
    _card,
    _evidence,
    _member,
    _request,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from rebase_core.models import (
    Company,
    Freelancer,
    Login,
    Referral,
)
from rebase_core.referral_evidence import domains_match, email_domain
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referrals import ReferralService
from rebase_core.signup_schemas import SignupUtm


def test_the_evidence_names_the_code_the_signup_used(clean_referrals: Session) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)

    assert _evidence(clean_referrals, card_id).code == code


def test_the_evidence_dates_the_signup_from_the_referred_card_not_the_ledger_row(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    earlier = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    clean_referrals.execute(text("UPDATE freelancers SET created_at = :at"), {"at": earlier})
    clean_referrals.execute(text("UPDATE companies SET created_at = :at"), {"at": earlier})
    clean_referrals.commit()

    assert _evidence(clean_referrals, card_id).signed_up_at == earlier
    assert _evidence(clean_referrals, company_id).signed_up_at == earlier


def test_the_evidence_falls_back_on_the_referral_when_the_referred_row_is_gone(
    clean_referrals: Session,
) -> None:
    """`Referral.entity_id` has no foreign key (one column points at two tables), so a
    hard-deleted card leaves the referral behind: its own moment, nothing to compare."""
    card_id = _card(
        clean_referrals, ref=ReferralService(clean_referrals).code_for(_member(clean_referrals))
    )
    clean_referrals.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": card_id})
    clean_referrals.commit()
    referral = clean_referrals.scalars(select(Referral)).one()

    evidence = _evidence(clean_referrals, card_id)

    assert evidence.signed_up_at == referral.created_at
    # Unknown, not a reassuring "no": the person's email and logins cannot be looked up.
    assert (evidence.utm_source, evidence.same_email_domain, evidence.ever_logged_in) == (
        None,
        None,
        None,
    )


def test_the_evidence_carries_the_utm_source_when_one_was_recorded(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    tracked = _card(clean_referrals, email="ada@studio.it", ref=code, utm_source="linkedin")
    untracked = _card(clean_referrals, email="grace@studio.it", ref=code)
    company = _request(
        clean_referrals, email="wile@acme.it", ref=code, utm=SignupUtm(utm_source="google")
    )

    assert _evidence(clean_referrals, company).utm_source == "google"
    assert _evidence(clean_referrals, tracked).utm_source == "linkedin"
    assert _evidence(clean_referrals, untracked).utm_source is None


def test_the_evidence_flags_a_shared_company_domain_on_a_freelancer_and_on_a_company(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals, "mario@studio.it"))
    colleague = _card(clean_referrals, email="ada@studio.it", ref=code)
    stranger = _card(clean_referrals, email="grace@other.it", ref=code)
    company = _request(clean_referrals, email="wile@STUDIO.it", ref=code)

    assert _evidence(clean_referrals, colleague).same_email_domain is True
    assert _evidence(clean_referrals, company).same_email_domain is True
    assert _evidence(clean_referrals, stranger).same_email_domain is False


def test_a_shared_public_mail_domain_is_not_a_match(clean_referrals: Session) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals, "mario@gmail.com"))
    card_id = _card(clean_referrals, email="ada@gmail.com", ref=code)

    assert _evidence(clean_referrals, card_id).same_email_domain is False


@pytest.mark.parametrize(
    ("referrer", "referred", "expected"),
    [
        ("mario@studio.it", "ada@studio.it", True),
        ("Mario@Studio.IT", "ada@studio.it", True),
        ("mario@studio.it", "ada@sub.studio.it", False),
        ("mario@gmail.com", "ada@gmail.com", False),
        ("mario@libero.it", "ada@libero.it", False),
        ("mario@studio.it", "ada@gmail.com", False),
        ("no-at-sign", "also-no-at-sign", False),
        ("", "", False),
    ],
)
def test_domains_match_ignores_case_and_public_providers(
    referrer: str, referred: str, expected: bool
) -> None:
    assert domains_match(referrer, referred) is expected


def test_email_domain_is_the_part_after_the_last_at() -> None:
    assert email_domain(' "a@b"@Studio.IT ') == "studio.it"
    assert email_domain("nobody") == ""


def test_the_evidence_says_a_referred_person_who_never_logged_in_never_did(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)

    assert _evidence(clean_referrals, card_id).ever_logged_in is False
    assert _evidence(clean_referrals, company_id).ever_logged_in is False


def test_the_evidence_says_a_referred_person_has_logged_in_once_a_login_exists(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    card_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    other_id = _card(clean_referrals, email="grace@studio.it", ref=code)
    card = clean_referrals.get(Freelancer, card_id)
    company = clean_referrals.get(Company, company_id)
    assert card is not None and company is not None
    # Two logins by the same person still make one `True`, never two rows in the ledger.
    clean_referrals.add_all([Login(user_id=card.user_id), Login(user_id=card.user_id)])
    clean_referrals.add(Login(user_id=company.user_id))
    clean_referrals.commit()

    assert _evidence(clean_referrals, card_id).ever_logged_in is True
    assert _evidence(clean_referrals, company_id).ever_logged_in is True
    assert _evidence(clean_referrals, other_id).ever_logged_in is False
    assert len(ReferralLedgerService(clean_referrals).list_rewards().items) == 3
