"""The intake of a work profile (REB-1124, spec 2026-11-17 § 3).

In order, before anything is stored: the token, the schema, what must not be there, the
structure, the groundedness recomputed. A refusal is a `WorkProfileRefused` (422) and leaves the
token unspent; a profile that passes is stored and the token spent in the same transaction.
`contact` is dropped (the token says who sent it); one row per freelancer, a new submission
replaces the previous one and goes back to `draft`.
"""

import calendar
import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.cli_auth import sha256_hex
from rebase_core.config import Settings
from rebase_core.errors import CliNotAllowed, CliTokenInvalid, WorkProfileRefused
from rebase_core.members import MemberService
from rebase_core.models import TOOL_VERSION_MAX_LENGTH, CliAuthorization, WorkProfile
from rebase_core.work_profiles.groundedness import assess_groundedness
from rebase_core.work_profiles.schemas import CandidateProfile, WorkProfileAccepted
from rebase_core.work_profiles.structure import forbidden, structure

MAX_BYTES = 512 * 1024
MIN_GROUNDEDNESS = 60
RETENTION_MONTHS = 12
ATTACHMENTS_REFUSED = "gli allegati non sono accettati: indica un link"


def add_months(moment: datetime, months: int) -> datetime:
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


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
        """The authorization a bearer names, or `CliTokenInvalid`: unknown, expired or spent."""
        if not raw:
            raise CliTokenInvalid("Accesso non valido o scaduto.")
        query = select(CliAuthorization).where(CliAuthorization.token_hash == sha256_hex(raw))
        row = self.session.scalar(query.with_for_update() if lock else query)
        if (
            row is None
            or row.state != "approved"
            or row.scope != "work_profile"
            or row.user_id is None
            or row.expires_at <= self._now()
        ):
            raise CliTokenInvalid("Accesso non valido o scaduto.")
        return row

    def submit(
        self, raw_token: str | None, body: bytes, tool_version: str | None = None
    ) -> WorkProfileAccepted:
        auth = self.live_token(raw_token, lock=True)  # 1. the token
        payload = self._checked(body)  # 2 to 5
        if auth.user_id is None:  # `live_token` has refused this already
            raise CliTokenInvalid("Accesso non valido o scaduto.")
        card = MemberService(self.session).card_for_user(auth.user_id)
        if card is None:
            raise CliNotAllowed("Non c'è una scheda rebase a cui collegare il profilo.")
        score = assess_groundedness(payload)
        stored = {key: value for key, value in payload.items() if key != "contact"}
        now = self._now()
        version = payload.get("toolVersion")
        if not isinstance(version, str):
            version = tool_version
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
        row.state = "draft"
        row.submitted_at = now
        row.expires_at = add_months(now, RETENTION_MONTHS)
        row.decided_at = None
        row.decided_by = None
        row.decision_note = None
        auth.state = "spent"
        auth.spent_at = now
        self.session.commit()
        return WorkProfileAccepted(
            state="draft", review_url=f"{self.settings.hub_url.rstrip('/')}/me"
        )

    def _checked(self, body: bytes) -> dict[str, Any]:
        if len(body) > MAX_BYTES:
            raise WorkProfileRefused("Il profilo supera i 512 KB.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise WorkProfileRefused("Il file non è un JSON valido.") from error
        if not isinstance(payload, dict):
            raise WorkProfileRefused("Il file non è un profilo: serve un oggetto JSON.")
        try:  # 2. the schema
            CandidateProfile.model_validate(payload)
        except ValidationError as error:
            issues = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()]
            raise WorkProfileRefused(_sentence(issues)) from error
        if issues := forbidden(payload):  # 3. what must not be there
            raise WorkProfileRefused(_sentence(issues))
        if issues := structure(payload):  # 4. the structure
            raise WorkProfileRefused(_sentence(issues))
        score = assess_groundedness(payload)  # 5. groundedness, recomputed
        if score.score is None:
            raise WorkProfileRefused(
                "Il profilo ha troppo poco testo verificabile: non si può controllare."
            )
        if score.score < MIN_GROUNDEDNESS:
            raise WorkProfileRefused(
                f"Il testo del profilo non regge con i suoi dati (verificabile al {score.score}%, "
                f"servono almeno {MIN_GROUNDEDNESS}%)."
            )
        return payload
