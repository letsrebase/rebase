from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.auth.models import User
from pigrocrm.core.errors import ValidationFailed


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_active(self, user_id: UUID) -> User:
        """Fetches a user and confirms it is usable as an authenticated actor: it
        exists and has not been deactivated. Both the cookie flow (`get_actor`) and the
        refresh flow (`refresh()`) need this identical check on every request -- it
        used to be copied verbatim in both places, which is exactly how the two could
        have drifted."""
        user = self.get(user_id)
        if user is None or not user.attivo:
            raise ValidationFailed("user", "id", "utente non trovato o non attivo")
        return user

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.strip().lower())
        return self.session.execute(stmt).scalar_one_or_none()

    def first_active_admin(self) -> User | None:
        """The installation's titolare when nothing else names one: the admin created
        first among those still active. The root installation has no registry row and so
        no `owner_email`, and this is who stands in for it (REB-263). `id` breaks the tie
        between two admins created in the same transaction, so the answer never depends
        on the plan Postgres picks."""
        stmt = (
            select(User)
            .where(User.ruolo == "admin", User.attivo.is_(True))
            .order_by(User.created_at, User.id)
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_all(self) -> list[User]:
        return list(self.session.execute(select(User).order_by(User.nome)).scalars())

    def count(self) -> int:
        return self.session.execute(select(func.count()).select_from(User)).scalar_one()

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user
