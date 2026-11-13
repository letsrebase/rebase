"""The reward a referral matures into (P-REB-44, REB-658): the base formula, the first signed
letter that writes it once, the login or the signature that verifies, and what the framework
agreement and the letter print about the referrer.
"""

from decimal import Decimal

import pytest
from admins import an_admin
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import SIGNER, TODAY, _documents, _fiscal
from referral_fixtures import (
    _card,
    _log_in,
    _match_body,
    _match_list,
    _member,
    _referral_of,
    _request,
    _sent_with_body,
    _sign_the_letter,
    _tell_the_hub,
    clean_referrals,  # noqa: F401  (fixture)
)
from signing_fixtures import (
    _active_framework,
    _letter_of,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.contract_schemas import (
    DAY_RATE,
    LUMP_SUM,
)
from rebase_core.matches import MatchService
from rebase_core.models import (
    HubEngagement,
    Referral,
    ReferralReward,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referrals import ReferralService, reward_base

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
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _log_in(clean_referrals, "wile@acme.it")
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)

    _sign_the_letter(clean_referrals, renderer, fake, match)

    rewards = clean_referrals.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
        .order_by(Referral.kind)
    ).all()
    by_kind = {clean_referrals.get(Referral, r.referral_id).kind: r for r in rewards}  # type: ignore[union-attr]
    assert set(by_kind) == {"company", "freelancer"}
    # Margin: (800 - 450) * 20 `giorni previsti` = 7000.
    assert by_kind["freelancer"].rate == Decimal("0.1000")
    assert by_kind["freelancer"].base_amount == Decimal("7000.00")
    assert by_kind["freelancer"].reward_amount == Decimal("700.00")
    assert by_kind["company"].rate == Decimal("0.3000")
    assert by_kind["company"].base_amount == Decimal("7000.00")
    assert by_kind["company"].reward_amount == Decimal("2100.00")
    assert by_kind["freelancer"].status == "to_confirm"

    # Telling the hub again (a retried notice, a later one of the same signed engagement) never
    # doubles the reward.
    assert _tell_the_hub(clean_referrals, match, "active", sequence=2) is False
    assert _tell_the_hub(clean_referrals, match, "active", sequence=3) is True
    ReferralService(clean_referrals).record_reward_if_signed(
        clean_referrals.get(HubEngagement, match.id)  # type: ignore[arg-type]
    )
    clean_referrals.commit()
    again = clean_referrals.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
    ).all()
    assert len(again) == 2


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
    accomplice's code. A freelancer is matched with that company and signs; the real
    referente has never logged in, so nothing is owed. When they do, the attribution
    they never made counts, for the accomplice and for nobody else: the first writer wins
    an address's attribution (REB-646), the proof here is of the person, not of the code."""
    accomplice = ReferralService(clean_referrals).code_for(
        _member(clean_referrals, "accomplice@community.it")
    )
    honest = ReferralService(clean_referrals).code_for(
        _member(clean_referrals, "honest@community.it")
    )
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals, email="prospect@acme.it", ref=accomplice)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)

    listed = {r.kind: r for r in _match_list(clean_referrals)[0].referrals}
    assert listed["company"].status == "to_verify"
    assert (listed["company"].rate, listed["company"].amount) == (None, None)
    (ledger,) = ReferralLedgerService(clean_referrals).list_rewards().items
    assert (ledger.referral_status, ledger.engagement_id, ledger.projected_amount) == (
        "to_verify",
        None,
        None,
    )

    _sign_the_letter(clean_referrals, renderer, fake, match)
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
    assert item.engagement_id == match.id
    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    assert reward.engagement_id == match.id


