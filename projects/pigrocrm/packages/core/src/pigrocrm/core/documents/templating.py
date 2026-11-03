"""Documents made from a template: the first version of a new document, and a regenerated
version of an old one."""

import hashlib
from typing import Any, get_args
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.customers.schemas import CustomerRead
from pigrocrm.core.db.clock import today_local
from pigrocrm.core.documents.context import ENTITY, DocumentContext
from pigrocrm.core.documents.models import Document, DocumentVersion
from pigrocrm.core.documents.schemas import (
    DocumentFromTemplate,
    DocumentRead,
    DocumentType,
    DocumentVersionRead,
)
from pigrocrm.core.documents.uploads import DocumentUploads
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.legal_entities.assets import logo_file_in
from pigrocrm.core.render.pdf import build_header, render_pdf
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.renderer import render_template

# The template types a `documents` row can legitimately be created from. Derived from
# `DocumentType` itself, never retyped, so a value added there is covered here by
# construction -- see `require_template` for why the two vocabularies stopped being
# the same Literal.
DOCUMENT_TIPI: frozenset[str] = frozenset(get_args(DocumentType))


class DocumentTemplating:
    def __init__(self, context: DocumentContext, uploads: DocumentUploads) -> None:
        self.ctx = context
        self.uploads = uploads

    def require_template(self, template_id: UUID) -> Template:
        """`TemplateService` exposes no `_require` of its own -- only `get`, which
        returns a `TemplateRead` (a detached copy with `declared_variables` already
        turned into `TemplateVariable` objects). This needs the live ORM row instead:
        `declared_variables` reads `template.declared_variables` as the raw list of
        dicts JSONB actually stores, and `template.body_markdown`/`template.type`/
        `template.nome` feed straight into rendering. Going through
        `templates.repo.get` directly is what `TemplateService.declared_variables`
        itself documents as the expected call shape for a `Template` already in hand.
        """
        template = self.ctx.templates.repo.get(template_id)
        if template is None:
            raise NotFound("template", template_id)
        if template.type not in DOCUMENT_TIPI:
            # `TemplateType` and `DocumentType` used to be the same Literal. Slice 5
            # widened the template side with `email` and `payment_reminder` -- neither of which
            # is a thing a `documents` row can be -- and `DocumentRead.type` is a plain
            # `str`, so without this check a document of type "sollecito" would land in
            # the customer's document list. The conversion between the two vocabularies
            # is explicit here rather than implicit in the overlap.
            raise ValidationFailed(
                ENTITY,
                "template_id",
                f"il template '{template.nome}' è di tipo '{template.type}', "
                "che non è un tipo di documento",
                expected=f"un template fra: {', '.join(sorted(DOCUMENT_TIPI))}",
            )
        return template

    def template_scope(
        self,
        customer: Customer | None,
        legal_entity_id: UUID,
        variables: dict[str, Any],
        actor: Actor,
    ) -> dict[str, Any]:
        """What a template can read: the caller's variables, plus `customer` and
        `emittente` from the record itself.

        The caller's own keys go in first and the record's go in second, so a caller
        cannot shadow `customer` or `emittente` with values of their own -- an offer
        must state the customer the document is filed under, not the one whoever
        pressed the button typed.
        """
        scope: dict[str, Any] = dict(variables)
        # The document's own azienda (REB-627, spec §1.8), read live: `regenerate` re-reads
        # it the way it re-read the one emitter before, so a corrected address reaches
        # the next version and a customer moved since does not.
        scope["emittente"] = self.ctx.legal_entity.as_template_values(actor, legal_entity_id)[
            "emittente"
        ]
        scope["cliente"] = (
            CustomerRead.model_validate(customer).model_dump(mode="json") if customer else {}
        )
        # `today_local()`, never a bare `date.today()`: see `db/clock.py`. `{{oggi}}` is
        # the date a template prints on the document itself. On a host that is not running
        # in Europe/Rome (the API image runs in UTC) every render between midnight and
        # 01:00 CET would date the document to the previous day, and on the night of
        # 31 December to the previous *year*; the date is frozen into an immutable
        # version and `regenerate` reproduces it from that snapshot, so nothing later
        # corrects it.
        scope.setdefault("oggi", today_local().isoformat())
        return scope

    def render_to_pdf(
        self, template: Template, scope: dict[str, Any], *, legal_entity_id: UUID
    ) -> tuple[str, bytes]:
        """Everything the render reads from the database is read here, then the read
        transaction is ended, and only then are Pandoc and Typst run: a compile takes
        seconds and must not hold a pooled connection while it does.

        **This commits the session.** The commit writes nothing of its own, but it ends the
        caller's transaction: anything the caller has pending when it calls
        `create_from_template` or `regenerate` is committed here, before the render, and
        is no longer undone if the render fails. A caller that needs such a write to
        survive deliberately commits it itself first (`ContractRegisterService` does, for
        the template it seeds).
        """
        markdown = render_template(
            template.body_markdown, scope, self.ctx.templates.declared_variables(template)
        )
        media = self.ctx.assets.media_for(legal_entity_id)
        header = build_header(scope["emittente"], logo=logo_file_in(media))
        self.ctx.session.commit()
        return markdown, render_pdf(
            markdown, header_typst=header, settings=self.ctx.settings, media=media
        )

    def create_from_template(self, data: DocumentFromTemplate, actor: Actor) -> DocumentRead:
        """The call behind "Claude, prepara una nuova offerta usando il template
        Consulenza CTO" (spec 7) and behind the UI's Genera button. One transaction:
        the document, its first version and the timeline entry commit together. The
        render comes first and writes nothing, so a render failure leaves nothing
        behind. It commits the caller's pending writes before the render: see
        `render_to_pdf`."""
        actor.require_write("create_document_from_template")
        template = self.require_template(data.template_id)
        owner = self.ctx.check_owner(data.customer_id, data.deal_id)
        legal_entity_id = (
            self.ctx.legal_entity.inherited(data.legal_entity_id, ENTITY)
            if data.legal_entity_id is not None
            else self.ctx.legal_entity_of(owner)
        )
        custom_fields = self.ctx.validated_custom(data.custom_fields or {})
        scope = self.template_scope(
            self.ctx.customer_for(data.customer_id, data.deal_id, None),
            legal_entity_id,
            data.variables,
            actor,
        )
        markdown, pdf = self.render_to_pdf(template, scope, legal_entity_id=legal_entity_id)

        document = self.ctx.repo.add(
            Document(
                customer_id=data.customer_id,
                deal_id=data.deal_id,
                legal_entity_id=legal_entity_id,
                type=template.type,
                title=data.title,
                status="draft" if template.type == "offer" else None,
                custom_fields=custom_fields,
            )
        )
        key = self.ctx.storage_key_for(document, 1, "application/pdf")
        version = DocumentVersion(
            document_id=document.id,
            number=1,
            markdown_source=markdown,
            template_id=template.id,
            variables=data.variables,
            storage_key=key,
            content_type="application/pdf",
            size=len(pdf),
            hash_sha256=hashlib.sha256(pdf).hexdigest(),
            created_by=actor.id,
        )
        # Same ordering `add_version` establishes and the same reason: the version
        # row is flushed -- and Postgres's own `uq_document_versions_document_number`
        # given a chance to reject it -- *before* a byte reaches `storage.put`, so a
        # losing racer's bytes can never physically overwrite a winner's.
        try:
            self.ctx.repo.add_version(version)
        except IntegrityError as exc:
            self.ctx.session.rollback()
            raise Conflict(
                "document_version",
                "conflitto di concorrenza sul numero di versione, riprova",
                document_id=str(document.id),
            ) from exc

        try:
            self.ctx.storage.put(key, pdf, "application/pdf")
        except Exception:
            # The row is flushed but not committed: rolling back here undoes that
            # insert (and the document's own flushed insert) too, so nothing is left
            # pointing at bytes that were never written.
            self.ctx.session.rollback()
            raise

        document.current_version = 1
        self.ctx.activities.record(
            ENTITY, document.id, "created_from_template", actor, {"template": template.nome}
        )
        self.ctx.session.commit()
        return DocumentRead.model_validate(document)

    def regenerate(self, document_id: UUID, number: int, actor: Actor) -> DocumentVersionRead:
        """Rebuild an old version as a new one.

        Reads the version's own `template_id` and `variables` -- which is exactly why
        both are stored (spec 4.2) -- so a six-month-old offer regenerates identically
        without the person who wrote it being in the room.
        """
        actor.require_write("regenerate_document")
        document = self.ctx.require(document_id)
        source = self.ctx.repo.version(document_id, number)
        if source is None:
            raise NotFound("document_version", f"{document_id}#{number}")
        if source.template_id is None or source.variables is None:
            raise ValidationFailed(
                ENTITY,
                "number",
                "questa versione non e' stata generata da un template e non si rigenera",
                expected="una versione creata da un template",
            )
        template = self.require_template(source.template_id)
        scope = self.template_scope(
            self.ctx.customer_of(document), document.legal_entity_id, source.variables, actor
        )
        markdown, pdf = self.render_to_pdf(
            template, scope, legal_entity_id=document.legal_entity_id
        )
        return self.uploads.add_version(
            document_id,
            pdf,
            "application/pdf",
            actor,
            markdown_source=markdown,
            template_id=template.id,
            variables=source.variables,
        )
