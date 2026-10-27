"""Talenti: every freelancer card and every bare sign-up as one list (REB-282), now
searchable, cursor-paginated, and filterable (REB-285).

«Developer e CTO» (`FreelancerService.list_recent`) and «Iscrizioni»
(`SignupService.list_recent`) read overlapping people out of two tables, and
`FreelancerService.list_recent`'s own `lead`/`totale_lead` pair already surfaces a
signup with no card beside the cards it counts (ORB-163) -- as a side channel next to
`items`, not a row of the same list. This module makes that merge a first-class read
model instead: one row per person, `stato` `lead` for the bare sign-ups and the
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

**Sorting by a card value keeps the same keyset (REB-801).** `ordina` `tariffa_*` and
`anni_*` rank by the freelancer's own rate or the years on the anonymous card, ascending
or descending, with the cursor carrying that value and the id, exactly as it carries
`created_at` or the trigram score. A lead has neither value, and so does a card with no
rate or no years: ranking drops them (a row with nothing to rank by has no place in the
order), `totale`/`per_stato` agree, and the lead query is skipped as for any card-only
filter. That keeps the order a single sorted query rather than a merge with a nullable
key, where a NULL tail would have to be reopened by every page.

**`origine` is the value a row shows (REB-798).** `form`, `wizard` or `admin`, derived
from `compilata_da`; the marketing page a person started from (`Freelancer.origine`,
`home`, `pigrocrm`) is `pagina_origine`, a filter of its own, so the two meanings no
longer share a name.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, get_args
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Numeric,
    Select,
    Subquery,
    and_,
    case,
    exists,
    false,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Session, aliased, defer

from rebase_core.bands import band_for
from rebase_core.card_level import level
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import LEAD_STATE
from rebase_core.models import (
    FREELANCER_STATES,
    Freelancer,
    FreelancerCard,
    Login,
    Signup,
    User,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.schemas import TalentoList, TalentoOrigine, TalentoRead
from rebase_core.search import (
    any_element_matches,
    escape_like,
    matches_any,
    similarity_score,
    string_field_matches,
)

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
_ORIGIN_FORM: TalentoOrigine = "form"
# `COMPILATA_DA`'s two values (`models.py`), named for what an admin reading the list
# actually asks: did the person write this themselves, through the wizard, or did an
# admin draft it from research. The column is `NOT NULL` and checked to those two, so
# the filter below and the row's own `origine` cannot part ways.
_ORIGIN_BY_COMPILATA_DA: dict[str, TalentoOrigine] = {"persona": "wizard", "admin": "admin"}
_COMPILATA_DA_BY_ORIGIN: dict[str, str] = {
    origin: compilata_da for compilata_da, origin in _ORIGIN_BY_COMPILATA_DA.items()
}
ORIGINS: tuple[str, ...] = get_args(TalentoOrigine)
SENIORITIES = ("junior", "mid", "senior", "lead")
SCHEDA_STATES = ("presente", "assente", "fallita")
ORDINA_VALUES = ("recenti", "tariffa_asc", "tariffa_desc", "anni_asc", "anni_desc")

_ENTITY = "talenti"
_RankedBy = Literal["tariffa", "anni"]


def _card_only_active(*values: object) -> bool:
    """Whether any filter with no lead-side equivalent is set: `posizione`, `remoto`,
    `tariffa_giornaliera` and the marketing `pagina_origine` are columns a bare sign-up
    simply does not have (`Signup` carries none of them), and `competenze`, `luogo`,
    `seniority`, `anni`, `settori`, `lingue` and `scheda` read an anonymous card a
    sign-up cannot have either, so a value here can only ever exclude every lead, and
    the lead query is skipped outright rather than issued for a result that can only be
    empty. `has_cv` is handled separately, since `has_cv=False` is a value every lead
    genuinely satisfies. `None`, an empty string and an empty tuple are unset."""
    return any(value not in (None, "") and value != () for value in values)


def _card_emails() -> Subquery:
    """Lowercased addresses that already have a card: a bare sign-up (ORB-163) is one
    whose address is not among these. The same anti-join `FreelancerService._leads`
    runs, written again here rather than imported, so this module reads the same
    self-contained way every other service in this package does."""
    return (
        select(func.lower(User.email).label("email"))
        .join(Freelancer, Freelancer.user_id == User.id)
        .subquery()
    )


def signup_read(row: Signup) -> TalentoRead:
    """A bare sign-up as a row of the list: `stato` `lead`, `origine` `form`, none of
    the card's fields."""
    return TalentoRead(
        id=row.id,
        nome=row.nome,
        cognome=row.cognome,
        email=row.email,
        linkedin_url=row.linkedin_url,
        stato=LEAD_STATE,
        origine=_ORIGIN_FORM,
        utm_source=row.utm_source,
        created_at=row.created_at,
    )


