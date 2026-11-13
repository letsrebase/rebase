"""Merging one person into another, and putting it back (REB-800): every relation that
belongs to a person moves, by id; nothing is deleted; the newest CV stays with its card;
the refusals say why; the undo gives every row back."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_crm_door import FakeCrmDoor
from fakes_pigro import PIGRO, TOKEN
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.db import Base
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.merge import PersonMergeService
from rebase_core.merge_relations import LEFT_IN_PLACE, RELATIONS
from rebase_core.models import (
    AdminAction,
    Campaign,
    CampaignOptout,
    CampaignRecipient,
    Company,
    ContractDocument,
    Freelancer,
    FreelancerCard,
    FreelancerFiscal,
    FreelancerSource,
    GuideDownload,
    HubEngagement,
    Login,
    MagicLinkToken,
    Match,
    Note,
    Referral,
    TalentCloudGrant,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
    UserContactPoint,
    UserSession,
)
from rebase_core.pigro_door import FreelancerDoor
from rebase_core.users import UserService

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
OLD_PDF = PDF + b"%old"

TABLES = (
    "admin_actions",
    "notes",
    "referral_rewards",
    "referrals",
    "campaign_recipients",
    "campaigns",
    "team_request_talents",
    "team_requests",
    "team_proposals",
    "talent_cloud_grants",
    "contract_documents",
    "hub_engagements",
    "matches",
    "freelancer_fiscal",
    "freelancer_cards",
    "companies",
    "logins",
    "sessions",
    "magic_link_tokens",
    "guide_downloads",
    "freelancers",
    "users",
    "signups",
)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


@pytest.fixture
def settings(hub_engine: Engine) -> Settings:
    return Settings(
        database_url=hub_engine.url.render_as_string(hide_password=False),
        hub_url="http://localhost:5180/hub",
        _env_file=None,  # type: ignore[call-arg]
    )


def _user(session: Session, email: str, **extra: Any) -> User:
    phone = extra.pop("phone", None)
    user = User(
        email=email, first_name=extra.pop("first_name", "Ada"), last_name="Lovelace", **extra
    )
    session.add(user)
    session.flush()
    if phone is not None:
        _phone(session, user, phone)
    return user


def _phone(session: Session, user: User, number: str, *, primary: bool = True) -> UserContactPoint:
    point = UserContactPoint(
        user_id=user.id, kind="phone", value=number, label="work", is_primary=primary
    )
    session.add(point)
    session.flush()
    session.expire(user, ["contact_points"])
    return point


def _card(
    session: Session,
    user: User,
    *,
    cv: bytes | None = PDF,
    uploaded: datetime | None = NOW,
    created: datetime = NOW,
    **extra: Any,
) -> Freelancer:
    row = Freelancer(user_id=user.id, created_at=created, **extra)
    if cv is not None:
        row.cv_bytes, row.cv_filename, row.cv_mime = cv, "cv.pdf", "application/pdf"
        row.cv_size, row.cv_uploaded_at = len(cv), uploaded
    session.add(row)
    session.flush()
    return row


def _admin(session: Session) -> User:
    return _user(session, "admin@rebase.it", first_name="Root", role="admin")


def _company(session: Session, user: User) -> Company:
    row = Company(
        user_id=user.id,
        company_name="Acme",
        requested_role="Backend",
        project="Un progetto",
        period_from=date(2026, 11, 1),
        duration="3 mesi",
        daily_budget=Decimal("500"),
        remote="remote",
        headcount=1,
    )
    session.add(row)
    session.flush()
    return row


def _match(session: Session, card: Freelancer, company: Company, admin: User, status: str) -> Match:
    row = Match(
        freelancer_id=card.id,
        company_id=company.id,
        customer_legal_name="Acme srl",
        customer_vat_number="01234567890",
        customer_registered_office="Milano",
        status=status,
        created_by=admin.id,
    )
    session.add(row)
    session.flush()
    return row


def _framework(session: Session, card: Freelancer, admin: User, status: str = "signed") -> None:
    session.add(
        ContractDocument(
            kind="framework",
            freelancer_id=card.id,
            text_version="1",
            pdf=PDF,
            status=status,
            created_by=admin.id,
        )
    )
    session.flush()


def _pair(session: Session) -> tuple[Freelancer, Freelancer, User, User, User]:
    """A, the survivor (the newer CV, no card), B (older CV), and an admin."""
    admin = _admin(session)
    a_user = _user(session, "nuova@studio.it", linkedin_url="https://www.linkedin.com/in/ada")
    b_user = _user(session, "vecchia@studio.it")
    a = _card(session, a_user, cv=PDF, uploaded=NOW, created=NOW)
    b = _card(
        session,
        b_user,
        cv=OLD_PDF,
        uploaded=NOW - timedelta(days=40),
        created=NOW - timedelta(days=40),
    )
    session.commit()
    return a, b, a_user, b_user, admin


def _merges(session: Session, crm: FakeCrmDoor | None = None) -> PersonMergeService:
    """The service over a fake CRM door that finds no conflict unless `crm` says one."""
    return PersonMergeService(session, (crm or FakeCrmDoor()).door())


def _everything_of(session: Session, b: Freelancer, b_user: User, admin: User) -> dict[str, UUID]:
    """One row of every relation, hung on B and on B's person."""
    other_user = _user(session, "altro@studio.it", first_name="Altro")
    other = _card(session, other_user, cv=None, uploaded=None)
    company = _company(session, b_user)
    ids: dict[str, UUID] = {"companies": company.id}
    for key, row in (
        (
            "magic_links",
            MagicLinkToken(
                user_id=b_user.id, token_hash="a" * 64, expires_at=NOW + timedelta(days=1)
            ),
        ),
        ("logins", Login(user_id=b_user.id)),
        ("guide_downloads", GuideDownload(user_id=b_user.id)),
        (
            "sessions",
            UserSession(user_id=b_user.id, token_hash="b" * 64, expires_at=NOW + timedelta(days=1)),
        ),
        (
            "team_proposals",
            TeamProposal(
                description="d",
                summary="r",
                location={},
                team=[],
                economics={},
                model="m",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                origin="cloud",
                user_id=b_user.id,
            ),
        ),
        (
            "team_requests",
            TeamRequest(origin="cloud", company="Acme", email="a@b.it", user_id=b_user.id),
        ),
        (
            "cloud_grants",
            TalentCloudGrant(user_id=b_user.id, company_id=company.id, granted_by=admin.id),
        ),
        (
            "referrals_made",
            Referral(referrer_user_id=b_user.id, kind="freelancer", entity_id=other.id, code="ABC"),
        ),
        (
            "referral_received",
            Referral(referrer_user_id=other_user.id, kind="freelancer", entity_id=b.id, code="DEF"),
        ),
        ("notes", Note(entity_type="freelancer", entity_id=b.id, text="t", author="x")),
        (
            "audit",
            AdminAction(entity_type="freelancer", entity_id=b.id, kind="vetted", admin_id=admin.id),
        ),
        (
            "fiscal",
            FreelancerFiscal(
                freelancer_id=b.id,
                tax_code="RSSMRA80A01H501U",
                domicile="Via Roma 1",
                updated_by=admin.id,
            ),
        ),
    ):
        session.add(row)
        session.flush()
        ids[key] = row.id
    ids["matches"] = _match(session, b, company, admin, "draft").id
    engagement = HubEngagement(
        freelancer_id=b.id, company_id=company.id, state="active", sequence=1
    )
    session.add(engagement)
    session.flush()
    ids["hub_engagements"] = engagement.id
    session.add(
        ContractDocument(
            kind="framework",
            freelancer_id=b.id,
            text_version="1",
            pdf=PDF,
            status="cancelled",
            created_by=admin.id,
        )
    )
    session.add(
        Campaign(
            created_by=admin.id,
            name="c",
            slug="c",
            source="status",
            journey_status="card_empty_new",
            subject="o",
            text="t",
            button_text="b",
            button_meta="area",
            action="signed_in",
            content_at=NOW,
        )
    )
    session.flush()
    campaign = session.scalars(select(Campaign)).one()
    for key, kwargs in (
        (
            "campaign_recipients_user",
            {"user_id": b_user.id, "email": "u@b.it", "code": "AAAAAAAA"},
        ),
        (
            "campaign_recipients_card",
            {"freelancer_id": b.id, "email": "f@b.it", "code": "BBBBBBBB"},
        ),
    ):
        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            kind="freelancer",
            unsubscribe_token=kwargs["email"] * 3,
            **kwargs,
        )
        session.add(recipient)
        session.flush()
        ids[key] = recipient.id
    request = session.scalars(select(TeamRequest)).one()
    talent = TeamRequestTalent(request_id=request.id, freelancer_id=b.id, role="Backend")
    session.add(talent)
    document = session.scalars(
        select(ContractDocument).where(ContractDocument.freelancer_id == b.id)
    ).one()
    session.flush()
    ids["team_request_talents"] = talent.id
    ids["contract_documents"] = document.id
    session.commit()
    return ids


