"""The aziende of a space, and each one's fiscal profile (REB-616, spec 2026-10-03 §3).

These routes replace `GET/PUT /api/emitter` and `GET/PUT /api/fiscal-profile`, which
addressed one row with no id. `POST /api/aziende` arrived last, with milestone 5 (§9,
REB-631): creating a second azienda waited until the register, the customer chain, the
rendering and the per-azienda taxes could serve it, and it is born with its fiscal
profile in the same request (§1.2). Everything an admin could do to the one profile
before, they do to `/api/aziende/{id}` now; the SPA reads the id from `GET /api/aziende`.

Admin-only writes are enforced by the services (`actor.require_admin`), not by a router
dependency: there is no role dependency in this codebase, and adding one here would
put the same rule in two places.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import Response

from pigrocrm.core.emitter.assets import MAX_IMAGE_BYTES, LegalEntityAssets
from pigrocrm.core.emitter.schemas import (
    NAZIONE_MAX_LENGTH,
    LegalEntityCreate,
    LegalEntityDeactivated,
    LegalEntityRead,
    LegalEntityUpsert,
)
from pigrocrm.core.emitter.service import LegalEntityService
from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.schemas import FiscalProfileRead, FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep
from pigrocrm_api.errors import PROBLEM_RESPONSES

router = APIRouter(prefix="/api/aziende", tags=["aziende"], responses=PROBLEM_RESPONSES)


@router.get("", response_model=list[LegalEntityRead])
def list_aziende(
    session: SessionDep, actor: ActorDep, include_inactive: bool = False
) -> list[LegalEntityRead]:
    """The default first. Deactivated aziende are left out unless asked for: a
    selector never offers one, Impostazioni may still show it."""
    return LegalEntityService(session).list(actor, only_active=not include_inactive)


@router.post("", response_model=LegalEntityRead, status_code=status.HTTP_201_CREATED)
def create_azienda(
    data: LegalEntityCreate, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    """A second azienda, with its fiscal profile, in one transaction (REB-631, spec
    2026-10-03 §3): active, not the default, and the first of a space when it had
    none. A profile the fiscal rules would refuse refuses the whole request, so no
    azienda exists that could not issue."""
    return LegalEntityService(session).create(data, actor)


# Before `/{azienda_id}`: a literal segment declared after the parameterised route would
# be read as an id and answer 422.
@router.get("/proposta", response_model=LegalEntityRead)
def propose_azienda(
    session: SessionDep,
    actor: ActorDep,
    nazione: Annotated[
        SafeStr | None,
        Query(
            max_length=NAZIONE_MAX_LENGTH,
            description="Nazione del cliente, ISO 3166-1 alpha-2; omessa, IT",
        ),
    ] = None,
) -> LegalEntityRead:
    """The azienda a new customer of `nazione` would be billed by when nobody picks one
    (REB-624, spec 2026-10-03 §1.6). The form and an agent both ask here, so the rule
    lives in the service once; `POST /api/customers` applies the same one
    when `azienda_id` is left out."""
    return LegalEntityRead.model_validate(LegalEntityService(session).propose(nazione))


@router.get("/{azienda_id}", response_model=LegalEntityRead)
def get_azienda(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> LegalEntityRead:
    return LegalEntityService(session).get(actor, azienda_id)


@router.put("/{azienda_id}", response_model=LegalEntityRead)
def update_azienda(
    azienda_id: UUID, data: LegalEntityUpsert, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    """Whole-row replacement, as the single profile always was: a key left out goes
    back to its default. With `updated_at` in the body, the row's version the draft was
    built on, a save on a row somebody else saved since answers 409 `stale_row`
    (REB-622)."""
    return LegalEntityService(session).update(azienda_id, data, actor)


@router.post("/{azienda_id}/predefinita", response_model=LegalEntityRead)
def set_default_azienda(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> LegalEntityRead:
    return LegalEntityService(session).set_default(azienda_id, actor)


@router.delete("/{azienda_id}", response_model=LegalEntityDeactivated)
def deactivate_azienda(
    azienda_id: UUID, session: SessionDep, actor: ActorDep
) -> LegalEntityDeactivated:
    """Deactivation, never a row delete: an azienda that issued an invoice stays
    readable forever. Refused on the default; move the default first. The answer says
    how many customers still point at the row (`clienti_collegati`): nothing new is
    born under them until they are moved to an active azienda."""
    return LegalEntityService(session).deactivate(azienda_id, actor)


@router.get("/{azienda_id}/fiscal-profile", response_model=FiscalProfileRead)
def get_fiscal_profile(azienda_id: UUID, session: SessionDep, actor: ActorDep) -> FiscalProfileRead:
    """404 until it is saved once: an empty profile and an unsaved one are different
    facts, and nothing can be issued without it."""
    return FiscalProfileService(session).get(actor, azienda_id)


@router.put(
    "/{azienda_id}/fiscal-profile",
    response_model=FiscalProfileRead,
    status_code=status.HTTP_200_OK,
)
def upsert_fiscal_profile(
    azienda_id: UUID, data: FiscalProfileUpsert, session: SessionDep, actor: ActorDep
) -> FiscalProfileRead:
    """Whole-row, like the azienda's own `PUT`; with `updated_at` in the body, a save on
    a profile somebody else saved since answers 409 `stale_row` (REB-622)."""
    return FiscalProfileService(session).upsert(data, actor, azienda_id)


# ---- the logo and the signature (REB-628, spec 2026-10-03 §3) -------------------------
#
# One pair of routes per image rather than a `{slot}` path parameter, so the OpenAPI
# document names each and the generated client types them apart. The bytes are served
# from the API, never from a storage URL: the API is the one place authorisation exists
# on both backends, as for a document's download.

Slot = Literal["logo", "firma"]

_IMAGE_HEADERS = {
    # A stored file served from the app's own origin: never sniffed into something
    # else, never run as a page. The policy is what keeps an SVG opened in a tab inert
    # even if the upload's own check were ever wrong.
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "Cache-Control": "private, no-store",
}


def _serve(session: SessionDep, storage: StorageDep, azienda_id: UUID, slot: Slot) -> Response:
    assets = LegalEntityAssets(session, storage)
    image = assets.logo(azienda_id) if slot == "logo" else assets.firma(azienda_id)
    if image is None:
        raise NotFound("emitter_profile", f"{azienda_id}/{slot}")
    data, content_type = image
    extension = "svg" if content_type.endswith("svg+xml") else "png"
    return Response(
        content=data,
        media_type=content_type,
        headers={
            **_IMAGE_HEADERS,
            "Content-Disposition": f'inline; filename="{slot}.{extension}"',
        },
    )


@router.get(
    "/{azienda_id}/logo",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}}},
)
def get_logo(
    azienda_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> Response:
    """The azienda's logo, PNG or SVG; 404 when it has none."""
    return _serve(session, storage, azienda_id, "logo")


