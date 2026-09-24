"""The draft: durable, and first.

Spec 6.2 orders the send, and the order is the design. The row exists **before** Gmail
is called, and the `Message-ID` we will put in the RFC822 is minted here, at creation.
Two consequences, and both are the point:

* text somebody typed is never lost to an HTTP error, a closed tab or a refresh -- a
  composer that keeps it in the browser's memory loses it on the first of the three;
* an unknown outcome (spec 6.3(b): the answer never arrived) is resolvable by an
  **exact** lookup on an id we chose, instead of by comparing recipients and timestamps
  and hoping two similar messages are not minutes apart.

The previous system kept this in a JSON file with a non-atomic read-modify-write, so two concurrent
sends lost the count. Here the uniqueness of `message_id_header` is the database's:
`test_email_drafts.py` races two creates onto the same id and requires exactly one to
survive.

Nothing here logs, and nothing that leaves this module carries the body or a recipient:
`NotFound`, `Conflict` and `ValidationFailed` name the field and the entity, never the
value. A draft is somebody's unsent private correspondence, which is if anything more
sensitive than the mail that has already gone.
"""

from collections.abc import Sequence
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.config import Settings
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.deals.models import Deal
from pigrocrm.core.documents.models import DocumentVersion
from pigrocrm.core.emitter.models import EmitterProfile
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.gmail.attach import describe_attachments
from pigrocrm.core.gmail.models import EmailDraft, GmailMessage
from pigrocrm.core.gmail.rfc822 import new_message_id
from pigrocrm.core.gmail.schemas import (
    EDITABLE_SEND_STATES,
    EmailDraftCreate,
    EmailDraftListQuery,
    EmailDraftPage,
    EmailDraftRead,
    EmailDraftUpdate,
)
from pigrocrm.core.people.models import Person

ENTITY = "email_draft"

# The states in which the text is still the user's to change, and -- the same set, and
# for the same reason -- the states from which it can be sent. Defined once in
# `schemas.py` rather than here, because `EmailSendService` and the claim statement of
# `GmailRepository.claim_draft_for_send` need the identical answer: a second copy that
# drifted would offer the composer a draft the send path refuses, or the reverse.
_EDITABLE = EDITABLE_SEND_STATES
_ALREADY_GONE = "questa email è già inviata o in invio: duplicala per modificarla"

# Which table each `entity_type` names. Checked against the *right* table rather than
# against "some row with this id": `entity_id` carries no foreign key (it points at one
# of three tables), so this mapping is the only thing standing between a caller's UUID
# and a draft filed against a row of the wrong kind.
_ENTITY_TABLES: dict[str, type[Customer] | type[Person] | type[Deal]] = {
    "customer": Customer,
    "person": Person,
    "deal": Deal,
}

# Every field of the read model that is a column of the row, which is all of them but
# the attachments by name. Derived rather than listed, so a column added to both the
# model and `EmailDraftRead` is read without anybody remembering to add it here too.
_COLUMNS = tuple(name for name in EmailDraftRead.model_fields if name != "attachments")


def read_drafts(session: Session, rows: Sequence[EmailDraft]) -> list[EmailDraftRead]:
    """The rows as the API answers them, each attachment named as it will leave.

    The one builder of `EmailDraftRead`, for this service and for the send path alike
    (REB-415): the Email tab renders the draft a person is about to send from this, and a
    draft read back after a send or a verification must name its files the same way the
    list did, or the row would appear to lose its attachments at the press. One query
    for every attachment of every row, however many rows there are.
    """
    named = describe_attachments(
        session, (UUID(str(value)) for row in rows for value in row.attachment_version_ids)
    )
    return [
        EmailDraftRead.model_validate(
            {
                **{name: getattr(row, name) for name in _COLUMNS},
                "attachments": [named[UUID(str(value))] for value in row.attachment_version_ids],
            }
        )
        for row in rows
    ]


