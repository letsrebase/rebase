"""What a credential does when it stops working, and who finds out.

Spec §5.5 and criteria 5-8. This is the point where an OAuth integration usually lies:
The previous system could not tell `invalid_grant` from a flaky network, so a revoked grant and a
dropped packet produced the same screen and the same wrong reaction -- retry, forever,
against something that will never come back.

`gmail/tokens.py` already keeps the two apart. This file is about the rest of the trip:
the fact is recorded where it is learned, the account stops being usable, the refusal
names what the person has to do, and the banner has as many texts as there are causes.

Two distinctions are load-bearing and are asserted here one by one:

* **`status` is the credential; capability is the scopes.** A grant of less than was
  asked for is a *healthy* credential on which one feature is unavailable. Conflating
  them produces a "reconnect your account" prompt for a problem reconnecting does not
  fix.
* **Google revoking a consent and the user disconnecting a mailbox are not the same
  event.** They used to be one `status`, told apart only by a nullable timestamp, which
  meant a person who disconnected their own mailbox on purpose was told Google had
  revoked it and nagged to reconnect.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fakes.fake_gmail import FakeGmail
from fakes.gmail_fixtures import (
    REFRESH_TOKEN,
    actor_for,
    connected_account,
    gmail_settings,
    sync_service,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.models import Activity
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.account import CONSENT_WARNING_HOURS, GoogleAccountService
from pigrocrm.core.gmail.errors import ConsentExpired, CredentialRevoked, ScopeMissing
from pigrocrm.core.gmail.schemas import SCOPE_EMAIL, SCOPE_READONLY, SCOPE_SEND


def _service(session: Session, *, unverified: bool = False) -> GoogleAccountService:
    """`unverified` is the operator saying the OAuth client is still in Testing, which
    is the only mode in which a Google consent expires by itself -- and therefore the
    only mode in which `consent_expires_at` means anything."""
    return GoogleAccountService(session, settings=gmail_settings(google_app_unverified=unverified))


def _with_a_correspondent(session: Session) -> None:
    """A non-empty roster, so a cycle actually reaches the token refresh."""
    session.add(Customer(ragione_sociale="Acme", email="info@acme.it"))
    session.flush()


# --- the fact is recorded where it is learned ----------------------------------------


def test_invalid_grant_marks_the_account_revoked_and_records_it(db_session: Session) -> None:
    """Spec 13, criterion 5 (a) and (b). The state and the timeline entry survive the
    failure of the operation that discovered them, which is the whole point: the sync
    that found out is going to raise, and if the fact went with it nobody would ever
    learn why."""
    account = connected_account(db_session)
    _with_a_correspondent(db_session)

    fake = FakeGmail(revoked=True)
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, fake).sync(actor_for(account))

    db_session.refresh(account)
    assert account.status == "revoked"
    assert account.disconnected_at is None, "nobody here disconnected anything"
    assert account.last_error is not None
    assert account.last_error_at is not None
    rows = (
        db_session.execute(select(Activity).where(Activity.kind == "gmail.credenziale_revocata"))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    # `system`, not the caller: Google revoked this, nobody in this CRM did.
    assert rows[0].actor_type == "system"
    assert rows[0].entity_type == "google_account"
    assert rows[0].entity_id == account.id


def test_the_refresh_is_never_retried_on_invalid_grant(db_session: Session) -> None:
    """Spec 13, criterion 5 (f). Retrying an `invalid_grant` is a bug: it cannot
    succeed, and retrying only delays telling the user the one thing they must act on."""
    account = connected_account(db_session)
    _with_a_correspondent(db_session)

    fake = FakeGmail(revoked=True)
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, fake).sync(actor_for(account))
    assert fake.token_requests == 1


def test_the_next_sync_refuses_before_it_asks_google_anything(db_session: Session) -> None:
    """The other half of criterion 5 (f), and the one a retry loop actually hits: once
    the account is marked, a further cycle must not go and ask again. A cron every
    fifteen minutes against a dead grant is the loop the previous system ran forever."""
    account = connected_account(db_session)
    _with_a_correspondent(db_session)
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, FakeGmail(revoked=True)).sync(actor_for(account))

    second = FakeGmail(revoked=True)
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, second).sync(actor_for(account))
    assert second.requests == [], "the second cycle went back to Google anyway"


def test_the_stored_error_is_a_sentence_and_never_a_token_or_a_stack(
    db_session: Session,
) -> None:
    """`last_error` is shown to a person. The previous system's equivalent was
    `parseGoogleError` fed through `truncateMessage(400)`, which is how upstream prose
    ended up on screen."""
    account = connected_account(db_session)
    _with_a_correspondent(db_session)
    with pytest.raises(CredentialRevoked):
        sync_service(db_session, FakeGmail(revoked=True)).sync(actor_for(account))

    db_session.refresh(account)
    stored = account.last_error or ""
    assert REFRESH_TOKEN not in stored
    assert "Traceback" not in stored
    assert "invalid_grant" not in stored
    assert "ricollega" in stored.lower()
    assert account.email_address in stored


# --- the gate ------------------------------------------------------------------------


def test_the_gate_refuses_a_revoked_account_with_no_transport_at_all(
    db_session: Session,
) -> None:
    """Spec 13, criterion 5 (d): a send against a non-active account answers before any
    HTTP call is made. That is why this gate lives in `GoogleAccountService`, which is
    constructed from a session and settings and has no transport to call *with* -- and
    why 5B-2 calls it before composing anything rather than after building the RFC822.
    """
    account = connected_account(db_session, status="revoked")
    db_session.flush()
    with pytest.raises(CredentialRevoked) as caught:
        _service(db_session).usable(actor_for(account), scope=SCOPE_SEND, feature="l'invio")
    assert "revocato" in caught.value.message


def test_the_gate_tells_an_expired_consent_apart_from_a_revoked_one(
    db_session: Session,
) -> None:
    """`expired` is what we predicted; `revoked` is what Google told us. They call for
    the same action and are not the same sentence, and a gate that said "revocato" for
    both would be the previous system's defect one level down."""
    account = connected_account(db_session, status="expired")
    db_session.flush()
    with pytest.raises(ConsentExpired) as caught:
        _service(db_session).usable(actor_for(account), scope=SCOPE_SEND, feature="l'invio")
    assert "scaduto" in caught.value.message
    assert "revocato" not in caught.value.message


