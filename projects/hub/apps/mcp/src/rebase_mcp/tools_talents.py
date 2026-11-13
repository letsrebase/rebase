"""The «Talenti» tools: the list an agent reads to rank people (`list_talents`), the one row
(`get_talent`), and the duplicates (`list_duplicates`, `merge_freelancers`)."""

from typing import Any
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import CallToolResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.duplicates import duplicate_list
from rebase_core.errors import NotFound
from rebase_core.freelancers import FreelancerService
from rebase_core.merge import PersonMergeService
from rebase_core.models import Freelancer, Signup, User
from rebase_core.talents import TalentsService, signup_read
from rebase_mcp.talents_view import LIST_LIMIT_DEFAULT as TALENTS_LIMIT_DEFAULT
from rebase_mcp.talents_view import LIST_LIMIT_MAX as TALENTS_LIMIT_MAX
from rebase_mcp.talents_view import compact_result, row_fields, talents_page
from rebase_mcp.tools_common import (
    EMAILS_READ,
    READ_ENTITY,
    Deps,
    bounded,
    moment,
    number,
    read_entity_id,
    search_term,
    skill_terms,
    to_uuid,
)


def _without_addresses(page: dict[str, Any]) -> dict[str, Any]:
    """The duplicates' page for an agent: the pairs by name and id, never an address."""
    for key in ("mergeable", "to_review"):
        for pair in page[key]:
            for side in ("survivor", "absorbed"):
                pair[side].pop("email", None)
                pair[side].pop("user_id", None)
    return page