def read_draft(session: Session, row: EmailDraft) -> EmailDraftRead:
    return read_drafts(session, [row])[0]


# The honest value for an installation with neither a public URL nor a website. It is a
# reserved TLD (RFC 2606), so a Message-ID built on it can never collide with a real
# domain and can never be mistaken for one -- which is what a placeholder like
# `example.com` would get wrong.
_FALLBACK_DOMAIN = "localhost.invalid"


class EmailDraftService:
    def __init__(self, session: Session, *, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    # ---- the pieces the write paths share ------------------------------------------

    def _domain(self) -> str:
        """The right-hand side of the `Message-ID`, from the installation's own identity.

        `public_url` first, because that is the origin this CRM answers on and the one
        Google already compares character by character for the OAuth redirect. The
        issuer's website second, because an installation reachable only on a LAN still
        has an identity its clients recognise. `localhost.invalid` last, and it is a
        deliberate admission rather than a default that looks plausible.
        """
        for candidate in (self.settings.public_url, self._emitter_site()):
            host = urlparse(candidate).hostname if "//" in candidate else candidate.strip()
            if host and "." in host:
                return host.lower()
        return _FALLBACK_DOMAIN

    def _emitter_site(self) -> str:
        site = self.session.execute(select(EmitterProfile.sito_web).limit(1)).scalar_one_or_none()
        return site or ""

    def _get(self, draft_id: UUID) -> EmailDraft:
        draft = self.session.get(EmailDraft, draft_id)
        if draft is None:
            raise NotFound(ENTITY, draft_id)
        return draft

    def _check_entity(self, entity_type: str, entity_id: UUID) -> None:
        model = _ENTITY_TABLES[entity_type]
        found = self.session.execute(
            select(model.id).where(model.id == entity_id, model.deleted_at.is_(None))
        ).scalar_one_or_none()
        if found is None:
            # Named as the entity the caller asked for, so the composer can say which
            # field is wrong -- and never carrying the draft's own contents.
            raise NotFound(entity_type, entity_id)

    def _check_attachments(self, version_ids: list[UUID]) -> list[str]:
        """Spec 6.4: attachments come only from `document_versions`, never from an
        upload. Validated here so a well-formed UUID is a `NotFound` the composer can
        show, rather than an `IntegrityError` from the driver -- or, worse, a send that
        discovers at Gmail's door that there is nothing to attach."""
        checked: list[str] = []
        for version_id in version_ids:
            found = self.session.get(DocumentVersion, version_id)
            if found is None:
                raise NotFound("document_version", version_id)
            checked.append(str(version_id))
        return checked

    def _check_reply_target(self, message_id: UUID | None) -> None:
        if message_id is not None and self.session.get(GmailMessage, message_id) is None:
            raise NotFound("gmail_message", message_id)

    @staticmethod
    def _check_recipients(addresses: list[str]) -> None:
        """One recipient at least, checked before the row is written rather than at send
        time: a draft that cannot be sent is not a draft, it is a trap with a Send
        button. The *shape* of each address is `build_rfc822`'s job (B2-2) and is checked
        again there, because that is the point at which the string becomes a header."""
        if not addresses:
            raise ValidationFailed(ENTITY, "to", "serve almeno un destinatario")

    def _require_editable(self, draft: EmailDraft) -> None:
        if draft.send_state not in _EDITABLE:
            raise Conflict(ENTITY, _ALREADY_GONE, send_state=draft.send_state)

    # ---- the surface ----------------------------------------------------------------

    def create(self, data: EmailDraftCreate, actor: Actor) -> EmailDraftRead:
        actor.require_write("create_email_draft")
        self._check_entity(data.entity_type, data.entity_id)
        self._check_reply_target(data.in_reply_to_message_id)
        self._check_recipients(list(data.to_addresses))
        attachments = self._check_attachments(list(data.attachment_version_ids))

        draft = EmailDraft(
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            to_addresses=list(data.to_addresses),
            cc_addresses=list(data.cc_addresses),
            subject=data.subject,
            body_markdown=data.body_markdown,
            attachment_version_ids=attachments,
            message_id_header=new_message_id(self._domain()),
            in_reply_to_message_id=data.in_reply_to_message_id,
            send_state="bozza",
        )
        self.session.add(draft)

        # Committed here, not left to the caller: "the draft is durable" is a claim about
        # what survives the request dying, and a row that is only flushed does not.
        self.session.commit()
        return read_draft(self.session, draft)

    def update(self, draft_id: UUID, data: EmailDraftUpdate, actor: Actor) -> EmailDraftRead:
        actor.require_write("update_email_draft")
        draft = self._get(draft_id)
        self._require_editable(draft)

        if data.to_addresses is not None:
            self._check_recipients(list(data.to_addresses))
            draft.to_addresses = list(data.to_addresses)
        if data.cc_addresses is not None:
            draft.cc_addresses = list(data.cc_addresses)
        if data.subject is not None:
            draft.subject = data.subject
        if data.body_markdown is not None:
            draft.body_markdown = data.body_markdown
        if data.attachment_version_ids is not None:
            draft.attachment_version_ids = self._check_attachments(
                list(data.attachment_version_ids)
            )

        # `message_id_header` is deliberately untouched: minting a new one on every edit
        # would make the reconciliation of spec 6.3 look for a message that was never
        # sent under that name. A failed draft that is edited becomes a draft again --
        # keeping `fallito` would leave an error sentence next to text it no longer
        # describes, which reads as a failure that has just happened.
        if draft.send_state == "fallito":
            draft.send_state = "bozza"
            draft.last_error = None
        self.session.commit()
        return read_draft(self.session, draft)

    def get(self, draft_id: UUID, actor: Actor) -> EmailDraftRead:
        return read_draft(self.session, self._get(draft_id))

    def delete(self, draft_id: UUID, actor: Actor) -> None:
        """A hard delete, unlike every CRM entity: an unsent draft the user discarded is
        not history, and keeping it would leave somebody's abandoned text in the database
        forever. A draft in flight or already sent is refused for the opposite reason --
        deleting the row mid-send leaves the outcome with nothing to be recorded against,
        which is the previous system's «404 Offerta non trovata per registrare
        l'invio email» with the mail already delivered."""
        actor.require_write("delete_email_draft")
        draft = self._get(draft_id)
        self._require_editable(draft)
        self.session.delete(draft)
        self.session.commit()

    def repo_draft(self, draft_id: UUID) -> EmailDraft:
        """The ORM row. For the send path of B2-5 and the reconciliation of B2-6, which
        write `send_state`, `send_attempted_at` and `sent_gmail_message_id` -- columns no
        Update schema accepts, because they are facts rather than input."""
        return self._get(draft_id)

    # `list` must stay the last method defined in this class -- the same unconditional
    # rule as `CustomerService.list`: defining a method named `list` rebinds that name in
    # the class namespace, so any later method annotated `-> list[...]` would resolve
    # `list` to this method instead of the builtin and fail at import time.
    def list(self, query: EmailDraftListQuery, actor: Actor) -> EmailDraftPage:
        conditions = []
        if query.entity_type is not None:
            conditions.append(EmailDraft.entity_type == query.entity_type)
        if query.entity_id is not None:
            conditions.append(EmailDraft.entity_id == query.entity_id)
        if query.send_state is not None:
            conditions.append(EmailDraft.send_state == query.send_state)
        if query.unsent:
            conditions.append(EmailDraft.send_state != "inviato")

        total = self.session.execute(
            select(func.count()).select_from(EmailDraft).where(*conditions)
        ).scalar_one()
        rows = (
            self.session.execute(
                select(EmailDraft)
                .where(*conditions)
                .order_by(EmailDraft.created_at.desc())
                .limit(query.limit)
            )
            .scalars()
            .all()
        )
        return EmailDraftPage(items=read_drafts(self.session, rows), total=total)
