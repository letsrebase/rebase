"""The team builder's engine (REB-511, spec § 3.3, § 3.4): a project's description, and
the catalogue of every talent's anonymous card, go to Claude; the answer comes back as a
team of freelancers, each with a role, a reason and a price band, and is written as a
`team_proposals` row that a visitor may later turn into a request (C5).

The engine is one of four modules (REB-892): `team_catalogue` (who is in the catalogue,
and its lines), `team_prompt` (what Claude is asked and what it may answer),
`team_public` (what a public read withholds of a place) and this one, which calls the
model, checks the answer and writes and reads the rows. The caps and the slots that
gate a proposal are `team_caps` and `team_slots`.

**The catalogue is the prompt's cached prefix.** The service keeps the positions of its
lines for the call and maps the answer back through them.

**The answer is checked, not trusted.** A refusal, a cut answer or a body that is not the
shape is `LlmUnavailable`, and no proposal is written. A position the catalogue does not
hold, or one already in the team, drops that line, and so does a member who left the
catalogue while Claude was writing. What was dropped is logged by position, never by
anything the model wrote. Where a person works is not a check but the model's judgement
(REB-598): on a need on site it prefers whoever is hybrid or on site and near the place,
and when nobody such fits the skills it proposes the best fits anyway and says in the
summary that they work remotely, from elsewhere or have not said, so the company decides;
the member's work mode is on every read for the same reason. That sentence names no place
of a person: the public read withholds a summary that names a member's card place the
description itself does not (`team_public.public_summary`), the way it withholds such a
motivazione. When the checks drop everyone the model chose, its summary describes a team
that is not there, and the hub's own sentence (`NO_FIT_SENTENCE`) takes its place; an
empty catalogue asks nobody and answers that sentence at once, for free.

**The economics are the hub's.** Each member's band comes from their own rate
(`bands.py`), read when the proposal is read, and the team's bands are their sum; the
model sees a band in the catalogue and never a rate.

**Every ask is a row, answered or not** (0028, DECISIONS.md 2026-09-29). Ivan: «è
importante salvare tutte le richieste dopo il click proponi team anche se non ancora
finalizzate/inviate così raccogliamo metriche di uso». A proposal Claude answered was
always a row; an ask that ended in `LlmUnavailable`, or that the caps refused
(`record_refusal`), is one too, with `error` set, an empty summary and nobody in it,
so «Proposte» in the admin area counts what people asked for and never filed. Such a
row is an attempt, not a proposal: its id never leaves the hub, `get` answers
`NotFound` for it, «Rigenera» and «Assumi team» refuse it, and the daily cap does not
count it (`NO_CALL_MODEL`). The `LlmUnavailable` still reaches the caller after the row.

The Claude call holds no transaction: the catalogue is read and the session committed
before it, and the row is written in a new one after. Nothing here logs the
description, the note, the summary or the answer: a proposal row keeps the tokens, not
the prompt.
"""

