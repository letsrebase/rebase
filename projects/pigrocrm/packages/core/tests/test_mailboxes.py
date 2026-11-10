"""Mailboxes with owners and purposes, wrapping today's Gmail (REB-1014, § 2.2 of
`2026-11-09-incoming-documents-inbox-design.md`)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fakes.gmail_fixtures import connected_account
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.mailboxes.health import GMAIL_HEALTH, gmail_health
from pigrocrm.core.mailboxes.models import Mailbox, MailboxPurpose
from pigrocrm.core.mailboxes.repository import MailboxRepository


def _space_imap(session: Session, *, status: str = "active") -> Mailbox:
    mailbox = Mailbox(owner_user_id=None, provider="imap", status=status)
    session.add(mailbox)
    session.flush()
    return mailbox


def test_every_google_account_is_a_mailbox_with_the_purpose_correspondence(
    db_session: Session,
) -> None:
    account = connected_account(db_session)

    repo = MailboxRepository(db_session)
    mailbox = repo.for_account(account.id)
    assert mailbox is not None
    assert (mailbox.provider, mailbox.owner_user_id) == ("gmail", account.user_id)
    assert mailbox.space_owned is False
    assert mailbox.status is None, "a Gmail mailbox holds nothing its account holds"
    assert [p.purpose for p in repo.purposes(mailbox)] == ["correspondence"]


def test_deleting_the_account_takes_its_mailbox_and_purposes(db_session: Session) -> None:
    account = connected_account(db_session)
    mailbox = MailboxRepository(db_session).for_account(account.id)
    assert mailbox is not None
    mailbox_id = mailbox.id
    db_session.expunge(mailbox)

    db_session.execute(text("DELETE FROM google_accounts WHERE id = :id"), {"id": account.id})

    assert db_session.get(Mailbox, mailbox_id) is None
    assert db_session.scalar(select(MailboxPurpose.id)) is None


@pytest.mark.parametrize(
    ("account_status", "health"),
    [
        ("active", "active"),
        ("expired", "auth_failed"),
        ("revoked", "auth_failed"),
        ("disconnected", "disconnected"),
    ],
)
def test_gmail_statuses_map_onto_the_mailbox_health(
    db_session: Session, account_status: str, health: str
) -> None:
    account = connected_account(db_session, status=account_status)
    repo = MailboxRepository(db_session)
    mailbox = repo.for_account(account.id)
    assert mailbox is not None
    assert repo.health(mailbox) == health
    assert gmail_health(account_status) == health


def test_an_unknown_gmail_status_is_not_read_as_healthy() -> None:
    assert set(GMAIL_HEALTH) == {"active", "expired", "revoked", "disconnected"}
    with pytest.raises(KeyError):
        gmail_health("suspended")


def test_a_mailbox_without_an_account_carries_its_own_health(db_session: Session) -> None:
    repo = MailboxRepository(db_session)
    assert repo.health(_space_imap(db_session, status="failing")) == "failing"
    assert repo.health(_space_imap(db_session, status="auth_failed")) == "auth_failed"


def test_the_gmail_correspondence_watermark_is_the_accounts(db_session: Session) -> None:
    account = connected_account(db_session)
    repo = MailboxRepository(db_session)
    mailbox = repo.for_account(account.id)
    assert mailbox is not None
    assert repo.purpose_watermark(mailbox, "correspondence") is None

    moment = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    account.sync_watermark = moment
    db_session.flush()
    assert repo.purpose_watermark(mailbox, "correspondence") == moment

    space = _space_imap(db_session)
    purpose = repo.add_purpose(space, "billing")
    assert repo.purpose_watermark(space, "billing") is None
    purpose.sync_watermark = moment
    db_session.flush()
    assert repo.purpose_watermark(space, "billing") == moment
    assert repo.purpose_watermark(space, "correspondence") is None


def test_a_personal_mailbox_never_takes_billing(db_session: Session) -> None:
    account = connected_account(db_session)
    repo = MailboxRepository(db_session)
    mailbox = repo.for_account(account.id)
    assert mailbox is not None

    with pytest.raises(ValidationFailed):
        repo.add_purpose(mailbox, "billing")

    # The schema says it too, to whoever skips the repository.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO mailbox_purposes (id, mailbox_id, space_owned, purpose) "
                "VALUES (gen_random_uuid(), :m, false, 'billing')"
            ),
            {"m": mailbox.id},
        )
    # And a copy that disagrees with the mailbox's owner is no purpose of it.
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(
            text(
                "INSERT INTO mailbox_purposes (id, mailbox_id, space_owned, purpose) "
                "VALUES (gen_random_uuid(), :m, true, 'billing')"
            ),
            {"m": mailbox.id},
        )


def test_only_space_owned_mailboxes_source_inbox_items(db_session: Session) -> None:
    connected_account(db_session)  # a personal one, which can never be a source
    repo = MailboxRepository(db_session)
    space = _space_imap(db_session)
    assert repo.inbox_sources() == []

    assert repo.add_purpose(space, "billing").space_owned is True
    assert repo.add_purpose(space, "billing").id == repo.add_purpose(space, "billing").id
    assert [m.id for m in repo.inbox_sources()] == [space.id]


def test_an_unknown_purpose_is_refused(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        MailboxRepository(db_session).add_purpose(_space_imap(db_session), "activities")


def _refused_by(session: Session, statement: str, params: dict[str, object], name: str) -> None:
    """The statement fails, and on the named constraint: not on one of the others it also breaks."""
    with pytest.raises(IntegrityError) as raised, session.begin_nested():
        session.execute(text(statement), params)
    assert name in str(raised.value), str(raised.value)


def test_the_schema_keeps_gmail_personal_wrapping_an_account_and_one_per_person(
    db_session: Session,
) -> None:
    first = connected_account(db_session)
    second = connected_account(db_session, email_address="other@example.it")

    _refused_by(
        db_session,
        "UPDATE mailboxes SET owner_user_id = NULL WHERE google_account_id = :a",
        {"a": first.id},
        "ck_mailboxes_gmail_is_personal",
    )
    _refused_by(
        db_session,
        "INSERT INTO mailboxes (id, owner_user_id, provider) "
        "VALUES (gen_random_uuid(), :u, 'gmail')",
        {"u": first.user_id},
        "ck_mailboxes_gmail_wraps_an_account",
    )
    _refused_by(
        db_session,
        "INSERT INTO mailboxes (id, provider) VALUES (gen_random_uuid(), 'imap')",
        {},
        "ck_mailboxes_status_belongs",
    )
    _refused_by(
        db_session,
        "INSERT INTO mailboxes (id, provider, status) "
        "VALUES (gen_random_uuid(), 'imap', 'expired')",
        {},
        "ck_mailboxes_status_belongs",
    )
    _refused_by(
        db_session,
        "INSERT INTO mailboxes (id, provider, status) VALUES (gen_random_uuid(), 'pop3', 'active')",
        {},
        "ck_mailboxes_provider",
    )
    # An account is wrapped once.
    _refused_by(
        db_session,
        "INSERT INTO mailboxes (id, owner_user_id, provider, google_account_id) "
        "VALUES (gen_random_uuid(), :u, 'gmail', :a)",
        {"u": second.user_id, "a": first.id},
        "google_account_id",
    )
    # A person has one Gmail: hand the second account's mailbox to the first's owner.
    _refused_by(
        db_session,
        "UPDATE mailboxes SET owner_user_id = :u WHERE google_account_id = :a",
        {"u": first.user_id, "a": second.id},
        "uq_mailboxes_one_gmail_per_user",
    )


def test_a_person_sees_their_own_mailboxes_and_an_admin_the_spaces_too(
    db_session: Session,
) -> None:
    mine = connected_account(db_session)
    theirs = connected_account(db_session, email_address="other@example.it")
    space = _space_imap(db_session)
    repo = MailboxRepository(db_session)

    def ids(owner: UUID, *, administers: bool) -> set[UUID]:
        return {m.id for m in repo.visible_to(owner, administers=administers)}

    own = repo.for_account(mine.id)
    other = repo.for_account(theirs.id)
    assert own is not None and other is not None
    assert ids(mine.user_id, administers=False) == {own.id}
    assert ids(mine.user_id, administers=True) == {own.id, space.id}
    assert other.id not in ids(mine.user_id, administers=True)


def test_a_space_with_only_an_imap_mailbox_is_connected(db_session: Session) -> None:
    repo = MailboxRepository(db_session)
    assert repo.any_connected() is False
    _space_imap(db_session, status="disconnected")
    assert repo.any_connected() is False
    assert repo.count_connected_without_account() == 0
    _space_imap(db_session, status="failing")
    assert repo.any_connected() is True
    assert repo.count_connected_without_account() == 1
