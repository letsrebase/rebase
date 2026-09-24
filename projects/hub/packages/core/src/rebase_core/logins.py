"""Who entered the hub, and when: the numbers the admin area reads off `logins`.

The rows are written by `UserService.enter` and by nothing else (ORB-158); this module
only reads them. Since REB-278 a login is for any signed-in person, not only a
freelancer, so `user_id` is the join; migration B (REB-281) renamed the table from
`member_logins` to `logins`, once "member" stopped describing who is in it, and dropped
the `freelancer_id` it replaced. `membri_totali` stays a count of `freelancers`, since
the page still reads as "how many of the community's cards have logged in". The shape is
`PerkService.guide_stats` (ORB-156), so the two admin pages read the same way.

REB-313 turns `recenti` from a hardcoded top 20 into a genuinely searchable,
cursor-paginated sub-list, the same search-and-cursor shape REB-285 gives Talenti and
Aziende -- newest first with no term, best-match first once `q` narrows it by the
person's `nome`/`cognome`/`email`. The four aggregate counters beside it (`totale`,
`membri`, `membri_totali`, `ultimi_7_giorni`) are unaffected by `q`: they always read
the whole table, not the filtered page, exactly as before REB-313.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from rebase_core.models import ATTRIBUTION_COLUMNS, Freelancer, Login, User
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.schemas import LoginRead, LoginStats
from rebase_core.search import matches_any, similarity_score

LOGIN_LIST_LIMIT_DEFAULT = 100
LOGIN_LIST_LIMIT_MAX = 500
# The short list a card's own detail shows (REB-284), well below the admin's own
# `/logins` page: an admin reading one person does not need their last twenty.
RECENT_LOGINS_FOR_CARD = 5
_LOGIN_SEARCH_COLUMNS = (User.nome, User.cognome, User.email)


class LoginService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def stats(
        self,
        now: datetime | None = None,
        *,
        limit: int = LOGIN_LIST_LIMIT_DEFAULT,
        q: str | None = None,
        cursor: str | None = None,
    ) -> LoginStats:
        moment = now or datetime.now(UTC)
        week_ago = moment - timedelta(days=7)
        totale = self.session.scalar(select(func.count()).select_from(Login)) or 0
        membri = self.session.scalar(select(func.count(func.distinct(Login.user_id)))) or 0
        membri_totali = self.session.scalar(select(func.count()).select_from(Freelancer)) or 0
        ultimi = (
            self.session.scalar(
                select(func.count()).select_from(Login).where(Login.logged_at >= week_ago)
            )
            or 0
        )

        limit = max(1, min(limit, LOGIN_LIST_LIMIT_MAX))
        term = (q or "").strip()
        stmt = select(Login, User).join(User, User.id == Login.user_id)
        if term:
            stmt = stmt.where(matches_any(_LOGIN_SEARCH_COLUMNS, term))
        sort_spec = SortSpec("score", "float") if term else SortSpec("logged_at", "datetime")
        sort_column: Any
        if term:
            score = similarity_score(_LOGIN_SEARCH_COLUMNS, term).label("score")
            stmt = stmt.add_columns(score)
            sort_column = score
        else:
            sort_column = Login.logged_at
        if cursor:
            value, row_id = decode_cursor(sort_spec, cursor)
            stmt = stmt.where(keyset_predicate(sort_column, Login.id, value, row_id))
        rows = self.session.execute(
            stmt.order_by(sort_column.desc(), Login.id.desc()).limit(limit + 1)
        ).all()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            last_sort = last.score if term else last[0].logged_at
            next_cursor = encode_cursor(sort_spec, last_sort, last[0].id)

        return LoginStats(
            totale=totale,
            membri=membri,
            membri_totali=membri_totali,
            ultimi_7_giorni=ultimi,
            recenti=[_read(row[0], row[1]) for row in page_rows],
            next_cursor=next_cursor,
        )

    def for_user(self, user_id: UUID, limit: int = RECENT_LOGINS_FOR_CARD) -> list[LoginRead]:
        """The last few times this one person entered (REB-284's freelancer detail):
        the same shape `stats` reads for everybody, scoped to a single `user_id`
        instead of grouped across the whole hub."""
        rows = self.session.execute(
            select(Login, User)
            .join(User, User.id == Login.user_id)
            .where(Login.user_id == user_id)
            .order_by(Login.logged_at.desc(), Login.id.desc())
            .limit(limit)
        ).all()
        return [_read(login, person) for login, person in rows]


def _read(login: Login, person: User) -> LoginRead:
    """One row as both lists show it, the campaign it came from included (REB-426)."""
    return LoginRead(
        id=login.id,
        user_id=login.user_id,
        nome=person.nome,
        cognome=person.cognome,
        email=person.email,
        logged_at=login.logged_at,
        **{column: getattr(login, column) for column in ATTRIBUTION_COLUMNS},
    )
