"""Connecting a Slack workspace to a space, matching its users, and disconnecting
(REB-739), against a fake of Slack's HTTP surface (`fakes/fake_slack.py`)."""

import base64
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fakes.fake_slack import BOT_TOKEN, CLIENT_ID, CLIENT_SECRET, FakeSlack
from fakes.slack_fixtures import (
    SLACK_KEY,
    actor_of,
    connect,
    make_user,
    slack_client,
    slack_settings,
)
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, PermissionDenied, ScopedAdmin
from pigrocrm.core.gmail.crypto import unseal
from pigrocrm.core.gmail.models import GoogleOAuthState
from pigrocrm.core.slack.errors import SlackRefused, SlackTokenRevoked
from pigrocrm.core.slack.matching import RETRY_UNMATCHED_AFTER
from pigrocrm.core.slack.models import SlackUserLink, SlackWorkspace
from pigrocrm.core.slack.oauth import SlackOAuthService
from pigrocrm.core.slack.schemas import SLACK_BOT_SCOPES, SlackHealth
from pigrocrm.core.slack.service import SlackService
from pigrocrm.core.tenants import space_base_settings


def _service(session: Session, fake: FakeSlack, **overrides: object) -> SlackOAuthService:
    return SlackOAuthService(
        session, settings=slack_settings(**overrides), client=slack_client(fake)
    )


