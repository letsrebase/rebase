"""P-REB-44: a member's own code, who it brings in, and the reward that matures once
at the referred entity's first signed letter. The full sign-to-reward path reuses
`test_signing.py`'s own fixtures (`FakeRenderer`, `FakeDocumenso`, `_active_framework`,
`_sent`): nothing here runs pandoc or reaches a real Documenso."""

import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_matches import PDF, SIGNER, TODAY, _documents, _fiscal
from test_signing import (
    SIGNED_AT,
    _active_framework,
    _envelope_of,
    _letter_of,
    _signing,
    _webhook,
)

from rebase_core.cloud import TalentCloudService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    DAY_RATE,
    LUMP_SUM,
    ClienteData,
    LetteraFields,
    MatchCreate,
    MatchListItem,
)
from rebase_core.db import session_factory
from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import (
    Company,
    Freelancer,
    Login,
    Match,
    Referral,
    ReferralReward,
    Signup,
    TeamRequest,
    User,
)
from rebase_core.referral_evidence import domains_match, email_domain
from rebase_core.referrals import ReferralService, reward_base
from rebase_core.schemas import (
    CompanyCreate,
    FreelancerCreate,
    FreelancerDraft,
    SignupCreate,
    SignupUtm,
)
from rebase_core.users import UserService

TABLES = (
    "talent_cloud_grants",
    "team_requests",
    "referral_rewards",
    "referrals",
    "referral_settings",
    "admin_actions",
    "contract_documents",
    "matches",
    "contract_letter_counters",
    "freelancer_fiscal",
    "comments",
    "freelancers",
    "companies",
    "users",
    "signups",
)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _member(session: Session, email: str = "mario@community.it") -> UUID:
    user = User(email=email, nome="Mario", cognome="Rossi", role="member")
    session.add(user)
    session.commit()
    return user.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(
    session: Session,
    *,
    email: str = "ada@studio.it",
    rif: str | None = None,
    utm_source: str | None = None,
) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email=email,
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
            rif=rif,
            utm=SignupUtm(utm_source=utm_source) if utm_source else None,
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(
    session: Session, *, email: str = "wile@acme.it", rif: str | None = None, **change: object
) -> UUID:
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": email,
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Le API del prodotto, per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("800"),
        "remoto": "remoto",
        "numero_risorse": 1,
        "rif": rif,
    }
    payload.update(change)
    row, _ = CompanyService(session).request(CompanyCreate(**payload))  # type: ignore[arg-type]
    return row.id


def _log_in(session: Session, email: str) -> None:
    """A real magic-link login: the mail `request_link` writes, its token spent by
    `enter`, the one door that records a `Login` and verifies the referrals naming the
    person."""
    users = UserService(session, Settings(_env_file=None))  # type: ignore[call-arg]
    mail = users.request_link(email)
    assert mail is not None
    token = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", mail.text)
    assert token is not None
    assert users.enter(token.group(1)) is not None


def _match_body(
    company_id: UUID, *, giorni_previsti: int | None = 20, modalita: str = "a giornata"
) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        cliente=ClienteData(
            cliente_ragione_sociale="ACME S.r.l.", cliente_piva="01234567890", cliente_sede="Milano"
        ),
        lettera=LetteraFields(
            ruolo="Backend developer",
            attivita="Le API del prodotto.",
            data_inizio=date(2026, 10, 1),
            compenso=Decimal("450"),
            modalita=modalita,
            unita=modalita,
            giorni_pagamento=30,
            fine_mese=True,
        ),
        giorni_previsti=giorni_previsti,
    )


def _sent_with_body(
    session: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    freelancer_id: UUID,
    company_id: UUID,
    admin_id: UUID,
    modalita: str = "a giornata",
) -> Match:
    """`test_signing._sent`'s own recipe (`_matches`/`_draft`/`send_match`), with
    `_match_body`'s explicit day rate and estimate instead of `test_matches._body`'s
    default, so the reward this earns is deterministic."""
    match = MatchService(session, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id, modalita=modalita), admin_id
    )
    _signing(session, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    row = session.get(Match, match.id)
    assert row is not None
    return row


# ---- the member's own code --------------------------------------------------------


def test_code_for_is_issued_once_and_stable(clean: Session) -> None:
    user_id = _member(clean)
    service = ReferralService(clean)

    first = service.code_for(user_id)
    second = service.code_for(user_id)

    assert first == second
    assert 6 <= len(first) <= 10
    assert set(first) <= set("ABCDEFGHJKLMNPQRSTUVWXYZ23456789")


def test_resolve_referrer_is_case_insensitive_and_mute_on_unknown(clean: Session) -> None:
    user_id = _member(clean)
    service = ReferralService(clean)
    code = service.code_for(user_id)

    assert service.resolve_referrer(code.lower()) is not None
    assert service.resolve_referrer(code.upper()).id == user_id  # type: ignore[union-attr]
    assert service.resolve_referrer(None) is None
    assert service.resolve_referrer("") is None
    assert service.resolve_referrer("NOSUCHCODE") is None


def test_link_signup_is_idempotent_and_refuses_self_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    service = ReferralService(clean)
    code = service.code_for(referrer_id)
    freelancer_id = _card(clean)

    service.link_signup("freelancer", freelancer_id, code, new_user_id=UUID(int=0))
    service.link_signup("freelancer", freelancer_id, code, new_user_id=UUID(int=0))
    clean.commit()  # `link_signup` stages; the caller's unit of work commits
    rows = clean.scalars(select(Referral).where(Referral.entity_id == freelancer_id)).all()
    assert len(rows) == 1
    assert rows[0].referrer_user_id == referrer_id

    other_freelancer_id = _card(clean, email="grace@studio.it")
    service.link_signup(
        "freelancer", other_freelancer_id, code, new_user_id=referrer_id
    )  # a member's own code, used for themself
    clean.commit()
    assert clean.scalar(select(Referral).where(Referral.entity_id == other_freelancer_id)) is None


def test_a_freelancer_application_with_rif_links_the_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)

    freelancer_id = _card(clean, rif=code)

    row = clean.scalar(select(Referral).where(Referral.entity_id == freelancer_id))
    assert row is not None
    assert (row.kind, row.referrer_user_id) == ("freelancer", referrer_id)


def _signup_with(session: Session, **extra: object) -> UUID:
    from rebase_core.service import SignupService

    payload: dict[str, object] = {"email": "cold@studio.it", "nome": "Cold", "cognome": "Lead"}
    payload.update(extra)
    return SignupService(session).subscribe(SignupCreate(**payload)).id  # type: ignore[arg-type]


def _draft_for(session: Session, signup_id: UUID) -> UUID:
    return (
        FreelancerService(session)
        .draft_from_signup(
            signup_id,
            FreelancerDraft(
                nome="Cold",
                cognome="Lead",
                posizione="Backend developer",
                fonti=["https://cold.dev"],
            ),
            "Claude",
        )
        .id
    )


def test_a_signups_rif_is_stored_on_the_signup_and_credits_nobody_yet(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))

    signup_id = _signup_with(clean, rif=code)

    assert clean.get(Signup, signup_id).rif == code  # type: ignore[union-attr]
    assert clean.scalar(select(Referral)) is None


