"""Identity: get-or-create by email, the magic link, sessions, and role resolution."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.logins import LoginService
from rebase_core.models import AdminAction, Login, MagicLinkToken, User, UserSession
from rebase_core.signup_schemas import SignupUtm
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
        "notes",
        "freelancers",
        "companies",
        "admin_actions",
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


def test_get_or_create_backfills_telefono_but_never_overwrites_it(
    users: UserService, hub_session: Session
) -> None:
    """`telefono` (REB-380) is the one field here that is not simply "left as found":
    a `users` row can predate it entirely (a freelancer application never asks for
    one), so a still-blank phone number is filled in by whichever call first supplies
    one, on a fresh row or a pre-existing one alike -- but a real answer already on
    file is never replaced by a later, different one."""
    row = users.get_or_create("ada@studio.it", "Ada", "Lovelace")
    assert row.telefono is None

    backfilled = users.get_or_create("ada@studio.it", "Ada", "Lovelace", telefono="+39 345 1234567")
    assert backfilled.id == row.id and backfilled.telefono == "+39 345 1234567"

    again = users.get_or_create("ada@studio.it", "Ada", "Lovelace", telefono="+39 345 9999999")
    assert again.id == row.id and again.telefono == "+39 345 1234567"  # unchanged, not overwritten


def test_two_company_requests_from_the_same_address_share_one_user(
    users: UserService, hub_session: Session
) -> None:
    """The path `CompanyService.request` and `FreelancerService.apply` both take: no
    row invented twice for one address, even without `_find`'s own short-circuit.
    `telefono` (REB-380) follows the same rule as `nome`/`cognome`: the first
    request's, never overwritten by a later one's own answer."""
    from rebase_core.companies import CompanyService
    from rebase_core.company_schemas import CompanyCreate

    def _company(nome: str, cognome: str, telefono: str) -> None:
        CompanyService(hub_session).request(
            CompanyCreate(
                nome_azienda="ACME",
                referente_nome=nome,
                referente_cognome=cognome,
                email="acme@example.it",
                telefono=telefono,
                figura_richiesta="Backend developer",
                progetto="Un progetto",
                periodo_da="2026-10-01",
                durata="3 mesi",
                budget_giornaliero="500",
                remoto="remoto",
                numero_risorse=1,
            )
        )

    _company("Wile", "Coyote", "+39 345 1111111")
    _company("Road", "Runner", "+39 345 2222222")
    rows = hub_session.scalars(select(User).where(User.email == "acme@example.it")).all()
    assert len(rows) == 1
    assert (rows[0].nome, rows[0].cognome, rows[0].telefono) == (
        "Wile",
        "Coyote",
        "+39 345 1111111",
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


def test_a_link_asked_from_a_tracked_page_puts_the_campaign_on_the_login(
    users: UserService, hub_session: Session
) -> None:
    """REB-426: the attribution the login page arrived with travels on the token and
    lands on the `logins` row the link opens, so an outreach mail's click is readable
    per person with no cookie involved."""
    _apply(hub_session)
    utm = SignupUtm(
        utm_source="email",
        utm_medium="outreach",
        utm_campaign="outreach-2026-09-r2",
        utm_content="cv",
        utm_term="11425b70",
    )
    mail = users.request_link("ada@studio.it", utm=utm)
    assert mail is not None
    token = hub_session.scalar(select(MagicLinkToken))
    assert token is not None and token.utm_campaign == "outreach-2026-09-r2"

    outcome = users.enter(_token_from(mail.text))
    assert outcome is not None
    login = hub_session.scalar(select(Login).where(Login.user_id == outcome[0].id))
    assert login is not None
    assert (login.utm_source, login.utm_medium, login.utm_campaign) == (
        "email",
        "outreach",
        "outreach-2026-09-r2",
    )
    assert (login.utm_content, login.utm_term, login.utm_id, login.origine) == (
        "cv",
        "11425b70",
        None,
        None,
    )

    recent = LoginService(hub_session).stats().recenti[0]
    assert recent.utm_campaign == "outreach-2026-09-r2"
    assert recent.utm_term == "11425b70"
    assert LoginService(hub_session).for_user(outcome[0].id)[0].utm_content == "cv"


def test_a_link_asked_with_no_campaign_writes_a_login_with_none(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    for utm in (None, SignupUtm()):
        mail = users.request_link("ada@studio.it", utm=utm)
        assert mail is not None
        outcome = users.enter(_token_from(mail.text))
        assert outcome is not None
    logins = hub_session.scalars(select(Login)).all()
    assert len(logins) == 2
    for login in logins:
        assert login.utm_campaign is None and login.utm_source is None
        assert login.origine is None
    assert LoginService(hub_session).stats().recenti[0].utm_campaign is None


def test_an_expired_link_opens_nothing_and_is_swept_by_the_next_request(
    users: UserService, hub_session: Session
) -> None:
    _apply(hub_session)
    mail = users.request_link("ada@studio.it")
    assert mail is not None
    row = hub_session.scalar(select(MagicLinkToken))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    row.created_at = datetime.now(UTC) - timedelta(days=2)  # today's rows are kept (the budget)
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


def _admin(users: UserService) -> User:
    """An admin made the way the operator makes the first one: `rebase setrole`."""
    return users.set_role("boss@rebase.it", "admin", "Boss", "Rebase")[0]


# ---- promote, demote, setrole -------------------------------------------------------------


def test_promote_grants_an_existing_row_the_role_with_no_form(
    users: UserService, hub_session: Session
) -> None:
    boss = _admin(users)
    _apply(hub_session, "ada@studio.it")
    user, created = users.promote("ADA@studio.it", admin_id=boss.id)
    assert created is False and user.role == "admin"


def test_promoting_a_new_address_needs_a_name_and_creates_a_bare_row(
    users: UserService, hub_session: Session
) -> None:
    boss = _admin(users)
    with pytest.raises(ValidationFailed):
        users.promote("ivan@rebase.it", admin_id=boss.id)
    user, created = users.promote("ivan@rebase.it", "Ivan", "Fiore", admin_id=boss.id)
    assert created is True and user.role == "admin" and user.cognome == "Fiore"
    # No freelancer card is invented for a bare admin.
    from rebase_core.models import Freelancer

    assert hub_session.scalar(select(Freelancer).where(Freelancer.user_id == user.id)) is None


def test_demote_is_fully_reversible(users: UserService, hub_session: Session) -> None:
    boss = _admin(users)
    user, _ = users.promote("ivan@rebase.it", "Ivan", "Fiore", admin_id=boss.id)
    demoted = users.demote(user.id, admin_id=boss.id)
    assert demoted.role == "member"
    promoted_again, created = users.promote("ivan@rebase.it", admin_id=boss.id)
    assert created is False and promoted_again.role == "admin"


def _trail(session: Session, user_id: UUID) -> list[tuple[str, UUID, dict[str, object]]]:
    return [
        (row.kind, row.admin_id, row.payload)
        for row in session.scalars(
            select(AdminAction)
            .where(AdminAction.entity_type == "user", AdminAction.entity_id == user_id)
            .order_by(AdminAction.created_at, AdminAction.id)
        )
    ]


def test_a_promotion_and_a_demotion_are_signed_in_the_trail(
    users: UserService, hub_session: Session
) -> None:
    boss = _admin(users)
    ivan, _ = users.promote("ivan@rebase.it", "Ivan", "Fiore", admin_id=boss.id)
    users.promote("ivan@rebase.it", admin_id=boss.id)  # already an admin: no second entry
    users.demote(ivan.id, admin_id=boss.id)

    assert _trail(hub_session, ivan.id) == [
        ("admin_promoted", boss.id, {"created": True, "before": None}),
        ("admin_demoted", boss.id, {"before": "admin"}),
    ]


def test_a_role_never_changes_without_its_entry(
    users: UserService, hub_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry is in the role's own transaction: when it cannot be written, the role
    is not changed either, rather than a promotion that exists with no trail."""
    boss = _admin(users)
    ada_id = users.get_or_create("ada@studio.it", "Ada", "Lovelace").id

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the trail is down")

    monkeypatch.setattr(AdminActionService, "record", refuse)
    with pytest.raises(RuntimeError):
        users.promote("ada@studio.it", admin_id=boss.id)
    hub_session.rollback()

    assert hub_session.get(User, ada_id).role == "member"  # type: ignore[union-attr]


