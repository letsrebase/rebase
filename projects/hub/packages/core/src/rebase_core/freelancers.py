"""Freelancers: the wizard's applications, what an admin does with them, and since
ORB-155 the card an admin writes from a signup for the person to complete."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Subquery, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.audit import (
    TIMELINE_LIMIT_DEFAULT,
    AdminActionRead,
    AdminActionService,
    coerce_stored_value,
    field_changes,
    reject_cleared_columns,
    supplied_changes,
    utcnow,
)
from rebase_core.comments import CommentService
from rebase_core.config import Settings
from rebase_core.cv_text import CvText, extract_text
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.http import HttpCall
from rebase_core.logins import LoginService
from rebase_core.models import (
    CV_MAX_BYTES,
    FREELANCER_STATES,
    UTM_COLUMNS,
    Freelancer,
    Login,
    Signup,
    User,
)
from rebase_core.perks import PerkService
from rebase_core.pigro import PigroRegistry, PigroUnavailable
from rebase_core.schemas import (
    CvFile,
    FreelancerCreate,
    FreelancerDetail,
    FreelancerDraft,
    FreelancerList,
    FreelancerOverride,
    FreelancerRead,
    SignupListItem,
    SignupUtm,
    StatusChange,
)
from rebase_core.users import UserService

ENTITY = "freelancer"
# The pseudo-state the list filter uses for signups without a card (ORB-163): never
# stored on a row, since a lead has no row of its own.
LEAD_STATE = "lead"
LIST_LIMIT_DEFAULT = 100
LIST_LIMIT_MAX = 500
PDF_MAGIC = b"%PDF-"
# The three fields a freelancer card shares with its `users` row (REB-281): an override
# of one of these lands on the identity, never on the card, the same split
# `rebase_core.members`' own `_IDENTITY_FIELDS` keeps for a self-edit.
_ADMIN_IDENTITY_FIELDS = ("nome", "cognome", "linkedin_url")
# The sentence `apply` asks for in the magic-link mail sent instead of overwriting an
# address already on file (REB-272): one more line in `magic_link_mail`'s voice, not a
# mail of its own.
ALREADY_HAS_CARD_NOTE = (
    "Risulta già una scheda su rebase con questo indirizzo: la trovi e la modifichi dalla tua area."
)


def check_cv(content: bytes, filename: str, mime: str) -> tuple[str, str]:
    """A PDF, at most `CV_MAX_BYTES`, or a refusal naming the field.

    Decided on the bytes, never on the declared type alone: a browser says whatever the
    extension suggests, and the five bytes every PDF starts with are what the file is.
    Returns the filename and mime as they will be stored -- the mime normalised to the
    one type the bytes proved.
    """
    if not content:
        raise ValidationFailed(ENTITY, "cv", "serve il CV, in PDF")
    if len(content) > CV_MAX_BYTES:
        raise ValidationFailed(ENTITY, "cv", "il CV può pesare al massimo 5 MB")
    if not content.startswith(PDF_MAGIC):
        raise ValidationFailed(ENTITY, "cv", "il CV deve essere un PDF")
    name = (filename or "cv.pdf").strip().replace("\\", "/").rsplit("/", 1)[-1][:255] or "cv.pdf"
    return name, "application/pdf" if mime in (
        "",
        "application/pdf",
        "application/octet-stream",
    ) else "application/pdf"


def cv_of(row: Freelancer) -> CvFile:
    """The stored CV as a download, or `NotFound("cv", ...)` on a card born from a
    signup that the person has not completed yet (ORB-155). Shared with the member
    area, so the two downloads answer the same thing to the same row."""
    if row.cv_bytes is None or row.cv_filename is None or row.cv_mime is None:
        raise NotFound("cv", row.id)
    return CvFile(filename=row.cv_filename, mime=row.cv_mime, content=row.cv_bytes)


def freelancer_read(row: Freelancer, user: User) -> FreelancerRead:
    """`FreelancerRead`, identity read off the linked `users` row: `nome`/`cognome`/
    `email`/`linkedin_url` left the card itself in migration B (REB-281), once
    `FreelancerService` no longer had two copies of the person to keep in step."""
    return FreelancerRead(
        id=row.id,
        nome=user.nome,
        cognome=user.cognome,
        email=user.email,
        linkedin_url=user.linkedin_url,
        cv_filename=row.cv_filename,
        cv_mime=row.cv_mime,
        cv_size=row.cv_size,
        tariffa_giornaliera=row.tariffa_giornaliera,
        posizione=row.posizione,
        remoto=row.remoto,
        links=list(row.links),
        stato=row.stato,
        note=row.note,
        compilata_da=row.compilata_da,
        origine=row.origine,
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        utm_content=row.utm_content,
        utm_term=row.utm_term,
        utm_id=row.utm_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        deleted_at=row.deleted_at,
    )


def _logins_per_card() -> Subquery:
    """How many times each card's owner entered and when last (ORB-158), as one grouped
    subquery the list joins once: two hundred people are not two hundred counts. Grouped
    by `user_id` since REB-278: `freelancer_id` is no longer written by a fresh login."""
    return (
        select(
            Login.user_id,
            func.count().label("accessi"),
            func.max(Login.logged_at).label("ultimo_accesso"),
        )
        .group_by(Login.user_id)
        .subquery()
    )


def _signed_up() -> Subquery:
    """Which addresses are also on the landing's list (ORB-161), lowercased once so the
    join lands on `uq_orbiters_signups_email_lower` rather than on a function per row."""
    return select(func.lower(Signup.email).label("email")).subquery()


def _card_emails() -> Subquery:
    """Lowercased addresses that already have a card (ORB-163): a card's identity lives
    on the linked `users` row since REB-281, so this is the one place that joins the
    two to answer "does this address have a card" without repeating the join per
    caller."""
    return (
        select(func.lower(User.email).label("email"))
        .join(Freelancer, Freelancer.user_id == User.id)
        .subquery()
    )


def _read_with_logins(
    row: Freelancer, user: User, accessi: int | None, ultimo: datetime | None, signed_up: object
) -> FreelancerRead:
    read = freelancer_read(row, user)
    read.accessi = accessi or 0
    read.ultimo_accesso = ultimo
    read.provenienza = "form" if signed_up is not None else "landing"
    return read


def _signup_utm(signup: Signup | None) -> SignupUtm | None:
    """The sign-up's own attribution (REB-284), never the card's: an admin-drafted
    card copies the signup's UTM onto the row at creation (`draft_from_signup`), but
    a wizard card carries its own, so the two can differ and the detail should show
    where this address actually first appeared."""
    if signup is None:
        return None
    return SignupUtm(**{column: getattr(signup, column) for column in UTM_COLUMNS})


class FreelancerService:
    def __init__(
        self, session: Session, settings: Settings | None = None, http: HttpCall | None = None
    ) -> None:
        """`settings`/`http` are for exactly one thing: the PigroCRM lookup in `get`'s
        enriched detail (REB-284). Every other caller -- the list, the wizard, the MCP
        tools -- hands only `session` and gets a `pigro_slug` of `None`, never an
        outbound request it did not ask for."""
        self.session = session
        self.settings = settings
        self.http = http

    def apply(
        self,
        data: FreelancerCreate,
        cv: bytes | None = None,
        cv_filename: str = "",
        cv_mime: str = "",
    ) -> tuple[FreelancerRead, bool]:
        """The card, and whether this call wrote it: `True` for a genuinely new
        address, `False` when one already had a card and nothing on it changed
        (REB-272). This route is public and unauthenticated, so a repeat post proves
        nothing about who is sending it -- the existing row keeps every profile
        field, `compilata_da`, and the CV exactly as stored, and the caller uses the
        `False` case to send that person the magic-link mail instead of pretending a
        new card was written. An admin's draft (`compilata_da == "admin"`, ORB-155) is
        taken over the same way: never by an anonymous repost, only from the
        authenticated member area. The boolean also tells the winner of a race
        between two first applications for the same address apart from its loser,
        which discovers on retry that the row already exists rather than writing
        again over it.

        **The CV is checked before the address is looked up**, so a broken upload
        answers the same 422 whether or not the address already has a card: from the
        outside, telling the two cases apart is exactly what this route must not do.
        A card without one is otherwise a state the model already had -- `cv_of`
        answers `NotFound` for it, `completa` is false, and the member area's
        `replace_cv` exists precisely to add it later.

        `cv is None` is "no file was attached"; `cv == b""` is an attached file with no
        bytes in it, and that is a refusal like any other broken upload. The difference
        is the caller's to make, and this signature is what lets them make it.
        """
        stored: tuple[bytes, str, str] | None = None
        if cv is not None:
            stored = (cv, *check_cv(cv, cv_filename, cv_mime))
        email = data.email.strip().lower()
        row = self._find(email)
        if row is not None:
            owner = self.session.get(User, row.user_id)
            assert owner is not None
            return freelancer_read(row, owner), False
        utm = data.utm.model_dump() if data.utm is not None and not data.utm.is_empty() else {}
        user = UserService(self.session).get_or_create(
            email, data.nome, data.cognome, data.linkedin_url
        )
        row = Freelancer(user_id=user.id, **utm)
        self.session.add(row)
        if stored is not None:
            content, filename, mime = stored
            row.cv_bytes, row.cv_filename, row.cv_mime, row.cv_size = (
                content,
                filename,
                mime,
                len(content),
            )
        row.tariffa_giornaliera = data.tariffa_giornaliera
        row.posizione = data.posizione
        row.remoto = data.remoto
        row.links = list(data.links)
        row.compilata_da = "persona"
        try:
            self.session.commit()
        except IntegrityError:
            # Two first applications racing on one address: the index decides, and the
            # loser discovers on retry that the winner's row now answers to `_find`.
            self.session.rollback()
            return self.apply(data, cv, cv_filename, cv_mime)
        return freelancer_read(row, user), True

    def draft_from_signup(
        self, signup_id: UUID, data: FreelancerDraft, autore: str
    ) -> FreelancerRead:
        """A card written by an admin from what the public web says about a signup
        (ORB-155): the address and the attribution come from the signup, the answers
        from the research, the CV from nobody -- the person adds it from the member
        area. One row per address still: a second research on a card the admin wrote
        replaces the researched fields and leaves `stato`, `note` and the CV alone; a
        research on a card the person filled (`compilata_da == "persona"`) is refused,
        because their own words win. One comment in the thread names the sources, so
        whoever reads the card can check where it came from."""
        signup = self.session.get(Signup, signup_id)
        if signup is None:
            raise NotFound("signup", signup_id)
        email = signup.email.strip().lower()
        row = self._find(email)
        if row is not None and row.compilata_da == "persona":
            raise ValidationFailed(ENTITY, "email", "la persona ha già compilato la sua scheda")
        created = row is None
        if row is None:
            utm = {column: getattr(signup, column) for column in UTM_COLUMNS}
            user = UserService(self.session).get_or_create(
                email, data.nome, data.cognome, data.linkedin_url
            )
            row = Freelancer(user_id=user.id, **utm)
            self.session.add(row)
        else:
            # A second research on the same card: the person's name may genuinely have
            # been corrected, so the linked `users` row follows it too, in this commit.
            existing_user = self.session.get(User, row.user_id)
            if existing_user is not None:
                existing_user.nome, existing_user.cognome, existing_user.linkedin_url = (
                    data.nome,
                    data.cognome,
                    data.linkedin_url,
                )
        row.posizione = data.posizione
        row.tariffa_giornaliera = data.tariffa_giornaliera
        row.remoto = data.remoto
        row.links = list(data.links)
        row.compilata_da = "admin"
        try:
            self.session.commit()
        except IntegrityError:
            # Two first drafts racing on one address: the index decides, and the loser
            # drafts again on top of the winner's row.
            self.session.rollback()
            return self.draft_from_signup(signup_id, data, autore)
        sources = ", ".join(data.fonti)
        text = (
            f"Scheda creata dall'iscrizione del {signup.created_at:%d/%m/%Y}. Fonti: {sources}"
            if created
            else f"Scheda aggiornata dalla ricerca. Fonti: {sources}"
        )
        CommentService(self.session).add(ENTITY, row.id, text, autore)
        return self.get(row.id)

    def list_recent(
        self, limit: int = LIST_LIMIT_DEFAULT, stato: str | None = None
    ) -> FreelancerList:
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        if stato == LEAD_STATE:
            lead, totale_lead = self._leads(limit)
            return FreelancerList(totale=0, items=[], totale_lead=totale_lead, lead=lead)
        lead, totale_lead = self._leads(limit) if stato is None else ([], 0)
        logins, signed = _logins_per_card(), _signed_up()
        stmt = (
            select(Freelancer, User, logins.c.accessi, logins.c.ultimo_accesso, signed.c.email)
            .join(User, User.id == Freelancer.user_id)
            .outerjoin(logins, logins.c.user_id == Freelancer.user_id)
            .outerjoin(signed, signed.c.email == func.lower(User.email))
            .where(Freelancer.deleted_at.is_(None))
        )
        count = select(func.count()).select_from(Freelancer).where(Freelancer.deleted_at.is_(None))
        if stato is not None:
            stmt = stmt.where(Freelancer.stato == stato)
            count = count.where(Freelancer.stato == stato)
        rows = self.session.execute(
            stmt.order_by(Freelancer.created_at.desc(), Freelancer.id.desc()).limit(limit)
        ).all()
        totale = self.session.scalar(count) or 0
        return FreelancerList(
            totale=totale,
            items=[
                _read_with_logins(row, user, accessi, ultimo, signed_up)
                for row, user, accessi, ultimo, signed_up in rows
            ],
            totale_lead=totale_lead,
            lead=lead,
        )

    def _leads(self, limit: int) -> tuple[list[SignupListItem], int]:
        """Signups whose address has no card (ORB-163), newest first, and how many
        there are: one anti-join on the two case-insensitive indexes."""
        card_emails = _card_emails()
        no_card = card_emails.c.email.is_(None)
        base = select(Signup).outerjoin(
            card_emails, card_emails.c.email == func.lower(Signup.email)
        )
        rows = self.session.scalars(
            base.where(no_card).order_by(Signup.created_at.desc(), Signup.id.desc()).limit(limit)
        ).all()
        totale = (
            self.session.scalar(
                select(func.count())
                .select_from(Signup)
                .outerjoin(card_emails, card_emails.c.email == func.lower(Signup.email))
                .where(no_card)
            )
            or 0
        )
        return [SignupListItem.model_validate(row) for row in rows], totale

    def get(self, freelancer_id: UUID) -> FreelancerDetail:
        """The row with its thread of comments, newest first, and since REB-284 every
        other place the hub already knows this address: the sign-up's own UTM set,
        the last handful of logins and guide downloads, and the PigroCRM space when
        the address owns one. Only here: the list stays `FreelancerRead` alone, since
        two hundred people are not two hundred fan-outs to four sources."""
        row = self._require(freelancer_id)
        user = self.session.get(User, row.user_id)
        assert user is not None
        logins = _logins_per_card()
        counted = self.session.execute(
            select(logins.c.accessi, logins.c.ultimo_accesso).where(logins.c.user_id == row.user_id)
        ).first()
        signup_row = self.session.scalar(
            select(Signup).where(func.lower(Signup.email) == user.email.lower())
        )
        accessi, ultimo = counted if counted is not None else (None, None)
        read = _read_with_logins(row, user, accessi, ultimo, signup_row)
        read.commenti = CommentService(self.session).list(ENTITY, freelancer_id)
        detail = FreelancerDetail.model_validate(read)
        detail.iscrizione_utm = _signup_utm(signup_row)
        detail.ultimi_accessi = LoginService(self.session).for_user(user.id)
        detail.ultimi_download_guida = PerkService(self.session).recent_for_user(user.id)
        detail.pigro_slug = self._pigro_slug(user.email)
        return detail

    def _pigro_slug(self, email: str) -> str | None:
        """`None` whenever Pigro is not configured, not reachable, or the address
        owns no space (REB-284): an admin reading a candidate's card is never blocked
        by a CRM the hub does not control."""
        if self.settings is None or self.http is None or not self.settings.pigro_registry_token:
            return None
        try:
            space = PigroRegistry(self.settings, self.http).find_by_email(email, self.session)
        except PigroUnavailable:
            return None
        return space.slug if space is not None else None

    def cv(self, freelancer_id: UUID) -> CvFile:
        return cv_of(self._require(freelancer_id))

    def cv_text(self, freelancer_id: UUID) -> CvText:
        """The stored CV as text (ORB-206), or `NotFound("cv", ...)` like `cv`: a card
        born from a signup has no file to read, and that is a sentence, not a scan."""
        file = cv_of(self._require(freelancer_id))
        return extract_text(file.content, file.filename)

    def set_status(self, freelancer_id: UUID, change: StatusChange) -> FreelancerRead:
        if change.stato not in FREELANCER_STATES:
            raise ValidationFailed(ENTITY, "stato", f"uno fra {', '.join(FREELANCER_STATES)}")
        row = self._require(freelancer_id)
        row.stato = change.stato
        if change.note is not None:
            row.note = change.note.strip() or None
        self.session.commit()
        user = self.session.get(User, row.user_id)
        assert user is not None
        return freelancer_read(row, user)

    def override(
        self, freelancer_id: UUID, data: FreelancerOverride, admin_id: UUID
    ) -> FreelancerRead:
        """Sets or clears any field `FreelancerOverride` names, on the card or on the
        linked `users` row for the three identity fields (`_ADMIN_IDENTITY_FIELDS`),
        and records the real delta -- never the patch, resending the value already
        there is not a change (`rebase_core.audit.field_changes`) -- as one
        `AdminAction` (REB-347). Identity lives on one `users` row across every role a
        person has here; overriding a name moves it everywhere that row is read, the
        same as a self-edit through `MemberService.update` already does."""
        row = self._require(freelancer_id)
        user = self.session.get(User, row.user_id)
        assert user is not None
        changes = supplied_changes(data)
        if not changes:
            return freelancer_read(row, user)
        identity_changes = {k: v for k, v in changes.items() if k in _ADMIN_IDENTITY_FIELDS}
        row_changes = {k: v for k, v in changes.items() if k not in _ADMIN_IDENTITY_FIELDS}
        reject_cleared_columns(ENTITY, User, identity_changes)
        reject_cleared_columns(ENTITY, Freelancer, row_changes)
        before = {
            **{field: getattr(user, field) for field in identity_changes},
            **{field: getattr(row, field) for field in row_changes},
        }
        for field, value in identity_changes.items():
            setattr(user, field, value)
        for field, value in row_changes.items():
            setattr(row, field, value)
        self.session.commit()
        after = {
            **{field: getattr(user, field) for field in identity_changes},
            **{field: getattr(row, field) for field in row_changes},
        }
        delta = field_changes(before, after)
        if delta:
            AdminActionService(self.session).record(ENTITY, row.id, "overridden", admin_id, delta)
        return freelancer_read(row, user)

    def clear_cv(self, freelancer_id: UUID, admin_id: UUID) -> FreelancerRead:
        """Drops the stored CV. The bytes are never part of the audit entry -- a CV is
        personal data with a retention to honour (`Freelancer`'s own docstring), and a
        payload that carried them would duplicate exactly what this call removes. The
        three metadata columns are recorded, so an admin can see a CV was there, by whom
        it was cleared and when, without the file itself; there is no `revert` for this
        one kind, on purpose (`rebase_core.audit`'s own module docstring).

        The anonymous card written from the CV goes in the same commit (REB-510): a
        description of a file the hub no longer holds is not one to keep showing, and
        the admin route and the MCP tool both reach it through here."""
        # Imported here, not at the top: `cards` reads the CV through this module.
        from rebase_core.cards import CardWriter

        row = self._require(freelancer_id)
        user = self.session.get(User, row.user_id)
        assert user is not None
        if row.cv_bytes is None:
            return freelancer_read(row, user)
        before = {"cv_filename": row.cv_filename, "cv_mime": row.cv_mime, "cv_size": row.cv_size}
        row.cv_bytes, row.cv_filename, row.cv_mime, row.cv_size = None, None, None, None
        CardWriter(self.session, None).delete(row.id)
        self.session.commit()
        AdminActionService(self.session).record(
            ENTITY,
            row.id,
            "cleared",
            admin_id,
            {"changed": ["cv"], "before": before, "after": dict.fromkeys(before, None)},
        )
        return freelancer_read(row, user)

    def soft_delete(self, freelancer_id: UUID, admin_id: UUID) -> FreelancerRead:
        """Sets `deleted_at`. No hard delete anywhere in this path: an admin's own
        mistake, or a delete aimed at the wrong row, must be reversible (Lorenzo,
        2026-09-22: «tutto deve essere tracciabile e reversibile da un admin»)."""
        row = self._require(freelancer_id)
        row.deleted_at = utcnow()
        self.session.commit()
        AdminActionService(self.session).record(ENTITY, row.id, "deleted", admin_id, {})
        user = self.session.get(User, row.user_id)
        assert user is not None
        return freelancer_read(row, user)

    def restore(self, freelancer_id: UUID, admin_id: UUID) -> FreelancerRead:
        """A no-op, not an error, on a card that is not deleted -- an idempotent call
        must never write a timeline entry claiming a recovery that never happened."""
        row = self._require(freelancer_id, include_deleted=True)
        user = self.session.get(User, row.user_id)
        assert user is not None
        if row.deleted_at is not None:
            row.deleted_at = None
            self.session.commit()
            AdminActionService(self.session).record(ENTITY, row.id, "restored", admin_id, {})
        return freelancer_read(row, user)

    def audit_timeline(
        self, freelancer_id: UUID, limit: int = TIMELINE_LIMIT_DEFAULT
    ) -> list[AdminActionRead]:
        """Who overrode, cleared, deleted or restored this card, and when. Works on a
        deleted card too -- otherwise the one entry that says so would be unreadable
        exactly when it matters most."""
        self._require(freelancer_id, include_deleted=True)
        return AdminActionService(self.session).timeline(ENTITY, freelancer_id, limit)

    def revert(self, freelancer_id: UUID, action_id: UUID, admin_id: UUID) -> FreelancerRead:
        """Restores a field an earlier `overridden` action changed to the value that
        entry's own `payload["before"]` names -- the reversibility REB-347 asks for,
        read from the trail rather than guessed at. Only an `overridden` entry reverts
        this way: `deleted` reverses through `restore`, and a cleared CV has no bytes
        left to put back (`clear_cv`'s own docstring)."""
        row = self._require(freelancer_id)
        action = AdminActionService(self.session).require(action_id)
        if action.entity_type != ENTITY or action.entity_id != row.id:
            raise NotFound(ENTITY, action_id)
        if action.kind != "overridden":
            raise ValidationFailed(
                ENTITY, "action_id", "si può ripristinare solo una modifica di campo"
            )
        before = action.payload.get("before", {})
        restored = {
            field: coerce_stored_value(
                User if field in _ADMIN_IDENTITY_FIELDS else Freelancer, field, value
            )
            for field, value in before.items()
        }
        return self.override(freelancer_id, FreelancerOverride(**restored), admin_id)

    def _require(self, freelancer_id: UUID, *, include_deleted: bool = False) -> Freelancer:
        row = self.session.get(Freelancer, freelancer_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            raise NotFound(ENTITY, freelancer_id)
        return row

    def _find(self, email: str) -> Freelancer | None:
        return self.session.scalar(
            select(Freelancer)
            .join(User, User.id == Freelancer.user_id)
            .where(func.lower(User.email) == email)
        )
