"""Identity: one `users` row per person, and the one way in -- the magic link -- for a
member and an admin alike (REB-278, design record 2026-09-17 §4).

Owns get-or-create by lowercased email, session open/close, and the magic link's
request-and-enter, moved out of the person-matching pieces `MemberService` and the
password-login `AdminService` REB-281 retired: `MemberService` keeps what is specific
to the freelancer card, and the admin list/promote/demote pair lives here alongside
everything else that means the person.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminList, AdminRead
from rebase_core.config import Settings
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.mail import Mail, magic_link_mail
from rebase_core.models import USER_ROLES, Login, MagicLinkToken, User, UserSession
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.search import matches_any, similarity_score

ENTITY = "user"
ADMIN_LIST_LIMIT_DEFAULT = 100
ADMIN_LIST_LIMIT_MAX = 500
_ADMIN_SEARCH_COLUMNS = (User.nome, User.cognome, User.email)
_LINK_REQUEST_THROTTLE_SECONDS = 60


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class UserService:
    """`settings` is required for the session/magic-link methods (deadlines, the link's
    minutes, `hub_url`) and unused by the pure identity ones below, so a caller that
    only needs `by_email`/`get_or_create` -- `FreelancerService`, `CompanyService` --
    may construct this with `session` alone."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings

    # ---- identity ----------------------------------------------------------------

    def by_email(self, email: str) -> User | None:
        lowered = email.strip().lower()
        return self.session.scalar(select(User).where(func.lower(User.email) == lowered))

    def get_or_create(
        self,
        email: str,
        nome: str = "",
        cognome: str = "",
        linkedin_url: str | None = None,
        telefono: str | None = None,
    ) -> User:
        """The `users` row for this address, made on the spot when none exists yet: one
        row per lowercased email, never two for a repeat application or request racing
        itself, the same guard `Freelancer.apply` already keeps for its own table.
        `nome`/`cognome`/`linkedin_url` are left exactly as they were found on a
        repeat call, never overwritten by a later request's own answer. `telefono`
        (REB-380) is different: a `users` row can predate it entirely (a freelancer
        application never asked for one, and it did not exist before migration 0016),
        so a still-`None` phone number is backfilled from whichever call first
        supplies one -- "leave as found" here means "never overwrite a real answer,"
        not "never touch a blank one." Left uncommitted on purpose: every caller of
        this method commits its own row right after (`CompanyService.request`, the
        only caller that ever passes a real `telefono`), so the backfill lands in the
        same transaction as whatever the caller was doing, atomically -- committing it
        here would persist the phone number even if the caller's own insert then
        failed."""
        row = self.by_email(email)
        if row is not None:
            if telefono is not None and row.telefono is None:
                row.telefono = telefono
            return row
        row = User(
            email=email.strip().lower(),
            nome=nome,
            cognome=cognome,
            linkedin_url=linkedin_url,
            telefono=telefono,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            existing = self.by_email(email)
            assert existing is not None
            return existing
        return row

    def list_admins(
        self,
        limit: int = ADMIN_LIST_LIMIT_DEFAULT,
        *,
        q: str | None = None,
        cursor: str | None = None,
    ) -> AdminList:
        """Every admin (ORB-123): oldest first with no search term, so the page still
        reads as a history -- the same order `AdminService.list` gave `admin_users`,
        now a filter on `users` -- or best-match first once `q` narrows it (REB-313,
        the same search-and-cursor shape REB-285 gives Talenti and Aziende).
        `next_cursor` is `None` on the last page."""
        limit = max(1, min(limit, ADMIN_LIST_LIMIT_MAX))
        term = (q or "").strip()
        stmt = select(User).where(User.role == "admin")
        if term:
            stmt = stmt.where(matches_any(_ADMIN_SEARCH_COLUMNS, term))
        sort_spec = SortSpec("score", "float") if term else SortSpec("created_at", "datetime")
        sort_column: Any
        if term:
            score = similarity_score(_ADMIN_SEARCH_COLUMNS, term).label("score")
            stmt = stmt.add_columns(score)
            sort_column = score
        else:
            sort_column = User.created_at
        descending = bool(term)
        if cursor:
            value, row_id = decode_cursor(sort_spec, cursor)
            stmt = stmt.where(
                keyset_predicate(sort_column, User.id, value, row_id, descending=descending)
            )
        order = (
            (sort_column.desc(), User.id.desc())
            if descending
            else (sort_column.asc(), User.id.asc())
        )
        rows = self.session.execute(stmt.order_by(*order).limit(limit + 1)).all()
        page_rows = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page_rows:
            last = page_rows[-1]
            last_sort = last.score if term else last[0].created_at
            next_cursor = encode_cursor(sort_spec, last_sort, last[0].id)
        return AdminList(
            items=[AdminRead.model_validate(row[0]) for row in page_rows],
            next_cursor=next_cursor,
        )

    def promote(
        self, email: str, nome: str | None = None, cognome: str | None = None
    ) -> tuple[User, bool]:
        """Whatever `users` row already answers to this address is promoted with one
        click and no form; an address with none yet needs `nome`/`cognome` to create a
        bare row first, no freelancer card invented for it (§1, Nav and Amministratori).
        The second element is whether a row was created, so the caller knows to mail a
        fresh promotion the same link everyone else gets."""
        row = self.by_email(email)
        created = row is None
        if row is None:
            nome, cognome = (nome or "").strip(), (cognome or "").strip()
            if not nome or not cognome:
                raise ValidationFailed(
                    ENTITY, "nome", "servono nome e cognome per un indirizzo nuovo"
                )
            row = User(email=email.strip().lower(), nome=nome, cognome=cognome)
            self.session.add(row)
        row.role = "admin"
        self.session.commit()
        return row, created

    def demote(self, user_id: UUID) -> User:
        """Sets `role = 'member'`, fully reversible since nothing is deleted."""
        row = self.session.get(User, user_id)
        if row is None:
            raise NotFound(ENTITY, user_id)
        row.role = "member"
        self.session.commit()
        return row

    def set_role(
        self, email: str, role: str, nome: str = "", cognome: str = ""
    ) -> tuple[User, bool]:
        """`rebase setrole`'s own shape: a minimal row created when none exists
        (`nome`/`cognome` required for it), the role set either way."""
        if role not in USER_ROLES:
            raise ValidationFailed(ENTITY, "role", f"uno fra {', '.join(USER_ROLES)}")
        row = self.by_email(email)
        created = row is None
        if row is None:
            nome, cognome = nome.strip(), cognome.strip()
            if not nome or not cognome:
                raise ValidationFailed(
                    ENTITY, "nome", "servono nome e cognome per un indirizzo nuovo"
                )
            row = User(email=email.strip().lower(), nome=nome, cognome=cognome)
            self.session.add(row)
        row.role = role
        self.session.commit()
        return row, created

    # ---- the way in ----------------------------------------------------------------

    def request_link(self, email: str, note: str | None = None) -> Mail | None:
        """The mail to send, or `None` when nobody with that address exists, or when
        the address already holds a live link younger than a minute (REB-100): no new
        token, no mail, the same silence a caller sees either way -- the throttle is
        per address, not per client, and sits beside the per-client bucket `spend_one`
        already charges in the router. Sweeps the person's spent and expired tokens
        first: nothing needs a cron."""
        assert self.settings is not None
        row = self.by_email(email)
        if row is None:
            return None
        now = datetime.now(UTC)
        self.session.execute(
            delete(MagicLinkToken).where(
                MagicLinkToken.user_id == row.id,
                or_(MagicLinkToken.used_at.is_not(None), MagicLinkToken.expires_at <= now),
            )
        )
        recent = self.session.scalar(
            select(MagicLinkToken).where(
                MagicLinkToken.user_id == row.id,
                MagicLinkToken.used_at.is_(None),
                MagicLinkToken.expires_at > now,
                MagicLinkToken.created_at > now - timedelta(seconds=_LINK_REQUEST_THROTTLE_SECONDS),
            )
        )
        if recent is not None:
            return None
        raw = secrets.token_urlsafe(32)
        self.session.add(
            MagicLinkToken(
                user_id=row.id,
                token_hash=_hash(raw),
                expires_at=now + timedelta(minutes=self.settings.magic_link_minutes),
            )
        )
        self.session.commit()
        link = f"{self.settings.hub_url.rstrip('/')}/entra?t={raw}"
        return magic_link_mail(row.email, link, self.settings.magic_link_minutes, note)

    def enter(self, raw_token: str) -> tuple[User, str] | None:
        """The person and the raw session token for the cookie, or `None` for a wrong,
        spent or expired link. Spent by a conditional update gated on it still being
        unused, so of two requests racing on the same raw token only one opens a
        session (`MemberService.enter`'s own reasoning, unchanged)."""
        assert self.settings is not None
        if not raw_token:
            return None
        now = datetime.now(UTC)
        token = self.session.scalar(
            select(MagicLinkToken).where(MagicLinkToken.token_hash == _hash(raw_token))
        )
        if token is None or token.used_at is not None or token.expires_at <= now:
            return None
        row = self.session.get(User, token.user_id)
        if row is None:
            return None
        spent = self.session.execute(
            update(MagicLinkToken)
            .where(MagicLinkToken.id == token.id, MagicLinkToken.used_at.is_(None))
            .values(used_at=now)
            .returning(MagicLinkToken.id)
        )
        if len(spent.scalars().all()) != 1:
            self.session.rollback()
            return None
        raw_session = secrets.token_urlsafe(32)
        self.session.add(
            UserSession(
                user_id=row.id, token_hash=_hash(raw_session), expires_at=self._deadline(now)
            )
        )
        # The login itself, kept after the session is gone (ORB-158): same commit, so a
        # session never exists without its login and a login never without its session.
        self.session.add(Login(user_id=row.id, logged_at=now))
        self.session.commit()
        return row, raw_session

    def resolve(self, raw: str | None) -> User | None:
        """The person behind a cookie, or `None`. Slides the expiry on every hit and
        forgets a session past its deadline the moment it is presented."""
        assert self.settings is not None
        if not raw:
            return None
        session_row = self.session.scalar(
            select(UserSession).where(UserSession.token_hash == _hash(raw))
        )
        if session_row is None:
            return None
        now = datetime.now(UTC)
        if session_row.expires_at <= now:
            self.session.delete(session_row)
            self.session.commit()
            return None
        row = self.session.get(User, session_row.user_id)
        if row is None:
            return None
        session_row.expires_at = self._deadline(now)
        self.session.commit()
        return row

    def resolve_admin(self, raw: str | None) -> User | None:
        """`resolve`, checked for the admin role: `None` for a signed-out cookie and
        for a real, signed-in member alike -- the caller decides which is a 401 and
        which is a 403."""
        user = self.resolve(raw)
        if user is None or user.role != "admin":
            return None
        return user

    def close_session(self, raw: str | None) -> None:
        if not raw:
            return
        session_row = self.session.scalar(
            select(UserSession).where(UserSession.token_hash == _hash(raw))
        )
        if session_row is not None:
            self.session.delete(session_row)
            self.session.commit()

    def _deadline(self, now: datetime | None = None) -> datetime:
        assert self.settings is not None
        return (now or datetime.now(UTC)) + timedelta(days=self.settings.member_session_days)
