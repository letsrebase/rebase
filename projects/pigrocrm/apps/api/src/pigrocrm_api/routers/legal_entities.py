"""The legal entities of a space, and each one's fiscal profile (REB-616, spec 2026-10-03 §3).

These routes replace `GET/PUT /api/emitter` and `GET/PUT /api/fiscal-profile`, which
addressed one row with no id. `POST /api/legal-entities` arrived last, with milestone 5 (§9,
REB-631): creating a second legal entity waited until the register, the customer chain, the
rendering and the per-legal-entity taxes could serve it, and it is born with its fiscal
profile in the same request (§1.2). Everything an admin could do to the one profile
before, they do to `/api/legal-entities/{id}` now; the SPA reads the id from
`GET /api/legal-entities`.

Admin-only writes are enforced by the services (`actor.require_admin`), not by a router
dependency: there is no role dependency in this codebase, and adding one here would
put the same rule in two places.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from pigrocrm.core.errors import NotFound
from pigrocrm.core.fiscal.schemas import FiscalProfileRead, FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService
from pigrocrm.core.legal_entities.assets import ASSETS_ACTION, MAX_IMAGE_BYTES, LegalEntityAssets
from pigrocrm.core.legal_entities.schemas import (
    COUNTRY_MAX_LENGTH,
    PROVINCE_MAX_LENGTH,
    LegalEntityCreate,
    LegalEntityDeactivated,
    LegalEntityRead,
    LegalEntityUpsert,
)
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.legal_entities.site_schemas import (
    IrapRegionOption,
    LegalEntitySiteCreate,
    LegalEntitySiteRead,
    LegalEntitySiteUpdate,
)
from pigrocrm.core.legal_entities.site_service import LegalEntitySiteService
from pigrocrm.core.places.locate import MunicipalityOption, municipalities_of
from pigrocrm.core.validation import SafeStr
from pigrocrm_api.deps import ActorDep, SessionDep, StorageDep, get_actor
from pigrocrm_api.downloads import attachment_response
from pigrocrm_api.errors import PROBLEM_RESPONSES
from pigrocrm_api.uploads import read_upload, single_file_body

router = APIRouter(
    prefix="/api/legal-entities",
    tags=["legal_entities"],
    responses=PROBLEM_RESPONSES,
    dependencies=[Depends(get_actor)],
)


@router.get("", response_model=list[LegalEntityRead])
def list_legal_entities(
    session: SessionDep, actor: ActorDep, include_inactive: bool = False
) -> list[LegalEntityRead]:
    """The default first. Deactivated legal entities are left out unless asked for: a
    selector never offers one, Settings may still show it."""
    return LegalEntityService(session).list(actor, only_active=not include_inactive)


@router.post("", response_model=LegalEntityRead, status_code=status.HTTP_201_CREATED)
def create_legal_entity(
    data: LegalEntityCreate, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    """A second legal entity, with its fiscal profile, in one transaction (REB-631, spec
    2026-10-03 §3): active, not the default, and the first of a space when it had
    none. A profile the fiscal rules would refuse refuses the whole request, so no
    legal entity exists that could not issue."""
    return LegalEntityService(session).create(data, actor)


# Before `/{legal_entity_id}`: a literal segment declared after the parameterised route would
# be read as an id and answer 422.
@router.get("/suggestion", response_model=LegalEntityRead)
def suggest_legal_entity(
    session: SessionDep,
    country: Annotated[
        SafeStr | None,
        Query(
            max_length=COUNTRY_MAX_LENGTH,
            description="The client's country, ISO 3166-1 alpha-2; omitted, IT",
        ),
    ] = None,
) -> LegalEntityRead:
    """The legal entity a new customer of `country` would be billed by when nobody picks one
    (REB-624, spec 2026-10-03 §1.6). The form and an agent both ask here, so the rule
    lives in the service once; `POST /api/organizations` applies the same one
    when `legal_entity_id` is left out."""
    return LegalEntityRead.model_validate(LegalEntityService(session).propose(country))


@router.get("/municipalities", response_model=list[MunicipalityOption])
def list_municipalities(
    province: Annotated[
        SafeStr,
        Query(
            min_length=PROVINCE_MAX_LENGTH,
            max_length=PROVINCE_MAX_LENGTH,
            description="The province's two-letter abbreviation, such as MI",
        ),
    ],
) -> list[MunicipalityOption]:
    """The municipalities of a province, by name, from ISTAT's list (REB-1069). The form
    offers them when a legal entity's city matches none; the name it picks matches exactly.
    Empty for an abbreviation the list does not carry."""
    return municipalities_of(province)


@router.get("/regions", response_model=list[IrapRegionOption])
def list_irap_regions(session: SessionDep) -> list[IrapRegionOption]:
    """The regions and autonomous provinces a site can be in (REB-1092): the keys of the IRAP
    rate table, in the order the fiscal pack lists them."""
    return LegalEntitySiteService(session).list_irap_regions()


@router.get("/{legal_entity_id}", response_model=LegalEntityRead)
def get_legal_entity(
    legal_entity_id: UUID, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    return LegalEntityService(session).get(actor, legal_entity_id)


@router.put("/{legal_entity_id}", response_model=LegalEntityRead)
def update_legal_entity(
    legal_entity_id: UUID, data: LegalEntityUpsert, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    """Whole-row replacement, as the single profile always was: a key left out goes
    back to its default. With `updated_at` in the body, the row's version the draft was
    built on, a save on a row somebody else saved since answers 409 `stale_row`
    (REB-622)."""
    return LegalEntityService(session).update(legal_entity_id, data, actor)


@router.post("/{legal_entity_id}/default", response_model=LegalEntityRead)
def set_default_legal_entity(
    legal_entity_id: UUID, session: SessionDep, actor: ActorDep
) -> LegalEntityRead:
    return LegalEntityService(session).set_default(legal_entity_id, actor)


@router.delete("/{legal_entity_id}", response_model=LegalEntityDeactivated)
def deactivate_legal_entity(
    legal_entity_id: UUID, session: SessionDep, actor: ActorDep
) -> LegalEntityDeactivated:
    """Deactivation, never a row delete: a legal entity that issued an invoice stays
    readable forever. Refused on the default; move the default first. The answer says
    how many organizations still point at the row (`linked_organizations`): nothing new is
    born under them until they are moved to an active legal entity."""
    return LegalEntityService(session).deactivate(legal_entity_id, actor)


@router.get("/{legal_entity_id}/sites", response_model=list[LegalEntitySiteRead])
def list_legal_entity_sites(
    legal_entity_id: UUID, session: SessionDep, actor: ActorDep
) -> list[LegalEntitySiteRead]:
    """The places of business beside the registered office, each with how many staff work
    there (REB-1092). An srl with sites in more than one region splits its IRAP base between
    them by the staff's pay."""
    return LegalEntitySiteService(session).list_legal_entity_sites(legal_entity_id, actor)


