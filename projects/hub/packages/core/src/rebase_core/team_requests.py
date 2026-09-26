"""The team request (REB-512, spec § 3.2, § 3.5): a company's «Assumi team» on a proposal
becomes a row the admin works by hand, one talent per member of the team, and a mail to
rebase's own address, which `create` builds and hands back: the route sends it after the
response, so a slow or failing provider never holds or undoes a request already filed.

**A proposal is requested once.** The insert relies on `uq_team_requests_proposal_id`:
two clicks in two sessions both pass every check and the index lets one of them in; the
other is the same `409` a second click gets, «Questa proposta è già stata richiesta.».
No read before the insert pretends to decide it.

**Only what the caller may request.** A public request takes a public proposal younger
than a day; the cloud's (D3), a cloud proposal of its own user. One sentence for every
refusal, so the answer does not say which proposals exist. A proposal with nobody in it
(nobody fit, or the catalogue was empty) has nobody to hire, and says so.

**The summary is checked, then refused when it is written.** A proposal's summary that
names the company is logged at creation, by the request's id alone, since the visitor
cannot change it; the admin's own edit (`set_summary`) that still names it is refused,
and so is the availability mail (D1), before the talents read it.

Nothing here logs a name, an address, a phone or a text: the log lines carry ids.
"""

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_REQUEST_SENT, Tracker
from rebase_core.audit import AdminActionService, field_changes, utcnow
from rebase_core.bands import band_for
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.mail import EmailSender, Mail, team_request_mail
from rebase_core.models import (
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
    Freelancer,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.team_builder import TeamBuilder
from rebase_core.team_schemas import (
    TeamRequestCreate,
    TeamRequestList,
    TeamRequestListItem,
    TeamRequestRead,
    TeamRequestTalentRead,
)

logger = logging.getLogger(__name__)

ENTITY = "team_request"
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200
# How long a proposal can be requested (spec § 3.2): the same day a visitor saw it.
REQUEST_MAX_AGE = timedelta(days=1)
ALREADY_REQUESTED = "Questa proposta è già stata richiesta."
PROPOSAL_REFUSED = "Questa proposta non esiste o è scaduta: chiedi di nuovo il team."
NOBODY_TO_HIRE = "Questa proposta non ha nessuno da assumere."
NO_SUMMARY = "Questa richiesta è per un talento solo: non ha un riassunto da modificare."
NAMES_THE_COMPANY = "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
UNIQUE_PROPOSAL_INDEX = "uq_team_requests_proposal_id"
_SORT = SortSpec("created_at", "datetime")
# A word of the company's name, as `names_the_company` reads one: letters and digits.
_WORD = re.compile(r"[^\W_]+")
# Shorter words are articles and initials («di», «Ars», «3M»), too common to be the name.
_NAME_WORD_MIN_LENGTH = 4
# The legal forms, with or without their dots: «S.r.l.» names the kind of company, not
# which one, and «srls» is the one of four letters.
_LEGAL_FORMS = frozenset({"srl", "srls", "spa", "snc", "sas"})
# The words a company's name shares with the kind of company it is, which the prompt
# itself asks the summary to name instead («un'azienda di logistica»): «Logistica
# Veneta S.r.l.» is not named by it, and «Studio Legale Bianchi» not by «uno studio
# legale». Lower case, matched whole; only the other words of a name are distinctive.
_GENERIC_WORDS = frozenset(
    {
        "agency",
        "agenzia",
        "associati",
        "azienda",
        "company",
        "consulenza",
        "consulting",
        "cooperativa",
        "design",
        "digital",
        "digitale",
        "engineering",
        "group",
        "gruppo",
        "holding",
        "impresa",
        "informatica",
        "ingegneria",
        "international",
        "italia",
        "italiana",
        "italy",
        "labs",
        "legale",
        "logistica",
        "marketing",
        "media",
        "partner",
        "partners",
        "servizi",
        "services",
        "sistemi",
        "società",
        "software",
        "solutions",
        "soluzioni",
        "studio",
        "systems",
        "tech",
        "technologies",
        "technology",
        "tecnologie",
        "trasporti",
    }
)


def names_the_company(riassunto: str, azienda: str) -> bool:
    """Any word of four letters or more of `azienda` that is neither a legal form nor a
    generic word of a company's kind, case-insensitively, inside the summary as a whole
    word: «Acme S.r.l.» is named by «ACME rifà il gestionale», «Data Srl» is not by «un
    database», «Acme Srls» is not by «una srls di Torino», and «Logistica Veneta
    S.r.l.» is not by «un'azienda di logistica»."""
    for token in azienda.split():
        if token.replace(".", "").strip(",;:").casefold() in _LEGAL_FORMS:
            continue
        for word in _WORD.findall(token):
            if len(word) < _NAME_WORD_MIN_LENGTH:
                continue
            if word.casefold() in _LEGAL_FORMS or word.casefold() in _GENERIC_WORDS:
                continue
            if re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", riassunto, re.IGNORECASE):
                return True
    return False


def _violates_unique_proposal(exc: IntegrityError) -> bool:
    """Whether the insert lost to another request of the same proposal: read from the
    driver's diagnostics, the way `matches._violates_match_id` reads its own key."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == UNIQUE_PROPOSAL_INDEX


class TeamRequestService:
    """`sender` is the availability mails' (D1, `None` without a mail key); the request's
    own mail to rebase is handed back by `create` for the caller to send after its
    response. `tracker` is `None` without PostHog."""

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

    # ---- the request ------------------------------------------------------------------

    def create(
        self,
        data: TeamRequestCreate,
        *,
        origine: str,
        user_id: UUID | None,
        company_id: UUID | None,
        telefono: str | None = None,
    ) -> tuple[TeamRequestRead, Mail]:
        """The request for `data.proposal_id`, with one talent per member of its team,
        and the mail to `settings.contracts_mail` (`request_mail`), built and not sent:
        the caller sends it after its own answer. `telefono`, when given, is stored in
        place of `data.telefono`: the cloud's route (D3) files with the user's own."""
        if origine not in TEAM_REQUEST_ORIGINS:
            raise ValueError(f"unknown origin {origine!r}")
        proposal = self._requestable(data.proposal_id, origine=origine, user_id=user_id)
        members = self._members(proposal)
        row = TeamRequest(
            proposal_id=proposal.id,
            origine=origine,
            azienda=data.azienda,
            email=str(data.email),
            telefono=telefono if telefono is not None else data.telefono,
            user_id=user_id,
            company_id=company_id,
            stato="nuova",
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            # The other click committed first: the index, not a read before the insert,
            # is what serializes the two.
            self.session.rollback()
            if not _violates_unique_proposal(exc):
                raise
            raise InvalidState(ALREADY_REQUESTED, proposal_id=str(data.proposal_id)) from exc
        self.session.add_all(
            TeamRequestTalent(request_id=row.id, freelancer_id=freelancer_id, ruolo=ruolo)
            for freelancer_id, ruolo in members
        )
        self.session.commit()
        if names_the_company(proposal.riassunto, row.azienda):
            logger.warning("team request %s: the summary names the company", row.id)
        if self.tracker is not None:
            self.tracker.team_event(TEAM_REQUEST_SENT, {"origine": origine})
        read = self.get(row.id)
        return read, self.request_mail(read)

    def request_mail(self, read: TeamRequestRead) -> Mail:
        """«Nuova richiesta team da …» to rebase's contracts address: the summary of the
        proposal, or for a request of one talent with no proposal (D3) that talent's
        name, and the link to the request's page, where the contacts are."""
        talento = None
        if read.proposal is None and read.talenti:
            talento = f"{read.talenti[0].nome} {read.talenti[0].cognome}".strip()
        return team_request_mail(
            self.settings.contracts_mail,
            azienda=read.azienda,
            riassunto=read.riassunto,
            talento=talento,
            url=f"{self.settings.hub_url.rstrip('/')}/admin/team/{read.id}",
        )

    # ---- the admin's list and page ----------------------------------------------------

    def list_recent(
        self,
        *,
        stato: str | None,
        origine: str | None,
        limit: int = LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> TeamRequestList:
        """«Richieste team», newest first, by cursor; each row with how many talents
        were asked and how many said yes, counted for the page in the same query."""
        if stato is not None and stato not in TEAM_REQUEST_STATES:
            raise ValidationFailed(ENTITY, "stato", f"uno fra {', '.join(TEAM_REQUEST_STATES)}")
        if origine is not None and origine not in TEAM_REQUEST_ORIGINS:
            raise ValidationFailed(ENTITY, "origine", f"uno fra {', '.join(TEAM_REQUEST_ORIGINS)}")
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        counts = (
            select(
                TeamRequestTalent.request_id.label("request_id"),
                func.count().label("totale"),
                func.count().filter(TeamRequestTalent.risposta == "si").label("si"),
            )
            .group_by(TeamRequestTalent.request_id)
            .subquery()
        )
        stmt = select(TeamRequest, counts.c.totale, counts.c.si).outerjoin(
            counts, counts.c.request_id == TeamRequest.id
        )
        if stato is not None:
            stmt = stmt.where(TeamRequest.stato == stato)
        if origine is not None:
            stmt = stmt.where(TeamRequest.origine == origine)
        if cursor:
            value, row_id = decode_cursor(_SORT, cursor)
            stmt = stmt.where(
                keyset_predicate(TeamRequest.created_at, TeamRequest.id, value, row_id)
            )
        rows = self.session.execute(
            stmt.order_by(TeamRequest.created_at.desc(), TeamRequest.id.desc()).limit(limit + 1)
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1][0]
            next_cursor = encode_cursor(_SORT, last.created_at, last.id)
        return TeamRequestList(
            items=[
                TeamRequestListItem(
                    id=request.id,
                    azienda=request.azienda,
                    origine=request.origine,
                    stato=request.stato,
                    created_at=request.created_at,
                    contacted_at=request.contacted_at,
                    talenti_totale=totale or 0,
                    talenti_si=si or 0,
                )
                for request, totale, si in page
            ],
            next_cursor=next_cursor,
        )

    def get(self, request_id: UUID) -> TeamRequestRead:
        row = self.session.get(TeamRequest, request_id)
        if row is None:
            raise NotFound(ENTITY, request_id)
        proposal = (
            self.session.get(TeamProposal, row.proposal_id) if row.proposal_id is not None else None
        )
        return TeamRequestRead(
            id=row.id,
            proposal=(
                TeamBuilder(self.session, None, self.settings).get(proposal.id, public=False)
                if proposal is not None
                else None
            ),
            riassunto=proposal.riassunto if proposal is not None else None,
            descrizione=proposal.descrizione if proposal is not None else None,
            origine=row.origine,
            azienda=row.azienda,
            email=row.email,
            telefono=row.telefono,
            user_id=row.user_id,
            company_id=row.company_id,
            stato=row.stato,
            note=row.note,
            talenti=self._talenti(row, proposal),
            contacted_at=row.contacted_at,
            closed_at=row.closed_at,
            created_at=row.created_at,
        )

    # ---- the admin's edits --------------------------------------------------------------

    def set_status(self, request_id: UUID, stato: str, admin_id: UUID) -> TeamRequestRead:
        """«Segna come contattata», «Chiudi», or back to `nuova` after a mis-click.
        `contacted_at` is set the first time the request is contacted and kept since;
        `closed_at` is when it was closed, and a request reopened has none."""
        if stato not in TEAM_REQUEST_STATES:
            raise ValidationFailed(ENTITY, "stato", f"uno fra {', '.join(TEAM_REQUEST_STATES)}")
        row = self._lock(request_id)
        before = row.stato
        if stato == before:
            self.session.rollback()
            return self.get(request_id)
        row.stato = stato
        if stato == "contattata" and row.contacted_at is None:
            row.contacted_at = self.now()
        row.closed_at = self.now() if stato == "chiusa" else None
        self.session.commit()
        self._record(row.id, admin_id, {"stato": before}, {"stato": stato})
        return self.get(request_id)

    def set_note(self, request_id: UUID, note: str | None, admin_id: UUID) -> TeamRequestRead:
        """The admin's own note; `None` or only spaces clears it."""
        cleaned = (note or "").strip() or None
        row = self._lock(request_id)
        before = row.note
        if cleaned == before:
            self.session.rollback()
            return self.get(request_id)
        row.note = cleaned
        self.session.commit()
        self._record(row.id, admin_id, {"note": before}, {"note": cleaned})
        return self.get(request_id)

    def set_summary(self, request_id: UUID, riassunto: str, admin_id: UUID) -> TeamRequestRead:
        """«Salva il riassunto»: the proposal's summary, the text the talents will read
        (D1), rewritten by the admin; recorded on the request, where it was edited. A
        summary that still names the company (`names_the_company`) is refused with the
        sentence the availability mail refuses it with, before anything is written."""
        row = self.session.get(TeamRequest, request_id)
        if row is None:
            raise NotFound(ENTITY, request_id)
        if row.proposal_id is None:
            raise InvalidState(NO_SUMMARY)
        if names_the_company(riassunto, row.azienda):
            raise InvalidState(NAMES_THE_COMPANY)
        proposal = self.session.get(
            TeamProposal, row.proposal_id, with_for_update=True, populate_existing=True
        )
        assert proposal is not None  # a foreign key
        before = proposal.riassunto
        if riassunto == before:
            self.session.rollback()
            return self.get(request_id)
        proposal.riassunto = riassunto
        self.session.commit()
        self._record(row.id, admin_id, {"riassunto": before}, {"riassunto": riassunto})
        return self.get(request_id)

    # ---- helpers -----------------------------------------------------------------------

    def _requestable(
        self, proposal_id: UUID, *, origine: str, user_id: UUID | None
    ) -> TeamProposal:
        """The proposal a request may be for: of the caller's origin, on the cloud the
        caller's own, and younger than a day."""
        proposal = self.session.get(TeamProposal, proposal_id)
        if (
            proposal is None
            or proposal.origine != origine
            or (origine == "cloud" and proposal.user_id != user_id)
            or proposal.created_at <= self.now() - REQUEST_MAX_AGE
        ):
            raise ValidationFailed(ENTITY, "proposal_id", PROPOSAL_REFUSED)
        return proposal

    def _members(self, proposal: TeamProposal) -> list[tuple[UUID, str]]:
        """Each member of the proposal's team, with the role proposed, who still has a
        row: a talent hard-deleted since the proposal cannot be asked, and is logged by
        position. A team with nobody in it (nobody fit, or the catalogue was empty) or
        nobody left has nobody to hire, and says so."""
        wanted = [(UUID(member["freelancer_id"]), member) for member in proposal.team]
        existing = set(
            self.session.scalars(
                select(Freelancer.id).where(
                    Freelancer.id.in_([freelancer_id for freelancer_id, _ in wanted])
                )
            )
        )
        members: list[tuple[UUID, str]] = []
        for freelancer_id, member in wanted:
            if freelancer_id not in existing:
                logger.info(
                    "team request on proposal %s: member %s is gone",
                    proposal.id,
                    member["posizione"],
                )
                continue
            members.append((freelancer_id, member["ruolo"]))
        if not members:
            raise ValidationFailed(ENTITY, "proposal_id", NOBODY_TO_HIRE)
        return members

    def _talenti(
        self, row: TeamRequest, proposal: TeamProposal | None
    ) -> list[TeamRequestTalentRead]:
        """The request's talents with their names and rates, in the proposal's order."""
        order = (
            {UUID(member["freelancer_id"]): member["posizione"] for member in proposal.team}
            if proposal is not None
            else {}
        )
        rows = self.session.execute(
            select(TeamRequestTalent, Freelancer, User)
            .join(Freelancer, Freelancer.id == TeamRequestTalent.freelancer_id)
            .join(User, User.id == Freelancer.user_id)
            .where(TeamRequestTalent.request_id == row.id)
        ).all()
        ordered = sorted(
            rows,
            key=lambda found: (
                order.get(found[0].freelancer_id, len(order) + 1),
                found[2].cognome,
                found[2].nome,
            ),
        )
        return [
            TeamRequestTalentRead(
                freelancer_id=talent.freelancer_id,
                nome=user.nome,
                cognome=user.cognome,
                ruolo=talent.ruolo,
                tariffa_giornaliera=freelancer.tariffa_giornaliera,
                fascia=band_for(freelancer.tariffa_giornaliera),
                mail_sent_at=talent.mail_sent_at,
                risposta=talent.risposta,
                risposta_at=talent.risposta_at,
            )
            for talent, freelancer, user in ordered
        ]

    def _lock(self, request_id: UUID) -> TeamRequest:
        row = self.session.get(
            TeamRequest, request_id, with_for_update=True, populate_existing=True
        )
        if row is None:
            self.session.rollback()
            raise NotFound(ENTITY, request_id)
        return row

    def _record(
        self, request_id: UUID, admin_id: UUID, before: dict[str, Any], after: dict[str, Any]
    ) -> None:
        AdminActionService(self.session).record(
            ENTITY, request_id, "overridden", admin_id, field_changes(before, after)
        )
