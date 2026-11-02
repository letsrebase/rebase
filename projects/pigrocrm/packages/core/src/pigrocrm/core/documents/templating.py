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
    DocumentTipo,
    DocumentVersionRead,
)
from pigrocrm.core.documents.uploads import DocumentUploads
from pigrocrm.core.emitter.assets import logo_file_in
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.render.pdf import build_header, render_pdf
from pigrocrm.core.templates.models import Template
from pigrocrm.core.templates.renderer import render_template

# The template types a `documents` row can legitimately be created from. Derived from
# `DocumentTipo` itself, never retyped, so a value added there is covered here by
# construction -- see `require_template` for why the two vocabularies stopped being
# the same Literal.
DOCUMENT_TIPI: frozenset[str] = frozenset(get_args(DocumentTipo))


class DocumentTemplating:
    def __init__(self, context: DocumentContext, uploads: DocumentUploads) -> None:
        self.ctx = context
        self.uploads = uploads

    def require_template(self, template_id: UUID) -> Template:
        """`TemplateService` exposes no `_require` of its own -- only `get`, which
        returns a `TemplateRead` (a detached copy with `variabili_dichiarate` already
        turned into `TemplateVariable` objects). This needs the live ORM row instead:
        `declared_variables` reads `template.variabili_dichiarate` as the raw list of
        dicts JSONB actually stores, and `template.corpo_markdown`/`template.tipo`/
        `template.nome` feed straight into rendering. Going through
        `templates.repo.get` directly is what `TemplateService.declared_variables`
        itself documents as the expected call shape for a `Template` already in hand.
        """
        template = self.ctx.templates.repo.get(template_id)
        if template is None:
            raise NotFound("template", template_id)
        if template.tipo not in DOCUMENT_TIPI:
            # `TemplateTipo` and `DocumentTipo` used to be the same Literal. Slice 5
            # widened the template side with `email` and `sollecito` -- neither of which
            # is a thing a `documents` row can be -- and `DocumentRead.tipo` is a plain
            # `str`, so without this check a document of type "sollecito" would land in
            # the customer's document list. The conversion between the two vocabularies
            # is explicit here rather than implicit in the overlap.
            raise ValidationFailed(
                ENTITY,
                "template_id",
                f"il template '{template.nome}' è di tipo '{template.tipo}', "
                "che non è un tipo di documento",
                expected=f"un template fra: {', '.join(sorted(DOCUMENT_TIPI))}",
            )
        return template

    def template_scope(
        self,
        customer: Customer | None,
        azienda_id: UUID,
        variabili: dict[str, Any],
        actor: Actor,
    ) -> dict[str, Any]:
        """What a template can read: the caller's variables, plus `cliente` and
        `emittente` from the record itself.

        The caller's own keys go in first and the record's go in second, so a caller
        cannot shadow `cliente` or `emittente` with values of their own -- an offer
        must state the customer the document is filed under, not the one whoever
        pressed the button typed.
        """
        scope: dict[str, Any] = dict(variabili)
        # The document's own azienda (REB-627, spec §1.8), read live: `regenerate` re-reads
        # it the way it re-read the one emitter before, so a corrected address reaches
        # the next version and a customer moved since does not.
        scope["emittente"] = self.ctx.emitter.as_template_values(actor, azienda_id)["emittente"]
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
        self, template: Template, scope: dict[str, Any], *, azienda_id: UUID
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
            template.corpo_markdown, scope, self.ctx.templates.declared_variables(template)
        )
        media = self.ctx.assets.media_for(azienda_id)
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
        azienda_id = (
            self.ctx.emitter.inherited(data.azienda_id, ENTITY)
            if data.azienda_id is not None
            else self.ctx.azienda_of(owner)
        )
        custom_fields = self.ctx.validated_custom(data.custom_fields or {})
        scope = self.template_scope(
            self.ctx.customer_for(data.customer_id, data.deal_id, None),
            azienda_id,
            data.variabili,
            actor,
        )
        markdown, pdf = self.render_to_pdf(template, scope, azienda_id=azienda_id)

        document = self.ctx.repo.add(
            Document(
                customer_id=data.customer_id,
                deal_id=data.deal_id,
                azienda_id=azienda_id,
                tipo=template.tipo,
                titolo=data.titolo,
                stato="bozza" if template.tipo == "offerta" else None,
                custom_fields=custom_fields,
            )
        )
        key = self.ctx.storage_key_for(document, 1, "application/pdf")
        version = DocumentVersion(
            document_id=document.id,
            numero=1,
            sorgente_markdown=markdown,
            template_id=template.id,
            variabili=data.variabili,
            storage_key=key,
            content_type="application/pdf",
            dimensione=len(pdf),
            hash_sha256=hashlib.sha256(pdf).hexdigest(),
            creato_da=actor.id,
        )
        # Same ordering `add_version` establishes and the same reason: the version
        # row is flushed -- and Postgres's own `uq_document_versions_document_numero`
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

        document.versione_corrente = 1
        self.ctx.activities.record(
            ENTITY, document.id, "created_from_template", actor, {"template": template.nome}
        )
        self.ctx.session.commit()
        return DocumentRead.model_validate(document)

    def regenerate(self, document_id: UUID, numero: int, actor: Actor) -> DocumentVersionRead:
        """Rebuild an old version as a new one.

        Reads the version's own `template_id` and `variabili` -- which is exactly why
        both are stored (spec 4.2) -- so a six-month-old offer regenerates identically
        without the person who wrote it being in the room.
        """
        actor.require_write("regenerate_document")
        document = self.ctx.require(document_id)
        source = self.ctx.repo.version(document_id, numero)
        if source is None:
            raise NotFound("document_version", f"{document_id}#{numero}")
        if source.template_id is None or source.variabili is None:
            raise ValidationFailed(
                ENTITY,
                "numero",
                "questa versione non e' stata generata da un template e non si rigenera",
                expected="una versione creata da un template",
            )
        template = self.require_template(source.template_id)
        scope = self.template_scope(
            self.ctx.customer_of(document), document.azienda_id, source.variabili, actor
        )
        markdown, pdf = self.render_to_pdf(template, scope, azienda_id=document.azienda_id)
        return self.uploads.add_version(
            document_id,
            pdf,
            "application/pdf",
            actor,
            sorgente_markdown=markdown,
            template_id=template.id,
            variabili=source.variabili,
        )
