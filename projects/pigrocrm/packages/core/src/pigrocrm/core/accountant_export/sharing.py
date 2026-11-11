"""Share links for a stored export (spec accountant export § 3 and § 5).

A link is how the file reaches the accountant, who has no account: so the link is the only
credential and its controls are the design. The token is 256 random bits (`token_urlsafe(32)`),
held only as its SHA-256 (the raw value exists once, in the answer that makes the link), lives 7
days unless the administrator says otherwise and at most 30, can be revoked in one step, and every
file it hands out counts and leaves an audit row with the moment and the client address.

**An export with pay never travels on a plain link** (§ 3 and § 4): the payroll consultant's
«variazioni paghe» and an export with the payslips ticked are shared only by a link that names
the address of whoever receives it. Opening such a link asks a one-time code, which the door mails
to that address, six digits that live 15 minutes and five wrong answers and are spent by the first
right one; the right answer opens a half-hour session, a random value only that browser holds and
that the door keeps as a hash, which the downloads then present. The link alone opens nothing.

Two halves, told apart by who they serve. `ExportShareService` is the administrators': make a
link, list the links, read one with its openings, revoke it. An administrator whose scope covers
the export's legal entity (an unscoped one for the export of every entity) or an agent with
`full_access` makes a link; reading and revoking are a person's, never an agent's
(`agent_actions.PERSON_ONLY_ACTIONS`). `ExportShareDoor` is the public half: it takes a raw token
and nothing else, binds the system scope for the one row that token's hash names, and hands out
the file or a refusal that says whether the link is unknown, expired or revoked. It is not a
`*Service` on purpose: it has no actor, and what the guard rails of a service assume (a role
check, an activity of the actor) do not exist here; the audit row is the record.
"""

import datetime as dt
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from uuid import UUID

from pydantic import EmailStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from pigrocrm.core.accountant_export.access import visible_export
from pigrocrm.core.accountant_export.models import (
    AccountantExportRow,
    ExportShareCode,
    ExportShareLink,
    ExportShareOpening,
)
from pigrocrm.core.accountant_export.schemas import (
    SharedExportPartRead,
    SharedExportRead,
    ShareLinkCreated,
    ShareLinkListRead,
    ShareLinkOpeningRead,
    ShareLinkRead,
)
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.actor import Actor
from pigrocrm.core.auth.models import User
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db.scope import bind_scope
from pigrocrm.core.errors import DomainError, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.models import LegalEntity
from pigrocrm.core.mail import EmailSender, export_code_mail
from pigrocrm.core.storage.base import DocumentStorage

LINK_ENTITY = "accountant_export_link"
EXPORT_ENTITY = "accountant_export"
CREATE_ACTION = "create_export_share_link"
LIST_ACTION = "list_export_share_links"
REVOKE_ACTION = "revoke_export_share_link"
CREATED_KIND = "export_share_link_created"
REVOKED_KIND = "export_share_link_revoked"
DEFAULT_DAYS = 7
MAX_DAYS = 30
ZIP_MIME = "application/zip"
ALL_ENTITIES_NAME = "Tutte le aziende"
# The openings one link answers: the audit is a table, this is the page of it a person reads.
OPENINGS_SHOWN = 100
ADDRESS_MAX_LENGTH = 64
# The path of the page that tells the accountant what the link is, before the download. The
# token rides in `?t=`, the shape of the CRM's other links by mail, which the SPA takes out of
# the address before anything else runs.
SHARE_PAGE_PATH = "/app/shared-export"
# The one-time code (§ 3): six digits, a quarter of an hour, five wrong answers, one mail a
# minute and five an hour per link. The session a right code opens lasts half an hour.
CODE_DIGITS = 6
CODE_MINUTES = 15
CODE_MAX_FAILURES = 5
CODE_RESEND_SECONDS = 60
CODES_PER_HOUR = 5
ACCESS_MINUTES = 30
RECIPIENT_MAX_LENGTH = 320
_EMAIL = TypeAdapter(EmailStr)


class ExportLinkUnknown(DomainError):
    """No link carries this token's hash: not told apart from a token that never existed."""

    code = "export_link_unknown"

    def __init__(self) -> None:
        super().__init__("questo link non esiste: chiedi a chi te l'ha mandato di rifarlo")