def _state_of(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def test_start_sends_the_admin_to_slacks_authorize_page_with_the_bot_scopes(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    url = _service(db_session, FakeSlack()).start(actor_of(admin))
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert (
        f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://slack.test/oauth/v2/authorize"
    )
    assert query["client_id"] == [CLIENT_ID]
    assert query["scope"] == [",".join(SLACK_BOT_SCOPES)]
    assert query["redirect_uri"] == ["https://crm.example.it/api/slack/oauth/callback"]
    assert CLIENT_SECRET not in url
    row = db_session.execute(
        select(GoogleOAuthState).where(GoogleOAuthState.jti == query["state"][0])
    ).scalar_one()
    assert row.purpose == "slack"
    assert row.user_id == admin.id


def test_a_collaboratore_cannot_start_and_an_unconfigured_install_refuses(
    db_session: Session,
) -> None:
    member = make_user(db_session, ruolo="collaboratore")
    with pytest.raises(PermissionDenied):
        _service(db_session, FakeSlack()).start(actor_of(member))
    admin = make_user(db_session)
    with pytest.raises(Conflict, match="Slack non è configurato"):
        _service(db_session, FakeSlack(), slack_client_secret="").start(actor_of(admin))


def test_a_scoped_admin_cannot_connect_the_space(db_session: Session) -> None:
    admin = make_user(db_session)
    scoped = Actor(id=admin.id, type="user", role="admin", aziende=())
    with pytest.raises(ScopedAdmin):
        _service(db_session, FakeSlack()).start(scoped)


def test_complete_stores_the_bot_token_sealed_and_matches_users_by_email(
    db_session: Session,
) -> None:
    admin = make_user(db_session, email="mario@example.it")
    ada = make_user(db_session, email="ada@example.it", nome="Ada", ruolo="collaboratore")
    nobody = make_user(db_session, email="nessuno@example.it", nome="Nessuno", ruolo="readonly")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    fake.add_member("ADA@example.it", "UADA")

    workspace = connect(db_session, fake, admin)

    assert (workspace.team_id, workspace.team_name, workspace.status) == (
        "T0TEST",
        "Acme Test",
        "active",
    )
    assert workspace.bot_user_id == "UBOT"
    assert workspace.connected_by == admin.id
    assert set(workspace.scopes_granted) == set(SLACK_BOT_SCOPES)
    # The token is sealed, with the key published to the tests, and is not in the row's text.
    assert BOT_TOKEN.encode() not in workspace.bot_token_ciphertext
    assert unseal(workspace.bot_token_ciphertext, workspace.bot_token_nonce, SLACK_KEY) == BOT_TOKEN

    links = {link.user_id: link for link in db_session.scalars(select(SlackUserLink))}
    assert (links[admin.id].slack_user_id, links[admin.id].source) == ("UMARIO", "email")
    assert (links[ada.id].slack_user_id, links[ada.id].source) == ("UADA", "email")
    assert (links[nobody.id].slack_user_id, links[nobody.id].source) == (None, "none")


def test_the_client_secret_goes_in_a_basic_header_and_never_in_the_body(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    connect(db_session, fake, admin)
    (exchange,) = fake.calls("oauth.v2.access")
    assert exchange.basic == (CLIENT_ID, CLIENT_SECRET)
    assert "client_secret" not in exchange.form
    assert exchange.form["redirect_uri"] == "https://crm.example.it/api/slack/oauth/callback"


def test_the_state_is_single_use_and_belongs_to_the_admin_who_started_it(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    other = make_user(db_session)
    fake = FakeSlack()
    fake.codes["second"] = "T0TEST"
    service = _service(db_session, fake)
    state = _state_of(service.start(actor_of(admin)))

    with pytest.raises(Conflict, match="non è più valida"):
        service.complete(code="good-code", state=state, actor=actor_of(other))
    # Redeemed by the wrong person, so it is gone for the right one too.
    with pytest.raises(Conflict, match="non è più valida"):
        service.complete(code="second", state=state, actor=actor_of(admin))
    assert fake.calls("oauth.v2.access") == []


def test_an_unknown_or_expired_state_is_refused_with_the_same_sentence(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    service = _service(db_session, FakeSlack())
    sentences = set()
    with pytest.raises(Conflict) as unknown:
        service.complete(code="good-code", state="nope", actor=actor_of(admin))
    sentences.add(unknown.value.message)
    state = _state_of(service.start(actor_of(admin)))
    db_session.execute(
        text("UPDATE google_oauth_states SET expires_at = :past WHERE jti = :jti"),
        {"past": datetime.now(UTC) - timedelta(minutes=1), "jti": state},
    )
    with pytest.raises(Conflict) as expired:
        service.complete(code="good-code", state=state, actor=actor_of(admin))
    sentences.add(expired.value.message)
    assert len(sentences) == 1


def test_a_gmail_or_drive_state_cannot_be_redeemed_as_slack(db_session: Session) -> None:
    admin = make_user(db_session)
    db_session.add(
        GoogleOAuthState(
            jti="gmail-jti",
            code_verifier="v" * 43,
            user_id=admin.id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db_session.flush()
    with pytest.raises(Conflict, match="non è più valida"):
        _service(db_session, FakeSlack()).complete(
            code="good-code", state="gmail-jti", actor=actor_of(admin)
        )


def test_slack_refusing_the_code_is_a_conflict_with_slacks_word(db_session: Session) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    service = _service(db_session, fake)
    state = _state_of(service.start(actor_of(admin)))
    with pytest.raises(SlackRefused) as caught:
        service.complete(code="stale-code", state=state, actor=actor_of(admin))
    assert caught.value.code == "invalid_code"
    assert service.repo.workspace() is None


def test_an_app_installed_with_fewer_scopes_than_the_manifest_is_refused_naming_them(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack(scope="chat:write,im:write")
    service = _service(db_session, fake)
    state = _state_of(service.start(actor_of(admin)))
    with pytest.raises(SlackRefused) as caught:
        service.complete(code="good-code", state=state, actor=actor_of(admin))
    assert "users:read.email" in caught.value.code
    assert service.repo.workspace() is None


def test_a_second_workspace_is_refused_until_the_first_is_disconnected(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    connect(db_session, fake, admin)

    other = FakeSlack(team_id="T1OTHER", team_name="Altro Team")
    service = _service(db_session, other)
    state = _state_of(service.start(actor_of(admin)))
    with pytest.raises(Conflict, match="Acme Test"):
        service.complete(code="good-code", state=state, actor=actor_of(admin))

    service.disconnect(actor_of(admin))
    state = _state_of(service.start(actor_of(admin)))
    other.codes["good-code"] = "T1OTHER"
    service.complete(code="good-code", state=state, actor=actor_of(admin))
    workspace = service.repo.workspace()
    assert workspace is not None
    assert (workspace.team_name, workspace.status) == ("Altro Team", "active")


def test_reconnecting_the_same_workspace_keeps_its_row_and_a_manual_match(
    db_session: Session,
) -> None:
    admin = make_user(db_session, email="mario@example.it")
    fake = FakeSlack()
    fake.add_member("m.rossi@acme.example", "UMARIO")
    first = connect(db_session, fake, admin)
    settings = slack_settings()
    SlackService(db_session, settings=settings, client=slack_client(fake)).link(
        actor_of(admin), admin.id, "m.rossi@acme.example"
    )
    first.status = "revoked"
    db_session.flush()

    fake.codes["good-code"] = "T0TEST"
    second = connect(db_session, fake, admin)

    assert second.id == first.id
    assert second.status == "active"
    link = db_session.execute(select(SlackUserLink)).scalar_one()
    assert (link.source, link.slack_user_id) == ("manual", "UMARIO")


def test_disconnect_wipes_the_token_the_channel_and_the_matches(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    fake.add_channel("C1", "promemoria")
    workspace = connect(db_session, fake, admin)
    SlackService(db_session, settings=slack_settings(), client=slack_client(fake)).set_channel(
        actor_of(admin), "C1"
    )

    _service(db_session, fake).disconnect(actor_of(admin))

    db_session.refresh(workspace)
    assert workspace.status == "disconnected"
    assert workspace.bot_token_ciphertext == b""
    assert workspace.bot_token_nonce == b""
    assert workspace.channel_id is None
    assert workspace.disconnected_at is not None
    assert db_session.scalars(select(SlackUserLink)).all() == []
    with pytest.raises(Conflict, match="nessun workspace"):
        _service(db_session, fake).disconnect(actor_of(admin))


def test_a_workspace_is_one_per_space_in_the_database(db_session: Session) -> None:
    admin = make_user(db_session)
    connect(db_session, FakeSlack(), admin)
    db_session.add(
        SlackWorkspace(
            team_id="T2",
            team_name="Un altro",
            bot_user_id="U2",
            bot_token_ciphertext=b"x",
            bot_token_nonce=b"y",
        )
    )
    with pytest.raises(Exception, match="uq_slack_workspaces_one_per_space"):
        db_session.flush()


# --- the page's reads and the fixes for a mismatch -----------------------------------


def _page(session: Session, fake: FakeSlack, admin_actor: Actor) -> SlackHealth:
    return SlackService(session, settings=slack_settings(), client=slack_client(fake)).health(
        admin_actor
    )


def test_the_page_lists_every_active_user_with_how_they_were_matched(
    db_session: Session,
) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    ada = make_user(db_session, email="ada@example.it", nome="Ada", ruolo="collaboratore")
    gone = make_user(db_session, email="gone@example.it", nome="Gone", ruolo="collaboratore")
    gone.attivo = False
    db_session.flush()
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    connect(db_session, fake, admin)

    health = _page(db_session, fake, actor_of(admin))

    assert health.configured is True
    assert health.workspace is not None and health.workspace.team_name == "Acme Test"
    by_name = {member.nome: member for member in health.members}
    assert set(by_name) == {"Mario", "Ada"}
    assert by_name["Mario"].state == "matched"
    assert by_name["Ada"].state == "unmatched"
    assert ada.email == by_name["Ada"].email
    assert "token" not in health.model_dump_json().lower().replace("tokens", "")


def test_the_page_reads_unconfigured_as_a_state_not_an_error(db_session: Session) -> None:
    admin = make_user(db_session)
    service = SlackService(
        db_session, settings=slack_settings(slack_client_id=""), client=slack_client(FakeSlack())
    )
    assert service.health(actor_of(admin)) == SlackHealth(configured=False, workspace=None)


def test_an_admin_fixes_a_mismatch_by_giving_the_slack_address(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    ada = make_user(db_session, email="ada@studio.example", nome="Ada", ruolo="collaboratore")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    fake.add_member("ada.bianchi@acme.example", "UADA")
    connect(db_session, fake, admin)
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    health = service.link(actor_of(admin), ada.id, "ada.bianchi@acme.example")

    member = next(m for m in health.members if m.user_id == ada.id)
    assert (member.state, member.slack_email) == ("manual", "ada.bianchi@acme.example")
    # A manual match is not overwritten by looking again.
    service.rematch(actor_of(admin))
    link = db_session.execute(
        select(SlackUserLink).where(SlackUserLink.user_id == ada.id)
    ).scalar_one()
    assert (link.source, link.slack_user_id) == ("manual", "UADA")


def test_a_fix_names_what_is_wrong_when_nobody_or_somebody_else_has_the_address(
    db_session: Session,
) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    ada = make_user(db_session, email="ada@studio.example", nome="Ada", ruolo="collaboratore")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    connect(db_session, fake, admin)
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    with pytest.raises(Conflict, match="nessuno nel workspace Acme Test"):
        service.link(actor_of(admin), ada.id, "nobody@acme.example")
    with pytest.raises(Conflict, match="già abbinato a un altro utente"):
        service.link(actor_of(admin), ada.id, "mario@example.it")


def test_unlinking_goes_back_to_the_automatic_match(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    fake.add_member("m.rossi@acme.example", "UOTHER")
    connect(db_session, fake, admin)
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))
    service.link(actor_of(admin), admin.id, "m.rossi@acme.example")

    health = service.unlink(actor_of(admin), admin.id)

    member = next(m for m in health.members if m.user_id == admin.id)
    assert member.state == "matched"
    link = db_session.execute(select(SlackUserLink)).scalar_one()
    assert (link.source, link.slack_user_id) == ("email", "UMARIO")


def test_look_again_finds_a_person_who_joined_slack_after_the_connection(
    db_session: Session,
) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    ada = make_user(db_session, email="ada@example.it", nome="Ada", ruolo="collaboratore")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    connect(db_session, fake, admin)
    fake.add_member("ada@example.it", "UADA")
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    health = service.rematch(actor_of(admin))

    assert next(m for m in health.members if m.user_id == ada.id).state == "matched"


def test_two_users_never_share_one_slack_member(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    twin = make_user(db_session, email="mario.old@example.it", nome="Gemello")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    fake.add_member(twin.email, "UMARIO")
    connect(db_session, fake, admin)

    states = {
        link.user_id: link.slack_user_id for link in db_session.scalars(select(SlackUserLink))
    }
    assert sorted(v or "" for v in states.values()) == ["", "UMARIO"]


def test_deactivated_slack_accounts_and_bots_are_not_a_match(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    bot_owner = make_user(db_session, email="bot@example.it", nome="Bot", ruolo="collaboratore")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO", deleted=True)
    fake.add_member("bot@example.it", "UBOT2", is_bot=True)
    connect(db_session, fake, admin)

    links = {link.user_id: link for link in db_session.scalars(select(SlackUserLink))}
    assert links[admin.id].source == "none"
    assert links[bot_owner.id].source == "none"


def test_an_unmatched_user_is_asked_again_only_after_a_day(db_session: Session) -> None:
    from pigrocrm.core.slack.matching import SlackMatcher
    from pigrocrm.core.slack.repository import SlackRepository

    admin = make_user(db_session, email="mario@example.it", nome="Mario")
    fake = FakeSlack()
    connect(db_session, fake, admin)  # nobody in Slack: a `none` link
    lookups = len(fake.calls("users.lookupByEmail"))
    matcher = SlackMatcher(SlackRepository(db_session), slack_client(fake), BOT_TOKEN)

    now = datetime.now(UTC)
    matcher.match_user(admin, now=now + timedelta(hours=1))
    assert len(fake.calls("users.lookupByEmail")) == lookups

    fake.add_member("mario@example.it", "UMARIO")
    link = matcher.match_user(admin, now=now + RETRY_UNMATCHED_AFTER + timedelta(minutes=1))
    assert (link.source, link.slack_user_id) == ("email", "UMARIO")


# --- the channel -----------------------------------------------------------------------


def test_the_channel_is_chosen_from_slacks_own_list_and_can_be_cleared(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    fake.add_channel("C1", "promemoria")
    fake.add_channel("C2", "generale", is_member=True)
    fake.add_channel("G1", "amministrazione", is_private=True, is_member=True)
    connect(db_session, fake, admin)
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    names = [channel.name for channel in service.channels(actor_of(admin))]
    assert names == ["amministrazione", "generale", "promemoria"]

    health = service.set_channel(actor_of(admin), "C1")
    assert health.workspace is not None
    assert (health.workspace.channel_id, health.workspace.channel_name) == ("C1", "promemoria")

    health = service.set_channel(actor_of(admin), None)
    assert health.workspace is not None and health.workspace.channel_id is None

    with pytest.raises(Conflict, match="non esiste nel workspace"):
        service.set_channel(actor_of(admin), "CNOPE")


def test_channel_pagination_follows_the_cursor(db_session: Session) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    for number in range(450):
        fake.add_channel(f"C{number:04d}", f"canale-{number:04d}")
    connect(db_session, fake, admin)
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    assert len(service.channels(actor_of(admin))) == 450
    assert len(fake.calls("conversations.list")) == 3


# --- a token Slack refuses ------------------------------------------------------------


def test_a_revoked_token_is_recorded_on_the_row_and_the_page_asks_to_reconnect(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    workspace = connect(db_session, fake, admin)
    fake.revoked = True
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    with pytest.raises(SlackTokenRevoked, match="ricollega Slack"):
        service.rematch(actor_of(admin))

    db_session.refresh(workspace)
    assert workspace.status == "revoked"
    assert workspace.last_error is not None and "Acme Test" in workspace.last_error
    assert workspace.last_error_at is not None
    health = service.health(actor_of(admin))
    assert health.workspace is not None and health.workspace.status == "revoked"
    with pytest.raises(Conflict, match="ricollega Slack"):
        service.rematch(actor_of(admin))


def test_slack_down_is_a_sentence_after_the_retries_and_changes_nothing(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    workspace = connect(db_session, fake, admin)
    fake.fail_status = [503, 503, 503, 503]
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))

    with pytest.raises(Conflict, match="non ha risposto correttamente"):
        service.rematch(actor_of(admin))

    db_session.refresh(workspace)
    assert workspace.status == "active"


def test_a_rate_limited_call_waits_the_retry_after_and_goes_through(db_session: Session) -> None:
    admin = make_user(db_session, email="mario@example.it")
    fake = FakeSlack()
    fake.add_member("mario@example.it", "UMARIO")
    sleeps: list[float] = []
    client = slack_client(fake)
    client._sleep = sleeps.append  # type: ignore[method-assign]
    connect(db_session, fake, admin)
    fake.fail_status = [429]

    member = client.lookup_user_by_email(BOT_TOKEN, "mario@example.it")

    assert member is not None and member.id == "UMARIO"
    assert sleeps == [1.0]


def test_the_stored_token_is_not_in_any_schema_or_repr(db_session: Session) -> None:
    admin = make_user(db_session)
    workspace = connect(db_session, FakeSlack(), admin)
    health = _page(db_session, FakeSlack(), actor_of(admin))
    assert BOT_TOKEN not in health.model_dump_json()
    assert BOT_TOKEN not in repr(workspace)
    assert BOT_TOKEN not in repr(slack_settings())
    assert CLIENT_SECRET not in repr(slack_settings())


# --- a space borrows the root's app -----------------------------------------------------


def test_a_space_borrows_the_roots_app_with_a_key_and_a_state_of_its_own() -> None:
    root = slack_settings()
    one = space_base_settings(root, "studio-uno")
    two = space_base_settings(root, "studio-due")

    assert one.slack_client_id == root.slack_client_id
    assert one.slack_client_secret == root.slack_client_secret
    assert one.slack_callback_base_url == "https://crm.example.it"
    assert one.slack_oauth_state_prefix == "studio-uno."
    assert one.slack_token_key != root.slack_token_key
    assert one.slack_token_key != two.slack_token_key
    assert len(base64.b64decode(one.slack_token_key)) == 32
    # The root itself is unchanged: its own address, a bare jti.
    assert space_base_settings(root, None) is root
    assert root.slack_oauth_state_prefix == ""


def test_a_space_without_a_slack_app_on_the_root_reads_as_not_configured() -> None:
    bare = slack_settings(slack_client_id="")
    assert space_base_settings(bare, "studio-uno").slack_client_id == ""
    assert space_base_settings(bare, "studio-uno").slack_token_key == ""


def test_a_space_sends_slack_to_the_roots_callback_with_its_slug_in_the_state(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    space = space_base_settings(slack_settings(), "studio-uno")
    service = SlackOAuthService(db_session, settings=space, client=slack_client(FakeSlack()))

    url = service.start(actor_of(admin))
    query = parse_qs(urlparse(url).query)

    assert query["redirect_uri"] == ["https://crm.example.it/api/slack/oauth/callback"]
    assert query["state"][0].startswith("studio-uno.")
    # A state without the space's prefix is refused before anything is looked up.
    with pytest.raises(Conflict, match="non è più valida"):
        service.complete(code="good-code", state="altro.abc", actor=actor_of(admin))


def test_a_token_sealed_for_one_space_does_not_open_in_another(db_session: Session) -> None:
    from pigrocrm.core.slack.token import bot_token

    admin = make_user(db_session)
    fake = FakeSlack()
    uno = space_base_settings(slack_settings(), "studio-uno")
    workspace = connect(db_session, fake, admin, uno)

    assert bot_token(workspace, uno) == BOT_TOKEN
    with pytest.raises(Conflict, match="PIGROCRM_SLACK_TOKEN_KEY"):
        bot_token(workspace, space_base_settings(slack_settings(), "studio-due"))


def test_a_rotated_key_shows_on_the_page_before_any_action_fails(db_session: Session) -> None:
    import base64

    admin = make_user(db_session)
    connect(db_session, FakeSlack(), admin)
    rotated = slack_settings(slack_token_key=base64.b64encode(b"r" * 32).decode())
    service = SlackService(db_session, settings=rotated, client=slack_client(FakeSlack()))

    health = service.health(actor_of(admin))

    assert health.problem is not None and "PIGROCRM_SLACK_TOKEN_KEY" in health.problem
    assert _page(db_session, FakeSlack(), actor_of(admin)).problem is None


def test_a_post_is_never_retried_on_a_server_error_but_a_rate_limit_is(
    db_session: Session,
) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    fake.add_channel("C1", "promemoria")
    connect(db_session, fake, admin)
    client = slack_client(fake)
    fake.fail_status = [503]
    with pytest.raises(Conflict, match="non ha risposto correttamente"):
        client.post_message(BOT_TOKEN, "C1", "ciao")
    assert fake.posts == [] and len(fake.calls("chat.postMessage")) == 1

    fake.fail_status = [429]
    client.post_message(BOT_TOKEN, "C1", "ciao")
    assert len(fake.posts) == 1


def test_access_denied_on_one_call_is_a_refusal_not_a_dead_token(db_session: Session) -> None:
    admin = make_user(db_session)
    fake = FakeSlack()
    workspace = connect(db_session, fake, admin)
    fake.fail["conversations.info"] = ["access_denied"]
    service = SlackService(db_session, settings=slack_settings(), client=slack_client(fake))
    with pytest.raises(SlackRefused):
        service.set_channel(actor_of(admin), "C1")
    db_session.refresh(workspace)
    assert workspace.status == "active"