def test_every_relation_of_the_absorbed_person_moves_to_the_survivor(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    ids = _everything_of(clean, b, b_user, admin)
    assert set(ids) == {relation.key for relation in RELATIONS}, (
        "a relation has no row in this test"
    )

    done = _merges(clean).merge(a.id, b.id, admin.id)

    assert done.moved == dict.fromkeys(ids, 1) and done.left_behind == {}
    for relation in RELATIONS:
        model = relation.column.class_
        holder = clean.scalar(
            select(relation.column)
            .where(model.id == ids[relation.key])
            .execution_options(populate_existing=True)
        )
        expected = a_user.id if relation.scope == "user" else a.id
        if relation.key in ("referrals_made",):
            expected = a_user.id
        assert holder == expected, relation.key


def test_the_absorbed_card_is_soft_deleted_and_points_at_the_survivor(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    done = _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert clean.get(Freelancer, b.id).deleted_at is not None  # type: ignore[union-attr]
    assert clean.get(User, b_user.id).merged_into_id == a_user.id  # type: ignore[union-attr]
    assert clean.get(User, b_user.id).email == "vecchia@studio.it"  # type: ignore[union-attr]
    merged = clean.get(AdminAction, done.action_id)
    assert merged is not None and merged.kind == "merged" and merged.entity_id == b.id
    assert merged.payload["into_freelancer_id"] == str(a.id)
    absorbed = clean.scalars(
        select(AdminAction).where(AdminAction.entity_id == a.id, AdminAction.kind == "absorbed")
    ).one()
    assert absorbed.payload["from_freelancer_id"] == str(b.id)
    thread = clean.scalars(select(Note).where(Note.entity_id == a.id)).all()
    assert any("vecchia@studio.it" in c.text for c in thread)
    # Nothing was deleted: the row, its CV and its address are all still there.
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]


def test_the_older_card_cannot_be_restored_by_itself_once_merged(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    _merges(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(InvalidState, match="unita a un'altra"):
        FreelancerService(clean).restore(b.id, admin.id)


def test_the_newest_cv_stays_and_the_older_file_stays_reachable_on_the_absorbed_card(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    done = _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert done.cv == "kept"
    assert clean.get(Freelancer, a.id).cv_bytes == PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]


def test_when_the_absorbed_cv_is_newer_the_cv_and_its_card_swap(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at = NOW - timedelta(days=90)
    b.cv_uploaded_at = NOW
    clean.add(FreelancerCard(freelancer_id=b.id, cv_sha256="b" * 64, card={"role": "Dev"}))
    clean.add(FreelancerCard(freelancer_id=a.id, cv_sha256="a" * 64, card={"role": "Old"}))
    clean.commit()

    done = _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()

    assert done.cv == "absorbed"
    survivor, absorbed = clean.get(Freelancer, a.id), clean.get(Freelancer, b.id)
    assert survivor is not None and absorbed is not None
    assert (survivor.cv_bytes, absorbed.cv_bytes) == (OLD_PDF, PDF)
    assert clean.get(FreelancerCard, a.id).card == {"role": "Dev"}  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, b.id).card == {"role": "Old"}  # type: ignore[union-attr]
    # The survivor's card describes a CV that is not the one it holds now: flagged.
    assert done.card_to_regenerate is True


def test_a_survivor_with_a_cv_and_no_card_is_flagged_and_a_failure_is_cleared(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    clean.add(
        FreelancerCard(
            freelancer_id=a.id, error="La risposta non era una scheda", error_cv_sha256="c" * 64
        )
    )
    clean.commit()
    done = _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    card = clean.get(FreelancerCard, a.id)
    assert done.card_to_regenerate is True
    assert card is not None and (card.error, card.error_cv_sha256) == (None, None)


def test_the_profile_the_survivor_leaves_empty_is_filled_and_the_first_attribution_stays(
    clean: Session,
) -> None:
    admin = _admin(clean)
    a_user = _user(clean, "nuova@studio.it", linkedin_url=None)
    b_user = _user(
        clean, "vecchia@studio.it", linkedin_url="https://www.linkedin.com/in/ada", phone="333"
    )
    a = _card(clean, a_user, created=NOW, job_title=None, utm_source=None)
    b = _card(
        clean,
        b_user,
        created=NOW - timedelta(days=40),
        uploaded=NOW - timedelta(days=40),
        job_title="Backend",
        daily_rate=Decimal("400"),
        utm_source="linkedin",
        status="contacted",
    )
    clean.commit()
    _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    survivor = clean.get(Freelancer, a.id)
    assert survivor is not None
    assert (survivor.job_title, survivor.daily_rate) == ("Backend", Decimal("400.00"))
    assert (survivor.utm_source, survivor.status) == ("linkedin", "contacted")
    user = clean.get(User, a_user.id)
    assert user is not None
    assert (user.linkedin_url, user.phone) == ("https://www.linkedin.com/in/ada", "333")
    # The absorbed address gave its profile up: one holder per profile.
    assert clean.get(User, b_user.id).linkedin_url is None  # type: ignore[union-attr]


def test_the_absorbed_address_still_logs_in_as_the_survivor_and_the_wizard_finds_them(
    clean: Session, settings: Settings
) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    _merges(clean).merge(a.id, b.id, admin.id)
    users = UserService(clean, settings)
    mail = users.request_link("vecchia@studio.it")
    assert mail is not None and mail.to == "vecchia@studio.it"
    token = clean.scalars(select(MagicLinkToken)).one()
    assert token.user_id == a_user.id
    read, created = FreelancerService(clean).apply(
        FreelancerCreate(
            first_name="Ada",
            last_name="Lovelace",
            email="vecchia@studio.it",
            daily_rate=Decimal("450"),
            job_title="Backend",
            remote="remote",
        )
    )
    assert (read.id, created) == (a.id, False)


def test_a_referral_the_survivor_already_has_stays_with_the_absorbed_card(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    referrer = _user(clean, "r@studio.it", first_name="Rita")
    own = Referral(referrer_user_id=referrer.id, kind="freelancer", entity_id=a.id, code="AAA")
    theirs = Referral(referrer_user_id=referrer.id, kind="freelancer", entity_id=b.id, code="BBB")
    clean.add_all([own, theirs])
    clean.commit()
    done = _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert done.left_behind == {"referral_received": 1} and done.moved == {}
    assert clean.get(Referral, theirs.id).entity_id == b.id  # type: ignore[union-attr]


def test_a_talent_already_on_the_same_team_request_is_not_moved_twice(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    request = TeamRequest(origin="public", company="Acme", email="a@b.it")
    clean.add(request)
    clean.flush()
    clean.add_all(
        [
            TeamRequestTalent(request_id=request.id, freelancer_id=a.id, role="Backend"),
            TeamRequestTalent(request_id=request.id, freelancer_id=b.id, role="Backend"),
        ]
    )
    clean.commit()
    done = _merges(clean).merge(a.id, b.id, admin.id)
    assert done.left_behind == {"team_request_talents": 1}


def _conflicts(a: Freelancer, b: Freelancer, **answer: object) -> FakeCrmDoor:
    """A CRM that says `answer` about the pair (the keys of its merge check), both ways."""
    said = {"framework_regimes": [], "shared_clients": 0, "tax_code_conflict": False, **answer}
    return FakeCrmDoor(merge={(a.id, b.id): said, (b.id, a.id): said})


def test_refused_when_the_crm_says_both_have_a_live_framework_agreement_of_the_same_type(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    crm = _conflicts(a, b, framework_regimes=["ordinary"])
    with pytest.raises(
        InvalidState, match=r"contratto quadro in corso o firmato \(vat_registered\)"
    ):
        _merges(clean, crm).merge(a.id, b.id, admin.id)
    assert clean.get(Freelancer, b.id).deleted_at is None  # type: ignore[union-attr]
    assert crm.calls == [("GET", f"/api/rebase/freelancers/{a.id}/merge-check?with={b.id}")]


def test_the_hubs_own_contract_tables_are_no_longer_the_merges_check(clean: Session) -> None:
    """The contracts and engagements are the CRM's: two live local framework agreements and
    an active local match with the same company, which the check used to refuse, are now
    for the removal card to drop, and a CRM that finds nothing lets the merge through and
    moves them with the card."""
    a, b, a_user, _, admin = _pair(clean)
    _framework(clean, a, admin, "signed")
    _framework(clean, b, admin, "sent")
    company = _company(clean, a_user)
    _match(clean, a, company, admin, "active")
    _match(clean, b, company, admin, "awaiting_signature")
    clean.commit()

    done = _merges(clean).merge(a.id, b.id, admin.id)

    # The absorbed card's own rows, one of each, move with it.
    assert done.moved["contract_documents"] == 1
    assert done.moved["matches"] == 1


def test_refused_when_the_crm_says_both_have_an_engagement_with_the_same_client(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    with pytest.raises(InvalidState, match="incarico in corso con la stessa azienda"):
        _merges(clean, _conflicts(a, b, shared_clients=1)).merge(a.id, b.id, admin.id)


def test_refused_when_the_crm_says_the_tax_codes_differ(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    with pytest.raises(InvalidState, match="codici fiscali diversi"):
        _merges(clean, _conflicts(a, b, tax_code_conflict=True)).merge(a.id, b.id, admin.id)


def test_a_merge_the_crm_finds_clean_goes_through(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    done = _merges(clean, _conflicts(a, b)).merge(a.id, b.id, admin.id)
    assert done.survivor_freelancer_id == a.id


def test_a_crm_that_cannot_be_asked_refuses_the_merge_and_writes_nothing(
    clean: Session,
) -> None:
    """Fails closed: a merge the CRM did not check could join two live agreements."""
    a, b, _, _, admin = _pair(clean)
    for failure in (
        [ConnectionRefusedError(), ConnectionRefusedError()],
        [(500, b"")],
        [(200, b"not json")],
    ):
        for ask in (
            lambda service: service.merge(a.id, b.id, admin.id),
            lambda service: service.plan(a.id, b.id),
        ):
            crm = FakeCrmDoor()
            crm.down(*failure)
            with pytest.raises(InvalidState, match="senza il suo controllo non unisco"):
                ask(_merges(clean, crm))
    assert clean.get(Freelancer, b.id).deleted_at is None  # type: ignore[union-attr]
    assert clean.scalar(select(AdminAction).where(AdminAction.kind == "merged")) is None
    # No token on this environment: refused the same way, and nothing left the process.
    silent = FakeCrmDoor()
    unconfigured = PersonMergeService(clean, silent.door(token=""))
    with pytest.raises(InvalidState, match="non è configurato"):
        unconfigured.merge(a.id, b.id, admin.id)
    assert silent.calls == []


def test_the_crm_is_asked_before_any_row_is_locked(clean: Session) -> None:
    """No row lock is held across the call to the CRM: when it is made the survivor's and the
    absorbed card's rows are not locked by this transaction yet."""
    a, b, _, _, admin = _pair(clean)
    crm = _conflicts(a, b)
    during: list[bool] = []
    original = crm.__call__

    def asking(*call: Any) -> tuple[int, bytes]:
        locked = clean.execute(
            text(
                "SELECT count(*) FROM pg_locks "
                "WHERE locktype = 'transactionid' AND granted AND pid = pg_backend_pid()"
            )
        ).scalar_one()
        during.append(locked == 0)
        return original(*call)

    door = FreelancerDoor(PIGRO, TOKEN, asking, asking)
    PersonMergeService(clean, door).merge(a.id, b.id, admin.id)
    assert during == [True]


def test_refused_when_the_two_cards_have_different_tax_codes(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    for card, code in ((a, "RSSMRA80A01H501U"), (b, "VRDLGU85M01F205X")):
        clean.add(
            FreelancerFiscal(
                freelancer_id=card.id, tax_code=code, domicile="Via 1", updated_by=admin.id
            )
        )
    clean.commit()
    with pytest.raises(InvalidState, match="codici fiscali diversi"):
        _merges(clean).merge(a.id, b.id, admin.id)


def test_refused_when_the_absorbed_card_is_an_admins_and_for_the_same_card_twice(
    clean: Session,
) -> None:
    a, b, _, b_user, admin = _pair(clean)
    b_user.role = "admin"
    clean.commit()
    with pytest.raises(InvalidState, match="amministratore"):
        _merges(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(ValidationFailed, match="due schede diverse"):
        _merges(clean).merge(a.id, a.id, admin.id)


def test_a_deleted_card_cannot_be_merged(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    b.deleted_at = NOW
    clean.commit()
    with pytest.raises(NotFound):
        _merges(clean).merge(a.id, b.id, admin.id)


def test_the_plan_counts_what_a_merge_would_move_and_writes_nothing(clean: Session) -> None:
    a, b, _, b_user, admin = _pair(clean)
    _everything_of(clean, b, b_user, admin)
    plan = _merges(clean).plan(a.id, b.id)
    assert plan == dict.fromkeys((relation.key for relation in RELATIONS), 1)
    assert clean.get(Freelancer, b.id).deleted_at is None  # type: ignore[union-attr]
    assert clean.scalar(select(AdminAction).where(AdminAction.kind == "merged")) is None


def test_undo_gives_every_row_back_and_makes_the_card_live_again(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    b_user.linkedin_url = "https://www.linkedin.com/in/ada-vecchia"
    a_user.linkedin_url = None
    clean.commit()
    ids = _everything_of(clean, b, b_user, admin)
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    back = service.undo(done.action_id, admin.id)

    clean.expire_all()
    assert back.moved == dict.fromkeys(ids, 1)
    for relation in RELATIONS:
        model = relation.column.class_
        holder = clean.scalar(select(relation.column).where(model.id == ids[relation.key]))
        assert holder == (b_user.id if relation.scope == "user" else b.id), relation.key
    absorbed, survivor = clean.get(Freelancer, b.id), clean.get(Freelancer, a.id)
    assert absorbed is not None and survivor is not None and absorbed.deleted_at is None
    user_b, user_a = clean.get(User, b_user.id), clean.get(User, a_user.id)
    assert user_b is not None and user_a is not None
    assert user_b.merged_into_id is None
    assert user_b.linkedin_url == "https://www.linkedin.com/in/ada-vecchia"
    assert user_a.linkedin_url is None
    kinds = clean.scalars(select(AdminAction.kind).where(AdminAction.entity_id == b.id)).all()
    assert "unmerged" in kinds
    with pytest.raises(ValidationFailed, match="già stata annullata"):
        service.undo(done.action_id, admin.id)


def test_undo_restores_a_swapped_cv_and_the_survivors_filled_fields(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at = NOW - timedelta(days=90)
    b.cv_uploaded_at = NOW
    b.job_title, a.job_title = "Backend", None
    clean.add(FreelancerCard(freelancer_id=b.id, cv_sha256="b" * 64, card={"role": "Dev"}))
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    service.undo(done.action_id, admin.id)
    clean.expire_all()
    assert clean.get(Freelancer, a.id).cv_bytes == PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, a.id).job_title is None  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, b.id).card == {"role": "Dev"}  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, a.id) is None


def test_the_absorbed_cards_source_text_is_deleted_and_the_survivors_kept(clean: Session) -> None:
    """What third parties' pages said about a card that no longer exists goes with it
    (REB-837); the survivor's own stays."""
    a, b, _, _, admin = _pair(clean)
    for owner in (a, b):
        clean.add(
            FreelancerSource(
                freelancer_id=owner.id,
                kind="link",
                ref="https://ada.example/",
                position=1,
                text="a page",
                content_sha256="c" * 64,
                status="ok",
                fetched_at=NOW,
            )
        )
    clean.commit()

    _merges(clean).merge(a.id, b.id, admin.id)

    owners = clean.scalars(select(FreelancerSource.freelancer_id)).all()
    assert owners == [a.id]


def test_a_swapped_card_keeps_its_provenance(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at = NOW - timedelta(days=90)
    b.cv_uploaded_at = NOW
    clean.add(
        FreelancerCard(
            freelancer_id=b.id,
            cv_sha256="b" * 64,
            card={"role": "Dev"},
            prompt_version=2,
            provider="anthropic",
            sources={"role": ["cv"]},
            source_hashes={"github": "d" * 64},
            conflicts=[{"field": "experience_years"}],
            thinking_tokens=120,
        )
    )
    clean.commit()

    _merges(clean).merge(a.id, b.id, admin.id)

    clean.expire_all()
    card = clean.get(FreelancerCard, a.id)
    assert card is not None and card.card == {"role": "Dev"}
    assert (card.prompt_version, card.provider, card.thinking_tokens) == (2, "anthropic", 120)
    assert card.sources == {"role": ["cv"]}
    assert card.source_hashes == {"github": "d" * 64}
    assert card.conflicts == [{"field": "experience_years"}]


def test_to_swapped_card_keeps_its_person_with_its_cv_and_undo_gives_it_back(
    clean: Session,
) -> None:
    """The persona is read from one CV: it moves with the CV and its card, both ways."""
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at = NOW - timedelta(days=90)
    b.cv_uploaded_at = NOW
    person = {"email": "dev@example.org", "cv_sha256": "b" * 64}
    clean.add(
        FreelancerCard(freelancer_id=b.id, cv_sha256="b" * 64, card={"role": "Dev"}, person=person)
    )
    clean.commit()
    service = _merges(clean)

    done = service.merge(a.id, b.id, admin.id)

    clean.expire_all()
    assert clean.get(FreelancerCard, a.id).person == person  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, b.id) is None

    service.undo(done.action_id, admin.id)

    clean.expire_all()
    assert clean.get(FreelancerCard, b.id).person == person  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, a.id) is None


def test_undo_leaves_what_the_survivor_got_after_the_merge(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    clean.add(Login(user_id=b_user.id))
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    clean.add(Login(user_id=a_user.id))
    clean.commit()
    service.undo(done.action_id, admin.id)
    logins = clean.scalars(select(Login.user_id)).all()
    assert sorted(logins) == sorted([a_user.id, b_user.id])


def test_every_foreign_key_to_a_person_is_moved_or_left_on_purpose() -> None:
    """A table added later that points at `users` or `freelancers` fails here, naming
    the column, until the merge moves it or `LEFT_IN_PLACE` says why it stays."""
    handled = {f"{r.column.class_.__tablename__}.{r.column.key}" for r in RELATIONS}
    found: set[str] = set()
    for table in Base.metadata.tables.values():
        for column in table.columns:
            for key in column.foreign_keys:
                if key.column.table.name in ("users", "freelancers"):
                    found.add(f"{table.name}.{column.name}")
    # `entity_id` columns carry no foreign key; the two relations below are on them.
    unaccounted = found - handled - set(LEFT_IN_PLACE)
    assert not unaccounted, sorted(unaccounted)
    stale = set(LEFT_IN_PLACE) - found
    assert not stale, sorted(stale)


def test_the_notes_of_the_absorbed_card_move_to_the_survivor_by_id_and_stay_whole(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    long_text = "nota di B " * 600
    mine = Note(entity_type="freelancer", entity_id=a.id, text="nota di A", author="Ivan")
    theirs = Note(entity_type="freelancer", entity_id=b.id, text=long_text, author="Ivan")
    clean.add_all([mine, theirs])
    clean.commit()
    _merges(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    thread = {n.text: n for n in clean.scalars(select(Note).where(Note.entity_id == a.id))}
    assert {"nota di A", long_text} <= set(thread)
    assert thread[long_text].id == theirs.id
    # The merge's own line is the hub's, and nobody may edit it.
    own = [n for n in thread.values() if n.text.startswith("Scheda unita con")]
    assert [(n.source, n.author_id) for n in own] == [("system", admin.id)]


def test_a_discarded_absorbed_card_does_not_discard_the_survivor(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    b.status = "discarded"
    clean.commit()
    _merges(clean).merge(a.id, b.id, admin.id)
    assert clean.get(Freelancer, a.id).status == "new"  # type: ignore[union-attr]


def test_a_refusal_to_be_mailed_follows_the_person_and_the_undo_takes_it_back(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    clean.add(CampaignOptout(email="vecchia@studio.it", source="link"))
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    assert clean.get(CampaignOptout, "nuova@studio.it") is not None
    service.undo(done.action_id, admin.id)
    clean.expire_all()
    assert clean.get(CampaignOptout, "nuova@studio.it") is None
    assert clean.get(CampaignOptout, "vecchia@studio.it") is not None


def test_a_code_of_the_absorbed_person_credits_the_survivor(clean: Session) -> None:
    from rebase_core.referrals import ReferralService

    a, b, a_user, b_user, admin = _pair(clean)
    b_user.referral_code = "ABCDEFGHJK"
    clean.commit()
    _merges(clean).merge(a.id, b.id, admin.id)
    owner = ReferralService(clean).resolve_referrer("abcdefghjk")
    assert owner is not None and owner.id == a_user.id


def test_undo_keeps_what_the_survivor_changed_since_the_merge(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    _phone(clean, b_user, "333")
    b.job_title, a.job_title = "Backend", None
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    # After the merge the survivor's own edits: the phone that arrived is replaced by a new
    # one, and a new position.
    clean.execute(delete(UserContactPoint).where(UserContactPoint.value == "333"))
    _phone(clean, clean.get(User, a_user.id), "444")  # type: ignore[arg-type]
    clean.get(Freelancer, a.id).job_title = "Tech lead"  # type: ignore[union-attr]
    clean.commit()
    back = service.undo(done.action_id, admin.id)
    clean.expire_all()
    assert clean.get(User, a_user.id).phone == "444"  # type: ignore[union-attr]
    assert clean.get(User, b_user.id).phone is None  # type: ignore[union-attr]
    assert clean.get(Freelancer, a.id).job_title == "Tech lead"  # type: ignore[union-attr]
    assert back.left_behind == {"contact_points": 1, "fields": 1}


def test_undo_is_refused_once_the_survivor_was_itself_merged_away(clean: Session) -> None:
    a, b, a_user, _, admin = _pair(clean)
    c_user = _user(clean, "terza@studio.it", first_name="Terza")
    c = _card(clean, c_user, uploaded=NOW + timedelta(days=1), created=NOW + timedelta(days=1))
    clean.commit()
    service = _merges(clean)
    first = service.merge(a.id, b.id, admin.id)
    service.merge(c.id, a.id, admin.id)
    with pytest.raises(InvalidState, match="a sua volta unita"):
        service.undo(first.action_id, admin.id)


def test_a_role_is_not_given_to_an_address_merged_into_somebody(
    clean: Session, settings: Settings
) -> None:
    a, b, _, _, admin = _pair(clean)
    _merges(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(ValidationFailed, match="unito a un'altra scheda"):
        UserService(clean, settings).set_role("vecchia@studio.it", "admin")


def test_a_saved_team_proposal_names_the_survivor_after_a_merge_and_the_absorbed_after_undo(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    proposal = TeamProposal(
        description="d",
        summary="r",
        location={},
        team=[
            {"freelancer_id": str(b.id), "role": "Backend"},
            {"freelancer_id": str(UUID(int=7))},
        ],
        economics={},
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        origin="public",
    )
    clean.add(proposal)
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    clean.refresh(proposal)
    assert [m["freelancer_id"] for m in proposal.team] == [str(a.id), str(UUID(int=7))]
    service.undo(done.action_id, admin.id)
    clean.refresh(proposal)
    assert [m["freelancer_id"] for m in proposal.team] == [str(b.id), str(UUID(int=7))]


def test_undo_is_refused_when_the_survivor_has_a_new_cv_after_a_swap(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at, b.cv_uploaded_at = NOW - timedelta(days=90), NOW
    clean.commit()
    service = _merges(clean)
    done = service.merge(a.id, b.id, admin.id)
    clean.get(Freelancer, a.id).cv_bytes = PDF + b"%new"  # type: ignore[union-attr]
    clean.commit()
    with pytest.raises(InvalidState, match="CV diverso"):
        service.undo(done.action_id, admin.id)


def test_a_merge_does_not_run_on_cards_another_transaction_just_merged(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    b.deleted_at = NOW
    with pytest.raises(InvalidState, match="appena stata unita"):
        PersonMergeService._still_live(a, b, a_user, b_user)


def test_a_cv_upload_on_a_card_a_merge_hid_is_refused(clean: Session) -> None:
    from rebase_core.members import MemberService

    a, b, _, _, admin = _pair(clean)
    _merges(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(NotFound):
        MemberService(clean).replace_cv(b.id, PDF, "cv.pdf", "application/pdf")
