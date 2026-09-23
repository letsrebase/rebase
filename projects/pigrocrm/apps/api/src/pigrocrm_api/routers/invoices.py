import logging
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from pigrocrm.core.activities.schemas import ActivityRead
from pigrocrm.core.activities.service import ActivityService
from pigrocrm.core.invoices.import_confirm import InvoiceConfirmRequest, InvoiceConfirmResult
from pigrocrm.core.invoices.import_review import InvoiceReviewRequest, InvoiceReviewResult
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
    InvoiceStato,
    InvoiceTipo,
    InvoiceTransmitted,
    InvoiceUpdate,
    PaymentState,
    RegisterGapRead,
    RegisterGapsDeclare,
    StatoPagamento,
)
from pigrocrm.core.invoices.service import InvoiceService
from pigrocrm_api.deps import ActorDep, SessionDep, SettingsDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/invoices", tags=["invoices"], responses=PROBLEM_RESPONSES)

logger = logging.getLogger(__name__)

ANNO_MIN = 2000
ANNO_MAX = 2999


class InvoiceLinesBody(BaseModel):
    """The whole list, never a partial patch.

    A body object rather than a bare array so the endpoint can grow a sibling field
    later without becoming a different shape, and so the `max_length` bound lives in
    one place the OpenAPI document also shows.
    """

    righe: list[InvoiceLineIn] = Field(default_factory=list, max_length=MAX_LINES)


def _service(session: SessionDep, storage: StorageDep, settings: SettingsDep) -> InvoiceService:
    return InvoiceService(session, storage, settings)


@router.get("", response_model=InvoicePage)
def list_invoices(
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
    customer_id: Annotated[UUID | None, Query()] = None,
    deal_id: Annotated[UUID | None, Query()] = None,
    tipo: Annotated[InvoiceTipo | None, Query()] = None,
    stato: Annotated[InvoiceStato | None, Query()] = None,
    anno: Annotated[int | None, Query(ge=ANNO_MIN, le=ANNO_MAX)] = None,
    stato_pagamento: Annotated[StatoPagamento | None, Query()] = None,
    # The drill-through of the operational dashboard's "scaduto e non incassato" card
    # (§6.2). The card links here and nowhere else, and the rows returned are counted by
    # the same predicate function the card's `COUNT` uses.
    scadute: Annotated[bool, Query()] = False,
    # The fattura a consumed proforma was issued as: the proforma's page asks for the
    # one row whose `origine_proforma_id` is its own id (ORB-134).
    origine_proforma_id: Annotated[UUID | None, Query()] = None,
    # Leave out the proformas that became a fattura: the list under «Tutte» otherwise
    # shows each issued proforma twice, once as itself and once as its number (ORB-169).
    escludi_consumate: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> InvoicePage:
    query = InvoiceListQuery(
        customer_id=customer_id,
        deal_id=deal_id,
        tipo=tipo,
        stato=stato,
        anno=anno,
        stato_pagamento=stato_pagamento,
        scadute=scadute,
        origine_proforma_id=origine_proforma_id,
        escludi_consumate=escludi_consumate,
        limit=limit,
        cursor=cursor,
    )
    return _service(session, storage, settings).list(query, actor)


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
    """The imported fattura alongside the gaps still waiting for a declaration -- the
    operator sees both in one round trip instead of importing blind and querying the
    register separately after every row."""

    fattura: InvoiceRead
    buchi_non_dichiarati: list[int]


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
) -> InvoiceImportResult:
    """Slice 9 §3: a fattura issued by the previous system. Admin only, enforced by the
    service. No artefacts are produced: the PDF, if any, is the original."""
    service = _service(session, storage, settings)
    fattura = service.import_issued(data, actor)
    return InvoiceImportResult(
        fattura=fattura, buchi_non_dichiarati=service.undeclared_gaps(data.anno)
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
    never writes a row."""
    righe = _service(session, storage, settings).review_import(data.document_ids, actor)
    return InvoiceReviewResult(righe=righe)


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
    path."""
    righe = _service(session, storage, settings).confirm_import(
        data.document_id, actor, customer_id=data.customer_id
    )
    return InvoiceConfirmResult(righe=righe)


@router.get("/register/{anno}/gaps", response_model=list[RegisterGapRead])
def register_gaps(
    anno: int, session: SessionDep, storage: StorageDep, settings: SettingsDep, actor: ActorDep
) -> list[RegisterGapRead]:
    return _service(session, storage, settings).register_gaps(anno, actor)


@router.post("/register/{anno}/gaps", response_model=list[RegisterGapRead])
def declare_register_gaps(
    anno: int,
    data: RegisterGapsDeclare,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> list[RegisterGapRead]:
    return _service(session, storage, settings).declare_gaps(anno, data, actor)


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
    return _service(session, storage, settings).replace_lines(invoice_id, body.righe, actor)


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
    `bozza` fattura is issued in place, and a `confermata` proforma produces a new
    numbered row linked back to it.

    `issue()` no longer produces the PDF/XML itself -- that used to be a second
    transaction nested inside the first, and its commit survived the test fixture's
    own rollback, leaking issued invoices across tests. `issue` now returns after its
    own single commit, and this is the caller `service.py` documents as owning the
    second transaction: `produce_artifacts` is called here, right after, so a caller
    of this endpoint never has to make a separate call to see the PDF/XML.
    """
    service = _service(session, storage, settings)
    result = service.issue(invoice_id, data, actor)
    # Once `issue` has committed, the answer is the issued row whatever the render does
    # (REB-143). The number is consumed and the invoice is a fiscal fact; a render that
    # raised used to turn that into an error, and the person saw a failure for an
    # invoice that was really issued. The failure is logged and the row is read back as
    # it stands, so `pdf_document_id`/`xml_document_id` say which file exists and
    # «Rigenera documenti» (`POST /artifacts`, below) is the retry. A comment and not
    # the docstring, which is this route's OpenAPI description.
    try:
        service.produce_artifacts(result.id, actor)
    except Exception:
        # Anything, not only a `DomainError`: a Typst crash or a storage backend that
        # refused the bytes is exactly the case, and none of them undoes the commit
        # above. Logged first, so a rollback that fails too (a dead connection) cannot
        # hide the render's own error. The rollback clears whatever the render left
        # half-flushed or aborted, so the read below starts on a clean session.
        logger.exception("invoice %s issued, its PDF/XML were not produced", result.id)
        session.rollback()
    return service.get(result.id, actor)


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
    return Response(
        content=data,
        media_type=content_type,
        headers={
            # RFC 5987, percent-encoded rather than interpolated: the XML name is built
            # from a fiscal identifier and the PDF name from integers, so nothing
            # dangerous should reach here -- encoding it anyway means a future change
            # cannot turn a name into a response header.
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


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


@router.get("/{invoice_id}/timeline", response_model=list[ActivityRead])
def timeline(
    invoice_id: UUID,
    session: SessionDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ActivityRead]:
    return ActivityService(session).timeline("invoice", invoice_id, limit)
