"""The member area: the freelancer card, and the referente's own company requests,
and what each owner may change once signed in.

The way in -- the magic link, session open/close, `resolve` -- moved to
`rebase_core.users` (REB-278): a person signs in as a `users` row, member or admin
alike. Every change the person makes is a comment in the row's thread (ORB-59), so
the admin sees what moved without an audit table. A company files several requests
over time (REB-381, `create_additional_request`), and since REB-602 the member area
lists every one an admin has not soft-deleted and lets the person edit any of them by
its id, never somebody else's: `requests_for_user` is the list, `require_request` the
one door onto a single row. Filing a genuinely new request is the same signed-in door:
it carries the company's name forward and asks everything else fresh, as a new row the
older ones are none the wiser about.
"""

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from rebase_core.cloud import TalentCloudService
from rebase_core.comments import CommentService
from rebase_core.errors import NotFound
from rebase_core.freelancers import check_cv, cv_of
from rebase_core.models import AUTORE_MAX_LENGTH, Company, Freelancer, User
from rebase_core.schemas import (
    CompanyFields,
    CompanyUpdate,
    CvFile,
    MemberLookup,
    MemberProfile,
    MemberRequest,
    MemberUpdate,
    MeRead,
)

ENTITY = "freelancer"
# What the comment calls each field, in the admin's language, in the wizard's order.
FIELD_LABELS: dict[str, str] = {
    "nome": "nome",
    "cognome": "cognome",
    "linkedin_url": "profilo LinkedIn",
    "tariffa_giornaliera": "tariffa giornaliera",
    "posizione": "posizione",
    "remoto": "modalità di lavoro",
    "links": "link",
}
# The three fields a freelancer card shares with its `users` row: since migration B
# (REB-281) they live on `users` alone, so a change here writes there instead of the
# card, and `GET /me` never reads a stale name the person just corrected.
_IDENTITY_FIELDS = ("nome", "cognome", "linkedin_url")

COMPANY_ENTITY = "company"
# What the comment calls each field, in the admin's language, in the wizard's order
# (REB-314; REB-380 adds the last four): the eight a company contact may change about
# one of their requests.
COMPANY_FIELD_LABELS: dict[str, str] = {
    "progetto": "progetto",
    "periodo_da": "data di inizio",
    "durata": "durata",
    "budget_giornaliero": "budget giornaliero",
    "remoto": "modalità di lavoro",
    "giorni_presenza": "giorni in sede",
    "numero_risorse": "numero di persone richieste",
    "figura_richiesta": "figura richiesta",
}


