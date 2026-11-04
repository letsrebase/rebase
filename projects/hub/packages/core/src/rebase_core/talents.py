"""Talenti: every freelancer card and every bare sign-up as one list (REB-282), now
searchable, cursor-paginated, and filterable (REB-285).

«Developer e CTO» (`FreelancerService.list_recent`) and «Iscrizioni»
(`SignupService.list_recent`) read overlapping people out of two tables, and
`FreelancerService.list_recent`'s own `lead`/`total_lead` pair already surfaces a
signup with no card beside the cards it counts (ORB-163) -- as a side channel next to
`items`, not a row of the same list. This module makes that merge a first-class read
model instead: one row per person, `status` `lead` for the bare sign-ups and the
freelancer's own state otherwise, over the same two tables, which stay exactly as they
are (Lorenzo's recommendation on REB-282: a sign-up that later fills the wizard keeps
two UTM sets, and folding the tables would have to drop one).

`FreelancerService`, `SignupService`, and every existing caller of either --
`GET /api/hub/freelancers`, `GET /api/hub/signups`, the MCP tools -- are untouched:
this is an additional read model beside them, not a replacement of their own queries.

**The merge stays two independently-sorted queries, never a SQL `UNION`.** REB-282
chose that because a top-`limit` merge of two sources each already sorted newest-first
needs nothing else: no row outside either source's own top `limit` can land in the
combined top `limit`, whichever source it is in. REB-285 keeps the same argument for a
page starting after a cursor -- both sources are queried for their own top-`(limit+1)`
*after* that cursor, under the same ordering (`created_at` or, while searching, the
trigram score), merged and truncated in Python exactly as before. The `+1` is what
tells the caller whether a further page exists without a separate `count(*)`. This also
keeps `search`'s `ILIKE`/`similarity()` filters sitting directly on each table's own
columns, where migration 0013's trigram indexes serve them, rather than on a `UNION`
subquery a planner may or may not push a predicate through.

**Sorting by a card value keeps the same keyset (REB-801).** `sort` `tariffa_*` and
`anni_*` rank by the freelancer's own rate or the years on the anonymous card, ascending
or descending, with the cursor carrying that value and the id, exactly as it carries
`created_at` or the trigram score. A lead has neither value, and so does a card with no
rate or no years: ranking drops them (a row with nothing to rank by has no place in the
order), `total`/`by_status` agree, and the lead query is skipped as for any card-only
filter. That keeps the order a single sorted query rather than a merge with a nullable
key, where a NULL tail would have to be reopened by every page.

**`origin` is the value a row shows (REB-798).** `form`, `wizard` or `admin`, derived
from `compilata_da`; the marketing page a person started from (`Freelancer.origin`,
`home`, `pigrocrm`) is `origin_page`, a filter of its own, so the two meanings no
longer share a name.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, TypedDict, get_args
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Numeric,
    Select,
    and_,
    case,
    exists,
    false,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.orm import Session, aliased, defer

from rebase_core.bands import Band, band_for
from rebase_core.card_level import level
from rebase_core.card_rows import card_emails, card_row_fields
from rebase_core.contact_points import has_point_matching, point_match_score
from rebase_core.duplicates import duplicates_of
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import LEAD_STATE
from rebase_core.leads import card_by_linkedin
from rebase_core.models import (
    FREELANCER_STATES,
    Freelancer,
    FreelancerCard,
    Login,
    Signup,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.search import (
    any_element_matches,
    escape_like,
    matches_any,
    similarity_score,
    string_field_matches,
)
from rebase_core.talent_schemas import TalentList, TalentOrigin, TalentRead

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
_ORIGIN_FORM: TalentOrigin = "form"
# `COMPILATA_DA`'s two values (`models.py`), named for what an admin reading the list
# actually asks: did the person write this themselves, through the wizard, or did an
# admin draft it from research. The column is `NOT NULL` and checked to those two, so
# the filter below and the row's own `origin` cannot part ways.
_ORIGIN_BY_FILLED_BY: dict[str, TalentOrigin] = {"person": "wizard", "admin": "admin"}
_FILLED_BY_BY_ORIGIN: dict[str, str] = {
    origin: filled_by for filled_by, origin in _ORIGIN_BY_FILLED_BY.items()
}
ORIGINS: tuple[str, ...] = get_args(TalentOrigin)
SENIORITIES = ("junior", "mid", "senior", "lead")
CARD_STATES = ("present", "absent", "failed")
SORT_VALUES = ("recent", "rate_asc", "rate_desc", "experience_years_asc", "experience_years_desc")

_ENTITY = "talents"
_RankedBy = Literal["rate", "experience_years"]


def _card_only_active(*values: object) -> bool:
    """Whether any filter with no lead-side equivalent is set: `job_title`, `remote`,
    `daily_rate` and the marketing `origin_page` are columns a bare sign-up
    simply does not have (`Signup` carries none of them), and `skills`, `location`,
    `seniority`, `experience_years`, `industries`, `languages` and `card` read an anonymous card a
    sign-up cannot have either, so a value here can only ever exclude every lead, and
    the lead query is skipped outright rather than issued for a result that can only be
    empty. `has_cv` is handled separately, since `has_cv=False` is a value every lead
    genuinely satisfies. `None`, an empty string and an empty tuple are unset."""
    return any(value not in (None, "") and value != () for value in values)


def signup_read(row: Signup) -> TalentRead:
    """A bare sign-up as a row of the list: `status` `lead`, `origin` `form`, none of
    the card's fields."""
    return TalentRead(
        id=row.id,
        first_name=row.first_name,
        last_name=row.last_name,
        email=row.email,
        linkedin_url=row.linkedin_url,
        status=LEAD_STATE,
        origin=_ORIGIN_FORM,
        utm_source=row.utm_source,
        created_at=row.created_at,
    )


