"""The talent cloud's door (REB-518, spec § 4.1): an admin opens the cloud from a company
request's page to that request's referente, one live grant per person and company, and
closes it again; the member area reads whether any grant of the person is live.

The cloud itself (REB-519, spec § 4.2), from the section of that name down: the named
talents with their filters, their order and the cap, the CV behind the one filter, and
the requests a company files from a card and from the builder."""

import json
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from admins import an_admin
from fakes_cards import CARD, MODEL
from sqlalchemy import Engine, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_REQUEST_SENT, Tracker
from rebase_core.bands import Band
from rebase_core.cloud import (
    CLOUD_LIST_CAP,
    NOT_IN_THE_CLOUD,
    CloudTalentService,
    TalentCloudService,
)
from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.config import Settings
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.models import (
    Freelancer,
    FreelancerCard,
    Note,
    TalentCloudGrant,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
)
from rebase_core.team_requests import PROPOSAL_REFUSED, TeamRequestService
from rebase_core.team_schemas import CloudTalentQuery, CloudTalentRead


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
    for table in ("talent_cloud_grants", "admin_actions", "notes", "companies", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _request(session: Session, email: str = "wile@acme.it", company: str = "Acme S.r.l.") -> UUID:
    read, _ = CompanyService(session).request(
        CompanyCreate(
            company_name=company,
            contact_first_name="Wile",
            contact_last_name="Coyote",
            email=email,
            phone="+39 345 1234567",
            requested_role="Backend developer",
            project="Serve un backend developer per tre mesi.",
            period_from=date(2026, 10, 1),
            duration="3 mesi",
            daily_budget=Decimal("500"),
            remote="remote",
            headcount=1,
        )
    )
    return read.id


def test_grant_is_one_live_per_user_and_company_and_mails(
    clean: Session, settings: Settings
) -> None:
    admin_id = an_admin(clean)
    acme = _request(clean)
    cloud = TalentCloudService(clean, settings)

    first, mail = cloud.grant(acme, admin_id)
    assert first.company_id == acme and first.company == "Acme S.r.l."
    assert (first.contact_person, first.email) == ("Wile Coyote", "wile@acme.it")
    assert first.granted_by == admin_id and first.granted_by_name == "Ivan"
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
    bianchi = _request(clean, company="Bianchi Srl")
    other, other_mail = cloud.grant(bianchi, admin_id)
    assert other.id != first.id and other.user_id == first.user_id
    assert other_mail is not None and "Bianchi Srl" in other_mail.subject
    live = clean.scalars(select(TalentCloudGrant).where(TalentCloudGrant.revoked_at.is_(None)))
    assert len(live.all()) == 2


def test_grant_on_a_request_that_is_not_there_is_not_found(
    clean: Session, settings: Settings
) -> None:
    admin_id = an_admin(clean)
    with pytest.raises(NotFound):
        TalentCloudService(clean, settings).grant(uuid4(), admin_id)
    acme = _request(clean)
    CompanyService(clean).soft_delete(acme, admin_id)
    with pytest.raises(NotFound):
        TalentCloudService(clean, settings).grant(acme, admin_id)


def test_revoke_closes_it(clean: Session, settings: Settings) -> None:
    admin_id = an_admin(clean)
    acme, bianchi = _request(clean), _request(clean, company="Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    opened, _ = cloud.grant(acme, admin_id)
    kept, _ = cloud.grant(bianchi, admin_id)

    closed = cloud.revoke(acme, admin_id)
    assert closed.id == opened.id
    assert closed.revoked_at is not None
    assert (closed.revoked_by, closed.revoked_by_name) == (admin_id, "Ivan")
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
    admin_id = an_admin(clean)
    acme, bianchi = _request(clean), _request(clean, company="Bianchi Srl")
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
    admin_id = an_admin(clean)
    acme = _request(clean)
    cloud = TalentCloudService(clean, settings)
    opened, _ = cloud.grant(acme, admin_id)

    CompanyService(clean).soft_delete(acme, admin_id)
    assert cloud.for_user(opened.user_id) is None

    CompanyService(clean).restore(acme, admin_id)
    restored = cloud.for_user(opened.user_id)
    assert restored is not None and restored.id == opened.id


def test_the_company_page_reads_its_live_grant(clean: Session, settings: Settings) -> None:
    admin_id = an_admin(clean)
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
    admin_id = an_admin(clean)
    acme, bianchi = _request(clean), _request(clean, "ada@studio.it", "Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    first, _ = cloud.grant(acme, admin_id)
    second, _ = cloud.grant(bianchi, admin_id)
    cloud.revoke(acme, admin_id)

    grants = cloud.list()
    assert [grant.id for grant in grants] == [second.id, first.id]
    assert [grant.company for grant in grants] == ["Bianchi Srl", "Acme S.r.l."]
    assert grants[0].email == "ada@studio.it" and grants[0].revoked_at is None
    assert grants[1].revoked_at is not None and grants[1].revoked_by_name == "Ivan"


def test_the_list_leaves_out_a_deleted_requests_grant_until_it_is_restored(
    clean: Session, settings: Settings
) -> None:
    """The list says what `for_user` does: a deleted request's live grant opens nothing,
    so it is not listed as live."""
    admin_id = an_admin(clean)
    acme, bianchi = _request(clean), _request(clean, "ada@studio.it", "Bianchi Srl")
    cloud = TalentCloudService(clean, settings)
    kept, _ = cloud.grant(acme, admin_id)
    hidden, _ = cloud.grant(bianchi, admin_id)

    CompanyService(clean).soft_delete(bianchi, admin_id)
    assert [grant.id for grant in cloud.list()] == [kept.id]

    CompanyService(clean).restore(bianchi, admin_id)
    assert [grant.id for grant in cloud.list()] == [hidden.id, kept.id]


def test_a_grant_that_loses_the_race_to_the_index_answers_the_winner(
    clean: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two «Apri» in two sessions both find nothing live and both insert: the partial
    unique index lets one in, and the other answers that row with no second mail."""
    admin_id = an_admin(clean)
    acme = _request(clean)
    cloud = TalentCloudService(clean, settings)
    winner, _ = cloud.grant(acme, admin_id)

    real_live = TalentCloudService._live
    misses = iter([True])

    def live_missing_once(
        self: TalentCloudService, user_id: UUID, company_id: UUID
    ) -> TalentCloudGrant | None:
        # The read before the insert misses the other session's row, as it would have
        # before that session committed; the read after the refusal finds it.
        if next(misses, False):
            return None
        return real_live(self, user_id, company_id)

    monkeypatch.setattr(TalentCloudService, "_live", live_missing_once)
    loser, mail = cloud.grant(acme, admin_id)
    assert loser.id == winner.id and mail is None
    rows = clean.scalars(select(TalentCloudGrant).where(TalentCloudGrant.company_id == acme))
    assert len(rows.all()) == 1


def test_a_refused_insert_that_is_not_the_race_is_not_swallowed(
    clean: Session, settings: Settings
) -> None:
    """Only the live-grant index is the other click: any other refusal (here an admin
    that is not a `users` row) still raises."""
    an_admin(clean)
    acme = _request(clean)
    with pytest.raises(IntegrityError):
        TalentCloudService(clean, settings).grant(acme, uuid4())
    clean.rollback()
    assert TalentCloudService(clean, settings).for_company(acme) is None


# ---- the cloud itself (REB-519, spec § 4.2) ------------------------------------------------

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
NOW = datetime.now(UTC)
SUMMARY = "Un'azienda di logistica rifà il gestionale degli ordini, backend in Python."


@pytest.fixture
def talents(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in (
        "team_request_talents",
        "team_requests",
        "team_proposals",
        "talent_cloud_grants",
        "admin_actions",
        "notes",
        "freelancers",
        "companies",
        "users",
    ):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        self.calls.append((event, properties))


def _talent(
    session: Session,
    last_name: str,
    *,
    nome: str = "Ada",
    card: dict[str, Any] | None = None,
    remote: str | None = "remote",
    rate: Decimal | None = Decimal("450.00"),
    status: str = "new",
    vetted: bool = False,
    deleted: bool = False,
    with_card: bool = True,
    cv: bool = True,
    note: str | None = "Chiede di essere richiamata dopo le sei.",
) -> UUID:
    """A freelancer with a CV and an anonymous card, the card `CARD` with `card` over it;
    the talent's own note, rate and state are there so a read that leaked them fails."""
    slug = last_name.lower()
    user = User(
        email=f"{slug}@studio.it",
        nome=nome,
        last_name=last_name,
        linkedin_url=f"https://www.linkedin.com/in/{slug}",
        phone="+39 333 1112233",
    )
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remote=remote,
        daily_rate=rate,
        status=status,
        posizione="Backend developer",
        links=[f"https://{slug}.dev"],
        cv_bytes=PDF if cv else None,
        cv_filename=f"CV {last_name}.pdf" if cv else None,
        cv_mime="application/pdf" if cv else None,
        cv_size=len(PDF) if cv else None,
        vetted_at=NOW if vetted else None,
        deleted_at=NOW if deleted else None,
    )
    session.add(row)
    session.flush()
    if note:
        session.add(
            Note(entity_type="freelancer", entity_id=row.id, text=note, author="Ivan", source="web")
        )
    if with_card:
        session.add(
            FreelancerCard(
                freelancer_id=row.id,
                cv_sha256="0" * 64,
                card={**CARD, **(card or {})},
                model=MODEL,
                input_tokens=1200,
                output_tokens=180,
                generated_at=NOW,
            )
        )
    session.commit()
    return row.id


def _cloud_proposal(
    session: Session,
    members: list[UUID],
    *,
    origin: str = "cloud",
    user_id: UUID | None = None,
    created_at: datetime | None = None,
) -> UUID:
    row = TeamProposal(
        description="Rifacciamo il gestionale degli ordini, sei mesi, da remoto, in Python.",
        summary=SUMMARY,
        location={"locale": False, "dove": None},
        team=[
            {
                "posizione": index,
                "freelancer_id": str(freelancer_id),
                "role": "Backend developer",
                "reasoning": "Nove anni di API in Python.",
                "week_days": 5,
            }
            for index, freelancer_id in enumerate(members, start=1)
        ],
        economics={"day": None, "month": None, "days_month": 22},
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=0,
        origin=origin,
        user_id=user_id,
        created_at=created_at or datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(row)
    session.commit()
    return row.id


def _ids(service: CloudTalentService, **filters: Any) -> set[UUID]:
    return {talent.freelancer_id for talent in service.list(CloudTalentQuery(**filters)).items}


def test_the_cloud_shows_every_visible_talent_by_name_vetted_first(talents: Session) -> None:
    rossi = _talent(talents, "Rossi", nome="Mario")
    bianchi = _talent(talents, "Bianchi", nome="Luca", vetted=True)
    verdi = _talent(talents, "Verdi", nome="Anna", vetted=True)
    abate = _talent(talents, "Abate", nome="Carla", remote=None, rate=None, cv=False)
    # Outside `cloud_visible`: turned down, deleted, no card, a card retired, and a stored
    # card that is an object but not a card any more.
    _talent(talents, "Scartato", status="discarded")
    _talent(talents, "Cancellato", deleted=True)
    _talent(talents, "Senzascheda", with_card=False)
    retired = _talent(talents, "Ritirata")
    talents.get_one(FreelancerCard, retired).card = None
    broken = _talent(talents, "Rotta")
    talents.get_one(FreelancerCard, broken).card = {"role": "Backend developer"}
    talents.commit()

    listed = CloudTalentService(talents).list(CloudTalentQuery())

    assert [talent.freelancer_id for talent in listed.items] == [bianchi, verdi, abate, rossi]
    assert listed.capped is False
    first = listed.items[0]
    assert (first.nome, first.last_name, first.vetted) == ("Luca", "Bianchi", True)
    assert first.linkedin_url == "https://www.linkedin.com/in/bianchi"
    assert first.links == ["https://bianchi.dev"]
    assert first.card.role == CARD["role"] and first.card.profile_summary == CARD["profile_summary"]
    assert first.card.skills == CARD["skills"]
    # The card's place is the engine's: the cloud shows the CV itself, not Claude's
    # reading of it.
    assert first.card.location is None
    assert (first.mode, first.band, first.ha_cv) == ("remote", Band(min=500, max=650), True)
    carla = listed.items[2]
    assert (carla.vetted, carla.mode, carla.band, carla.ha_cv) == (False, None, None, False)


def test_the_read_carries_no_rate_state_or_note(talents: Session) -> None:
    _talent(
        talents,
        "Rossi",
        rate=Decimal("463.21"),
        status="active",
        note="Nota riservata: vuole 463,21 e non un euro meno.",
    )

    [read] = CloudTalentService(talents).list(CloudTalentQuery()).items

    assert set(CloudTalentRead.model_fields).isdisjoint(
        {"daily_rate", "status", "note", "commenti", "email", "phone", "cv_filename"}
    )
    dumped = read.model_dump_json()
    for secret in ("463.21", "463,21", "active", "Nota riservata", "@studio.it", "+39"):
        assert secret not in dumped, secret
    assert json.loads(dumped)["band"] == {"min": 500, "max": 650}  # 463.21 x 1.4 = 648.49


def test_the_filters_narrow_the_cloud(talents: Session) -> None:
    backend = _talent(
        talents,
        "Rossi",
        card={
            "role": "Backend developer",
            "seniority": "senior",
            "skills": ["Python", "FastAPI"],
        },
        remote="remote",
        rate=Decimal("450.00"),  # 630: 500–650
    )
    frontend = _talent(
        talents,
        "Bianchi",
        card={
            "role": "Frontend developer",
            "seniority": "mid",
            "skills": ["React", "TypeScript"],
        },
        remote="hybrid",
        rate=Decimal("250.00"),  # 350: 300–400
    )
    lead = _talent(
        talents,
        "Verdi",
        card={
            "role": "Tech lead",
            "seniority": "lead",
            "skills": ["Go", "Kubernetes", "50%_off"],
        },
        remote="on_site",
        rate=Decimal("700.00"),  # 980: oltre 800
    )
    unrated = _talent(
        talents,
        "Neri",
        card={"role": "Backend developer", "seniority": "junior", "skills": ["python"]},
        remote=None,
        rate=None,
    )
    service = CloudTalentService(talents)
    everyone = {backend, frontend, lead, unrated}

    assert _ids(service) == everyone
    assert _ids(service, role="", skill="   ") == everyone  # blank narrows nothing
    # The role is one of the cards' own, matched whole.
    assert _ids(service, role="Backend developer") == {backend, unrated}
    assert _ids(service, role="Backend") == set()
    assert _ids(service, seniority="mid") == {frontend}
    # One skill, inside any of the card's skills, whatever the case; `%` and `_` are
    # the characters they are, not a pattern.
    assert _ids(service, skill="PYTH") == {backend, unrated}
    assert _ids(service, skill="script") == {frontend}
    assert _ids(service, skill="%") == {lead}
    assert _ids(service, skill="_") == {lead}
    assert _ids(service, skill="Go Kubernetes") == set()
    assert _ids(service, mode="hybrid") == {frontend}
    # A band: its bottom at or above `min_band`, its top at or below `max_band`; a
    # talent with no rate has no band, and no band filter finds them.
    assert _ids(service, min_band=500, max_band=650) == {backend}
    assert _ids(service, min_band=800) == {lead}
    assert _ids(service, max_band=400) == {frontend}
    assert _ids(service, min_band=450) == {backend, lead}
    assert _ids(service, min_band=0) == {backend, frontend, lead}
    assert _ids(service, min_band=900) == set()
    assert _ids(service, max_band=200) == set()
    assert _ids(
        service,
        role="Backend developer",
        seniority="senior",
        skill="fast",
        mode="remote",
        min_band=500,
        max_band=650,
    ) == {backend}


def test_the_admins_level_beats_the_cards_in_the_cloud_and_its_filter(talents: Session) -> None:
    """REB-799: the seniority filter and the card the cloud shows read the admin's word
    over Claude's; a card with no dated role shows its years as null and its seniority
    uncertain, and the admin's seniority makes it certain again."""
    corrected = _talent(talents, "Rossi", card={"seniority": "lead"})
    talents.get_one(Freelancer, corrected).seniority_admin = "junior"
    talents.get_one(Freelancer, corrected).experience_years_admin = 2
    undated = _talent(talents, "Bianchi", card={"seniority": "mid", "experience_years": None})
    talents.commit()
    service = CloudTalentService(talents)

    assert _ids(service, seniority="junior") == {corrected}
    assert _ids(service, seniority="lead") == set()
    assert _ids(service, seniority="mid") == {undated}
    cards = {talent.freelancer_id: talent.card for talent in service.list(CloudTalentQuery()).items}
    assert (cards[corrected].seniority, cards[corrected].experience_years) == ("junior", 2)
    assert cards[corrected].seniority_uncertain is False
    assert (cards[undated].seniority, cards[undated].experience_years) == ("mid", None)
    assert cards[undated].seniority_uncertain is True

    talents.get_one(Freelancer, undated).seniority_admin = "senior"
    talents.commit()
    [after] = [
        talent.card
        for talent in service.list(CloudTalentQuery()).items
        if talent.freelancer_id == undated
    ]
    assert (after.seniority, after.seniority_uncertain) == ("senior", False)


@pytest.mark.parametrize(("field", "value"), [("seniority", "guru"), ("mode", "luna")])
def test_a_filter_word_the_cloud_does_not_know_is_refused(
    talents: Session, field: str, value: str
) -> None:
    with pytest.raises(ValidationFailed) as refused:
        CloudTalentService(talents).list(CloudTalentQuery(**{field: value}))
    assert refused.value.details["field"] == field


def test_the_roles_are_every_card_role_once_sorted(talents: Session) -> None:
    """The role filter's choices: each `role` of the cloud's cards once, sorted the way
    a person reads them, whatever the other filters narrowed the list to."""
    _talent(talents, "Rossi", card={"role": "Backend developer"})
    _talent(talents, "Bianchi", card={"role": "Backend developer"})
    _talent(talents, "Verdi", card={"role": "Designer"})
    _talent(talents, "Gialli", card={"role": "analista dati"})
    _talent(talents, "Neri", card={"role": "UX researcher"}, status="discarded")

    listed = CloudTalentService(talents).list(CloudTalentQuery(role="Designer"))

    assert len(listed.items) == 1
    assert listed.roles == ["analista dati", "Backend developer", "Designer"]


def _insert_talents(
    session: Session,
    count: int,
    *,
    start: int = 0,
    vetted: int | None = None,
    card: Callable[[int], dict[str, Any]] = lambda n: CARD,
) -> list[UUID]:
    """`count` talents in three inserts, `Cognome{start:03}` on, each with the card
    `card(n)`; the `vetted`-th is vetted, so the order puts it first."""
    numbers = range(start, start + count)
    users = [
        {
            "id": uuid4(),
            "email": f"talento{n:03}@studio.it",
            "nome": "Ada",
            "last_name": f"Cognome{n:03}",
        }
        for n in numbers
    ]
    session.execute(insert(User), users)
    freelancers = [
        {
            "id": uuid4(),
            "user_id": user["id"],
            "status": "new",
            "links": [],
            "compilata_da": "person",
            "remote": "remote",
            "daily_rate": Decimal("450.00"),
            "vetted_at": NOW if n == vetted else None,
        }
        for n, user in zip(numbers, users, strict=True)
    ]
    session.execute(insert(Freelancer), freelancers)
    session.execute(
        insert(FreelancerCard),
        [
            {
                "freelancer_id": row["id"],
                "cv_sha256": "0" * 64,
                "card": card(n),
                "model": MODEL,
                "generated_at": NOW,
            }
            for n, row in zip(numbers, freelancers, strict=True)
        ],
    )
    session.commit()
    return [row["id"] for row in freelancers]


def test_the_cloud_shows_at_most_200_and_says_so(talents: Session) -> None:
    assert CLOUD_LIST_CAP == 200
    count = CLOUD_LIST_CAP + 1
    # The last by name is vetted, so the cap keeps it first.
    _insert_talents(
        talents,
        count,
        vetted=count - 1,
        card=lambda n: {**CARD, "seniority": "lead" if n < 3 else "senior"},
    )
    service = CloudTalentService(talents)

    listed = service.list(CloudTalentQuery())

    assert len(listed.items) == CLOUD_LIST_CAP and listed.capped is True
    assert listed.items[0].last_name == f"Cognome{count - 1:03}"
    assert [talent.last_name for talent in listed.items[1:3]] == ["Cognome000", "Cognome001"]
    narrowed = service.list(CloudTalentQuery(seniority="lead"))
    assert len(narrowed.items) == 3 and narrowed.capped is False


def test_a_card_that_is_not_a_card_takes_no_place_under_the_cap(talents: Session) -> None:
    """The cap counts the talents the cloud shows: a stored card that no longer
    validates, first in the order, is left out and the 200 valid talents after it are
    all there; the list says it was cut only once a valid talent is left over."""
    ids = _insert_talents(talents, CLOUD_LIST_CAP + 1, vetted=0)
    talents.get_one(FreelancerCard, ids[0]).card = {"role": "Backend developer"}
    talents.commit()
    service = CloudTalentService(talents)

    listed = service.list(CloudTalentQuery())

    assert [talent.freelancer_id for talent in listed.items] == ids[1:]
    assert listed.capped is False
    _insert_talents(talents, 1, start=CLOUD_LIST_CAP + 1)
    again = service.list(CloudTalentQuery())
    assert [talent.freelancer_id for talent in again.items] == ids[1:]
    assert again.capped is True


def test_a_card_whose_skills_are_not_a_list_leaves_the_skill_filter_working(
    talents: Session,
) -> None:
    """A card written by hand with `skills` a string or an object: the skill filter
    expands only an array, so it finds the others and leaves these out, and the list
    leaves them out too, since they are not cards."""
    python = _talent(talents, "Rossi", card={"skills": ["Python", "FastAPI"]})
    for last_name, skills in (("Rotta", "Python"), ("Storta", {"linguaggio": "Python"})):
        broken = _talent(talents, last_name)
        talents.execute(
            text(
                "UPDATE freelancer_cards SET card = jsonb_set(card, '{competenze}', "
                "CAST(:competenze AS JSONB)) WHERE freelancer_id = :id"
            ),
            {"skills": json.dumps(skills), "id": broken},
        )
    talents.commit()
    service = CloudTalentService(talents)

    assert _ids(service, skill="pyth") == {python}
    assert _ids(service) == {python}


def test_the_cv_is_open_only_inside_the_cloud(talents: Session) -> None:
    visible = _talent(talents, "Rossi")
    outside = [
        _talent(talents, "Bianchi", status="discarded"),
        _talent(talents, "Verdi", deleted=True),
        _talent(talents, "Neri", with_card=False),
        _talent(talents, "Gialli", cv=False),
        uuid4(),
    ]
    service = CloudTalentService(talents)

    cv = service.cv(visible)

    assert (cv.filename, cv.mime, cv.content) == ("CV Rossi.pdf", "application/pdf", PDF)
    for freelancer_id in outside:
        with pytest.raises(NotFound) as refused:
            service.cv(freelancer_id)
        assert refused.value.message == NOT_IN_THE_CLOUD == "Profilo non disponibile."


def test_the_caller_is_the_newest_grants_company_and_the_users_phone(
    talents: Session, settings: Settings
) -> None:
    admin_id = an_admin(talents)
    acme = _request(talents)
    user_id = talents.scalar(select(User.id).where(User.email == "wile@acme.it"))
    assert user_id is not None
    service = CloudTalentService(talents)
    assert service.caller(user_id) is None

    TalentCloudService(talents, settings).grant(acme, admin_id)
    bianchi = _request(talents, company="Bianchi Srl")
    newest, _ = TalentCloudService(talents, settings).grant(bianchi, admin_id)
    caller = service.caller(user_id)

    assert caller is not None
    assert (caller.user_id, caller.email, caller.phone) == (
        user_id,
        "wile@acme.it",
        "+39 345 1234567",
    )
    assert (caller.company_id, caller.company) == (bianchi, "Bianchi Srl")
    assert caller.granted_at == newest.granted_at
    TalentCloudService(talents, settings).revoke(bianchi, admin_id)
    again = service.caller(user_id)
    assert again is not None and (again.company_id, again.company) == (acme, "Acme S.r.l.")


def _open_cloud(session: Session, settings: Settings) -> tuple[UUID, UUID, UUID]:
    """The admin, the referente and the company of a live grant, opened an hour ago so
    the proposals the tests write (five minutes old) come after it."""
    admin_id = an_admin(session)
    acme = _request(session)
    grant, _ = TalentCloudService(session, settings).grant(acme, admin_id)
    session.get_one(TalentCloudGrant, grant.id).granted_at = NOW - timedelta(hours=1)
    session.commit()
    user_id = session.scalar(select(User.id).where(User.email == "wile@acme.it"))
    assert user_id is not None
    return admin_id, user_id, acme


def test_a_card_files_a_request_for_that_talent_alone(talents: Session, settings: Settings) -> None:
    ada = _talent(talents, "Lovelace", nome="Ada", card={"role": "Data engineer"})
    _, user_id, acme = _open_cloud(talents, settings)
    caller = CloudTalentService(talents).caller(user_id)
    assert caller is not None
    capture = FakeCapture()
    service = TeamRequestService(talents, settings=settings, tracker=Tracker(capture))

    read = service.create_for_talent(
        ada,
        company=caller.company,
        email=caller.email,
        phone=caller.phone,
        user_id=caller.user_id,
        company_id=caller.company_id,
    )

    assert (read.origin, read.status) == ("cloud", "new")
    assert read.proposal is None and read.summary is None and read.description is None
    assert (read.company, read.email, read.phone) == (
        "Acme S.r.l.",
        "wile@acme.it",
        "+39 345 1234567",
    )
    assert (read.user_id, read.company_id) == (user_id, acme)
    assert [(talent.freelancer_id, talent.role) for talent in read.talents] == [
        (ada, "Data engineer")
    ]
    assert capture.calls == [
        (TEAM_REQUEST_SENT, {"origin": "cloud", "$process_person_profile": False})
    ]
    mail = service.request_mail(read)
    assert mail.subject == "Nuova richiesta team da Acme S.r.l."
    assert "Ada Lovelace" in mail.text

    # A referente with no phone on file files all the same, with none.
    no_phone = service.create_for_talent(
        ada,
        company=caller.company,
        email=caller.email,
        phone=None,
        user_id=caller.user_id,
        company_id=caller.company_id,
    )
    assert no_phone.phone is None and no_phone.id != read.id

    # Only a talent the cloud shows can be asked for, with the CV route's sentence.
    for outside in (_talent(talents, "Scartata", status="discarded"), uuid4()):
        with pytest.raises(NotFound) as refused:
            service.create_for_talent(
                outside,
                company=caller.company,
                email=caller.email,
                phone=None,
                user_id=caller.user_id,
                company_id=caller.company_id,
            )
        assert refused.value.message == NOT_IN_THE_CLOUD
    assert len(talents.scalars(select(TeamRequest)).all()) == 2


def test_the_builder_files_its_own_cloud_proposal_at_once(
    talents: Session, settings: Settings
) -> None:
    ada = _talent(talents, "Lovelace", nome="Ada")
    admin_id, user_id, acme = _open_cloud(talents, settings)
    own = _cloud_proposal(talents, [ada], user_id=user_id)
    someone_elses = _cloud_proposal(talents, [ada], user_id=admin_id)
    public = _cloud_proposal(talents, [ada], origin="public")
    old = _cloud_proposal(
        talents, [ada], user_id=user_id, created_at=datetime.now(UTC) - timedelta(days=1, minutes=1)
    )
    service = TeamRequestService(talents, settings=settings)
    caller = CloudTalentService(talents).caller(user_id)
    assert caller is not None

    def hire(proposal_id: UUID) -> Any:
        return service.create_in_cloud(proposal_id, caller=caller)

    read, mail = hire(own)

    assert (read.origin, read.phone, read.user_id, read.company_id) == (
        "cloud",
        "+39 345 1234567",
        user_id,
        acme,
    )
    assert read.proposal is not None and read.proposal.id == own
    assert read.summary == SUMMARY
    assert [talent.freelancer_id for talent in read.talents] == [ada]
    assert mail.subject == "Nuova richiesta team da Acme S.r.l." and SUMMARY in mail.text
    stored = talents.get_one(TeamRequest, read.id)
    assert (stored.origin, stored.phone) == ("cloud", "+39 345 1234567")

    # Another person's cloud proposal, a public one, or one older than a day: the one
    # sentence of every refusal.
    for proposal_id in (someone_elses, public, old):
        with pytest.raises(ValidationFailed) as refused:
            hire(proposal_id)
        assert refused.value.details["reason"] == PROPOSAL_REFUSED
    # A second «Assumi team» on the same proposal is the public page's 409.
    with pytest.raises(InvalidState):
        hire(own)
    assert talents.scalars(select(TeamRequestTalent.request_id)).all() == [read.id]
