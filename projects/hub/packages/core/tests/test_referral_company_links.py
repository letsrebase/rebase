"""A company's request and the referral it makes (P-REB-44): through the wizard, the talent
cloud and a returning contact, which code wins, and that a card or a request is never
committed without its referral.
"""

import pytest
from admins import an_admin
from referral_fixtures import (
    _card,
    _company_referral,
    _fail_after_the_card_is_written,
    _log_in,
    _member,
    _public_request,
    _request,
    clean_referrals,  # noqa: F401  (fixture)
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.cloud import TalentCloudService
from rebase_core.config import Settings
from rebase_core.models import (
    Company,
    Freelancer,
    Referral,
)
from rebase_core.referrals import ReferralService


def test_a_companys_first_request_with_rif_links_the_referral_but_not_the_next_one(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)

    company_id = _request(clean_referrals, rif=code)
    second_company_id = _request(
        clean_referrals, rif=code, progetto="Un secondo progetto, altrettanto lungo."
    )

    first = clean_referrals.scalar(select(Referral).where(Referral.entity_id == company_id))
    assert first is not None and first.kind == "company"
    # The referente already had a request: the second one is not a fresh referral.
    assert (
        clean_referrals.scalar(select(Referral).where(Referral.entity_id == second_company_id))
        is None
    )


def test_a_company_appearing_through_the_wizard_makes_the_pending_referral(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    _public_request(clean_referrals, rif=code)
    assert clean_referrals.scalar(select(Referral)) is None  # nothing at filing time

    company_id = _request(clean_referrals)  # the wizard, no `rif` of its own

    row = _company_referral(clean_referrals, company_id)
    assert row is not None
    assert (row.kind, row.referrer_user_id, row.stato, row.verified_at) == (
        "company",
        referrer_id,
        "da_verificare",
        None,
    )
    # REB-658: the contact's first magic-link login is what verifies it.
    _log_in(clean_referrals, "wile@acme.it")
    clean_referrals.expire_all()
    assert _company_referral(clean_referrals, company_id).stato == "verificato"  # type: ignore[union-attr]


def test_the_wizards_own_rif_wins_over_the_requests_when_it_names_a_member(
    clean_referrals: Session,
) -> None:
    stored = _member(clean_referrals, "stored@community.it")
    own = _member(clean_referrals, "own@community.it")
    service = ReferralService(clean_referrals)
    _public_request(clean_referrals, rif=service.code_for(stored))

    company_id = _request(clean_referrals, rif=service.code_for(own))

    row = _company_referral(clean_referrals, company_id)
    assert row is not None and row.referrer_user_id == own


def test_an_unknown_wizard_rif_falls_back_to_the_requests(clean_referrals: Session) -> None:
    stored = _member(clean_referrals)
    _public_request(clean_referrals, rif=ReferralService(clean_referrals).code_for(stored))

    company_id = _request(clean_referrals, rif="ZZZZZZZZZZ")

    row = _company_referral(clean_referrals, company_id)
    assert row is not None and row.referrer_user_id == stored


def test_a_company_opened_to_the_cloud_makes_the_pending_referral(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    admin_id = an_admin(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    company_id = _request(clean_referrals)  # the company came first, with no code
    assert clean_referrals.scalar(select(Referral)) is None
    _public_request(clean_referrals, rif=code)  # the request is filed afterwards
    assert clean_referrals.scalar(select(Referral)) is None

    settings = Settings(hub_url="http://localhost:5180/hub", _env_file=None)  # type: ignore[call-arg]
    TalentCloudService(clean_referrals, settings).grant(company_id, admin_id)

    row = _company_referral(clean_referrals, company_id)
    assert row is not None and (row.referrer_user_id, row.stato) == (referrer_id, "da_verificare")


def test_the_cloud_never_adds_a_second_referral_to_a_contact_who_has_one(
    clean_referrals: Session,
) -> None:
    first = _member(clean_referrals, "first@community.it")
    second = _member(clean_referrals, "second@community.it")
    admin_id = an_admin(clean_referrals)
    service = ReferralService(clean_referrals)
    company_id = _request(clean_referrals, rif=service.code_for(first))
    other_id = _request(clean_referrals, progetto="Un secondo progetto, altrettanto lungo.")
    _public_request(clean_referrals, rif=service.code_for(second))

    settings = Settings(hub_url="http://localhost:5180/hub", _env_file=None)  # type: ignore[call-arg]
    TalentCloudService(clean_referrals, settings).grant(other_id, admin_id)

    assert _company_referral(clean_referrals, other_id) is None
    kept = _company_referral(clean_referrals, company_id)
    assert kept is not None and kept.referrer_user_id == first


def test_the_wizards_own_code_never_blocks_the_requests_valid_fallback(
    clean_referrals: Session,
) -> None:
    """Greptile on REB-600: the contact's own code in the wizard is a self-referral, so it
    is no code and the member the public request named is credited."""
    stored = _member(clean_referrals, "stored@community.it")
    own = _member(clean_referrals, "wile@acme.it")
    service = ReferralService(clean_referrals)
    _public_request(clean_referrals, rif=service.code_for(stored))

    company_id = _request(clean_referrals, rif=service.code_for(own))

    row = _company_referral(clean_referrals, company_id)
    assert row is not None and row.referrer_user_id == stored


def test_a_returning_contact_with_no_referral_gets_the_requests_code(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    _request(clean_referrals)  # a first company, no code anywhere
    _public_request(clean_referrals, rif=ReferralService(clean_referrals).code_for(referrer_id))

    second = _request(clean_referrals, progetto="Un secondo progetto, altrettanto lungo.")

    row = _company_referral(clean_referrals, second)
    assert row is not None and row.referrer_user_id == referrer_id


def test_a_returning_contacts_own_code_wins_when_no_company_has_a_referral(
    clean_referrals: Session,
) -> None:
    stored = _member(clean_referrals, "stored@community.it")
    own = _member(clean_referrals, "own@community.it")
    service = ReferralService(clean_referrals)
    _request(clean_referrals)
    _public_request(clean_referrals, rif=service.code_for(stored))

    second = _request(
        clean_referrals,
        rif=service.code_for(own),
        progetto="Un secondo progetto, altrettanto lungo.",
    )

    row = _company_referral(clean_referrals, second)
    assert row is not None and row.referrer_user_id == own


@pytest.mark.parametrize("stored", [None, "ZZZZZZZZZZ"])
def test_no_rif_or_an_unknown_one_on_the_request_makes_no_referral(
    clean_referrals: Session, stored: str | None
) -> None:
    _member(clean_referrals)
    _public_request(clean_referrals, rif=stored)

    assert _company_referral(clean_referrals, _request(clean_referrals)) is None


def test_a_rif_that_is_the_contacts_own_code_makes_no_referral(clean_referrals: Session) -> None:
    own = _member(clean_referrals, "wile@acme.it")
    _public_request(clean_referrals, rif=ReferralService(clean_referrals).code_for(own))

    assert _company_referral(clean_referrals, _request(clean_referrals)) is None


def test_a_request_of_another_contact_never_credits_this_company(clean_referrals: Session) -> None:
    referrer_id = _member(clean_referrals)
    _public_request(
        clean_referrals,
        email="other@elsewhere.it",
        rif=ReferralService(clean_referrals).code_for(referrer_id),
    )

    assert _company_referral(clean_referrals, _request(clean_referrals)) is None


def test_a_freelancer_card_is_never_committed_without_its_referral(
    clean_referrals: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-566: `apply` committed the card and only then linked the referral, so a
    failure between the two left a card that a retry short-circuited on, and the referral
    was lost for good. Both writes are one commit now: either both land or neither does,
    and the retry attributes the card."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)

    with monkeypatch.context() as patched:
        _fail_after_the_card_is_written(patched)
        with pytest.raises(RuntimeError, match="connection dropped"):
            _card(clean_referrals, rif=code)
    clean_referrals.rollback()  # what the request's session does when it closes on the error

    assert clean_referrals.scalar(select(Freelancer.id)) is None
    assert clean_referrals.scalar(select(Referral.id)) is None

    freelancer_id = _card(clean_referrals, rif=code)
    row = clean_referrals.scalar(select(Referral).where(Referral.entity_id == freelancer_id))
    assert row is not None and row.referrer_user_id == referrer_id


def test_a_company_request_is_never_committed_without_its_referral(
    clean_referrals: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-566: the same one-commit rule for `CompanyService.request`."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)

    with monkeypatch.context() as patched:
        _fail_after_the_card_is_written(patched)
        with pytest.raises(RuntimeError, match="connection dropped"):
            _request(clean_referrals, rif=code)
    clean_referrals.rollback()

    assert clean_referrals.scalar(select(Company.id)) is None
    assert clean_referrals.scalar(select(Referral.id)) is None

    company_id = _request(clean_referrals, rif=code)
    row = clean_referrals.scalar(select(Referral).where(Referral.entity_id == company_id))
    assert row is not None and row.referrer_user_id == referrer_id