def test_a_referred_freelancers_own_signature_verifies_them_and_writes_the_reward(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)
    # Before the signature the pending freelancer's reward is still projected: signing
    # is what verifies them. The pending company's is not.
    by_kind = {i.kind: i for i in ReferralLedgerService(clean_referrals).list_rewards().items}
    assert by_kind["freelancer"].projected_amount == Decimal("700.00")
    assert by_kind["company"].projected_amount is None and by_kind["company"].engagement_id is None

    _sign_the_letter(clean_referrals, renderer, fake, match)

    freelancer = _referral_of(clean_referrals, freelancer_id)
    assert (freelancer.status, freelancer.verified_via) == ("verified", "letter")
    assert freelancer.verified_at == _letter_of(clean_referrals, match.id).signed_at
    assert _referral_of(clean_referrals, company_id).status == "to_verify"
    (reward,) = clean_referrals.scalars(select(ReferralReward)).all()
    assert reward.referral_id == freelancer.id and reward.reward_amount == Decimal("700.00")


def test_a_login_after_the_signature_does_not_write_a_second_reward(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean_referrals, renderer, fake, match)

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


def test_an_unreferred_match_earns_no_reward(clean_referrals: Session) -> None:
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)

    _sign_the_letter(clean_referrals, renderer, fake, match)

    assert clean_referrals.scalar(select(ReferralReward)) is None


def test_a_referred_freelancers_framework_agreement_names_the_referrer(
    clean_referrals: Session,
) -> None:
    """`_framework_date` prints the freelancer's own referrer, exercised through
    `write_framework`'s real path (no `_active_framework` shortcut here, unlike the
    reward tests above, so the framework agreement is actually generated)."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _log_in(clean_referrals, "ada@studio.it")
    renderer = FakeRenderer(draft=False)
    MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    framework = _documents(clean_referrals, freelancer_id, "framework")[-1]
    assert framework.data["segnalato-da"] == "Mario Rossi"


def test_a_pending_freelancer_referral_is_not_printed_on_the_framework_agreement(
    clean_referrals: Session,
) -> None:
    """REB-664: a `to_verify` referral may be squatted, so the agreement prints
    `nessuno`. The framework agreement is written before the freelancer's first letter
    signature (which is what verifies them), so it stays `nessuno` unless they logged in."""
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    assert _referral_of(clean_referrals, freelancer_id).status == "to_verify"
    framework = _documents(clean_referrals, freelancer_id, "framework")[-1]
    assert framework.data["segnalato-da"] == "nessuno"


def test_a_pending_company_referral_is_not_printed_on_the_letter_until_verified(
    clean_referrals: Session,
) -> None:
    code = ReferralService(clean_referrals).code_for(_member(clean_referrals))
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals, ref=code)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    pending = MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )
    assert _letter_of(clean_referrals, pending.id).data["azienda-segnalata-da"] == "nessuno"

    _log_in(clean_referrals, "wile@acme.it")
    verified = MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )
    assert _letter_of(clean_referrals, verified.id).data["azienda-segnalata-da"] == "Mario Rossi"


def test_an_unreferred_freelancers_framework_agreement_prints_none_not_to_blank(
    clean_referrals: Session,
) -> None:
    """A blank `{{segnalato-da}}` would print as a hand-fillable line on every
    framework agreement, since nobody referred the overwhelming majority of
    freelancers: `nessuno` reads as a fact instead (the `CERTIFIED_EMAIL_MISSING` convention)."""
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    framework = _documents(clean_referrals, freelancer_id, "framework")[-1]
    assert framework.data["segnalato-da"] == "nessuno"


def test_to_referred_companys_letter_names_the_referrer_and_an_unreferred_one_prints_none(
    clean_referrals: Session,
) -> None:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals)
    referred_company_id = _request(clean_referrals, ref=code, email="wile@acme.it")
    _log_in(clean_referrals, "wile@acme.it")
    unreferred_company_id = _request(
        clean_referrals, email="other@beta.it", company_name="Beta Srl"
    )
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    referred_match = _sent_with_body(
        clean_referrals, renderer, FakeDocumenso(), freelancer_id, referred_company_id, admin_id
    )
    unreferred_match = MatchService(clean_referrals, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(unreferred_company_id), admin_id
    )

    referred_letter = _letter_of(clean_referrals, referred_match.id)
    unreferred_letter = _letter_of(clean_referrals, unreferred_match.id)
    assert referred_letter.data["azienda-segnalata-da"] == "Mario Rossi"
    assert unreferred_letter.data["azienda-segnalata-da"] == "nessuno"