def _card_text(card: dict[str, Any], key: str) -> str | None:
    value = card.get(key)
    return value if isinstance(value, str) else None


def _card_texts(card: dict[str, Any], key: str) -> list[str]:
    value = card.get(key)
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def card_fields(
    card: object,
    rate: Decimal | None,
    seniority_admin: str | None = None,
    anni_admin: int | None = None,
) -> dict[str, Any]:
    """What a row says about the person from the anonymous card (REB-778, REB-798), and
    the client's price band of their rate: the one builder behind a row of the list and
    the MCP `get_talento`, so the two cannot disagree. `card` is the row's
    `freelancer_cards.card` as stored: a JSON object for a live anonymous card
    (REB-518), `None` for none and for one the writer retired after a failure. Each
    field is taken only when it has the type the writer gives it, since a card written
    by hand may hold anything, and a wrong one reads as missing.

    `seniority` and `anni` are the admin's when one corrected them (`seniority_admin`,
    `anni_admin`, REB-799), the card's otherwise, through `card_level.level`;
    `seniority_incerta` says the seniority is a guess, the card having no dated years."""
    scheda = card if isinstance(card, dict) else {}
    effective = level(scheda, seniority_admin, anni_admin)
    return {
        "ruolo": _card_text(scheda, "ruolo"),
        "seniority": effective.seniority,
        "anni": effective.anni,
        "seniority_incerta": effective.incerta,
        "competenze": _card_texts(scheda, "competenze"),
        "settori": _card_texts(scheda, "settori"),
        "lingue": _card_texts(scheda, "lingue"),
        "luogo": _card_text(scheda, "luogo"),
        "sintesi": _card_text(scheda, "sintesi"),
        "fascia": band_for(rate),
    }


def _card_read(row: Freelancer, user: User, card: object) -> TalentoRead:
    """One freelancer as a row: the person and the card's own columns, then
    `card_fields` for what the anonymous `card` says."""
    return TalentoRead(
        id=row.id,
        nome=user.nome,
        cognome=user.cognome,
        email=user.email,
        linkedin_url=user.linkedin_url,
        stato=row.stato,
        origine=_ORIGIN_BY_COMPILATA_DA.get(row.compilata_da, "wizard"),
        utm_source=row.utm_source,
        created_at=row.created_at,
        tariffa_giornaliera=row.tariffa_giornaliera,
        remoto=row.remoto,
        vetted_at=row.vetted_at,
        ha_scheda_anonima=isinstance(card, dict),
        posizione=row.posizione,
        **card_fields(card, row.tariffa_giornaliera, row.seniority_admin, row.anni_admin),
    )


def _names_skill(term: str) -> ColumnElement[bool]:
    """Whether the row's freelancer names `term`, case-insensitively: in their own
    position, or in the role, a skill or the summary of their anonymous card. Only a
    JSON string on the card can match, the values `card_fields` keeps, so a match on the
    role, the skills or the place is always one the row shows; a card that is not a
    JSON object matches nothing.

    The card is read through an alias: the listing joins `freelancer_cards` itself, and
    a subquery on the same table would correlate to that join and lose its own FROM."""
    scheda = aliased(FreelancerCard)
    on_the_card = exists(
        select(scheda.freelancer_id).where(
            scheda.freelancer_id == Freelancer.id,
            or_(
                string_field_matches(scheda.card["ruolo"], term),
                string_field_matches(scheda.card["sintesi"], term),
                any_element_matches(scheda.card["competenze"], term),
            ),
        )
    )
    return or_(matches_any((Freelancer.posizione,), term), on_the_card)


def _lives_in(place: str) -> ColumnElement[bool]:
    """Whether the row's anonymous card places the freelancer in `place`, as a part of
    its `luogo` («Monza» finds «Monza e Brianza»), a JSON string as `_names_skill` reads
    one, through an alias for the same reason."""
    scheda = aliased(FreelancerCard)
    return exists(
        select(scheda.freelancer_id).where(
            scheda.freelancer_id == Freelancer.id,
            string_field_matches(scheda.card["luogo"], place),
        )
    )


