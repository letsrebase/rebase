from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile

from pigrocrm.core.invoices.import_confirm import InvoiceConfirmRequest, InvoiceConfirmResult
from pigrocrm.core.invoices.import_review import InvoiceReviewRequest, InvoiceReviewResult
from pigrocrm.core.invoices.import_upload import (
    UPLOAD_MAX_TOTAL_BYTES,
    UploadedInvoiceConfirmResult,
    UploadedInvoiceReviewResult,
    parse_decisions,
)
from pigrocrm.core.invoices.importing import CONFIRM_ACTION, REVIEW_ACTION
from pigrocrm.core.invoices.schemas import (
    MAX_LINES,
    ArtifactKind,
    InvoiceAnnul,
    InvoiceArtifact,
    InvoiceCreate,
    InvoiceImport,
    InvoiceIssue,
    InvoiceLineIn,
    InvoiceLineRead,
    InvoiceListQuery,
    InvoicePage,
    InvoiceRead,
    InvoiceStatus,
    InvoiceTransmitted,
    InvoiceType,
    InvoiceUpdate,
    PaymentState,
    PaymentStatus,
    RegisterGapRead,
    RegisterGapsDeclare,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.query_params import LEGAL_ENTITY_QUERY_DESCRIPTION, KeysetParams
from pigrocrm_api.timeline import timeline_router

router = APIRouter(
    prefix="/api/invoices",
    tags=["invoices"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)

YEAR_MIN = 2000
YEAR_MAX = 2999


class InvoiceLinesBody(BaseModel):
    """The whole list, never a partial patch.

    A body object rather than a bare array so the endpoint can grow a sibling field
    later without becoming a different shape, and so the `max_length` bound lives in
    one place the OpenAPI document also shows.
    """

    lines: list[InvoiceLineIn] = Field(default_factory=list, max_length=MAX_LINES)


def _service(session: SessionDep, storage: StorageDep, settings: SettingsDep) -> InvoiceService:
    return InvoiceService(session, storage, settings)


class InvoiceListParams(KeysetParams):
    customer_id: UUID | None = None
    deal_id: UUID | None = None
    legal_entity_id: UUID | None = Field(default=None, description=LEGAL_ENTITY_QUERY_DESCRIPTION)
    type: InvoiceType | None = None
    status: InvoiceStatus | None = None
    year: int | None = Field(default=None, ge=YEAR_MIN, le=YEAR_MAX)
    payment_status: PaymentStatus | None = None
    # The drill-through of the operational dashboard's "scaduto e non incassato" card
    # (§6.2). The card links here and nowhere else, and the rows returned are counted by
    # the same predicate function the card's `COUNT` uses.
    overdue: bool = False
    # The fattura a consumed proforma was issued as: the proforma's page asks for the
    # one row whose `origin_proforma_id` is its own id (ORB-134).
    origin_proforma_id: UUID | None = None
    # Leave out the proformas that became a fattura: the list under «Tutte» otherwise
    # shows each issued proforma twice, once as itself and once as its number (ORB-169).
    exclude_consumed: bool = False


@router.get("", response_model=InvoicePage)
def list_invoices(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    params: Annotated[InvoiceListParams, Query()],
) -> InvoicePage:
    return _service(session, storage, settings).list(params.to_query(InvoiceListQuery), actor)


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create(
    data: InvoiceCreate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).create(data, actor)


class InvoiceImportResult(BaseModel):
    """The imported invoice alongside the gaps still waiting for a declaration -- the
    operator sees both in one round trip instead of importing blind and querying the
    register separately after every row."""

    invoice: InvoiceRead
    undeclared_gaps: list[int]


# The four routes below must stay ahead of every `/{invoice_id}` route in this file:
# `import` and `register` are literal path segments, and FastAPI matches routes in
# declaration order, so a `/{invoice_id}` route declared first would swallow them and
# try (and fail) to parse `"import"`/`"register"` as a UUID.
@router.post("/import", response_model=InvoiceImportResult, status_code=status.HTTP_201_CREATED)
def import_issued(
    data: InvoiceImport,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    legal_entity_id: Annotated[UUID | None, Query()] = None,
) -> InvoiceImportResult:
    """Slice 9 §3: an invoice issued by the previous system. Admin only, enforced by the
    service. No artefacts are produced: the PDF, if any, is the original. On the
    register of `legal_entity_id`, the default legal entity when omitted (REB-620)."""
    service = _service(session, storage, settings)
    invoice = service.import_issued(data, actor, legal_entity_id=legal_entity_id)
    return InvoiceImportResult(
        invoice=invoice, undeclared_gaps=service.undeclared_gaps(data.year, legal_entity_id)
    )


@router.post("/import/review", response_model=InvoiceReviewResult)
def review_import(
    data: InvoiceReviewRequest,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceReviewResult:
    """REB-365: read-only, admin only, enforced by the service. Reviews one or more
    already-archived documents and reports one row per invoice they parse into --
    never writes a row. A `"ready"` row whose customer resolves to exactly one
    contract with a day-rate card in force on the invoice's own `issue_date`
    also carries `days_mappings` (REB-369): a proposed link, per line, to that
    contract's recorded, unbilled `work_units`."""
    lines = _service(session, storage, settings).review_import(data.document_ids, actor)
    return InvoiceReviewResult(lines=lines)


@router.post("/import/confirm", response_model=InvoiceConfirmResult)
def confirm_import(
    data: InvoiceConfirmRequest,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceConfirmResult:
    """REB-366: admin only, enforced by the service. Re-reads and re-parses the
    document's own stored bytes -- never trusts an earlier `/import/review` call
    -- and writes the register through `import_issued` itself for every invoice
    that classifies `"ready"`: never a second, independently-maintained write
    path. `create_organization` (REB-367) creates the matched party as a new
    `Organization` inside that same write when no `customer_id` is given and no
    exact tax-id match exists."""
    lines = _service(session, storage, settings).confirm_import(
        data.document_id,
        actor,
        customer_id=data.customer_id,
        create_organization=data.create_organization,
    )
    return InvoiceConfirmResult(lines=lines)


# The two upload routes read the multipart body themselves (`await request.form()`), after
# the admin check, instead of declaring `File()`/`Form()` parameters: FastAPI parses a
# declared body before it calls the handler, and Starlette spools every file part to disk
# while it does, so a collaboratore would have had up to the proxy's limit written to disk
# before a 403. The schema is stated by hand for the OpenAPI document and the docs page.
_UPLOAD_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": {
                        "type": "array",
                        "items": {"type": "string", "format": "binary"},
                        "description": "FatturaPA XML files or ZIPs of them, one part each.",
                    },
                    "decisioni": {
                        "type": "string",
                        "description": (
                            'On the confirmation: `{"clienti": {"<party_key>": '
                            '{"create_organization": true}}}`.'
                        ),
                    },
                },
            }
        }
    },
}