def test_the_gate_on_a_disconnected_mailbox_says_there_is_none(db_session: Session) -> None:
    """A mailbox the user disconnected is not a broken credential. Telling them it was
    revoked, and offering to reconnect what they deliberately unhooked, is a lie about
    their own action."""
    account = connected_account(db_session, status="disconnected")
    db_session.flush()
    with pytest.raises(Conflict, match="nessuna casella") as caught:
        _service(db_session).usable(actor_for(account), scope=SCOPE_SEND, feature="l'invio")
    assert not isinstance(caught.value, CredentialRevoked)


def test_a_partial_grant_leaves_the_account_active_and_refuses_only_the_sync(
    db_session: Session,
) -> None:
    """Spec 13, criterion 7. `status` describes the credential; capability is derived
    from the granted scopes at the point of use. The two must not be conflated."""
    account = connected_account(db_session, scopes=("openid", SCOPE_EMAIL, SCOPE_SEND))
    db_session.flush()
    service = _service(db_session)

    assert service.usable(actor_for(account), scope=SCOPE_SEND, feature="l'invio").id == account.id
    with pytest.raises(ScopeMissing) as caught:
        service.usable(actor_for(account), scope=SCOPE_READONLY, feature="la sincronizzazione")
    assert SCOPE_READONLY in caught.value.message
    db_session.refresh(account)
    assert account.status == "active"


def test_a_sync_without_the_read_scope_names_the_scope_instead_of_failing_upstream(
    db_session: Session,
) -> None:
    """And it refuses *before* the lock is taken, so a refused call cannot make the next
    one answer "already running"."""
    account = connected_account(db_session, scopes=("openid", SCOPE_EMAIL, SCOPE_SEND))
    _with_a_correspondent(db_session)
    fake = FakeGmail()
    with pytest.raises(ScopeMissing):
        sync_service(db_session, fake).sync(actor_for(account))
    assert fake.requests == []

    account.scopes_granted = ["openid", SCOPE_EMAIL, SCOPE_READONLY, SCOPE_SEND]
    db_session.flush()
    # The lock was never taken, so the very next call runs rather than being told
    # somebody else is already running.
    assert sync_service(db_session, fake).sync(actor_for(account)).already_running is False


# --- the banner ----------------------------------------------------------------------