def _card_text(card: dict[str, Any], key: str) -> str | None:
    value = card.get(key)
    return value if isinstance(value, str) else None


def _card_texts(card: dict[str, Any], key: str) -> list[str]:
    value = card.get(key)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


class CardFields(TypedDict):
    """What `card_fields` answers: the `TalentRead` fields a card's own JSON fills."""

    role: str | None
    seniority: str | None
    experience_years: int | None
    seniority_uncertain: bool
    skills: list[str]
    industries: list[str]
    languages: list[str]
    location: str | None
    profile_summary: str | None
    band: Band | None


def card_fields(
    card: object,
    rate: Decimal | None,
    seniority_admin: str | None = None,
    experience_years_admin: int | None = None,
) -> CardFields:
    """What a row says about the person from the anonymous card (REB-778, REB-798), and
    the client's price band of their rate: the one builder behind a row of the list and
    the MCP `get_talent`, so the two cannot disagree. `card` is the row's
    `freelancer_cards.card` as stored: a JSON object for a live anonymous card
    (REB-518), `None` for none and for one the writer retired after a failure. Each
    field is taken only when it has the type the writer gives it, since a card written
    by hand may hold anything, and a wrong one reads as missing.

    `seniority` and `experience_years` are the admin's when one corrected them (`seniority_admin`,
    `experience_years_admin`, REB-799), the card's otherwise, through `card_level.level`;
    `seniority_uncertain` says the seniority is a guess, the card having no dated years."""
    card_json = card if isinstance(card, dict) else {}
    effective = level(card_json, seniority_admin, experience_years_admin)
    return {
        "role": _card_text(card_json, "role"),
        "seniority": effective.seniority,
        "experience_years": effective.experience_years,
        "seniority_uncertain": effective.uncertain,
        "skills": _card_texts(card_json, "skills"),
        "industries": _card_texts(card_json, "industries"),
        "languages": _card_texts(card_json, "languages"),
        "location": _card_text(card_json, "location"),
        "profile_summary": _card_text(card_json, "profile_summary"),
        "band": band_for(rate),
    }


