"""The only place in this slice that can call `users.messages.send`.

One send path, in the whole slice. Payment reminders do not get their own -- spec 8.3 is
explicit that `POST /api/payment-reminders` creates a row and a draft and does not send,
so that the reminder logic can rely on this idempotence rather than reimplementing it.

**On transaction boundaries.** Every other service method in this codebase is one
transaction. This one is deliberately three, and the reason is the design rather than an
oversight: there is a non-transactional side effect -- an email leaving the building --
in the middle.

1. The claim (`bozza`/`fallito` -> `in_invio`) is committed **before** the HTTP call,
   because a concurrent request has to be able to see it. An uncommitted claim is
   invisible to the other connection, and two requests would both send.
2. The HTTP call happens outside any transaction, holding no locks. A synchronous send
   holding a row lock for the length of a network round trip is how a database gets
   wedged by a slow third party.
3. The outcome is committed after.

The cost is that the process can die between 2 and 3. That is precisely the `incerto`
state, and `reconcile` is how it gets resolved -- by asking Gmail, not by guessing. The previous
system had the same window and no name for it, which is why it answers
`404 'Fattura non trovata per registrare l'invio'` while the email is already delivered,
and why the operator then presses the button again.

**No `Session.rollback()` anywhere below, and that is a rule rather than an accident.**
Between the claim's commit and the outcome's commit this service performs reads only, so
there is nothing of its own a rollback could discard -- and on a send path a rollback
that reached one statement too far would un-record that a send happened, which is the
one defect here that costs somebody's client a second copy of an email. 5B-1 found that
shape twice already (a consumed OAuth state reopened, a whole cycle's messages thrown
away on the expected duplicate).

Nothing here logs. Nothing that leaves this module -- an exception, a `last_error` the
composer renders, an activity payload -- carries a body, and `last_error` carries no
recipient either: it is a stored column that the drafts list, the REST layer and every
test failure dump render. The bearer token exists only as a local in `_token`.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, decode_google_token_key, require_gmail_configured
from pigrocrm.core.errors import Conflict, NotFound
from pigrocrm.core.gmail.account import GoogleAccountService
from pigrocrm.core.gmail.attach import resolve_attachments
from pigrocrm.core.gmail.crypto import unseal
from pigrocrm.core.gmail.drafts import read_draft
from pigrocrm.core.gmail.errors import GoogleCallFailed
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage, GoogleAccount
from pigrocrm.core.gmail.query import GMAIL_SEND_URL, messages_list_url, rfc822msgid_query
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.rfc822 import build_rfc822, to_base64url
from pigrocrm.core.gmail.roster import AddressRoster, EntityRef
from pigrocrm.core.gmail.schemas import (
    EDITABLE_SEND_STATES,
    SCOPE_READONLY,
    SCOPE_SEND,
    EmailDraftRead,
)
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport
from pigrocrm.core.storage.base import DocumentStorage

ENTITY = "email_draft"
_SEND_ACTION = "inviare un'email"
_RECONCILE_ACTION = "verificare l'esito di un'email"
_WHAT_SEND = "invio del messaggio"
_WHAT_VERIFY = "verifica dell'invio"

# The two states in which nobody knows whether the message left. `incerto` is the one
# spec 6.3(b) names -- Gmail did not answer. `in_invio` is the same ignorance arrived at
# differently: the process died between the claim and the record, so the claim is all
# that is left. Both are resolved the same way, by asking, and leaving `in_invio` out
# would make a crashed request strand a draft nobody can edit, send or verify.
_UNRESOLVED = frozenset({"incerto", "in_invio"})

# A definite refusal: Gmail looked at the message and said no, so nothing left and the
# draft is safe to retry. Anything else -- a timeout, a lost connection, a 5xx after
# every retry -- is "we do not know", and guessing either way is what produces a double
# send or a lost email. The asymmetry is deliberate: calling an unknown outcome `fallito`
# invites a retry that may be the second copy, while calling it `incerto` costs one
# lookup. Only statuses that mean "this request was rejected before it became mail" are
# in here.
_DEFINITE_REFUSAL = frozenset({400, 403, 404, 413, 422})

# What the user reads. No status code from Google's prose, no recipient, no line of the
# message: `last_error` is rendered by the composer, the drafts list and the REST layer.
_COMPOSE_FAILED = "Non è stato possibile comporre il messaggio: controlla gli allegati e riprova."
_REFUSED = "Gmail ha rifiutato il messaggio: la bozza è intatta, correggila e riprova."
_TOKEN_FAILED = (
    "Non è stato possibile accedere a Gmail: il messaggio non è partito e la bozza è intatta."
)
_UNCERTAIN = (
    "Non sappiamo se il messaggio sia partito: Gmail non ha risposto. "
    "Usa «verifica» prima di rinviarlo."
)
# The sentence for the one case this design cannot make certain. It names where to look
# rather than only asserting the negative, because the residual risk is exactly that
# Gmail replaced the `Message-ID` we supplied -- the note on that check still records
# UNVERIFIED -- and a person who reads «non è partito» about a message sitting in their
# own Sent folder will send it a second time.
_NOT_SENT = (
    "Il messaggio non risulta partito: la bozza è intatta e puoi riprovare. "
    "Controlla «Posta inviata» su Gmail prima di rinviarlo."
)

# The sentences a refused send raises with. They name the state, never the correspondence.
_ALREADY: dict[str, str] = {
    "inviato": "questa email è già stata inviata",
    "in_invio": "questa email è già in invio",
    "incerto": "l'esito di questa email è da verificare: usa «verifica» prima di rinviare",
}


class EmailSendService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        transport: GmailTransport,
        tokens: GoogleTokenClient,
        storage: DocumentStorage,
    ) -> None:
        self.session = session
        self.settings = settings
        self.transport = transport
        self.tokens = tokens
        self.storage = storage
        self.repo = GmailRepository(session)
        self.roster = AddressRoster(session)
        self.activities = ActivityService(session)
        self.accounts = GoogleAccountService(session, settings=settings)

    def send(self, draft_id: UUID, actor: Actor) -> EmailDraftRead:
        require_gmail_configured(self.settings)
        actor.require_write(_SEND_ACTION)

        # (1) The gate, first. Before the attachments are read, before the RFC822 is
        # built and before the draft is claimed: an operator pressing Invia on a revoked
        # account learns it at the press, nothing is half-done in between, and -- because
        # `GoogleAccountService` holds no transport -- no request was made to find out.
        account = self.accounts.usable(actor, scope=SCOPE_SEND, feature="l'invio")

        draft = self.session.get(EmailDraft, draft_id)
        if draft is None:
            raise NotFound(ENTITY, draft_id)
        if draft.send_state not in EDITABLE_SEND_STATES:
            # A cheap read that answers the ordinary double click without touching the
            # row. It is *not* the guarantee -- two concurrent requests both pass it,
            # which is why the claim below is the thing that arbitrates.
            raise Conflict(ENTITY, _already(draft.send_state), send_state=draft.send_state)

        # (2) Claim it, and commit the claim so a concurrent request can see it.
        if not self.repo.claim_draft_for_send(draft_id, datetime.now(UTC), account.id):
            state = self._state_now(draft_id)
            raise Conflict(ENTITY, _already(state), send_state=state)
        self.session.commit()
        # The claim was a Core UPDATE, so the ORM copy loaded above still carries the
        # pre-claim `send_state` and a null `send_attempted_at`. Expired rather than
        # trusted: everything below composes from, and reasons about, the committed row.
        self.session.expire(draft)

        try:
            raw = self._compose(account, draft)
        except Exception:
            # Composition failed -- an oversized attachment, a missing blob, a header the
            # builder refused. Nothing left, so the draft goes back to a state the
            # composer can reopen rather than being stranded in `in_invio` forever. The
            # real reason travels in the exception, which the adapter renders; the stored
            # sentence stays generic because a storage key or a document title is not
            # something `last_error` may hold.
            self._finish(draft_id, "fallito", error=_COMPOSE_FAILED, actor=actor)
            raise

        try:
            token = self._token(account)
        except Exception:
            # A token refresh happens *before* the message exists at Gmail, so a refresh
            # that fails -- revoked grant, dead network, a 500 after every retry -- has
            # sent nothing. `fallito` and not `incerto`: there is no outcome to verify,
            # and making the user press «verifica» for a message that was never composed
            # into a request would be an unknown invented out of a known.
            self._finish(draft_id, "fallito", error=_TOKEN_FAILED, actor=actor)
            raise

        try:
            answer = self.transport.json(
                "POST",
                GMAIL_SEND_URL,
                token=token,
                body={"raw": raw},
                what=_WHAT_SEND,
                # **One attempt.** A retry is safe only when the failed attempt is known
                # to have had no effect, and a 599 or a 502 on a send means "no answer
                # arrived", not "nothing was sent". Gmail offers no idempotency key, so
                # the second attempt is a second email -- the transport's four attempts
                # would deliver up to four copies to somebody's client, which is the
                # exact defect this task exists to close, arrived at from the inside.
                # An unknown outcome is `incerto` and is resolved by `reconcile`.
                retry=False,
            )
        except GoogleCallFailed as failed:
            if failed.failure.status in _DEFINITE_REFUSAL:
                # (3a) Gmail refused. The clean case of spec 6.3(a): nothing left, so the
                # draft is intact and editable with the error beside it.
                self._finish(draft_id, "fallito", error=_REFUSED, actor=actor)
                raise Conflict(
                    ENTITY,
                    "Gmail ha rifiutato il messaggio: il testo è rimasto nella bozza",
                    send_state="fallito",
                    status=failed.failure.status,
                ) from failed
            # (3b) We do not know. The message MAY be in the user's Sent folder. Neither
            # assumption is made -- `reconcile` settles it by asking.
            # The timeline entry goes in the same transaction as the state, not after it:
            # «esito da verificare» is a thing that happened to this customer's
            # correspondence, and a state without its trace is how it goes unnoticed.
            self._finish(
                draft_id,
                "incerto",
                error=_UNCERTAIN,
                actor=actor,
                kind="gmail.invio_incerto",
            )
            raise Conflict(
                ENTITY,
                "esito dell'invio da verificare: Gmail non ha risposto",
                send_state="incerto",
                status=failed.failure.status,
            ) from failed

        return self._record_sent(
            account,
            draft_id,
            gmail_message_id=str(answer.get("id") or ""),
            gmail_thread_id=str(answer.get("threadId") or ""),
            actor=actor,
        )

    # ---- resolving an outcome nobody knows ----------------------------------------

    def reconcile(self, draft_id: UUID, actor: Actor) -> EmailDraftRead:
        """Resolves a send whose outcome is unknown by *asking*, never by guessing.

        Three lookups, cheapest and most certain first, and the third exists because of
        an assumption this project has been careful not to promote into a fact:

        1. **Our own `Message-ID` in `gmail_messages`.** If any cycle has already stored
           an outbound message carrying the id we minted for this draft, the message
           left. Free, exact, and no request to Google at all.
        2. **`q=rfc822msgid:<our Message-ID>` at Gmail.** Exact, because we chose that id
           before calling send (spec 6.2). This is the path the design is built around --
           *if* Gmail preserves a client-supplied `Message-ID`, which
           `docs/superpowers/notes/2026-08-20-gmail-message-id-verification.md` records
           as **UNVERIFIED**: nobody has run the check against a real Gmail.
        3. **Recipient + subject inside the window, over the mail this account has
           already synchronised.** The fallback spec 6.3 names, and it runs *always* --
           not only when `gmail_reconcile_by_message_id` is off. That is the whole point:
           a `rfc822msgid` lookup coming back empty is exactly what an unpreserved
           `Message-ID` looks like, and declaring `fallito` on the strength of it would
           be reporting a message the user can see in Sent as not sent. It is
           declaredly inferior -- an approximate match -- so it is used only after the
           exact one has found nothing, and it refuses a message some other draft already
           records as its own send.

        Found means it left: adopt Gmail's id and mark it `inviato`. Not found, after the
        grace window, means it did not: `fallito`, with the draft intact and a sentence
        that tells the person to look in Sent before resending, because that residual
        case is precisely what `NO` in the note would look like in production.

        Why a grace window at all: Gmail's search index is not instantaneous. Declaring
        failure at second zero would turn a slow index into a resend, which is the
        outcome this whole design exists to prevent.
        """
        # Role-gated like the send it repairs, and for two reasons rather than one: this
        # method writes `send_state` (`inviato` or `fallito`, both terminal) and it spends
        # the owner's Gmail quota under the owner's OAuth grant. A read-only actor may
        # look at an `incerto` draft; resolving it is the other half of having pressed
        # Invia. `reconcile_all` reaches here from `GmailSyncService._run_cycle`, whose
        # own `sync` already requires write, so the cycle is unaffected.
        actor.require_write(_RECONCILE_ACTION)

        draft = self.session.get(EmailDraft, draft_id)
        if draft is None:
            raise NotFound(ENTITY, draft_id)
        if draft.send_state not in _UNRESOLVED:
            # `bozza`, `fallito`, `inviato`: nothing to resolve, and no request made. A
            # draft that was never sent has no outcome to look up, and re-adopting a
            # message for one already `inviato` is what would make this method
            # non-idempotent.
            return read_draft(self.session, draft)

        attempted = draft.send_attempted_at or datetime.now(UTC)
        inside_grace = datetime.now(UTC) - attempted < timedelta(
            minutes=self.settings.gmail_send_grace_minutes
        )
        if draft.send_state == "in_invio" and inside_grace:
            # Still plausibly in flight on another connection: the transport gives up
            # after four attempts and a 30-second timeout apiece, which is minutes inside
            # a fifteen-minute window. Touching it here would race a send that is about
            # to record its own outcome. Past the window it is an abandoned claim -- a
            # process that died between the claim and the record -- and that is an
            # unknown outcome like any other, so it falls through.
            return read_draft(self.session, draft)

        account = self.accounts.usable(
            # `gmail.readonly`, not `gmail.send`: the lookup is `users.messages.list`,
            # which `gmail.send` does not authorise. Naming the wrong scope here would
            # produce a 403 from Google where a legible «manca l'autorizzazione» belongs.
            actor,
            scope=SCOPE_READONLY,
            feature="la verifica dell'invio",
        )
        if draft.google_account_id is not None and draft.google_account_id != account.id:
            # Somebody else's send. Refused rather than attempted, because attempting it
            # is the defect: this mailbox cannot contain that message, every lookup below
            # would come back empty, and past the grace window the code beneath would
            # write `fallito` on an email already delivered from another mailbox. The
            # sentence names the mailbox, not the correspondence.
            raise Conflict(
                ENTITY,
                "questa email è partita da un'altra casella: solo chi l'ha inviata può "
                "verificarne l'esito",
            )
        found = self._find_sent(account, draft)
        if found is not None:
            gmail_id, thread_id = found
            return self._record_sent(
                account,
                draft_id,
                gmail_message_id=gmail_id,
                gmail_thread_id=thread_id,
                actor=actor,
            )

        if inside_grace:
            # "We do not know yet" is a true answer. A resend is not.
            return read_draft(self.session, draft)

        draft.send_state = "fallito"
        draft.last_error = _NOT_SENT
        self.session.commit()
        return read_draft(self.session, draft)

    def reconcile_all(self, account_id: UUID, actor: Actor) -> int:
        """Every draft **of this mailbox** whose outcome is unknown, and how many of them
        this run settled.

        Runs at the start of each sync cycle, so an unresolved outcome does not wait for
        somebody to remember it -- a state that only resolves when a human presses a
        button is a state that stays wrong.

        The `account_id` is the cycle's own, not the actor's, and passing it is what keeps
        one user's cycle out of another user's unresolved sends: see
        `GmailRepository.uncertain_draft_ids`.
        """
        resolved = 0
        for draft_id in self.repo.uncertain_draft_ids(account_id):
            before = self.session.get(EmailDraft, draft_id)
            state_before = before.send_state if before is not None else ""
            self.reconcile(draft_id, actor)
            after = self.session.get(EmailDraft, draft_id)
            if after is not None and after.send_state != state_before:
                resolved += 1
        return resolved

    def _find_sent(self, account: GoogleAccount, draft: EmailDraft) -> tuple[str, str] | None:
        """`(gmail_message_id, gmail_thread_id)` of the message that left, or `None`."""
        already = self.repo.outbound_by_header(draft.message_id_header)
        if already is not None:
            return already.gmail_message_id, already.gmail_thread_id
        if self.settings.gmail_reconcile_by_message_id:
            exact = self._find_by_message_id(account, draft)
            if exact is not None:
                return exact
        return self._find_by_approximation(account, draft)

    def _find_by_message_id(
        self, account: GoogleAccount, draft: EmailDraft
    ) -> tuple[str, str] | None:
        payload = self.transport.json(
            "GET",
            messages_list_url(rfc822msgid_query(draft.message_id_header)),
            token=self._token(account),
            what=_WHAT_VERIFY,
        )
        entries = [entry for entry in (payload.get("messages") or []) if isinstance(entry, dict)]
        if not entries:
            return None
        first = entries[0]
        return str(first.get("id") or ""), str(first.get("threadId") or "")

    def _find_by_approximation(
        self, account: GoogleAccount, draft: EmailDraft
    ) -> tuple[str, str] | None:
        """The declaredly inferior match of spec 6.3: same recipient, same subject,
        inside the grace window.

        It reads the CRM and not Gmail, because the per-address sweep of spec 4 is what
        puts this account's own outgoing mail into `gmail_messages` -- so this costs no
        quota and cannot itself be a broad search. The cost is latency: it can only find
        a message a cycle has already stored, which is why it is the third lookup and not
        the first.

        A candidate some *other* draft already records as its own send is skipped. That
        is what keeps «two near-identical messages minutes apart are indistinguishable»
        from being «and the second one steals the first one's id».
        """
        if draft.send_attempted_at is None:
            return None
        since = draft.send_attempted_at - timedelta(minutes=self.settings.gmail_send_grace_minutes)
        candidates = self.repo.outbound_candidates(account.id, draft.subject, since=since)
        if not candidates:
            return None
        claimed = self.repo.claimed_send_ids([row.gmail_message_id for row in candidates])
        wanted = {address.strip().lower() for address in draft.to_addresses}
        for row in candidates:
            if row.gmail_message_id in claimed:
                continue
            if not wanted & {address.strip().lower() for address in row.to_addresses}:
                continue
            return row.gmail_message_id, row.gmail_thread_id
        return None

    # ---- internals ---------------------------------------------------------------

    def _state_now(self, draft_id: UUID) -> str:
        """The committed `send_state`, read past the identity map.

        A Core `SELECT` of the one column rather than `Session.get`, which would answer
        from the ORM copy this session loaded *before* the winner committed -- the
        difference between telling the loser of the race what actually happened and
        telling it what it already believed.
        """
        state = self.session.execute(
            select(EmailDraft.send_state).where(EmailDraft.id == draft_id)
        ).scalar_one_or_none()
        # `None` only if the draft was deleted between the read and the claim, which
        # `EmailDraftService.delete` refuses for anything not editable. Named rather than
        # crashed on: a race this narrow deserves a sentence, not an AttributeError.
        return state or "sconosciuto"

    def _token(self, account: GoogleAccount) -> str:
        return self.tokens.access_token(
            account_id=account.id,
            email_address=account.email_address,
            refresh_token=unseal(
                account.refresh_token_ciphertext,
                account.refresh_token_nonce,
                decode_google_token_key(self.settings),
            ),
        )

    def _compose(self, account: GoogleAccount, draft: EmailDraft) -> str:
        attachments = resolve_attachments(
            self.session,
            self.storage,
            [UUID(str(value)) for value in draft.attachment_version_ids],
            max_bytes=self.settings.gmail_attachment_max_bytes,
        )
        in_reply_to, references = self._thread_headers(draft)
        return to_base64url(
            build_rfc822(
                from_address=account.email_address,
                from_name=self._signature_name(),
                to=list(draft.to_addresses),
                cc=list(draft.cc_addresses),
                subject=draft.subject,
                body_text=draft.body_markdown,
                message_id=draft.message_id_header,
                in_reply_to=in_reply_to,
                references=references,
                attachments=attachments,
            )
        )

    def _thread_headers(self, draft: EmailDraft) -> tuple[str, str]:
        """`In-Reply-To` and `References` for a reply or a chase inside a thread.

        `References` is appended to, never replaced: it is the whole chain, and a
        reminder that drops it arrives detached from the invoice it is about -- so the
        first thing the recipient does is ask for that invoice again (spec 6.2 rule 2).
        """
        if draft.in_reply_to_message_id is None:
            return "", ""
        parent = self.session.get(GmailMessage, draft.in_reply_to_message_id)
        if parent is None or not parent.message_id_header:
            return "", ""
        chain = " ".join(part for part in (parent.references, parent.message_id_header) if part)
        return parent.message_id_header, chain

    def _signature_name(self) -> str:
        """The display name on the `From` header, from `emitter_profile.ragione_sociale`.

        Never hardcoded, and never defaulted to something plausible: slice 2's whole
        point about `header.typ` was that a CRM for Italian freelancers cannot carry one
        freelancer's name in its source. An installation that has not filled the issuer
        in sends from a bare address, which is honest.
        """
        profile = self.repo.emitter_profile()
        return profile.ragione_sociale if profile else ""

    def _finish(
        self, draft_id: UUID, state: str, *, error: str, actor: Actor, kind: str | None = None
    ) -> None:
        """Records the outcome of an attempt, in a transaction of its own so that it
        survives the exception raised immediately after it.

        Deliberately not preceded by a rollback: see the module docstring. Everything
        since the claim's commit was a read, so there is nothing to discard, and the
        statement that would discard it is the one that can un-record a send.
        """
        row = self.session.get(EmailDraft, draft_id)
        if row is None:
            return
        row.send_state = state
        row.last_error = error
        if kind is not None:
            # Last before the commit, per `ActivityService.record`. The payload carries
            # the subject and our own `Message-ID` -- the two things a person needs to
            # find the message in their Sent folder -- and no recipient and no body.
            self.activities.record(
                row.entity_type,
                row.entity_id,
                kind,
                actor,
                {"subject": row.subject, "message_id_header": row.message_id_header},
            )
        self.session.commit()

    def _record_sent(
        self,
        account: GoogleAccount,
        draft_id: UUID,
        *,
        gmail_message_id: str,
        gmail_thread_id: str,
        actor: Actor,
    ) -> EmailDraftRead:
        """The message left. One transaction: the draft's outcome, the outbound row, its
        links and the timeline entry (spec 6.2 -- «nella stessa transazione»)."""
        row = self.session.get(EmailDraft, draft_id)
        if row is None:
            # Unreachable while `EmailDraftService.delete` refuses anything that is not
            # editable, which a claimed draft is not. Named rather than left to an
            # `AttributeError`, because the shape of it -- the mail has left and there is
            # no row to record it against -- is exactly the previous system's «404 per registrare
            # l'invio», and a future edit that made deletion wider must land here loudly.
            raise NotFound(ENTITY, draft_id)
        row.send_state = "inviato"
        row.sent_gmail_message_id = gmail_message_id
        # The stale sentence goes with the state: an error next to a message that has now
        # left describes a failure that is no longer true.
        row.last_error = None

        message = self._outbound_row(account, row, gmail_message_id, gmail_thread_id)
        for ref in self._refs_for(row):
            self.repo.add_link(message.id, ref)

        # If this draft was a payment reminder, this is the moment it stops being merely
        # *prepared*. In the same transaction as the draft's own outcome, because the two
        # facts are one event: a `sent_at` that could survive a rolled-back send would let
        # the ceiling and the interval count a letter nobody received, and a send recorded
        # without it would let them offer a second one an hour later. Looked up by the
        # draft, so an ordinary email to a client who happens to owe money stamps nothing.
        reminder = self.repo.reminder_for_draft(draft_id)
        if reminder is not None:
            reminder.sent_at = datetime.now(UTC)

        # Last before the commit, as `ActivityService.record` requires: nothing that
        # commits on its own behalf may run after it.
        self.activities.record(
            row.entity_type,
            row.entity_id,
            "gmail.messaggio_inviato",
            actor,
            {
                "subject": row.subject,
                "to_addresses": list(row.to_addresses),
                "gmail_message_id": gmail_message_id,
            },
        )
        self.session.commit()
        return read_draft(self.session, row)

    def _outbound_row(
        self, account: GoogleAccount, draft: EmailDraft, gmail_id: str, thread_id: str
    ) -> GmailMessage:
        """The `gmail_messages` row for what just left, written only now -- after Gmail
        answered, never before (spec 6.2).

        It may already exist: an ordinary sync cycle reads this account's own Sent mail
        through the address filter, and the reconciliation adopts a message the sync may
        already have stored. So the existing row is reused rather than duplicated, and
        the insert goes through `add_message_if_absent` so that the unique constraint --
        not a preceding `SELECT` two writers both pass -- is what arbitrates.
        """
        existing = self.repo.message_by_gmail_id(account.id, gmail_id)
        if existing is not None:
            return existing
        in_reply_to, references = self._thread_headers(draft)
        message = GmailMessage(
            google_account_id=account.id,
            gmail_message_id=gmail_id,
            gmail_thread_id=thread_id,
            message_id_header=draft.message_id_header[:998],
            in_reply_to=in_reply_to[:998],
            references=references,
            direction="outbound",
            from_address=account.email_address,
            to_addresses=list(draft.to_addresses),
            cc_addresses=list(draft.cc_addresses),
            subject=draft.subject[:998],
            snippet=draft.body_markdown[:500],
            internal_date=datetime.now(UTC),
            # The same switch that governs inbound mail. A user who turned the body store
            # off did so about their correspondence, and their own half of it is still
            # their correspondence.
            body_text=draft.body_markdown if account.gmail_store_bodies else "",
        )
        if self.repo.add_message_if_absent(message):
            return message
        stored = self.repo.message_by_gmail_id(account.id, gmail_id)
        if stored is None:  # pragma: no cover - the constraint refused it for no reason
            raise Conflict("gmail", "impossibile registrare il messaggio inviato")
        return stored

    def _refs_for(self, draft: EmailDraft) -> list[EntityRef]:
        """Where the sent message is filed: the entity the draft was written against,
        plus every entity its recipients resolve to.

        The same rule as an inbound message, in the other direction. A reply visible on
        the person but not on the customer they belong to -- or the reverse -- is the
        conversation that lies, which is what `_thread_refs` exists to prevent on the way
        in.
        """
        refs = [EntityRef(draft.entity_type, draft.entity_id)]
        for address in [*draft.to_addresses, *draft.cc_addresses]:
            for ref in self.roster.resolve(address):
                if ref not in refs:
                    refs.append(ref)
        return refs


def _already(state: str) -> str:
    return _ALREADY.get(state, f"questa email non è inviabile nello stato {state}")


class _NoDocuments:
    """The document backend the reconciliation path must never reach.

    Not a stand-in for storage: it is an assertion, the same shape as `UnusedStorage` in
    the test fixtures. `reconcile` and `reconcile_all` look a message up and adopt an id;
    they compose nothing, so they read no document -- and a storage whose `get` refuses is
    how that stays true rather than merely being true today.
    """

    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise Conflict("gmail", "la verifica dell'invio non scrive documenti")

    def get(self, key: str) -> bytes:
        raise Conflict("gmail", "la verifica dell'invio non legge documenti")

    def delete(self, key: str) -> None:
        raise Conflict("gmail", "la verifica dell'invio non cancella documenti")

    def signed_url(self, key: str, ttl: timedelta) -> str | None:
        return None


def reconcile_only(
    session: Session,
    *,
    settings: Settings,
    transport: GmailTransport,
    tokens: GoogleTokenClient,
) -> EmailSendService:
    """An `EmailSendService` for `reconcile_all` and nothing else.

    It exists so that `GmailSyncService` -- which has no `DocumentStorage` and no reason
    to acquire one -- can run the reconciliation at the start of every cycle without
    widening its constructor across all three of its call sites. A module-level function
    rather than a classmethod because a public method on a `*Service` class is a
    surface decision `test_mcp_surface_coverage.py` would (rightly) demand an entry for,
    and this is plumbing, not an operation anybody performs.
    """
    return EmailSendService(
        session, settings=settings, transport=transport, tokens=tokens, storage=_NoDocuments()
    )