def test_drafting_a_card_from_a_signup_with_rif_makes_a_pending_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    signup_id = _signup_with(clean, rif=code.lower())

    card_id = _draft_for(clean, signup_id)
    _draft_for(clean, signup_id)  # a second research adds no second referral

    rows = clean.scalars(select(Referral)).all()
    assert len(rows) == 1
    assert (rows[0].kind, rows[0].entity_id, rows[0].referrer_user_id) == (
        "freelancer",
        card_id,
        referrer_id,
    )
    assert (rows[0].stato, rows[0].verified_at) == ("da_verificare", None)


def test_the_referred_person_first_login_verifies_a_signup_referral(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _draft_for(clean, _signup_with(clean, rif=code))
    owner = clean.scalar(select(Freelancer.user_id).where(Freelancer.id == card_id))
    assert owner is not None

    ReferralService(clean).verify_on_login(owner, datetime.now(UTC) + timedelta(minutes=1))
    clean.commit()

    row = clean.scalar(select(Referral))
    assert row is not None and (row.stato, row.verified_via) == ("verificato", "accesso")


def test_a_signup_without_rif_drafts_a_card_with_no_referral(clean: Session) -> None:
    _draft_for(clean, _signup_with(clean))

    assert clean.scalar(select(Referral)) is None


def test_a_garbage_or_unknown_rif_never_breaks_the_signup_or_the_draft(clean: Session) -> None:
    _member(clean)  # codes exist; this one is not among them
    garbage = _signup_with(clean, email="a@studio.it", rif="not a code!!")
    unknown = _signup_with(clean, email="b@studio.it", rif="ZZZZZZZZZZ")

    assert clean.get(Signup, garbage).rif is None  # type: ignore[union-attr]
    _draft_for(clean, garbage)
    _draft_for(clean, unknown)

    assert clean.scalar(select(Referral)) is None


def test_a_member_drafted_from_their_own_signup_code_refers_nobody(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean, "cold@studio.it"))

    _draft_for(clean, _signup_with(clean, rif=code))

    assert clean.scalar(select(Referral)) is None


def test_a_repeat_signup_never_adds_or_replaces_a_rif(clean: Session) -> None:
    service = ReferralService(clean)
    first, second = service.code_for(_member(clean)), service.code_for(_member(clean, "b@c.it"))
    bare = _signup_with(clean)
    kept = _signup_with(clean, email="kept@studio.it", rif=first)

    _signup_with(clean, rif=second)
    _signup_with(clean, email="kept@studio.it", rif=second)

    assert clean.get(Signup, bare).rif is None  # type: ignore[union-attr]
    assert clean.get(Signup, kept).rif == first  # type: ignore[union-attr]


def test_a_companys_first_request_with_rif_links_the_referral_but_not_the_next_one(
    clean: Session,
) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)

    company_id = _request(clean, rif=code)
    second_company_id = _request(
        clean, rif=code, progetto="Un secondo progetto, altrettanto lungo."
    )

    first = clean.scalar(select(Referral).where(Referral.entity_id == company_id))
    assert first is not None and first.kind == "company"
    # The referente already had a request: the second one is not a fresh referral.
    assert clean.scalar(select(Referral).where(Referral.entity_id == second_company_id)) is None


# ---- a public team request's code, credited when a company exists (REB-600) -------


def _public_request(
    session: Session, *, email: str = "wile@acme.it", rif: str | None, azienda: str = "ACME Srl"
) -> None:
    session.add(
        TeamRequest(
            origine="pubblico", azienda=azienda, email=email, telefono="+39 345 1234567", rif=rif
        )
    )
    session.commit()


def _company_referral(session: Session, company_id: UUID) -> Referral | None:
    return session.scalar(select(Referral).where(Referral.entity_id == company_id))


def test_a_company_appearing_through_the_wizard_makes_the_pending_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    _public_request(clean, rif=code)
    assert clean.scalar(select(Referral)) is None  # nothing at filing time

    company_id = _request(clean)  # the wizard, no `rif` of its own

    row = _company_referral(clean, company_id)
    assert row is not None
    assert (row.kind, row.referrer_user_id, row.stato, row.verified_at) == (
        "company",
        referrer_id,
        "da_verificare",
        None,
    )
    # REB-658: the contact's first magic-link login is what verifies it.
    _log_in(clean, "wile@acme.it")
    clean.expire_all()
    assert _company_referral(clean, company_id).stato == "verificato"  # type: ignore[union-attr]


def test_the_wizards_own_rif_wins_over_the_requests_when_it_names_a_member(
    clean: Session,
) -> None:
    stored = _member(clean, "stored@community.it")
    own = _member(clean, "own@community.it")
    service = ReferralService(clean)
    _public_request(clean, rif=service.code_for(stored))

    company_id = _request(clean, rif=service.code_for(own))

    row = _company_referral(clean, company_id)
    assert row is not None and row.referrer_user_id == own


def test_an_unknown_wizard_rif_falls_back_to_the_requests(clean: Session) -> None:
    stored = _member(clean)
    _public_request(clean, rif=ReferralService(clean).code_for(stored))

    company_id = _request(clean, rif="ZZZZZZZZZZ")

    row = _company_referral(clean, company_id)
    assert row is not None and row.referrer_user_id == stored


def test_a_company_opened_to_the_cloud_makes_the_pending_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    admin_id = _admin(clean)
    code = ReferralService(clean).code_for(referrer_id)
    company_id = _request(clean)  # the company came first, with no code
    assert clean.scalar(select(Referral)) is None
    _public_request(clean, rif=code)  # the request is filed afterwards
    assert clean.scalar(select(Referral)) is None

    settings = Settings(hub_url="http://localhost:5180/hub", _env_file=None)  # type: ignore[call-arg]
    TalentCloudService(clean, settings).grant(company_id, admin_id)

    row = _company_referral(clean, company_id)
    assert row is not None and (row.referrer_user_id, row.stato) == (referrer_id, "da_verificare")


def test_the_cloud_never_adds_a_second_referral_to_a_contact_who_has_one(clean: Session) -> None:
    first = _member(clean, "first@community.it")
    second = _member(clean, "second@community.it")
    admin_id = _admin(clean)
    service = ReferralService(clean)
    company_id = _request(clean, rif=service.code_for(first))
    other_id = _request(clean, progetto="Un secondo progetto, altrettanto lungo.")
    _public_request(clean, rif=service.code_for(second))

    settings = Settings(hub_url="http://localhost:5180/hub", _env_file=None)  # type: ignore[call-arg]
    TalentCloudService(clean, settings).grant(other_id, admin_id)

    assert _company_referral(clean, other_id) is None
    kept = _company_referral(clean, company_id)
    assert kept is not None and kept.referrer_user_id == first


def test_the_wizards_own_code_never_blocks_the_requests_valid_fallback(clean: Session) -> None:
    """Greptile on REB-600: the contact's own code in the wizard is a self-referral, so it
    is no code and the member the public request named is credited."""
    stored = _member(clean, "stored@community.it")
    own = _member(clean, "wile@acme.it")
    service = ReferralService(clean)
    _public_request(clean, rif=service.code_for(stored))

    company_id = _request(clean, rif=service.code_for(own))

    row = _company_referral(clean, company_id)
    assert row is not None and row.referrer_user_id == stored