def _card_list_names(key: str, term: str) -> ColumnElement[bool]:
    """Whether the anonymous card's array `key` (`settori`, `lingue`) holds a string
    element containing `term`, the same substring match `competenze` applies."""
    scheda = aliased(FreelancerCard)
    return exists(
        select(scheda.freelancer_id).where(
            scheda.freelancer_id == Freelancer.id,
            any_element_matches(scheda.card[key], term),
        )
    )


def _has_seniority(values: Sequence[str]) -> ColumnElement[bool]:
    """Whether the row's seniority is one of `values`: the admin's when one corrected it
    (`seniority_admin`, REB-799), the anonymous card's otherwise, of which only a JSON
    string can be, the same test `card_fields` applies."""
    scheda = aliased(FreelancerCard)
    field = scheda.card["seniority"]
    on_the_card = exists(
        select(scheda.freelancer_id).where(
            scheda.freelancer_id == Freelancer.id,
            func.jsonb_typeof(field) == "string",
            func.lower(field.astext).in_(values),
        )
    )
    return case(
        (Freelancer.seniority_admin.is_not(None), Freelancer.seniority_admin.in_(values)),
        else_=on_the_card,
    )


def _card_anni() -> ColumnElement[Any]:
    """The row's years as a number: the admin's when one corrected them (`anni_admin`,
    REB-799), else the anonymous card's, `NULL` when there is no card or the value is
    not a whole JSON number: the values `card_fields` keeps, so a filter or a
    ranking never meets a row whose years the row itself does not show (`5.5`, `"9"`).
    A scalar subquery by primary key, so the same expression serves a `WHERE`, an
    `ORDER BY` and a keyset predicate, and the guard sits inside the `CASE` rather than
    beside it in an `AND`, where SQL would not promise to run it first."""
    scheda = aliased(FreelancerCard)
    field = scheda.card["anni"]
    years = case(
        (
            and_(func.jsonb_typeof(field) == "number", field.astext.regexp_match(r"^-?[0-9]+$")),
            field.astext.cast(Numeric),
        )
    )
    on_the_card = select(years).where(scheda.freelancer_id == Freelancer.id).scalar_subquery()
    return func.coalesce(Freelancer.anni_admin.cast(Numeric), on_the_card)


def _scheda_is(state: str) -> ColumnElement[bool]:
    """The state of the freelancer's anonymous card: `presente` is a live card (a JSON
    object, the test `card_fields` and REB-518 apply), `fallita` a recorded failure
    (`error`) with no live card, `assente` neither: no CV yet, or a CV nobody has asked
    Claude about. A failure beside an older live card (an outage keeps it) reads as
    `presente`: the card shown is a card."""
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
    if state == "presente":
        return has_live
    if state == "fallita":
        return has_failure & ~has_live
    return ~has_live & ~has_failure


@dataclass(frozen=True, slots=True)
class _Order:
    """What the list is sorted by: the cursor's key, the direction, and the card value
    ranked by (`None` for `created_at` and the trigram score, which every row has)."""

    spec: SortSpec
    descending: bool
    ranked_by: _RankedBy | None = None


def _order(ordina: str | None, term: str) -> _Order:
    if ordina is not None and ordina not in ORDINA_VALUES:
        raise ValidationFailed(_ENTITY, "ordina", f"atteso uno fra {', '.join(ORDINA_VALUES)}")
    if ordina is None:
        return _Order(
            SortSpec("score", "float") if term else SortSpec("created_at", "datetime"), True
        )
    if ordina == "recenti":
        return _Order(SortSpec("created_at", "datetime"), True)
    field, direction = ordina.split("_")
    ranked_by: _RankedBy = "tariffa" if field == "tariffa" else "anni"
    # The whole `ordina` is the cursor's key, direction included: a cursor minted going
    # up must not be replayed going down, where `(value, id) > ...` would skip and
    # repeat rows.
    return _Order(SortSpec(ordina, "float"), direction == "desc", ranked_by)


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
    posizione: str | None
    remoto: str | None
    tariffa_min: Decimal | None
    tariffa_max: Decimal | None
    origine: str | None
    pagina_origine: str | None
    utm_source: str | None
    has_cv: bool | None
    con_accessi: bool | None
    creato_da: datetime | None
    creato_a: datetime | None
    competenze: tuple[str, ...]
    luogo: str | None
    seniority: tuple[str, ...]
    anni_min: int | None
    anni_max: int | None
    settori: tuple[str, ...]
    lingue: tuple[str, ...]
    scheda: str | None
    ranked_by: _RankedBy | None

    @property
    def card_only(self) -> bool:
        """Whether a lead can never satisfy these: any filter `_card_only_active`
        names, `origine` `wizard`/`admin` (a lead is `form`), or a ranking by a card
        value."""
        return _card_only_active(
            self.posizione,
            self.remoto,
            self.tariffa_min,
            self.tariffa_max,
            self.origine if self.origine != _ORIGIN_FORM else None,
            self.pagina_origine,
            self.competenze,
            self.luogo,
            self.seniority,
            self.anni_min,
            self.anni_max,
            self.settori,
            self.lingue,
            self.scheda,
            self.ranked_by,
        )

    @property
    def leads_possible(self) -> bool:
        """Whether a bare sign-up can be in the answer at all, `stato` aside."""
        return not self.card_only and self.has_cv is not True


