"""The admin a test hands to a service that records who acted: a real `users` row, since
the trail's foreign key refuses an id nobody has."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.models import User

ADMIN_EMAIL = "ivan@rebase.it"


def an_admin(
    session: Session, email: str = ADMIN_EMAIL, nome: str = "Ivan", last_name: str = ""
) -> UUID:
    """The admin with this address, created (and committed) on first call. The one place a
    test makes an admin: another address, name or surname is a parameter, not another
    helper."""
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing.id
    admin = User(email=email, nome=nome, last_name=last_name, role="admin")
    session.add(admin)
    session.commit()
    return admin.id
