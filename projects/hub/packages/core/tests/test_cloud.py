"""The talent cloud's door (REB-518, spec § 4.1): an admin opens the cloud from a company
request's page to that request's referente, one live grant per person and company, and
closes it again; the member area reads whether any grant of the person is live."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.cloud import TalentCloudService
from rebase_core.companies import CompanyService
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound
from rebase_core.models import TalentCloudGrant, User
from rebase_core.schemas import CompanyCreate


@pytest.fixture
def settings(hub_engine: Engine) -> Settings:
    return Settings(
        database_url=hub_engine.url.render_as_string(hide_password=False),
        hub_url="http://localhost:5180/hub",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    for table in ("talent_cloud_grants", "admin_actions", "comments", "companies", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="Sala", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _request(session: Session, email: str = "wile@acme.it", azienda: str = "Acme S.r.l.") -> UUID:
    read, _ = CompanyService(session).request(
        CompanyCreate(
            nome_azienda=azienda,
            referente_nome="Wile",
            referente_cognome="Coyote",
            email=email,
            telefono="+39 345 1234567",
            figura_richiesta="Backend developer",
            progetto="Serve un backend developer per tre mesi.",
            periodo_da=date(2026, 10, 1),
            durata="3 mesi",
            budget_giornaliero=Decimal("500"),
            remoto="remoto",
            numero_risorse=1,
        )
    )
    return read.id


def test_grant_is_one_live_per_user_and_company_and_mails(
    clean: Session, settings: Settings
) -> None:
    admin_id = _admin(clean)
    acme = _request(clean)
    cloud = TalentCloudService(clean, settings)

    first, mail = cloud.grant(acme, admin_id)
    assert first.company_id == acme and first.azienda == "Acme S.r.l."
    assert (first.referente, first.email) == ("Wile Coyote", "wile@acme.it")
    assert first.granted_by == admin_id and first.granted_by_nome == "Ivan"
    assert first.revoked_at is None
    assert mail is not None
    assert mail.to == "wile@acme.it"
    assert mail.subject == "Il talent cloud di rebase è aperto per Acme S.r.l."
    assert "http://localhost:5180/hub/me/cloud" in mail.text

    # A second «Apri» on the same request, a double click included, answers the live
    # grant and mails nobody: no second row, no 500 from the partial unique index.
    again, second_mail = cloud.grant(acme, admin_id)
    assert again.id == first.id and second_mail is None

    # The same person behind a second request is a second company: a grant of its own,
    # and a mail naming that company.
    bianchi = _request(clean, azienda="Bianchi Srl")
    other, other_mail = cloud.grant(bianchi, admin_id)
    assert other.id != first.id and other.user_id == first.user_id
    assert other_mail is not None and "Bianchi Srl" in other_mail.subject
    live = clean.scalars(select(TalentCloudGrant).where(TalentCloudGrant.revoked_at.is_(None)))
    assert len(live.all()) == 2


def test_grant_on_a_request_that_is_not_there_is_not_found(
    clean: Session, settings: Settings
) -> None:
    admin_id = _admin(clean)
    with pytest.raises(NotFound):
        TalentCloudService(clean, settings).grant(uuid4(), admin_id)
    acme = _request(clean)
    CompanyService(clean).soft_delete(acme, admin_id)
    with pytest.raises(NotFound):
        TalentCloudService(clean, settings).grant(acme, admin_id)


def test_revoke_closes_it(clean: Session, settings: Settings) -> None:
    admin_id = _admin(clean)
    acme, bianchi = _request(clean), _request(clean, azienda="Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    opened, _ = cloud.grant(acme, admin_id)
    kept, _ = cloud.grant(bianchi, admin_id)

    closed = cloud.revoke(acme, admin_id)
    assert closed.id == opened.id
    assert closed.revoked_at is not None
    assert (closed.revoked_by, closed.revoked_by_nome) == (admin_id, "Ivan")
    # That company's grant only: the other company's stays live, and so does the cloud.
    assert cloud.for_company(acme) is None
    live = cloud.for_company(bianchi)
    assert live is not None and live.id == kept.id
    assert cloud.for_user(opened.user_id) is not None

    # Nothing live left for that company: a sentence, not a second closing.
    with pytest.raises(InvalidState) as refused:
        cloud.revoke(acme, admin_id)
    assert refused.value.message == "Il talent cloud non è aperto per questa azienda."

    # Opened again after a revoke: a new row, and a new mail.
    reopened, mail = cloud.grant(acme, admin_id)
    assert reopened.id != opened.id and mail is not None


def test_for_user_answers_the_newest_live_grant_across_companies(
    clean: Session, settings: Settings
) -> None:
    admin_id = _admin(clean)
    acme, bianchi = _request(clean), _request(clean, azienda="Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    older, _ = cloud.grant(acme, admin_id)
    assert cloud.for_user(older.user_id) is not None
    newer, _ = cloud.grant(bianchi, admin_id)

    newest = cloud.for_user(older.user_id)
    assert newest is not None and newest.id == newer.id and newest.company_id == bianchi

    cloud.revoke(bianchi, admin_id)
    remaining = cloud.for_user(older.user_id)
    assert remaining is not None and remaining.id == older.id

    cloud.revoke(acme, admin_id)
    assert cloud.for_user(older.user_id) is None
    assert cloud.for_user(uuid4()) is None


def test_a_deleted_request_closes_its_door_and_a_restore_opens_it_again(
    clean: Session, settings: Settings
) -> None:
    """The cloud shows names and CVs: a request an admin took down does not keep the
    door open behind it, and `for_company` reads the page the admin can still open."""
    admin_id = _admin(clean)
    acme = _request(clean)
    cloud = TalentCloudService(clean, settings)
    opened, _ = cloud.grant(acme, admin_id)

    CompanyService(clean).soft_delete(acme, admin_id)
    assert cloud.for_user(opened.user_id) is None

    CompanyService(clean).restore(acme, admin_id)
    restored = cloud.for_user(opened.user_id)
    assert restored is not None and restored.id == opened.id


def test_the_company_page_reads_its_live_grant(clean: Session, settings: Settings) -> None:
    admin_id = _admin(clean)
    acme = _request(clean)
    assert CompanyService(clean).get(acme).talent_cloud_grant is None

    opened, _ = TalentCloudService(clean, settings).grant(acme, admin_id)
    read = CompanyService(clean).get(acme).talent_cloud_grant
    assert read is not None and read.id == opened.id
    # Only the page reads it: the list leaves it empty, as it does the thread.
    listed = CompanyService(clean).list_recent()
    assert all(item.talent_cloud_grant is None for item in listed.items)


def test_the_list_reads_every_grant_newest_first_with_the_names(
    clean: Session, settings: Settings
) -> None:
    admin_id = _admin(clean)
    acme, bianchi = _request(clean), _request(clean, "ada@studio.it", "Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    first, _ = cloud.grant(acme, admin_id)
    second, _ = cloud.grant(bianchi, admin_id)
    cloud.revoke(acme, admin_id)

    grants = cloud.list()
    assert [grant.id for grant in grants] == [second.id, first.id]
    assert [grant.azienda for grant in grants] == ["Bianchi Srl", "Acme S.r.l."]
    assert grants[0].email == "ada@studio.it" and grants[0].revoked_at is None
    assert grants[1].revoked_at is not None and grants[1].revoked_by_nome == "Ivan"