def test_the_health_banner_has_a_cause_and_a_text_for_each(db_session: Session) -> None:
    """Spec 11: a single banner saying "problema con Gmail" helps nobody. Each cause
    calls for a different action, so each gets its own sentence."""
    account = connected_account(db_session)
    db_session.flush()
    # In Testing, so that the `expiring` branch -- the one that reads
    # `consent_expires_at` -- is live at all: see the two tests at the end of this
    # section for what a published app does with the same row.
    service = _service(db_session, unverified=True)
    actor = actor_for(account)

    assert service.health(actor).banner is None

    account.status = "revoked"
    db_session.flush()
    revoked = service.health(actor)
    assert revoked.banner == "revoked"
    assert "revocato" in (revoked.banner_text or "")

    account.status = "expired"
    db_session.flush()
    expired = service.health(actor)
    assert expired.banner == "expired"
    assert "scaduto" in (expired.banner_text or "")

    account.status = "active"
    account.consent_expires_at = datetime.now(UTC) + timedelta(hours=36)
    db_session.flush()
    expiring = service.health(actor)
    assert expiring.banner == "expiring"
    assert "rinnovato" in (expiring.banner_text or "")

    account.consent_expires_at = None
    account.scopes_granted = ["openid", SCOPE_EMAIL, SCOPE_SEND]
    db_session.flush()
    partial = service.health(actor)
    assert partial.banner == "scope_missing"
    assert SCOPE_READONLY in (partial.banner_text or "")
    assert partial.missing_scopes == [SCOPE_READONLY]

    # Four distinct texts, not one text with four causes behind it.
    texts = {revoked.banner_text, expired.banner_text, expiring.banner_text, partial.banner_text}
    assert len(texts) == 4


def test_a_mailbox_the_user_disconnected_shows_no_banner(db_session: Session) -> None:
    """Nothing is wrong, so there is nothing to warn about. This is the case that used
    to read "il consenso è stato revocato" at somebody who had just pressed
    "scollega"."""
    account = connected_account(db_session, status="disconnected")
    account.disconnected_at = datetime.now(UTC)
    db_session.flush()
    health = _service(db_session).health(actor_for(account))
    assert health.banner is None
    assert health.banner_text is None
    assert health.account is not None
    assert health.account.status == "disconnected"


def test_the_warning_arrives_before_anything_has_failed(db_session: Session) -> None:
    """Spec 13, criterion 6. A warning after the first error is not a warning: the error
    was the warning."""
    account = connected_account(db_session)
    account.consent_expires_at = datetime.now(UTC) + timedelta(hours=36)
    db_session.flush()
    health = _service(db_session, unverified=True).health(actor_for(account))
    assert health.banner == "expiring"
    assert account.status == "active"
    assert account.last_error is None
    assert CONSENT_WARNING_HOURS == 48


def test_the_expiring_banner_names_the_day_the_consent_runs_out(db_session: Session) -> None:
    """ "Rinnova presto" is not actionable. The date is what lets somebody decide whether
    this is a thing for now or a thing for Monday."""
    account = connected_account(db_session)
    when = datetime.now(UTC) + timedelta(hours=36)
    account.consent_expires_at = when
    db_session.flush()
    text = _service(db_session, unverified=True).health(actor_for(account)).banner_text or ""
    assert when.strftime("%d/%m/%Y") in text


def test_an_expiry_further_out_than_the_warning_window_says_nothing_yet(
    db_session: Session,
) -> None:
    """Warning about something six days away, every day, is how a banner becomes
    wallpaper."""
    account = connected_account(db_session)
    account.consent_expires_at = datetime.now(UTC) + timedelta(days=6)
    db_session.flush()
    assert _service(db_session, unverified=True).health(actor_for(account)).banner is None


def test_a_consent_already_past_its_date_is_not_reported_as_merely_expiring(
    db_session: Session,
) -> None:
    """The window is "within 48 hours", and a date in the past is inside it by
    arithmetic. It must not therefore read as a gentle heads-up."""
    account = connected_account(db_session)
    account.consent_expires_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.flush()
    health = _service(db_session, unverified=True).health(actor_for(account))
    assert health.banner == "expired"
    assert "scaduto" in (health.banner_text or "")


def test_a_published_app_stops_predicting_an_expiry_written_when_it_was_in_testing(
    db_session: Session,
) -> None:
    """The seven days belong to Testing mode, not to this CRM.

    A row written while the OAuth client was unverified carries a date; once the client
    is Internal or verified, Google stops expiring the refresh token and that date
    describes nothing. Left in, it would produce a banner asking somebody to renew a
    consent that is not going to lapse -- the same class of defect as "revocato" for a
    mailbox the user disconnected: a true-sounding sentence about a thing that did not
    happen.
    """
    account = connected_account(db_session)
    account.consent_expires_at = datetime.now(UTC) + timedelta(hours=36)
    db_session.flush()

    assert _service(db_session, unverified=True).health(actor_for(account)).banner == "expiring"
    assert _service(db_session).health(actor_for(account)).banner is None


