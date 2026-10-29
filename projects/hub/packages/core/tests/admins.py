"""The admin a test hands to a service that records who acted: a real `users` row, since
the trail's foreign key refuses an id nobody has."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from rebase_core.models import User


def an_admin(session: Session, email: str = "claude@rebase.it", nome: str = "Claude") -> UUID:
    """The admin with this address, created on first call."""
    existing = session.scalar(select(User).where(User.email == email))
    if existing is not None:
        return existing.id
    admin = User(email=email, nome=nome, cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id
