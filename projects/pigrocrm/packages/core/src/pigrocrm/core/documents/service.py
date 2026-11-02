"""`DocumentService`, the facade over the document domain.

The work is in four collaborators that share one `DocumentContext`: `uploads` (versions
and imported bytes), `text` (a document's text), `templating` (documents made from a
template) and the context's own owner resolution. This class keeps the rows' own
lifecycle (create, update, delete, restore, offer state) and exposes every public
operation under one name, because it is the domain's API for the api, the mcp and the
other domains.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.automations.runner import AutomationRunner
from pigrocrm.core.config import Settings
from pigrocrm.core.db import encode_cursor, today_local
from pigrocrm.core.documents.context import ENTITY, DocumentContext
from pigrocrm.core.documents.models import Document
from pigrocrm.core.documents.schemas import (
    DOCUMENT_SORTS,
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListQuery,
    DocumentPage,
    DocumentRead,
    DocumentTextRead,
    DocumentTipo,
    DocumentUpdate,
    DocumentVersionRead,
    OfferState,
)
from pigrocrm.core.documents.templating import DocumentTemplating
from pigrocrm.core.documents.text import DocumentText
from pigrocrm.core.documents.uploads import DocumentUploads
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.fields.custom import merge_custom
from pigrocrm.core.schemas import reject_cleared_columns, supplied_changes
from pigrocrm.core.storage.base import DocumentStorage

# The offer lifecycle, as a table rather than a chain of `if`s: the UI reads it to
# decide which buttons to show, and the MCP tool reads it to tell an agent what it may
# do next. `accettata` and `rifiutata` are terminal -- an offer that was answered is a
# fact, and editing that fact is a new offer, not a state change.
OFFER_TRANSITIONS: dict[str, frozenset[str]] = {
    "bozza": frozenset({"inviata"}),
    "inviata": frozenset({"accettata", "rifiutata", "bozza"}),
    "accettata": frozenset(),
    "rifiutata": frozenset(),
}


class DocumentService:
    def __init__(
        self, session: Session, storage: DocumentStorage, settings: Settings | None = None
    ) -> None:
        self._context = DocumentContext(session, storage, settings)
        self._uploads = DocumentUploads(self._context)
        self._text = DocumentText(self._context)
        self._templating = DocumentTemplating(self._context, self._uploads)
        self.session = session
        self.storage = storage
        self.settings = self._context.settings
        self.repo = self._context.repo

    # ---- documents ------------------------------------------------------------

    def create(
        self, data: DocumentCreate, actor: Actor, *, azienda_id: UUID | None = None
    ) -> DocumentRead:
        document = self._context.create_row(data, actor, azienda_id=azienda_id)
        self.session.commit()
        return DocumentRead.model_validate(document)

    def update(self, document_id: UUID, data: DocumentUpdate, actor: Actor) -> DocumentRead:
        actor.require_write("update_document")
        document = self._context.require(document_id)
        changes = supplied_changes(data, exclude={"custom_fields"})
        reject_cleared_columns(ENTITY, Document, changes)
        if data.custom_fields is not None:
            changes["custom_fields"] = merge_custom(
                ENTITY,
                self._context.fields.specs_for(ENTITY),
                document.custom_fields,
                data.custom_fields,
            )
        for key, value in changes.items():
            setattr(document, key, value)
        self._context.activities.record(
            ENTITY, document.id, "updated", actor, {"changed": sorted(changes)}
        )
        self.session.commit()
        return DocumentRead.model_validate(document)

    def get(self, document_id: UUID, actor: Actor) -> DocumentRead:
        return DocumentRead.model_validate(self._context.require(document_id))

    def soft_delete(self, document_id: UUID, actor: Actor) -> None:
        """Sets `deleted_at`. The stored bytes are left alone -- soft delete never
        touches storage. `delete` on the storage backends is a separate, deliberate
        act (the one that had to be fixed so it could not leave a document retrievable
        after deletion); a restore that came back without the file would not be a real
        restore, so nothing here calls it."""
        actor.require_write("delete_document")
        document = self._context.require(document_id)
        document.deleted_at = datetime.now(UTC)
        self._context.activities.record(ENTITY, document.id, "deleted", actor)
        self.session.commit()

    def restore(self, document_id: UUID, actor: Actor) -> DocumentRead:
        actor.require_write("restore_document")
        document = self.repo.get(document_id, include_deleted=True)
        if document is None:
            raise NotFound(ENTITY, document_id)
        was_deleted = document.deleted_at is not None
        document.deleted_at = None
        if was_deleted:
            self._context.activities.record(ENTITY, document.id, "restored", actor)
        self.session.commit()
        return DocumentRead.model_validate(document)

    # ---- versions and bytes ---------------------------------------------------

    def add_version(
        self,
        document_id: UUID,
        data: bytes,
        content_type: str,
        actor: Actor,
        *,
        sorgente_markdown: str | None = None,
        template_id: UUID | None = None,
        variabili: dict[str, Any] | None = None,
        storage_prefix: str | None = None,
    ) -> DocumentVersionRead:
        """A new version of a document, committed. See `DocumentUploads.add_version`."""
        return self._uploads.add_version(
            document_id,
            data,
            content_type,
            actor,
            sorgente_markdown=sorgente_markdown,
            template_id=template_id,
            variabili=variabili,
            storage_prefix=storage_prefix,
        )

    def import_bytes(
        self,
        *,
        customer_id: UUID | None = None,
        deal_id: UUID | None = None,
        contract_id: UUID | None = None,
        tipo: DocumentTipo,
        titolo: str,
        data: bytes,
        content_type: str,
        actor: Actor,
        origine: dict[str, Any],
        commit: bool = True,
        azienda_id: UUID | None = None,
    ) -> DocumentRead:
        """Bytes from somewhere else, filed as a document and its first version in one
        unit of work. See `DocumentUploads.import_bytes` for what `commit=False` does
        and does not promise."""
        return self._uploads.import_bytes(
            customer_id=customer_id,
            deal_id=deal_id,
            contract_id=contract_id,
            tipo=tipo,
            titolo=titolo,
            data=data,
            content_type=content_type,
            actor=actor,
            origine=origine,
            commit=commit,
            azienda_id=azienda_id,
        )

    def versions(self, document_id: UUID, actor: Actor) -> list[DocumentVersionRead]:
        return self._uploads.versions(document_id)

    def download(
        self, document_id: UUID, numero: int | None, actor: Actor
    ) -> tuple[bytes, str, str]:
        """`(bytes, content_type, filename)`; `numero=None` is the current version."""
        return self._uploads.download(document_id, numero)

    def extract_text(
        self, document_id: UUID, numero: int | None, actor: Actor, *, max_bytes: int | None = None
    ) -> DocumentTextRead:
        """The text of an archived document. See `DocumentText.extract_text`."""
        return self._text.extract_text(document_id, numero, max_bytes=max_bytes)

    # ---- template-driven documents ------------------------------------------

    def create_from_template(self, data: DocumentFromTemplate, actor: Actor) -> DocumentRead:
        """A new document whose first version is a template rendered to PDF."""
        return self._templating.create_from_template(data, actor)

    def regenerate(self, document_id: UUID, numero: int, actor: Actor) -> DocumentVersionRead:
        """An old version rebuilt from its own template and variables, as a new version."""
        return self._templating.regenerate(document_id, numero, actor)

    # ---- offers ---------------------------------------------------------------

    def set_offer_state(self, document_id: UUID, stato: OfferState, actor: Actor) -> DocumentRead:
        """The only writer of `documents.stato`.

        Deliberately not a field on `DocumentUpdate`, and it stays that way now that
        A14 is closed and a `null` on an Update schema really does clear a column: the
        original reason was that it could not, but the better one is that
        `documents.stato` is `NULL` for everything that is not an offer, so "clear it"
        is not a state a caller should be able to ask for on an offer at all. A
        dedicated method taking a required, non-nullable literal cannot express it.
        """
        actor.require_write("set_offer_state")
        document = self._context.require(document_id)
        if document.tipo != "offerta" or document.stato is None:
            raise ValidationFailed(
                ENTITY,
                "stato",
                "solo un documento di tipo offerta ha uno stato",
                expected="un documento di tipo offerta",
            )
        allowed = OFFER_TRANSITIONS[document.stato]
        if stato not in allowed:
            raise Conflict(
                ENTITY,
                f"da '{document.stato}' non si puo' passare a '{stato}'",
                stato_attuale=document.stato,
                transizioni_ammesse=sorted(allowed),
            )
        previous, document.stato = document.stato, stato
        # The day this state began. `today_local()` and never `date.today()`: see
        # `db/clock.py`. An offer whose state was set at 00:30 CET on 1 January would
        # otherwise be reported as having been in that state since the previous year.
        document.stato_dal = today_local()

        # Slice 6 §9.3. The order of these four steps is fixed and is not cosmetic:
        #
        #   1. the mutation above,
        #   2. the runner -- which mutates the deal and records its own activity,
        #   3. this document's own activity,
        #   4. the commit.
        #
        # `ActivityService.record`'s docstring requires it to be the last thing that
        # touches the session before the caller's commit, and forbids following it with a
        # call into another service that commits on its own behalf. The runner sits before
        # it and never commits, so both halves of that contract hold -- and the automation
        # is atomic with its trigger: either the offer is accepted and the deal is moved,
        # or neither is true. `test_automation_atomicity.py` proves both directions,
        # including that a concurrent reader never sees one half without the other.
        #
        # Called explicitly, not through a hook: an implicit hook on a state change is a
        # mechanism whose call sites cannot be found by reading the code.
        AutomationRunner(self.session).on_offer_state_changed(document, previous, actor)

        self._context.activities.record(
            ENTITY, document.id, "state_changed", actor, {"da": previous, "a": stato}
        )
        self.session.commit()
        return DocumentRead.model_validate(document)

    # `list` must stay the last method defined in this class -- an unconditional
    # project rule (`test_module_imports.py`). Defining a method named `list` rebinds
    # that name in the *class* namespace, so any later method whose own return
    # annotation is a bare `list[...]` would resolve `list` to this method instead of
    # the builtin and fail at import time on Python 3.13.
    def list(self, query: DocumentListQuery, actor: Actor) -> DocumentPage:
        rows = self.repo.list(query)
        has_more = len(rows) > query.limit
        items = rows[: query.limit]
        # See `CustomerService.list` for why the cursor carries the sort value as well
        # as the id.
        spec = DOCUMENT_SORTS.resolve(query.sort)
        next_cursor = (
            encode_cursor(spec, getattr(items[-1], spec.key), items[-1].id)
            if has_more and items
            else None
        )
        return DocumentPage(
            items=[DocumentRead.model_validate(d) for d in items],
            next_cursor=next_cursor,
        )