def _to_profile(row: Freelancer, user: User) -> MemberProfile:
    """`MemberProfile`, identity read off the linked `users` row since REB-281 dropped
    the card's own `nome`/`cognome`/`email`/`linkedin_url`."""
    return MemberProfile(
        id=row.id,
        nome=user.nome,
        cognome=user.cognome,
        email=user.email,
        linkedin_url=user.linkedin_url,
        cv_filename=row.cv_filename,
        cv_size=row.cv_size,
        tariffa_giornaliera=row.tariffa_giornaliera,
        posizione=row.posizione,
        remoto=row.remoto,
        links=list(row.links),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class MemberService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # ---- the identity behind a card ---------------------------------------------------

    def card_for_user(self, user_id: UUID) -> Freelancer | None:
        """`None` for a card an admin has soft-deleted (REB-347), the same as for one
        that never existed: a person's own area must not go on serving a record the
        admin took down."""
        return self.session.scalar(
            select(Freelancer).where(Freelancer.user_id == user_id, Freelancer.deleted_at.is_(None))
        )

    def require_card(self, user_id: UUID) -> Freelancer:
        """The freelancer card for a signed-in person, or a 404 named "scheda": an
        admin with none yet is a new case this record's card-less admin introduces
        (design record §4)."""
        row = self.card_for_user(user_id)
        if row is None:
            raise NotFound("scheda", user_id)
        return row

    # ---- the identity behind a company request -----------------------------------------

    @staticmethod
    def _live_requests(user_id: UUID) -> Select[tuple[Company]]:
        return (
            select(Company)
            .where(Company.user_id == user_id, Company.deleted_at.is_(None))
            # `id` (UUIDv7, time-ordered) breaks a tie on `created_at`, the same tiebreak
            # `_list_stmt`'s own ordering uses (`companies.py`): two requests a
            # `func.now()` transaction start could otherwise date identically.
            .order_by(Company.created_at.desc(), Company.id.desc())
        )

    def requests_for_user(self, user_id: UUID) -> list[Company]:
        """Every request the signed-in person filed that an admin has not soft-deleted
        (REB-347), newest first: the list the member area shows (REB-602). A deleted
        request is gone from it, and its older siblings stay, each one editable."""
        return list(self.session.scalars(self._live_requests(user_id)))

    def company_for_user(self, user_id: UUID) -> Company | None:
        """The signed-in person's newest live request, or `None` when they have none:
        what the request-less `PATCH /me/company` edits and what a new request carries
        the company's name forward from."""
        return self.session.scalar(self._live_requests(user_id).limit(1))

    def require_company(self, user_id: UUID) -> Company:
        """The signed-in person's newest live request, or a 404 named "azienda": a
        person with no request yet is the ordinary case for anyone who is not a
        company contact."""
        row = self.company_for_user(user_id)
        if row is None:
            raise NotFound("azienda", user_id)
        return row

    def require_request(self, user_id: UUID, company_id: UUID) -> Company:
        """One of the signed-in person's own live requests by id, or a 404 named
        "azienda". Somebody else's request, a soft-deleted one and an id that does not
        exist are the same 404: a 403 would say the row exists."""
        row = self.session.scalar(
            select(Company).where(
                Company.id == company_id,
                Company.user_id == user_id,
                Company.deleted_at.is_(None),
            )
        )
        if row is None:
            raise NotFound("azienda", company_id)
        return row

    def me_read(self, user_id: UUID) -> MeRead:
        """The full `GET /me` shape for whoever `user_id` names: the identity off
        `users`, plus the freelancer card's own fields when one exists and every live
        company request, newest first (REB-602), blank or empty otherwise, and whether
        a talent cloud grant of theirs is live (REB-518)."""
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        card = self.card_for_user(user_id)
        return MeRead(
            id=user.id,
            nome=user.nome,
            cognome=user.cognome,
            email=user.email,
            linkedin_url=user.linkedin_url,
            telefono=user.telefono,
            role=user.role,
            created_at=user.created_at,
            updated_at=user.updated_at,
            ha_scheda=card is not None,
            cv_filename=card.cv_filename if card else None,
            cv_size=card.cv_size if card else None,
            tariffa_giornaliera=card.tariffa_giornaliera if card else None,
            posizione=card.posizione if card else None,
            remoto=card.remoto if card else None,
            links=list(card.links) if card else [],
            richieste=[
                MemberRequest.model_validate(row) for row in self.requests_for_user(user_id)
            ],
            talent_cloud=TalentCloudService(self.session).for_user(user_id) is not None,
        )

    # ---- what another product may ask ------------------------------------------------

    def lookup(self, email: str) -> MemberLookup:
        """Whether a freelancer with that address exists, and their two names if so
        (ORB-173). The same match as `uq_freelancers_user_id`, so the answer agrees
        with what the wizard would have refused as a duplicate. An unknown address is
        `membro=False` and never an error: the caller is PigroCRM's signup, and a person
        who is not in the community is the ordinary case there, not a fault."""
        result = self._by_email(email.strip().lower())
        if result is None:
            return MemberLookup(membro=False)
        _row, user = result
        return MemberLookup(membro=True, nome=user.nome, cognome=user.cognome)

    # ---- what they see and change -----------------------------------------------------

    def profile(self, freelancer_id: UUID) -> MemberProfile:
        row = self._require(freelancer_id)
        user = self._owner(row)
        return _to_profile(row, user)

    def update(self, freelancer_id: UUID, data: MemberUpdate) -> MemberProfile:
        """Applies the seven answers and leaves one comment naming the ones that moved,
        signed with the person's name after the change. Nothing moved, no comment --
        unless the card was an admin's draft from a signup (ORB-155): saving it, even
        unchanged, makes it the person's (`compilata_da = "persona"`), and the thread
        says so. `stato`, `note` and the attribution are never touched here. `nome`/
        `cognome`/`linkedin_url` live on the linked `users` row since REB-281, so a
        change to them lands there directly rather than through the card."""
        row = self._require(freelancer_id)
        user = self._owner(row)
        changed: list[str] = []
        for field, label in FIELD_LABELS.items():
            target = user if field in _IDENTITY_FIELDS else row
            value = getattr(data, field)
            if field == "links":
                value = list(value)
            if getattr(target, field) != value:
                setattr(target, field, value)
                changed.append(label)
        taken_over = row.compilata_da != "persona"
        if not changed and not taken_over:
            return _to_profile(row, user)
        row.compilata_da = "persona"
        self.session.commit()
        if changed:
            self._comment(user, row, f"Profilo aggiornato dalla persona: {', '.join(changed)}")
        else:
            self._comment(user, row, "Scheda confermata dalla persona")
        return _to_profile(row, user)

    def update_company(
        self, user_id: UUID, data: CompanyUpdate, company_id: UUID | None = None
    ) -> MeRead:
        """Applies the eight project answers (REB-314; REB-380 adds the last four) to
        one of the signed-in person's own requests -- `company_id`, or their newest live
        one when it is `None` (REB-602) -- and leaves a comment naming what moved, the
        same discipline `update` keeps for the freelancer card. `stato`, `note`,
        `nome_azienda`, `telefono` and the referente's identity are never touched
        here."""
        row = (
            self.require_company(user_id)
            if company_id is None
            else self.require_request(user_id, company_id)
        )
        user = self.session.get(User, user_id)
        assert user is not None
        changed: list[str] = []
        for field, label in COMPANY_FIELD_LABELS.items():
            value = getattr(data, field)
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(label)
        if changed:
            self.session.commit()
            author = f"{user.nome} {user.cognome}"[:AUTORE_MAX_LENGTH]
            CommentService(self.session).add(
                COMPANY_ENTITY,
                row.id,
                f"Richiesta aggiornata dal referente: {', '.join(changed)}",
                author,
            )
        return self.me_read(user_id)

    def create_additional_request(self, user_id: UUID, payload: CompanyFields) -> MeRead:
        """A signed-in referente files a genuinely new request instead of correcting
        one of theirs (REB-381): a fresh `Company` row, not an edit, so every request
        already on file stands exactly as it was -- admin-editable, like any other. The
        company's own name is read off the newest live request and carried forward,
        never asked again; everything else is the payload's own fresh answers. No UTM
        carried forward either: unlike the public wizard's own `CompanyService.request`,
        a request filed from inside the member area is not attributable to whatever ad
        brought the referente in the first time, so it goes in blank, the same as
        anything an admin creates. A person with no live request has nothing to add to,
        so this is a 404 named "azienda" for them too, the same as `update_company`."""
        existing = self.require_company(user_id)
        row = Company(
            user_id=user_id,
            nome_azienda=existing.nome_azienda,
            figura_richiesta=payload.figura_richiesta,
            progetto=payload.progetto,
            periodo_da=payload.periodo_da,
            durata=payload.durata,
            budget_giornaliero=payload.budget_giornaliero,
            remoto=payload.remoto,
            giorni_presenza=payload.giorni_presenza,
            numero_risorse=payload.numero_risorse,
        )
        self.session.add(row)
        self.session.commit()
        return self.me_read(user_id)

    def replace_cv(
        self, freelancer_id: UUID, content: bytes, filename: str, mime: str
    ) -> MemberProfile:
        """The same check as the wizard's (`check_cv`), then the bytes replace the old
        ones and the thread says so."""
        filename, mime = check_cv(content, filename, mime)
        row = self._require(freelancer_id)
        user = self._owner(row)
        first = row.cv_bytes is None
        row.cv_bytes, row.cv_filename, row.cv_mime, row.cv_size = (
            content,
            filename,
            mime,
            len(content),
        )
        row.compilata_da = "persona"
        self.session.commit()
        self._comment(
            user, row, "CV caricato dalla persona" if first else "CV aggiornato dalla persona"
        )
        return _to_profile(row, user)

    def cv(self, freelancer_id: UUID) -> CvFile:
        return cv_of(self._require(freelancer_id))

    # ---- helpers ---------------------------------------------------------------------

    def _owner(self, row: Freelancer) -> User:
        user = self.session.get(User, row.user_id)
        assert user is not None
        return user

    def _comment(self, user: User, row: Freelancer, text: str) -> None:
        author = f"{user.nome} {user.cognome}"[:AUTORE_MAX_LENGTH]
        CommentService(self.session).add(ENTITY, row.id, text, author)

    def _require(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None:
            raise NotFound(ENTITY, freelancer_id)
        return row

    def _by_email(self, email: str) -> tuple[Freelancer, User] | None:
        return self.session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .where(func.lower(User.email) == email, Freelancer.deleted_at.is_(None))
        ).first()  # type: ignore[return-value]
