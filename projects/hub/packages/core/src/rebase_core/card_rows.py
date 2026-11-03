"""What the admin lists share about a card: which addresses have one, and the columns
every row of a card carries (REB-805).

`FreelancerService` (the «Developer e CTO» list), `TalentsService` (Talenti, the MCP's
`list_talents`) and the campaign states each need to ask «does this address have a card»
and to build a row from a `Freelancer` and the `User` it hangs on. They read it from here
rather than each writing the anti-join and the column mapping again.
"""

from datetime import datetime
from decimal import Decimal
from typing import TypedDict
from uuid import UUID

from sqlalchemy import Subquery, func, select

from rebase_core.models import Freelancer, User


def card_emails() -> Subquery:
    """Lowercased addresses that have a card, deleted or not (ORB-163): a card's identity
    lives on the linked `users` row since REB-281, so this is the one place that joins the
    two. A bare sign-up is one whose address is not among these, and a card deleted by an
    admin still says «this person is not a lead to chase». Outer-join it on
    `func.lower(Signup.email)` and keep the rows where `.c.email` is null."""
    return (
        select(func.lower(User.email).label("email"))
        .join(Freelancer, Freelancer.user_id == User.id)
        .subquery()
    )


class CardRowFields(TypedDict):
    id: UUID
    nome: str
    last_name: str
    email: str
    linkedin_url: str | None
    status: str
    utm_source: str | None
    created_at: datetime
    daily_rate: Decimal | None
    remote: str | None
    vetted_at: datetime | None
    posizione: str | None


def card_row_fields(row: Freelancer, user: User) -> CardRowFields:
    """The columns a `FreelancerRead` and a `TalentRead` both carry for a card: the
    person off the `users` row, the rest off the card. Each caller adds what only its
    own row has."""
    return {
        "id": row.id,
        "nome": user.nome,
        "last_name": user.last_name,
        "email": user.email,
        "linkedin_url": user.linkedin_url,
        "status": row.status,
        "utm_source": row.utm_source,
        "created_at": row.created_at,
        "daily_rate": row.daily_rate,
        "remote": row.remote,
        "vetted_at": row.vetted_at,
        "posizione": row.posizione,
    }