@router.post(
    "/{legal_entity_id}/sites",
    response_model=LegalEntitySiteRead,
    status_code=status.HTTP_201_CREATED,
)
def add_legal_entity_site(
    legal_entity_id: UUID, data: LegalEntitySiteCreate, session: SessionDep, actor: ActorDep
) -> LegalEntitySiteRead:
    """Administrators only, and a person's act: no agent credential reaches it."""
    return LegalEntitySiteService(session).add_legal_entity_site(legal_entity_id, data, actor)


@router.patch("/{legal_entity_id}/sites/{site_id}", response_model=LegalEntitySiteRead)
def update_legal_entity_site(
    legal_entity_id: UUID,
    site_id: UUID,
    data: LegalEntitySiteUpdate,
    session: SessionDep,
    actor: ActorDep,
) -> LegalEntitySiteRead:
    return LegalEntitySiteService(session).update_legal_entity_site(
        legal_entity_id, site_id, data, actor
    )


@router.delete("/{legal_entity_id}/sites/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_legal_entity_site(
    legal_entity_id: UUID, site_id: UUID, session: SessionDep, actor: ActorDep
) -> Response:
    """Refused with 409 while staff are assigned to the site: move them first."""
    LegalEntitySiteService(session).remove_legal_entity_site(legal_entity_id, site_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{legal_entity_id}/fiscal-profile", response_model=FiscalProfileRead)
def get_fiscal_profile(
    legal_entity_id: UUID, session: SessionDep, actor: ActorDep
) -> FiscalProfileRead:
    """404 until it is saved once: an empty profile and an unsaved one are different
    facts, and nothing can be issued without it."""
    return FiscalProfileService(session).get(actor, legal_entity_id)


@router.put(
    "/{legal_entity_id}/fiscal-profile",
    response_model=FiscalProfileRead,
    status_code=status.HTTP_200_OK,
)
def upsert_fiscal_profile(
    legal_entity_id: UUID, data: FiscalProfileUpsert, session: SessionDep, actor: ActorDep
) -> FiscalProfileRead:
    """Whole-row, like the legal entity's own `PUT`; with `updated_at` in the body, a save on
    a profile somebody else saved since answers 409 `stale_row` (REB-622)."""
    return FiscalProfileService(session).upsert(data, actor, legal_entity_id)


# ---- the logo and the signature (REB-628, spec 2026-10-03 §3) -------------------------
#
# One pair of routes per image rather than a `{slot}` path parameter, so the OpenAPI
# document names each and the generated client types them apart. The bytes are served
# from the API, never from a storage URL: the API is the one place authorisation exists
# on both backends, as for a document's download.

Slot = Literal["logo", "signature"]

_IMAGE_HEADERS = {
    # A stored file served from the app's own origin, never run as a page (`nosniff` comes
    # with `attachment_response`). The policy is what keeps an SVG opened in a tab inert
    # even if the upload's own check were ever wrong.
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "Cache-Control": "private, no-store",
}


def _serve(session: SessionDep, storage: StorageDep, legal_entity_id: UUID, slot: Slot) -> Response:
    assets = LegalEntityAssets(session, storage)
    image = assets.logo(legal_entity_id) if slot == "logo" else assets.signature(legal_entity_id)
    if image is None:
        raise NotFound("legal_entities", f"{legal_entity_id}/{slot}")
    data, content_type = image
    extension = "svg" if content_type.endswith("svg+xml") else "png"
    return attachment_response(
        data,
        content_type,
        f"{slot}.{extension}",
        disposition="inline",
        headers=_IMAGE_HEADERS,
    )


@router.get(
    "/{legal_entity_id}/logo",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}}},
)
def get_logo(legal_entity_id: UUID, session: SessionDep, storage: StorageDep) -> Response:
    """The legal entity's logo, PNG or SVG; 404 when it has none."""
    return _serve(session, storage, legal_entity_id, "logo")


