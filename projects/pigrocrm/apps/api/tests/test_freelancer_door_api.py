"""The hub's reads of a freelancer through the root door (REB-1107, collaborators design § 5 and
§ 8): availability, the member's contracts and their signed PDF, the fiscal identifiers the
anonymous CV redacts, and the merge check, each by the hub's freelancer id, under
`PIGROCRM_ENGAGEMENTS_TOKEN`. The service behind them is `HubDoorService`; this file is the routes,
the token, the root-only rule and what each answers for a freelancer the CRM knows and one it does
not.

One real space is furnished once for the module: three freelancers with their engagements and
documents, written through the services and the Documenso fake, then every route reads it.
"""

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes.fake_documenso import SIGNING_HOST, FakeDocumenso
from fakes.letter_world import ADMIN, LEGAL_ENTITY_FIELDS, World, needs_binaries
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select, text, update
from sqlalchemy.orm import Session

from pigrocrm.core.collaborators.schemas import EngagementUpdate
from pigrocrm.core.collaborators.signing import SigningService
from pigrocrm.core.config import Settings, get_settings
from pigrocrm.core.db import session_factory
from pigrocrm.core.db.sidecar import drop_database
from pigrocrm.core.engagements.models import RebaseEngagement
from pigrocrm.core.legal_entities.schemas import LegalEntityUpsert
from pigrocrm.core.legal_entities.service import LegalEntityService
from pigrocrm.core.mail import RecordingSender
from pigrocrm.core.organizations.models import Organization
from pigrocrm.core.tenants import TenantService, TenantSignup, ensure_tenants_database
from pigrocrm.core.tenants.database import tenant_database_name, tenant_database_url
from pigrocrm.core.tenants.models import Tenant
from pigrocrm_api.deps import registry_session_factory, reset_session_factories
from pigrocrm_api.main import create_app
from pigrocrm_api.ratelimit import reset_rate_limit

# Furnishing a space writes real letters: pandoc and Typst live in the API's image.
pytestmark = needs_binaries

TOKEN = "un-token-per-la-porta-lungo-abbastanza"
SLUG = "rebase-door-test"
ROOT = "/api/rebase/freelancers"
SIGNED_PDF = b"%PDF-1.4 firmato a mano"
ADA_TAX_CODE = "LVLDAA15B10Z114X"
BEA_TAX_CODE = "BNCBEA80A41H501Z"
MONDAY = dt.date(2026, 11, 2)


def _bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Who:
    hub_id: UUID
    organization_id: UUID


@dataclass
class Furnished:
    client: TestClient
    settings: Settings
    ada: Who
    bea: Who
    cleo: Who
    dan: Who
    ada_framework_id: UUID
    ada_letter_id: UUID
    ada_letter_number: str
    ada_engagement_id: UUID
    bea_framework_id: UUID
    cleo_letter_id: UUID

    def get(self, who: Who | UUID, tail: str, **params: Any) -> Any:
        hub_id = who.hub_id if isinstance(who, Who) else who
        return self.client.get(f"{ROOT}/{hub_id}/{tail}", params=params, headers=_bearer())


@contextmanager
def _space(settings: Settings) -> Iterator[Session]:
    engine = create_engine(tenant_database_url(settings, tenant_database_name(SLUG)), future=True)
    try:
        with session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _hand_signed(
    session: Session, world: World, document_id: UUID, settings: Settings, day: dt.date
) -> None:
    SigningService(
        session, world.storage, settings, documenso=None, sender=None
    ).upload_signed_copy(document_id, SIGNED_PDF, "application/pdf", day, ADMIN)


