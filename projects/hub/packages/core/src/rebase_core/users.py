"""Identity: one `users` row per person, and the one way in -- the magic link -- for a
member and an admin alike (REB-278, design record 2026-09-17 §4).

Owns get-or-create by lowercased email, session open/close, and the magic link's
request-and-enter, moved out of the person-matching pieces `MemberService` and the
password-login `AdminService` REB-281 retired: `MemberService` keeps what is specific
to the freelancer card, and the admin list/promote/demote pair lives here alongside
everything else that means the person.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.admin_tokens import AdminList, AdminRead
from rebase_core.audit import AdminActionService
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.linkedin import linkedin_key
from rebase_core.mail import Mail
from rebase_core.mail_member import magic_link_mail
from rebase_core.models import (
    ATTRIBUTION_COLUMNS,
    USER_ROLES,
    Freelancer,
    Login,
    MagicLinkToken,
    User,
    UserSession,
)
from rebase_core.pagination import SortSpec, decode_cursor, encode_cursor, keyset_predicate
from rebase_core.referrals import ReferralService
from rebase_core.search import matches_any, similarity_score
from rebase_core.signup_schemas import SignupUtm
from rebase_core.team_caps import rome_midnight

logger = logging.getLogger(__name__)

ENTITY = "user"
# The public doors that ask for a login link (`MagicLinkToken.asked_via`).
VIA_LOGIN = "login"
VIA_WIZARD = "wizard"
LAST_ADMIN = "Questo è l'ultimo amministratore: promuovine un altro prima di togliergli il ruolo."
# A session's deadline is pushed forward on use, but only once it has fallen this far
# behind the deadline a fresh request would set: every authenticated call used to write
# and commit, the SPA's `/me` poll included, for a deadline measured in days.
SESSION_SLIDE_AFTER = timedelta(hours=1)
# The one sentence for a LinkedIn profile that belongs to somebody else's card (REB-800):
# the member is told how to get back in, the admin how to settle it.
LINKEDIN_TAKEN = (
    "Questo profilo LinkedIn risulta già su un'altra scheda: accedi con l'indirizzo che "
    "avevi usato la prima volta, oppure chiedi a un admin di unire le due schede."
)
ADMIN_LIST_LIMIT_DEFAULT = 100
ADMIN_LIST_LIMIT_MAX = 500
_ADMIN_SEARCH_COLUMNS = (User.first_name, User.last_name, User.email)
_LINK_REQUEST_THROTTLE_SECONDS = 60


def refuse_linkedin_conflict(session: Session, exc: IntegrityError) -> None:
    """A write that lost a race on `uq_users_linkedin_lower` answers the same sentence the
    pre-check would have (REB-800), not a 500; any other integrity error is the caller's."""
    if "linkedin" in str(exc.orig):
        session.rollback()
        raise ValidationFailed(ENTITY, "linkedin_url", LINKEDIN_TAKEN) from exc
    raise exc


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
        """The row that holds this address, merged or not: what a login or a role is
        about. Who the address *is* since a merge is `person`."""
        lowered = email.strip().lower()
        return self.session.scalar(select(User).where(func.lower(User.email) == lowered))

    def survivor(self, user: User) -> User:
        """The person a row stands for: itself, or the one an admin merged it into
        (REB-800). A merge repoints the rows that pointed at the one it absorbs, so
        this is one hop, never a walk."""
        if user.merged_into_id is None:
            return user
        target = self.session.get(User, user.merged_into_id)
        return target if target is not None else user

    def person(self, email: str) -> User | None:
        """The person who answers to this address: `by_email`, followed through a merge."""
        row = self.by_email(email)
        return self.survivor(row) if row is not None else None

    def by_linkedin(self, linkedin_url: str | None) -> User | None:
        """The live person with this LinkedIn profile, compared as `linkedin_key`
        compares (REB-800), or `None` for nothing to compare."""
        key = linkedin_key(linkedin_url)
        if key is None:
            return None
        return self.session.scalar(
            select(User).where(func.lower(User.linkedin_url) == key, User.merged_into_id.is_(None))
        )

    def find_person(self, email: str, linkedin_url: str | None = None) -> User | None:
        """Who this submission is about: the person who answers to the address, else the
        one who holds the LinkedIn profile **and has a card** (REB-800). Identity is both,
        so a second address with the same profile is the same person, never a second row.
        A holder with no card (a company's contact, an admin) is not who a freelancer
        form is about: typing their profile attaches nothing to them."""
        found = self.person(email)
        if found is not None:
            return found
        holder = self.by_linkedin(linkedin_url)
        if holder is not None and self.has_card(holder):
            return holder
        return None

    @staticmethod
    def _refuse_merged(row: User | None) -> None:
        """A role goes to a person, not to an address an admin merged into somebody."""
        if row is not None and row.merged_into_id is not None:
            raise ValidationFailed(
                ENTITY, "email", "questo indirizzo è stato unito a un'altra scheda: usa quello"
            )

    def has_card(self, user: User) -> bool:
        return (
            self.session.scalar(select(Freelancer.id).where(Freelancer.user_id == user.id))
            is not None
        )

    def require_free_linkedin(self, user_id: UUID, linkedin_url: str | None) -> None:
        """Refuses a LinkedIn profile that is another person's, with the sentence for
        whoever typed it: the member editing their own card, an admin overriding it."""
        holder = self.by_linkedin(linkedin_url)
        if holder is not None and holder.id != user_id:
            raise ValidationFailed(ENTITY, "linkedin_url", LINKEDIN_TAKEN)

    def get_or_create(
        self,
        email: str,
        first_name: str = "",
        last_name: str = "",
        linkedin_url: str | None = None,
        phone: str | None = None,
    ) -> User:
        """The `users` row for this address, made on the spot when none exists yet: one
        row per lowercased email, never two for a repeat application or request racing
        itself, the same guard `Freelancer.apply` already keeps for its own table.
        `nome`/`last_name`/`linkedin_url` are left exactly as they were found on a
        repeat call, never overwritten by a later request's own answer. `phone`
        (REB-380) is different: a `users` row can predate it entirely (a freelancer
        application never asked for one, and it did not exist before migration 0016),
        so a still-`None` phone number is backfilled from whichever call first
        supplies one -- "leave as found" here means "never overwrite a real answer,"
        not "never touch a blank one." Left uncommitted on purpose: every caller of
        this method commits its own row right after (`CompanyService.request`, the
        only caller that ever passes a real `phone`), so the backfill lands in the
        same transaction as whatever the caller was doing, atomically -- committing it
        here would persist the phone number even if the caller's own insert then
        failed."""
        row = self.find_person(email, linkedin_url)
        if row is not None:
            if phone is not None and row.phone is None:
                row.phone = phone
            return row
        # A profile somebody else holds (a user with no card, see `find_person`) is not
        # written on a second row: the index would refuse it, and it is theirs.
        taken = self.by_linkedin(linkedin_url) is not None
        row = User(
            email=email.strip().lower(),
            first_name=first_name,
            last_name=last_name,
            linkedin_url=None if taken else linkedin_url,
            phone=phone,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError as exc:
            # Two first applications racing: the index on the address, or since REB-800
            # on the LinkedIn profile, decides, and the loser attaches to the winner.
            self.session.rollback()
            existing = self.find_person(email, linkedin_url)
            if existing is None:
                raise exc  # not a race we know: the caller's own integrity error
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
        the same search-and-cursor shape REB-285 gives Talents and Companies).
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
        self,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
        *,
        admin_id: UUID,
    ) -> tuple[User, bool]:
        """Whatever `users` row already answers to this address is promoted with one
        click and no form; an address with none yet needs `nome`/`last_name` to create a
        bare row first, no freelancer card invented for it (§1, Nav and Amministratori).
        The second element is whether a row was created, so the caller knows to mail a
        fresh promotion the same link everyone else gets.

        `admin_promoted` is written in the same transaction as the role (`record` with
        `commit=False`): a role is the most sensitive change an admin can make, and it
        never exists without its entry. Promoting somebody who already is an admin
        changes nothing and records nothing."""
        row = self.by_email(email)
        self._refuse_merged(row)
        created = row is None
        was = None if row is None else row.role
        if row is None:
            first_name, last_name = (first_name or "").strip(), (last_name or "").strip()
            if not first_name or not last_name:
                raise ValidationFailed(
                    ENTITY, "first_name", "servono nome e cognome per un indirizzo nuovo"
                )
            row = User(email=email.strip().lower(), first_name=first_name, last_name=last_name)
            self.session.add(row)
        row.role = "admin"
        if was != "admin":
            self.session.flush()
            AdminActionService(self.session).record(
                ENTITY,
                row.id,
                "admin_promoted",
                admin_id,
                {"created": created, "before": was},
                commit=False,
            )
        self.session.commit()
        return row, created

    def demote(self, user_id: UUID, *, admin_id: UUID) -> User:
        """Sets `role = 'member'`, fully reversible since nothing is deleted. Refused
        for an address merged into somebody (the role is theirs), and for the last
        admin: with nobody left to promote anybody, the only way back would be
        `rebase setrole` on the server. Demoting yourself is allowed while another
        admin exists, and is the same act as demoting anyone else (it signs the entry
        with your own id); it is the last-admin rule, not a self-check, that protects
        the area. The admin rows are locked for the count, so two admins demoting each
        other at once cannot both pass it. `admin_demoted` is written in the same
        transaction as the role."""
        row = self.session.get(User, user_id)
        if row is None:
            raise NotFound(ENTITY, user_id)
        self._refuse_merged(row)
        if row.role != "admin":
            return row
        admins = self.session.scalars(
            select(User.id).where(User.role == "admin").with_for_update()
        ).all()
        if len(admins) <= 1:
            raise InvalidState(LAST_ADMIN)
        row.role = "member"
        AdminActionService(self.session).record(
            ENTITY, row.id, "admin_demoted", admin_id, {"before": "admin"}, commit=False
        )
        self.session.commit()
        return row

    def set_role(
        self, email: str, role: str, first_name: str = "", last_name: str = ""
    ) -> tuple[User, bool]:
        """`rebase setrole`'s own shape: a minimal row created when none exists
        (`nome`/`last_name` required for it), the role set either way."""
        if role not in USER_ROLES:
            raise ValidationFailed(ENTITY, "role", f"uno fra {', '.join(USER_ROLES)}")
        row = self.by_email(email)
        self._refuse_merged(row)
        created = row is None
        if row is None:
            first_name, last_name = first_name.strip(), last_name.strip()
            if not first_name or not last_name:
                raise ValidationFailed(
                    ENTITY, "first_name", "servono nome e cognome per un indirizzo nuovo"
                )
            row = User(email=email.strip().lower(), first_name=first_name, last_name=last_name)
            self.session.add(row)
        row.role = role
        self.session.commit()
        return row, created

    # ---- the way in ----------------------------------------------------------------

    def wizard_links_left(self, now: datetime | None = None) -> int:
        """How many more «you already have a card» mails the wizard may send today (REB-889):
        the budget (`public_mail_daily_budget`) less the links the wizard asked for since
        midnight in Rome. That mail goes to an address its owner did not ask to be mailed,
        so it is the one a stranger can use to burn the Resend quota: zero is the circuit
        breaker for it, and only for it. A login link is never counted here, so no number of
        logins can close sign-in."""
        moment = now or datetime.now(UTC)
        sent = self.session.scalar(
            select(func.count())
            .select_from(MagicLinkToken)
            .where(
                MagicLinkToken.asked_via == VIA_WIZARD,
                MagicLinkToken.created_at >= rome_midnight(moment),
            )
        )
        return max(self._config.public_mail_daily_budget - (sent or 0), 0)

    def request_link(
        self,
        email: str,
        note: str | None = None,
        utm: SignupUtm | None = None,
        *,
        via: str | None = None,
    ) -> Mail | None:
        """The mail to send, or `None` when nobody with that address exists, or when
        the address already holds a live link younger than a minute (REB-100): no new
        token, no mail, the same silence a caller sees either way -- the throttle is
        per address, not per client, and sits beside the per-client bucket `spend_one`
        already charges in the router. Sweeps the person's spent and expired tokens
        from before today first: nothing needs a cron, and today's rows stay, since
        they are what the daily cap and `wizard_links_left` count.

        `via` says which public door asked (`VIA_LOGIN`, `VIA_WIZARD`; `None` for an admin's
        promotion or the command line). A public ask counts toward the person's own daily
        cap (`magic_link_daily_per_user`): past it no token and no mail, the same silence as
        any other refusal, and the log says so without the address. An admin is never
        refused, so no stranger and no busy day can shut an admin out.

        `utm` is what the login page's URL said (REB-426), kept on the token for the
        `Login` it will open; an empty one is no attribution at all."""
        settings = self._config
        typed = self.by_email(email)
        if typed is None:
            return None
        # An address an admin merged away still gets its mail, at that address, and the
        # link opens the person it was merged into (REB-800).
        row = self.survivor(typed)
        now = datetime.now(UTC)
        self.session.execute(
            delete(MagicLinkToken).where(
                MagicLinkToken.user_id == row.id,
                MagicLinkToken.created_at < rome_midnight(now),
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
        if (
            via is not None
            and row.role != "admin"
            and self._asked_today(row.id, now) >= (settings.magic_link_daily_per_user)
        ):
            logger.warning("a person is past the daily cap of %s login links", via)
            return None
        raw = secrets.token_urlsafe(32)
        attribution = utm.model_dump() if utm is not None and not utm.is_empty() else {}
        self.session.add(
            MagicLinkToken(
                user_id=row.id,
                token_hash=_hash(raw),
                expires_at=now + timedelta(minutes=settings.magic_link_minutes),
                asked_via=via,
                **attribution,
            )
        )
        self.session.commit()
        link = f"{settings.hub_url.rstrip('/')}/verify?t={raw}"
        return magic_link_mail(typed.email, link, settings.magic_link_minutes, note)

    def _asked_today(self, user_id: UUID, now: datetime) -> int:
        """The links this person asked for from a public door since midnight in Rome."""
        return (
            self.session.scalar(
                select(func.count())
                .select_from(MagicLinkToken)
                .where(
                    MagicLinkToken.user_id == user_id,
                    MagicLinkToken.asked_via.is_not(None),
                    MagicLinkToken.created_at >= rome_midnight(now),
                )
            )
            or 0
        )

    def enter(self, raw_token: str) -> tuple[User, str] | None:
        """The person and the raw session token for the cookie, or `None` for a wrong,
        spent or expired link. Spent by a conditional update gated on it still being
        unused, so of two requests racing on the same raw token only one opens a
        session (`MemberService.enter`'s own reasoning, unchanged)."""
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
        # A link written for an address that was merged since opens the survivor (REB-800).
        row = self.survivor(row)
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
        # It carries the campaign the link was asked from, off the token (REB-426).
        self.session.add(
            Login(
                user_id=row.id,
                logged_at=now,
                **{column: getattr(token, column) for column in ATTRIBUTION_COLUMNS},
            )
        )
        # The same proof for the referrals that name this person (REB-658): they just
        # spent a link mailed to their address, and this is the commit that records it.
        ReferralService(self.session).verify_on_login(row.id, now)
        self.session.commit()
        return row, raw_session

    def resolve(self, raw: str | None) -> User | None:
        """The person behind a cookie, or `None`. Slides the expiry forward on use, but
        only once the stored deadline is `SESSION_SLIDE_AFTER` behind the one this
        request would set: a request inside that hour writes nothing, so an
        authenticated read is no longer a write transaction. Forgets a session past its
        deadline the moment it is presented."""
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
        row = self.survivor(row)
        deadline = self._deadline(now)
        if deadline - session_row.expires_at > SESSION_SLIDE_AFTER:
            session_row.expires_at = deadline
            self.session.commit()
        return row

    def close_session(self, raw: str | None) -> None:
        if not raw:
            return
        session_row = self.session.scalar(
            select(UserSession).where(UserSession.token_hash == _hash(raw))
        )
        if session_row is not None:
            self.session.delete(session_row)
            self.session.commit()

    @property
    def _config(self) -> Settings:
        """The settings the session and link methods need; the pure identity methods
        never ask. A service built without them is a programming error."""
        if self.settings is None:
            raise RuntimeError("UserService needs settings for sessions and magic links")
        return self.settings

    def _deadline(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now(UTC)) + timedelta(days=self._config.member_session_days)
