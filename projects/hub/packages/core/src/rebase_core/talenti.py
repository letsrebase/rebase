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
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, Subquery, exists, func, select
from sqlalchemy.orm import Session, aliased, defer

from rebase_core.freelancers import LEAD_STATE
from rebase_core.models import FREELANCER_STATES, Freelancer, Login, Signup, User
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.schemas import TalentoList, TalentoOrigine, TalentoRead
from rebase_core.search import escape_like, matches_any, similarity_score

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
_ORIGIN_FORM: TalentoOrigine = "form"
# `COMPILATA_DA`'s two values (`models.py`), named for what an admin reading the list
# actually asks: did the person write this themselves, through the wizard, or did an
# admin draft it from research.
_ORIGIN_BY_COMPILATA_DA: dict[str, TalentoOrigine] = {"persona": "wizard", "admin": "admin"}

# Filters with no lead-side equivalent at all: `posizione`, `remoto`,
# `tariffa_giornaliera` and the marketing `origine` are columns a bare sign-up simply
# does not have (`Signup` carries none of them), so a non-empty value here can only
# ever exclude every lead, and the lead query is skipped outright rather than issued
# for a result that can only be empty. `has_cv` is handled separately, since
# `has_cv=False` is a value every lead genuinely satisfies.
_CARD_ONLY = ("posizione", "remoto", "tariffa_min", "tariffa_max", "origine")


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


def _signup_read(row: Signup) -> TalentoRead:
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


def _card_read(row: Freelancer, user: User) -> TalentoRead:
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
    )


def _card_stmt(
    *,
    q: str,
    posizione: str | None,
    remoto: str | None,
    tariffa_min: Decimal | None,
    tariffa_max: Decimal | None,
    origine: str | None,
    utm_source: str | None,
    has_cv: bool | None,
    con_accessi: bool | None,
    creato_da: datetime | None,
    creato_a: datetime | None,
) -> Select[Any]:
    """Every freelancer card, `stato` left for the caller to filter separately -- this
    statement backs both the listing and `_counts`' per-state `GROUP BY`, and the
    counts must see every state at once."""
    stmt = select(Freelancer, User).join(User, User.id == Freelancer.user_id)
    stmt = stmt.where(Freelancer.deleted_at.is_(None))
    if q:
        search_cols = (User.nome, User.cognome, User.email, Freelancer.posizione)
        stmt = stmt.where(matches_any(search_cols, q))
    if posizione:
        stmt = stmt.where(Freelancer.posizione.ilike(f"%{escape_like(posizione)}%"))
    if remoto is not None:
        stmt = stmt.where(Freelancer.remoto == remoto)
    if tariffa_min is not None:
        stmt = stmt.where(Freelancer.tariffa_giornaliera >= tariffa_min)
    if tariffa_max is not None:
        stmt = stmt.where(Freelancer.tariffa_giornaliera <= tariffa_max)
    if origine is not None:
        stmt = stmt.where(Freelancer.origine == origine)
    if utm_source is not None:
        stmt = stmt.where(Freelancer.utm_source == utm_source)
    if has_cv is not None:
        stmt = stmt.where(
            Freelancer.cv_filename.isnot(None) if has_cv else Freelancer.cv_filename.is_(None)
        )
    if con_accessi is not None:
        login_exists = exists(select(Login.id).where(Login.user_id == Freelancer.user_id))
        stmt = stmt.where(login_exists if con_accessi else ~login_exists)
    if creato_da is not None:
        stmt = stmt.where(Freelancer.created_at >= creato_da)
    if creato_a is not None:
        stmt = stmt.where(Freelancer.created_at <= creato_a)
    return stmt


def _lead_stmt(
    *,
    q: str,
    utm_source: str | None,
    con_accessi: bool | None,
    creato_da: datetime | None,
    creato_a: datetime | None,
) -> Select[Any]:
    """Every bare sign-up (no card), the same anti-join `_card_emails` describes."""
    card_emails = _card_emails()
    stmt = (
        select(Signup)
        .outerjoin(card_emails, card_emails.c.email == func.lower(Signup.email))
        .where(card_emails.c.email.is_(None))
    )
    if q:
        stmt = stmt.where(matches_any((Signup.nome, Signup.cognome, Signup.email), q))
    if utm_source is not None:
        stmt = stmt.where(Signup.utm_source == utm_source)
    if con_accessi is not None:
        # A lead has no `user_id` of its own; the address is the only thing it can be
        # matched to a `users`/`logins` row by, the same lowercased join `_card_read`'s
        # sibling `FreelancerService._signed_up` uses elsewhere in this package.
        lead_user = aliased(User)
        login_exists = exists(
            select(Login.id)
            .join(lead_user, lead_user.id == Login.user_id)
            .where(func.lower(lead_user.email) == func.lower(Signup.email))
        )
        stmt = stmt.where(login_exists if con_accessi else ~login_exists)
    if creato_da is not None:
        stmt = stmt.where(Signup.created_at >= creato_da)
    if creato_a is not None:
        stmt = stmt.where(Signup.created_at <= creato_a)
    return stmt