import logging
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_PROPOSAL_GENERATED, Tracker
from rebase_core.audit import utcnow
from rebase_core.bands import DAYS_PER_MONTH, Band, band_for, team_bands
from rebase_core.card_level import card_read
from rebase_core.config import Settings
from rebase_core.errors import (
    DomainError,
    LlmCapReached,
    LlmNotConfigured,
    LlmUnavailable,
    NotFound,
    TeamBuilderBusy,
    TeamBuilderOff,
    ValidationFailed,
)
from rebase_core.llm import LlmCall, LlmResponse
from rebase_core.llm_prompt import read_response
from rebase_core.models import (
    TEAM_PROPOSAL_ERRORS,
    TEAM_PROPOSAL_ORIGINS,
    Freelancer,
    FreelancerCard,
    TeamProposal,
    TeamRequest,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.team_catalogue import catalogue_lines, cloud_visible
from rebase_core.team_prompt import (
    TASK,
    ProposalAnswer,
    ProposedMember,
    proposal_prompt,
)
from rebase_core.team_public import (
    public_card,
    public_place,
    public_reason,
    public_summary,
)
from rebase_core.team_schemas import (
    Card,
    TeamEconomics,
    TeamMemberRead,
    TeamPlace,
    TeamProposalCreate,
    TeamProposalList,
    TeamProposalListItem,
    TeamProposalListMember,
    TeamProposalRead,
)
from rebase_core.team_sentences import BUSY_SENTENCE, NO_CALL_MODEL

logger = logging.getLogger(__name__)

ENTITY = "team_proposal"
OFF_SENTENCE = "Il team builder è spento."
# The summary of a proposal with nobody in it, when the hub rather than the model is the
# one who knows (spec § 3.2: «an empty team and the summary's sentence»): the catalogue
# is empty, so nobody was asked, or the checks dropped everyone the model chose, so its
# own summary describes a team that is not there.
NO_FIT_SENTENCE = "Al momento nessun profilo corrisponde alla richiesta."
# How long a proposal can be regenerated (spec § 3.2): the same day a visitor asked it.
PREVIOUS_MAX_AGE = timedelta(days=1)
_PREVIOUS_REFUSED = "la proposta da rigenerare non esiste o è scaduta"
# The admin's «Proposte» (0028): a page at a time, newest first, like «Richieste team».
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200
# What `outcome` filters the list by: the proposals that answered, or the attempts.
LIST_OUTCOMES = ("ok", "error")
_SORT = SortSpec("created_at", "datetime")
# A catalogue id as the model writes it; the number is checked against the catalogue.
_POSITION = re.compile(r"t([0-9]{1,6})")

# ---- the engine ------------------------------------------------------------------------


class TeamBuilder:
    """`llm` is `None` when no model is configured for the `team_proposal` task
    (`call_for_task`), and then, as with the switch off, `propose` refuses with
    `TeamBuilderOff`; `get` needs neither."""

    def __init__(
        self,
        session: Session,
        llm: LlmCall | None,
        settings: Settings,
        *,
        tracker: Tracker | None = None,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self.session = session
        self.llm = llm
        self.settings = settings
        self.tracker = tracker
        self.now = now

    def propose(
        self,
        data: TeamProposalCreate,
        *,
        origin: str,
        user_id: UUID | None,
        company_id: UUID | None = None,
        now: datetime | None = None,
    ) -> TeamProposalRead:
        """A new proposal for `data`, written and read back: public (no ids, no card
        `location`) when `origin` is `public`, whole for the cloud and the admin. The
        concurrency cap and the daily cap are the route's (C5). `now` is the instant the
        proposal is asked and stamped at, one read of the clock for the whole of it: the
        route passes the one its daily cap counted from, so a call to Claude that ends
        past midnight in Rome is not checked on one day and written on the next
        (REB-581). Without it, the clock is read once here. `company_id` is the company
        of the grant a cloud proposal is made under (REB-578), kept on the row so
        «Assumi team» files under it; `None` for the public page and the admin."""
        if not self.settings.team_builder_enabled or self.llm is None:
            raise TeamBuilderOff(OFF_SENTENCE)
        if origin not in TEAM_PROPOSAL_ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        at = now if now is not None else self.now()
        previous = self._previous(data.previous_id, origin=origin, user_id=user_id, at=at)
        catalogue, positions = catalogue_lines(self.session)
        if not positions:
            # Nobody has a card: nothing for Claude to choose from, and nothing to pay
            # for. The row is still written, so «Rigenera» and a later read find it.
            logger.info("team proposal: the catalogue is empty, no call made")
            return self._write(
                data,
                previous,
                origin=origin,
                user_id=user_id,
                company_id=company_id,
                summary=NO_FIT_SENTENCE,
                location={"locale": False, "dove": None},
                team=[],
                bands=[],
                response=None,
                at=at,
            )
        request = proposal_prompt(catalogue, data, previous, positions=positions)
        # Claude takes seconds to tens of seconds: the transaction ends here, so no
        # pooled connection waits on it. The row is written in the next one.
        self.session.commit()
        try:
            completion = read_response(TASK, self.llm.complete(request), ProposalAnswer)
            response = completion.response
            answer = completion.required("team proposal")
        except LlmUnavailable as exc:
            # The ask is kept all the same (0028): what was written, by whom, and that
            # Claude did not answer it. The caller still gets the refusal, and gets it
            # even when the database refuses the row: the record is a metric, the
            # refusal is the product.
            try:
                self._write_attempt(
                    data,
                    previous,
                    origin=origin,
                    user_id=user_id,
                    company_id=company_id,
                    error=exc,
                    at=at,
                )
            except Exception:
                self.session.rollback()
                logger.exception("team proposal attempt not kept")
            # What «Modelli AI» says about the task is for an admin; a visitor gets the
            # sentence of the switch off or of the daily cap, the two states these are.
            if isinstance(exc, LlmNotConfigured):
                raise TeamBuilderOff(OFF_SENTENCE) from None
            if isinstance(exc, LlmCapReached):
                raise TeamBuilderBusy(BUSY_SENTENCE) from None
            raise
        team, bands = self._team(answer, positions)
        summary = answer.summary
        if answer.team and not team:
            # The model's summary describes the team it chose; nobody of it is left.
            logger.warning("team proposal: every member dropped, the summary is the hub's")
            summary = NO_FIT_SENTENCE
        return self._write(
            data,
            previous,
            origin=origin,
            user_id=user_id,
            company_id=company_id,
            summary=summary,
            location=answer.location.model_dump(),
            team=team,
            bands=bands,
            response=response,
            at=at,
        )

    def get(self, proposal_id: UUID, *, public: bool) -> TeamProposalRead:
        row = self.session.get(TeamProposal, proposal_id)
        # An attempt that failed is not a proposal anybody was handed (0028).
        if row is None or row.error is not None:
            raise NotFound(ENTITY, proposal_id)
        return self._read(row, public=public)

    def get_public(self, proposal_id: UUID, *, now: datetime | None = None) -> TeamProposalRead:
        """The public page opened on a proposal by its id (REB-675, `/hub/team?proposal=`):
        the public read of a `public` proposal younger than `PREVIOUS_MAX_AGE`, the
        window «Rigenera» and «Assumi team» give it, and `NotFound` for anything else,
        an attempt, a cloud's or an admin's proposal, one older than a day and an unknown
        id alike, so the answer never says which proposals exist. The id is the
        capability (spec § 3.2): random bits the visitor was handed, and all this read
        trusts."""
        at = now if now is not None else self.now()
        row = self.session.get(TeamProposal, proposal_id)
        if (
            row is None
            or row.error is not None
            or row.origin != "public"
            or row.created_at <= at - PREVIOUS_MAX_AGE
        ):
            raise NotFound(ENTITY, proposal_id)
        return self._read(row, public=True)

    def record_refusal(
        self,
        data: TeamProposalCreate,
        *,
        origin: str,
        user_id: UUID | None,
        error: DomainError,
        company_id: UUID | None = None,
        now: datetime | None = None,
    ) -> None:
        """The ask the route refused before this engine ran (`TeamBuilderBusy`: every
        slot taken, or the day's proposals spent), kept as an attempt row (0028) so it is
        counted with the rest. `previous_id` is linked when it names a proposal the
        caller could regenerate and left `NULL` otherwise: a refusal is not the place to
        say which proposals exist. Never raises past the write: the caller re-raises the
        refusal it was given."""
        if origin not in TEAM_PROPOSAL_ORIGINS:
            raise ValueError(f"unknown origin {origin!r}")
        at = now if now is not None else self.now()
        try:
            previous = self._previous(data.previous_id, origin=origin, user_id=user_id, at=at)
        except ValidationFailed:
            previous = None
        self._write_attempt(
            data,
            previous,
            origin=origin,
            user_id=user_id,
            company_id=company_id,
            error=error,
            at=at,
        )

    def list_recent(
        self,
        *,
        origin: str | None = None,
        outcome: str | None = None,
        limit: int = LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> TeamProposalList:
        """«Proposte» in the admin area (0028): every ask, newest first, by cursor, with
        the «Assumi team» filed on it when there is one. `origin` filters by who asked
        and `outcome` by whether it answered (`ok`) or not (`error`); a word that is not
        one of theirs is a 422 naming the field. The rows are read as written, never
        through `_read`: the list is about what was asked, not about who is still in
        the catalogue. Each member is named all the same (REB-607), with one query for
        the page's talents: a name is what the admin follows to the profile, and a
        talent removed with «Elimina» since, whose page no longer opens, stays in the
        row by id, unnamed."""
        if origin is not None and origin not in TEAM_PROPOSAL_ORIGINS:
            raise ValidationFailed(ENTITY, "origin", f"uno fra {', '.join(TEAM_PROPOSAL_ORIGINS)}")
        if outcome is not None and outcome not in LIST_OUTCOMES:
            raise ValidationFailed(ENTITY, "outcome", f"uno fra {', '.join(LIST_OUTCOMES)}")
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        stmt = select(TeamProposal, TeamRequest.id).outerjoin(
            TeamRequest, TeamRequest.proposal_id == TeamProposal.id
        )
        if origin is not None:
            stmt = stmt.where(TeamProposal.origin == origin)
        if outcome == "ok":
            stmt = stmt.where(TeamProposal.error.is_(None))
        elif outcome == "error":
            stmt = stmt.where(TeamProposal.error.is_not(None))
        if cursor:
            value, row_id = decode_cursor(_SORT, cursor)
            stmt = stmt.where(
                keyset_predicate(TeamProposal.created_at, TeamProposal.id, value, row_id)
            )
        rows = self.session.execute(
            stmt.order_by(TeamProposal.created_at.desc(), TeamProposal.id.desc()).limit(limit + 1)
        ).all()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1][0]
            next_cursor = encode_cursor(_SORT, last.created_at, last.id)
        names = self._names(
            [UUID(member["freelancer_id"]) for row, _ in page for member in row.team]
        )
        return TeamProposalList(
            items=[
                TeamProposalListItem(
                    id=row.id,
                    description=row.description,
                    people=row.people,
                    note=row.note,
                    previous_id=row.previous_id,
                    origin=row.origin,
                    user_id=row.user_id,
                    error=row.error,
                    summary=row.summary if row.error is None else None,
                    members=len(row.team),
                    team=[_list_member(member, names) for member in row.team],
                    request_id=request_id,
                    created_at=row.created_at,
                )
                for row, request_id in page
            ],
            next_cursor=next_cursor,
        )

    def _names(self, ids: list[UUID]) -> dict[UUID, tuple[str, str]]:
        """The name of each talent still on file, in one query for a page of the list.
        One removed with «Elimina» (`deleted_at` set, the product's only delete) is
        absent on purpose: the admin's page answers 404 for them, so a name would be a
        link to nowhere, and the caller names nobody for them."""
        if not ids:
            return {}
        rows = self.session.execute(
            select(Freelancer.id, User.nome, User.last_name)
            .join(User, User.id == Freelancer.user_id)
            .where(Freelancer.id.in_(list(set(ids))), Freelancer.deleted_at.is_(None))
        ).all()
        return {freelancer_id: (nome, last_name) for freelancer_id, nome, last_name in rows}

    # ---- helpers -----------------------------------------------------------------------

    def _write(
        self,
        data: TeamProposalCreate,
        previous: TeamProposal | None,
        *,
        origin: str,
        user_id: UUID | None,
        company_id: UUID | None,
        summary: str,
        location: dict[str, Any],
        team: list[dict[str, Any]],
        bands: list[Band | None],
        response: LlmResponse | None,
        at: datetime,
    ) -> TeamProposalRead:
        """The row, the event and the read: `response` is the call's usage, `None` when
        no call was made (every count 0, `model` empty); `at` is the proposal's instant."""
        day, month = team_bands(bands)
        input_tokens = response.input_tokens if response is not None else 0
        # Thinking is billed as output and the row has always counted it there.
        output_tokens = (
            response.output_tokens + response.thinking_tokens if response is not None else 0
        )
        row = TeamProposal(
            description=data.description,
            note=data.note,
            previous_id=previous.id if previous is not None else None,
            summary=summary,
            location=location,
            team=team,
            economics=_economics(day, month, dump=True),
            model=response.model if response is not None else NO_CALL_MODEL,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=response.cache_read_tokens if response is not None else 0,
            origin=origin,
            user_id=user_id,
            company_id=company_id,
            people=data.people,
            created_at=at,
        )
        self.session.add(row)
        self.session.commit()
        if self.tracker is not None:
            self.tracker.team_event(
                TEAM_PROPOSAL_GENERATED,
                {
                    "origin": origin,
                    "people": len(team),
                    "persone_richieste": data.people,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
        return self._read(row, public=origin == "public")

    def _write_attempt(
        self,
        data: TeamProposalCreate,
        previous: TeamProposal | None,
        *,
        origin: str,
        user_id: UUID | None,
        company_id: UUID | None,
        error: DomainError,
        at: datetime,
    ) -> None:
        """The row of an ask that got no proposal (0028): the ask as it came, `error`
        the refusal's code, an empty summary, nobody in the team, no call counted. No
        event: `team_proposal_generated` counts proposals, and this is not one. A
        refusal whose code is not one the table takes is a bug in the caller."""
        if error.code not in TEAM_PROPOSAL_ERRORS:
            raise ValueError(f"not an attempt's error: {error.code!r}")
        row = TeamProposal(
            description=data.description,
            note=data.note,
            previous_id=previous.id if previous is not None else None,
            summary="",
            location={"locale": False, "dove": None},
            team=[],
            economics=_economics(None, None, dump=True),
            model=NO_CALL_MODEL,
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            origin=origin,
            user_id=user_id,
            company_id=company_id,
            people=data.people,
            error=error.code,
            created_at=at,
        )
        self.session.add(row)
        self.session.commit()
        logger.info("team proposal attempt kept: %s", error.code)

    def _previous(
        self, previous_id: UUID | None, *, origin: str, user_id: UUID | None, at: datetime
    ) -> TeamProposal | None:
        """The proposal «Rigenera» replaces: of the caller's own origin, and on the
        cloud the caller's own, younger than a day. One sentence for every refusal, so
        the answer does not say which proposals exist."""
        if previous_id is None:
            return None
        row = self.session.get(TeamProposal, previous_id)
        if (
            row is None
            or row.error is not None
            or row.origin != origin
            or (origin == "cloud" and row.user_id != user_id)
            or row.created_at <= at - PREVIOUS_MAX_AGE
        ):
            raise ValidationFailed(ENTITY, "previous_id", _PREVIOUS_REFUSED)
        return row

    def _team(
        self, answer: ProposalAnswer, positions: Sequence[UUID]
    ) -> tuple[list[dict[str, Any]], list[Band | None]]:
        """The answer's members as the row stores them, numbered again from 1 after
        the drops, and each one's band from their current rate."""
        chosen: list[tuple[UUID, ProposedMember]] = []
        for member in answer.team:
            match = _POSITION.fullmatch(member.id)
            if match is None:
                logger.warning("team proposal: a member dropped: its id is not a position")
                continue
            index = int(match.group(1))
            if not 1 <= index <= len(positions):
                logger.warning("team proposal: %r dropped: not in the catalogue", member.id)
                continue
            freelancer_id = positions[index - 1]
            if any(freelancer_id == seen for seen, _ in chosen):
                logger.warning("team proposal: %r dropped: proposed twice", member.id)
                continue
            chosen.append((freelancer_id, member))

        live = self._live([freelancer_id for freelancer_id, _ in chosen])
        team: list[dict[str, Any]] = []
        bands: list[Band | None] = []
        for freelancer_id, member in chosen:
            if freelancer_id not in live:
                logger.info("team proposal: %r dropped: left the catalogue", member.id)
                continue
            team.append(
                {
                    "posizione": len(team) + 1,
                    "freelancer_id": str(freelancer_id),
                    "role": member.role,
                    "reasoning": member.reasoning,
                    "week_days": member.week_days,
                }
            )
            bands.append(band_for(live[freelancer_id]))
        return team, bands

    def _live(self, ids: list[UUID]) -> dict[UUID, Decimal | None]:
        """The rate of whoever is still in the catalogue now, after the call: a talent
        deleted, turned down or left without a card meanwhile is not proposed."""
        if not ids:
            return {}
        rows = self.session.execute(
            cloud_visible(select(Freelancer.id, Freelancer.daily_rate)).where(
                Freelancer.id.in_(ids)
            )
        ).all()
        return {freelancer_id: rate for freelancer_id, rate in rows}

    def _read(self, row: TeamProposal, *, public: bool) -> TeamProposalRead:
        """The row as a page reads it, with each member's card, work mode and band as
        they are now (§ 2.1), and the team's bands summed from those. Only who is still
        `cloud_visible` is read: a member deleted, turned down or left without a card
        since the proposal is left out, and so is one whose stored card no longer
        validates, logged by position; the row keeps them all. The cloud's and the
        admin's read name each member (§ 4.2); the public read names nobody, and it
        withholds the card's `location`, and with it any field of the card, a motivazione
        and the summary that name that place (`public_card`, `public_reason`,
        `public_summary`), and a `location.dove` the visitor did not write
        (`public_place`)."""
        ids = [UUID(member["freelancer_id"]) for member in row.team]
        found: dict[
            UUID, tuple[str | None, Decimal | None, Any, str, str, str | None, int | None]
        ] = {}
        if ids:
            rows = self.session.execute(
                cloud_visible(
                    select(
                        Freelancer.id,
                        Freelancer.remote,
                        Freelancer.daily_rate,
                        FreelancerCard.card,
                        User.nome,
                        User.last_name,
                        Freelancer.seniority_admin,
                        Freelancer.experience_years_admin,
                    ).join(User, User.id == Freelancer.user_id)
                ).where(Freelancer.id.in_(ids))
            ).all()
            found = {
                row.id: (
                    row.remote,
                    row.daily_rate,
                    row.card,
                    row.nome,
                    row.last_name,
                    row.seniority_admin,
                    row.experience_years_admin,
                )
                for row in rows
            }
        members: list[TeamMemberRead] = []
        cards: list[Card] = []
        for stored in row.team:
            freelancer_id = UUID(stored["freelancer_id"])
            if freelancer_id not in found:
                logger.info(
                    "team proposal %s: member %s left out of the read: not in the catalogue",
                    row.id,
                    stored["posizione"],
                )
                continue
            remote, rate, card, nome, last_name, seniority_admin, experience_years_admin = found[
                freelancer_id
            ]
            try:
                scheda = card_read(card, seniority_admin, experience_years_admin)
            except ValidationError:
                logger.warning(
                    "team proposal %s: member %s left out of the read: the card is not a card",
                    row.id,
                    stored["posizione"],
                )
                continue
            cards.append(scheda)
            members.append(
                TeamMemberRead(
                    posizione=stored["posizione"],
                    freelancer_id=None if public else freelancer_id,
                    nome=None if public else nome,
                    last_name=None if public else last_name,
                    role=stored["role"],
                    reasoning=(
                        public_reason(stored["reasoning"], scheda)
                        if public
                        else stored["reasoning"]
                    ),
                    week_days=stored["week_days"],
                    card=public_card(scheda, stored["role"]) if public else scheda,
                    mode=remote,
                    band=band_for(rate),
                )
            )
        day, month = team_bands([member.band for member in members])
        return TeamProposalRead(
            id=row.id,
            description=row.description,
            people=row.people,
            summary=(
                public_summary(
                    row.id, row.summary, row.location.get("dove"), row.description, cards
                )
                if public
                else row.summary
            ),
            location=TeamPlace.model_validate(
                public_place(row.location, row.description) if public else row.location
            ),
            team=members,
            economics=TeamEconomics.model_validate(_economics(day, month)),
            previous_id=row.previous_id,
            origin=row.origin,
            created_at=row.created_at,
        )


def _economics(day: Band | None, month: Band | None, *, dump: bool = False) -> dict[str, Any]:
    """§ 3.3's `economics`: the team's bands per day and per month at 22 days, as models
    for a read or as plain JSON for the row."""
    if dump:
        return {
            "day": day.model_dump() if day is not None else None,
            "month": month.model_dump() if month is not None else None,
            "days_month": DAYS_PER_MONTH,
        }
    return {"day": day, "month": month, "days_month": DAYS_PER_MONTH}


def _list_member(
    stored: dict[str, Any], names: dict[UUID, tuple[str, str]]
) -> TeamProposalListMember:
    """A stored member as «Proposte» lists it: named when still on file, by id alone
    once removed."""
    freelancer_id = UUID(stored["freelancer_id"])
    nome, last_name = names.get(freelancer_id, (None, None))
    return TeamProposalListMember(
        posizione=stored["posizione"],
        freelancer_id=freelancer_id,
        role=stored["role"],
        nome=nome,
        last_name=last_name,
    )