def _card_read(row: Freelancer, user: User, card: object) -> TalentRead:
    """One freelancer as a row: the columns every card row carries (`card_row_fields`),
    then `card_fields` for what the anonymous `card` says."""
    return TalentRead(
        **card_row_fields(row, user),
        origin=_ORIGIN_BY_FILLED_BY.get(row.filled_by, "wizard"),
        has_anonymous_card=isinstance(card, dict),
        **card_fields(card, row.daily_rate, row.seniority_admin, row.experience_years_admin),
    )


def _names_skill(term: str) -> ColumnElement[bool]:
    """Whether the row's freelancer names `term`, case-insensitively: in their own
    position, or in the role, a skill or the summary of their anonymous card. Only a
    JSON string on the card can match, the values `card_fields` keeps, so a match on the
    role, the skills or the place is always one the row shows; a card that is not a
    JSON object matches nothing.

    The card is read through an alias: the listing joins `freelancer_cards` itself, and
    a subquery on the same table would correlate to that join and lose its own FROM."""
    card = aliased(FreelancerCard)
    on_the_card = exists(
        select(card.freelancer_id).where(
            card.freelancer_id == Freelancer.id,
            or_(
                string_field_matches(card.card["role"], term),
                string_field_matches(card.card["profile_summary"], term),
                any_element_matches(card.card["skills"], term),
            ),
        )
    )
    return or_(matches_any((Freelancer.job_title,), term), on_the_card)


def _lives_in(place: str) -> ColumnElement[bool]:
    """Whether the row's anonymous card places the freelancer in `place`, as a part of
    its `location` («Monza» finds «Monza e Brianza»), a JSON string as `_names_skill` reads
    one, through an alias for the same reason."""
    card = aliased(FreelancerCard)
    return exists(
        select(card.freelancer_id).where(
            card.freelancer_id == Freelancer.id,
            string_field_matches(card.card["location"], place),
        )
    )


def _card_list_names(key: str, term: str) -> ColumnElement[bool]:
    """Whether the anonymous card's array `key` (`industries`, `languages`) holds a string
    element containing `term`, the same substring match `skills` applies."""
    card = aliased(FreelancerCard)
    return exists(
        select(card.freelancer_id).where(
            card.freelancer_id == Freelancer.id,
            any_element_matches(card.card[key], term),
        )
    )


def _has_seniority(values: Sequence[str]) -> ColumnElement[bool]:
    """Whether the row's seniority is one of `values`: the admin's when one corrected it
    (`seniority_admin`, REB-799), the anonymous card's otherwise, of which only a JSON
    string can be, the same test `card_fields` applies."""
    card = aliased(FreelancerCard)
    field = card.card["seniority"]
    on_the_card = exists(
        select(card.freelancer_id).where(
            card.freelancer_id == Freelancer.id,
            func.jsonb_typeof(field) == "string",
            func.lower(field.astext).in_(values),
        )
    )
    return case(
        (Freelancer.seniority_admin.is_not(None), Freelancer.seniority_admin.in_(values)),
        else_=on_the_card,
    )


def _card_experience_years() -> ColumnElement[Any]:
    """The row's years as a number: the admin's when one corrected them (`experience_years_admin`,
    REB-799), else the anonymous card's, `NULL` when there is no card or the value is
    not a whole JSON number: the values `card_fields` keeps, so a filter or a
    ranking never meets a row whose years the row itself does not show (`5.5`, `"9"`).
    A scalar subquery by primary key, so the same expression serves a `WHERE`, an
    `ORDER BY` and a keyset predicate, and the guard sits inside the `CASE` rather than
    beside it in an `AND`, where SQL would not promise to run it first."""
    card = aliased(FreelancerCard)
    field = card.card["experience_years"]
    years = case(
        (
            and_(func.jsonb_typeof(field) == "number", field.astext.regexp_match(r"^-?[0-9]+$")),
            field.astext.cast(Numeric),
        )
    )
    on_the_card = select(years).where(card.freelancer_id == Freelancer.id).scalar_subquery()
    return func.coalesce(Freelancer.experience_years_admin.cast(Numeric), on_the_card)


