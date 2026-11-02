"""Connecting and disconnecting the mailbox: Gmail's half of the Google consent flow.

The flow itself -- PKCE, the single-use state, the identity checks, the sealed refresh
token -- is `google/oauth_flow.py`, shared with Drive; read its docstring for why each of
those is the way it is. What is here is what is Gmail's: the scopes, the sentences, the
mailbox row, the Drive credential it must agree with, and what a mailbox that changed
identity must forget.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import REQUESTED_SCOPES, GoogleAccountRead
from pigrocrm.core.gmail.tokens import GoogleTokenClient, TokenGrant
from pigrocrm.core.google.oauth_flow import GoogleConsentFlow, GoogleCredential

_DISCONNECT_ACTION = "scollegare un account Google"


class GmailOAuthService(GoogleConsentFlow[GoogleAccount]):
    purpose = "gmail"
    entity = "google_account"
    scopes = REQUESTED_SCOPES
    callback_path = "/api/gmail/oauth/callback"
    connect_action = "collegare un account Google"
    settings_tab = "Impostazioni → Gmail"
    identity_noun = "della casella"
    needs_user_message = "solo un utente può collegare una casella Google"
    connected_event = "gmail.account_collegato"

    def __init__(self, session: Session, *, settings: Settings, tokens: GoogleTokenClient) -> None:
        super().__init__(session, settings=settings, tokens=tokens)
        self.repo = GmailRepository(session)
        # Read-only here: only the cross-identity check in `connect` uses it, to read the
        # Drive row of the same CRM user.
        self.drive = DriveRepository(session)

    def complete(self, *, code: str, state: str, actor: Actor) -> GoogleAccountRead:
        return GoogleAccountRead.model_validate(self.connect(code=code, state=state, actor=actor))

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

        self._blank(account)
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

    # --- hooks -----------------------------------------------------------------------

    def account_for_user(self, user_id: UUID) -> GoogleAccount | None:
        return self.repo.account_for_user(user_id)

    def sibling_account(self, user_id: UUID) -> GoogleCredential | None:
        return self.drive.account_for_user(user_id)

    def new_account(self, user_id: UUID) -> GoogleAccount:
        return GoogleAccount(user_id=user_id)

    def already_connected(self, existing: GoogleAccount, grant: TokenGrant) -> Conflict:
        return Conflict(
            self.entity,
            f"questa installazione è collegata a {existing.email_address}, non a "
            f"{grant.email_address}: scollega prima l'account attuale",
            connected=existing.email_address,
            offered=grant.email_address,
        )

    def sibling_mismatch(self, sibling: GoogleCredential) -> Conflict:
        return Conflict(
            self.entity,
            "questa casella appartiene a un account Google diverso dal Drive "
            f"collegato ({sibling.email_address}): usa lo stesso account",
        )

    def on_identity_changed(self, account: GoogleAccount) -> None:
        """The sync window and the register of searched addresses are the previous
        mailbox's. Kept, they would make the new mailbox's first cycle start where the
        other one stopped, and its backfill skip every address that mailbox had already
        been searched for: the history of the new mailbox would silently never arrive.
        Both are cleared, so the new mailbox starts like a first connection. The stored
        messages stay: the user chose to keep them when disconnecting."""
        account.sync_watermark = None
        account.last_sync_at = None
        self.repo.delete_known_addresses(account.id)