class TalentiService:
    def __init__(self, session: Session) -> None:
        self.session = session

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
        utm_source: str | None = None,
        has_cv: bool | None = None,
        con_accessi: bool | None = None,
        creato_da: datetime | None = None,
        creato_a: datetime | None = None,
    ) -> TalentoList:
        """Every card and every bare sign-up as one row, newest-or-best-match first,
        `stato` `lead` for the bare ones.

        `stato` filters the same way it always has: `"lead"` selects the bare sign-ups
        alone, any other value selects `Freelancer.stato == stato` (an unknown one is
        simply an empty list, like `FreelancerService.list_recent`'s own pass-through
        filter -- REB-285 does not change that), and `None` merges both. Every other
        filter narrows both sides at once, except the five in `_CARD_ONLY`, which a
        lead can never satisfy and so drop it from the merge outright, and `has_cv`,
        which a lead satisfies only as `False`.
        """
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        term = (q or "").strip()
        card_only_active = any(
            value not in (None, "")
            for value in (posizione, remoto, tariffa_min, tariffa_max, origine)
        )
        wants_cards = stato is None or stato != LEAD_STATE
        wants_leads = (
            (stato is None or stato == LEAD_STATE) and not card_only_active and has_cv is not True
        )

        sort_spec = SortSpec("score", "float") if term else SortSpec("created_at", "datetime")
        cursor_bound: tuple[datetime | float, UUID] | None = (
            decode_cursor(sort_spec, cursor) if cursor else None
        )

        candidates: list[tuple[datetime | float, UUID, TalentoRead]] = []

        if wants_cards:
            stmt = _card_stmt(
                q=term,
                posizione=posizione,
                remoto=remoto,
                tariffa_min=tariffa_min,
                tariffa_max=tariffa_max,
                origine=origine,
                utm_source=utm_source,
                has_cv=has_cv,
                con_accessi=con_accessi,
                creato_da=creato_da,
                creato_a=creato_a,
            )
            if stato is not None:
                stmt = stmt.where(Freelancer.stato == stato)
            sort_col: Any
            if term:
                search_cols = (User.nome, User.cognome, User.email, Freelancer.posizione)
                score = similarity_score(search_cols, term).label("score")
                stmt = stmt.add_columns(score)
                sort_col = score
            else:
                sort_col = Freelancer.created_at
            if cursor_bound is not None:
                stmt = stmt.where(
                    keyset_predicate(sort_col, Freelancer.id, cursor_bound[0], cursor_bound[1])
                )
            # A row reads `cv_size` and the file's name, never the PDF itself: a page of
            # 100 cards (or a campaign's whole list, P-REB-41) must not carry 100 CVs.
            rows = self.session.execute(
                stmt.order_by(sort_col.desc(), Freelancer.id.desc())
                .limit(limit + 1)
                .options(defer(Freelancer.cv_bytes))
            ).all()
            for row in rows:
                card, user = row[0], row[1]
                sort_value = row[2] if term else card.created_at
                candidates.append((sort_value, card.id, _card_read(card, user)))

        if wants_leads:
            lead_stmt = _lead_stmt(
                q=term,
                utm_source=utm_source,
                con_accessi=con_accessi,
                creato_da=creato_da,
                creato_a=creato_a,
            )
            if term:
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
                sort_value = lead_row[1] if term else signup.created_at
                candidates.append((sort_value, signup.id, _signup_read(signup)))

        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        page = candidates[:limit]
        items = [item for _, _, item in page]
        next_cursor = (
            encode_cursor(sort_spec, page[-1][0], page[-1][1])
            if len(candidates) > limit and page
            else None
        )

        per_stato = self._counts(
            q=term,
            posizione=posizione,
            remoto=remoto,
            tariffa_min=tariffa_min,
            tariffa_max=tariffa_max,
            origine=origine,
            utm_source=utm_source,
            has_cv=has_cv,
            con_accessi=con_accessi,
            creato_da=creato_da,
            creato_a=creato_a,
        )
        totale = sum(per_stato.values()) if stato is None else per_stato.get(stato, 0)
        return TalentoList(totale=totale, items=items, per_stato=per_stato, next_cursor=next_cursor)

    def _counts(
        self,
        *,
        q: str,
        posizione: str | None,
        remoto: str | None,
        tariffa_min: Decimal | None,
        tariffa_max: Decimal | None,
        origine: str | None,
        utm_source: str | None,
        has_cv: bool | None,
        con_accessi: bool | None,
        creato_da: datetime | None,
        creato_a: datetime | None,
    ) -> dict[str, int]:
        """How many rows sit in each state, `lead` included, with every filter but
        `stato` itself applied (REB-285) -- the numbers the admin area's tabs need
        beside the page, answering "how many if I picked this one" rather than "how
        many exist at all"."""
        card_stmt = _card_stmt(
            q=q,
            posizione=posizione,
            remoto=remoto,
            tariffa_min=tariffa_min,
            tariffa_max=tariffa_max,
            origine=origine,
            utm_source=utm_source,
            has_cv=has_cv,
            con_accessi=con_accessi,
            creato_da=creato_da,
            creato_a=creato_a,
        )
        grouped = self.session.execute(
            card_stmt.with_only_columns(Freelancer.stato, func.count()).group_by(Freelancer.stato)
        ).all()
        by_state: dict[str, int] = {stato: count for stato, count in grouped}
        counts = {stato: by_state.get(stato, 0) for stato in FREELANCER_STATES}

        card_only_active = any(
            value not in (None, "")
            for value in (posizione, remoto, tariffa_min, tariffa_max, origine)
        )
        if card_only_active or has_cv is True:
            counts[LEAD_STATE] = 0
        else:
            lead_stmt = _lead_stmt(
                q=q,
                utm_source=utm_source,
                con_accessi=con_accessi,
                creato_da=creato_da,
                creato_a=creato_a,
            )
            counts[LEAD_STATE] = self.session.scalar(lead_stmt.with_only_columns(func.count())) or 0
        return counts