def test_a_returning_contact_with_no_referral_gets_the_requests_code(clean: Session) -> None:
    referrer_id = _member(clean)
    _request(clean)  # a first company, no code anywhere
    _public_request(clean, rif=ReferralService(clean).code_for(referrer_id))

    second = _request(clean, progetto="Un secondo progetto, altrettanto lungo.")

    row = _company_referral(clean, second)
    assert row is not None and row.referrer_user_id == referrer_id


@pytest.mark.parametrize("stored", [None, "ZZZZZZZZZZ"])
def test_no_rif_or_an_unknown_one_on_the_request_makes_no_referral(
    clean: Session, stored: str | None
) -> None:
    _member(clean)
    _public_request(clean, rif=stored)

    assert _company_referral(clean, _request(clean)) is None


def test_a_rif_that_is_the_contacts_own_code_makes_no_referral(clean: Session) -> None:
    own = _member(clean, "wile@acme.it")
    _public_request(clean, rif=ReferralService(clean).code_for(own))

    assert _company_referral(clean, _request(clean)) is None


def test_a_request_of_another_contact_never_credits_this_company(clean: Session) -> None:
    referrer_id = _member(clean)
    _public_request(
        clean, email="other@elsewhere.it", rif=ReferralService(clean).code_for(referrer_id)
    )

    assert _company_referral(clean, _request(clean)) is None


def _fail_after_the_card_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: ReferralService, code: str | None) -> User | None:
        raise RuntimeError("the connection dropped after the card write")

    monkeypatch.setattr(ReferralService, "resolve_referrer", boom)


def test_a_freelancer_card_is_never_committed_without_its_referral(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-566: `apply` committed the card and only then linked the referral, so a
    failure between the two left a card that a retry short-circuited on, and the referral
    was lost for good. Both writes are one commit now: either both land or neither does,
    and the retry attributes the card."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)

    with monkeypatch.context() as patched:
        _fail_after_the_card_is_written(patched)
        with pytest.raises(RuntimeError, match="connection dropped"):
            _card(clean, rif=code)
    clean.rollback()  # what the request's session does when it closes on the error

    assert clean.scalar(select(Freelancer.id)) is None
    assert clean.scalar(select(Referral.id)) is None

    freelancer_id = _card(clean, rif=code)
    row = clean.scalar(select(Referral).where(Referral.entity_id == freelancer_id))
    assert row is not None and row.referrer_user_id == referrer_id


def test_a_company_request_is_never_committed_without_its_referral(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REB-566: the same one-commit rule for `CompanyService.request`."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)

    with monkeypatch.context() as patched:
        _fail_after_the_card_is_written(patched)
        with pytest.raises(RuntimeError, match="connection dropped"):
            _request(clean, rif=code)
    clean.rollback()

    assert clean.scalar(select(Company.id)) is None
    assert clean.scalar(select(Referral.id)) is None

    company_id = _request(clean, rif=code)
    row = clean.scalar(select(Referral).where(Referral.entity_id == company_id))
    assert row is not None and row.referrer_user_id == referrer_id


# ---- the reward's base -------------------------------------------------------------


@pytest.mark.parametrize(
    ("modalita", "giorni", "expected"),
    [
        # A day rate, projected over the admin's own estimate.
        ("a giornata", 20, Decimal("7000")),
        # No estimate: one day's margin, the conservative floor.
        ("a giornata", None, Decimal("350")),
        # A lump sum compared against the client's own projected total.
        ("a corpo", 20, Decimal("7000")),
        # A lump sum with nothing to project against: no reliable base.
        ("a corpo", None, None),
        # A fee above the client's budget is rebase's own loss, never a negative reward.
        ("a giornata", 1, Decimal("0")),
    ],
)
def test_reward_base_formula(modalita: str, giorni: int | None, expected: Decimal | None) -> None:
    budget = Decimal("800")
    compenso = Decimal("450") if modalita == "a giornata" or giorni is None else Decimal("9000")
    if modalita == "a giornata" and giorni == 1:
        compenso = Decimal("900")  # above the day's budget, on purpose
    assert reward_base(budget, compenso, modalita, giorni) == expected


def test_reward_base_compares_the_two_modes_it_stores() -> None:
    # Day rate: margin 800 - 450 over 20 days, not the lump-sum reading (800 * 20 - 450).
    assert reward_base(Decimal("800"), Decimal("450"), DAY_RATE, 20) == Decimal("7000")
    assert reward_base(Decimal("800"), Decimal("450"), DAY_RATE, None) == Decimal("350")
    # Lump sum: the fee against the client's projected total, none without an estimate.
    assert reward_base(Decimal("800"), Decimal("9000"), LUMP_SUM, 20) == Decimal("7000")
    assert reward_base(Decimal("800"), Decimal("9000"), LUMP_SUM, None) is None


# ---- the reward, at the first signed letter ----------------------------------------


def _sign_the_letter(
    session: Session, renderer: FakeRenderer, fake: FakeDocumenso, match: Match
) -> None:
    envelope = _envelope_of(_letter_of(session, match.id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(session, renderer, fake, RecordingSender())
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)


def test_a_referred_freelancer_and_a_referred_company_each_earn_a_reward_once(
    clean: Session,
) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    _log_in(clean, "wile@acme.it")
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)

    _sign_the_letter(clean, renderer, fake, match)

    rewards = clean.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
        .order_by(Referral.kind)
    ).all()
    by_kind = {clean.get(Referral, r.referral_id).kind: r for r in rewards}  # type: ignore[union-attr]
    assert set(by_kind) == {"company", "freelancer"}
    # Margin: (800 - 450) * 20 giorni previsti = 7000.
    assert by_kind["freelancer"].rate == Decimal("0.1000")
    assert by_kind["freelancer"].base_amount == Decimal("7000.00")
    assert by_kind["freelancer"].reward_amount == Decimal("700.00")
    assert by_kind["company"].rate == Decimal("0.3000")
    assert by_kind["company"].base_amount == Decimal("7000.00")
    assert by_kind["company"].reward_amount == Decimal("2100.00")
    assert by_kind["freelancer"].stato == "da_confermare"

    # Re-running the confirmation (a repeated webhook, a sweep) never doubles the reward.
    ReferralService(clean).record_reward_if_signed(_letter_of(clean, match.id), match)
    clean.commit()
    again = clean.scalars(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.referrer_user_id == referrer_id)
    ).all()
    assert len(again) == 2


# ---- a referral counts once the referred person is verified (REB-658) --------------


def _referral_of(session: Session, entity_id: UUID) -> Referral:
    session.expire_all()
    row = session.scalar(select(Referral).where(Referral.entity_id == entity_id))
    assert row is not None
    return row


def test_a_referral_is_pending_when_it_is_made_and_has_nothing_to_confirm(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)

    for entity_id in (card_id, company_id):
        referral = _referral_of(clean, entity_id)
        assert (referral.stato, referral.verified_at, referral.verified_via) == (
            "da_verificare",
            None,
            None,
        )
    items = ReferralService(clean).list_rewards().items
    assert {(i.kind, i.referral_stato, i.reward_id, i.stato) for i in items} == {
        ("freelancer", "da_verificare", None, None),
        ("company", "da_verificare", None, None),
    }


