"""Reading and arranging mailboxes (REB-1014). Nothing here connects, reads or sends mail."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.mailboxes.health import MailboxHealth, gmail_health
from pigrocrm.core.mailboxes.models import PURPOSES, Mailbox, MailboxPurpose


class MailboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def for_account(self, account_id: UUID) -> Mailbox | None:
        """The mailbox wrapping a Google account. Every account has one: an insert of the
        account makes it (`gmail/models.py`)."""
        return self.session.scalar(select(Mailbox).where(Mailbox.google_account_id == account_id))

    def visible_to(self, user_id: UUID, *, administers: bool) -> list[Mailbox]:
        """A person's own mailboxes, visible to them alone, and the space's too when they
        administer it. Oldest first, so two identical calls answer in one order."""
        owned = Mailbox.owner_user_id == user_id
        where = or_(owned, Mailbox.space_owned) if administers else owned
        return list(
            self.session.scalars(
                select(Mailbox).where(where).order_by(Mailbox.created_at, Mailbox.id)
            )
        )

    def purposes(self, mailbox: Mailbox) -> list[MailboxPurpose]:
        return list(
            self.session.scalars(
                select(MailboxPurpose)
                .where(MailboxPurpose.mailbox_id == mailbox.id)
                .order_by(MailboxPurpose.purpose)
            )
        )

    def add_purpose(self, mailbox: Mailbox, purpose: str) -> MailboxPurpose:
        """Gives a mailbox a purpose it did not have; one it has is returned as it is. `billing`
        is refused on a personal mailbox: it sources inbox items, and a personal mailbox never
        does (REB-960). The schema refuses it too; this says why in words."""
        if purpose not in PURPOSES:
            raise ValidationFailed(
                "mailbox", "purpose", "unknown purpose", expected=", ".join(PURPOSES)
            )
        existing = self.session.scalar(
            select(MailboxPurpose).where(
                MailboxPurpose.mailbox_id == mailbox.id, MailboxPurpose.purpose == purpose
            )
        )
        if existing is not None:
            return existing
        if purpose == "billing" and mailbox.owner_user_id is not None:
            raise ValidationFailed(
                "mailbox", "purpose", "billing is allowed on a space-owned mailbox only"
            )
        row = MailboxPurpose(
            mailbox_id=mailbox.id, space_owned=mailbox.space_owned, purpose=purpose
        )
        self.session.add(row)
        self.session.flush()
        return row

    def purpose_watermark(self, mailbox: Mailbox, purpose: str) -> datetime | None:
        """Where the next read of this purpose starts, `None` for one never read. The
        `correspondence` watermark of a Gmail mailbox is its account's, which the Gmail sync
        reads and writes; every other one is the purpose row's."""
        if mailbox.google_account_id is not None and purpose == "correspondence":
            return self.session.scalar(
                select(GoogleAccount.sync_watermark).where(
                    GoogleAccount.id == mailbox.google_account_id
                )
            )
        return self.session.scalar(
            select(MailboxPurpose.sync_watermark).where(
                MailboxPurpose.mailbox_id == mailbox.id, MailboxPurpose.purpose == purpose
            )
        )

    def health(self, mailbox: Mailbox) -> MailboxHealth:
        """`active`, `failing`, `auth_failed` or `disconnected`. A Gmail mailbox says what its
        account says, mapped (`expired` and `revoked` are `auth_failed`)."""
        if mailbox.google_account_id is None:
            assert mailbox.status is not None  # the schema's check: only Gmail has no status
            return mailbox.status  # type: ignore[return-value]
        status = self.session.scalar(
            select(GoogleAccount.status).where(GoogleAccount.id == mailbox.google_account_id)
        )
        return gmail_health(status) if status is not None else "disconnected"

    def any_connected(self) -> bool:
        """Whether this space has a mailbox somebody has not disconnected, of any provider: what
        the sync job needs to read «configured». A space with IMAP mailboxes only is configured."""
        return (
            self.session.scalar(
                select(Mailbox.id)
                .outerjoin(GoogleAccount, Mailbox.google_account_id == GoogleAccount.id)
                .where(
                    or_(
                        GoogleAccount.status.not_in(("disconnected",)),
                        and_(Mailbox.google_account_id.is_(None), Mailbox.status != "disconnected"),
                    )
                )
                .limit(1)
            )
            is not None
        )

    def count_connected_without_account(self) -> int:
        """The mailboxes that are not Gmail and not disconnected: the ones `mail-sync` does not
        read through Google, which still make a space «configured»."""
        return len(
            self.session.scalars(
                select(Mailbox.id).where(
                    Mailbox.google_account_id.is_(None), Mailbox.status != "disconnected"
                )
            ).all()
        )

    def inbox_sources(self) -> list[Mailbox]:
        """The mailboxes that source inbox items: the ones holding the `billing` purpose, which
        the schema allows on a space-owned mailbox only. A personal mailbox is never in it."""
        return list(
            self.session.scalars(
                select(Mailbox)
                .join(MailboxPurpose, MailboxPurpose.mailbox_id == Mailbox.id)
                .where(MailboxPurpose.purpose == "billing")
                .order_by(Mailbox.created_at, Mailbox.id)
            )
        )