def _furnish(settings: Settings, storage_root: Path) -> dict[str, Any]:
    """Ada: a signed framework agreement and a letter out for signature, with a second, open
    engagement that has no `days_per_week`. Bea: another tax code, her own signed framework and a
    signed letter (so an active engagement with the same client). Cleo: a letter written and never
    sent. Dan: an archived organization."""
    fake = FakeDocumenso()
    with _space(settings) as session:
        world = World(session, storage_root / "tenants" / SLUG)
        LegalEntityService(session).upsert_default(
            LegalEntityUpsert(**LEGAL_ENTITY_FIELDS, email="contratti@rebase.test"),
            ADMIN,
        )

        ada = world.freelancer(certified_email="ada@pec.test")
        ada_engagement = world.engagement(ada)
        ada_letter = world.letters.create_letter(ada_engagement.id, {"attivita": "Sviluppo"}, ADMIN)
        ada_framework = world.letters.create_framework_agreement(ada.id, None, ADMIN)
        _hand_signed(session, world, ada_framework.document.id, settings, dt.date(2026, 11, 4))
        # The letter leaves through the Documenso fake: `sent`, with the signer's link.
        SigningService(
            session,
            world.storage,
            settings,
            documenso=fake.client(),
            sender=RecordingSender(),
            tenant_id=uuid4(),
        ).send_engagement(ada_engagement.id, ADMIN)
        # An open engagement from an import: no `days_per_week`, so its week is «sconosciuta».
        unknown = world.engagement(
            ada, starts_on=MONDAY + dt.timedelta(days=14), ends_on=MONDAY + dt.timedelta(days=20)
        )
        world.engagements.update(unknown.id, EngagementUpdate(days_per_week=None), ADMIN)

        bea = world.freelancer(legal_name="Bea Bianchi", tax_code=BEA_TAX_CODE)
        bea_engagement = world.engagement(bea)
        bea_letter = world.letters.create_letter(bea_engagement.id, {"attivita": "Test"}, ADMIN)
        bea_framework = world.letters.create_framework_agreement(bea.id, None, ADMIN)
        _hand_signed(session, world, bea_framework.document.id, settings, dt.date(2026, 11, 5))
        _hand_signed(session, world, bea_letter.document.id, settings, dt.date(2026, 11, 5))

        cleo = world.freelancer(legal_name="Cleo Verdi", tax_code=ADA_TAX_CODE)
        cleo_engagement = world.engagement(cleo)
        cleo_letter = world.letters.create_letter(cleo_engagement.id, {"attivita": "X"}, ADMIN)

        dan = world.freelancer(legal_name="Dan Neri", tax_code="NREDNA80A01H501Q")
        session.execute(
            update(Organization)
            .where(Organization.id == dan.id)
            .values(deleted_at=dt.datetime.now(dt.UTC))
        )
        session.commit()

        return {
            "ada": Who(ada.hub_freelancer_id, ada.id),
            "bea": Who(bea.hub_freelancer_id, bea.id),
            "cleo": Who(cleo.hub_freelancer_id, cleo.id),
            "dan": Who(dan.hub_freelancer_id, dan.id),
            "ada_framework_id": ada_framework.document.id,
            "ada_letter_id": ada_letter.document.id,
            "ada_letter_number": ada_letter.number,
            "ada_engagement_id": ada_engagement.id,
            "bea_framework_id": bea_framework.document.id,
            "cleo_letter_id": cleo_letter.document.id,
        }


@pytest.fixture(scope="module")
def furnished(api_engine: Engine, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Furnished]:
    storage_root = tmp_path_factory.mktemp("storage")
    settings = Settings(
        database_url=api_engine.url.render_as_string(hide_password=False),
        jwt_secret="test-secret-for-the-api-test-suite-only",
        cookie_secure=True,
        public_url="https://pigro.test",
        hub_url="http://127.0.0.1:9",
        engagements_token=TOKEN,
        leads_space=SLUG,
        storage_local_root=str(storage_root),
        _env_file=None,  # type: ignore[call-arg]
    )
    reset_session_factories()
    get_settings.cache_clear()
    reset_rate_limit()
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    registry = ensure_tenants_database(settings)
    with session_factory(registry)() as session:
        TenantService(session, settings).provision(
            TenantSignup(first_name="Ada", slug=SLUG, name="rebase", email="spazio@studio.it")
        )
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield Furnished(client=client, settings=settings, **_furnish(settings, storage_root))
    finally:
        with registry.begin() as connection:
            slugs = connection.execute(text("SELECT slug FROM tenants")).scalars().all()
            connection.execute(text("DELETE FROM rebase_engagements"))
            connection.execute(text("DELETE FROM tenants"))
        registry.dispose()
        reset_session_factories()
        for slug in slugs:
            drop_database(settings, tenant_database_url(settings, tenant_database_name(slug)))
        get_settings.cache_clear()


