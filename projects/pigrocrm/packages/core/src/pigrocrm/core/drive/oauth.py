"""Connecting and disconnecting Google Drive: Drive's half of the Google consent flow
(spec 9 §5.1-5.2).

The flow itself -- PKCE, the single-use state, the identity checks, the sealed refresh
token -- is `google/oauth_flow.py`, shared with Gmail; read its docstring for why each of
those is the way it is, and the cross-identity check (spec 9 §5.2) in particular. What is
here is what is Drive's.

**Why a second grant, and not a wider Gmail one.** `drive/models.py`'s docstring
states the reason at length: a person can hold, revoke and reconnect a Drive
consent independently of their mailbox consent, so the CRM asks for a second,
separate authorisation rather than folding `drive.readonly`/`drive.file` into the
scopes Gmail already requests. `google_sub` on the two rows will normally agree --
one person's Google identity -- but they are not the same grant and the two tables
share no foreign key.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.drive.errors import DriveCredentialRevoked, DriveUnavailable
from pigrocrm.core.drive.models import GoogleDriveAccount
from pigrocrm.core.drive.repository import DriveRepository
from pigrocrm.core.drive.schemas import DRIVE_REQUESTED_SCOPES, GoogleDriveAccountRead
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.errors import CredentialRevoked, GmailUnavailable
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.tokens import GoogleTokenClient, TokenGrant
from pigrocrm.core.google.oauth_flow import GoogleConsentFlow, GoogleCredential

_DISCONNECT_ACTION = "disconnect_drive"


class GoogleDriveOAuthService(GoogleConsentFlow[GoogleDriveAccount]):
    purpose = "drive"
    entity = "google_drive_account"
    scopes = DRIVE_REQUESTED_SCOPES
    callback_path = "/api/drive/oauth/callback"
    connect_action = "connect_drive"
    settings_tab = "Impostazioni → Drive"
    identity_noun = "dell'account"
    needs_user_message = "solo un utente può collegare Google Drive"
    connected_event = "drive.account_collegato"

    def __init__(self, session: Session, *, settings: Settings, tokens: GoogleTokenClient) -> None:
        super().__init__(session, settings=settings, tokens=tokens)
        self.repo = DriveRepository(session)
        # Gmail's own repository, for the mailbox row the cross-identity check reads.
        self.gmail = GmailRepository(session)

    def complete(self, *, code: str, state: str, actor: Actor) -> GoogleDriveAccountRead:
        return GoogleDriveAccountRead.model_validate(
            self.connect(code=code, state=state, actor=actor)
        )

    def disconnect(self, actor: Actor) -> None:
        """Mirrors `GmailOAuthService.disconnect`, minus the messages: Drive stores no
        correspondence, so there is nothing here for a `delete_messages` flag to name.
        """
        actor.require_write(_DISCONNECT_ACTION)
        if actor.id is None:
            raise Conflict("google_drive_account", "solo un utente può scollegare Google Drive")
        account = self.repo.account_for_user(actor.id)
        if account is None:
            raise Conflict("google_drive_account", "nessun account Google Drive collegato")

        self._blank(account)
        self.activities.record(
            "google_drive_account",
            account.id,
            "drive.account_scollegato",
            actor,
            {"email_address": account.email_address},
        )
        self.session.commit()

    # --- hooks -----------------------------------------------------------------------

    def exchange_code(self, *, code: str, code_verifier: str) -> TokenGrant:
        """`exchange_code` belongs to the `GoogleTokenClient` the Gmail side owns, and
        its two failure exits name Gmail: `CredentialRevoked` reads "ricollega la
        casella da Impostazioni → Gmail" under the entity `google_account`, and
        `GmailUnavailable` reads "Gmail non ha risposto correttamente". The router
        turns either into `?esito=errore`, so the browser flow is unchanged -- but the
        same `Conflict` is what a direct caller renders as a problem document or an
        MCP message, and there it would send somebody to reconnect a mailbox, or to
        wait for Gmail, over a *Drive* consent. Re-shaped for the same reason
        `UserTokens.access_token` re-shapes the refresh path's own two."""
        try:
            return super().exchange_code(code=code, code_verifier=code_verifier)
        except CredentialRevoked as revoked:
            # No account id and no address to name: the row does not exist yet, and
            # Google refused before saying which identity this was.
            raise DriveCredentialRevoked() from revoked
        except GmailUnavailable as unavailable:
            raise DriveUnavailable(int(unavailable.details.get("status", 0))) from unavailable

    def account_for_user(self, user_id: UUID) -> GoogleDriveAccount | None:
        return self.repo.account_for_user(user_id)

    def sibling_account(self, user_id: UUID) -> GoogleCredential | None:
        return self.gmail.account_for_user(user_id)

    def new_account(self, user_id: UUID) -> GoogleDriveAccount:
        return GoogleDriveAccount(user_id=user_id)

    def already_connected(self, existing: GoogleDriveAccount, grant: TokenGrant) -> Conflict:
        return Conflict(
            self.entity,
            f"questa installazione ha già un Drive collegato a {existing.email_address}: "
            "scollega prima l'account attuale",
            connected=existing.email_address,
            offered=grant.email_address,
        )

    def sibling_mismatch(self, sibling: GoogleCredential) -> Conflict:
        return Conflict(
            self.entity,
            "questo Drive appartiene a un account Google diverso dalla casella "
            f"collegata ({sibling.email_address}): usa lo stesso account",
        )

    def on_identity_changed(self, account: GoogleDriveAccount) -> None:
        """The folders belonged to the identity that just left. Carrying them forward
        would let the new identity's grant read from, and write into, folders it was
        never asked about and may not even have access to -- so a changed identity
        starts from an unconfigured Drive again, exactly like a first connection."""
        account.root_folder_ids = []
        account.storage_folder_id = None
        # And with it whatever proof somebody had of it: there is no verified `None`, and
        # the next folder this identity chooses is proven on its own credential
        # (`GoogleDriveAccountService.set_roots`).
        account.storage_folder_verified = False
