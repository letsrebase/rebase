from typing import Annotated, Any
from uuid import UUID

from pydantic import Field, TypeAdapter

from pigrocrm.core.documents.schemas import DocumentFromTemplate, DocumentListQuery
from pigrocrm.core.documents.service import DocumentService
from pigrocrm.core.templates.schemas import TemplateListQuery
from pigrocrm.core.templates.service import TemplateService
from pigrocrm_mcp.context import McpContext

# A version number arrives as `int | str` (see `tools/params.py`'s note on why every
# scalar tool parameter has to stay permissive at the SDK boundary) and is validated
# here, inside the guarded call, where a bad value becomes rendered guidance instead of
# the SDK's own raw dump. Through pydantic rather than a bare `int(...)`: `int("prima")`
# raises a `ValueError` the guard renders with `_malformed_identifier_error`'s text --
# "non è un identificativo valido", true of a UUID and nonsense about a version number --
# whereas pydantic's `int_parsing` already has its own translation in `errors.py`.
_NUMERO: TypeAdapter[int] = TypeAdapter(Annotated[int, Field(ge=1)])


def _documents(context: McpContext) -> DocumentService:
    return DocumentService(context.session, context.storage)


def search(context: McpContext, query: DocumentListQuery) -> dict[str, Any]:
    page = _documents(context).list(query, context.actor)
    return {
        "items": [item.model_dump(mode="json") for item in page.items],
        # Already a string since slice 6 (an opaque `(sort value, id)` cursor), so no
        # `str()`: keeping one here would suggest the value is still a UUID being
        # rendered, which is exactly the assumption this change removes.
        "next_cursor": page.next_cursor,
    }


def get(context: McpContext, document_id: str) -> dict[str, Any]:
    return _documents(context).get(UUID(document_id), context.actor).model_dump(mode="json")


def create_from_template(context: McpContext, data: dict[str, Any]) -> dict[str, Any]:
    result = _documents(context).create_from_template(DocumentFromTemplate(**data), context.actor)
    return result.model_dump(mode="json")


def set_state(context: McpContext, document_id: str, stato: str) -> dict[str, Any]:
    result = _documents(context).set_offer_state(UUID(document_id), stato, context.actor)  # type: ignore[arg-type]
    return result.model_dump(mode="json")


def versions(context: McpContext, document_id: str) -> dict[str, Any]:
    entries = _documents(context).versions(UUID(document_id), context.actor)
    return {"versions": [entry.model_dump(mode="json") for entry in entries]}


def list_templates(context: McpContext, include_archived: bool) -> dict[str, Any]:
    """`TemplateService.list` takes a `TemplateListQuery` and returns a paginated
    `TemplatePage` -- not `(actor, include_archived=...)` returning a plain list.
    The plan this tool was drafted from predates that signature; `include_archived`
    (the tool's own public parameter name, unchanged, since it is what an agent
    already sees in the registered schema) maps onto the service's own
    `include_inactive` field."""
    page = TemplateService(context.session).list(
        TemplateListQuery(include_inactive=include_archived), context.actor
    )
    return {
        "templates": [
            {"id": str(t.id), "nome": t.nome, "tipo": t.tipo, "attivo": t.attivo}
            for t in page.items
        ],
        # `TemplatePage.next_cursor` is still a `UUID`: `templates` has no sort
        # whitelist and still pages by id alone, unlike the four entities R9 covers.
        "next_cursor": str(page.next_cursor) if page.next_cursor else None,
    }


def describe_template(context: McpContext, template_id: str) -> dict[str, Any]:
    described = TemplateService(context.session).describe(UUID(template_id), context.actor)
    return described.model_dump(mode="json")


def preview_template(
    context: McpContext, template_id: str, variabili: dict[str, Any]
) -> dict[str, Any]:
    """Markdown, not a document and not bytes: `preview` renders and returns, storing
    nothing. It is the read `describe_template` leads to -- what the values an agent is
    about to pass would actually produce -- so it is exposed for the same reason
    `describe_template` is, and returns a string because that is all `TemplateService.
    preview` has to give."""
    markdown = TemplateService(context.session).preview(UUID(template_id), variabili, context.actor)
    return {"template_id": template_id, "markdown": markdown}


def extract_text(
    context: McpContext, document_id: str, numero: int | str | None = None
) -> dict[str, Any]:
    """The text of an archived file. Text and not bytes, which is why it may exist at
    all under the "MCP returns identifiers, never files" rule this module's registration
    block states: a base64 PDF in a model's context is waste, and the codice
    destinatario printed on page one of that same PDF is the answer somebody asked for.

    `numero` stays optional and defaults, in the service, to the current version -- an
    agent that has just read `get_document` holds one id and no version number, and
    that is the ordinary case."""
    result = _documents(context).extract_text(
        UUID(document_id),
        _NUMERO.validate_python(numero) if numero is not None else None,
        context.actor,
    )
    return result.model_dump(mode="json")


def regenerate_version(context: McpContext, document_id: str, numero: int | str) -> dict[str, Any]:
    version = _documents(context).regenerate(
        UUID(document_id), _NUMERO.validate_python(numero), context.actor
    )
    return version.model_dump(mode="json")


def archive(context: McpContext, document_id: str) -> dict[str, str]:
    _documents(context).soft_delete(UUID(document_id), context.actor)
    return {"status": "archiviato", "document_id": document_id}


def restore(context: McpContext, document_id: str) -> dict[str, Any]:
    return _documents(context).restore(UUID(document_id), context.actor).model_dump(mode="json")