def test_the_first_login_after_the_referral_verifies_it_and_only_the_loggers_own(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    ada = _card(clean, rif=code)
    grace = _card(clean, email="grace@studio.it", rif=code)
    wile = _request(clean, rif=code)

    _log_in(clean, "ada@studio.it")

    verified = _referral_of(clean, ada)
    assert (verified.stato, verified.verified_via) == ("verificato", "accesso")
    assert verified.verified_at is not None and verified.verified_at >= verified.created_at
    assert _referral_of(clean, grace).stato == "da_verificare"
    assert _referral_of(clean, wile).stato == "da_verificare"

    # A second login changes nothing: the proof is the first one.
    first = verified.verified_at
    _log_in(clean, "ada@studio.it")
    assert _referral_of(clean, ada).verified_at == first

    _log_in(clean, "wile@acme.it")
    assert _referral_of(clean, wile).stato == "verificato"


def test_a_login_from_before_the_referral_proves_nothing_about_it(clean: Session) -> None:
    """Someone posts the address of a member who already logged in, with an accomplice's
    code: the old login must not count the new referral at once."""
    _request(clean, email="ada@studio.it")
    _log_in(clean, "ada@studio.it")
    code = ReferralService(clean).code_for(_member(clean))

    card_id = _card(clean, email="ada@studio.it", rif=code)

    assert _referral_of(clean, card_id).stato == "da_verificare"
    _log_in(clean, "ada@studio.it")
    assert _referral_of(clean, card_id).stato == "verificato"


def test_a_squatted_company_earns_nothing_until_its_referente_logs_in_then_pays_the_squatter(
    clean: Session,
) -> None:
    """The squatting case, end to end. Someone posts a prospect's address with an
    accomplice's code. A freelancer is matched with that company and signs; the real
    referente has never logged in, so nothing is owed. When they do, the attribution
    they never made counts, for the accomplice and for nobody else: the first writer wins
    an address's attribution (REB-646), the proof here is of the person, not of the code."""
    accomplice = ReferralService(clean).code_for(_member(clean, "accomplice@community.it"))
    honest = ReferralService(clean).code_for(_member(clean, "honest@community.it"))
    admin_id = _admin(clean)
    freelancer_id = _card(clean)
    company_id = _request(clean, email="prospect@acme.it", rif=accomplice)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)

    listed = {r.kind: r for r in _match_list(clean)[0].referrals}
    assert listed["company"].stato == "da_verificare"
    assert (listed["company"].rate, listed["company"].amount) == (None, None)
    (ledger,) = ReferralService(clean).list_rewards().items
    assert (ledger.referral_stato, ledger.match_id, ledger.projected_amount) == (
        "da_verificare",
        None,
        None,
    )

    _sign_the_letter(clean, renderer, fake, match)
    assert clean.scalar(select(ReferralReward)) is None

    # The real person comes back with the code of someone who did refer them: no change.
    _request(clean, email="prospect@acme.it", rif=honest)
    assert clean.scalar(select(ReferralReward)) is None

    _log_in(clean, "prospect@acme.it")

    (item,) = ReferralService(clean).list_rewards().items
    assert item.referral_stato == "verificato" and item.verified_via == "accesso"
    assert item.referrer_email == "accomplice@community.it"
    assert (item.stato, item.base_amount, item.reward_amount) == (
        "da_confermare",
        Decimal("7000.00"),
        Decimal("2100.00"),
    )
    assert item.match_id == match.id
    (reward,) = clean.scalars(select(ReferralReward)).all()
    assert reward.document_id == _letter_of(clean, match.id).id


def test_a_referred_freelancers_own_signature_verifies_them_and_writes_the_reward(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    # Before the signature the pending freelancer's reward is still projected: signing
    # is what verifies them. The pending company's is not.
    by_kind = {i.kind: i for i in ReferralService(clean).list_rewards().items}
    assert by_kind["freelancer"].projected_amount == Decimal("700.00")
    assert by_kind["company"].projected_amount is None and by_kind["company"].match_id is None

    _sign_the_letter(clean, renderer, fake, match)

    freelancer = _referral_of(clean, freelancer_id)
    assert (freelancer.stato, freelancer.verified_via) == ("verificato", "lettera")
    assert freelancer.verified_at == _letter_of(clean, match.id).signed_at
    assert _referral_of(clean, company_id).stato == "da_verificare"
    (reward,) = clean.scalars(select(ReferralReward)).all()
    assert reward.referral_id == freelancer.id and reward.reward_amount == Decimal("700.00")


def test_a_login_after_the_signature_does_not_write_a_second_reward(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, match)

    _log_in(clean, "ada@studio.it")
    _log_in(clean, "wile@acme.it")

    assert len(clean.scalars(select(ReferralReward)).all()) == 2
    assert _referral_of(clean, freelancer_id).verified_via == "lettera"
    assert _referral_of(clean, company_id).verified_via == "accesso"


def test_the_database_refuses_a_referral_half_verified(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)

    for statement in (
        "UPDATE referrals SET stato = 'verificato'",
        "UPDATE referrals SET verified_at = now()",
        "UPDATE referrals SET stato = 'verificato', verified_at = now()",
        "UPDATE referrals SET stato = 'verificato', verified_at = now(), verified_via = 'x'",
    ):
        with pytest.raises(IntegrityError):
            clean.execute(text(statement))
        clean.rollback()
    assert _referral_of(clean, card_id).stato == "da_verificare"


def test_an_unreferred_match_earns_no_reward(clean: Session) -> None:
    admin_id = _admin(clean)
    freelancer_id = _card(clean)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)

    _sign_the_letter(clean, renderer, fake, match)

    assert clean.scalar(select(ReferralReward)) is None