def _card_is(state: str) -> ColumnElement[bool]:
    """The state of the freelancer's anonymous card: `present` is a live card (a JSON
    object, the test `card_fields` and REB-518 apply), `failed` a recorded failure
    (`error`) with no live card, `absent` neither: no CV yet, or a CV nobody has asked
    Claude about. A failure beside an older live card (an outage keeps it) reads as
    `present`: the card shown is a card."""
    live = aliased(FreelancerCard)
    failed = aliased(FreelancerCard)
    has_live = exists(
        select(live.freelancer_id).where(
            live.freelancer_id == Freelancer.id, func.jsonb_typeof(live.card) == "object"
        )
    )
    has_failure = exists(
        select(failed.freelancer_id).where(
            failed.freelancer_id == Freelancer.id,
            failed.error.is_not(None),
            or_(failed.card.is_(None), func.jsonb_typeof(failed.card) != "object"),
        )
    )
    if state == "present":
        return has_live
    if state == "failed":
        return has_failure & ~has_live
    return ~has_live & ~has_failure


@dataclass(frozen=True, slots=True)
class _Order:
    """What the list is sorted by: the cursor's key, the direction, and the card value
    ranked by (`None` for `created_at` and the trigram score, which every row has)."""

    spec: SortSpec
    descending: bool
    ranked_by: _RankedBy | None = None


def _order(sort: str | None, term: str) -> _Order:
    if sort is not None and sort not in SORT_VALUES:
        raise ValidationFailed(_ENTITY, "sort", f"atteso uno fra {', '.join(SORT_VALUES)}")
    if sort is None:
        return _Order(
            SortSpec("score", "float") if term else SortSpec("created_at", "datetime"), True
        )
    if sort == "recent":
        return _Order(SortSpec("created_at", "datetime"), True)
    field, direction = sort.rsplit("_", 1)
    ranked_by: _RankedBy = "rate" if field == "rate" else "experience_years"
    # The whole `sort` is the cursor's key, direction included: a cursor minted going
    # up must not be replayed going down, where `(value, id) > ...` would skip and
    # repeat rows.
    return _Order(SortSpec(sort, "float"), direction == "desc", ranked_by)


def _choices(field: str, values: Sequence[str] | None, allowed: Sequence[str]) -> tuple[str, ...]:
    """The distinct lowercase `values`, each one of `allowed`; blank ones are ignored."""
    chosen = tuple(dict.fromkeys(v.strip().lower() for v in values or () if v.strip()))
    for value in chosen:
        if value not in allowed:
            raise ValidationFailed(
                _ENTITY, field, f"«{value}»: atteso uno fra {', '.join(allowed)}"
            )
    return chosen


