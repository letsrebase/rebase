"""The talents' availability, asked by mail and answered on a page (REB-517, spec § 3.2,
§ 3.6; REB-892 split it out of `team_requests.py`).

**Each talent answers once, on a page, never on a GET.** «Contatta i talenti» mails every
talent of a request once; «Rimanda a chi non ha risposto» mails again only who has not
answered, each time with a fresh token in place of the old one, so a talent who answered
is never mailed again. The token is the magic link's shape (`token_urlsafe(32)` sent, its
SHA-256 at rest), good for one answer within thirty days of its mail; an unknown, spent
or expired one is the same `invalid`, so the answer says nothing about which. The mails
leave after the admin's answer (`prepare_contact`, then `deliver` in the route's
background task): `mail_sent_at` is written with the token, so the page shows the talent
as contacted at once, and taken back with the token if the provider refuses the mail,
since that link reaches nobody; a batch refused whole also gives the request back the
state it had before the send, unless a talent of the request has a mail by then (a
«Rimanda» prepared meanwhile).

The request itself (its rows, its reads, its lock) is `TeamRequestService`'s; this
service holds one and asks it. Nothing here logs a name, an address, a phone or a text:
the log lines carry ids.
"""

import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_TALENT_ANSWER, Tracker
from rebase_core.audit import AdminActionService, utcnow
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.mail import EmailSender, Mail
from rebase_core.mail_team import team_availability_mail
from rebase_core.models import TALENT_ANSWERS, TeamProposal, TeamRequest, TeamRequestTalent
from rebase_core.team_company_names import NAMES_THE_COMPANY, names_the_company
from rebase_core.team_requests import ENTITY, TeamRequestService, contactable
from rebase_core.team_schemas import TeamRequestRead

logger = logging.getLogger(__name__)

ALREADY_CONTACTED = "I talenti sono già stati contattati: rimanda a chi non ha risposto."
NOBODY_TO_CONTACT = "Non c'è nessun talento da contattare."
REQUEST_CLOSED = "La richiesta è chiusa: riaprila prima di scrivere ai talenti."
NO_SENDER = (
    "L'invio delle email non è attivo su questo ambiente: la mail non arriverebbe ai talenti."
)
# How long a talent can answer the availability mail (spec § 3.6), from when it left.
ANSWER_MAX_AGE = timedelta(days=30)
CONTACTED = "talents_contacted"
Answer = Literal["yes", "no", "invalid"]


def _token_hash(raw: str) -> str:
    """How a token is kept: its SHA-256, the magic link's own (`users._hash`)."""
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class AvailabilityMail:
    """One talent's availability mail, built by `prepare_contact` and sent by `deliver`:
    the talent's row, the hash of the token the mail carries (so a delivery never
    touches a row a later «Rimanda» gave another token), and the mail itself."""

    talent_id: UUID
    token_hash: str
    mail: Mail


@dataclass(frozen=True)
class AvailabilityBatch:
    """One «Contatta i talenti» or «Rimanda», as `prepare_contact` hands it to `deliver`:
    the request, its mails, and what the send changed on the request, the state it had
    before and the `contacted_at` it wrote (`None` when one was already there), so a
    batch the provider refuses whole leaves the request as it found it."""

    request_id: UUID
    mails: tuple[AvailabilityMail, ...]
    status_before: str
    contacted_at: datetime | None