@contextmanager
def _settings(furnished: Furnished, **changes: Any) -> Iterator[None]:
    """The same app under other settings, for the tests of a door that is off or misconfigured."""
    overrides = furnished.client.app.dependency_overrides  # type: ignore[attr-defined]
    before = overrides[get_settings]
    other = furnished.settings.model_copy(update=changes)
    overrides[get_settings] = lambda: other
    try:
        yield
    finally:
        overrides[get_settings] = before


def _every_route(hub_id: UUID) -> list[str]:
    other = uuid4()
    return [
        f"{ROOT}/{hub_id}/availability",
        f"{ROOT}/{hub_id}/contracts",
        f"{ROOT}/{hub_id}/contracts/{other}/pdf",
        f"{ROOT}/{hub_id}/fiscal-identifiers",
        f"{ROOT}/{hub_id}/merge-check?with={other}",
    ]


# ---- the token and the root's door -----------------------------------------------------------


def test_every_route_wants_the_bearer_before_it_reads_anything(furnished: Furnished) -> None:
    for url in _every_route(furnished.ada.hub_id):
        assert furnished.client.get(url).status_code == 401, url
        wrong = furnished.client.get(url, headers=_bearer("sbagliato"))
        assert wrong.status_code == 401, url
    # A path that is not even an id still meets the bearer first.
    assert furnished.client.get(f"{ROOT}/non-un-id/contracts").status_code == 401


def test_without_a_token_setting_the_door_is_absent(furnished: Furnished) -> None:
    with _settings(furnished, engagements_token=""):
        for url in _every_route(furnished.ada.hub_id):
            assert furnished.client.get(url, headers=_bearer()).status_code == 404, url


def test_the_door_is_the_roots_and_a_spaces_prefix_is_a_404(furnished: Furnished) -> None:
    url = f"/{SLUG}{ROOT}/{furnished.ada.hub_id}/contracts"
    refused = furnished.client.get(url, headers=_bearer())
    assert refused.status_code == 404, refused.text
    assert refused.json() == {"detail": "Not Found"}


def test_a_leads_space_that_is_not_there_is_a_503_the_hub_retries(furnished: Furnished) -> None:
    for slug in ("", "nessuno-spazio"):
        with _settings(furnished, leads_space=slug):
            for url in _every_route(furnished.ada.hub_id):
                answer = furnished.client.get(url, headers=_bearer())
                assert answer.status_code == 503, (slug, url, answer.text)


# ---- availability ----------------------------------------------------------------------------


