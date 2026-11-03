"""Authorization-code flow with PKCE, server-side: the part Gmail and Drive share.

A person connects a mailbox (`gmail/oauth.py`) and, separately, a Drive (`drive/oauth.py`)
through the same Google OAuth client, so the two consents are the same flow with different
scopes, rows and sentences. This module is that flow once; each product subclasses it and
supplies only what differs (`GoogleConsentFlow`'s class attributes and hooks). Slack's
install (`slack/oauth.py`) is a third consent with a provider of its own, and shares the
state registry (`states.py`) and the two state-prefix helpers below.

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
before the identity comparison, before the write. A refusal therefore never hands the
state back for a second attempt. The obvious-looking alternative (roll the consumption
back when the callback turns out to be invalid) reopens exactly the replay window the
row exists to close, and leaves a live state behind after every transient error.

What a refusal is allowed to say is a third decision, made here on purpose. Everything
that fails *before* the state has been redeemed against the caller's own user id
answers with one sentence naming nothing at all: unknown jti, expired, replayed and
belonging to another session are indistinguishable from outside, or the callback
becomes an oracle for "which account is connected here". Only once the caller has
proved ownership by redeeming their own state does the identity-mismatch refusal name
the two addresses -- and by then it is naming an address that caller can already read
on their own settings page, while the alternative ("that is not the right account",
without saying which is) is an instruction nobody can act on.

**The cross-identity check.** Two independent grants can point at two different Google
identities if someone picks another account in Google's chooser -- deliberately or by
mistake. That would leave one CRM *user* whose mailbox and whose Drive belong to two
different people, with no way to notice short of comparing the rows by hand. The
invariant is per CRM user and not per installation: both tables are keyed by `user_id`,
and two users of the same installation are meant to hold two different Google
identities. So `connect` reads the *sibling* credential of the same user and refuses
when a connected row there names a different `sub`; only `sub` is compared, since
nothing in the sibling product is filed against the other one's address.

**A space borrowing the root's client** (REB-394, `tenants/google.py`) changes two
strings and nothing else: the redirect URI is the root's callback, and the state is the
jti with the space's prefix in front, so the root can relay the browser to the right
space. `connect` strips that prefix and refuses a state that lacks it before anything is
looked up, so the redemption is the same single-use row either way.
"""

import hashlib
import os
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, decode_google_token_key, require_gmail_configured
from pigrocrm.core.crypto import seal
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.tokens import GOOGLE_AUTH_URL, GoogleTokenClient, TokenGrant
from pigrocrm.core.google.states import OAuthStateRepository, b64url

# Testing-mode consumer refresh tokens expire seven days after consent. Google exposes
# no API to detect verification status, so the operator declares it.
UNVERIFIED_CONSENT_DAYS = 7
_VERIFIER_BYTES = 48  # 64 base64url characters, inside PKCE's 43-128 range


def callback_url(settings: Settings, path: str) -> str:
    """The redirect URI Google compares character by character: this installation's
    own address, or the root's while a space borrows the root's client (REB-394)."""
    base = settings.google_callback_base_url or settings.public_url
    return f"{base.rstrip('/')}{path}"


def published_state(prefix: str, jti: str) -> str:
    """The `state` sent to the provider: the jti, behind the space's prefix when the root
    relays this consent (`*_oauth_state_prefix`, empty everywhere else)."""
    return f"{prefix}{jti}"


def jti_of(prefix: str, state: str) -> str | None:
    """The jti a returning `state` carries, or None when it does not carry this
    installation's prefix. None is refused with the same sentence as an unknown jti:
    a state minted for another space is exactly that, here."""
    if not state.startswith(prefix):
        return None
    return state[len(prefix) :] or None


type GoogleCredential = GoogleAccount | GoogleDriveAccount
"""The two rows this flow connects. They share the columns it reads and writes
(`google_sub`, `email_address`, the sealed refresh token, the status and the dates), and a
union of the two says exactly that where a structural type could only approximate it."""


