"""The freelancer card's tools: create it from a signup, read it with its thread, write what an
admin knows (status, notes, overrides, vetted), delete and restore it, and read or revert
its trail."""

from typing import Any

from mcp.server import MCPServer
from sqlalchemy.orm import Session

from rebase_core.admin_schemas import StatusChange
from rebase_core.card_person import person_differences, stored_person
from rebase_core.freelancer_schemas import FreelancerDraft, FreelancerOverride, FreelancerRead
from rebase_core.freelancers import FreelancerService
from rebase_core.notes import NoteService
from rebase_mcp.tools_common import (
    SOURCE,
    Deps,
    as_remote,
    integer,
    number,
    supplied_text,
    to_uuid,
)


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    @mcp.tool()
    def create_freelancer_from_signup(
        signup_id: str,
        first_name: str,
        last_name: str,
        sources: list[str],
        linkedin_url: str | None = None,
        job_title: str | None = None,
        daily_rate: str | None = None,
        remote: str | None = None,
        links: list[str] | None = None,
    ) -> dict[str, Any]:
        """Creates (or rewrites) the freelancer card of a signup with what can be found
        publicly about that person: first and last name required, then the LinkedIn profile,
        the job title as they state it, other links (website, GitHub, portfolio). Rate and
        work mode only if a public source says so, otherwise they stay empty
        along with the CV: the person fills them in from their own area. `sources` are the
        https addresses the information comes from, from one to ten, and they are saved as a
        note in the card's thread, signed by the admin behind the token. A card the person
        has already filled in is not touched: the tool refuses."""
        draft = FreelancerDraft(
            first_name=first_name,
            last_name=last_name,
            linkedin_url=linkedin_url,
            job_title=job_title,
            daily_rate=number(daily_rate, "daily_rate"),
            remote=as_remote(remote),
            links=links or [],
            sources=sources,
        )
        # `draft_from_signup` returns through `get()`, which now answers a
        # `FreelancerDetail` for the admin HTTP route (REB-284); this tool keeps the
        # plain `FreelancerRead` shape it always had. The card was just drafted, so it
        # has no logins, downloads or signup UTM to report yet, and the thread comes
        # back on the write anyway. `get_talent` is the tool for the full detail.
        return deps.run(
            lambda s: FreelancerRead.model_validate(
                FreelancerService(s)
                .draft_from_signup(to_uuid(signup_id, "signup_id"), draft, deps.admin().id, SOURCE)
                .model_dump()
            )
        )

    @mcp.tool()
    def get_freelancer(freelancer_id: str) -> dict[str, Any]:
        """A freelancer, by id, with `note`: the thread of the people who followed them, most
        recent first, each with `id`, author, date, `source` (`web`, `mcp`, `person`, `system`),
        last edit and `editable`. Deleted notes are not included. `phone` is
        the account's; `cv_contacts` is what the CV says about the person, read by
        Claude together with the anonymous card (first name, last name, email, phone, city,
        LinkedIn, other links, companies), `None` if the card has none yet (written before
        version 3 of the prompt or without a CV) and `cv_differences` where the CV and the
        profile say different things. This is personal data: for the admin only, never for
        anonymous cards, the cloud or the team builder."""

        def call(session: Session) -> dict[str, Any]:
            key = to_uuid(freelancer_id, "freelancer_id")
            read = FreelancerRead.model_validate(FreelancerService(session).get(key).model_dump())
            person = stored_person(session, key)
            return {
                **read.model_dump(mode="json"),
                "cv_contacts": person.model_dump(mode="json") if person is not None else None,
                "cv_differences": [
                    difference.model_dump(mode="json")
                    for difference in person_differences(session, person, key)
                ],
            }

        return deps.call(call)

    @mcp.tool()
    def set_freelancer_status(freelancer_id: str, status: str) -> dict[str, Any]:
        """Moves an application between `new`, `contacted`, `active` and `discarded`. It does
        not touch what the person wrote. The change is written to the log of
        `get_freelancer_audit` (`status_changed`, with before and after): for the reason use
        `add_freelancer_note`."""
        return deps.run(
            lambda s: FreelancerService(s).set_status(
                to_uuid(freelancer_id, "freelancer_id"),
                StatusChange(status=status),
                admin_id=deps.admin().id,
                source=SOURCE,
            )
        )

    @mcp.tool()
    def add_freelancer_note(freelancer_id: str, text: str) -> dict[str, Any]:
        """Adds a note to a freelancer's thread, without touching the status: a call made,
        an impression, something to remember. The note is already signed
        (the admin behind the token) and dated, with `source` `mcp`: the text must not start
        with a date or a time nor name who writes it. Up to 4000 characters, multiple
        lines allowed. It can be corrected with `update_note` and deleted with `delete_note`,
        and every addition is written to the log of `get_freelancer_audit`."""
        return deps.run(
            lambda s: NoteService(s).add(
                "freelancer",
                to_uuid(freelancer_id, "freelancer_id"),
                text,
                author_id=deps.admin().id,
                source=SOURCE,
            )
        )

    @mcp.tool()
    def update_note(note_id: str, text: str) -> dict[str, Any]:
        """Replaces the text of a freelancer's or company's note, by `id`
        (you find it in `note` of `get_freelancer` and `get_company`). Only notes written by
        an admin (`source` `web` or `mcp`, or without a source): the person's or the system's
        are events and are refused. Who edited it and when stays recorded, and the
        previous text is in the log (`note_edited`). The text must not start with a
        date or a time nor name who writes it: the note is already signed and dated."""
        return deps.run(
            lambda s: NoteService(s).update(
                to_uuid(note_id, "note_id"), text, admin_id=deps.admin().id, source=SOURCE
            )
        )

    @mcp.tool()
    def delete_note(note_id: str) -> dict[str, Any]:
        """Deletes a freelancer's or company's note, by `id`. It leaves the thread and
        `get_freelancer`/`get_company`, but the log keeps who deleted it and its
        text (`note_deleted`). The rules of `update_note` apply: the person's or the
        system's notes cannot be deleted."""

        def call(session: Session) -> dict[str, Any]:
            NoteService(session).delete(
                to_uuid(note_id, "note_id"), admin_id=deps.admin().id, source=SOURCE
            )
            return {"note": note_id, "deleted": True}

        return deps.call(call)

    @mcp.tool()
    def override_freelancer(
        freelancer_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        linkedin_url: str | None = None,
        daily_rate: str | None = None,
        job_title: str | None = None,
        remote: str | None = None,
        links: list[str] | None = None,
        status: str | None = None,
        filled_by: str | None = None,
        seniority: str | None = None,
        experience_years: str | None = None,
    ) -> dict[str, Any]:
        """Writes or clears any field of the profile: first name,
        last name and LinkedIn profile (on the shared identity in `users`), rate,
        job title, work mode, links, status and who filled in last
        (`person`/`admin`). Notes are not a field: they live in the thread (`add_freelancer_note`,
        `update_note`). Every omitted parameter stays as it was; an empty string clears
        the field where allowed (rate, job title, LinkedIn, work
        mode), elsewhere it is refused because the field cannot be left without a value.
        `links` replaces the whole list, `[]` clears it. `seniority` (junior, mid,
        senior or lead) and `experience_years` (0-60) correct the level the anonymous card
        declares: the admin's word wins over Claude's in every read
        (`list_talents`, `get_talent`, `get_freelancer_card`, the cloud, the team
        builder) and survives «Rigenera scheda»; the empty string removes it. Every
        real change is written to the log of `get_freelancer_audit`, with the value
        before and after; to undo it use `revert_freelancer_action`."""
        kwargs: dict[str, Any] = {}
        if first_name is not None:
            kwargs["first_name"] = supplied_text(first_name)
        if last_name is not None:
            kwargs["last_name"] = supplied_text(last_name)
        if linkedin_url is not None:
            kwargs["linkedin_url"] = supplied_text(linkedin_url)
        if daily_rate is not None:
            kwargs["daily_rate"] = None if daily_rate == "" else number(daily_rate, "daily_rate")
        if job_title is not None:
            kwargs["job_title"] = supplied_text(job_title)
        if remote is not None:
            kwargs["remote"] = supplied_text(remote)
        if links is not None:
            kwargs["links"] = links
        if status is not None:
            kwargs["status"] = supplied_text(status)
        if filled_by is not None:
            kwargs["filled_by"] = supplied_text(filled_by)
        if seniority is not None:
            kwargs["seniority_admin"] = supplied_text(seniority)
        if experience_years is not None:
            kwargs["experience_years_admin"] = integer(experience_years, "experience_years")
        override = FreelancerOverride(**kwargs)
        return deps.run(
            lambda s: FreelancerService(s).override(
                to_uuid(freelancer_id, "freelancer_id"), override, deps.admin().id
            )
        )

    @mcp.tool()
    def delete_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Deletes the card: it leaves «Talenti» and the freelancers list. Reversible,
        never a real deletion: `restore_freelancer` brings it back as it was."""
        return deps.run(
            lambda s: FreelancerService(s).soft_delete(
                to_uuid(freelancer_id, "freelancer_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def restore_freelancer(freelancer_id: str) -> dict[str, Any]:
        """Restores a deleted card: it returns to «Talenti» and the freelancers list.
        On an already active card nothing changes."""
        return deps.run(
            lambda s: FreelancerService(s).restore(
                to_uuid(freelancer_id, "freelancer_id"), deps.admin().id
            )
        )

    @mcp.tool()
    def set_freelancer_vetted(freelancer_id: str, vetted: bool) -> dict[str, Any]:
        """«Segna come verificato» (`vetted` true) or «Togli la verifica» (false, REB-518):
        the «Verificato da rebase» badge the talent cloud shows next to the profile.
        A mark already set, or already removed, writes nothing: the date stays that of the
        first verification, not of the last click."""
        return deps.run(
            lambda s: FreelancerService(s).set_vetted(
                to_uuid(freelancer_id, "freelancer_id"), vetted, deps.admin().id
            )
        )

    @mcp.tool()
    def get_freelancer_audit(freelancer_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Who edited, cleared, deleted or restored this card, and
        when: each entry with the administrator, the kind of action and, for a field edit,
        the value before and after. Also every time the anonymous card was
        rewritten (`card_regenerated`: outcome, reason if not written, `source`, cost, whether
        in bulk). It also works on a deleted card. Most
        recent first. Read only; to undo a field edit use
        `revert_freelancer_action` with the entry's id."""
        return deps.run_list(
            lambda s: FreelancerService(s).audit_timeline(
                to_uuid(freelancer_id, "freelancer_id"), limit
            )
        )

    @mcp.tool()
    def revert_freelancer_action(freelancer_id: str, action_id: str) -> dict[str, Any]:
        """Puts a field back to the value it had before a past edit, read from the
        log entry `action_id` (from `get_freelancer_audit`). Only on an entry that is a
        field edit, not on a deletion or a restore."""
        return deps.run(
            lambda s: FreelancerService(s).revert(
                to_uuid(freelancer_id, "freelancer_id"),
                to_uuid(action_id, "action_id"),
                deps.admin().id,
            )
        )
