"""The member area: the freelancer card, and the referente's own company requests,
and what each owner may change once signed in.

The way in -- the magic link, session open/close, `resolve` -- moved to
`rebase_core.users` (REB-278): a person signs in as a `users` row, member or admin
alike. Every change the person makes is a note in the row's thread (ORB-59, REB-856), so
the admin sees what moved without an audit table. A company files several requests
over time (REB-381, `create_additional_request`), and since REB-602 the member area
lists every one an admin has not soft-deleted and lets the person edit any of them by
its id, never somebody else's: `requests_for_user` is the list, `require_request` the
one door onto a single row. Filing a genuinely new request is the same signed-in door:
it carries the company's name forward and asks everything else fresh, as a new row the
older ones are none the wiser about.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import utcnow
from rebase_core.cloud import TalentCloudService
from rebase_core.company_schemas import CompanyFields, CompanyUpdate
from rebase_core.contact_points import ContactPointInput, ContactPointService
from rebase_core.crm_words import TO_LINK
from rebase_core.errors import NotFound
from rebase_core.freelancer_schemas import CvFile, MemberUpdate
from rebase_core.freelancers import check_cv, cv_of
from rebase_core.member_schemas import MemberLookup, MemberProfile, MemberRequest, MeRead
from rebase_core.models import Company, Freelancer, User
from rebase_core.notes import NoteService
from rebase_core.users import UserService, refuse_linkedin_conflict

ENTITY = "freelancer"
# What the note calls each field, in the admin's language, in the wizard's order.
FIELD_LABELS: dict[str, str] = {
    "first_name": "nome",
    "last_name": "cognome",
    "linkedin_url": "profilo LinkedIn",
    "daily_rate": "tariffa giornaliera",
    "job_title": "posizione",
    "remote": "modalità di lavoro",
    "links": "link",
}
# The three fields a freelancer card shares with its `users` row: since migration B
# (REB-281) they live on `users` alone, so a change here writes there instead of the
# card, and `GET /me` never reads a stale name the person just corrected.
_IDENTITY_FIELDS = ("first_name", "last_name", "linkedin_url")

COMPANY_ENTITY = "company"
# What the note calls each field, in the admin's language, in the wizard's order
# (REB-314; REB-380 adds the last four): the eight a company contact may change about
# one of their requests.
COMPANY_FIELD_LABELS: dict[str, str] = {
    "project": "progetto",
    "period_from": "data di inizio",
    "duration": "durata",
    "daily_budget": "budget giornaliero",
    "remote": "modalità di lavoro",
    "onsite_days": "giorni in sede",
    "headcount": "numero di persone richieste",
    "requested_role": "figura richiesta",
}


def _to_profile(row: Freelancer, user: User) -> MemberProfile:
    """`MemberProfile`, identity read off the linked `users` row since REB-281 dropped
    the card's own `first_name`/`last_name`/`email`/`linkedin_url`."""
    return MemberProfile(
        id=row.id,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        linkedin_url=user.linkedin_url,
        cv_filename=row.cv_filename,
        cv_size=row.cv_size,
        daily_rate=row.daily_rate,
        job_title=row.job_title,
        remote=row.remote,
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
            raise NotFound("card", user_id)
        return row

    # ---- the identity behind a company request -----------------------------------------

    @staticmethod
    def _live_requests(user_id: UUID) -> Select[Company]:
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
            raise NotFound("company", user_id)
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
            raise NotFound("company", company_id)
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
            first_name=user.first_name,
            last_name=user.last_name,
            email=user.email,
            linkedin_url=user.linkedin_url,
            phone=user.phone,
            contact_points=ContactPointService.present(user),
            role=user.role,
            created_at=user.created_at,
            updated_at=user.updated_at,
            has_card=card is not None,
            cv_filename=card.cv_filename if card else None,
            cv_size=card.cv_size if card else None,
            daily_rate=card.daily_rate if card else None,
            job_title=card.job_title if card else None,
            remote=card.remote if card else None,
            links=list(card.links) if card else [],
            requests=[MemberRequest.model_validate(row) for row in self.requests_for_user(user_id)],
            talent_cloud=TalentCloudService(self.session).for_user(user_id) is not None,
        )

    # ---- what another product may ask ------------------------------------------------

    def lookup(self, email: str) -> MemberLookup:
        """Whether a freelancer with that address exists, and their two names if so
        (ORB-173). The same match as `uq_freelancers_user_id`, so the answer agrees
        with what the wizard would have refused as a duplicate. An unknown address is
        `member=False` and never an error: the caller is PigroCRM's signup, and a person
        who is not in the community is the ordinary case there, not a fault."""
        result = self._by_email(email.strip().lower())
        if result is None:
            return MemberLookup(member=False)
        _row, user = result
        return MemberLookup(member=True, first_name=user.first_name, last_name=user.last_name)

    # ---- what they see and change -----------------------------------------------------

    def profile(self, freelancer_id: UUID) -> MemberProfile:
        row = self._require(freelancer_id)
        user = self._owner(row)
        return _to_profile(row, user)

    def update(self, freelancer_id: UUID, data: MemberUpdate) -> MemberProfile:
        """Applies the seven answers and leaves one note naming the ones that moved,
        signed with the person's name after the change. Nothing moved, no note --
        unless the card was an admin's draft from a signup (ORB-155): saving it, even
        unchanged, makes it the person's (`compilata_da = "persona"`), and the thread
        says so. `status` and the attribution are never touched here. `nome`/
        `last_name`/`linkedin_url` live on the linked `users` row since REB-281, so a
        change to them lands there directly rather than through the card."""
        row = self._require(freelancer_id)
        user = self._owner(row)
        changed: list[str] = []
        for field, label in FIELD_LABELS.items():
            target = user if field in _IDENTITY_FIELDS else row
            value = getattr(data, field)
            if field == "links":
                value = list(value)
            if field == "linkedin_url" and getattr(target, field) != value:
                UserService(self.session).require_free_linkedin(user.id, value)
            if getattr(target, field) != value:
                setattr(target, field, value)
                changed.append(label)
        taken_over = row.filled_by != "person"
        if not changed and not taken_over:
            return _to_profile(row, user)
        row.filled_by = "person"
        try:
            self.session.commit()
        except IntegrityError as exc:
            refuse_linkedin_conflict(self.session, exc)
        if changed:
            self._note(user, row, f"Profilo aggiornato dalla persona: {', '.join(changed)}")
        else:
            self._note(user, row, "Scheda confermata dalla persona")
        return _to_profile(row, user)

    def update_contact_points(self, user_id: UUID, points: list[ContactPointInput]) -> MeRead:
        """The signed-in person replaces their own list of extra contact points (REB-943),
        whatever they are here: a freelancer, a company's referente or both. The address
        they sign in with is not part of it. A change leaves one note in the thread of
        the card, and of the newest request, saying the contacts moved and never what they
        are, so the admin knows without a value in the thread."""
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        if ContactPointService(self.session).replace(user, points):
            self.session.commit()
            card = self.card_for_user(user_id)
            if card is not None:
                self._note(user, card, "Recapiti aggiornati dalla persona")
            request = self.company_for_user(user_id)
            if request is not None:
                NoteService(self.session).add(
                    COMPANY_ENTITY,
                    request.id,
                    "Recapiti aggiornati dal referente",
                    author_id=user.id,
                    source="person",
                )
        return self.me_read(user_id)

    def update_company(
        self, user_id: UUID, data: CompanyUpdate, company_id: UUID | None = None
    ) -> MeRead:
        """Applies the eight project answers (REB-314; REB-380 adds the last four) to
        one of the signed-in person's own requests -- `company_id`, or their newest live
        one when it is `None` (REB-602) -- and leaves a note naming what moved, the
        same discipline `update` keeps for the freelancer card. `status`,
        `company_name`, `phone` and the referente's identity are never touched
        here."""
        row = (
            self.require_company(user_id)
            if company_id is None
            else self.require_request(user_id, company_id)
        )
        user = self.session.get(User, user_id)
        if user is None:
            raise NotFound("user", user_id)
        changed: list[str] = []
        for field, label in COMPANY_FIELD_LABELS.items():
            value = getattr(data, field)
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(label)
        if changed:
            self.session.commit()
            NoteService(self.session).add(
                COMPANY_ENTITY,
                row.id,
                f"Richiesta aggiornata dal referente: {', '.join(changed)}",
                author_id=user.id,
                source="person",
            )
        return self.me_read(user_id)

    def create_additional_request(
        self, user_id: UUID, payload: CompanyFields
    ) -> tuple[MeRead, UUID]:
        """A signed-in requester files a genuinely new request instead of correcting
        one of theirs (REB-381): a fresh `Company` row, not an edit, so every request
        already on file stands exactly as it was -- admin-editable, like any other. The
        company's own name is read off the newest live request and carried forward,
        never asked again; everything else is the payload's own fresh answers. No UTM
        carried forward either: unlike the public wizard's own `CompanyService.request`,
        a request filed from inside the member area is not attributable to whatever ad
        brought the referente in the first time, so it goes in blank, the same as
        anything an admin creates. A person with no live request has nothing to add to,
        so this is a 404 named "azienda" for them too, the same as `update_company`.

        The row is written `to_link` (REB-868), in the same transaction, and its id is
        answered beside the member's view so the route can start the CRM filing."""
        existing = self.require_company(user_id)
        row = Company(
            user_id=user_id,
            company_name=existing.company_name,
            requested_role=payload.requested_role,
            project=payload.project,
            period_from=payload.period_from,
            duration=payload.duration,
            daily_budget=payload.daily_budget,
            remote=payload.remote,
            onsite_days=payload.onsite_days,
            headcount=payload.headcount,
            crm_status=TO_LINK,
        )
        self.session.add(row)
        self.session.commit()
        return self.me_read(user_id), row.id

    def replace_cv(
        self, freelancer_id: UUID, content: bytes, filename: str, mime: str
    ) -> MemberProfile:
        """The same check as the wizard's (`check_cv`), then the bytes replace the old
        ones and the thread says so."""
        filename, mime = check_cv(content, filename, mime)
        row = self._require(freelancer_id)
        # Locked and re-read: a merge that took this card's lock first has hidden it by
        # now (REB-800), and a CV written on a hidden card would be lost to the person.
        self.session.refresh(row, with_for_update=True)
        if row.deleted_at is not None:
            raise NotFound(ENTITY, freelancer_id)
        user = self._owner(row)
        first = row.cv_bytes is None
        row.cv_bytes, row.cv_filename, row.cv_mime, row.cv_size = (
            content,
            filename,
            mime,
            len(content),
        )
        row.cv_uploaded_at = utcnow()
        row.filled_by = "person"
        self.session.commit()
        self._note(
            user, row, "CV caricato dalla persona" if first else "CV aggiornato dalla persona"
        )
        return _to_profile(row, user)

    def cv(self, freelancer_id: UUID) -> CvFile:
        return cv_of(self._require(freelancer_id))

    # ---- helpers ---------------------------------------------------------------------

    def _owner(self, row: Freelancer) -> User:
        user = self.session.get(User, row.user_id)
        if user is None:
            raise NotFound("user", row.user_id)
        return user

    def _note(self, user: User, row: Freelancer, text: str) -> None:
        NoteService(self.session).add(ENTITY, row.id, text, author_id=user.id, source="person")

    def _require(self, freelancer_id: UUID) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None:
            raise NotFound(ENTITY, freelancer_id)
        return row

    def _by_email(self, email: str) -> tuple[Freelancer, User] | None:
        """The live card of the person who answers to this address, an address merged
        away (REB-800) answering for the person it was merged into, and an extra one
        (REB-943) for the person who keeps it."""
        person = UserService(self.session).person_at(email)
        if person is None:
            return None
        return self.session.execute(
            select(Freelancer, User)
            .join(User, User.id == Freelancer.user_id)
            .where(User.id == person.id, Freelancer.deleted_at.is_(None))
        ).first()