def test_a_referred_freelancers_framework_agreement_names_the_referrer(
    clean: Session,
) -> None:
    """`_quadro_data` prints the freelancer's own referrer, exercised through
    `write_framework`'s real path (no `_active_framework` shortcut here, unlike the
    reward tests above, so the framework agreement is actually generated)."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _log_in(clean, "ada@studio.it")
    renderer = FakeRenderer(draft=False)
    MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    quadro = _documents(clean, freelancer_id, "quadro")[-1]
    assert quadro.data["segnalato-da"] == "Mario Rossi"


def test_a_pending_freelancer_referral_is_not_printed_on_the_framework_agreement(
    clean: Session,
) -> None:
    """REB-664: a `da_verificare` referral may be squatted, so the agreement prints
    `nessuno`. The framework agreement is written before the freelancer's first letter
    signature (which is what verifies them), so it stays `nessuno` unless they logged in."""
    code = ReferralService(clean).code_for(_member(clean))
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    assert _referral_of(clean, freelancer_id).stato == "da_verificare"
    quadro = _documents(clean, freelancer_id, "quadro")[-1]
    assert quadro.data["segnalato-da"] == "nessuno"


def test_a_pending_company_referral_is_not_printed_on_the_letter_until_verified(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    admin_id = _admin(clean)
    freelancer_id = _card(clean)
    company_id = _request(clean, rif=code)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    pending = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )
    assert _letter_of(clean, pending.id).data["azienda-segnalata-da"] == "nessuno"

    _log_in(clean, "wile@acme.it")
    verified = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )
    assert _letter_of(clean, verified.id).data["azienda-segnalata-da"] == "Mario Rossi"


def test_an_unreferred_freelancers_framework_agreement_prints_nessuno_not_a_blank(
    clean: Session,
) -> None:
    """A blank `{{segnalato-da}}` would print as a hand-fillable line on every
    framework agreement, since nobody referred the overwhelming majority of
    freelancers: `nessuno` reads as a fact instead (the `PEC_MISSING` convention)."""
    admin_id = _admin(clean)
    freelancer_id = _card(clean)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    quadro = _documents(clean, freelancer_id, "quadro")[-1]
    assert quadro.data["segnalato-da"] == "nessuno"


def test_a_referred_companys_letter_names_the_referrer_and_an_unreferred_one_prints_nessuno(
    clean: Session,
) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean)
    referred_company_id = _request(clean, rif=code, email="wile@acme.it")
    _log_in(clean, "wile@acme.it")
    unreferred_company_id = _request(clean, email="other@beta.it", nome_azienda="Beta Srl")
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer = FakeRenderer(draft=False)
    referred_match = _sent_with_body(
        clean, renderer, FakeDocumenso(), freelancer_id, referred_company_id, admin_id
    )
    unreferred_match = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(unreferred_company_id), admin_id
    )

    referred_letter = _letter_of(clean, referred_match.id)
    unreferred_letter = _letter_of(clean, unreferred_match.id)
    assert referred_letter.data["azienda-segnalata-da"] == "Mario Rossi"
    assert unreferred_letter.data["azienda-segnalata-da"] == "nessuno"


# ---- the admin's two rates and ledger ------------------------------------------------


def test_get_settings_seeds_the_documented_defaults(clean: Session) -> None:
    settings = ReferralService(clean).get_settings()
    assert (settings.rate_freelancer, settings.rate_company) == (
        Decimal("0.1000"),
        Decimal("0.3000"),
    )


def test_save_settings_updates_the_one_row(clean: Session) -> None:
    admin_id = _admin(clean)
    service = ReferralService(clean)
    before = service.get_settings()

    after = service.save_settings(Decimal("0.12"), Decimal("0.35"), admin_id)

    assert after.id == before.id
    assert (after.rate_freelancer, after.rate_company) == (Decimal("0.1200"), Decimal("0.3500"))
    assert after.updated_by == admin_id


def test_set_state_moves_one_step_at_a_time(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, match)
    reward = clean.scalar(select(ReferralReward))
    assert reward is not None
    service = ReferralService(clean)

    with pytest.raises(ValidationFailed, match="si passa solo a"):
        service.set_state(reward.id, "pagato", admin_id)

    confirmed = service.set_state(reward.id, "confermato", admin_id)
    assert confirmed.stato == "confermato"
    assert confirmed.confirmed_by == admin_id
    assert confirmed.confirmed_at is not None

    paid = service.set_state(reward.id, "pagato", admin_id)
    assert paid.stato == "pagato"
    assert paid.paid_at is not None


def test_set_price_only_before_confirmation(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, match)
    reward = clean.scalar(select(ReferralReward))
    assert reward is not None
    service = ReferralService(clean)

    priced = service.set_price(reward.id, Decimal("100.00"), Decimal("10.00"))
    assert (priced.base_amount, priced.reward_amount) == (Decimal("100.00"), Decimal("10.00"))

    service.set_state(reward.id, "confermato", admin_id)
    with pytest.raises(ValidationFailed, match="prima della conferma"):
        service.set_price(reward.id, Decimal("1"), Decimal("1"))


def _signed_reward(clean: Session, admin_id: UUID) -> UUID:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, match)
    reward = clean.scalar(select(ReferralReward))
    assert reward is not None
    return reward.id


def test_a_stale_writer_cannot_reprice_a_reward_another_admin_just_confirmed(
    clean: Session,
) -> None:
    """REB-566: `set_price` read `stato`, checked it, then wrote, with no lock. A second
    session that had loaded the reward before the confirmation committed still saw
    `da_confermare` and wrote its price over the confirmed figure. The locking read
    re-reads the row, so the stale session is refused."""
    admin_id = _admin(clean)
    reward_id = _signed_reward(clean, admin_id)
    before = clean.get(ReferralReward, reward_id)
    assert before is not None
    confirmed_amount = before.reward_amount

    with session_factory(clean.get_bind())() as stale:  # type: ignore[arg-type]
        loaded = stale.get(ReferralReward, reward_id)
        assert loaded is not None and loaded.stato == "da_confermare"

        ReferralService(clean).set_state(reward_id, "confermato", admin_id)

        with pytest.raises(ValidationFailed, match="prima della conferma"):
            ReferralService(stale).set_price(reward_id, Decimal("1.00"), Decimal("0.10"))

    clean.expire_all()
    stored = clean.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.stato == "confermato"
    assert stored.reward_amount == confirmed_amount


def test_a_stale_writer_cannot_confirm_a_reward_twice(clean: Session) -> None:
    """REB-566: the same check-then-write gap in `set_state`: two admins confirming the
    same reward must leave the first one's signature and timestamp, and the second is
    told the reward has moved on."""
    admin_id = _admin(clean)
    other_admin = User(email="anna@rebase.it", nome="Anna", cognome="", role="admin")
    clean.add(other_admin)
    clean.commit()
    reward_id = _signed_reward(clean, admin_id)

    with session_factory(clean.get_bind())() as stale:  # type: ignore[arg-type]
        loaded = stale.get(ReferralReward, reward_id)
        assert loaded is not None and loaded.stato == "da_confermare"

        ReferralService(clean).set_state(reward_id, "confermato", admin_id)

        with pytest.raises(ValidationFailed, match="si passa solo a pagato"):
            ReferralService(stale).set_state(reward_id, "confermato", other_admin.id)

    clean.expire_all()
    stored = clean.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.confirmed_by == admin_id


def test_a_price_waits_for_the_confirmation_in_flight_then_is_refused(clean: Session) -> None:
    """REB-566, Greptile on fb9dd39a2: the two stale-writer tests above finish the first
    write before the second starts, so they would pass without the row lock too. This is
    the overlap: `clean` has moved the reward to `confermato`, locked and uncommitted,
    and a second session's `set_price` starts meanwhile. It must block on the locking
    read and, once `clean` commits, see `confermato` and refuse. Without `FOR UPDATE` the
    plain read returns the committed `da_confermare`, the check passes, and only the
    final UPDATE waits, then writes the price over the confirmed figure: no error."""
    admin_id = _admin(clean)
    reward_id = _signed_reward(clean, admin_id)
    row = clean.get(ReferralReward, reward_id, with_for_update=True)
    assert row is not None
    confirmed_amount = row.reward_amount
    row.stato = "confermato"
    row.confirmed_by = admin_id
    clean.flush()

    factory = session_factory(clean.get_bind())  # type: ignore[arg-type]
    outcome: list[BaseException] = []
    started = threading.Event()
    backend: list[int] = []

    def price_from_another_session() -> None:
        # The session is opened, used and closed on this thread alone.
        with factory() as other:
            try:
                backend.append(other.execute(text("SELECT pg_backend_pid()")).scalar_one())
                started.set()
                ReferralService(other).set_price(reward_id, Decimal("1.00"), Decimal("0.10"))
            except ValidationFailed as exc:
                outcome.append(exc)
            finally:
                other.rollback()
                started.set()

    worker = threading.Thread(target=price_from_another_session)
    worker.start()
    try:
        assert started.wait(timeout=5), "the second session never connected"
        # Wait until the database itself reports the worker parked on a lock inside the
        # locking SELECT. Without `FOR UPDATE` that read never waits: the worker's first
        # wait would be the UPDATE at commit, whose query text is not a SELECT. A
        # transaction caches `pg_stat_activity` after its first read, and `clean` stays
        # in one until the commit below, so each poll drops that cache first.
        for _ in range(100):
            clean.execute(text("SELECT pg_stat_clear_snapshot()"))
            blocked = clean.execute(
                text(
                    "SELECT 1 FROM pg_stat_activity WHERE pid = :pid "
                    "AND wait_event_type = 'Lock' AND query ILIKE 'SELECT%FOR UPDATE%'"
                ),
                {"pid": backend[0]},
            ).first()
            if blocked is not None:
                break
            worker.join(timeout=0.05)
        assert blocked is not None, "set_price did not wait on the row the confirmation holds"
    finally:
        # Release the row whatever happened above, so a failed assertion above never
        # leaves the worker parked on a lock the fixture's teardown then waits on.
        clean.commit()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert len(outcome) == 1 and "prima della conferma" in str(outcome[0])
    clean.expire_all()
    stored = clean.get(ReferralReward, reward_id)
    assert stored is not None
    assert stored.reward_amount == confirmed_amount


def test_list_rewards_shows_a_referral_before_any_letter_is_signed(clean: Session) -> None:
    """Regression: the ledger used to start from `ReferralReward`, so a referral
    that had only just signed up, with no letter signed yet, never had a row at all
    -- the admin had no way to tell it existed (Greptile, P-REB-44)."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    _card(clean, rif=code)
    service = ReferralService(clean)

    page = service.list_rewards()

    assert len(page.items) == 1
    item = page.items[0]
    assert item.referred_nome == "Ada Lovelace"
    assert item.reward_id is None
    assert item.stato is None
    assert item.rate is None
    assert (item.base_amount, item.reward_amount) == (None, None)