def _terms(values: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(value.strip() for value in values or () if value.strip())


@dataclass(frozen=True, slots=True)
class _Filters:
    """Every filter of one list call, validated once, so the listing, the leads and
    `_counts` read the same values and cannot drift apart."""

    q: str
    job_title: str | None
    remote: str | None
    min_rate: Decimal | None
    max_rate: Decimal | None
    origin: str | None
    origin_page: str | None
    utm_source: str | None
    has_cv: bool | None
    with_logins: bool | None
    created_from: datetime | None
    created_to: datetime | None
    skills: tuple[str, ...]
    location: str | None
    seniority: tuple[str, ...]
    min_years: int | None
    max_years: int | None
    industries: tuple[str, ...]
    languages: tuple[str, ...]
    card: str | None
    ranked_by: _RankedBy | None

    @property
    def card_only(self) -> bool:
        """Whether a lead can never satisfy these: any filter `_card_only_active`
        names, `origin` `wizard`/`admin` (a lead is `form`), or a ranking by a card
        value."""
        return _card_only_active(
            self.job_title,
            self.remote,
            self.min_rate,
            self.max_rate,
            self.origin if self.origin != _ORIGIN_FORM else None,
            self.origin_page,
            self.skills,
            self.location,
            self.seniority,
            self.min_years,
            self.max_years,
            self.industries,
            self.languages,
            self.card,
            self.ranked_by,
        )

    @property
    def leads_possible(self) -> bool:
        """Whether a bare sign-up can be in the answer at all, `status` aside."""
        return not self.card_only and self.has_cv is not True


# What a search term is read against on a card, besides the person's extra contact points.
_CARD_SEARCH_COLUMNS = (User.first_name, User.last_name, User.email, Freelancer.job_title)


def _card_stmt(f: _Filters, *, counting: bool = False) -> Select[Freelancer, User]:
    """Every freelancer card, `status` left for the caller to filter separately -- this
    statement backs both the listing and `_counts`' per-state `GROUP BY`, and the
    counts must see every state at once. A count with no `q` reads nothing of the
    person, so it leaves the `users` join out."""
    stmt = select(Freelancer, User)
    if not counting or f.q:
        stmt = stmt.join(User, User.id == Freelancer.user_id)
    stmt = stmt.where(Freelancer.deleted_at.is_(None))
    if f.q:
        stmt = stmt.where(
            or_(matches_any(_CARD_SEARCH_COLUMNS, f.q), has_point_matching(User.id, f.q))
        )
    if f.job_title:
        stmt = stmt.where(Freelancer.job_title.ilike(f"%{escape_like(f.job_title)}%"))
    if f.remote is not None:
        stmt = stmt.where(Freelancer.remote == f.remote)
    if f.min_rate is not None:
        stmt = stmt.where(Freelancer.daily_rate >= f.min_rate)
    if f.max_rate is not None:
        stmt = stmt.where(Freelancer.daily_rate <= f.max_rate)
    if f.origin == _ORIGIN_FORM:
        # `form` is a bare sign-up: no card is one.
        stmt = stmt.where(false())
    elif f.origin is not None:
        stmt = stmt.where(Freelancer.filled_by == _FILLED_BY_BY_ORIGIN[f.origin])
    if f.origin_page is not None:
        stmt = stmt.where(Freelancer.origin == f.origin_page)
    if f.utm_source is not None:
        stmt = stmt.where(Freelancer.utm_source == f.utm_source)
    if f.has_cv is not None:
        stmt = stmt.where(
            Freelancer.cv_filename.isnot(None) if f.has_cv else Freelancer.cv_filename.is_(None)
        )
    if f.with_logins is not None:
        login_exists = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
        stmt = stmt.where(login_exists if f.with_logins else ~login_exists)
    if f.created_from is not None:
        stmt = stmt.where(Freelancer.created_at >= f.created_from)
    if f.created_to is not None:
        stmt = stmt.where(Freelancer.created_at <= f.created_to)
    for skill in f.skills:
        stmt = stmt.where(_names_skill(skill))
    if f.location:
        stmt = stmt.where(_lives_in(f.location))
    if f.seniority:
        stmt = stmt.where(_has_seniority(f.seniority))
    if f.min_years is not None:
        stmt = stmt.where(_card_experience_years() >= f.min_years)
    if f.max_years is not None:
        stmt = stmt.where(_card_experience_years() <= f.max_years)
    for sector in f.industries:
        stmt = stmt.where(_card_list_names("industries", sector))
    for language in f.languages:
        stmt = stmt.where(_card_list_names("languages", language))
    if f.card is not None:
        stmt = stmt.where(_card_is(f.card))
    if f.ranked_by == "rate":
        stmt = stmt.where(Freelancer.daily_rate.is_not(None))
    elif f.ranked_by == "experience_years":
        stmt = stmt.where(_card_experience_years().is_not(None))
    return stmt


def _bare_signups() -> Select[Signup]:
    """Every bare sign-up (no card), the same anti-join `card_emails` describes."""
    addresses = card_emails()
    return (
        select(Signup)
        .outerjoin(addresses, addresses.c.email == func.lower(Signup.email))
        .where(addresses.c.email.is_(None), ~card_by_linkedin())
    )


def lead_by_id(session: Session, signup_id: UUID) -> TalentRead | None:
    """One bare sign-up as its list row, or None when there is no such sign-up or the
    person already has a card: the list shows that person as a card, never as a lead."""
    signup = session.scalar(_bare_signups().where(Signup.id == signup_id))
    return None if signup is None else signup_read(signup)


def _lead_stmt(f: _Filters) -> Select[Signup]:
    """Every bare sign-up (no card) the filters keep."""
    stmt = _bare_signups()
    if f.q:
        stmt = stmt.where(matches_any((Signup.first_name, Signup.last_name, Signup.email), f.q))
    if f.utm_source is not None:
        stmt = stmt.where(Signup.utm_source == f.utm_source)
    if f.with_logins is not None:
        # A lead has no `user_id` of its own; the address is the only thing it can be
        # matched to a `users`/`logins` row by.
        lead_user = aliased(User)
        login_exists = exists(
            select(Login.id)
            .join(lead_user, lead_user.id == Login.user_id)
            .where(func.lower(lead_user.email) == func.lower(Signup.email))
        )
        stmt = stmt.where(login_exists if f.with_logins else ~login_exists)
    if f.created_from is not None:
        stmt = stmt.where(Signup.created_at >= f.created_from)
    if f.created_to is not None:
        stmt = stmt.where(Signup.created_at <= f.created_to)
    return stmt


class TalentsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def row_for_freelancer(self, freelancer_id: UUID) -> TalentRead:
        """The list's own row for one freelancer, deleted or not (the caller decides
        what a deleted card means): what `get_talent` merges over the admin detail, so
        a card reads the same in both. `NotFound` for an id that is no freelancer."""
        found = self.session.execute(
            select(Freelancer, User, FreelancerCard.card.label("card"))
            .join(User, User.id == Freelancer.user_id)
            .outerjoin(FreelancerCard, FreelancerCard.freelancer_id == Freelancer.id)
            .where(Freelancer.id == freelancer_id)
            .options(defer(Freelancer.cv_bytes))
        ).first()
        if found is None:
            raise NotFound("talento", freelancer_id)
        row = _card_read(found[0], found[1], found.card)
        row.duplicate_possible_of = duplicates_of(self.session, [freelancer_id]).get(
            freelancer_id, []
        )
        return row

    def list_recent(
        self,
        limit: int = LIST_LIMIT_DEFAULT,
        status: str | None = None,
        *,
        q: str | None = None,
        cursor: str | None = None,
        job_title: str | None = None,
        remote: str | None = None,
        min_rate: Decimal | None = None,
        max_rate: Decimal | None = None,
        origin: str | None = None,
        origin_page: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        with_logins: bool | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        skills: Sequence[str] | None = None,
        location: str | None = None,
        seniority: Sequence[str] | None = None,
        min_years: int | None = None,
        max_years: int | None = None,
        industries: Sequence[str] | None = None,
        languages: Sequence[str] | None = None,
        card: str | None = None,
        sort: str | None = None,
    ) -> TalentList:
        """Every card and every bare sign-up as one row, newest-or-best-match first,
        `status` `lead` for the bare ones.

        `status` filters the same way it always has: `"lead"` selects the bare sign-ups
        alone, any other value selects `Freelancer.stato == stato` (an unknown one is
        simply an empty list, like `FreelancerService.list_recent`'s own pass-through
        filter -- REB-285 does not change that), and `None` merges both. Every other
        filter narrows both sides at once, except the card-only ones
        (`_card_only_active`), which a lead can never satisfy and so drop it from the
        merge outright, and `has_cv`, which a lead satisfies only as `False`.

        `origin` is what the row shows (`form`, `wizard`, `admin`; anything else is a
        `ValidationFailed`, not an empty list that reads like «nobody»), and
        `origin_page` the marketing page the person started from (REB-798).

        `skills` keeps the cards that name every one of its terms (REB-778), each
        in the position or in the anonymous card's role, skills or summary; `location`
        keeps the cards whose anonymous card places them there. Blank terms are ignored.
        REB-801 adds, on the same anonymous card: `seniority` (one of `SENIORITIES`,
        any of those given), `min_years`/`max_years` (inclusive), `industries` and `languages`
        (every term must be named by one element, a substring, like `skills`), and
        `card`, the card's own state (`CARD_STATES`).

        `sort` is `recent` (newest first, even while searching), or `rate_asc`,
        `rate_desc`, `experience_years_asc`, `experience_years_desc`: the cursor then carries that
        value, and
        the rows with none to rank by (every lead, a card with no rate or no years) are
        left out of the list and of its counts. Without it the order is newest first, or
        best match while `q` searches."""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        term = (q or "").strip()
        # A blank is no filter, the way a blank `q` or `location` always was.
        origin = (origin or "").strip().lower() or None
        origin_page = (origin_page or "").strip() or None
        card = (card or "").strip().lower() or None
        sort = (sort or "").strip().lower() or None
        order = _order(sort, term)
        if origin is not None and origin not in ORIGINS:
            raise ValidationFailed(_ENTITY, "origin", f"atteso uno fra {', '.join(ORIGINS)}")
        if card is not None and card not in CARD_STATES:
            raise ValidationFailed(_ENTITY, "card", f"atteso uno fra {', '.join(CARD_STATES)}")
        f = _Filters(
            q=term,
            job_title=job_title,
            remote=remote,
            min_rate=min_rate,
            max_rate=max_rate,
            origin=origin,
            origin_page=origin_page,
            utm_source=utm_source,
            has_cv=has_cv,
            with_logins=with_logins,
            created_from=created_from,
            created_to=created_to,
            skills=_terms(skills),
            location=(location or "").strip() or None,
            seniority=_choices("seniority", seniority, SENIORITIES),
            min_years=min_years,
            max_years=max_years,
            industries=_terms(industries),
            languages=_terms(languages),
            card=card,
            ranked_by=order.ranked_by,
        )
        wants_cards = status is None or status != LEAD_STATE
        wants_leads = (status is None or status == LEAD_STATE) and f.leads_possible

        sort_spec = order.spec
        cursor_bound: tuple[datetime | float, UUID] | None = (
            decode_cursor(sort_spec, cursor) if cursor else None
        )

        candidates: list[tuple[datetime | float, UUID, TalentRead]] = []

        if wants_cards:
            stmt = _card_stmt(f)
            if status is not None:
                stmt = stmt.where(Freelancer.status == status)
            sort_col: Any
            if order.ranked_by == "rate":
                sort_col = Freelancer.daily_rate
            elif order.ranked_by == "experience_years":
                sort_col = _card_experience_years()
            elif sort_spec.key == "score":
                # An extra point that is the term ranks like an exact address; one that only
                # contains it, below a name or address that equals it.
                sort_col = func.greatest(
                    similarity_score(_CARD_SEARCH_COLUMNS, term), point_match_score(User.id, term)
                )
            else:
                sort_col = Freelancer.created_at
            if sort_spec.key != "created_at":
                stmt = stmt.add_columns(sort_col.label("sort_key"))
            if cursor_bound is not None:
                stmt = stmt.where(
                    keyset_predicate(
                        sort_col,
                        Freelancer.id,
                        cursor_bound[0],
                        cursor_bound[1],
                        descending=order.descending,
                    )
                )
            # The anonymous card rides along whole (one row per freelancer, the primary
            # key): its fields are what an agent matches a request on (REB-778), and a
            # JSON object there is also what makes a live card (REB-518).
            stmt = stmt.outerjoin(
                FreelancerCard, FreelancerCard.freelancer_id == Freelancer.id
            ).add_columns(FreelancerCard.card.label("card"))
            # A row reads `cv_size` and the file's name, never the PDF itself: a page of
            # 100 cards (or a campaign's whole list, P-REB-41) must not carry 100 CVs.
            rows = self.session.execute(
                stmt.order_by(
                    sort_col.desc() if order.descending else sort_col.asc(),
                    Freelancer.id.desc() if order.descending else Freelancer.id.asc(),
                )
                .limit(limit + 1)
                .options(defer(Freelancer.cv_bytes))
            ).all()
            for row in rows:
                freelancer, user = row[0], row[1]
                # A Postgres `numeric` comes back as a `Decimal`; the cursor carries
                # JSON numbers, so a ranked value is a `float` on both sides.
                sort_value = (
                    freelancer.created_at if sort_spec.key == "created_at" else float(row.sort_key)
                )
                candidates.append(
                    (sort_value, freelancer.id, _card_read(freelancer, user, row.card))
                )

        if wants_leads:
            lead_stmt = _lead_stmt(f)
            if sort_spec.key == "score":
                lead_score = similarity_score(
                    (Signup.first_name, Signup.last_name, Signup.email), term
                )
                lead_stmt = lead_stmt.add_columns(lead_score.label("score"))
                lead_sort_col: Any = lead_score
            else:
                lead_sort_col = Signup.created_at
            if cursor_bound is not None:
                lead_stmt = lead_stmt.where(
                    keyset_predicate(lead_sort_col, Signup.id, cursor_bound[0], cursor_bound[1])
                )
            lead_rows = self.session.execute(
                lead_stmt.order_by(lead_sort_col.desc(), Signup.id.desc()).limit(limit + 1)
            ).all()
            for lead_row in lead_rows:
                signup = lead_row[0]
                sort_value = lead_row.score if sort_spec.key == "score" else signup.created_at
                candidates.append((sort_value, signup.id, signup_read(signup)))

        candidates.sort(key=lambda c: (c[0], c[1]), reverse=order.descending)
        page = candidates[:limit]
        items = [item for _, _, item in page]
        self._flag_duplicates(items)
        next_cursor = (
            encode_cursor(sort_spec, page[-1][0], page[-1][1])
            if len(candidates) > limit and page
            else None
        )

        by_status = self._counts(f)
        total = sum(by_status.values()) if status is None else by_status.get(status, 0)
        return TalentList(total=total, items=items, by_status=by_status, next_cursor=next_cursor)

    def _flag_duplicates(self, items: list[TalentRead]) -> None:
        """`duplicate_possible_of` on the cards of a page (REB-800): the other live cards
        that share their LinkedIn, name or telephone number, by id."""
        card_ids = [item.id for item in items if item.status != LEAD_STATE]
        if not card_ids:
            return
        flagged = duplicates_of(self.session, card_ids)
        for item in items:
            item.duplicate_possible_of = flagged.get(item.id, [])

    def _counts(self, f: _Filters) -> dict[str, int]:
        """How many rows sit in each state, `lead` included, with every filter but
        `status` itself applied (REB-285) -- the numbers the admin area's tabs need
        beside the page, answering "how many if I picked this one" rather than "how
        many exist at all". One statement: the cards' `GROUP BY` and the leads' count
        as a `UNION ALL`."""
        cards = (
            _card_stmt(f, counting=True)
            .with_only_columns(Freelancer.status, func.count())
            .group_by(Freelancer.status)
        )
        statement = (
            union_all(cards, _lead_stmt(f).with_only_columns(literal(LEAD_STATE), func.count()))
            if f.leads_possible
            else cards
        )
        by_state: dict[str, int] = {
            status: count for status, count in self.session.execute(statement)
        }
        counts = {status: by_state.get(status, 0) for status in FREELANCER_STATES}
        counts[LEAD_STATE] = by_state.get(LEAD_STATE, 0)
        return counts