def register(mcp: MCPServer, deps: Deps) -> None:
    """Declares this family's tools on `mcp`."""

    def _freelancer_talent(session: Session, freelancer_id: UUID) -> dict[str, Any]:
        """`get_talent`'s answer for a card: `FreelancerDetail`
        (`FreelancerService.get`) with the row `list_talents` shows for the same person
        laid over it (`TalentsService.row_for_freelancer`, REB-798): the same `origin`
        (how the row was born: `wizard` or `admin`), the card's own fields and
        `has_anonymous_card`, which the detail schema does not carry. What the detail
        called `origin`, the marketing page, is `origin_page` here, as it is a
        filter of the list."""
        detail = FreelancerService(session, deps.settings, deps.http).get(freelancer_id)
        row = TalentsService(session).row_for_freelancer(freelancer_id)
        shown = detail.model_dump(mode="json")
        return {**shown, **row.model_dump(mode="json"), "origin_page": shown["origin"]}

    @mcp.tool()
    def list_talents(
        limit: int = TALENTS_LIMIT_DEFAULT,
        status: str | None = None,
        q: str | None = None,
        cursor: str | None = None,
        job_title: str | None = None,
        remote: str | None = None,
        min_rate: str | None = None,
        max_rate: str | None = None,
        origin: str | None = None,
        origin_page: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        with_logins: bool | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        skills: list[str] | None = None,
        location: str | None = None,
        seniority: list[str] | None = None,
        min_years: int | None = None,
        max_years: int | None = None,
        industries: list[str] | None = None,
        languages: list[str] | None = None,
        card: str | None = None,
        sort: str | None = None,
        fields: list[str] | None = None,
    ) -> CallToolResult:
        """The talents as the «Talenti» screen of the admin area shows them: the freelancer
        cards and the sign-ups without a card in a single list, `status` `lead` for the
        latter. Each row is short (`id`, `first_name`, `last_name`, `status` and, from
        Claude's anonymous card, `role`, `seniority`, `experience_years`, `daily_rate`,
        `location`, the first five `skills` and the shortened `profile_summary`; a key with no
        value is missing): to judge who fits a request this list is enough, and a page of
        fifty rows weighs about 25 KB. `seniority` and `experience_years` are the ones an
        admin corrected (`override_freelancer`) if there are any; without `experience_years`
        and with `seniority_uncertain` true the CV dates no role and the level is only a
        reading of the responsibilities. `fields` chooses the fields by name and gives only
        those (with `id`), whole: `["email", "industries", "languages", "band"]`, with
        `skills` and `profile_summary` whole if you name them; `["all"]` gives the full row,
        with `email`, `linkedin_url`, `origin`, `utm_source`, `created_at`, `vetted_at`,
        `has_anonymous_card`, `job_title`; a row carries only the login `email`, the other ways
        to reach the person (`phone`, `contact_points`) are in `get_talent`. Newest first, or
        most relevant first when `q` narrows. A row's `id` goes to `get_talent`, which for a
        lead answers the row itself; a lead's card is created with `create_freelancer_from_signup`.

        Filters, all together: `q` searches first name, last name, login email, position and
        the person's extra contact points (an extra email, a phone in any form, a LinkedIn
        profile);
        `skills` keeps the cards that name every given term (for example `["SQL Server",
        "React"]`) in the position or role, in the skills or in the summary of the anonymous
        card; `seniority` those with one of the given levels (`junior`, `mid`, `senior`,
        `lead`); `min_years` and `max_years` by the card's years of experience (bounds
        included); `industries` and `languages` by the card's industries and languages (every
        term must appear, like `skills`: `["fintech"]`, `["English"]`); `location` by the
        card's location (`"Milano"`); `job_title` by the declared role; `remote` by
        availability (`remote`/`hybrid`/`on_site`); `min_rate` and `max_rate` by daily rate
        (string, «500», «450.50» or in Italian format «1.500», «1.234,50»); `origin` by how
        the row was born (`form` sign-up without a card, `wizard` filled in by the person,
        `admin` written by an admin from a search), `origin_page` by the site page it started
        from (`home`, `pigrocrm`); `utm_source` by campaign; `card` by the state of the
        anonymous card (`present`; `failed` if Claude could not write it; `absent` if there
        is none and it did not fail, for example without a CV); `has_cv` for the cards with or
        without a CV; `with_logins` for those who entered their area at least once;
        `created_from` and `created_to` by creation date (ISO 8601, also just `YYYY-MM-DD`).
        A value not allowed for `origin`, `seniority`, `card`, `sort` or `fields` is an error,
        not an empty list. The filters a bare row does not have (position, skills, location,
        seniority, years, industries, languages, card, remote, rate, `origin_page`, `origin`
        `wizard` or `admin`, `has_cv=true`) exclude leads; `origin=form` instead excludes
        every card.

        `sort`: `recent` (newest first, even if `q` searches), `rate_asc`, `rate_desc`,
        `experience_years_asc`, `experience_years_desc`. Sorting by rate or by years leaves
        only the cards that have that value (no leads, no cards without a rate or without
        years), and `total` and `by_status` count those. Without `sort`: newest first, or most
        relevant first when `q` searches.

        `limit` is 50 rows if you do not give it and 200 at most. `by_status` counts with
        every filter except `status`, `total` with all of them. `next_cursor` is the opaque
        cursor of the next page, `None` on the last: pass it again in `cursor` together with
        the same parameters (also `sort`: a cursor born with another order or another `q` is
        refused). Read-only."""
        term = search_term(q)
        skills = skill_terms(skills)
        sectors = skill_terms(industries, "industries")
        languages = skill_terms(languages, "languages")
        levels = skill_terms(seniority, "seniority")
        place = search_term(location)
        for field, value in (
            ("status", status),
            ("job_title", job_title),
            ("remote", remote),
            ("origin", origin),
            ("origin_page", origin_page),
            ("utm_source", utm_source),
            ("card", card),
            ("sort", sort),
            ("cursor", cursor),
        ):
            bounded(value, field)
        created_after = moment(created_from, "created_from")
        created_before = moment(created_to, "created_to")
        rate_from = number(min_rate, "min_rate")
        rate_to = number(max_rate, "max_rate")

        def call(session: Session) -> CallToolResult:
            # The fields first: a misspelt name costs no query.
            selected = row_fields(fields)
            page = TalentsService(session).list_recent(
                limit=max(1, min(limit, TALENTS_LIMIT_MAX)),
                status=status,
                q=term,
                cursor=cursor,
                job_title=job_title,
                remote=remote,
                min_rate=rate_from,
                max_rate=rate_to,
                origin=origin,
                origin_page=origin_page,
                utm_source=utm_source,
                has_cv=has_cv,
                with_logins=with_logins,
                created_from=created_after,
                created_to=created_before,
                skills=skills,
                location=place,
                seniority=levels,
                min_years=min_years,
                max_years=max_years,
                industries=sectors,
                languages=languages,
                card=card,
                sort=sort,
            )
            if selected is not None and "email" in selected:
                deps.record_read(
                    session,
                    READ_ENTITY,
                    read_entity_id("list_talents"),
                    EMAILS_READ,
                    {"tool": "list_talents", "lines": len(page.items)},
                )
            return compact_result(talents_page(page.model_dump(mode="json"), selected))

        return deps.call(call)

    @mcp.tool()
    def get_talent(talent_id: str) -> dict[str, Any]:
        """One talent, by id: the row `list_talents` shows, read one at a time, with all its
        fields (also `industries`, `languages`, `profile_summary` and `band`, the price band
        for the customer). For a freelancer card it answers the full detail of the admin
        screen (REB-284), the same one «Dettaglio talento» reads: the person's data with the
        status and the `note` thread, the sign-up attribution `signup_utm`, the latest logins
        and the latest downloads of the guide, and the Pigro space when the address has one.
        `phone` is the primary phone point and `contact_points` every way to reach the person:
        the login address first (primary email, no `id`), then the extra addresses, phones and
        profiles with their label and note. `origin` is the list's (`wizard` or `admin`); the
        site page the person started from (`home`, `pigrocrm`) is `origin_page`. For a sign-up
        without a card it answers the bare row, `status` `lead`, from which the card is created with
        `create_freelancer_from_signup`; an id whose email now has a card answers the card,
        like the list. Every row with a card also carries `vetted_at` (REB-518, `None` if not
        verified) and `has_anonymous_card`, whether Claude has written one to propose in the
        team builder. Read-only."""
        key = to_uuid(talent_id, "talent_id")

        def call(session: Session) -> dict[str, Any]:
            if session.get(Freelancer, key) is not None:
                return _freelancer_talent(session, key)
            signup = session.get(Signup, key)
            if signup is None:
                raise NotFound("talento", key)
            # A sign-up whose address gained a card since the list was read is the
            # card's person: answer the card, which is what «Talenti» shows for them.
            holder = session.execute(
                select(Freelancer.id)
                .join(User, User.id == Freelancer.user_id)
                .where(func.lower(User.email) == signup.email.lower())
            ).first()
            if holder is not None:
                return _freelancer_talent(session, holder[0])
            return signup_read(signup).model_dump(mode="json")

        return deps.call(call)

    @mcp.tool()
    def list_duplicates() -> dict[str, Any]:
        """Who is on the list twice (REB-800): `mergeable` are the pairs of live cards with the
        same LinkedIn profile, which `merge_freelancers` can merge; `to_review` those that
        share only the name, the primary phone, or an address (one card's login address is the
        other's extra address), to look at before deciding, never merged on their own; each
        pair's `signals` say which; `signups` how many sign-ups repeat a profile (no card to
        merge: they stay out of the lead lists). For each pair `survivor` is the card with the
        most recent CV, the one that stays, and `absorbed` the one that goes into the other.
        Read-only, without addresses."""
        return deps.call(lambda s: _without_addresses(duplicate_list(s).model_dump(mode="json")))

    @mcp.tool()
    def merge_freelancers(freelancer_id: str, da_id: str) -> dict[str, Any]:
        """Merges card `da_id` into `freelancer_id` (REB-800): notes, logins, matches,
        contracts, campaign rows, referrals and log entries move to the card that stays; the
        most recent CV stays there, with its anonymous card marked to be rewritten (rewritten
        by `cards-refresh`, or `regenerate_freelancer_card`); the other card is not deleted,
        it is hidden and points to this one, and the address it had still works to log in. The
        answer is how many rows moved, by type, and the action id. Refused with a sentence
        when the CRM, where the contracts and engagements are, says both cards have a framework
        agreement in progress or signed of the same type or an engagement in progress with the
        same client, when they have two different tax codes, when the card to absorb belongs
        to an administrator, or when the CRM cannot be asked. Undone from the terminal with
        `rebase talent-duplicates --undo`."""
        return deps.run(
            lambda s: PersonMergeService(s, deps.freelancer_door).merge(
                to_uuid(freelancer_id, "freelancer_id"), to_uuid(da_id, "da_id"), deps.admin().id
            )
        )
