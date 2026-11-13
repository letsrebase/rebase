"""What the referral tests share (HTEST-10): a member, a card, a company's request, a login,
the signed letter and the reward it earns, the match list and the evidence of a referral.
The tests are in `test_referrals.py` (codes and links), `test_referral_company_links.py`,
`test_referral_rewards.py`, `test_referral_settings.py`, `test_referral_ledger.py` and
`test_referral_evidence.py`.
"""

import re
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from admins import an_admin
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import PDF, SIGNER, TODAY, _fiscal
from signing_fixtures import (
    SIGNED_AT,
    _active_framework,
    _envelope_of,
    _letter_of,
    _signing,
    _webhook,
)
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.config import Settings
from rebase_core.contract_schemas import (
    CustomerDate,
    LetterFields,
    MatchCreate,
    MatchListItem,
)
from rebase_core.engagement_notices import EngagementNoticeService
from rebase_core.engagement_schemas import EngagementNotice
from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerDraft
from rebase_core.freelancers import FreelancerService
from rebase_core.mail import RecordingSender
from rebase_core.match_words import PAY_MODE_WORDS
from rebase_core.matches import MatchService
from rebase_core.models import (
    HubEngagement,
    Match,
    Referral,
    ReferralReward,
    TeamRequest,
    User,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referrals import ReferralService
from rebase_core.signup_schemas import SignupCreate, SignupUtm
from rebase_core.users import UserService


def _member(session: Session, email: str = "mario@community.it") -> UUID:
    user = User(email=email, first_name="Mario", last_name="Rossi", role="member")
    session.add(user)
    session.commit()
    return user.id


def _card(
    session: Session,
    *,
    email: str = "ada@studio.it",
    ref: str | None = None,
    utm_source: str | None = None,
) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            first_name="Ada",
            last_name="Lovelace",
            email=email,
            daily_rate=Decimal("450"),
            job_title="Backend developer",
            remote="remote",
            ref=ref,
            utm=SignupUtm(utm_source=utm_source) if utm_source else None,
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _request(
    session: Session, *, email: str = "wile@acme.it", ref: str | None = None, **change: object
) -> UUID:
    payload: dict[str, object] = {
        "company_name": "ACME Srl",
        "contact_first_name": "Wile",
        "contact_last_name": "E.",
        "email": email,
        "phone": "+39 345 1234567",
        "requested_role": "Backend developer",
        "project": "Le API del prodotto, per tre mesi.",
        "period_from": date(2026, 10, 1),
        "duration": "3 mesi",
        "daily_budget": Decimal("800"),
        "remote": "remote",
        "headcount": 1,
        "ref": ref,
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
    token = re.search(r"/verify\?t=([A-Za-z0-9_-]+)", mail.text)
    assert token is not None
    assert users.enter(token.group(1)) is not None


def _match_body(
    company_id: UUID, *, expected_days: int | None = 20, mode: str = "to_work_unit"
) -> MatchCreate:
    return MatchCreate(
        company_id=company_id,
        customer=CustomerDate(
            customer_legal_name="ACME S.r.l.",
            customer_vat_number="01234567890",
            customer_registered_office="Milano",
        ),
        letter=LetterFields(
            role="Backend developer",
            activities="Le API del prodotto.",
            start_date=date(2026, 10, 1),
            fee=Decimal("450"),
            mode=mode,
            unit=PAY_MODE_WORDS[mode],
            payment_days=30,
            month_end=True,
        ),
        expected_days=expected_days,
    )


def _sent_with_body(
    session: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    freelancer_id: UUID,
    company_id: UUID,
    admin_id: UUID,
    mode: str = "to_work_unit",
) -> Match:
    """`test_signing._sent`'s own recipe (`_matches`/`_draft`/`send_match`), with
    `_match_body`'s explicit day rate and estimate instead of `match_fixtures._body`'s
    default, so the reward this earns is deterministic."""
    match = MatchService(session, renderer, SIGNER, today=lambda: TODAY).create(
        freelancer_id, _match_body(company_id, mode=mode), admin_id
    )
    _signing(session, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    row = session.get(Match, match.id)
    assert row is not None
    _tell_the_hub(session, row, "awaiting_signature", sequence=1)
    return row


def _signup_with(session: Session, **extra: object) -> UUID:
    from rebase_core.service import SignupService

    payload: dict[str, object] = {
        "email": "cold@studio.it",
        "first_name": "Cold",
        "last_name": "Lead",
    }
    payload.update(extra)
    return SignupService(session).subscribe(SignupCreate(**payload)).id  # type: ignore[arg-type]


def _draft_for(session: Session, signup_id: UUID) -> UUID:
    return (
        FreelancerService(session)
        .draft_from_signup(
            signup_id,
            FreelancerDraft(
                first_name="Cold",
                last_name="Lead",
                job_title="Backend developer",
                sources=["https://cold.dev"],
            ),
            an_admin(session),
            "web",
        )
        .id
    )


# ---- a public team request's code, credited when a company exists (REB-600) -------


def _public_request(
    session: Session, *, email: str = "wile@acme.it", ref: str | None, company: str = "ACME Srl"
) -> None:
    session.add(
        TeamRequest(origin="public", company=company, email=email, phone="+39 345 1234567", ref=ref)
    )
    session.commit()


def _company_referral(session: Session, company_id: UUID) -> Referral | None:
    return session.scalar(select(Referral).where(Referral.entity_id == company_id))


def _fail_after_the_card_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: ReferralService, code: str | None) -> User | None:
        raise RuntimeError("the connection dropped after the card write")

    monkeypatch.setattr(ReferralService, "resolve_referrer", boom)


# ---- what the CRM's notices tell the hub (REB-1106) --------------------------------


def _notice(
    key: UUID, freelancer_id: UUID, company_id: UUID | None, **change: object
) -> EngagementNotice:
    """The CRM's notice for an engagement the hub keyed by `key` (its `hub_match_id`):
    a day-rate letter of 450 from 1 October 2026, signed unless `signed_at` says otherwise."""
    fields: dict[str, object] = {
        "sequence": 1,
        "kind": "created",
        "engagement_id": uuid4(),
        "hub_match_id": key,
        "hub_freelancer_id": freelancer_id,
        "hub_company_id": company_id,
        "client_name": "ACME S.r.l.",
        "mode": "day",
        "fee": Decimal("450"),
        "expected_days": Decimal("20"),
        "role": "Backend developer",
        "starts_on": date(2026, 10, 1),
        "state": "draft",
    }
    fields.update(change)
    return EngagementNotice(**fields)  # type: ignore[arg-type]


def _handoff(
    session: Session, freelancer_id: UUID, company_id: UUID | None, budget: Decimal | None
) -> UUID:
    """The row «Crea match» opens before the CRM has said anything: the budget snapshot only."""
    row = HubEngagement(
        id=uuid4(),
        freelancer_id=freelancer_id,
        company_id=company_id,
        company_daily_budget=budget,
        state="handoff",
        sequence=0,
    )
    session.add(row)
    session.commit()
    return row.id


def _tell_the_hub(session: Session, match: Match, state: str, *, sequence: int) -> bool:
    """What the CRM would have said about the hub's own match at this point: the match's own
    letter figures, keyed by the match's id, on a row opened with the match's budget snapshot.
    A match's letter is the only thing these tests have to stand in for the CRM's engagement."""
    session.refresh(match)
    if session.get(HubEngagement, match.id) is None:
        session.add(
            HubEngagement(
                id=match.id,
                freelancer_id=match.freelancer_id,
                company_id=match.company_id,
                company_daily_budget=match.company_daily_budget,
                state="handoff",
                sequence=0,
            )
        )
        session.commit()
    letter = _letter_of(session, match.id)
    signed = state == "active"
    notice = _notice(
        match.id,
        match.freelancer_id,
        match.company_id,
        sequence=sequence,
        kind="signed" if signed else "sent",
        mode="flat" if letter.data.get("modalita") == PAY_MODE_WORDS["lump_sum"] else "day",
        fee=match.letter_fee,
        expected_days=match.expected_days,
        starts_on=match.letter_start_date,
        ends_on=match.letter_end_date,
        letter_id=letter.id,
        letter_number=letter.number,
        signed_at=letter.signed_at if signed else None,
        state=state,
    )
    return EngagementNoticeService(session).apply(notice)


# ---- the reward, at the first signed letter ----------------------------------------


def _sign_the_letter(
    session: Session, renderer: FakeRenderer, fake: FakeDocumenso, match: Match
) -> None:
    envelope = _envelope_of(_letter_of(session, match.id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(session, renderer, fake, RecordingSender())
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.recovery.finish(signed)
    session.expire_all()
    _tell_the_hub(session, match, "active", sequence=2)


# ---- a referral counts once the referred person is verified (REB-658) --------------


def _referral_of(session: Session, entity_id: UUID) -> Referral:
    session.expire_all()
    row = session.scalar(select(Referral).where(Referral.entity_id == entity_id))
    assert row is not None
    return row


def _signed_reward(clean_referrals: Session, admin_id: UUID) -> UUID:
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals)
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent_with_body(clean_referrals, renderer, fake, freelancer_id, company_id, admin_id)
    _sign_the_letter(clean_referrals, renderer, fake, match)
    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    return reward.id


# ---- what a match would earn, and where the lists show it (REB-609) ---------------------


def _statements(session: Session) -> tuple[list[str], Callable[[], None]]:
    """A recorder of every statement the session's engine runs, and the call that stops it."""
    seen: list[str] = []

    def record(_c: object, _cur: object, statement: str, *_rest: object) -> None:
        seen.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    return seen, lambda: event.remove(engine, "before_cursor_execute", record)


def _referred_pair(clean_referrals: Session) -> tuple[UUID, UUID, UUID, UUID]:
    """A referrer, and a freelancer card and a company request both brought in by his
    code, with tax data and an active framework agreement so a match can be written. The
    company's referente has logged in since, so both referrals are verified on the
    company side too (the freelancer's is by its own signature)."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _log_in(clean_referrals, "wile@acme.it")
    _fiscal(clean_referrals, freelancer_id, admin_id)
    _active_framework(clean_referrals, freelancer_id, admin_id)
    return referrer_id, admin_id, freelancer_id, company_id


def _match_list(session: Session) -> list[MatchListItem]:
    service = MatchService(session, FakeRenderer(draft=False), SIGNER, today=lambda: TODAY)
    return service.reads.list_all(status=None, q=None, limit=100, offset=0).items


def _evidence(session: Session, entity_id: UUID):  # type: ignore[no-untyped-def]
    (item,) = [
        item
        for item in ReferralLedgerService(session).list_rewards().items
        if item.referred_id == entity_id
    ]
    return item.evidence


@pytest.fixture
def clean_referrals(clean_hub: Session) -> Iterator[Session]:
    """`clean_hub`, and the referral settings row gone too: it is seeded, so `wipe_hub`
    leaves it, and the tests that read the defaults need it absent."""
    yield clean_hub
    clean_hub.rollback()
    clean_hub.execute(text("DELETE FROM referral_settings"))
    clean_hub.commit()
