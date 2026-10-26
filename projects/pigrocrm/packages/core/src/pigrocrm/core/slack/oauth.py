"""Installing the Slack app in a space, and removing it (REB-739).

Slack's OAuth v2 authorization-code flow, server-side, in the shape of `drive/oauth.py`:
the `state` is a fresh, unguessable jti registered in `google_oauth_states` against the
admin who started the flow with `purpose = 'slack'`, redeemed by one conditional UPDATE and
committed **before** anything that can fail, so a refusal never hands the state back for a
second attempt (`gmail/oauth.py` argues the rest at length). It is the one in-flight
registry of the three flows, so `GmailRepository.add_state` and `consume_state` are reused
and not copied.

**No PKCE, on purpose.** Slack supports it, but turning it on marks the app a public client
and cannot be turned off again without Slack's support (docs.slack.dev, «Using PKCE»), and
this app keeps its client secret on the server. The row's `code_verifier` is therefore the
empty string, and what binds the redirect to the person who started it is the same as in
Drive's flow: the state is single use, five minutes, and redeemed against the caller's own
user id.

**A space borrows the root's app** (`tenants/registry.py`): the redirect URL is the root's
and the state carries the space's slug in front of the jti, so the root's callback relays
the browser to the space (`pigrocrm_api/oauth_relay.py`), where this service redeems it
against the space's own database. Only an **unscoped admin** connects or disconnects: the
workspace is the space's, not one person's, the way the Drive folder a document is written
to is.

**Connecting leaves the space with a workspace and a first matching.** The token is sealed
under the key derived for the space and stored with the team, and every active user is
looked up by email. A Slack outage during that lookup does not undo the installation, which
has succeeded: the users stay unmatched and «Cerca di nuovo» asks again.
"""

import base64
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, decode_slack_token_key, require_slack_configured
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.crypto import seal
from pigrocrm.core.gmail.models import GoogleOAuthState
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.slack.client import SlackClient, SlackInstall
from pigrocrm.core.slack.errors import SLACK_ENTITY, SlackRefused
from pigrocrm.core.slack.matching import SlackMatcher
from pigrocrm.core.slack.models import SlackWorkspace
from pigrocrm.core.slack.repository import SlackRepository
from pigrocrm.core.slack.schemas import SLACK_BOT_SCOPES, SlackWorkspaceRead

STATE_TTL_MINUTES = 5
PURPOSE = "slack"
# 32 base64url characters: the state is the only thing between a forged callback and a
# redeemable authorisation, so it is sized as a secret (`gmail/oauth.py`).
_JTI_BYTES = 24

