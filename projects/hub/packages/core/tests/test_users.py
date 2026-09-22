"""Identity: get-or-create by email, the magic link, sessions, and role resolution."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import MagicLinkToken, User, UserSession
from rebase_core.schemas import FreelancerCreate
from rebase_core.users import UserService

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"

GOOD = {
    "nome": "Ada",
    "cognome": "Lovelace",
    "linkedin_url": "https://www.linkedin.com/in/ada",
    "tariffa_giornaliera": "450",
    "posizione": "Backend developer",
    "remoto": "remoto",
    "links": ["https://github.com/ada"],
}


@pytest.fixture
def settings(hub_engine: Engine) -> Settings:
    return Settings(
        database_url=hub_engine.url.render_as_string(hide_password=False),
        hub_url="http://localhost:5180/hub",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def users(hub_engine: Engine, hub_session: Session, settings: Settings) -> UserService:
    yield UserService(hub_session, settings)
    hub_session.rollback()
    for table in (
        "sessions",
        "magic_link_tokens",
        "comments",
        "freelancers",
        "companies",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _apply(session: Session, email: str = "ada@studio.it") -> UUID:
    read, _ = FreelancerService(session).apply(
        FreelancerCreate(**GOOD, email=email), PDF, "Ada CV.pdf", "application/pdf"
    )
    return read.id


def _token_from(mail_text: str) -> str:
    match = re.search(r"/entra\?t=([A-Za-z0-9_-]+)", mail_text)
    assert match, mail_text
    return match.group(1)


# ---- get-or-create ----------------------------------------------------------------------


def test_get_or_create_makes_one_row_per_address_and_returns_it_after(
    users: UserService, hub_session: Session
) -> None:
    row = users.get_or_create("Ada@Studio.it", "Ada", "Lovelace")
    assert row.email == "ada@studio.it" and row.role == "member"
    again = users.get_or_create("ada@studio.it", "Somebody", "Else")
    assert again.id == row.id and again.nome == "Ada"  # unchanged, not overwritten
    matching = hub_session.scalars(select(User).where(User.email == "ada@studio.it")).all()
    assert len(matching) == 1


def test_two_company_requests_from_the_same_address_share_one_user(
    users: UserService, hub_session: Session
) -> None:
    """The path `CompanyService.request` and `FreelancerService.apply` both take: no
    row invented twice for one address, even without `_find`'s own short-circuit."""
    from rebase_core.companies import CompanyService
    from rebase_core.schemas import CompanyCreate

    def _company(nome: str, cognome: str) -> None:
        CompanyService(hub_session).request(
            CompanyCreate(
                nome_azienda="ACME",
                referente_nome=nome,
                referente_cognome=cognome,
                email="acme@example.it",
                progetto="Un progetto",
                periodo_da="2026-10-01",
                durata="3 mesi",
                budget_giornaliero="500",
            )
        )

    _company("Wile", "Coyote")
    _company("Road", "Runner")
    rows = hub_session.scalars(select(User).where(User.email == "acme@example.it")).all()
    assert len(rows) == 1
    assert (rows[0].nome, rows[0].cognome) == (
        "Wile",
        "Coyote",
    )  # the first request's, not overwritten


def test_a_freelancer_application_never_duplicates_the_user_it_already_has(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session, "ada@studio.it")
    _apply(hub_session, "ada@studio.it")  # a repeat application, short-circuits at `_find`
    rows = hub_session.scalars(select(User).where(User.email == "ada@studio.it")).all()
    assert len(rows) == 1


# ---- the way in -------------------------------------------------------------------------


def test_a_link_is_written_only_for_an_address_that_exists(
    users: UserService, hub_session: Session
) -> None:
    assert users.request_link("nessuno@studio.it") is None
    assert hub_session.scalar(select(MagicLinkToken)) is None

    _apply(hub_session)
    mail = users.request_link("  ADA@studio.it ")
    assert mail is not None and mail.to == "ada@studio.it"
    assert "http://localhost:5180/hub/entra?t=" in mail.text
    row = hub_session.scalar(select(MagicLinkToken))
    assert row is not None and row.used_at is None
    assert row.token_hash != _token_from(mail.text) and len(row.token_hash) == 64