def test_set_state_refuses_to_confirm_an_unpriced_reward(clean: Session) -> None:
    """The same rule `set_price` already enforces from the other side (Greptile,
    P-REB-44): a fixed-price letter with no estimate leaves a reward with no figure,
    and confirming it anyway would make it unpriceable forever, since `set_price`
    itself only accepts `da_confermare`."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    body = _match_body(company_id, giorni_previsti=None)
    body.lettera.modalita = body.lettera.unita = "a corpo"
    match = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, body, admin_id
    )
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    match = clean.get(Match, match.id)
    assert match is not None
    _sign_the_letter(clean, renderer, fake, match)
    reward = clean.scalar(select(ReferralReward))
    assert reward is not None and reward.reward_amount is None
    service = ReferralService(clean)

    with pytest.raises(ValidationFailed, match="prezzato prima di confermarlo"):
        service.set_state(reward.id, "confermato", admin_id)


def test_reward_reads_the_matchs_own_budget_snapshot_not_a_later_company_edit(
    clean: Session,
) -> None:
    """Regression: this used to re-read `Company.budget_giornaliero` live, at signing
    time; an admin editing the company's request between the match and the signature
    would silently change a reward the match had already promised (Greptile,
    P-REB-44). `matches.company_budget_giornaliero` snapshots it at match creation,
    the same way `lettera_compenso` already does."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean)
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )
    match_row = clean.get(Match, match.id)
    assert match_row is not None
    assert match_row.company_budget_giornaliero == Decimal("800.00")

    company = clean.get(Company, company_id)
    assert company is not None
    company.budget_giornaliero = Decimal("2000")
    clean.commit()

    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    match = clean.get(Match, match.id)
    assert match is not None
    _sign_the_letter(clean, renderer, fake, match)

    reward = clean.scalar(select(ReferralReward))
    assert reward is not None
    # (800 - 450) * 20, the budget at match creation, never the 2000 it became after.
    assert reward.base_amount == Decimal("7000.00")


# ---- what a match would earn, and where the lists show it (REB-609) ---------------------


def _statements(session: Session) -> tuple[list[str], Callable[[], None]]:
    """A recorder of every statement the session's engine runs, and the call that stops it."""
    seen: list[str] = []

    def record(_c: object, _cur: object, statement: str, *_rest: object) -> None:
        seen.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(engine, "before_cursor_execute", record)


def _referred_pair(clean: Session) -> tuple[UUID, UUID, UUID, UUID]:
    """A referrer, and a freelancer card and a company request both brought in by his
    code, with tax data and an active framework agreement so a match can be written. The
    company's referente has logged in since, so both referrals are verified on the
    company side too (the freelancer's is by its own signature)."""
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    freelancer_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    _log_in(clean, "wile@acme.it")
    _fiscal(clean, freelancer_id, admin_id)
    _active_framework(clean, freelancer_id, admin_id)
    return referrer_id, admin_id, freelancer_id, company_id


