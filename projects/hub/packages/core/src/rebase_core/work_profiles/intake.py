"""The intake of a work profile (REB-1124, spec 2026-11-17 § 3).

In order, before anything is stored: the token, the schema, what must not be there, the
structure, the groundedness recomputed. A refusal is a `WorkProfileRefused` (422) and leaves the
token unspent; a profile that passes is stored and the token spent in the same transaction.
`contact` is dropped (the token says who sent it); one row per freelancer, a new submission
replaces the previous one and goes back to `draft`.
"""

import calendar
import copy
import json
import math
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.ai_spend import ROME
from rebase_core.audit import AdminActionService, utcnow
from rebase_core.cli_auth import sha256_hex
from rebase_core.config import Settings
from rebase_core.errors import (
    CliNotAllowed,
    CliTokenInvalid,
    InvalidState,
    NotFound,
    WorkProfileGuardBlocked,
    WorkProfileRefused,
)
from rebase_core.freelancers import ENTITY as FREELANCER
from rebase_core.freelancers import FreelancerService
from rebase_core.github_link import cross_check
from rebase_core.mail import Mail
from rebase_core.mail_member import work_profile_approved_mail, work_profile_rejected_mail
from rebase_core.members import MemberService
from rebase_core.models import (
    TOOL_VERSION_MAX_LENGTH,
    CliAuthorization,
    Freelancer,
    GithubAccount,
    User,
    WorkProfile,
)
from rebase_core.work_profiles import practices
from rebase_core.work_profiles.groundedness import (
    Groundedness,
    TooManyAnchors,
    assess_groundedness,
)
from rebase_core.work_profiles.guard import guard_hits_for
from rebase_core.work_profiles.schemas import (
    CandidateProfile,
    WorkProfileAccepted,
    WorkProfileAdminRead,
    WorkProfileAnchor,
    WorkProfileGuardHit,
)
from rebase_core.work_profiles.structure import forbidden, shape, structure
from rebase_core.work_profiles.tech_usage import drop_unknown
from rebase_core.work_profiles.view import WORK_PROFILE_LANGUAGES as LANGUAGES
from rebase_core.work_profiles.view import render_profile

MAX_BYTES = 512 * 1024
MIN_GROUNDEDNESS = 60
# A person's profile has a few dozen anchors; this many is a payload built to be slow.
MAX_ANCHORS = 2000
RETENTION_MONTHS = 12
ATTACHMENTS_REFUSED = "gli allegati non sono accettati: indica un link"
NO_PROFILE = "lavoro"
ALREADY_DECIDED = "Il profilo è già stato deciso."
GUARD_BLOCKED = (
    "Un campo che le aziende leggono nomina la persona o qualcosa di suo: correggi il profilo "
    "o approva con una nota che spiega perché va bene."
)
OVERRIDE_NEEDS_NOTE = "Per approvare nonostante le segnalazioni serve una nota."
CHANGED = "Il profilo è cambiato: rileggilo."
EXPIRED = "Il profilo è scaduto: la persona deve inviarne uno nuovo."


def add_months(moment: datetime, months: int) -> datetime:
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("a number too large to hold")
    return value


def _refuse_constant(name: str) -> float:
    raise ValueError(f"{name} is not a number in JSON")


def _sentence(issues: list[str]) -> str:
    shown = "; ".join(issues[:5])
    more = f" (e altri {len(issues) - 5})" if len(issues) > 5 else ""
    return f"Il profilo non è stato accettato: {shown}{more}."