@router.put(
    "/{legal_entity_id}/logo",
    response_model=LegalEntityRead,
    openapi_extra=single_file_body("A PNG or an SVG under 1 MiB."),
)
async def put_logo(
    legal_entity_id: UUID,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
) -> LegalEntityRead:
    """A PNG or an SVG under 1 MiB, sniffed from the bytes: the client's content type
    and file name are never trusted. Replaces the one before. One byte past the limit
    is read and no more, so an oversize body is refused by the service's own sentence
    without being buffered whole."""
    actor.require_admin(ASSETS_ACTION)
    data, _ = await read_upload(request, MAX_IMAGE_BYTES)
    return await run_in_threadpool(
        LegalEntityAssets(session, storage).set_logo, data, actor, legal_entity_id
    )


@router.delete("/{legal_entity_id}/logo", response_model=LegalEntityRead)
def delete_logo(
    legal_entity_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> LegalEntityRead:
    """Idempotent: a legal entity with no logo answers its row, not an error."""
    return LegalEntityAssets(session, storage).remove_logo(actor, legal_entity_id)


@router.get(
    "/{legal_entity_id}/signature",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_signature(legal_entity_id: UUID, session: SessionDep, storage: StorageDep) -> Response:
    """The legal entity's signature image, PNG; 404 when it has none."""
    return _serve(session, storage, legal_entity_id, "signature")


@router.put(
    "/{legal_entity_id}/signature",
    response_model=LegalEntityRead,
    openapi_extra=single_file_body("A PNG under 1 MiB."),
)
async def put_signature(
    legal_entity_id: UUID,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    actor: ActorDep,
) -> LegalEntityRead:
    """A PNG under 1 MiB: the offers draw the signature through a Markdown image whose
    name is fixed in the template, so an SVG is refused."""
    actor.require_admin(ASSETS_ACTION)
    data, _ = await read_upload(request, MAX_IMAGE_BYTES)
    return await run_in_threadpool(
        LegalEntityAssets(session, storage).set_signature, data, actor, legal_entity_id
    )


@router.delete("/{legal_entity_id}/signature", response_model=LegalEntityRead)
def delete_signature(
    legal_entity_id: UUID, session: SessionDep, storage: StorageDep, actor: ActorDep
) -> LegalEntityRead:
    return LegalEntityAssets(session, storage).remove_signature(actor, legal_entity_id)
