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
(nobody fit, or the catalogue was empty) has nobody to hire, and says so. A cloud
proposal older than the caller's newest live grant was made while another company was
theirs, and the request would be filed for the wrong one: it is refused with a sentence
of its own, «La proposta è di un altro contesto: rigenerala.», since it is the caller's.

**The cloud files with no form** (REB-519, spec § 4.2): «Assumi team» on its own
proposal (`create_in_cloud`) and «Richiedi» on one card (`create_for_talent`, a request
of that talent alone and no proposal) take the company of the caller's grant and the
caller's own address and phone, none when they gave none. A card's talent must be one
the cloud shows (`cloud.cloud_card`), with the CV route's sentence otherwise.

**The summary is checked, then refused when it is written.** A proposal's summary that
names the company (`team_company_names`) is logged at creation, by the request's id
alone, since the visitor cannot change it; the admin's own edit (`set_summary`) that
still names it is refused, and so is the availability mail (`team_availability`, D1),
before the talents read it.

Asking the talents whether they are available, and their answers, are
`TeamAvailabilityService` (REB-892 split them out of this module).

Nothing here logs a name, an address, a phone or a text: the log lines carry ids.
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_REQUEST_SENT, Tracker
from rebase_core.audit import AdminActionService, field_changes, utcnow
from rebase_core.bands import band_for
from rebase_core.cloud import CloudCaller, TalentCloudService, cloud_card
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.mail import Mail
from rebase_core.mail_team import team_request_mail
from rebase_core.models import (
    TEAM_REQUEST_ORIGINS,
    TEAM_REQUEST_STATES,
    Company,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.team_builder import TeamBuilder
from rebase_core.team_catalogue import cloud_visible
from rebase_core.team_company_names import NAMES_THE_COMPANY, names_the_company
from rebase_core.team_schemas import (
    Card,
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
OTHER_CONTEXT = "La proposta è di un altro contesto: rigenerala."
# The proposal was made under a company whose talent cloud the caller no longer has open:
# revoked, the request deleted, or never theirs.
CLOUD_CLOSED_FOR_PROPOSAL = (
    "Il talent cloud con cui hai fatto questa proposta non è più aperto per te: "
    "rigenerala dall'accesso che hai adesso."
)
NO_SUMMARY = "Questa richiesta è per un talento solo: non ha un riassunto da modificare."
UNIQUE_PROPOSAL_INDEX = "uq_team_requests_proposal_id"
_SORT = SortSpec("created_at", "datetime")


def _is_card(card: Any) -> bool:
    """Whether a stored card still validates, as `TeamBuilder._read` asks before it
    shows a member."""
    try:
        Card.model_validate(card)
    except ValidationError:
        return False
    return True


def contactable(freelancer: Freelancer) -> bool:
    """Whether the availability mail may go to a talent: a card neither deleted nor
    `scartato`, whenever that happened. The send skips the others, and the page offers
    no send when only they are silent."""
    return freelancer.deleted_at is None and freelancer.stato != "scartato"


def _violates_unique_proposal(exc: IntegrityError) -> bool:
    """Whether the insert lost to another request of the same proposal: read from the
    driver's diagnostics, the way `matches._violates_match_id` reads its own key."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == UNIQUE_PROPOSAL_INDEX


class TeamRequestService:
    """The request's own mail to rebase is handed back by `create` for the caller to send
    after its response. `tracker` is `None` without PostHog. The talents' availability
    is `TeamAvailabilityService`'s."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings,
        tracker: Tracker | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.settings = settings
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
    ) -> tuple[TeamRequestRead, Mail]:
        """The request for `data.proposal_id`, with one talent per member of its team,
        and the mail to `settings.contracts_mail` (`request_mail`), built and not sent:
        the caller sends it after its own answer. The public page's, with the three
        contacts the visitor typed; the cloud's is `create_in_cloud`."""
        return self._create_for_proposal(
            data.proposal_id,
            origine=origine,
            azienda=data.azienda,
            email=str(data.email),
            telefono=data.telefono,
            user_id=user_id,
            company_id=company_id,
            rif=data.rif,
        )

    def create_in_cloud(
        self,
        proposal_id: UUID,
        *,
        caller: CloudCaller,
    ) -> tuple[TeamRequestRead, Mail]:
        """«Assumi team» in the talent cloud (spec § 4.2): `create` with no form, on a
        cloud proposal of the caller's own younger than a day, with the caller's address
        and their phone, `None` when they have none. It is filed for the company the
        proposal was made under (REB-578), by that company's name, and refused with
        `CLOUD_CLOSED_FOR_PROPOSAL` when the caller holds no live grant for it, or only
        one opened after the proposal was made (the grant it was made under was closed). A
        proposal from before the company was kept falls back to the caller's newest
        grant, and is refused with `OTHER_CONTEXT` when it is older than it."""
        proposal = self._requestable(proposal_id, origine="cloud", user_id=caller.user_id)
        if proposal.company_id is None:
            if caller.granted_at > proposal.created_at:
                raise ValidationFailed(ENTITY, "proposal_id", OTHER_CONTEXT)
            company_id, azienda = caller.company_id, caller.azienda
        else:
            company_id = proposal.company_id
            grant = TalentCloudService(self.session).live_for(caller.user_id, company_id)
            # A grant opened after the proposal is another grant of the same company: the
            # one the proposal was made under was closed, and reopening access does not
            # bring its proposals back.
            if grant is None or grant.granted_at > proposal.created_at:
                raise ValidationFailed(ENTITY, "proposal_id", CLOUD_CLOSED_FOR_PROPOSAL)
            company = self.session.get_one(Company, company_id)
            azienda = company.nome_azienda
        return self._file_proposal(
            proposal,
            origine="cloud",
            azienda=azienda,
            email=caller.email,
            telefono=caller.telefono,
            user_id=caller.user_id,
            company_id=company_id,
        )

    def create_for_talent(
        self,
        freelancer_id: UUID,
        *,
        azienda: str,
        email: str,
        telefono: str | None,
        user_id: UUID,
        company_id: UUID,
    ) -> TeamRequestRead:
        """«Richiedi» on one card of the talent cloud (spec § 4.2): a request of that
        talent alone, with no proposal and so no summary, in the role their card gives
        them. `NotInTheCloud` («Profilo non disponibile.») for a talent the cloud does
        not show. The mail to rebase is `request_mail`, which names the talent for a
        request with no summary, sent by the caller after its answer."""
        card = cloud_card(self.session, freelancer_id)
        row = TeamRequest(
            proposal_id=None,
            origine="cloud",
            azienda=azienda,
            email=email,
            telefono=telefono,
            user_id=user_id,
            company_id=company_id,
            stato="nuova",
        )
        self.session.add(row)
        self.session.flush()
        self.session.add(
            TeamRequestTalent(request_id=row.id, freelancer_id=freelancer_id, ruolo=card.ruolo)
        )
        self.session.commit()
        if self.tracker is not None:
            self.tracker.team_event(TEAM_REQUEST_SENT, {"origine": "cloud"})
        return self.get(row.id)

    def _create_for_proposal(
        self,
        proposal_id: UUID,
        *,
        origine: str,
        azienda: str,
        email: str,
        telefono: str | None,
        user_id: UUID | None,
        company_id: UUID | None,
        rif: str | None = None,
    ) -> tuple[TeamRequestRead, Mail]:
        if origine not in TEAM_REQUEST_ORIGINS:
            raise ValueError(f"unknown origin {origine!r}")
        proposal = self._requestable(proposal_id, origine=origine, user_id=user_id)
        return self._file_proposal(
            proposal,
            origine=origine,
            azienda=azienda,
            email=email,
            telefono=telefono,
            user_id=user_id,
            company_id=company_id,
            rif=rif,
        )

    def _file_proposal(
        self,
        proposal: TeamProposal,
        *,
        origine: str,
        azienda: str,
        email: str,
        telefono: str | None,
        user_id: UUID | None,
        company_id: UUID | None,
        rif: str | None = None,
    ) -> tuple[TeamRequestRead, Mail]:
        proposal_id = proposal.id
        members = self._members(proposal)
        row = TeamRequest(
            proposal_id=proposal.id,
            origine=origine,
            azienda=azienda,
            email=email,
            telefono=telefono,
            user_id=user_id,
            company_id=company_id,
            rif=rif,
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
            raise InvalidState(ALREADY_REQUESTED, proposal_id=str(proposal_id)) from exc
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
        row = self.lock(request_id)
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
        row = self.lock(request_id)
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
        # A foreign key: the proposal is there, and `get_one` says so if it is not.
        proposal = self.session.get_one(
            TeamProposal, row.proposal_id, with_for_update=True, populate_existing=True
        )
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
        self,
        proposal_id: UUID,
        *,
        origine: str,
        user_id: UUID | None,
    ) -> TeamProposal:
        """The proposal a request may be for: of the caller's origin, on the cloud the
        caller's own, and younger than a day. The cloud's company check comes after, in
        `create_in_cloud`, so its own sentences are only ever said of the caller's own
        proposal."""
        proposal = self.session.get(TeamProposal, proposal_id)
        if (
            proposal is None
            or proposal.errore is not None
            or proposal.origine != origine
            or (origine == "cloud" and proposal.user_id != user_id)
            or proposal.created_at <= self.now() - REQUEST_MAX_AGE
        ):
            raise ValidationFailed(ENTITY, "proposal_id", PROPOSAL_REFUSED)
        return proposal

    def _members(self, proposal: TeamProposal) -> list[tuple[UUID, str]]:
        """Each member of the proposal's team, with the role proposed, whom the
        proposal's own read still shows (`TeamBuilder._read`: `cloud_visible`, and a
        stored card that still validates as a `Card`): a talent deleted, turned down or
        left without a card since the proposal is not asked, and is logged by
        position, so the request holds the team the visitor saw. A team with nobody in
        it (nobody fit, or the catalogue was empty) or nobody left has nobody to hire,
        and says so."""
        wanted = [(UUID(member["freelancer_id"]), member) for member in proposal.team]
        rows = self.session.execute(
            cloud_visible(select(Freelancer.id, FreelancerCard.card)).where(
                Freelancer.id.in_([freelancer_id for freelancer_id, _ in wanted])
            )
        ).all()
        existing = {freelancer_id for freelancer_id, card in rows if _is_card(card)}
        members: list[tuple[UUID, str]] = []
        for freelancer_id, member in wanted:
            if freelancer_id not in existing:
                logger.info(
                    "team request on proposal %s: member %s is no longer in the catalogue",
                    proposal.id,
                    member["posizione"],
                )
                continue
            members.append((freelancer_id, member["ruolo"]))
        if not members:
            raise ValidationFailed(ENTITY, "proposal_id", NOBODY_TO_HIRE)
        return members

    def talent_rows(
        self, row: TeamRequest, proposal: TeamProposal | None, *, lock: bool = False
    ) -> list[tuple[TeamRequestTalent, Freelancer, User]]:
        """The request's talents with their cards and names, in the proposal's order;
        `lock` takes the talents' rows for the send that writes their tokens."""
        order = (
            {UUID(member["freelancer_id"]): member["posizione"] for member in proposal.team}
            if proposal is not None
            else {}
        )
        stmt = (
            select(TeamRequestTalent, Freelancer, User)
            .join(Freelancer, Freelancer.id == TeamRequestTalent.freelancer_id)
            .join(User, User.id == Freelancer.user_id)
            .where(TeamRequestTalent.request_id == row.id)
        )
        if lock:
            # Fresh rows, not the session's copies: an answer or a card deleted in
            # another session since must count here.
            stmt = stmt.with_for_update(of=TeamRequestTalent).execution_options(
                populate_existing=True
            )
        rows = self.session.execute(stmt).all()
        return sorted(
            rows,
            key=lambda found: (
                order.get(found[0].freelancer_id, len(order) + 1),
                found[2].cognome,
                found[2].nome,
            ),
        )

    def _talenti(
        self, row: TeamRequest, proposal: TeamProposal | None
    ) -> list[TeamRequestTalentRead]:
        """The request's talents with their names and rates, in the proposal's order."""
        ordered = self.talent_rows(row, proposal)
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
                contattabile=contactable(freelancer),
            )
            for talent, freelancer, user in ordered
        ]

    def lock(self, request_id: UUID) -> TeamRequest:
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