_CONNECT_ACTION = "collegare Slack"
_DISCONNECT_ACTION = "scollegare Slack"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class SlackOAuthService:
    def __init__(self, session: Session, *, settings: Settings, client: SlackClient) -> None:
        self.session = session
        self.settings = settings
        self.client = client
        self.repo = SlackRepository(session)
        # Gmail's repository, reused for the shared state table only.
        self.states = GmailRepository(session)
        self.activities = ActivityService(session)

    @property
    def redirect_uri(self) -> str:
        """The one string Slack compares with the list registered on the app: this
        installation's own address, or the root's while a space borrows the root's app."""
        base = self.settings.slack_callback_base_url or self.settings.public_url
        return f"{base.rstrip('/')}/api/slack/oauth/callback"

    def start(self, actor: Actor) -> str:
        require_slack_configured(self.settings)
        actor.require_unscoped_admin(_CONNECT_ACTION)
        if actor.id is None:
            raise Conflict(SLACK_ENTITY, "solo un utente può collegare Slack")
        jti = _b64url(os.urandom(_JTI_BYTES))
        self.states.add_state(
            GoogleOAuthState(
                jti=jti,
                code_verifier="",
                user_id=actor.id,
                expires_at=datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES),
                purpose=PURPOSE,
            )
        )
        # Committed here: the person is about to leave for Slack, and a state that only
        # exists in an open transaction would be unredeemable by the callback.
        self.session.commit()
        return f"{self.settings.slack_url.rstrip('/')}/oauth/v2/authorize?" + urlencode(
            {
                "client_id": self.settings.slack_client_id,
                "scope": ",".join(SLACK_BOT_SCOPES),
                "redirect_uri": self.redirect_uri,
                "state": self._published(jti),
            }
        )

    def complete(self, *, code: str, state: str, actor: Actor) -> SlackWorkspaceRead:
        require_slack_configured(self.settings)
        actor.require_unscoped_admin(_CONNECT_ACTION)
        now = datetime.now(UTC)
        if actor.id is None:
            raise self._invalid_authorisation()
        jti = self._jti_of(state)
        row = self.states.consume_state(jti, now, purpose=PURPOSE) if jti is not None else None
        if row is None:
            raise self._invalid_authorisation()
        # The redemption stands whatever happens next.
        self.session.commit()
        if row.user_id != actor.id:
            raise self._invalid_authorisation()

        install = self.client.oauth_access(
            client_id=self.settings.slack_client_id,
            client_secret=self.settings.slack_client_secret,
            code=code,
            redirect_uri=self.redirect_uri,
        )
        missing = sorted(set(SLACK_BOT_SCOPES) - set(install.scopes))
        if missing:
            # Slack does not let a person untick a requested scope, but an app whose
            # settings drifted from the manifest would install with fewer: say which, so
            # the fix is the manifest and not a guess.
            raise SlackRefused("oauth.v2.access", f"missing_scope:{','.join(missing)}")

        existing = self.repo.workspace()
        if (
            existing is not None
            and existing.status == "active"
            and existing.team_id != install.team_id
        ):
            raise Conflict(
                SLACK_ENTITY,
                f"questo spazio ha già Slack collegato a {existing.team_name}: "
                "scollega prima il workspace attuale",
                connected=existing.team_name,
                offered=install.team_name,
            )
        workspace = self._store(existing, install, actor.id, now)
        self.activities.record(
            SLACK_ENTITY,
            workspace.id,
            "slack.workspace_collegato",
            actor,
            {"team_id": workspace.team_id, "team_name": workspace.team_name},
        )
        self.session.commit()
        self._first_matching(workspace, install.access_token)
        return SlackWorkspaceRead.model_validate(workspace)

    def disconnect(self, actor: Actor) -> None:
        actor.require_unscoped_admin(_DISCONNECT_ACTION)
        workspace = self.repo.workspace()
        if workspace is None or workspace.status == "disconnected":
            raise Conflict(SLACK_ENTITY, "nessun workspace Slack collegato")
        workspace.status = "disconnected"
        workspace.disconnected_at = datetime.now(UTC)
        # Overwritten, not merely dereferenced: a ciphertext left behind is in every
        # backup taken after the disconnect.
        workspace.bot_token_ciphertext = b""
        workspace.bot_token_nonce = b""
        workspace.channel_id = None
        workspace.channel_name = None
        workspace.last_error = None
        workspace.last_error_at = None
        # Who is whom belongs to the workspace that left, manual fixes included.
        self.repo.delete_links()
        self.activities.record(
            SLACK_ENTITY,
            workspace.id,
            "slack.workspace_scollegato",
            actor,
            {"team_id": workspace.team_id, "team_name": workspace.team_name},
        )
        self.session.commit()

    # -- internals -------------------------------------------------------------

    def _published(self, jti: str) -> str:
        """The `state` sent to Slack: the jti behind the space's prefix when the root relays
        the consent (empty for the root itself)."""
        return f"{self.settings.slack_oauth_state_prefix}{jti}"

    def _jti_of(self, state: str) -> str | None:
        """The jti a returning state carries, or None when it does not carry this
        installation's prefix, which is refused like an unknown jti."""
        prefix = self.settings.slack_oauth_state_prefix
        if not state.startswith(prefix):
            return None
        return state[len(prefix) :] or None

    @staticmethod
    def _invalid_authorisation() -> Conflict:
        """One sentence for every failure before ownership is proved, with no details: an
        unknown, expired, replayed and foreign state are indistinguishable from outside
        (`gmail/oauth.py`)."""
        return Conflict(
            SLACK_ENTITY,
            "questa autorizzazione non è più valida: ricomincia da Impostazioni → Slack",
        )

    def _store(
        self,
        existing: SlackWorkspace | None,
        install: SlackInstall,
        user_id: UUID,
        now: datetime,
    ) -> SlackWorkspace:
        ciphertext, nonce = seal(install.access_token, decode_slack_token_key(self.settings))
        workspace = existing or SlackWorkspace(
            team_id=install.team_id,
            team_name=install.team_name,
            bot_user_id=install.bot_user_id,
            bot_token_ciphertext=ciphertext,
            bot_token_nonce=nonce,
        )
        if existing is None:
            self.repo.add_workspace(workspace)
        else:
            if existing.team_id != install.team_id:
                # Another workspace after a disconnect or a revocation: the channel and
                # the members belong to the one that left.
                workspace.channel_id = None
                workspace.channel_name = None
                self.repo.delete_links()
            if existing.status != "active":
                workspace.connected_at = now
        workspace.team_id = install.team_id
        workspace.team_name = install.team_name
        workspace.bot_user_id = install.bot_user_id
        workspace.bot_token_ciphertext = ciphertext
        workspace.bot_token_nonce = nonce
        workspace.scopes_granted = list(install.scopes)
        workspace.status = "active"
        workspace.connected_by = user_id
        workspace.last_error = None
        workspace.last_error_at = None
        workspace.disconnected_at = None
        self.session.flush()
        return workspace

    def _first_matching(self, workspace: SlackWorkspace, token: str) -> None:
        try:
            SlackMatcher(self.repo, self.client, token).match_all(self.repo.people(), force=True)
            self.session.commit()
        except Conflict:
            # The installation stands; what is unmatched is asked again from the page.
            self.session.rollback()