def test_availability_counts_live_engagements_and_never_calls_an_unknown_week_free(
    furnished: Furnished,
) -> None:
    answer = furnished.get(
        furnished.ada, "availability", **{"from": "2026-11-02", "to": "2026-12-06"}
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["freelancer_id"] == str(furnished.ada.organization_id)
    assert body["hub_freelancer_id"] == str(furnished.ada.hub_id)
    weeks = {week["week"]: week for week in body["weeks"]}
    assert [week["status"] for week in weeks.values()] == [
        "committed",
        "committed",
        "unknown",
        "committed",
        "free",
    ]
    assert weeks["2026-W45"]["committed_days"] == "3.0"
    assert weeks["2026-W47"]["free_days"] is None
    assert weeks["2026-W47"]["label"] == "sconosciuta"
    assert weeks["2026-W49"]["label"] == "libera"


def test_availability_of_a_freelancer_the_crm_does_not_know_is_free_not_a_404(
    furnished: Furnished,
) -> None:
    stranger = uuid4()
    answer = furnished.get(stranger, "availability", **{"from": "2026-11-02", "to": "2026-11-15"})

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["freelancer_id"] is None and body["hub_freelancer_id"] == str(stranger)
    assert [week["status"] for week in body["weeks"]] == ["free", "free"]


def test_an_availability_window_the_crm_refuses_is_a_422(furnished: Furnished) -> None:
    backwards = furnished.get(
        furnished.ada, "availability", **{"from": "2026-11-09", "to": "2026-11-02"}
    )
    assert backwards.status_code == 422, backwards.text


# ---- the member's contracts ------------------------------------------------------------------


def test_contracts_carry_the_state_the_link_and_the_derived_dates(furnished: Furnished) -> None:
    answer = furnished.get(furnished.ada, "contracts")

    assert answer.status_code == 200, answer.text
    documents = {document["id"]: document for document in answer.json()["documents"]}
    assert set(documents) == {str(furnished.ada_framework_id), str(furnished.ada_letter_id)}

    framework = documents[str(furnished.ada_framework_id)]
    assert (framework["kind"], framework["letter_type"]) == ("framework_agreement", "ordinary")
    assert (framework["state"], framework["active"], framework["has_signed_copy"]) == (
        "signed",
        True,
        True,
    )
    assert framework["signing_url"] is None
    renewal = dt.date.fromisoformat(framework["renewal"])
    assert (renewal.month, renewal.day) == (11, 4)
    assert dt.date.fromisoformat(framework["last_notice_day"]) == renewal - dt.timedelta(days=30)
    assert framework["filename"].startswith("contratto-quadro-v")

    letter = documents[str(furnished.ada_letter_id)]
    assert (letter["kind"], letter["state"], letter["active"]) == (
        "engagement_letter",
        "sent",
        False,
    )
    assert letter["signing_url"].startswith(f"{SIGNING_HOST}/sign/")
    assert letter["number"] == furnished.ada_letter_number
    assert letter["customer"] == "Acme S.r.l."
    assert (letter["start"], letter["end"]) == ("2 novembre 2026", "29 novembre 2026")
    assert letter["has_signed_copy"] is False and letter["filename"] is None
    assert "450" not in answer.text and "7331" not in answer.text


def test_a_letter_links_to_its_deal_in_the_freelancers_own_space_once_the_door_wrote_it(
    furnished: Furnished,
) -> None:
    deal_id = uuid4()
    with registry_session_factory(furnished.settings)() as registry:
        tenant_id = registry.scalars(select(Tenant.id).where(Tenant.slug == SLUG)).one()
        row = RebaseEngagement(
            match_id=furnished.ada_engagement_id, tenant_id=tenant_id, deal_id=deal_id
        )
        registry.add(row)
        registry.commit()
        try:
            documents = furnished.get(furnished.ada, "contracts").json()["documents"]
        finally:
            registry.delete(row)
            registry.commit()

    letter = next(d for d in documents if d["id"] == str(furnished.ada_letter_id))
    framework = next(d for d in documents if d["id"] == str(furnished.ada_framework_id))
    assert letter["pigro_url"] == f"https://pigro.test/{SLUG}/app/deal/{deal_id}"
    assert framework["pigro_url"] is None


def test_a_letter_that_never_left_is_not_the_members_business(furnished: Furnished) -> None:
    answer = furnished.get(furnished.cleo, "contracts")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"documents": []}


def test_contracts_of_a_freelancer_the_crm_does_not_know_are_empty_not_a_404(
    furnished: Furnished,
) -> None:
    answer = furnished.get(uuid4(), "contracts")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"documents": []}