def test_a_published_app_does_not_deduce_an_expired_consent_from_a_date(
    db_session: Session,
) -> None:
    """The date is stale, not the credential.

    This is the case that matters in practice: the seven days of Testing ran out weeks
    ago, the operator published the app in the meantime, and the mailbox has been
    syncing perfectly ever since. Reading `expired` off that old row would stop a
    working integration on the strength of a prediction nothing renewed.
    """
    account = connected_account(db_session)
    account.consent_expires_at = datetime.now(UTC) - timedelta(days=30)
    db_session.flush()

    assert _service(db_session, unverified=True).health(actor_for(account)).banner == "expired"
    health = _service(db_session).health(actor_for(account))
    assert health.banner is None
    assert health.banner_text is None
    assert health.account is not None
    assert health.account.status == "active"


def test_a_published_app_hands_no_expiry_to_the_settings_page_either(
    db_session: Session,
) -> None:
    """The banner is not the only place that prediction reached.

    `GmailHealth.account` is what the settings page renders, and it printed the same
    date next to the address -- «Consenso da rinnovare entro il ...» -- straight from
    this field. Filtering the banner alone would have moved the sentence rather than
    removed it, and the person would read it in the one page they go to in order to fix
    the thing it is wrong about.
    """
    account = connected_account(db_session)
    when = datetime.now(UTC) + timedelta(hours=36)
    account.consent_expires_at = when
    db_session.flush()

    testing = _service(db_session, unverified=True).health(actor_for(account))
    assert testing.account is not None
    assert testing.account.consent_expires_at == when

    published = _service(db_session).health(actor_for(account))
    assert published.account is not None
    assert published.account.consent_expires_at is None
    # The row itself is untouched: it is the record of what was true under the consent
    # that wrote it.
    assert account.consent_expires_at == when


def test_a_status_of_expired_is_a_fact_and_is_reported_whatever_the_app_is(
    db_session: Session,
) -> None:
    """Only the *prediction* goes away. `status` is what the credential is, written by
    something that learned it, and it is reported exactly as `revoked` is."""
    account = connected_account(db_session, status="expired")
    db_session.flush()
    health = _service(db_session).health(actor_for(account))
    assert health.banner == "expired"
    assert "scaduto" in (health.banner_text or "")


def test_health_on_an_installation_with_no_account_is_empty_not_an_error(
    db_session: Session,
) -> None:
    """An installation that never connected Gmail has nothing to say about Gmail. A
    banner there would be an error message for a feature nobody switched on."""
    user = User(email="solo@example.it", nome="Solo", password_hash="x", ruolo="admin", attivo=True)
    db_session.add(user)
    db_session.flush()
    health = _service(db_session).health(Actor(id=user.id, type="user", role="admin"))
    assert health.account is None
    assert health.banner is None
    assert health.missing_scopes == []


def test_health_never_answers_about_somebody_elses_mailbox(db_session: Session) -> None:
    """One account per user, and the question is always "mine". A `health` that read the
    first row in the table would show one person another's address."""
    mine = connected_account(db_session, email_address="mia@example.it")
    theirs = connected_account(db_session, email_address="sua@example.it")
    theirs.status = "revoked"
    db_session.flush()

    health = _service(db_session).health(actor_for(mine))
    assert health.account is not None
    assert health.account.email_address == "mia@example.it"
    assert health.banner is None


# --- settings stay reachable precisely when something is wrong -----------------------


def test_the_body_store_can_still_be_turned_off_on_a_revoked_account(
    db_session: Session,
) -> None:
    """A settings change is not a use of the credential. Gating it behind a healthy one
    would mean the moment a user most wants to stop the CRM keeping their mail is the
    moment they cannot."""
    account = connected_account(db_session, status="revoked")
    db_session.flush()
    read = _service(db_session).set_store_bodies(enabled=False, actor=actor_for(account))
    assert read.gmail_store_bodies is False

    db_session.refresh(account)
    assert account.gmail_store_bodies is False
    kinds = db_session.execute(select(Activity.kind)).scalars().all()
    assert "gmail.impostazioni_modificate" in kinds


def test_a_readonly_actor_cannot_change_the_gmail_settings(db_session: Session) -> None:
    from pigrocrm.core.errors import PermissionDenied  # noqa: PLC0415

    account = connected_account(db_session)
    db_session.flush()
    reader = Actor(id=account.user_id, type="user", role="readonly")
    with pytest.raises(PermissionDenied):
        _service(db_session).set_store_bodies(enabled=False, actor=reader)