def _card_stmt(f: _Filters) -> Select[Freelancer, User]:
    """Every freelancer card, `stato` left for the caller to filter separately -- this
    statement backs both the listing and `_counts`' per-state `GROUP BY`, and the
    counts must see every state at once."""
    stmt = select(Freelancer, User).join(User, User.id == Freelancer.user_id)
    stmt = stmt.where(Freelancer.deleted_at.is_(None))
    if f.q:
        search_cols = (User.nome, User.cognome, User.email, Freelancer.posizione)
        stmt = stmt.where(matches_any(search_cols, f.q))
    if f.posizione:
        stmt = stmt.where(Freelancer.posizione.ilike(f"%{escape_like(f.posizione)}%"))
    if f.remoto is not None:
        stmt = stmt.where(Freelancer.remoto == f.remoto)
    if f.tariffa_min is not None:
        stmt = stmt.where(Freelancer.tariffa_giornaliera >= f.tariffa_min)
    if f.tariffa_max is not None:
        stmt = stmt.where(Freelancer.tariffa_giornaliera <= f.tariffa_max)
    if f.origine == _ORIGIN_FORM:
        # `form` is a bare sign-up: no card is one.
        stmt = stmt.where(false())
    elif f.origine is not None:
        stmt = stmt.where(Freelancer.compilata_da == _COMPILATA_DA_BY_ORIGIN[f.origine])
    if f.pagina_origine is not None:
        stmt = stmt.where(Freelancer.origine == f.pagina_origine)
    if f.utm_source is not None:
        stmt = stmt.where(Freelancer.utm_source == f.utm_source)
    if f.has_cv is not None:
        stmt = stmt.where(
            Freelancer.cv_filename.isnot(None) if f.has_cv else Freelancer.cv_filename.is_(None)
        )
    if f.con_accessi is not None:
        login_exists = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
        stmt = stmt.where(login_exists if f.con_accessi else ~login_exists)
    if f.creato_da is not None:
        stmt = stmt.where(Freelancer.created_at >= f.creato_da)
    if f.creato_a is not None:
        stmt = stmt.where(Freelancer.created_at <= f.creato_a)
    for skill in f.competenze:
        stmt = stmt.where(_names_skill(skill))
    if f.luogo:
        stmt = stmt.where(_lives_in(f.luogo))
    if f.seniority:
        stmt = stmt.where(_has_seniority(f.seniority))
    if f.anni_min is not None:
        stmt = stmt.where(_card_anni() >= f.anni_min)
    if f.anni_max is not None:
        stmt = stmt.where(_card_anni() <= f.anni_max)
    for sector in f.settori:
        stmt = stmt.where(_card_list_names("settori", sector))
    for language in f.lingue:
        stmt = stmt.where(_card_list_names("lingue", language))
    if f.scheda is not None:
        stmt = stmt.where(_scheda_is(f.scheda))
    if f.ranked_by == "tariffa":
        stmt = stmt.where(Freelancer.tariffa_giornaliera.is_not(None))
    elif f.ranked_by == "anni":
        stmt = stmt.where(_card_anni().is_not(None))
    return stmt