async def _uploaded(request: Request) -> tuple[list[tuple[str, bytes]], str]:
    """The files of the multipart body as `(name, bytes)`, and the `decisioni` field. Each
    file is read through the whole request's ceiling plus one byte, so a body larger than
    the service will take is refused by `expand_uploads` with its sentence instead of being
    held in memory first; the service opens the ZIPs, after the admin check."""
    form = await request.form()
    files = [
        (part.filename or "senza-nome", await part.read(UPLOAD_MAX_TOTAL_BYTES + 1))
        for part in form.getlist("file")
        if isinstance(part, StarletteUploadFile)
    ]
    decisions = form.get("decisioni")
    return files, decisions if isinstance(decisions, str) else "{}"


@router.post(
    "/import/upload/review",
    response_model=UploadedInvoiceReviewResult,
    openapi_extra={"requestBody": _UPLOAD_BODY},
)
async def review_import_upload(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> UploadedInvoiceReviewResult:
    """REB-758: `/import/review` for the page, which holds bytes and not `document_id`s.
    The multipart field `file` is repeated, one per FatturaPA XML file or ZIP of them (a
    ZIP is opened, one level deep). Read-only and admin only, and nothing is stored: a
    row names its file and the file's `sha256`, and its customer by a `party_key`, which
    is the key of that customer's decision in `/import/upload/confirm`."""
    actor.require_admin(REVIEW_ACTION)
    files, _ = await _uploaded(request)
    lines = await run_in_threadpool(
        _service(session, storage, settings).review_upload, files, actor
    )
    return UploadedInvoiceReviewResult(lines=lines)


@router.post(
    "/import/upload/confirm",
    response_model=UploadedInvoiceConfirmResult,
    openapi_extra={"requestBody": _UPLOAD_BODY},
)
async def confirm_import_upload(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> UploadedInvoiceConfirmResult:
    """REB-758: `/import/confirm` for the page. The same bytes as the review, sent again:
    each file is parsed and classified from its own bytes against the register as it is
    now, never from the review, and written by the same code `/import/confirm` runs. A
    single-invoice file's XML is archived as a document of the invoice's customer.
    `decisioni` is the JSON `{"clienti": {"<party_key>": {"create_organization": true}}}`:
    per customer, a `customer_id` to attach or `create_organization`, as on
    `/import/confirm`."""
    actor.require_admin(CONFIRM_ACTION)
    files, decisions = await _uploaded(request)
    lines = await run_in_threadpool(
        _service(session, storage, settings).confirm_upload,
        files,
        parse_decisions(decisions),
        actor,
    )
    return UploadedInvoiceConfirmResult(lines=lines)


# The register is per legal entity (REB-619): both routes take the legal entity as a query
# parameter and read the default when it is omitted, so a space with one legal entity calls
# them exactly as before.
@router.get("/register/{year}/gaps", response_model=list[RegisterGapRead])
def register_gaps(
    year: Annotated[int, Path(ge=YEAR_MIN, le=YEAR_MAX)],
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    legal_entity_id: Annotated[UUID | None, Query()] = None,
) -> list[RegisterGapRead]:
    return _service(session, storage, settings).register_gaps(year, actor, legal_entity_id)


@router.post("/register/{year}/gaps", response_model=list[RegisterGapRead])
def declare_register_gaps(
    year: Annotated[int, Path(ge=YEAR_MIN, le=YEAR_MAX)],
    data: RegisterGapsDeclare,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    legal_entity_id: Annotated[UUID | None, Query()] = None,
) -> list[RegisterGapRead]:
    return _service(session, storage, settings).declare_gaps(year, data, actor, legal_entity_id)


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).get(invoice_id, actor)


@router.patch("/{invoice_id}", response_model=InvoiceRead)
def update(
    invoice_id: UUID,
    data: InvoiceUpdate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).update(invoice_id, data, actor)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> None:
    """Refused with 409 once a number has been consumed. There is no physical delete
    anywhere, and the `CHECK` on the table refuses the soft one for a numbered row even
    in raw SQL."""
    _service(session, storage, settings).soft_delete(invoice_id, actor)


@router.get("/{invoice_id}/lines", response_model=list[InvoiceLineRead])
def lines(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> list[InvoiceLineRead]:
    return _service(session, storage, settings).lines(invoice_id, actor)


@router.put("/{invoice_id}/lines", response_model=InvoiceRead)
def replace_lines(
    invoice_id: UUID,
    body: InvoiceLinesBody,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    """`PUT`, not `PATCH`: the whole list is replaced. It is the natural shape of a line
    editor, and it is what makes clearing an optional numeric column possible at all
    (A14)."""
    return _service(session, storage, settings).replace_lines(invoice_id, body.lines, actor)


@router.post("/{invoice_id}/confirm", response_model=InvoiceRead)
def confirm(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).confirm_proforma(invoice_id, actor)


@router.post("/{invoice_id}/issue", response_model=InvoiceRead)
def issue(
    invoice_id: UUID,
    data: InvoiceIssue,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    """Admin only, enforced by the service. `{invoice_id}` is the *source* row: a
    `draft` invoice is issued in place, and a `confirmed` proforma produces a new
    numbered row linked back to it.

    `issue()` no longer produces the PDF/XML itself -- that used to be a second
    transaction nested inside the first, and its commit survived the test fixture's
    own rollback, leaking issued invoices across tests. `issue` now returns after its
    own single commit, and this is the caller `service.py` documents as owning the
    second transaction: `produce_artifacts` is called here, right after, so a caller
    of this endpoint never has to make a separate call to see the PDF/XML.
    """
    return _service(session, storage, settings).issue_and_render(invoice_id, data, actor)


@router.post("/{invoice_id}/annul", response_model=InvoiceRead)
def annul(
    invoice_id: UUID,
    data: InvoiceAnnul,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).annul(invoice_id, data, actor)


@router.post("/{invoice_id}/transmitted", response_model=InvoiceRead)
def transmitted(
    invoice_id: UUID,
    data: InvoiceTransmitted,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).mark_transmitted_externally(invoice_id, data, actor)


@router.patch("/{invoice_id}/payment", response_model=InvoiceRead)
def payment(
    invoice_id: UUID,
    data: PaymentState,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> InvoiceRead:
    return _service(session, storage, settings).set_payment_state(invoice_id, data, actor)


@router.post("/{invoice_id}/artifacts", response_model=list[InvoiceArtifact])
def artifacts(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> list[InvoiceArtifact]:
    """Regenerate -- or verify -- the PDF and the XML. Idempotent: an unchanged
    artefact is returned rather than rewritten, a lost file is repaired with an
    identical version, and a divergence is a 409."""
    return _service(session, storage, settings).produce_artifacts(invoice_id, actor)


def _download(
    service: InvoiceService, invoice_id: UUID, kind: ArtifactKind, actor: ActorDep
) -> Response:
    data, content_type, filename = service.download(invoice_id, kind, actor)
    return attachment_response(data, content_type, filename)


@router.get("/{invoice_id}/pdf")
def download_pdf(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> Response:
    return _download(_service(session, storage, settings), invoice_id, "pdf", actor)


@router.get("/{invoice_id}/xml")
def download_xml(
    invoice_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> Response:
    """The download always goes through the API: it is the only place authorisation
    exists, on both storage backends."""
    return _download(_service(session, storage, settings), invoice_id, "xml", actor)


router.include_router(timeline_router("invoice", id_param="invoice_id"))
