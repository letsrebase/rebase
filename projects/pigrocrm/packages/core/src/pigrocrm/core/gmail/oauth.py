"""Authorization-code flow with PKCE, server-side.

Two mechanisms carry the whole security of this flow, and both are here rather than in
a convention somebody has to remember.

**PKCE.** The `code_verifier` lives in `google_oauth_states`, never in anything the
browser can read. Putting it in the `state` -- signed or not -- would cancel PKCE
entirely, because the `state` travels through the user agent and through Google's
redirect. What goes out is only the S256 challenge; what comes back is only the jti.

**The single-use state.** The `state` parameter *is* the jti: a fresh 192 bits of
`os.urandom`, registered in `google_oauth_states` against the CRM user who started the
flow, with a five-minute TTL. There is no JWT and nothing signed, because there is
nothing to sign: the value is unguessable, it is looked up server-side, and the row is
the authority on whether it has already been used. A signature would only let a
*forged* state be recognised as well-formed before the same lookup rejected it.

It is redeemed by one conditional `UPDATE ... WHERE consumed_at IS NULL` and that
redemption is **committed before anything that can fail** -- before the token exchange,
before the mailbox comparison, before the write. A refusal therefore never hands the
state back for a second attempt. The obvious-looking alternative (roll the consumption
back when the callback turns out to be invalid) reopens exactly the replay window the
row exists to close, and leaves a live state behind after every transient error.

What a refusal is allowed to say is a third decision, made here on purpose. Everything
that fails *before* the state has been redeemed against the caller's own user id
answers with one sentence naming nothing at all: unknown jti, expired, replayed and
belonging to another session are indistinguishable from outside, or the callback
becomes an oracle for "which mailbox is connected here". Only once the caller has
proved ownership by redeeming their own state does the mailbox-mismatch refusal name
the two addresses -- and by then it is naming an address that caller can already read
on their own settings page, while the alternative ("that is not the right mailbox",
without saying which is) is an instruction nobody can act on.

**A space borrowing the root's client** (REB-394, `tenants/google.py`) changes two
strings and nothing else: the redirect URI is the root's callback, and the state is the
jti with the space's prefix in front, so the root can relay the browser to the right
space. `complete` strips that prefix and refuses a state that lacks it before anything
is looked up, so the redemption below is the same single-use row either way.
"""

import base64
import hashlib
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, decode_google_token_key, require_gmail_configured
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.crypto import seal
from pigrocrm.core.gmail.models import GoogleAccount, GoogleOAuthState
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES, GoogleAccountRead
from pigrocrm.core.gmail.tokens import GOOGLE_AUTH_URL, GoogleTokenClient, TokenGrant

STATE_TTL_MINUTES = 5
# Testing-mode consumer refresh tokens expire seven days after consent. Google exposes
# no API to detect verification status, so the operator declares it.
UNVERIFIED_CONSENT_DAYS = 7
_VERIFIER_BYTES = 48  # 64 base64url characters, inside PKCE's 43-128 range
# 32 base64url characters. The `state` is the only thing standing between a forged
# callback and a redeemable authorisation, so it is sized as a secret and not as an
# identifier.
_JTI_BYTES = 24

_CONNECT_ACTION = "collegare un account Google"
_DISCONNECT_ACTION = "scollegare un account Google"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def callback_url(settings: Settings, path: str) -> str:
    """The redirect URI Google compares character by character: this installation's
    own address, or the root's while a space borrows the root's client (REB-394).
    Shared with `drive/oauth.py`, whose consent comes back the same way."""
    base = settings.google_callback_base_url or settings.public_url
    return f"{base.rstrip('/')}{path}"


def published_state(settings: Settings, jti: str) -> str:
    """The `state` sent to Google: the jti, behind the space's prefix when the root
    relays this consent (`google_oauth_state_prefix`, empty everywhere else)."""
    return f"{settings.google_oauth_state_prefix}{jti}"


def jti_of(settings: Settings, state: str) -> str | None:
    """The jti a returning `state` carries, or None when it does not carry this
    installation's prefix. None is refused with the same sentence as an unknown jti:
    a state minted for another space is exactly that, here."""
    prefix = settings.google_oauth_state_prefix
    if not state.startswith(prefix):
        return None
    return state[len(prefix) :] or None