class ExportLinkExpired(DomainError):
    code = "export_link_expired"

    def __init__(self) -> None:
        super().__init__("questo link è scaduto: chiedi a chi te l'ha mandato di rifarlo")


class ExportLinkRevoked(DomainError):
    code = "export_link_revoked"

    def __init__(self) -> None:
        super().__init__("questo link è stato revocato: chiedi a chi te l'ha mandato di rifarlo")


class ExportCodeRequired(DomainError):
    """The link asks a code and the request brought no session a code opened."""

    code = "export_code_required"

    def __init__(self) -> None:
        super().__init__("questo link chiede un codice: chiedilo e inseriscilo per aprire i file")


class ExportCodeInvalid(DomainError):
    """A wrong, expired or spent code, never told apart: what is wrong with it is not the
    business of whoever is guessing."""

    code = "export_code_invalid"

    def __init__(self) -> None:
        super().__init__("il codice non è giusto o è scaduto: chiedine uno nuovo")


class ExportCodeThrottled(DomainError):
    code = "export_code_throttled"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ExportCodeMailFailed(DomainError):
    code = "export_code_mail_failed"

    def __init__(self) -> None:
        super().__init__("non sono riuscito a mandare il codice: riprova tra poco")


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_code(link_id: UUID, code: str) -> str:
    """The code held with the link's id as salt: six digits are few, so the table alone must not
    be enough to read one, and the same digits on two links do not hash alike."""
    return hashlib.sha256(f"{link_id}:{code}".encode()).hexdigest()


def mask_email(address: str) -> str:
    """`p…@studio-esempio.it`: enough for the recipient to know which inbox, no more."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}…@{domain}"


def _words(day: dt.date) -> str:
    return day.strftime("%d/%m/%Y")


def link_state(link: ExportShareLink, now: dt.datetime) -> str:
    if link.revoked_at is not None:
        return "revoked"
    return "expired" if link.expires_at <= now else "active"


def share_url(settings: Settings, raw: str) -> str:
    """The address of the page the accountant opens, on the space's own origin (a space's
    `public_url` already ends with its prefix). The origin is the configured one and nothing
    else: a link built from a request's own `Host` would hand a live credential to that host."""
    origin = settings.public_url.strip().rstrip("/")
    if not origin:
        raise ValidationFailed(
            LINK_ENTITY,
            "public_url",
            "PIGROCRM_PUBLIC_URL non configurato: il link non ha un indirizzo",
        )
    return f"{origin}{SHARE_PAGE_PATH}?t={raw}"


def _read(
    link: ExportShareLink,
    export: AccountantExportRow,
    now: dt.datetime,
    openings: list[ExportShareOpening] | None = None,
) -> ShareLinkRead:
    return ShareLinkRead(
        id=link.id,
        export_id=link.export_id,
        legal_entity_id=link.legal_entity_id,
        created_at=link.created_at,
        created_by=link.created_by,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        recipient_email=link.recipient_email,
        state=link_state(link, now),
        download_count=link.download_count,
        period_from=export.period_from,
        period_to=export.period_to,
        scope=export.scope,
        openings=[
            ShareLinkOpeningRead(opened_at=o.opened_at, address=o.address, part=o.part)
            for o in openings or []
        ],
    )