class TeamAvailabilityService:
    """`sender` is the mails' (`None` without a mail key: `contact_talents` and `deliver`
    then refuse with `NO_SENDER`). `tracker` is `None` without PostHog."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        sender: EmailSender | None = None,
        tracker: Tracker | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
        self.sender = sender
        self.tracker = tracker
        self.now = now
        self.requests = TeamRequestService(session, settings=settings, tracker=tracker, now=now)

    def contact_talents(
        self, request_id: UUID, admin_id: UUID, *, only_silent: bool
    ) -> TeamRequestRead:
        """«Contatta i talenti» (`only_silent` false) and «Rimanda a chi non ha
        risposto» (true), start to end: the tokens, then the mails through `sender`.
        The API's route runs the two halves apart, `deliver` after its answer."""
        if self.sender is None:
            raise InvalidState(NO_SENDER)
        _, batch = self.prepare_contact(request_id, admin_id, only_silent=only_silent)
        self.deliver(batch)
        return self.requests.get(request_id)

    def prepare_contact(
        self, request_id: UUID, admin_id: UUID, *, only_silent: bool
    ) -> tuple[TeamRequestRead, AvailabilityBatch]:
        """The tokens and the mails, committed and not sent. Every talent the first
        time, and a first time only: once anyone has the mail, writing again is
        `only_silent`, to each talent with no answer. A talent whose card was deleted or
        `discarded` since the request is skipped (`_contactable`), and logged by id.
        Refused, before anything is written: a closed request, a summary that names the
        company (`names_the_company`), a second first time, and nobody left to write to.
        The request becomes `contacted`, with `contacted_at` from its first send, until
        `deliver` finds that no mail of the batch left."""
        row = self.requests.lock(request_id)
        if row.status == "closed":
            self.session.rollback()
            raise InvalidState(REQUEST_CLOSED)
        # Read fresh, as `_lock` reads the request: the summary may have been edited in
        # another session since this one last saw it, and the mail sends what is there.
        proposal = (
            self.session.get(TeamProposal, row.proposal_id, populate_existing=True)
            if row.proposal_id is not None
            else None
        )
        summary = proposal.summary if proposal is not None else None
        if summary is not None and names_the_company(summary, row.company):
            self.session.rollback()
            raise InvalidState(NAMES_THE_COMPANY)
        talents = self.requests.talent_rows(row, proposal, lock=True)
        if not only_silent and any(talent.mail_sent_at is not None for talent, _, _ in talents):
            self.session.rollback()
            raise InvalidState(ALREADY_CONTACTED)
        now = self.now()
        page = f"{self.settings.hub_url.rstrip('/')}/team/answer"
        mails: list[AvailabilityMail] = []
        for talent, freelancer, user in talents:
            if talent.answer is not None:
                continue
            if not contactable(freelancer):
                logger.info("team request %s: talent %s is gone, not mailed", row.id, talent.id)
                continue
            raw = secrets.token_urlsafe(32)
            talent.token_hash = _token_hash(raw)
            talent.mail_sent_at = now
            mails.append(
                AvailabilityMail(
                    talent_id=talent.id,
                    token_hash=talent.token_hash,
                    mail=team_availability_mail(
                        user.email,
                        first_name=user.first_name,
                        role=talent.role,
                        summary=summary,
                        rate=freelancer.daily_rate,
                        yes_url=f"{page}?t={raw}&r=yes",
                        no_url=f"{page}?t={raw}&r=no",
                    ),
                )
            )
        if not mails:
            self.session.rollback()
            raise InvalidState(NOBODY_TO_CONTACT)
        batch = AvailabilityBatch(
            request_id=row.id,
            mails=tuple(mails),
            status_before=row.status,
            contacted_at=now if row.contacted_at is None else None,
        )
        if row.status == "new":
            row.status = "contacted"
        if row.contacted_at is None:
            row.contacted_at = now
        self.session.commit()
        AdminActionService(self.session).record(
            ENTITY, row.id, CONTACTED, admin_id, {"talents": len(mails), "only_silent": only_silent}
        )
        return self.requests.get(request_id), batch

    def deliver(self, batch: AvailabilityBatch) -> int:
        """Sends each mail `prepare_contact` built, and answers how many the provider
        accepted. An accepted one keeps its token and moves `mail_sent_at` to the moment
        it left; a refused one takes both back, since that link reaches nobody, and is
        logged by id, so «Rimanda» writes to that talent again. A batch refused whole
        contacted nobody: the request goes back to the state it had and loses the
        `contacted_at` this batch wrote, so the page offers «Contatta i talenti» again,
        unless a talent of the request has another send's mail by then (`_undo_contact`)."""
        if self.sender is None:
            raise InvalidState(NO_SENDER)
        request_id = batch.request_id
        accepted = 0
        for item in batch.mails:
            sent = self.sender.send(item.mail)
            same = (
                TeamRequestTalent.id == item.talent_id,
                TeamRequestTalent.token_hash == item.token_hash,
                TeamRequestTalent.answer.is_(None),
            )
            if sent:
                accepted += 1
                values: dict[str, Any] = {"mail_sent_at": self.now()}
            else:
                logger.warning(
                    "team request %s: the provider refused the mail to talent %s",
                    request_id,
                    item.talent_id,
                )
                values = {"token_hash": None, "mail_sent_at": None}
            self.session.execute(update(TeamRequestTalent).where(*same).values(**values))
            self.session.commit()
        if accepted == 0:
            self._undo_contact(batch)
        return accepted

    def answer(self, raw_token: str, answer: str) -> Answer:
        """A talent's «Conferma» on the answer page: `answer` recorded with its time
        and counted (`team_talent_answer`, the answer alone), or `invalid` for a token
        that is unknown, already answered, or older than `ANSWER_MAX_AGE`, all alike."""
        if answer not in TALENT_ANSWERS:
            raise ValidationFailed(ENTITY, "answer", f"uno fra {', '.join(TALENT_ANSWERS)}")
        if not raw_token:
            return "invalid"
        talent = self.session.scalar(
            select(TeamRequestTalent)
            .where(TeamRequestTalent.token_hash == _token_hash(raw_token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self.now()
        if (
            talent is None
            or talent.answer is not None
            or talent.mail_sent_at is None
            or talent.mail_sent_at <= now - ANSWER_MAX_AGE
        ):
            self.session.rollback()
            return "invalid"
        talent.answer = answer
        talent.answered_at = now
        self.session.commit()
        if self.tracker is not None:
            self.tracker.team_event(TEAM_TALENT_ANSWER, {"answer": answer})
        return "yes" if answer == "yes" else "no"

    def _undo_contact(self, batch: AvailabilityBatch) -> None:
        """What a batch refused whole wrote on the request, taken back where it still
        stands: the state, if the send moved it and nobody moved it since, and the
        `contacted_at` it wrote. Nothing is taken back while any talent of the request,
        of this batch or not, has a mail (`mail_sent_at`): a send prepared after this one
        (a «Rimanda») wrote to them, and the request was contacted after all. The row is
        locked first, as `prepare_contact` locks it, so a send prepared meanwhile is
        either seen here or waits for this to finish."""
        self.requests.lock(batch.request_id)
        mailed = self.session.scalar(
            select(TeamRequestTalent.id)
            .where(
                TeamRequestTalent.request_id == batch.request_id,
                TeamRequestTalent.mail_sent_at.is_not(None),
            )
            .limit(1)
        )
        if mailed is not None:
            self.session.commit()
            logger.warning(
                "team request %s: no mail of this batch left, but a talent has another "
                "send's; the request stays as it is",
                batch.request_id,
            )
            return
        if batch.status_before != "contacted":
            self.session.execute(
                update(TeamRequest)
                .where(TeamRequest.id == batch.request_id, TeamRequest.status == "contacted")
                .values(status=batch.status_before)
            )
        if batch.contacted_at is not None:
            self.session.execute(
                update(TeamRequest)
                .where(
                    TeamRequest.id == batch.request_id,
                    TeamRequest.contacted_at == batch.contacted_at,
                )
                .values(contacted_at=None)
            )
        self.session.commit()
        logger.warning(
            "team request %s: no availability mail left, the request is %s again",
            batch.request_id,
            batch.status_before,
        )