@router.put("/{azienda_id}/logo", response_model=LegalEntityRead)
async def put_logo(
    azienda_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
) -> LegalEntityRead:
    """A PNG or an SVG under 1 MiB, sniffed from the bytes: the client's content type
    and file name are never trusted. Replaces the one before. One byte past the limit
    is read and no more, so an oversize body is refused by the service's own sentence
    without being buffered whole."""
    data = await file.read(MAX_IMAGE_BYTES + 1)
    return LegalEntityAssets(session, storage).set_logo(data, actor, azienda_id)


@router.delete("/{azienda_id}/logo", response_model=LegalEntityRead)
def delete_logo(
    azienda_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> LegalEntityRead:
    """Idempotent: an azienda with no logo answers its row, not an error."""
    return LegalEntityAssets(session, storage).remove_logo(actor, azienda_id)


@router.get(
    "/{azienda_id}/firma",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_firma(
    azienda_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> Response:
    """The azienda's signature image, PNG; 404 when it has none."""
    return _serve(session, storage, azienda_id, "firma")


@router.put("/{azienda_id}/firma", response_model=LegalEntityRead)
async def put_firma(
    azienda_id: UUID,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
    file: Annotated[UploadFile, File()],
) -> LegalEntityRead:
    """A PNG under 1 MiB: the offers draw the signature through a Markdown image whose
    name is fixed in the template, so an SVG is refused."""
    data = await file.read(MAX_IMAGE_BYTES + 1)
    return LegalEntityAssets(session, storage).set_firma(data, actor, azienda_id)


@router.delete("/{azienda_id}/firma", response_model=LegalEntityRead)
def delete_firma(
    azienda_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> LegalEntityRead:
    return LegalEntityAssets(session, storage).remove_firma(actor, azienda_id)