def same_identity(existing: GoogleCredential, grant: TokenGrant) -> bool:
    """Both halves of the identity, not just the stable one.

    `google_sub` is what survives a Google account being renamed, and it is the reason
    `openid` is requested at all. But everything filed against a credential is matched by
    *address* -- the roster, the stored messages and the send path for a mailbox, the
    chosen folders for a Drive -- so an address that moved under an unchanged `sub`
    would relabel exactly the same history the `sub` check exists to protect. Both must
    match, and the cure for either is the same explicit, audited disconnect.
    """
    return existing.google_sub == grant.subject and (
        existing.email_address.casefold() == grant.email_address.casefold()
    )


class GoogleConsentFlow[AccountT: GoogleCredential](ABC):
    """Connect and reconnect one Google credential. Subclasses fill in the class
    attributes and the hooks at the bottom; `disconnect` stays theirs, since what leaves
    with a credential differs, and they call `_blank` for the part that does not."""

    # What differs per product: the row `purpose` tags in `google_oauth_states`, the
    # entity its refusals and timeline entries are filed under, the scopes asked for and
    # the callback path, and the sentences.
    purpose: ClassVar[str]
    entity: ClassVar[str]
    scopes: ClassVar[tuple[str, ...]]
    callback_path: ClassVar[str]
    connect_action: ClassVar[str]
    # Where a person is sent to start over, as the settings page names it.
    settings_tab: ClassVar[str]
    # "della casella" / "dell'account": what Google did not identify.
    identity_noun: ClassVar[str]
    needs_user_message: ClassVar[str]
    connected_event: ClassVar[str]

    def __init__(self, session: Session, *, settings: Settings, tokens: GoogleTokenClient) -> None:
        self.session = session
        self.settings = settings
        self.tokens = tokens
        self.states = OAuthStateRepository(session)
        self.activities = ActivityService(session)

    @property
    def redirect_uri(self) -> str:
        # One fixed, configured string. Google compares it character for character.
        return callback_url(self.settings, self.callback_path)

    def start(self, actor: Actor) -> str:
        require_gmail_configured(self.settings)
        actor.require_write(self.connect_action)
        if actor.id is None:
            raise Conflict(self.entity, self.needs_user_message)

        verifier = b64url(os.urandom(_VERIFIER_BYTES))
        challenge = b64url(hashlib.sha256(verifier.encode()).digest())
        jti = self.states.issue(
            user_id=actor.id, purpose=self.purpose, code_verifier=verifier, now=datetime.now(UTC)
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
                "scope": " ".join(self.scopes),
                "access_type": "offline",
                # Without this, a repeat authorisation returns no refresh token.
                "prompt": "consent",
                # The granted set must describe this consent alone. Carrying previously
                # granted scopes forward would make `scopes_granted` a record of
                # everything ever agreed to rather than of what this credential holds.
                "include_granted_scopes": "false",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": published_state(self.settings.google_oauth_state_prefix, jti),
            }
        )

    def connect(self, *, code: str, state: str, actor: Actor) -> AccountT:
        """Redeems the state, exchanges the code, checks the identity and stores the
        credential. The callers wrap the row in their own read schema."""
        require_gmail_configured(self.settings)
        actor.require_write(self.connect_action)
        now = datetime.now(UTC)
        if actor.id is None:
            # A state is bound to a `users.id`. An actor without one has nothing it
            # could ever have been issued, so it takes the same silent refusal.
            raise self._invalid_authorisation()

        jti = jti_of(self.settings.google_oauth_state_prefix, state)
        row = self.states.consume(jti, now, self.purpose) if jti is not None else None
        if row is None:
            raise self._invalid_authorisation()
        # The redemption stands whatever happens next. See the module docstring.
        self.session.commit()
        if row.user_id != actor.id:
            raise self._invalid_authorisation()

        grant = self.exchange_code(code=code, code_verifier=row.code_verifier)
        if not grant.subject or not grant.email_address:
            # `openid` and `email` are requested precisely so the account can be named.
            # Storing a row without an identity would leave the reconnection check
            # below with nothing to compare, which is the one check that stops a whole
            # stored history being silently relabelled.
            raise Conflict(
                self.entity,
                f"Google non ha restituito l'identità {self.identity_noun}: riprova, e se "
                f"l'errore persiste ricomincia da {self.settings_tab}",
            )

        existing = self.account_for_user(actor.id)
        # The comparison is skipped once the account has been explicitly disconnected:
        # the refusal tells the user to disconnect first, so honouring it after they
        # have would make the instruction a dead end and turn the row into a permanent
        # lock on an address they no longer own.
        if (
            existing is not None
            and existing.disconnected_at is None
            and not same_identity(existing, grant)
        ):
            raise self.already_connected(existing, grant)

        # Skipped once the sibling credential has been explicitly disconnected, for the
        # same reason the comparison above is.
        sibling = self.sibling_account(actor.id)
        if (
            sibling is not None
            and sibling.disconnected_at is None
            and sibling.google_sub != grant.subject
        ):
            raise self.sibling_mismatch(sibling)

        account = self._store(existing, grant, actor.id, now)
        # The cached access token belongs to a grant that no longer applies.
        self.tokens.forget(account.id)
        # Last thing before the commit: ActivityService.record flushes and joins this
        # transaction, so nothing may commit after it on this session.
        self.activities.record(
            self.entity,
            account.id,
            self.connected_event,
            actor,
            {"email_address": account.email_address, "scopes_granted": list(grant.scopes)},
        )
        self.session.commit()
        return account

    # --- hooks -----------------------------------------------------------------------

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant:
        return self.tokens.exchange_code(
            code=code, code_verifier=code_verifier, redirect_uri=self.redirect_uri
        )

    @abstractmethod
    def account_for_user(self, user_id: UUID) -> AccountT | None: ...

    @abstractmethod
    def sibling_account(self, user_id: UUID) -> GoogleCredential | None:
        """The same user's credential of the other Google product, if there is a row."""

    @abstractmethod
    def new_account(self, user_id: UUID) -> AccountT: ...

    @abstractmethod
    def already_connected(self, existing: AccountT, grant: TokenGrant) -> Conflict:
        """The refusal for a connected account that names a different identity."""

    @abstractmethod
    def sibling_mismatch(self, sibling: GoogleCredential) -> Conflict:
        """The refusal for a connected sibling credential with a different `sub`."""

    @abstractmethod
    def on_identity_changed(self, account: AccountT) -> None:
        """What belonged to the identity that just left: a reconnection under another
        Google identity must start from a clean slate, exactly like a first connection,
        and not inherit state the previous identity built up."""

    # --- internals -------------------------------------------------------------------

    def _invalid_authorisation(self) -> Conflict:
        """One sentence for every pre-ownership failure, carrying no details at all.

        `details` is empty on purpose: the API renders it into the problem document and
        the MCP adapter into guidance, so anything put here is published.
        """
        return Conflict(
            self.entity,
            f"questa autorizzazione non è più valida: ricomincia da {self.settings_tab}",
        )

    def _store(
        self, existing: AccountT | None, grant: TokenGrant, user_id: UUID, now: datetime
    ) -> AccountT:
        ciphertext, nonce = seal(grant.refresh_token, decode_google_token_key(self.settings))
        # No CRM-imposed expiry: only Google's Testing-mode window.
        consent_expires_at = (
            now + timedelta(days=UNVERIFIED_CONSENT_DAYS)
            if self.settings.google_app_unverified
            else None
        )
        account = existing or self.new_account(user_id)
        if existing is None:
            self.session.add(account)
        elif existing.disconnected_at is not None:
            # A genuine re-connection, possibly of a different identity -- `connect` only
            # refuses that while the row is still *connected*. `connected_at` describes
            # this consent, not the row's first ever one.
            account.connected_at = now
            if not same_identity(existing, grant):
                self.on_identity_changed(account)
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

    def _blank(self, account: AccountT) -> None:
        """The credential half of a disconnect.

        `disconnected`, not `revoked`: the two used to share one value, told apart only
        by `disconnected_at` -- which meant every banner and every gate reading `status`
        told a person who had just pressed «scollega» that Google had revoked their
        consent, and offered to reconnect what they had deliberately unhooked.
        """
        account.status = "disconnected"
        account.disconnected_at = datetime.now(UTC)
        # Overwritten, not merely dereferenced: leaving the ciphertext behind means the
        # credential is still in every backup taken after the disconnect.
        account.refresh_token_ciphertext = b""
        account.refresh_token_nonce = b""
        self.tokens.forget(account.id)