def _lead_stmt(f: _Filters) -> Select[Signup]:
    """Every bare sign-up (no card), the same anti-join `_card_emails` describes."""
    card_emails = _card_emails()
    stmt = (
        select(Signup)
        .outerjoin(card_emails, card_emails.c.email == func.lower(Signup.email))
        .where(card_emails.c.email.is_(None))
    )
    if f.q:
        stmt = stmt.where(matches_any((Signup.nome, Signup.cognome, Signup.email), f.q))
    if f.utm_source is not None:
        stmt = stmt.where(Signup.utm_source == f.utm_source)
    if f.con_accessi is not None:
        # A lead has no `user_id` of its own; the address is the only thing it can be
        # matched to a `users`/`logins` row by, the same lowercased join `_card_read`'s
        # sibling `FreelancerService._signed_up` uses elsewhere in this package.
        lead_user = aliased(User)
        login_exists = exists(
            select(Login.id)
            .join(lead_user, lead_user.id == Login.user_id)
            .where(func.lower(lead_user.email) == func.lower(Signup.email))
        )
        stmt = stmt.where(login_exists if f.con_accessi else ~login_exists)
    if f.creato_da is not None:
        stmt = stmt.where(Signup.created_at >= f.creato_da)
    if f.creato_a is not None:
        stmt = stmt.where(Signup.created_at <= f.creato_a)
    return stmt