# ---- the signed PDF --------------------------------------------------------------------------


def test_the_signed_copy_of_their_own_document_is_served(furnished: Furnished) -> None:
    answer = furnished.get(furnished.ada, f"contracts/{furnished.ada_framework_id}/pdf")

    assert answer.status_code == 200, answer.text
    assert answer.content == SIGNED_PDF
    assert answer.headers["content-type"] == "application/pdf"
    assert "contratto-quadro-v" in answer.headers["content-disposition"]


def test_a_document_that_is_not_theirs_is_the_same_404_as_one_that_does_not_exist(
    furnished: Furnished,
) -> None:
    someone_elses = furnished.get(furnished.ada, f"contracts/{furnished.bea_framework_id}/pdf")
    missing = furnished.get(furnished.ada, f"contracts/{uuid4()}/pdf")
    unsigned = furnished.get(furnished.ada, f"contracts/{furnished.ada_letter_id}/pdf")
    stranger = furnished.get(uuid4(), f"contracts/{furnished.ada_framework_id}/pdf")

    for refused in (someone_elses, missing, unsigned, stranger):
        assert refused.status_code == 404, refused.text
        assert SIGNED_PDF not in refused.content
    # Bea's own copy is Bea's to read.
    assert (
        furnished.get(furnished.bea, f"contracts/{furnished.bea_framework_id}/pdf").content
        == SIGNED_PDF
    )


# ---- the fiscal identifiers ------------------------------------------------------------------


def test_fiscal_identifiers_are_the_organizations_even_when_it_is_archived(
    furnished: Furnished,
) -> None:
    ada = furnished.get(furnished.ada, "fiscal-identifiers")
    assert ada.status_code == 200, ada.text
    assert ada.json()["tax_code"] == ADA_TAX_CODE
    assert ada.json()["certified_email"] == "ada@pec.test"
    assert len(ada.json()["vat_number"]) == 11

    archived = furnished.get(furnished.dan, "fiscal-identifiers")
    assert archived.status_code == 200, archived.text
    assert archived.json()["tax_code"] == "NREDNA80A01H501Q"
    # Archived, so not a freelancer the other reads would answer for.
    assert furnished.get(furnished.dan, "contracts").json() == {"documents": []}


def test_fiscal_identifiers_of_a_freelancer_the_crm_does_not_know_are_null(
    furnished: Furnished,
) -> None:
    answer = furnished.get(uuid4(), "fiscal-identifiers")

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"tax_code": None, "vat_number": None, "certified_email": None}


# ---- the merge check -------------------------------------------------------------------------


def test_the_merge_check_names_a_shared_framework_a_shared_client_and_two_tax_codes(
    furnished: Furnished,
) -> None:
    answer = furnished.get(furnished.ada, "merge-check", **{"with": str(furnished.bea.hub_id)})

    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "framework_regimes": ["ordinary"],
        "shared_clients": 1,
        "tax_code_conflict": True,
    }
    # The check is symmetric.
    again = furnished.get(furnished.bea, "merge-check", **{"with": str(furnished.ada.hub_id)})
    assert again.json() == answer.json()


def test_the_merge_check_is_clean_for_a_card_with_nothing_live_and_for_an_unknown_one(
    furnished: Furnished,
) -> None:
    # Cleo has Ada's tax code, a draft engagement and no framework agreement.
    clean = furnished.get(furnished.ada, "merge-check", **{"with": str(furnished.cleo.hub_id)})
    assert clean.json() == {
        "framework_regimes": [],
        "shared_clients": 0,
        "tax_code_conflict": False,
    }

    unknown = furnished.get(furnished.ada, "merge-check", **{"with": str(uuid4())})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == clean.json()

    missing = furnished.client.get(f"{ROOT}/{furnished.ada.hub_id}/merge-check", headers=_bearer())
    assert missing.status_code == 422, missing.text