class GmailOAuthService:
    def __init__(self, session: Session, *, settings: Settings, tokens: GoogleTokenClient) -> None:
        self.session = session
        self.settings = settings
        self.tokens = tokens
        self.repo = GmailRepository(session)
        # Read-only here: only the cross-identity check in `complete` uses it, to read
        # the Drive row of the same CRM user. See that check for why.
        self.drive = DriveRepository(session)
        self.activities = ActivityService(session)

    @property
    def redirect_uri(self) -> str:
        # One fixed, configured string. Google compares it character for character.
        return callback_url(self.settings, "/api/gmail/oauth/callback")

    def start(self, actor: Actor) -> str:
        require_gmail_configured(self.settings)
        actor.require_write(_CONNECT_ACTION)
        if actor.id is None:
            raise Conflict("google_account", "solo un utente può collegare una casella Google")

        verifier = _b64url(os.urandom(_VERIFIER_BYTES))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        jti = _b64url(os.urandom(_JTI_BYTES))
        now = datetime.now(UTC)
        self.repo.add_state(
            GoogleOAuthState(
                jti=jti,
                code_verifier=verifier,
                user_id=actor.id,
                expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
            )
        )
        # Committed here and not at the end of the request: the user is about to leave
        # for Google, and a state that only exists in an open transaction would be
        # unredeemable by the callback that follows.
        self.session.commit()

        return f"{GOOGLE_AUTH_URL}?" + urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": " ".join(REQUESTED_SCOPES),
                "access_type": "offline",
                # Without this, a repeat authorisation returns no refresh token.
                "prompt": "consent",
                # The granted set must describe this consent alone. Carrying previously
                # granted scopes forward would make `scopes_granted` a record of
                # everything ever agreed to rather than of what this credential holds.
                "include_granted_scopes": "false",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": published_state(self.settings, jti),
            }
        )

    def complete(self, *, code: str, state: str, actor: Actor) -> GoogleAccountRead:
        require_gmail_configured(self.settings)
        actor.require_write(_CONNECT_ACTION)
        now = datetime.now(UTC)
        if actor.id is None:
            # A state is bound to a `users.id`. An actor without one has nothing it
            # could ever have been issued, so it takes the same silent refusal.
            raise self._invalid_authorisation()

        jti = jti_of(self.settings, state)
        row = self.repo.consume_state(jti, now) if jti is not None else None
        if row is None:
            raise self._invalid_authorisation()
        # The redemption stands whatever happens next. See the module docstring.
        self.session.commit()
        if row.user_id != actor.id:
            raise self._invalid_authorisation()

        grant = self.tokens.exchange_code(
            code=code, code_verifier=row.code_verifier, redirect_uri=self.redirect_uri
        )
        if not grant.subject or not grant.email_address:
            # `openid` and `email` are requested precisely so the mailbox can be named.
            # Storing a row without an identity would leave the reconnection check
            # below with nothing to compare, which is the one check that stops a whole
            # stored history being silently relabelled.
            raise Conflict(
                "google_account",
                "Google non ha restituito l'identità della casella: riprova, e se "
                "l'errore persiste ricomincia da Impostazioni → Gmail",
            )

        existing = self.repo.account_for_user(actor.id)
        # The comparison is skipped once the account has been explicitly disconnected:
        # the refusal below tells the user to disconnect first, so honouring it after
        # they have would make the instruction a dead end and turn the row into a
        # permanent lock on an address they no longer own.
        if (
            existing is not None
            and existing.disconnected_at is None
            and not self._same_mailbox(existing, grant)
        ):
            raise Conflict(
                "google_account",
                f"questa installazione è collegata a {existing.email_address}, non a "
                f"{grant.email_address}: scollega prima l'account attuale",
                connected=existing.email_address,
                offered=grant.email_address,
            )

        # The mirror of `GoogleDriveOAuthService.complete`'s own cross-identity check
        # (spec 9 §5.2): two independent grants naming two different Google identities
        # would leave this *user's* mailbox and their Drive belonging to two different
        # people, with nothing short of comparing the rows by hand to notice. Per CRM
        # user, not per installation: both tables are keyed by `user_id`, and it is
        # `account_for_user(actor.id)` that is read here.
        # Skipped once the Drive credential has been explicitly disconnected, for the
        # same reason the mailbox comparison above is.
        drive_account = self.drive.account_for_user(actor.id)
        if (
            drive_account is not None
            and drive_account.disconnected_at is None
            and drive_account.google_sub != grant.subject
        ):
            raise Conflict(
                "google_account",
                "questa casella appartiene a un account Google diverso dal Drive "
                f"collegato ({drive_account.email_address}): usa lo stesso account",
            )

        account = self._store(existing, grant, actor.id, now)
        # The cached access token belongs to a grant that no longer applies.
        self.tokens.forget(account.id)
        # Last thing before the commit: ActivityService.record flushes and joins this
        # transaction, so nothing may commit after it on this session.
        self.activities.record(
            "google_account",
            account.id,
            "gmail.account_collegato",
            actor,
            {"email_address": account.email_address, "scopes_granted": list(grant.scopes)},
        )
        self.session.commit()
        return GoogleAccountRead.model_validate(account)

    def disconnect(self, *, delete_messages: bool, actor: Actor) -> None:
        """Offers to delete the stored messages; never does it on its own. Deleting a
        customer's correspondence because a token expired would be a disaster, so the
        choice is recorded in the timeline."""
        actor.require_write(_DISCONNECT_ACTION)
        if actor.id is None:
            raise Conflict("google_account", "solo un utente può scollegare una casella Google")
        account = self.repo.account_for_user(actor.id)
        if account is None:
            raise Conflict("google_account", "nessuna casella Google collegata")

        deleted = self.repo.delete_messages_for(account.id) if delete_messages else 0

        # `disconnected`, not `revoked`. The two used to share one value, told apart
        # only by `disconnected_at` -- which meant every banner and every gate reading
        # `status` told a person who had just pressed «scollega» that Google had revoked
        # their consent, and offered to reconnect what they had deliberately unhooked.
        account.status = "disconnected"
        account.disconnected_at = datetime.now(UTC)
        # Overwritten, not merely dereferenced: leaving the ciphertext behind means the
        # credential is still in every backup taken after the disconnect.
        account.refresh_token_ciphertext = b""
        account.refresh_token_nonce = b""
        self.tokens.forget(account.id)
        self.activities.record(
            "google_account",
            account.id,
            "gmail.account_scollegato",
            actor,
            {
                "email_address": account.email_address,
                "messaggi_cancellati": delete_messages,
                # The count, not the messages: the timeline has to be able to say how
                # much correspondence this irreversible choice destroyed, and it is the
                # one place that answer survives the deletion.
                "messaggi_cancellati_conteggio": deleted,
            },
        )
        self.session.commit()

    # --- internals -------------------------------------------------------------------

    @staticmethod
    def _invalid_authorisation() -> Conflict:
        """One sentence for every pre-ownership failure, carrying no details at all.

        `details` is empty on purpose: the API renders it into the problem document and
        the MCP adapter into guidance, so anything put here is published.
        """
        return Conflict(
            "google_account",
            "questa autorizzazione non è più valida: ricomincia da Impostazioni → Gmail",
        )

    @staticmethod
    def _same_mailbox(existing: GoogleAccount, grant: TokenGrant) -> bool:
        """Both halves of the identity, not just the stable one.

        `google_sub` is what survives a Google account being renamed, and it is the
        reason `openid` is requested at all. But everything downstream of this row is
        matched by *address* -- the roster, the stored messages, the send path -- so an
        address that moved under an unchanged `sub` would relabel exactly the same
        history the `sub` check exists to protect. Both must match, and the cure for
        either is the same explicit, audited disconnect.
        """
        return existing.google_sub == grant.subject and (
            existing.email_address.casefold() == grant.email_address.casefold()
        )

    def _store(
        self, existing: GoogleAccount | None, grant: TokenGrant, user_id: UUID, now: datetime
    ) -> GoogleAccount:
        ciphertext, nonce = seal(grant.refresh_token, decode_google_token_key(self.settings))
        consent_expires_at = (
            now + timedelta(days=UNVERIFIED_CONSENT_DAYS)
            if self.settings.google_app_unverified
            else None
        )
        account = existing or GoogleAccount(user_id=user_id)
        if existing is None:
            self.session.add(account)
        elif existing.disconnected_at is not None:
            # A genuine re-connection, possibly of a different mailbox. `connected_at`
            # describes this consent, not the row's first ever one.
            account.connected_at = now
        account.google_sub = grant.subject
        account.email_address = grant.email_address
        account.refresh_token_ciphertext = ciphertext
        account.refresh_token_nonce = nonce
        account.scopes_granted = list(grant.scopes)
        account.status = "active"
        account.consent_expires_at = consent_expires_at
        # A fresh grant answers whatever the last one failed at; leaving the old error
        # visible would show the user a warning about a credential that no longer
        # exists.
        account.last_error = None
        account.last_error_at = None
        account.disconnected_at = None
        self.session.flush()
        return account