def _match_list(session: Session) -> list[MatchListItem]:
    service = MatchService(session, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    return service.list_all(stato=None, q=None, limit=100, offset=0).items


@pytest.mark.parametrize(
    ("stato", "cancelled", "modalita", "giorni", "expected"),
    [
        ("bozza", False, "a giornata", 20, Decimal("700.00")),
        ("in_firma", False, "a giornata", None, Decimal("35.00")),
        ("bozza", False, "a corpo", 20, Decimal("1555.00")),
        ("bozza", False, "a corpo", None, None),
        ("annullato", False, "a giornata", 20, None),
        ("bozza", True, "a giornata", 20, None),
        ("attivo", False, "a giornata", 20, None),
        ("concluso", False, "a giornata", 20, None),
    ],
)
def test_project_reward_follows_reward_base_and_only_while_a_signature_can_still_land(
    stato: str, cancelled: bool, modalita: str, giorni: int | None, expected: Decimal | None
) -> None:
    match = Match(
        stato=stato,
        cancelled_at=datetime(2026, 9, 1, tzinfo=UTC) if cancelled else None,
        lettera_compenso=Decimal("450"),
        company_budget_giornaliero=Decimal("800"),
        giorni_previsti=giorni,
    )
    projected = ReferralService.project_reward(match, modalita, Decimal("0.10"))
    assert projected == expected


def test_the_match_list_shows_a_referral_projected_then_real_then_by_state(
    clean: Session,
) -> None:
    referrer_id, admin_id, freelancer_id, company_id = _referred_pair(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)

    (before,) = _match_list(clean)
    assert before.company_id == company_id
    assert (before.lettera_compenso, before.lettera_unita) == (Decimal("450.00"), "a giornata")
    assert [
        (r.kind, r.referrer_nome, r.stato, r.rate, r.amount, r.reward_id) for r in before.referrals
    ] == [
        ("freelancer", "Mario Rossi", "previsto", Decimal("0.1000"), Decimal("700.00"), None),
        ("company", "Mario Rossi", "previsto", Decimal("0.3000"), Decimal("2100.00"), None),
    ]

    _sign_the_letter(clean, renderer, fake, match)
    # A rate edited after the signature never moves what the signature already earned.
    ReferralService(clean).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    service = ReferralService(clean)
    company_reward = clean.scalar(
        select(ReferralReward)
        .join(Referral, Referral.id == ReferralReward.referral_id)
        .where(Referral.kind == "company")
    )
    assert company_reward is not None
    service.set_state(company_reward.id, "confermato", admin_id)

    (after,) = _match_list(clean)
    by_kind = {r.kind: r for r in after.referrals}
    assert (by_kind["freelancer"].stato, by_kind["freelancer"].rate) == (
        "da_confermare",
        Decimal("0.1000"),
    )
    assert by_kind["freelancer"].amount == Decimal("700.00")
    assert (by_kind["company"].stato, by_kind["company"].amount) == (
        "confermato",
        Decimal("2100.00"),
    )
    assert by_kind["company"].reward_id == company_reward.id


def test_a_referral_that_already_paid_on_another_match_is_gia_maturato_here(
    clean: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    first = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, first)
    other_company = _request(clean, email="road@runner.it", nome_azienda="Road Runner")
    second = MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(other_company), admin_id
    )

    listed = {item.id: item for item in _match_list(clean)}

    # The company side of the first match earned there; the freelancer side of the second
    # match belongs to a referral that already paid, and the other company was never referred.
    assert [r.kind for r in listed[first.id].referrals] == ["freelancer", "company"]
    assert {r.kind: r.stato for r in listed[first.id].referrals} == {
        "freelancer": "da_confermare",
        "company": "da_confermare",
    }
    (elsewhere,) = listed[second.id].referrals
    assert (elsewhere.kind, elsewhere.stato) == ("freelancer", "gia_maturato")
    assert (elsewhere.rate, elsewhere.amount, elsewhere.reward_id) == (None, None, None)


def test_a_match_with_no_referral_lists_none_and_an_unpriceable_one_projects_nothing(
    clean: Session,
) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    plain_id = _card(clean, email="plain@studio.it")
    referred_id = _card(clean, email="referred@studio.it", rif=code)
    company_id = _request(clean)
    for freelancer_id in (plain_id, referred_id):
        _fiscal(clean, freelancer_id, admin_id)
        _active_framework(clean, freelancer_id, admin_id)
    service = MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    plain = service.create(plain_id, _match_body(company_id), admin_id)
    body = _match_body(company_id, giorni_previsti=None)
    body.lettera.modalita = body.lettera.unita = "a corpo"
    lump = service.create(referred_id, body, admin_id)

    listed = {item.id: item for item in _match_list(clean)}

    assert listed[plain.id].referrals == []
    (side,) = listed[lump.id].referrals
    assert (side.stato, side.amount) == ("previsto", None)
    assert listed[lump.id].lettera_unita == "a corpo"


def test_a_cancelled_match_still_names_its_referral_but_promises_no_amount(
    clean: Session,
) -> None:
    _, admin_id, freelancer_id, company_id = _referred_pair(clean)
    service = MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    match = service.create(freelancer_id, _match_body(company_id), admin_id)
    service.cancel(match.id, admin_id)

    (row,) = _match_list(clean)

    assert [(r.stato, r.amount) for r in row.referrals] == [("previsto", None), ("previsto", None)]


def test_a_referrer_with_a_card_of_his_own_is_named_with_it(clean: Session) -> None:
    referrer_card = _card(clean, email="ref@studio.it")
    referrer_user = clean.scalar(select(Freelancer.user_id).where(Freelancer.id == referrer_card))
    assert referrer_user is not None
    code = ReferralService(clean).code_for(referrer_user)
    admin_id = _admin(clean)
    referred_id = _card(clean, email="referred@studio.it", rif=code)
    company_id = _request(clean)
    _fiscal(clean, referred_id, admin_id)
    _active_framework(clean, referred_id, admin_id)
    MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY).create(
        referred_id, _match_body(company_id), admin_id
    )

    (row,) = _match_list(clean)
    (side,) = row.referrals
    (ledger,) = ReferralService(clean).list_rewards().items

    assert side.referrer_freelancer_id == referrer_card
    assert ledger.referrer_freelancer_id == referrer_card
    assert ledger.referrer_nome == "Ada Lovelace"


def test_the_match_list_reads_its_referrals_in_a_page_wide_handful_of_queries(
    clean: Session,
) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    service = MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    for index in range(4):
        freelancer_id = _card(clean, email=f"f{index}@studio.it", rif=code)
        company_id = _request(clean, email=f"c{index}@acme.it", rif=code)
        _fiscal(clean, freelancer_id, admin_id)
        _active_framework(clean, freelancer_id, admin_id)
        service.create(freelancer_id, _match_body(company_id), admin_id)

    service.list_all(stato=None, q=None, limit=1, offset=0)  # warm the session's own caches
    seen, stop = _statements(clean)
    try:
        one = service.list_all(stato=None, q=None, limit=1, offset=0)
        for_one = len(seen)
        seen.clear()
        four = service.list_all(stato=None, q=None, limit=100, offset=0)
        for_four = len(seen)
    finally:
        stop()

    assert len(one.items) == 1 and len(four.items) == 4
    assert all(len(item.referrals) == 2 for item in four.items)
    assert for_four == for_one


