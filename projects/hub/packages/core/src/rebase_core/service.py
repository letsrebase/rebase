from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.models import Freelancer, Signup, User
from rebase_core.schemas import SignupCreate, SignupList, SignupListItem, SignupRead

LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 1000


class SignupService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def subscribe(self, data: SignupCreate) -> SignupRead:
        """Idempotent: the same address twice is one row and two successes.

        A signup form is retried by people, not by clients that read status codes -- a
        double click, a page reloaded on a slow connection. Refusing the second attempt
        would show an error to someone who did exactly what the page asked.
        """
        email = data.email.strip().lower()
        existing = self._find(email)
        if existing is not None:
            return _read(self._fill_in_what_is_missing(existing, data), nuova=False)

        # The first attribution of an address is the one that stays: a person who comes
        # back through another ad and types the same email is the same person.
        # The signups table predates `origine` and the community page has no door to name,
        # so the page slug stays out of this row (ORB-167).
        utm = (
            data.utm.model_dump(exclude={"origine"})
            if data.utm is not None and not data.utm.is_empty()
            else {}
        )
        row = Signup(
            email=email,
            nome=data.nome,
            cognome=data.cognome,
            linkedin_url=data.linkedin_url,
            rif=data.rif,
            **utm,
        )
        self.session.add(row)
        try:
            self.session.commit()
        except IntegrityError:
            # Two requests for the same new address can both pass `_find` before either
            # commits; the functional unique index decides, and the loser reads the
            # winner's row instead of reporting a conflict nobody caused.
            self.session.rollback()
            winner = self._find(email)
            assert winner is not None
            return _read(winner, nuova=False)
        return _read(row, nuova=True)

    def list_recent(self, limit: int = LIST_LIMIT_DEFAULT) -> SignupList:
        """Who is on the list, newest first.

        These are other people's addresses, and the only thing anyone does with them is
        decide when to write -- so no public adapter exposes this. The API offers it only
        behind the admin session (hub spec, step 3) and the MCP server is an admin's own
        tool by construction: neither hands an actor down here, because there is exactly
        one kind of caller allowed to reach this method at all."""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        # The freelancer card with the same address, when there is one (ORB-155): one
        # outer join through `users` (the card's identity since REB-281), not a query
        # per row.
        rows = self.session.execute(
            select(Signup, Freelancer.id)
            .outerjoin(User, func.lower(User.email) == func.lower(Signup.email))
            .outerjoin(Freelancer, Freelancer.user_id == User.id)
            .order_by(Signup.created_at.desc(), Signup.id.desc())
            .limit(limit)
        ).all()
        totale = self.session.scalar(select(func.count()).select_from(Signup)) or 0
        return SignupList(
            totale=totale,
            iscrizioni=[
                SignupListItem.model_validate(
                    {**SignupListItem.model_validate(row).model_dump(), "freelancer_id": card_id}
                )
                for row, card_id in rows
            ],
        )

    def _fill_in_what_is_missing(self, row: Signup, data: SignupCreate) -> Signup:
        """Writes only into columns that are empty, never over one that already says
        something -- the same rule the attribution follows, applied to what the earlier
        form never asked. The rows collected before this form had a name field have
        `nome` NULL, and somebody signing up again is how they get one; a profile left
        out the first time and given the second is the same case.

        The values filled in this way are **unverified**: whoever types an address the
        second time is not necessarily the person who owns it. That is tolerable only
        because the write is blind -- `subscribe` answers `SignupAck`, which says
        nothing about the row -- so nobody can use it to read, or to probe, what was
        already there.
        """
        changed = False
        for field in ("nome", "cognome", "linkedin_url"):
            value = getattr(data, field)
            if value is not None and not (getattr(row, field) or "").strip():
                setattr(row, field, value)
                changed = True
        if changed:
            self.session.commit()
        return row

    def _find(self, email: str) -> Signup | None:
        return self.session.scalar(select(Signup).where(func.lower(Signup.email) == email))


def _read(row: Signup, *, nuova: bool) -> SignupRead:
    return SignupRead.model_validate(row, from_attributes=True).model_copy(update={"nuova": nuova})