class ExportShareService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.activities = ActivityService(session)

    def create_export_share_link(
        self,
        export_id: UUID,
        actor: Actor,
        *,
        days: int = DEFAULT_DAYS,
        recipient_email: str | None = None,
        now: dt.datetime | None = None,
    ) -> ShareLinkCreated:
        """A link for one stored export, valid `days` days (7 by default, at most 30). The
        address in the answer is the only time it is shown. With `recipient_email` the link
        asks a one-time code mailed to that address before the files open; an export that
        carries pay has no other kind of link, and a plain one is refused."""
        actor.require_admin(CREATE_ACTION)
        export = visible_export(self.session, export_id, actor, CREATE_ACTION)
        if not 1 <= days <= MAX_DAYS:
            raise ValidationFailed(
                LINK_ENTITY,
                "days",
                f"un link dura da 1 a {MAX_DAYS} giorni",
                expected=f"un numero da 1 a {MAX_DAYS}",
            )
        if export.deleted_at is not None:
            raise ValidationFailed(
                EXPORT_ENTITY,
                "export_id",
                "l'esportazione è stata eliminata dopo 13 mesi: se serve, va rifatta",
            )
        recipient = self._recipient(recipient_email, required=export.payroll)
        moment = now or dt.datetime.now(dt.UTC)
        raw = secrets.token_urlsafe(32)
        url = share_url(self.settings, raw)
        link = ExportShareLink(
            export_id=export.id,
            legal_entity_id=export.legal_entity_id,
            token_hash=hash_token(raw),
            expires_at=moment + dt.timedelta(days=days),
            created_by=actor.id,
            created_by_type=actor.type,
            recipient_email=recipient,
            download_count=0,
        )
        self.session.add(link)
        self.session.flush()
        # Who made it and until when; never the address, which is the credential.
        self.activities.record(
            EXPORT_ENTITY,
            export.id,
            CREATED_KIND,
            actor,
            {
                "link_id": str(link.id),
                "expires_at": link.expires_at.isoformat(),
                "days": days,
                "with_code": recipient is not None,
            },
        )
        self.session.commit()
        return ShareLinkCreated(**_read(link, export, moment).model_dump(), url=url)

    @staticmethod
    def _recipient(address: str | None, *, required: bool) -> str | None:
        if address is None or not address.strip():
            if required:
                raise ValidationFailed(
                    LINK_ENTITY,
                    "recipient_email",
                    "un'esportazione con le paghe si condivide solo con un link con codice: "
                    "serve l'indirizzo di chi lo riceve",
                    expected="un indirizzo email",
                )
            return None
        try:
            return str(_EMAIL.validate_python(address.strip())).lower()
        except PydanticValidationError as invalid:
            raise ValidationFailed(
                LINK_ENTITY,
                "recipient_email",
                "l'indirizzo di chi riceve il link non è valido",
                expected="un indirizzo email",
            ) from invalid

    def list_export_share_links(
        self,
        actor: Actor,
        export_id: UUID | None = None,
        *,
        offset: int = 0,
        limit: int = 50,
        now: dt.datetime | None = None,
    ) -> ShareLinkListRead:
        """The links of the exports the actor's scope covers, newest first, with how many there
        are in all. A list carries no openings: `get_export_share_link` has them."""
        actor.require_admin(LIST_ACTION)
        moment = now or dt.datetime.now(dt.UTC)
        base = select(ExportShareLink).join(
            AccountantExportRow, AccountantExportRow.id == ExportShareLink.export_id
        )
        if export_id is not None:
            base = base.where(ExportShareLink.export_id == export_id)
        if actor.legal_entities is not None:
            # The database hides these rows as well; the explicit filter is for a session that
            # is not bound to the actor's scope, and hides the export of every entity.
            base = base.where(ExportShareLink.legal_entity_id.in_(actor.legal_entities))
        total = self.session.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = self.session.execute(
            base.add_columns(AccountantExportRow)
            .order_by(ExportShareLink.created_at.desc(), ExportShareLink.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return ShareLinkListRead(
            items=[_read(link, export, moment) for link, export in rows],
            total_count=total,
        )

    def get_export_share_link(
        self, link_id: UUID, actor: Actor, *, now: dt.datetime | None = None
    ) -> ShareLinkRead:
        """One link with its latest openings: when and from which address."""
        link, export = self._visible_link(link_id, actor, LIST_ACTION)
        openings = list(
            self.session.scalars(
                select(ExportShareOpening)
                .where(ExportShareOpening.link_id == link.id)
                .order_by(ExportShareOpening.opened_at.desc(), ExportShareOpening.id.desc())
                .limit(OPENINGS_SHOWN)
            )
        )
        return _read(link, export, now or dt.datetime.now(dt.UTC), openings)

    def revoke_export_share_link(
        self, link_id: UUID, actor: Actor, *, now: dt.datetime | None = None
    ) -> ShareLinkRead:
        """Ends the link at once: whoever holds the address is refused from now on. Revoking a
        link that is already revoked changes nothing."""
        actor.require_admin(REVOKE_ACTION)
        link, export = self._visible_link(link_id, actor, REVOKE_ACTION)
        moment = now or dt.datetime.now(dt.UTC)
        if link.revoked_at is None:
            link.revoked_at = moment
            link.revoked_by = actor.id
            self.activities.record(
                EXPORT_ENTITY,
                export.id,
                REVOKED_KIND,
                actor,
                {"link_id": str(link.id), "download_count": link.download_count},
            )
            self.session.commit()
        return _read(link, export, moment)

    def _visible_link(
        self, link_id: UUID, actor: Actor, action: str
    ) -> tuple[ExportShareLink, AccountantExportRow]:
        actor.require_admin(action)
        link = self.session.get(ExportShareLink, link_id)
        if link is None:
            raise NotFound(LINK_ENTITY, link_id)
        # The link's export decides who may see it, exactly as the export's own routes do.
        export = visible_export(self.session, link.export_id, actor, action)
        return link, export


@dataclass(frozen=True)
class OpenedFile:
    data: bytes
    content_type: str
    filename: str


class ExportShareDoor:
    """The public half: a raw token in, the file out. No actor, no session of a person: the
    system scope is bound for the one link the token's hash names, and nothing else is read.
    A link with a code opens nothing until the code mailed to its recipient was given."""

    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()

    def describe(
        self, token: str, *, access: str | None = None, now: dt.datetime | None = None
    ) -> SharedExportRead:
        """What the link opens, before any download: the company and period, who shared it, when
        it expires and the ZIPs with their sizes. Reading it is not an opening. A link with a
        code says so, and lists no file until `access` is the session a right code opened."""
        moment = now or dt.datetime.now(dt.UTC)
        link, export = self._live(token, moment)
        unlocked = self._unlocked(link, access, moment)
        return SharedExportRead(
            company=self._company(export),
            period_from=export.period_from,
            period_to=export.period_to,
            built_at=export.built_at,
            shared_by=self._sharer(link),
            expires_at=link.expires_at,
            requires_code=link.recipient_email is not None,
            recipient_hint=(
                mask_email(link.recipient_email) if link.recipient_email is not None else None
            ),
            unlocked=unlocked,
            parts=[
                SharedExportPartRead(
                    part=number, filename=entry["filename"], size_bytes=entry["size_bytes"]
                )
                for number, entry in enumerate(export.parts, start=1)
            ]
            if unlocked
            else [],
        )

    def send_code(
        self,
        token: str,
        sender: EmailSender,
        *,
        now: dt.datetime | None = None,
    ) -> str:
        """Mails a new code to the link's recipient and answers where it went (the address with
        most of it hidden). One a minute and five an hour per link, so the recipient's inbox
        cannot be flooded through a link. A link with no recipient has no code to send."""
        moment = now or dt.datetime.now(dt.UTC)
        link, export = self._live(token, moment)
        if link.recipient_email is None:
            raise ValidationFailed(
                LINK_ENTITY,
                "token",
                "questo link non chiede un codice",
                expected="un link con codice",
            )
        recent = list(
            self.session.scalars(
                select(ExportShareCode.sent_at).where(
                    ExportShareCode.link_id == link.id,
                    ExportShareCode.sent_at > moment - dt.timedelta(hours=1),
                )
            )
        )
        if recent and max(recent) > moment - dt.timedelta(seconds=CODE_RESEND_SECONDS):
            raise ExportCodeThrottled("il codice è già stato mandato: aspetta un minuto")
        if len(recent) >= CODES_PER_HOUR:
            raise ExportCodeThrottled("hai chiesto troppi codici: riprova tra un'ora")
        code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
        self.session.add(
            ExportShareCode(
                link_id=link.id,
                legal_entity_id=link.legal_entity_id,
                code_hash=_hash_code(link.id, code),
                sent_at=moment,
                expires_at=moment + dt.timedelta(minutes=CODE_MINUTES),
                failures=0,
            )
        )
        self.session.flush()
        period = f"dal {_words(export.period_from)} al {_words(export.period_to)}"
        mail = export_code_mail(
            link.recipient_email, code, CODE_MINUTES, self._company(export), period
        )
        if not sender.send(mail):
            self.session.rollback()
            raise ExportCodeMailFailed
        self.session.commit()
        return mask_email(link.recipient_email)

    def verify_code(self, token: str, code: str, *, now: dt.datetime | None = None) -> str:
        """Spends a right code and answers the session it opens, a random value shown here once
        and kept as a hash. A wrong answer counts against the codes still alive and burns them at
        the fifth; a code that is wrong, expired or spent is the same refusal."""
        moment = now or dt.datetime.now(dt.UTC)
        link, _ = self._live(token, moment)
        if link.recipient_email is None:
            raise ExportCodeInvalid
        live = list(
            self.session.scalars(
                select(ExportShareCode)
                .where(
                    ExportShareCode.link_id == link.id,
                    ExportShareCode.consumed_at.is_(None),
                    ExportShareCode.expires_at > moment,
                    ExportShareCode.failures < CODE_MAX_FAILURES,
                )
                .with_for_update()
            )
        )
        given = _hash_code(link.id, code.strip())
        match = next((row for row in live if hmac.compare_digest(row.code_hash, given)), None)
        if match is None:
            for row in live:
                row.failures += 1
            self.session.commit()
            raise ExportCodeInvalid
        raw = secrets.token_urlsafe(32)
        for row in live:
            row.consumed_at = moment
        match.access_hash = hash_token(raw)
        match.access_expires_at = moment + dt.timedelta(minutes=ACCESS_MINUTES)
        self.session.commit()
        return raw

    def open(
        self,
        token: str,
        part: int,
        address: str,
        *,
        access: str | None = None,
        now: dt.datetime | None = None,
    ) -> OpenedFile:
        """One ZIP of the export. The file is read first, then the opening is counted and
        written to the audit in one commit, so a download that failed leaves no row."""
        moment = now or dt.datetime.now(dt.UTC)
        link, export = self._live(token, moment)
        if not self._unlocked(link, access, moment):
            raise ExportCodeRequired
        if not 1 <= part <= len(export.parts):
            raise NotFound(EXPORT_ENTITY, f"{export.id}/parts/{part}")
        entry = export.parts[part - 1]
        data = self.storage.get(entry["key"])
        self.session.execute(
            update(ExportShareLink)
            .where(ExportShareLink.id == link.id)
            .values(download_count=ExportShareLink.download_count + 1)
        )
        self.session.add(
            ExportShareOpening(
                link_id=link.id,
                legal_entity_id=link.legal_entity_id,
                opened_at=moment,
                address=address[:ADDRESS_MAX_LENGTH],
                part=part,
            )
        )
        self.session.commit()
        return OpenedFile(data, ZIP_MIME, entry["filename"])

    def _unlocked(self, link: ExportShareLink, access: str | None, now: dt.datetime) -> bool:
        """A plain link is open; a link with a code is open to the session a code opened."""
        if link.recipient_email is None:
            return True
        if not access:
            return False
        opened = self.session.scalar(
            select(ExportShareCode.id).where(
                ExportShareCode.link_id == link.id,
                ExportShareCode.access_hash == hash_token(access),
                ExportShareCode.access_expires_at > now,
            )
        )
        return opened is not None

    def _live(self, token: str, now: dt.datetime) -> tuple[ExportShareLink, AccountantExportRow]:
        bind_scope(self.session, Actor.system())
        link = self.session.scalar(
            select(ExportShareLink).where(ExportShareLink.token_hash == hash_token(token))
        )
        if link is None:
            raise ExportLinkUnknown
        if link.revoked_at is not None:
            raise ExportLinkRevoked
        export = self.session.get(AccountantExportRow, link.export_id)
        # A link outlives nothing: past its day, or past the export's own 13 months, it is over.
        if link.expires_at <= now or export is None or export.deleted_at is not None:
            raise ExportLinkExpired
        if export.payroll and link.recipient_email is None:
            # No service makes one, and a row made another way opens nothing: pay is on no
            # plain link.
            raise ExportLinkRevoked
        return link, export

    def _company(self, export: AccountantExportRow) -> str:
        if export.legal_entity_id is None:
            return ALL_ENTITIES_NAME
        entity = self.session.get(LegalEntity, export.legal_entity_id)
        return entity.name if entity is not None else ALL_ENTITIES_NAME

    def _sharer(self, link: ExportShareLink) -> str | None:
        if link.created_by is None or link.created_by_type != "user":
            return None
        user = self.session.get(User, link.created_by)
        return user.name if user is not None else None


__all__ = [
    "ExportCodeInvalid",
    "ExportCodeMailFailed",
    "ExportCodeRequired",
    "ExportCodeThrottled",
    "ExportLinkExpired",
    "ExportLinkRevoked",
    "ExportLinkUnknown",
    "ExportShareDoor",
    "ExportShareService",
    "OpenedFile",
    "hash_token",
    "mask_email",
    "share_url",
]