def test_the_ledger_projects_a_pending_referral_from_its_live_match_and_keeps_real_figures(
    clean: Session,
) -> None:
    referrer_id, admin_id, freelancer_id, company_id = _referred_pair(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    service = MatchService(clean, renderer, SIGNER, today=lambda: TODAY)
    service.create(freelancer_id, _match_body(company_id, giorni_previsti=5), admin_id)
    cancelled = service.create(freelancer_id, _match_body(company_id), admin_id)
    service.cancel(cancelled.id, admin_id)
    live = service.create(freelancer_id, _match_body(company_id, giorni_previsti=10), admin_id)
    # A newer match whose letter is already out of the running cannot mature the reward.
    past = service.create(freelancer_id, _match_body(company_id, giorni_previsti=1), admin_id)
    clean.get(Match, past.id).stato = "attivo"  # type: ignore[union-attr]
    clean.commit()

    pending = {item.kind: item for item in ReferralService(clean).list_rewards().items}

    freelancer, company = pending["freelancer"], pending["company"]
    assert (freelancer.referred_id, company.referred_id) == (freelancer_id, company_id)
    assert freelancer.reward_id is None and freelancer.reward_amount is None
    # The newest match that can still earn (not the older one, not the cancelled one in
    # between, not the newer one already active), projected on its own days: 350 * 10.
    assert (freelancer.match_id, company.match_id) == (live.id, live.id)
    assert (freelancer.projected_rate, freelancer.projected_amount) == (
        Decimal("0.1000"),
        Decimal("350.00"),
    )
    assert company.projected_amount == Decimal("1050.00")
    assert (freelancer.match_nome_azienda, freelancer.match_freelancer_nome) == (
        "ACME Srl",
        "Ada Lovelace",
    )
    assert freelancer.match_freelancer_id == freelancer_id

    # Once signed the real figures stand, the projection is gone, even at another rate.
    signed = _sent_with_body(clean, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean, renderer, fake, signed)
    ReferralService(clean).save_settings(Decimal("0.5"), Decimal("0.5"), admin_id)
    real = {item.kind: item for item in ReferralService(clean).list_rewards().items}
    assert real["freelancer"].reward_amount == Decimal("700.00")
    assert real["freelancer"].match_id == signed.id
    assert (real["freelancer"].projected_rate, real["freelancer"].projected_amount) == (None, None)


def test_the_ledger_reads_a_page_in_a_row_independent_number_of_queries(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)
    admin_id = _admin(clean)
    service = MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    for index in range(4):
        freelancer_id = _card(clean, email=f"f{index}@studio.it", rif=code)
        company_id = _request(clean, email=f"c{index}@acme.it", rif=code)
        _log_in(clean, f"c{index}@acme.it")
        _fiscal(clean, freelancer_id, admin_id)
        _active_framework(clean, freelancer_id, admin_id)
        service.create(freelancer_id, _match_body(company_id), admin_id)

    ReferralService(clean).list_rewards(limit=2)  # warm the session's own caches
    seen, stop = _statements(clean)
    try:
        two = ReferralService(clean).list_rewards(limit=2)
        for_two = len(seen)
        seen.clear()
        many = ReferralService(clean).list_rewards(limit=100)
        for_many = len(seen)
    finally:
        stop()

    assert {item.kind for item in two.items} == {"freelancer", "company"} and len(many.items) == 8
    assert {(item.kind, item.projected_amount) for item in many.items} == {
        ("freelancer", Decimal("700.00")),
        ("company", Decimal("2100.00")),
    }
    assert for_many == for_two


def test_a_deleted_request_or_card_is_flagged_so_no_page_links_to_it(clean: Session) -> None:
    """A deleted card or request answers not found on its own page: both lists say so
    instead of leaving a name that opens nothing."""
    _, admin_id, freelancer_id, company_id = _referred_pair(clean)
    other = _card(
        clean,
        email="grace@studio.it",
        rif=ReferralService(clean).code_for(_member(clean, "lia@community.it")),
    )
    service = MatchService(clean, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    service.create(freelancer_id, _match_body(company_id), admin_id)
    CompanyService(clean).soft_delete(company_id, admin_id)
    FreelancerService(clean).soft_delete(other, admin_id)

    (row,) = _match_list(clean)
    ledger = {item.referred_id: item for item in ReferralService(clean).list_rewards().items}

    assert row.company_deleted is True
    assert ledger[company_id].referred_deleted is True
    assert ledger[other].referred_deleted is True
    assert ledger[freelancer_id].referred_deleted is False
    assert ledger[freelancer_id].match_freelancer_deleted is False

    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    after = {item.referred_id: item for item in ReferralService(clean).list_rewards().items}
    assert after[freelancer_id].referred_deleted is True
    assert after[company_id].match_freelancer_deleted is True


def _evidence(session: Session, entity_id: UUID):  # type: ignore[no-untyped-def]
    (item,) = [
        item
        for item in ReferralService(session).list_rewards().items
        if item.referred_id == entity_id
    ]
    return item.evidence


def test_the_evidence_names_the_code_the_signup_used(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)

    assert _evidence(clean, card_id).code == code


def test_the_evidence_dates_the_signup_from_the_referred_card_not_the_ledger_row(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    earlier = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    clean.execute(text("UPDATE freelancers SET created_at = :at"), {"at": earlier})
    clean.execute(text("UPDATE companies SET created_at = :at"), {"at": earlier})
    clean.commit()

    assert _evidence(clean, card_id).signed_up_at == earlier
    assert _evidence(clean, company_id).signed_up_at == earlier


def test_the_evidence_falls_back_on_the_referral_when_the_referred_row_is_gone(
    clean: Session,
) -> None:
    """`Referral.entity_id` has no foreign key (one column points at two tables), so a
    hard-deleted card leaves the referral behind: its own moment, nothing to compare."""
    card_id = _card(clean, rif=ReferralService(clean).code_for(_member(clean)))
    clean.execute(text("DELETE FROM freelancers WHERE id = :id"), {"id": card_id})
    clean.commit()
    referral = clean.scalars(select(Referral)).one()

    evidence = _evidence(clean, card_id)

    assert evidence.signed_up_at == referral.created_at
    # Unknown, not a reassuring "no": the person's email and logins cannot be looked up.
    assert (evidence.utm_source, evidence.same_email_domain, evidence.ever_logged_in) == (
        None,
        None,
        None,
    )


def test_the_evidence_carries_the_utm_source_when_one_was_recorded(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    tracked = _card(clean, email="ada@studio.it", rif=code, utm_source="linkedin")
    untracked = _card(clean, email="grace@studio.it", rif=code)
    company = _request(clean, email="wile@acme.it", rif=code, utm=SignupUtm(utm_source="google"))

    assert _evidence(clean, company).utm_source == "google"
    assert _evidence(clean, tracked).utm_source == "linkedin"
    assert _evidence(clean, untracked).utm_source is None


def test_the_evidence_flags_a_shared_company_domain_on_a_freelancer_and_on_a_company(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean, "mario@studio.it"))
    colleague = _card(clean, email="ada@studio.it", rif=code)
    stranger = _card(clean, email="grace@other.it", rif=code)
    company = _request(clean, email="wile@STUDIO.it", rif=code)

    assert _evidence(clean, colleague).same_email_domain is True
    assert _evidence(clean, company).same_email_domain is True
    assert _evidence(clean, stranger).same_email_domain is False


def test_a_shared_public_mail_domain_is_not_a_match(clean: Session) -> None:
    code = ReferralService(clean).code_for(_member(clean, "mario@gmail.com"))
    card_id = _card(clean, email="ada@gmail.com", rif=code)

    assert _evidence(clean, card_id).same_email_domain is False


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
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)

    assert _evidence(clean, card_id).ever_logged_in is False
    assert _evidence(clean, company_id).ever_logged_in is False


def test_the_evidence_says_a_referred_person_has_logged_in_once_a_login_exists(
    clean: Session,
) -> None:
    code = ReferralService(clean).code_for(_member(clean))
    card_id = _card(clean, rif=code)
    company_id = _request(clean, rif=code)
    other_id = _card(clean, email="grace@studio.it", rif=code)
    card = clean.get(Freelancer, card_id)
    company = clean.get(Company, company_id)
    assert card is not None and company is not None
    # Two logins by the same person still make one `True`, never two rows in the ledger.
    clean.add_all([Login(user_id=card.user_id), Login(user_id=card.user_id)])
    clean.add(Login(user_id=company.user_id))
    clean.commit()

    assert _evidence(clean, card_id).ever_logged_in is True
    assert _evidence(clean, company_id).ever_logged_in is True
    assert _evidence(clean, other_id).ever_logged_in is False
    assert len(ReferralService(clean).list_rewards().items) == 3
