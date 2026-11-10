"""The team requests and the talent cloud (REB-520, spec § 8): read the requests and the
proposals, correct a summary, write to the talents the admin asks to, open and close the
cloud."""

import logging
from typing import Any

from mcp.server import MCPServer
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from rebase_core.cloud import TalentCloudService
from rebase_core.team_availability import TeamAvailabilityService
from rebase_core.team_builder import (
    LIST_LIMIT_DEFAULT as TEAM_PROPOSAL_LIST_LIMIT_DEFAULT,
)
from rebase_core.team_builder import TeamBuilder
from rebase_core.team_requests import (
    LIST_LIMIT_DEFAULT as TEAM_REQUEST_LIST_LIMIT_DEFAULT,
)
from rebase_core.team_requests import TeamRequestService
from rebase_core.team_schemas import (
    TeamRequestNote,
    TeamRequestRead,
    TeamRequestSummary,
)
from rebase_mcp.tools_common import (
    Deps,
    field_refusal,
    to_uuid,
)

logger = logging.getLogger(__name__)


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def list_team_requests(
        status: str | None = None,
        origin: str | None = None,
        limit: int = TEAM_REQUEST_LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """«Richieste team» (spec § 3.5), newest first: company, origin, status, when it
        arrived, and how many talents were asked and how many have already answered yes.
        `status` and `origin` filter on the words those fields use; a word that is not one of
        them is refused naming the field. `next_cursor` is the opaque cursor of the next
        page, `None` on the last. Read-only."""
        return deps.run(
            lambda s: TeamRequestService(s, settings=deps.team_settings()).list_recent(
                status=status, origin=origin, limit=limit, cursor=cursor
            )
        )

    @mcp.tool()
    def list_team_proposals(
        origin: str | None = None,
        outcome: str | None = None,
        limit: int = TEAM_PROPOSAL_LIST_LIMIT_DEFAULT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """«Proposte»: every «Proponi il team» asked from the public page or the talent cloud
        (and, in the older rows, by an admin), newest first, even when no request was sent
        afterwards: the project description, how many people were asked, the note of a
        «Rigenera», who asked, the summary, how many members the proposal had and who they
        were (`team`: `position`, `freelancer_id`, proposed `role`, `first_name` and
        `last_name`, empty for a talent deleted in the meantime), and `request_id` of the
        «Assumi team» request if there is one. `error` is empty on a proposal that answered
        and the refusal code (`llm_unavailable`, `team_builder_busy`) on an attempt with no
        proposal. `origin` (`public`, `cloud`, `admin`) and `outcome` (`ok`, `error`) filter;
        a word that is not one of them is refused naming the field. `next_cursor` is the
        opaque cursor of the next page, `None` on the last. Read-only."""
        return deps.run(
            lambda s: TeamBuilder(s, None, deps.team_settings()).list_recent(
                origin=origin, outcome=outcome, limit=limit, cursor=cursor
            )
        )

    @mcp.tool()
    def get_team_request(request_id: str) -> dict[str, Any]:
        """One team request, by id (spec § 3.5): the proposal with the talents by name and their
        rate and band, the summary and the location the talents will read, the company's
        contacts, the status, the admin's note, and each talent's answer with its time.
        Read-only."""
        key = to_uuid(request_id, "request_id")
        return deps.run(lambda s: TeamRequestService(s, settings=deps.team_settings()).get(key))

    @mcp.tool()
    def set_team_request_summary(request_id: str, summary: str) -> dict[str, Any]:
        """«Salva il riassunto» (spec § 3.5): the text the talents will read in the availability
        email, rewritten here by the admin before writing to them. Refused with the same
        sentence as `contact_team_talents` if it still names the company: the summary must be
        corrected where it is written."""
        key = to_uuid(request_id, "request_id")
        change = TeamRequestSummary(summary=summary)
        return deps.run(
            lambda s: TeamRequestService(s, settings=deps.team_settings()).set_summary(
                key, change.summary, deps.admin().id
            )
        )

    @mcp.tool()
    def contact_team_talents(request_id: str, only_silent: bool = False) -> dict[str, Any]:
        """«Contatta i talenti» (`only_silent` false, the first time) or «Rimanda a chi non ha
        risposto» (true, spec § 3.6), for a team request that came from the site or the
        talent cloud: an email to each talent of the team with the summary, the proposed role
        and their own rate, and two links, «Sono disponibile» and «Non sono disponibile»,
        valid for thirty days. It really writes to people: use it only when the admin asks,
        never as a step of a search. Refused, with its sentence, for a closed request, a
        summary that still names the company, a second first time, or no talent left to write
        to; without a configured sending key it answers that sending is not active here."""
        key = to_uuid(request_id, "request_id")
        return deps.run(
            lambda s: TeamAvailabilityService(
                s, settings=deps.team_settings(), sender=deps.sender
            ).contact_talents(key, deps.admin().id, only_silent=only_silent)
        )

    @mcp.tool()
    def set_team_request_status(
        request_id: str, status: str, note: str | None = None
    ) -> dict[str, Any]:
        """«Segna come contattata», «Chiudi» (spec § 3.5), with an optional note for whoever
        will reread it; an omitted `note` leaves the note as it was. A refused note (over
        4000 characters, or with a null character) also leaves the status as it was."""
        key = to_uuid(request_id, "request_id")
        # The note is checked before anything is written: refused after `set_status`,
        # it would leave the state moved and the call answering an error.
        try:
            cleaned = TeamRequestNote(note=note) if note is not None else None
        except PydanticValidationError as exc:
            raise field_refusal(exc) from None

        def call(session: Session) -> TeamRequestRead:
            service = TeamRequestService(session, settings=deps.team_settings())
            read = service.set_status(key, status, deps.admin().id)
            if cleaned is not None:
                read = service.set_note(key, cleaned.note, deps.admin().id)
            return read

        return deps.run(call)

    @mcp.tool()
    def grant_talent_cloud(company_id: str) -> dict[str, Any]:
        """«Apri il talent cloud» (spec § 4.1): opens access to the private cloud for this
        request's contact person, and sends them «Il talent cloud di rebase è aperto per …»
        when it is a new grant; on one already live for this company and this person it
        answers that one, sending nothing else. `mail_sent` says whether the email went out;
        without a configured sending key the cloud still opens and nobody receives it."""

        def call(session: Session) -> dict[str, Any]:
            read, mail = TalentCloudService(session, deps.team_settings()).grant(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
            mail_sent = False
            if mail is not None:
                if deps.sender is None:
                    logger.info("talent cloud grant %s: no mail sender, not mailed", read.id)
                else:
                    mail_sent = deps.sender.send(mail)
                    if not mail_sent:
                        logger.warning(
                            "talent cloud grant %s: the mail was refused by the provider",
                            read.id,
                        )
            return {**read.model_dump(mode="json"), "mail_sent": mail_sent}

        return deps.call(call)

    @mcp.tool()
    def revoke_talent_cloud(company_id: str) -> dict[str, Any]:
        """«Revoca il talent cloud» (spec § 4.1): closes the live grant of this request, and
        only this one; another company of the same person stays as it was and its cloud stays
        open. Refused, with its sentence, when there is nothing live to close."""
        return deps.run(
            lambda s: TalentCloudService(s).revoke(
                to_uuid(company_id, "company_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def list_talent_cloud_grants() -> list[dict[str, Any]]:
        """Every talent cloud grant, live or closed, newest first, up to 200 (spec § 4.1, not
        paginated because there are few for a while): for each, who opened it and when, and
        who closed it, if anyone, and when. Read-only."""
        return deps.run_list(lambda s: TalentCloudService(s).list())
