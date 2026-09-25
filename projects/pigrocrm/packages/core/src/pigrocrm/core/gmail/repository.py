"""Queries only. Never commits -- the service owns the transaction."""

import hashlib
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from pigrocrm.core.db import escape_like
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.gmail.models import (
    EmailDraft,
    GmailKnownAddress,
    GmailMessage,
    GmailMessageLink,
    GoogleAccount,
    GoogleOAuthState,
    PaymentReminder,
)
from pigrocrm.core.gmail.roster import EntityRef
from pigrocrm.core.gmail.schemas import EDITABLE_SEND_STATES
from pigrocrm.core.invoices.models import Invoice

# An arbitrary but stable first key for `pg_try_advisory_lock(int, int)`, so this
# project's locks cannot collide with another application sharing the database. The
# two-integer form is used rather than the single bigint one precisely because it
# namespaces: hashing a UUID into 64 bits alone risks colliding with anything else that
# also hashes something into 64 bits.
SYNC_LOCK_NAMESPACE = 0x7091


def _lock_key(account_id: UUID) -> int:
    """A signed 32-bit integer derived from the account id. Postgres advisory-lock keys
    are `int4`, and passing an out-of-range value is an error rather than a truncation,
    so the 128 bits have to be folded into 32 somehow.

    Two ways of folding them are wrong here, and both look right:

    * **The leading bytes of the UUID.** Every primary key in this schema is a **uuid7**
      (`db/base.py`), whose first six bytes are a millisecond timestamp. The first four
      of them therefore only change once every 65 seconds, so every mailbox connected in
      the same minute would share a lock and one user's cron would silently suppress
      everyone else's sync. `test_gmail_lock.py` catches exactly that: two accounts made
      in the same test collide on the nose.
    * **`hash()`.** Python randomises the hash of `bytes` per process, so the API worker
      and the cron container would compute different keys for the same mailbox and the
      lock would not exist at all.

    A short blake2b digest has neither problem: it spreads uuid7's ordered bits over the
    whole range and it is the same number in every process, forever. A collision -- one
    in 2^32 per pair -- costs one user's sync answering "già in corso" and being picked
    up by the next cycle, which is why this is worth less than a lock table of its own.
    """
    return int.from_bytes(
        hashlib.blake2b(account_id.bytes, digest_size=4).digest(), "big", signed=True
    )


class GmailRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def account_for_user(self, user_id: UUID) -> GoogleAccount | None:
        return self.session.execute(
            select(GoogleAccount).where(GoogleAccount.user_id == user_id)
        ).scalar_one_or_none()

    def account(self, account_id: UUID) -> GoogleAccount | None:
        return self.session.get(GoogleAccount, account_id)

    def any_account(self) -> GoogleAccount | None:
        """One connected mailbox, whichever the installation has, or `None`.

        For the reminder candidate list, which is a per-installation question rather than
        a per-user one: the invoices are the issuer's, not any particular user's, and the
        reply signal only needs *a* mailbox that has synchronised the correspondence. On
        the ordinary single-user install this is that user's own account.

        Ordered by `connected_at`, so it is deterministic on an install with more than
        one -- an arbitrary row would make the reply signal appear and disappear between
        two identical calls. `None` is a supported answer and not an error: an
        installation that never connected Gmail still has invoices to chase, it simply
        carries no reply signal.
        """
        return self.session.execute(
            select(GoogleAccount).order_by(GoogleAccount.connected_at).limit(1)
        ).scalar_one_or_none()

    def all_accounts(self) -> list[GoogleAccount]:
        """Every mailbox row, whatever its status, oldest consent first.

        Unfiltered deliberately: `status` is what the caller is usually asking about,
        and a repository that hid the disconnected ones would make "there is one, and
        it is disconnected" indistinguishable from "there is none". `cli.py` drops them
        for its own question.

        For `pigrocrm gmail-sync`, which has to tell "there is one, use it" from "there
        are two, say which" -- a question `any_account` answers by picking, which is
        right for the reply signal and wrong for a cron that would otherwise leave one
        mailbox silently unsynchronised. Same ordering, for the same reason: an
        arbitrary row order would make the CLI's own error message list the mailboxes
        differently between two identical calls.
        """
        return list(
            self.session.execute(
                select(GoogleAccount).order_by(GoogleAccount.connected_at)
            ).scalars()
        )

    def add_state(self, state: GoogleOAuthState) -> GoogleOAuthState:
        self.session.add(state)
        self.session.flush()
        return state

    def consume_state(
        self, jti: str, now: datetime, purpose: str = "gmail"
    ) -> GoogleOAuthState | None:
        """Marks the row consumed and returns it, or returns `None` if it does not
        exist, has expired, was already consumed, or was minted for a different flow.

        One statement, deliberately: a conditional `UPDATE ... WHERE consumed_at IS
        NULL ... RETURNING`. Postgres serialises two concurrent callbacks carrying the
        same jti on the row lock, and the loser re-evaluates its `WHERE` against the
        winner's committed row, finds `consumed_at` no longer NULL and updates nothing.
        `test_gmail_models.py` proves that with two real connections and a barrier --
        drop the predicate and it reports `[1, 1]` instead of `[0, 1]`.

        A read-then-write pair over the same column would pass every single-threaded
        test and hand two callbacks the same authorisation code.

        `purpose` defaults to `"gmail"`, the value every row carried before Drive
        existed (see `GoogleOAuthState.purpose`'s docstring), so `GmailOAuthService`
        needs no change to keep filtering on it. It is part of the `WHERE` and not a
        check on the row after the fact, so a state minted for the other flow simply
        does not match this statement at all: it is left exactly as it was, still
        single-use and still redeemable by its own flow's callback. Answering `None`
        here must not spend it -- a Drive state is not this method's to consume.
        """
        return self.session.execute(
            update(GoogleOAuthState)
            .where(
                GoogleOAuthState.jti == jti,
                GoogleOAuthState.consumed_at.is_(None),
                GoogleOAuthState.expires_at > now,
                GoogleOAuthState.purpose == purpose,
            )
            .values(consumed_at=now)
            .returning(GoogleOAuthState)
        ).scalar_one_or_none()

    def prune_states(self, now: datetime) -> int:
        """Called at the start of every sync. `refresh_tokens` has this same problem
        and, per residuo R8, no pruning at all -- this table does not repeat it.

        Expiry, not consumption, is the criterion: a consumed row is still evidence
        that a jti was used, and it has to outlive its own TTL or the single-use check
        above would start answering "unknown" instead of "already redeemed" for a
        replay that arrives seconds later.
        """
        deleted = self.session.execute(
            delete(GoogleOAuthState)
            .where(GoogleOAuthState.expires_at < now)
            .returning(GoogleOAuthState.id)
        )
        # `RETURNING` rather than `rowcount`: the DBAPI's row count is typed as
        # `Any`-free only on `CursorResult`, and this table is small and short-lived
        # by construction, so counting the returned ids costs nothing worth naming.
        return len(deleted.scalars().all())

    # --- the per-mailbox sync lock ---------------------------------------------------

    def try_sync_lock(self, account_id: UUID) -> bool:
        """Non-blocking. A caller that does not get the lock must answer "already in
        progress" -- it must not wait, and it must not fail.

        Session-scoped and not `pg_try_advisory_xact_lock`, so the lock covers the whole
        cycle regardless of how the cycle chooses to commit. That is also why it has to
        be handed back explicitly: see `release_sync_lock`.
        """
        return bool(
            self.session.execute(
                text("SELECT pg_try_advisory_lock(:ns, :key)"),
                {"ns": SYNC_LOCK_NAMESPACE, "key": _lock_key(account_id)},
            ).scalar_one()
        )

    def release_sync_lock(self, account_id: UUID) -> None:
        """Hands the lock back on the connection that holds it.

        This must not be a statement that can fail. A session-level advisory lock
        outlives its transaction *and* its SQLAlchemy `Session`: the connection returns
        to the pool still holding it, and every later sync for that mailbox then answers
        "already running" for the life of the process -- indistinguishable from a hung
        job, and unfixable without a restart.

        The one way the unlock can fail is on a transaction Postgres has already
        aborted, which refuses every further statement until it is rolled back. Hence
        the retry, and hence the `rollback` -- which is not the wide one it looks like:
        the server threw that work away itself when it aborted, so there is nothing left
        to discard. Deliberately *not* pre-emptive. `Session.is_active` stays `True`
        after a failed raw `execute` (it only goes false on a failed flush), so a check
        before the fact would not fire, and rolling back unconditionally would throw
        away a healthy caller's uncommitted work for nothing.
        """
        try:
            self._advisory_unlock(account_id)
        except DBAPIError:
            self.session.rollback()
            self._advisory_unlock(account_id)

    def _advisory_unlock(self, account_id: UUID) -> None:
        self.session.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)"),
            {"ns": SYNC_LOCK_NAMESPACE, "key": _lock_key(account_id)},
        )

    def sync_started_at(self, account_id: UUID) -> datetime | None:
        """When the last cycle that *committed* began, or `None` if none ever has.

        Read from the row rather than from an in-memory attribute, because it is asked
        on behalf of a run happening on another connection: that run has not published
        its own `last_sync_at` yet and cannot, so the honest answer is the last one that
        finished. It is what makes "già in corso da stamattina" possible to tell apart
        from "già in corso da quaranta secondi", which is the whole reason a caller who
        lost the lock is given a time at all.
        """
        return self.session.execute(
            select(GoogleAccount.last_sync_at).where(GoogleAccount.id == account_id)
        ).scalar_one_or_none()

    # --- the addresses this mailbox has already looked for ---------------------------

    def seen_addresses(self, account_id: UUID) -> set[str]:
        """The register the backfill is derived from: roster minus this is "new"."""
        return set(
            self.session.execute(
                select(GmailKnownAddress.address).where(
                    GmailKnownAddress.google_account_id == account_id
                )
            )
            .scalars()
            .all()
        )

    def remember_addresses(self, account_id: UUID, addresses: Sequence[str]) -> int:
        """Records that this mailbox has now been searched over these addresses, and
        answers how many of them were not already recorded.

        One `INSERT ... ON CONFLICT DO NOTHING`, and that is the point of the method. A
        duplicate is expected -- two cycles overlapping, or an explicit backfill on an
        address the automatic one had already covered -- so it must cost exactly nothing
        beyond the statement. Catching an `IntegrityError` and calling
        `Session.rollback()` instead would discard every message and every link stored
        earlier in the same cycle, because the cycle commits once at the end: that is the
        same defect `add_message_if_absent` documents at length, and it would fire here
        on the *expected* outcome rather than on a rare one.

        Not the SAVEPOINT loop that method uses, because this one needs no row back: the
        count comes from `RETURNING`, so the whole thing is a single round trip however
        long the roster is.
        """
        if not addresses:
            return 0
        unique = list(dict.fromkeys(address.strip().lower() for address in addresses if address))
        if not unique:
            return 0
        inserted = self.session.execute(
            pg_insert(GmailKnownAddress)
            .values([{"google_account_id": account_id, "address": address} for address in unique])
            .on_conflict_do_nothing(constraint="uq_gmail_known_addresses")
            .returning(GmailKnownAddress.id)
        )
        return len(inserted.scalars().all())

    # --- messages --------------------------------------------------------------------

    def message_ids_present(self, account_id: UUID, gmail_ids: Sequence[str]) -> set[str]:
        """Which of these Gmail ids this account has already stored.

        One query per thread rather than one per message: a cycle re-reads a whole day
        of already-stored conversations by design, so the common case is a thread in
        which every message is known and the interesting number is how many round trips
        finding that out costs.
        """
        if not gmail_ids:
            return set()
        rows = (
            self.session.execute(
                select(GmailMessage.gmail_message_id).where(
                    GmailMessage.google_account_id == account_id,
                    GmailMessage.gmail_message_id.in_(list(gmail_ids)),
                )
            )
            .scalars()
            .all()
        )
        return set(rows)

    def add_message_if_absent(self, message: GmailMessage) -> bool:
        """Inserts, and answers `False` if the unique constraint refused it.

        The insert runs inside a SAVEPOINT, and that is the point of the method. A
        duplicate is expected -- the watermark's overlap produces them on every cycle --
        so it must cost exactly the failed statement and nothing else. Catching the
        `IntegrityError` and calling `Session.rollback()` instead would discard every
        message stored earlier in the same cycle, because the cycle commits once at the
        end: one duplicate in a thread of thirty would throw away the twenty-nine before
        it. `test_gmail_sync.py` proves that difference on a single connection, and the
        two-thread race proves why the constraint is needed at all.
        """
        try:
            with self.session.begin_nested():
                self.session.add(message)
                self.session.flush()
        except IntegrityError:
            return False
        return True

    def message(self, message_id: UUID) -> GmailMessage | None:
        """One stored message by *our* id, not Gmail's.

        Returns `None` rather than raising, like `account` and `message_by_gmail_id`
        above: a repository answers what is there, and which of "not found" and "not
        yours" a caller should say is the caller's decision. The MCP surface turns this
        into `NotFound`.
        """
        return self.session.get(GmailMessage, message_id)

    def message_by_gmail_id(self, account_id: UUID, gmail_id: str) -> GmailMessage | None:
        return self.session.execute(
            select(GmailMessage).where(
                GmailMessage.google_account_id == account_id,
                GmailMessage.gmail_message_id == gmail_id,
            )
        ).scalar_one_or_none()

    def delete_messages_for(self, account_id: UUID) -> int:
        """Called only when the user explicitly chose to on disconnect. The
        `gmail_message_links` rows go with them by ON DELETE CASCADE.

        `RETURNING` and not `rowcount`, for the reason `prune_states` states: the
        DBAPI's row count is only typed on `CursorResult`. The count matters here --
        it is what the timeline entry records about an irreversible deletion -- so it
        has to come from somewhere the type system agrees exists.
        """
        deleted = self.session.execute(
            delete(GmailMessage)
            .where(GmailMessage.google_account_id == account_id)
            .returning(GmailMessage.id)
        )
        return len(deleted.scalars().all())

    # --- links -----------------------------------------------------------------------

    def add_link(self, message_id: UUID, ref: EntityRef) -> bool:
        """Files one message against one entity, and answers `False` when the triple
        already existed.

        Concurrency-safe by constraint and not by pre-check: two overlapping cycles both
        pass a `SELECT`, and only `uq_gmail_message_links_triple` can arbitrate. And, for
        the reason `add_message_if_absent` states at length, the refusal is absorbed by a
        SAVEPOINT rather than by `Session.rollback()`: a cycle commits once at the end,
        so a session-wide rollback here would discard every message and every link
        written earlier in the same cycle -- on the *expected* outcome of the watermark's
        overlap re-reading a conversation that is already filed.
        """
        try:
            with self.session.begin_nested():
                self.session.add(
                    GmailMessageLink(
                        gmail_message_id=message_id,
                        entity_type=ref.entity_type,
                        entity_id=ref.entity_id,
                    )
                )
                self.session.flush()
        except IntegrityError:
            return False
        return True

    def messages_for_entity(
        self, entity_type: str, entity_id: UUID, *, limit: int
    ) -> list[GmailMessage]:
        """Everything filed against one customer, person or deal.

        Ordered by thread and then by date, because the reader of a customer page is
        reading conversations rather than a flat mailbox: interleaving two threads by
        timestamp alone produces a page on which no exchange can be followed.
        """
        return list(
            self.session.execute(
                select(GmailMessage)
                .join(GmailMessageLink, GmailMessageLink.gmail_message_id == GmailMessage.id)
                .where(
                    GmailMessageLink.entity_type == entity_type,
                    GmailMessageLink.entity_id == entity_id,
                )
                .order_by(GmailMessage.gmail_thread_id, GmailMessage.internal_date)
                .limit(limit)
            )
            .scalars()
            .all()
        )

    # --- the send claim ---------------------------------------------------------------

    def claim_draft_for_send(self, draft_id: UUID, now: datetime, account_id: UUID) -> bool:
        """Moves a draft out of an editable state into `in_invio` in one statement, and
        answers whether *this* caller is the one that moved it.

        A conditional UPDATE and not a SELECT-then-set: two concurrent requests both pass
        a read, and only the database can arbitrate which of them owns the send.
        `send_state` is the guard in the WHERE clause, so the second caller updates zero
        rows and learns it lost -- and the email is sent once. The previous system kept this count
        in a JSON file with a non-atomic read-modify-write and lost it under exactly this race.

        The predicate is `EDITABLE_SEND_STATES` rather than `== "bozza"`, and that is the
        same fact stated twice on purpose: a draft whose previous attempt was refused is
        `fallito`, nothing left, and it must be sendable again -- otherwise the only
        recovery from one 400 from Gmail is retyping the message.

        `last_error` is cleared with the claim: an error sentence next to a send that is
        currently in flight describes a previous attempt and reads as one happening now.

        `google_account_id` is written **here** and nowhere else, because the claim is the
        first moment a mailbox is involved at all -- and every state whose outcome nobody
        knows (`in_invio`, `incerto`) is reached through this statement, so writing it
        with the claim is what makes "every reconcilable draft says whose it is" true by
        construction rather than by remembering. Without it, one user's sync reconciles
        another user's unresolved send against the wrong mailbox and can write `fallito`
        on a delivered message.

        `synchronize_session=False` because the default would be actively wrong here.
        SQLAlchemy's `evaluate` strategy applies the `values()` to any object in this
        session that matches the criteria *in memory* -- and the loser of the race holds
        an ORM copy loaded before the winner committed, so it still reads `bozza` and
        would be marked `in_invio` locally by an UPDATE that touched zero rows. The
        caller re-reads the committed state instead, which is the only honest source.

        `RETURNING` and not `rowcount`, for the reason `prune_states` and
        `delete_messages_for` both give: the DBAPI's row count is typed only on
        `CursorResult`, and this answer decides whether an email is sent -- it has to come
        from somewhere the type system agrees exists.
        """
        claimed = self.session.execute(
            update(EmailDraft)
            .where(
                EmailDraft.id == draft_id,
                EmailDraft.send_state.in_(sorted(EDITABLE_SEND_STATES)),
            )
            .values(
                send_state="in_invio",
                send_attempted_at=now,
                last_error=None,
                google_account_id=account_id,
            )
            .returning(EmailDraft.id)
            .execution_options(synchronize_session=False)
        )
        return claimed.scalar_one_or_none() is not None

    # --- resolving an unknown send outcome --------------------------------------------

    def uncertain_draft_ids(self, account_id: UUID) -> list[UUID]:
        """Every draft **of this mailbox** whose outcome nobody knows, oldest attempt
        first.

        `in_invio` is in here beside `incerto` and it is not an oversight: a process that
        died between the claim and the record leaves exactly that, and a draft left in it
        can be neither edited, nor sent, nor verified. `EmailSendService.reconcile` is
        what decides whether a given `in_invio` is still in flight or abandoned -- this
        query only says which rows are worth asking about.

        Scoped to one account, and that scoping is the fix for a defect B2-6 could
        mitigate and not remove. `reconcile_all` runs inside each user's own sync cycle,
        so an unscoped list handed user A's cycle user B's unresolved send: A's mailbox
        cannot contain it, the lookups all come back empty, and past the grace window the
        reconciliation writes `fallito` on a message that is sitting in B's client's
        inbox. Telling somebody their email failed when it was delivered is the one
        outcome here that cannot be walked back, because the answer to it is to send it
        again.

        A draft with no account -- an install that had two mailboxes when 0020 ran, so the
        backfill could not attribute it -- is deliberately in nobody's list. It keeps its
        state, its text and its «controlla Posta inviata» sentence until a person asks
        for it by hand, which is the safe direction of the two.
        """
        return list(
            self.session.execute(
                select(EmailDraft.id)
                .where(
                    EmailDraft.send_state.in_(("incerto", "in_invio")),
                    EmailDraft.google_account_id == account_id,
                )
                .order_by(EmailDraft.send_attempted_at)
            )
            .scalars()
            .all()
        )

    def outbound_by_header(self, message_id_header: str) -> GmailMessage | None:
        """An already-stored outbound message carrying a `Message-ID` we minted.

        Deliberately **not** scoped to one account, unlike every other read here, and the
        reason is that the value is ours: `email_drafts.message_id_header` is unique, so
        this can only ever match the very message this draft produced. It costs no Gmail
        quota and it is exact, which is why the reconciliation asks it first.
        """
        if not message_id_header:
            return None
        return self.session.execute(
            select(GmailMessage).where(
                GmailMessage.direction == "outbound",
                GmailMessage.message_id_header == message_id_header,
            )
        ).scalar_one_or_none()

    def outbound_candidates(
        self, account_id: UUID, subject: str, since: datetime
    ) -> list[GmailMessage]:
        """This account's own outgoing mail with this exact subject, from `since` on.

        The raw material of the approximate match of spec 6.3. Scoped to the account,
        because another user's mailbox cannot answer this user's question, and to an
        exact subject, because the whole value of a declaredly inferior match is that it
        stays narrow.
        """
        return list(
            self.session.execute(
                select(GmailMessage)
                .where(
                    GmailMessage.google_account_id == account_id,
                    GmailMessage.direction == "outbound",
                    GmailMessage.subject == subject,
                    GmailMessage.internal_date >= since,
                )
                .order_by(GmailMessage.internal_date)
            )
            .scalars()
            .all()
        )

    def claimed_send_ids(self, gmail_ids: Sequence[str]) -> set[str]:
        """Which of these Gmail ids some draft already records as its own send.

        The guard on the approximate match: without it, a second near-identical message
        could adopt the id of the first one, and two drafts would both claim one email.
        """
        if not gmail_ids:
            return set()
        # The column is nullable -- most drafts have never been sent -- so the `IN`
        # already excludes NULL and the comprehension is what tells the type checker so.
        return {
            claimed
            for claimed in self.session.execute(
                select(EmailDraft.sent_gmail_message_id).where(
                    EmailDraft.sent_gmail_message_id.in_(list(gmail_ids))
                )
            )
            .scalars()
            .all()
            if claimed is not None
        }

    # --- payment reminders -------------------------------------------------------------

    def reminder_rows(self, invoice_id: UUID) -> tuple[int, int, datetime | None]:
        """`(rows, sent, last_activity)` for one invoice's reminders.

        Three numbers in one round trip, because `create_reminder` needs all three and
        each answers a different question: how many positions in the sequence are taken
        (the ceiling), how many letters a client actually received (the wording), and when
        the last reminder was sent *or merely prepared* (the interval). Collapsing any two
        of them either offers a fourth reminder the constraint refuses, or opens a second
        letter with «nonostante il precedente sollecito» about one nobody received.
        """
        row = self.session.execute(
            select(
                func.count(),
                func.count(PaymentReminder.sent_at),
                func.max(func.coalesce(PaymentReminder.sent_at, PaymentReminder.created_at)),
            ).where(PaymentReminder.invoice_id == invoice_id)
        ).one()
        return int(row[0]), int(row[1]), row[2]

    def reminder_for_draft(self, draft_id: UUID) -> PaymentReminder | None:
        """The reminder a draft carries, if it carries one.

        Keyed on the draft and never on "the newest reminder for this invoice": the send
        path calls this for *every* message it sends, and an ordinary email to a client
        who also happens to owe money must leave `payment_reminders` untouched.
        """
        return self.session.execute(
            select(PaymentReminder).where(PaymentReminder.email_draft_id == draft_id)
        ).scalar_one_or_none()

    def invoice_pdf_version_ids(self, invoice_id: UUID) -> list[UUID]:
        """The current version of the invoice's own PDF, or an empty list.

        Spec 6.4: attachments come from `document_versions` and never from an upload, so
        the courtesy copy the reminder promises is the same artefact slice 3 rendered and
        stored -- not a fresh render, which could differ from what the client already has.

        Empty is a supported answer. A proforma converted before the PDF existed, or an
        invoice whose document was removed, still deserves a reminder; the body says «in
        allegato trova copia di cortesia», which is the one imperfection here, and it is
        smaller than refusing to chase a real debt over a missing file.
        """
        document_id = self.session.execute(
            select(Invoice.pdf_document_id).where(Invoice.id == invoice_id)
        ).scalar_one_or_none()
        if document_id is None:
            return []
        version_id = self.session.execute(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.document_id == document_id,
                # The *current* version, by the document's own pointer rather than by
                # `max(numero)`: those two disagree the moment a version is added and the
                # pointer is not moved, and the document layer owns that decision.
                DocumentVersion.numero == Document.versione_corrente,
                # The PDF goes out as a PDF or not at all (REB-480): a current version of
                # another type, written before `add_version` refused one on this
                # document, is no courtesy copy, and the reminder goes without one.
                DocumentVersion.content_type == "application/pdf",
                Document.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        return [version_id] if version_id is not None else []

    def last_outbound_about(self, account_id: UUID, numero: str) -> UUID | None:
        """The newest message this mailbox sent whose subject names this invoice.

        What threads a reminder onto the invoice's own covering email, so the recipient
        can see the document above it (spec 6.2 rule 2). Without it the reminder arrives
        detached and the first thing they do is ask for the invoice again.

        `escape_like` on the number, because `_` and `%` in a `LIKE` pattern are wildcards
        and an invoice number is data: `2026/1` must not match `2026/14`. It cannot,
        because the escaped needle is matched literally -- the number is bracketed by the
        subject's own text on both sides only when it really appears.
        """
        if not numero:
            return None
        like = f"%{escape_like(numero)}%"
        return self.session.execute(
            select(GmailMessage.id)
            .where(
                GmailMessage.google_account_id == account_id,
                GmailMessage.direction == "outbound",
                GmailMessage.subject.like(like, escape="\\"),
            )
            .order_by(GmailMessage.internal_date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def emitter_profile(self) -> EmitterProfile | None:
        """The single issuer row, or `None` on an installation that has not filled it in.

        It supplies the display name on the `From` header. Read through the repository
        rather than by constructing `EmitterProfileService`, which would pull a whole
        service (and its `actor` checks) into a path that needs one string.
        """
        return self.session.execute(select(EmitterProfile).limit(1)).scalar_one_or_none()

    def last_inbound_from(
        self, account_id: UUID, addresses: Sequence[str], since: datetime
    ) -> GmailMessage | None:
        """The most recent inbound message from any of `addresses` after `since`.

        This is the signal the previous system could not have had: from the moment the CRM reads the
        mail, the reminder candidate list can say "the client replied on 12 August".
        Chasing someone who has already replied is the mistake a CRM that does not read
        email cannot even notice it is making.

        Scoped to one account, because Gmail's message ids and the addresses in them
        belong to a mailbox: another user's correspondence must not answer this user's
        question about whether the client wrote back.
        """
        if not addresses:
            # Not merely an optimisation: an empty sequence would render as `IN ()`,
            # which Postgres refuses outright.
            return None
        return self.session.execute(
            select(GmailMessage)
            .where(
                GmailMessage.google_account_id == account_id,
                GmailMessage.direction == "inbound",
                GmailMessage.from_address.in_([address.strip().lower() for address in addresses]),
                GmailMessage.internal_date > since,
            )
            .order_by(GmailMessage.internal_date.desc())
            .limit(1)
        ).scalar_one_or_none()
