"""What the referral tests share (HTEST-10): a member, a card, a company's request, a login,
the signed letter and the reward it earns, the match list and the evidence of a referral.
The tests are in `test_referrals.py` (codes and links), `test_referral_company_links.py`,
`test_referral_rewards.py`, `test_referral_settings.py`, `test_referral_ledger.py` and
`test_referral_evidence.py`.
"""

import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from admins import an_admin
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.config import Settings
from rebase_core.engagement_notices import EngagementNoticeService
from rebase_core.engagement_schemas import EngagementNotice
from rebase_core.freelancer_schemas import FreelancerCreate, FreelancerDraft
from rebase_core.freelancers import FreelancerService
from rebase_core.models import (
    Company,
    HubEngagement,
    Referral,
    ReferralReward,
    TeamRequest,
    User,
)
from rebase_core.referral_ledger import ReferralLedgerService
from rebase_core.referrals import ReferralService
from rebase_core.signup_schemas import SignupCreate, SignupUtm
from rebase_core.users import UserService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


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


def _engagement_sent(
    session: Session,
    freelancer_id: UUID,
    company_id: UUID | None,
    *,
    state: str = "awaiting_signature",
    mode: str = "day",
    fee: int | Decimal = 450,
    expected_days: int | None = 20,
    sequence: int = 1,
    ends_on: date | None = None,
) -> UUID:
    """An engagement as the CRM tells it before the signature: the row «Crea match» opens with
    the company's budget snapshot (`_handoff`), then a `created` (draft) or `sent` notice. The
    engagement's key is returned."""
    company = session.get(Company, company_id) if company_id is not None else None
    key = _handoff(
        session, freelancer_id, company_id, None if company is None else company.daily_budget
    )
    notice = _notice(
        key,
        freelancer_id,
        company_id,
        sequence=sequence,
        kind="created" if state == "draft" else "sent",
        mode=mode,
        fee=Decimal(fee),
        expected_days=None if expected_days is None else Decimal(expected_days),
        ends_on=ends_on,
        state=state,
    )
    assert EngagementNoticeService(session).apply(notice) is True
    return key


# ---- the reward, at the first signed letter ----------------------------------------


def _sign(
    session: Session, key: UUID, *, sequence: int = 2, signed_at: datetime = SIGNED_AT
) -> bool:
    """The `signed` notice of an engagement `_engagement_sent` opened: its own figures again,
    now with the letter, its number and the signature. Whether the hub applied it."""
    row = session.get(HubEngagement, key)
    assert row is not None and row.mode is not None and row.fee is not None
    assert row.starts_on is not None and row.role is not None
    notice = _notice(
        key,
        row.freelancer_id,
        row.company_id,
        sequence=sequence,
        kind="signed",
        mode=row.mode,
        fee=row.fee,
        expected_days=row.expected_days,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        letter_id=uuid4(),
        letter_number="2026/001",
        signed_at=signed_at,
        state="active",
    )
    applied = EngagementNoticeService(session).apply(notice)
    session.expire_all()
    return applied


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
    key = _engagement_sent(clean_referrals, freelancer_id, company_id)
    _sign(clean_referrals, key)
    reward = clean_referrals.scalar(select(ReferralReward))
    assert reward is not None
    return reward.id


# ---- what a referral would earn, and where the ledger shows it (REB-609) ----------------


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
    code. The company's referente has logged in since, so both referrals are verified on the
    company side too (the freelancer's is by its own signature)."""
    referrer_id = _member(clean_referrals)
    code = ReferralService(clean_referrals).code_for(referrer_id)
    admin_id = an_admin(clean_referrals)
    freelancer_id = _card(clean_referrals, ref=code)
    company_id = _request(clean_referrals, ref=code)
    _log_in(clean_referrals, "wile@acme.it")
    return referrer_id, admin_id, freelancer_id, company_id


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