class TalentiService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def row_for_freelancer(self, freelancer_id: UUID) -> TalentoRead:
        """The list's own row for one freelancer, deleted or not (the caller decides
        what a deleted card means): what `get_talento` merges over the admin detail, so
        a card reads the same in both. `NotFound` for an id that is no freelancer."""
        found = self.session.execute(
            select(Freelancer, User, FreelancerCard.card.label("scheda"))
            .join(User, User.id == Freelancer.user_id)
            .outerjoin(FreelancerCard, FreelancerCard.freelancer_id == Freelancer.id)
            .where(Freelancer.id == freelancer_id)
            .options(defer(Freelancer.cv_bytes))
        ).first()
        if found is None:
            raise NotFound("talento", freelancer_id)
        return _card_read(found[0], found[1], found.scheda)

    def list_recent(
        self,
        limit: int = LIST_LIMIT_DEFAULT,
        stato: str | None = None,
        *,
        q: str | None = None,
        cursor: str | None = None,
        posizione: str | None = None,
        remoto: str | None = None,
        tariffa_min: Decimal | None = None,
        tariffa_max: Decimal | None = None,
        origine: str | None = None,
        pagina_origine: str | None = None,
        utm_source: str | None = None,
        has_cv: bool | None = None,
        con_accessi: bool | None = None,
        creato_da: datetime | None = None,
        creato_a: datetime | None = None,
        competenze: Sequence[str] | None = None,
        luogo: str | None = None,
        seniority: Sequence[str] | None = None,
        anni_min: int | None = None,
        anni_max: int | None = None,
        settori: Sequence[str] | None = None,
        lingue: Sequence[str] | None = None,
        scheda: str | None = None,
        ordina: str | None = None,
    ) -> TalentoList:
        """Every card and every bare sign-up as one row, newest-or-best-match first,
        `stato` `lead` for the bare ones.

        `stato` filters the same way it always has: `"lead"` selects the bare sign-ups
        alone, any other value selects `Freelancer.stato == stato` (an unknown one is
        simply an empty list, like `FreelancerService.list_recent`'s own pass-through
        filter -- REB-285 does not change that), and `None` merges both. Every other
        filter narrows both sides at once, except the card-only ones
        (`_card_only_active`), which a lead can never satisfy and so drop it from the
        merge outright, and `has_cv`, which a lead satisfies only as `False`.

        `origine` is what the row shows (`form`, `wizard`, `admin`; anything else is a
        `ValidationFailed`, not an empty list that reads like «nobody»), and
        `pagina_origine` the marketing page the person started from (REB-798).

        `competenze` keeps the cards that name every one of its terms (REB-778), each
        in the position or in the anonymous card's role, skills or summary; `luogo`
        keeps the cards whose anonymous card places them there. Blank terms are ignored.
        REB-801 adds, on the same anonymous card: `seniority` (one of `SENIORITIES`,
        any of those given), `anni_min`/`anni_max` (inclusive), `settori` and `lingue`
        (every term must be named by one element, a substring, like `competenze`), and
        `scheda`, the card's own state (`SCHEDA_STATES`).

        `ordina` is `recenti` (newest first, even while searching), or `tariffa_asc`,
        `tariffa_desc`, `anni_asc`, `anni_desc`: the cursor then carries that value, and
        the rows with none to rank by (every lead, a card with no rate or no years) are
        left out of the list and of its counts. Without it the order is newest first, or
        best match while `q` searches."""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        term = (q or "").strip()
        # A blank is no filter, the way a blank `q` or `luogo` always was.
        origine = (origine or "").strip().lower() or None
        pagina_origine = (pagina_origine or "").strip() or None
        scheda = (scheda or "").strip().lower() or None
        ordina = (ordina or "").strip().lower() or None
        order = _order(ordina, term)
        if origine is not None and origine not in ORIGINS:
            raise ValidationFailed(_ENTITY, "origine", f"atteso uno fra {', '.join(ORIGINS)}")
        if scheda is not None and scheda not in SCHEDA_STATES:
            raise ValidationFailed(_ENTITY, "scheda", f"atteso uno fra {', '.join(SCHEDA_STATES)}")
        f = _Filters(
            q=term,
            posizione=posizione,
            remoto=remoto,
            tariffa_min=tariffa_min,
            tariffa_max=tariffa_max,
            origine=origine,
            pagina_origine=pagina_origine,
            utm_source=utm_source,
            has_cv=has_cv,
            con_accessi=con_accessi,
            creato_da=creato_da,
            creato_a=creato_a,
            competenze=_terms(competenze),
            luogo=(luogo or "").strip() or None,
            seniority=_choices("seniority", seniority, SENIORITIES),
            anni_min=anni_min,
            anni_max=anni_max,
            settori=_terms(settori),
            lingue=_terms(lingue),
            scheda=scheda,
            ranked_by=order.ranked_by,
        )
        wants_cards = stato is None or stato != LEAD_STATE
        wants_leads = (stato is None or stato == LEAD_STATE) and f.leads_possible

        sort_spec = order.spec
        cursor_bound: tuple[datetime | float, UUID] | None = (
            decode_cursor(sort_spec, cursor) if cursor else None
        )

        candidates: list[tuple[datetime | float, UUID, TalentoRead]] = []

        if wants_cards:
            stmt = _card_stmt(f)
            if stato is not None:
                stmt = stmt.where(Freelancer.stato == stato)
            sort_col: Any
            if order.ranked_by == "tariffa":
                sort_col = Freelancer.tariffa_giornaliera
            elif order.ranked_by == "anni":
                sort_col = _card_anni()
            elif sort_spec.key == "score":
                search_cols = (User.nome, User.cognome, User.email, Freelancer.posizione)
                sort_col = similarity_score(search_cols, term)
            else:
                sort_col = Freelancer.created_at
            if sort_spec.key != "created_at":
                stmt = stmt.add_columns(sort_col.label("ordine"))
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
            ).add_columns(FreelancerCard.card.label("scheda"))
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
                card, user = row[0], row[1]
                # A Postgres `numeric` comes back as a `Decimal`; the cursor carries
                # JSON numbers, so a ranked value is a `float` on both sides.
                sort_value = card.created_at if sort_spec.key == "created_at" else float(row.ordine)
                candidates.append((sort_value, card.id, _card_read(card, user, row.scheda)))

        if wants_leads:
            lead_stmt = _lead_stmt(f)
            if sort_spec.key == "score":
                lead_score = similarity_score((Signup.nome, Signup.cognome, Signup.email), term)
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
        next_cursor = (
            encode_cursor(sort_spec, page[-1][0], page[-1][1])
            if len(candidates) > limit and page
            else None
        )

        per_stato = self._counts(f)
        totale = sum(per_stato.values()) if stato is None else per_stato.get(stato, 0)
        return TalentoList(totale=totale, items=items, per_stato=per_stato, next_cursor=next_cursor)

    def _counts(self, f: _Filters) -> dict[str, int]:
        """How many rows sit in each state, `lead` included, with every filter but
        `stato` itself applied (REB-285) -- the numbers the admin area's tabs need
        beside the page, answering "how many if I picked this one" rather than "how
        many exist at all"."""
        grouped = self.session.execute(
            _card_stmt(f)
            .with_only_columns(Freelancer.stato, func.count())
            .group_by(Freelancer.stato)
        ).all()
        by_state: dict[str, int] = {stato: count for stato, count in grouped}
        counts = {stato: by_state.get(stato, 0) for stato in FREELANCER_STATES}

        if f.leads_possible:
            counts[LEAD_STATE] = (
                self.session.scalar(_lead_stmt(f).with_only_columns(func.count())) or 0
            )
        else:
            counts[LEAD_STATE] = 0
        return counts