def test_a_second_request_inside_sixty_seconds_sends_nothing_new(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    first = users.request_link("ada@studio.it")
    assert first is not None

    second = users.request_link("ada@studio.it")
    assert second is None
    tokens = hub_session.scalars(select(MagicLinkToken)).all()
    assert len(tokens) == 1


def test_a_request_a_minute_later_gets_a_link_of_its_own(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    first = users.request_link("ada@studio.it")
    assert first is not None
    original = hub_session.scalar(select(MagicLinkToken))
    assert original is not None
    original_id = original.id
    original_hash = original.token_hash
    original.created_at = datetime.now(UTC) - timedelta(seconds=61)
    hub_session.commit()

    second = users.request_link("ada@studio.it")
    assert second is not None
    tokens = hub_session.scalars(select(MagicLinkToken)).all()
    assert len(tokens) == 2
    fresh = next(row for row in tokens if row.id != original_id)
    assert fresh.token_hash != original_hash


def test_a_link_opens_a_session_once_and_never_twice_for_a_card_or_a_bare_admin(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    mail = users.request_link("ada@studio.it")
    assert mail is not None
    raw = _token_from(mail.text)

    outcome = users.enter(raw)
    assert outcome is not None
    person, session_token = outcome
    assert person.email == "ada@studio.it"
    assert users.resolve(session_token) is not None
    assert users.enter(raw) is None, "a spent link opens nothing"
    assert users.enter("non-un-token-vero-ma-lungo-abbastanza") is None
    assert users.enter("") is None

    # A person with no freelancer card at all enters the same way.
    bare = users.get_or_create("ivan@rebase.it", "Ivan", "Fiore")
    bare_mail = users.request_link(bare.email)
    assert bare_mail is not None
    bare_outcome = users.enter(_token_from(bare_mail.text))
    assert bare_outcome is not None and bare_outcome[0].id == bare.id


def test_an_expired_link_opens_nothing_and_is_swept_by_the_next_request(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    mail = users.request_link("ada@studio.it")
    assert mail is not None
    row = hub_session.scalar(select(MagicLinkToken))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    hub_session.commit()
    assert users.enter(_token_from(mail.text)) is None

    users.request_link("ada@studio.it")
    tokens = hub_session.scalars(select(MagicLinkToken)).all()
    assert len(tokens) == 1 and tokens[0].id != row.id


def test_a_session_is_hashed_sliding_and_closable(users: UserService, hub_session: Session) -> None:
    _apply(hub_session)
    mail = users.request_link("ada@studio.it")
    assert mail is not None
    outcome = users.enter(_token_from(mail.text))
    assert outcome is not None
    _, raw = outcome
    row = hub_session.scalar(select(UserSession))
    assert row is not None and row.token_hash != raw and len(row.token_hash) == 64
    row.expires_at = row.expires_at - timedelta(days=1)
    hub_session.commit()
    before = row.expires_at
    assert users.resolve(raw) is not None
    hub_session.refresh(row)
    assert row.expires_at > before

    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    hub_session.commit()
    assert users.resolve(raw) is None
    assert hub_session.scalar(select(UserSession)) is None

    reentered = users.enter(_token_from(users.request_link("ada@studio.it").text))  # type: ignore[union-attr]
    assert reentered is not None
    users.close_session(reentered[1])
    assert users.resolve(reentered[1]) is None
    assert users.resolve(None) is None


def test_resolve_admin_tells_a_member_from_an_admin(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session, "ada@studio.it")  # a plain member
    users.promote("ivan@rebase.it", "Ivan", "Fiore")

    member_mail = users.request_link("ada@studio.it")
    admin_mail = users.request_link("ivan@rebase.it")
    assert member_mail is not None and admin_mail is not None
    _, member_session = users.enter(_token_from(member_mail.text))  # type: ignore[misc]
    _, admin_session = users.enter(_token_from(admin_mail.text))  # type: ignore[misc]

    assert users.resolve_admin(member_session) is None
    assert users.resolve(member_session) is not None  # a real identity, just not an admin
    admin = users.resolve_admin(admin_session)
    assert admin is not None and admin.email == "ivan@rebase.it"


# ---- promote, demote, setrole -------------------------------------------------------------


def test_promote_grants_an_existing_row_the_role_with_no_form(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session, "ada@studio.it")
    user, created = users.promote("ADA@studio.it")
    assert created is False and user.role == "admin"


def test_promoting_a_new_address_needs_a_name_and_creates_a_bare_row(
    users: UserService, hub_session: Session
) -> None:
    with pytest.raises(ValidationFailed):
        users.promote("ivan@rebase.it")
    user, created = users.promote("ivan@rebase.it", "Ivan", "Fiore")
    assert created is True and user.role == "admin" and user.cognome == "Fiore"
    # No freelancer card is invented for a bare admin.
    from rebase_core.models import Freelancer

    assert hub_session.scalar(select(Freelancer).where(Freelancer.user_id == user.id)) is None


def test_demote_is_fully_reversible(users: UserService, hub_session: Session) -> None:
    user, _ = users.promote("ivan@rebase.it", "Ivan", "Fiore")
    demoted = users.demote(user.id)
    assert demoted.role == "member"
    promoted_again, created = users.promote("ivan@rebase.it")
    assert created is False and promoted_again.role == "admin"


def test_set_role_is_createtokens_own_shape(users: UserService, hub_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        users.set_role("ivan@rebase.it", "superadmin")
    user, created = users.set_role("ivan@rebase.it", "admin", "Ivan", "Fiore")
    assert created is True and user.role == "admin"
    demoted, created_again = users.set_role("ivan@rebase.it", "member")
    assert created_again is False and demoted.role == "member" and demoted.id == user.id
