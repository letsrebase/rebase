"""P-REB-44: a member's own code, who it brings in, and the reward that matures once
at the referred entity's first signed letter. The full sign-to-reward path reuses
`test_signing.py`'s own fixtures (`FakeRenderer`, `FakeDocumenso`, `_active_framework`,
`_sent`): nothing here runs pandoc or reaches a real Documenso."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import select, text
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

from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import ClienteData, LetteraFields, MatchCreate
from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import Company, Match, Referral, ReferralReward, User
from rebase_core.referrals import ReferralService, reward_base
from rebase_core.schemas import CompanyCreate, FreelancerCreate

TABLES = (
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


def _card(session: Session, *, email: str = "ada@studio.it", rif: str | None = None) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email=email,
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
            rif=rif,
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


def _match_body(company_id: UUID, *, giorni_previsti: int | None = 20) -> MatchCreate:
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
            modalita="a giornata",
            unita="a giornata",
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
) -> Match:
    """`test_signing._sent`'s own recipe (`_matches`/`_draft`/`send_match`), with
    `_match_body`'s explicit day rate and estimate instead of `test_matches._body`'s
    default, so the reward this earns is deterministic."""
    match = MatchService(session, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
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
    rows = clean.scalars(select(Referral).where(Referral.entity_id == freelancer_id)).all()
    assert len(rows) == 1
    assert rows[0].referrer_user_id == referrer_id

    other_freelancer_id = _card(clean, email="grace@studio.it")
    service.link_signup(
        "freelancer", other_freelancer_id, code, new_user_id=referrer_id
    )  # a member's own code, used for themself
    assert clean.scalar(select(Referral).where(Referral.entity_id == other_freelancer_id)) is None


def test_a_freelancer_application_with_rif_links_the_referral(clean: Session) -> None:
    referrer_id = _member(clean)
    code = ReferralService(clean).code_for(referrer_id)

    freelancer_id = _card(clean, rif=code)

    row = clean.scalar(select(Referral).where(Referral.entity_id == freelancer_id))
    assert row is not None
    assert (row.kind, row.referrer_user_id) == ("freelancer", referrer_id)


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
    renderer = FakeRenderer(draft=False)
    MatchService(clean, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id), admin_id
    )

    quadro = _documents(clean, freelancer_id, "quadro")[-1]
    assert quadro.data["segnalato-da"] == "Mario Rossi"


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