def test_the_last_admin_cannot_be_demoted_not_even_by_themselves(
    users: UserService, hub_session: Session
) -> None:
    boss = _admin(users)

    with pytest.raises(InvalidState):
        users.demote(boss.id, admin_id=boss.id)
    assert hub_session.get(User, boss.id).role == "admin"  # type: ignore[union-attr]

    # With a second admin the first may step down: it is the same act as any demotion.
    ivan, _ = users.promote("ivan@rebase.it", "Ivan", "Fiore", admin_id=boss.id)
    assert users.demote(boss.id, admin_id=boss.id).role == "member"
    with pytest.raises(InvalidState):
        users.demote(ivan.id, admin_id=ivan.id)


def test_a_demotion_refuses_an_address_merged_into_somebody(
    users: UserService, hub_session: Session
) -> None:
    boss = _admin(users)
    ivan, _ = users.promote("ivan@rebase.it", "Ivan", "Fiore", admin_id=boss.id)
    ivan.merged_into_id = boss.id
    hub_session.commit()

    with pytest.raises(ValidationFailed):
        users.demote(ivan.id, admin_id=boss.id)


def test_set_role_is_createtokens_own_shape(users: UserService, hub_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        users.set_role("ivan@rebase.it", "superadmin")
    user, created = users.set_role("ivan@rebase.it", "admin", "Ivan", "Fiore")
    assert created is True and user.role == "admin"
    demoted, created_again = users.set_role("ivan@rebase.it", "member")
    assert created_again is False and demoted.role == "member" and demoted.id == user.id


# ---- the session slides, but not on every request ---------------------------------------


def test_a_request_inside_the_hour_writes_nothing_and_a_later_one_slides(
    users: UserService, hub_session: Session, settings: Settings
) -> None:
    _apply(hub_session)
    _, raw = users.enter(_token_from(users.request_link("ada@studio.it").text))  # type: ignore[union-attr,misc]
    row = hub_session.scalar(select(UserSession))
    assert row is not None
    full = timedelta(days=settings.member_session_days)

    inside = datetime.now(UTC) + full - timedelta(minutes=30)
    row.expires_at = inside
    hub_session.commit()
    assert users.resolve(raw) is not None
    hub_session.refresh(row)
    assert row.expires_at == inside  # no write: the deadline is the one stored

    behind = datetime.now(UTC) + full - timedelta(hours=2)
    row.expires_at = behind
    hub_session.commit()
    assert users.resolve(raw) is not None
    hub_session.refresh(row)
    assert row.expires_at > behind + timedelta(hours=1)


# ---- the public mail budget -----------------------------------------------------------


def test_the_days_links_are_counted_and_todays_rows_survive_the_sweep(
    users: UserService, hub_session: Session, settings: Settings
) -> None:
    for index in range(3):
        email = f"p{index}@studio.it"
        users.get_or_create(email, "Ada", "Lovelace")
        assert users.request_link(email) is not None
    assert users.public_links_left() == settings.public_mail_daily_budget - 3

    # A spent link of the same person, asked for again, is not swept while today lasts:
    # it is part of the day's count.
    token = hub_session.scalars(select(MagicLinkToken)).first()
    assert token is not None
    token.used_at = datetime.now(UTC)
    hub_session.commit()
    users.request_link("p0@studio.it")
    assert users.public_links_left() <= settings.public_mail_daily_budget - 3


def test_the_budget_runs_out_at_zero_and_starts_again_tomorrow(
    users: UserService, hub_session: Session, settings: Settings
) -> None:
    tight = settings.model_copy(update={"public_mail_daily_budget": 2})
    limited = UserService(hub_session, tight)
    for index in range(2):
        email = f"p{index}@studio.it"
        limited.get_or_create(email, "Ada", "Lovelace")
        assert limited.request_link(email) is not None

    assert limited.public_links_left() == 0
    assert limited.public_links_left(datetime.now(UTC) + timedelta(days=1)) == 2
