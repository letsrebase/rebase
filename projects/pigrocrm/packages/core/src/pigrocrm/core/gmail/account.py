"""The credential's state, and the two questions that must never be confused.

*Is the credential healthy?* is `status`. *Is this feature available?* is derived from
`scopes_granted` at the point of use. A valid credential that was granted less than was
asked for is healthy; it is the feature that is unavailable. Conflating them is the
contradiction the rest of the OAuth-integration world falls into, and it produces a
"ricollega la casella" prompt for a problem reconnecting does not fix.

Four statuses and four sentences, because each one asks something different of the
person: nothing, wait, re-consent, re-connect. The one thing this module refuses to do
is collapse them into "problema con Gmail" -- that is the previous system's defect, which turned a
revoked grant and a dropped packet into the same screen and therefore the same wrong
reaction.

`/health` deliberately does **not** change when any of this goes wrong. A broken Gmail
credential is not a broken deploy, and teaching the operator that the probe goes red for
things they cannot fix from the deploy layer teaches them to ignore the probe.

One new `activities` kind lives here: `gmail.impostazioni_modificate`, beyond the nine
the design lists. Turning the body store off changes what the CRM will remember of
somebody's correspondence, which is worth a trace; `activities.kind` is open by project
(slice 1 §5.8), so a new one costs no migration.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings, gmail_configured
from pigrocrm.core.errors import Conflict
from pigrocrm.core.gmail.errors import ConsentExpired, CredentialRevoked, ScopeMissing
from pigrocrm.core.gmail.models import GoogleAccount
from pigrocrm.core.gmail.repository import GmailRepository
from pigrocrm.core.gmail.schemas import (
    SCOPE_READONLY,
    SCOPE_SEND,
    GmailBannerReason,
    GmailHealth,
    GoogleAccountRead,
)

# Warn two days out -- before anything fails. A warning issued after the first error is
# not a warning, because the error already was one.
CONSENT_WARNING_HOURS = 48

_NO_ACCOUNT = "nessuna casella Google collegata"
_NOT_A_USER = "solo un utente può usare una casella Google"
_SETTINGS_ACTION = "update_gmail_settings"

_REVOKED_TEXT = (
    "Il consenso Google per {email} è stato revocato: la sincronizzazione e l'invio "
    "sono sospesi finché non ricolleghi la casella."
)
_EXPIRING_TEXT = (
    "Il consenso Google per {email} va rinnovato entro il {when}. "
    "Dopo quella data la sincronizzazione si interrompe."
)
_EXPIRED_TEXT = (
    "Il consenso Google per {email} è scaduto e va rinnovato: la sincronizzazione è ferma."
)
_SCOPE_TEXT = (
    "Il sync è spento: manca l'autorizzazione {scopes}. Usa «ri-autorizza» per concederla."
)


class GoogleAccountService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repo = GmailRepository(session)
        self.activities = ActivityService(session)

    def health(self, actor: Actor) -> GmailHealth:
        """One call answers the whole shell banner.

        No account at all is not a problem to report: an installation that never
        connected Gmail has nothing to say about Gmail, and a banner there would be an
        error message for a feature nobody switched on.
        """
        # Read here rather than taken from the caller: this method is the one place that
        # answers "what is the state of Gmail for this person", and an adapter that had
        # to remember to say whether Gmail exists at all would eventually forget.
        configured = gmail_configured(self.settings)
        account = self.repo.account_for_user(actor.id) if actor.id else None
        if account is None:
            return GmailHealth(
                account=None,
                banner=None,
                banner_text=None,
                missing_scopes=[],
                configured=configured,
            )

        # The seven days are Testing mode's, never this CRM's. `oauth.py._store` writes
        # `consent_expires_at` only while `google_app_unverified` is on, and once the
        # operator publishes the client -- Internal, or verified -- Google stops
        # expiring the refresh token while the rows written before that keep their date.
        # Read literally, an old row would go on asking somebody to renew a consent that
        # is not going to lapse, and then declare it dead on a day nothing happened:
        # exactly the class of defect this module exists to prevent, a true-sounding
        # sentence about something that did not occur.
        #
        # So the prediction is dropped with the mode that made it, and only the
        # prediction: `status` below is a fact somebody learned and is reported either
        # way. The stale column is deliberately left in place rather than cleared -- it
        # is the record of what was true under the consent that wrote it, and a
        # migration that erased it would also erase the evidence for an install that
        # went back to Testing.
        #
        # Filtered *here*, above the read model, and not only in the branches below.
        # `GmailHealth.account` is what the settings page renders, and it printed the
        # same prediction next to the address -- «Consenso da rinnovare entro il ...» --
        # from this very field. Dropping it in the banner alone would have moved the
        # sentence rather than removed it.
        expires_at = account.consent_expires_at if self.settings.google_app_unverified else None

        read = GoogleAccountRead.model_validate(account).model_copy(
            update={"consent_expires_at": expires_at}
        )
        missing = [
            scope for scope in (SCOPE_READONLY, SCOPE_SEND) if scope not in account.scopes_granted
        ]
        now = datetime.now(UTC)

        def answer(banner: GmailBannerReason, text: str | None) -> GmailHealth:
            """Every branch below answers with the same account and the same scope list;
            only the cause and its sentence differ. Written once so a new cause cannot
            be added that forgets to report `missing_scopes`."""
            return GmailHealth(
                account=read,
                banner=banner,
                banner_text=text,
                missing_scopes=missing,
                configured=configured,
            )

        # Order matters, and it is an order of actionability. Revoked is the most final,
        # so it wins over everything below it.
        if account.status == "revoked":
            return answer("revoked", _REVOKED_TEXT.format(email=account.email_address))
        if account.status == "disconnected":
            # Nothing is wrong. The user unhooked this mailbox on purpose, and telling
            # them it was revoked -- which is what this row said before `disconnected`
            # was a status of its own -- is a lie about their own action.
            return answer(None, None)
        # A date already past is inside "within 48 hours" by arithmetic, so it is caught
        # here rather than being reported as a gentle heads-up about the future.
        if account.status == "expired" or (expires_at is not None and expires_at <= now):
            return answer("expired", _EXPIRED_TEXT.format(email=account.email_address))
        if expires_at is not None and expires_at - now <= timedelta(hours=CONSENT_WARNING_HOURS):
            return answer(
                "expiring",
                _EXPIRING_TEXT.format(
                    email=account.email_address,
                    # The day, not "presto": the date is what lets somebody decide
                    # whether this is a thing for now or a thing for Monday.
                    when=expires_at.strftime("%d/%m/%Y alle %H:%M"),
                ),
            )
        if SCOPE_READONLY in missing:
            # Only the read scope raises a banner. A credential granted everything but
            # `gmail.send` still syncs, which is most of the feature; `missing_scopes`
            # carries that fact to the settings page without a banner over the whole app.
            return answer("scope_missing", _SCOPE_TEXT.format(scopes=SCOPE_READONLY))
        return answer(None, None)

    def usable(self, actor: Actor, *, scope: str, feature: str) -> GoogleAccount:
        """The gate. Called **before** anything is composed and before any HTTP call is
        made, so that somebody who presses Invia on a revoked account learns it at the
        press rather than afterwards -- and so that nothing is half-done in between.

        This class is constructed from a session and settings and holds no transport,
        which is what makes "no HTTP call" a property of the code rather than a promise:
        there is nothing here to call Google *with*.

        Each refusal is the true one for its state. They all end in "ricollega la
        casella", but a person reading "è stato revocato" about a consent that merely
        ran out, or about a mailbox they disconnected themselves, has been told
        something that did not happen.
        """
        account = self._present(actor)
        if account.status == "disconnected":
            raise Conflict("google_account", _NO_ACCOUNT)
        if account.status == "expired":
            raise ConsentExpired(account.id, account.email_address)
        if account.status != "active":
            raise CredentialRevoked(account.id, account.email_address)
        if scope not in account.scopes_granted:
            # Not a credential problem: `status` stays `active` and the other features
            # keep working. It is this feature that is unavailable.
            raise ScopeMissing(scope, feature)
        return account

    def mark_revoked(self, account: GoogleAccount, actor: Actor, reason: str) -> None:
        """Terminal. Called from the one place that can learn it -- a refresh answering
        `invalid_grant`.

        Committed on its own behalf, deliberately and unusually. The operation that
        discovered this is about to raise, and its transaction will be rolled back by
        whoever catches it; if the fact went with it, the next cron run would ask again,
        get the same `invalid_grant`, and nobody would ever be told. `actor` is
        `Actor.system()` at every call site for the same reason the entry exists: Google
        revoked this, nobody in this CRM did.

        `reason` is the sentence the user reads. It must carry no upstream prose, no
        error code and no token -- which is why it is a parameter and not something
        derived from the exception here.
        """
        account.status = "revoked"
        account.last_error = reason
        account.last_error_at = datetime.now(UTC)
        self.activities.record(
            "google_account",
            account.id,
            "gmail.credenziale_revocata",
            actor,
            # The address and nothing else. Not the upstream body, which is the thing
            # The previous system truncated to 400 characters and put on screen.
            {"email_address": account.email_address},
        )
        self.session.commit()

    def set_store_bodies(self, *, enabled: bool, actor: Actor) -> GoogleAccountRead:
        """Turns the body store on or off.

        Reached through `_present` and not `usable`: a settings change is not a use of
        the credential, and gating it behind a healthy one would mean the moment a user
        most wants the CRM to stop keeping their mail is the moment they cannot say so.
        """
        actor.require_write(_SETTINGS_ACTION)
        account = self._present(actor)
        account.gmail_store_bodies = enabled
        self.activities.record(
            "google_account",
            account.id,
            "gmail.impostazioni_modificate",
            actor,
            {"gmail_store_bodies": enabled},
        )
        self.session.commit()
        return GoogleAccountRead.model_validate(account)

    # --- internals -------------------------------------------------------------------

    def _present(self, actor: Actor) -> GoogleAccount:
        """This actor's account whatever its state, or a `Conflict` if there is none.

        Always this actor's: one account per user, and a lookup that read the first row
        in the table would show one person another's address.
        """
        if actor.id is None:
            raise Conflict("google_account", _NOT_A_USER)
        account = self.repo.account_for_user(actor.id)
        if account is None:
            raise Conflict("google_account", _NO_ACCOUNT)
        return account