class WorkProfileService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
        self._now = now

    def live_token(self, raw: str | None, *, lock: bool = False) -> CliAuthorization:
        """The authorization a bearer names, or `CliTokenInvalid`: unknown, expired or spent.
        With `lock` the row is read `FOR UPDATE` and refreshed from the database
        (`populate_existing`), so a second submission waits for the first and then sees it spent,
        not the copy the session loaded before."""
        if not raw:
            raise CliTokenInvalid("Accesso non valido o scaduto.")
        query = select(CliAuthorization).where(CliAuthorization.token_hash == sha256_hex(raw))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(query)
        if (
            row is None
            or row.state != "approved"
            or row.scope != "work_profile"
            or row.user_id is None
            or row.expires_at <= self._now()
        ):
            raise CliTokenInvalid("Accesso non valido o scaduto.")
        return row

    def precheck(self, raw: str | None) -> None:
        """The router's look at the bearer before it reads the body: unlocked, and the
        transaction it opened is ended, so the locked read of `submit` starts clean."""
        try:
            self.live_token(raw)
        finally:
            self.session.rollback()

    def spend(self, raw: str | None) -> None:
        """Spends the token of a refused upload: whatever refuses a profile after the token was
        accepted costs the sign-in, so a payload built to be expensive cannot be tried again."""
        try:
            auth = self.live_token(raw, lock=True)
        except CliTokenInvalid:
            self.session.rollback()
            return
        self._mark_spent(auth)
        self.session.commit()

    def _mark_spent(self, auth: CliAuthorization) -> None:
        auth.state = "spent"
        auth.spent_at = self._now()

    def submit(
        self, raw_token: str | None, body: bytes, tool_version: str | None = None
    ) -> tuple[WorkProfileAccepted, UUID]:
        """The stored profile's answer, and the card it hangs on: the caller checks its
        artifacts against the linked GitHub account after the response (REB-1168)."""
        auth = self.live_token(raw_token, lock=True)  # 1. the token
        try:
            payload, score = self._checked(body)  # 2 to 5
            if auth.user_id is None:  # `live_token` has refused this already
                raise CliTokenInvalid("Accesso non valido o scaduto.")
            card = MemberService(self.session).card_for_user(auth.user_id)
            if card is None:
                raise CliNotAllowed("Non c'è una scheda rebase a cui collegare il profilo.")
        except (WorkProfileRefused, CliNotAllowed):
            self._mark_spent(auth)
            self.session.commit()
            raise
        # Two first submissions of one freelancer would both insert: the card's row serialises them.
        self.session.scalar(
            select(Freelancer.id)
            .where(Freelancer.id == card.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        stored = copy.deepcopy({key: value for key, value in payload.items() if key != "contact"})
        unknown = drop_unknown(stored)
        unknown_practices = practices.drop_unknown(stored)
        now = self._now()
        version = payload.get("toolVersion")
        if not isinstance(version, str):
            version = tool_version
        # Read before the row is touched: the guards query, and a half-filled row must not flush.
        hits = guard_hits_for(self.session, card.id, stored)
        row = self.session.scalar(
            select(WorkProfile).where(WorkProfile.freelancer_id == card.id).with_for_update()
        )
        if row is None:
            row = WorkProfile(freelancer_id=card.id)
            self.session.add(row)
        row.payload = stored
        row.tool_version = version[:TOOL_VERSION_MAX_LENGTH] if version else None
        row.groundedness = score.score or 0
        row.unsupported_anchors = score.anomalies
        row.guard_hits = hits
        row.unknown_technologies = unknown
        row.unknown_practices = unknown_practices
        row.state = "draft"
        row.submitted_at = now
        row.expires_at = add_months(now, RETENTION_MONTHS)
        row.decided_at = None
        row.decided_by = None
        row.decision_note = None
        # Checked against the linked account after the commit, by the caller (REB-1168).
        row.artifact_checks = []
        row.artifacts_checked_at = None
        self._mark_spent(auth)
        self.session.commit()
        accepted = WorkProfileAccepted(
            state="draft", review_url=f"{self.settings.hub_url.rstrip('/')}/me"
        )
        return accepted, card.id

    def _checked(self, body: bytes) -> tuple[dict[str, Any], Groundedness]:
        if len(body) > MAX_BYTES:
            raise WorkProfileRefused("Il profilo supera i 512 KB.")
        try:
            payload = json.loads(
                body.decode("utf-8"), parse_float=_finite_float, parse_constant=_refuse_constant
            )
        except (UnicodeDecodeError, ValueError, RecursionError) as error:
            raise WorkProfileRefused("Il file non è un JSON valido.") from error
        if not isinstance(payload, dict):
            raise WorkProfileRefused("Il file non è un profilo: serve un oggetto JSON.")
        if issues := shape(payload):
            raise WorkProfileRefused(_sentence(issues))
        if (language := payload.get("language")) is not None and language not in LANGUAGES:
            raise WorkProfileRefused(
                "language: la lingua del profilo non è supportata, "
                f"le lingue supportate sono {', '.join(LANGUAGES)}."
            )
        try:  # 2. the schema
            CandidateProfile.model_validate(payload)
        except ValidationError as error:
            issues = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()]
            raise WorkProfileRefused(_sentence(issues)) from error
        if issues := forbidden(payload):  # 3. what must not be there
            raise WorkProfileRefused(_sentence(issues))
        if issues := structure(payload):  # 4. the structure
            raise WorkProfileRefused(_sentence(issues))
        try:  # 5. groundedness, recomputed
            score = assess_groundedness(payload, max_anchors=MAX_ANCHORS)
        except TooManyAnchors as error:
            raise WorkProfileRefused(
                "Il testo del profilo contiene troppe cifre e nomi da verificare."
            ) from error
        if score.score is None:
            raise WorkProfileRefused(
                "Il profilo ha troppo poco testo verificabile: non si può controllare."
            )
        if score.score < MIN_GROUNDEDNESS:
            raise WorkProfileRefused(
                f"Il testo del profilo non regge con i suoi dati (verificabile al {score.score}%, "
                f"servono almeno {MIN_GROUNDEDNESS}%)."
            )
        return payload, score

    # ---- the review (REB-1126, spec § 4) ----

    def _pending(self, freelancer_id: UUID, submitted_at: datetime) -> WorkProfile:
        """The profile an admin may decide on: locked, there, not past its 12 months. A profile
        already decided is decided again only by a new submission, which is a `draft`."""
        row = self.session.scalar(
            select(WorkProfile)
            .where(WorkProfile.freelancer_id == freelancer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        card = self.session.get(Freelancer, freelancer_id)
        if row is None or card is None or card.deleted_at is not None:
            raise NotFound(NO_PROFILE, freelancer_id)
        if row.submitted_at != submitted_at:
            raise InvalidState(CHANGED)
        if row.expires_at <= self._now():
            raise InvalidState(EXPIRED)
        if row.state != "draft":
            raise InvalidState(ALREADY_DECIDED)
        return row

    def admin_read(self, freelancer_id: UUID, *, live_only: bool = False) -> WorkProfileAdminRead:
        """The profile as the admin's section reads it, an expired one included and marked
        (`expired`): the page says why a profile that counts for nothing is still there. With
        `live_only` an expired one is not there at all, as for every other reader."""
        row = self.session.scalar(
            select(WorkProfile).where(WorkProfile.freelancer_id == freelancer_id)
        )
        if row is None or (live_only and row.expires_at <= self._now()):
            raise NotFound(NO_PROFILE, freelancer_id)
        decider = self.session.get(User, row.decided_by) if row.decided_by else None
        return WorkProfileAdminRead(
            freelancer_id=row.freelancer_id,
            state=row.state,
            submitted_at=row.submitted_at,
            expires_at=row.expires_at,
            expired=row.expires_at <= self._now(),
            tool_version=row.tool_version,
            groundedness=row.groundedness,
            unsupported_anchors=[
                WorkProfileAnchor(
                    where=str(a.get("where", "")),
                    anchor=str(a.get("anchor", "")),
                    kind=str(a.get("kind", "")),
                )
                for a in row.unsupported_anchors
            ],
            decided_at=row.decided_at,
            decided_by_name=(
                f"{decider.first_name} {decider.last_name}".strip() if decider else None
            ),
            decision_note=row.decision_note,
            unknown_technologies=row.unknown_technologies,
            unknown_practices=row.unknown_practices,
            guard_hits=[
                WorkProfileGuardHit(field=str(h.get("field", "")), rule=str(h.get("rule", "")))
                for h in row.guard_hits
            ],
            profile=render_profile(row.payload),
            cross_check=cross_check(
                self.session.scalar(
                    select(GithubAccount).where(GithubAccount.freelancer_id == freelancer_id)
                ),
                row,
            ),
        )

    def decision_mail(self, freelancer_id: UUID) -> Mail | None:
        """The mail the person gets for the decision just taken (REB-1127): approved with the
        date it stops counting, or not approved with the admin's note. `None` for a profile
        still in review, which is nobody's news."""
        row = self.session.scalar(
            select(WorkProfile).where(WorkProfile.freelancer_id == freelancer_id)
        )
        card = self.session.get(Freelancer, freelancer_id)
        if row is None or card is None or card.deleted_at is not None or row.state == "draft":
            return None
        user = self.session.get_one(User, card.user_id)
        url = f"{self.settings.hub_url.rstrip('/')}/me"
        if row.state == "approved":
            return work_profile_approved_mail(
                user.email,
                first_name=user.first_name,
                expires_on=row.expires_at.astimezone(ROME).date(),
                url=url,
            )
        return work_profile_rejected_mail(
            user.email, first_name=user.first_name, note=row.decision_note or "", url=url
        )

    def approve(
        self,
        freelancer_id: UUID,
        admin_id: UUID,
        note: str | None,
        submitted_at: datetime,
        *,
        override: bool = False,
    ) -> WorkProfileAdminRead:
        """«Approva»: the state, the date and the admin, and an entry of kind
        `work_profile_decided` in the same transaction every time. The person is vetted only
        when `vetted_at` is still null (`FreelancerService.set_vetted`, which writes its own
        `vetted` entry and records nothing when the flag already matches, which is why the
        decision has an entry of its own). The card follows by its inputs' hash: a profile
        that counts is one of them (REB-1134), so approving it makes the card stale."""
        row = self._pending(freelancer_id, submitted_at)
        card = self.session.get_one(Freelancer, freelancer_id)
        vets = card.vetted_at is None
        # The guards read again, with what the hub knows now (a client may have come since).
        row.guard_hits = guard_hits_for(self.session, freelancer_id, row.payload)
        overridden = bool(row.guard_hits) and override
        if row.guard_hits and not override:
            self.session.commit()  # the fresh hits are kept, the profile stays a draft
            raise WorkProfileGuardBlocked(GUARD_BLOCKED)
        if overridden and not note:
            raise WorkProfileGuardBlocked(OVERRIDE_NEEDS_NOTE)
        row.state = "approved"
        row.decided_at = self._now()
        row.decided_by = admin_id
        row.decision_note = note
        AdminActionService(self.session).record(
            FREELANCER,
            freelancer_id,
            "work_profile_decided",
            admin_id,
            {"decision": "approved", "vetted": vets}
            | ({"override": True, "override_note": note} if overridden else {}),
            commit=False,
        )
        if vets:
            FreelancerService(self.session).set_vetted(freelancer_id, True, admin_id)
        else:
            self.session.commit()
        return self.admin_read(freelancer_id)

    def reject(
        self, freelancer_id: UUID, admin_id: UUID, note: str, submitted_at: datetime
    ) -> WorkProfileAdminRead:
        """«Rifiuta»: the note is the freelancer's to read. `vetted_at` is not touched."""
        row = self._pending(freelancer_id, submitted_at)
        row.state = "rejected"
        row.decided_at = self._now()
        row.decided_by = admin_id
        row.decision_note = note
        AdminActionService(self.session).record(
            FREELANCER,
            freelancer_id,
            "work_profile_decided",
            admin_id,
            {"decision": "rejected", "vetted": False},
            commit=False,
        )
        self.session.commit()
        return self.admin_read(freelancer_id)
