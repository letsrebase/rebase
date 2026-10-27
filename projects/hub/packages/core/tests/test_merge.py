"""Merging one person into another, and putting it back (REB-800): every relation that
belongs to a person moves, by id; nothing is deleted; the newest CV stays with its card;
the refusals say why; the undo gives every row back."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.db import Base
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.merge import LEFT_IN_PLACE, RELATIONS, PersonMergeService
from rebase_core.models import (
    AdminAction,
    Campaign,
    CampaignOptout,
    CampaignRecipient,
    Comment,
    Company,
    ContractDocument,
    Freelancer,
    FreelancerCard,
    FreelancerFiscal,
    GuideDownload,
    Login,
    MagicLinkToken,
    Match,
    Referral,
    TalentCloudGrant,
    TeamProposal,
    TeamRequest,
    TeamRequestTalent,
    User,
    UserSession,
)
from rebase_core.schemas import FreelancerCreate
from rebase_core.users import UserService

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
OLD_PDF = PDF + b"%old"

TABLES = (
    "admin_actions",
    "comments",
    "referral_rewards",
    "referrals",
    "campaign_recipients",
    "campaigns",
    "team_request_talents",
    "team_requests",
    "team_proposals",
    "talent_cloud_grants",
    "contract_documents",
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
    user = User(email=email, nome=extra.pop("nome", "Ada"), cognome="Lovelace", **extra)
    session.add(user)
    session.flush()
    return user


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
    return _user(session, "admin@rebase.it", nome="Root", role="admin")


def _company(session: Session, user: User) -> Company:
    row = Company(
        user_id=user.id,
        nome_azienda="Acme",
        figura_richiesta="Backend",
        progetto="Un progetto",
        periodo_da=date(2026, 11, 1),
        durata="3 mesi",
        budget_giornaliero=Decimal("500"),
        remoto="remoto",
        numero_risorse=1,
    )
    session.add(row)
    session.flush()
    return row


def _match(session: Session, card: Freelancer, company: Company, admin: User, stato: str) -> Match:
    row = Match(
        freelancer_id=card.id,
        company_id=company.id,
        cliente_ragione_sociale="Acme srl",
        cliente_piva="01234567890",
        cliente_sede="Milano",
        stato=stato,
        created_by=admin.id,
    )
    session.add(row)
    session.flush()
    return row


def _quadro(session: Session, card: Freelancer, admin: User, stato: str = "firmato") -> None:
    session.add(
        ContractDocument(
            kind="quadro",
            freelancer_id=card.id,
            text_version="1",
            pdf=PDF,
            stato=stato,
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


def _everything_of(session: Session, b: Freelancer, b_user: User, admin: User) -> dict[str, UUID]:
    """One row of every relation, hung on B and on B's person."""
    other_user = _user(session, "altro@studio.it", nome="Altro")
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
                descrizione="d",
                riassunto="r",
                luogo={},
                team=[],
                economia={},
                model="m",
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                origine="cloud",
                user_id=b_user.id,
            ),
        ),
        (
            "team_requests",
            TeamRequest(origine="cloud", azienda="Acme", email="a@b.it", user_id=b_user.id),
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
        ("comments", Comment(entity_type="freelancer", entity_id=b.id, testo="t", autore="x")),
        (
            "audit",
            AdminAction(entity_type="freelancer", entity_id=b.id, kind="vetted", admin_id=admin.id),
        ),
        (
            "fiscal",
            FreelancerFiscal(
                freelancer_id=b.id,
                codice_fiscale="RSSMRA80A01H501U",
                domicilio="Via Roma 1",
                updated_by=admin.id,
            ),
        ),
    ):
        session.add(row)
        session.flush()
        ids[key] = row.id
    ids["matches"] = _match(session, b, company, admin, "bozza").id
    session.add(
        ContractDocument(
            kind="quadro",
            freelancer_id=b.id,
            text_version="1",
            pdf=PDF,
            stato="annullato",
            created_by=admin.id,
        )
    )
    session.add(
        Campaign(
            created_by=admin.id,
            nome="c",
            slug="c",
            fonte="stato",
            stato_percorso="scheda_vuota_nuovi",
            oggetto="o",
            testo="t",
            bottone_testo="b",
            bottone_meta="area",
            azione="entrato",
            contenuto_at=NOW,
        )
    )
    session.flush()
    campaign = session.scalars(select(Campaign)).one()
    for key, kwargs in (
        (
            "campaign_recipients_user",
            {"user_id": b_user.id, "email": "u@b.it", "codice": "AAAAAAAA"},
        ),
        (
            "campaign_recipients_card",
            {"freelancer_id": b.id, "email": "f@b.it", "codice": "BBBBBBBB"},
        ),
    ):
        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            tipo="freelancer",
            disiscrizione_token=kwargs["email"] * 3,
            **kwargs,
        )
        session.add(recipient)
        session.flush()
        ids[key] = recipient.id
    request = session.scalars(select(TeamRequest)).one()
    talent = TeamRequestTalent(request_id=request.id, freelancer_id=b.id, ruolo="Backend")
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

    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)

    assert done.moved == dict.fromkeys(ids, 1) and done.lasciati == {}
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
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
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
    thread = clean.scalars(select(Comment).where(Comment.entity_id == a.id)).all()
    assert any("vecchia@studio.it" in c.testo for c in thread)
    # Nothing was deleted: the row, its CV and its address are all still there.
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]


def test_the_older_card_cannot_be_restored_by_itself_once_merged(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(InvalidState, match="unita a un'altra"):
        FreelancerService(clean).restore(b.id, admin.id)


def test_the_newest_cv_stays_and_the_older_file_stays_reachable_on_the_absorbed_card(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert done.cv == "invariato"
    assert clean.get(Freelancer, a.id).cv_bytes == PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]


def test_when_the_absorbed_cv_is_newer_the_cv_and_its_card_swap(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    a.cv_uploaded_at = NOW - timedelta(days=90)
    b.cv_uploaded_at = NOW
    clean.add(FreelancerCard(freelancer_id=b.id, cv_sha256="b" * 64, card={"ruolo": "Dev"}))
    clean.add(FreelancerCard(freelancer_id=a.id, cv_sha256="a" * 64, card={"ruolo": "Old"}))
    clean.commit()

    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()

    assert done.cv == "assorbita"
    survivor, absorbed = clean.get(Freelancer, a.id), clean.get(Freelancer, b.id)
    assert survivor is not None and absorbed is not None
    assert (survivor.cv_bytes, absorbed.cv_bytes) == (OLD_PDF, PDF)
    assert clean.get(FreelancerCard, a.id).card == {"ruolo": "Dev"}  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, b.id).card == {"ruolo": "Old"}  # type: ignore[union-attr]
    # The survivor's card describes a CV that is not the one it holds now: flagged.
    assert done.scheda_da_rigenerare is True


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
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    card = clean.get(FreelancerCard, a.id)
    assert done.scheda_da_rigenerare is True
    assert card is not None and (card.error, card.error_cv_sha256) == (None, None)


def test_the_profile_the_survivor_leaves_empty_is_filled_and_the_first_attribution_stays(
    clean: Session,
) -> None:
    admin = _admin(clean)
    a_user = _user(clean, "nuova@studio.it", linkedin_url=None)
    b_user = _user(
        clean, "vecchia@studio.it", linkedin_url="https://www.linkedin.com/in/ada", telefono="333"
    )
    a = _card(clean, a_user, created=NOW, posizione=None, utm_source=None)
    b = _card(
        clean,
        b_user,
        created=NOW - timedelta(days=40),
        uploaded=NOW - timedelta(days=40),
        posizione="Backend",
        tariffa_giornaliera=Decimal("400"),
        utm_source="linkedin",
        note="una nota",
        stato="contattato",
    )
    clean.commit()
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    survivor = clean.get(Freelancer, a.id)
    assert survivor is not None
    assert (survivor.posizione, survivor.tariffa_giornaliera) == ("Backend", Decimal("400.00"))
    assert (survivor.utm_source, survivor.stato) == ("linkedin", "contattato")
    thread = clean.scalars(select(Comment.testo).where(Comment.entity_id == a.id)).all()
    assert "Nota della scheda unita: una nota" in thread
    user = clean.get(User, a_user.id)
    assert user is not None
    assert (user.linkedin_url, user.telefono) == ("https://www.linkedin.com/in/ada", "333")
    # The absorbed address gave its profile up: one holder per profile.
    assert clean.get(User, b_user.id).linkedin_url is None  # type: ignore[union-attr]


def test_the_absorbed_address_still_logs_in_as_the_survivor_and_the_wizard_finds_them(
    clean: Session, settings: Settings
) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    users = UserService(clean, settings)
    mail = users.request_link("vecchia@studio.it")
    assert mail is not None and mail.to == "vecchia@studio.it"
    token = clean.scalars(select(MagicLinkToken)).one()
    assert token.user_id == a_user.id
    read, created = FreelancerService(clean).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email="vecchia@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend",
            remoto="remoto",
        )
    )
    assert (read.id, created) == (a.id, False)


def test_a_referral_the_survivor_already_has_stays_with_the_absorbed_card(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    referrer = _user(clean, "r@studio.it", nome="Rita")
    own = Referral(referrer_user_id=referrer.id, kind="freelancer", entity_id=a.id, code="AAA")
    theirs = Referral(referrer_user_id=referrer.id, kind="freelancer", entity_id=b.id, code="BBB")
    clean.add_all([own, theirs])
    clean.commit()
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert done.lasciati == {"referral_received": 1} and done.moved == {}
    assert clean.get(Referral, theirs.id).entity_id == b.id  # type: ignore[union-attr]


def test_a_talent_already_on_the_same_team_request_is_not_moved_twice(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    request = TeamRequest(origine="pubblico", azienda="Acme", email="a@b.it")
    clean.add(request)
    clean.flush()
    clean.add_all(
        [
            TeamRequestTalent(request_id=request.id, freelancer_id=a.id, ruolo="Backend"),
            TeamRequestTalent(request_id=request.id, freelancer_id=b.id, ruolo="Backend"),
        ]
    )
    clean.commit()
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    assert done.lasciati == {"team_request_talents": 1}


def test_refused_when_both_have_a_live_framework_agreement_of_the_same_regime(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    _quadro(clean, a, admin, "firmato")
    _quadro(clean, b, admin, "inviato")
    clean.commit()
    with pytest.raises(InvalidState, match="contratto quadro in corso o firmato"):
        PersonMergeService(clean).merge(a.id, b.id, admin.id)
    assert clean.get(Freelancer, b.id).deleted_at is None  # type: ignore[union-attr]


def test_a_cancelled_framework_agreement_on_one_side_is_no_conflict(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    _quadro(clean, a, admin, "firmato")
    _quadro(clean, b, admin, "annullato")
    clean.commit()
    done = PersonMergeService(clean).merge(a.id, b.id, admin.id)
    assert done.moved["contract_documents"] == 1


def test_refused_when_both_have_an_active_match_with_the_same_company(clean: Session) -> None:
    a, b, a_user, _, admin = _pair(clean)
    company = _company(clean, a_user)
    _match(clean, a, company, admin, "attivo")
    _match(clean, b, company, admin, "in_firma")
    clean.commit()
    with pytest.raises(InvalidState, match="incarico in corso con la stessa azienda"):
        PersonMergeService(clean).merge(a.id, b.id, admin.id)


def test_refused_when_the_two_cards_have_different_tax_codes(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    for card, code in ((a, "RSSMRA80A01H501U"), (b, "VRDLGU85M01F205X")):
        clean.add(
            FreelancerFiscal(
                freelancer_id=card.id, codice_fiscale=code, domicilio="Via 1", updated_by=admin.id
            )
        )
    clean.commit()
    with pytest.raises(InvalidState, match="codici fiscali diversi"):
        PersonMergeService(clean).merge(a.id, b.id, admin.id)


def test_refused_when_the_absorbed_card_is_an_admins_and_for_the_same_card_twice(
    clean: Session,
) -> None:
    a, b, _, b_user, admin = _pair(clean)
    b_user.role = "admin"
    clean.commit()
    with pytest.raises(InvalidState, match="amministratore"):
        PersonMergeService(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(ValidationFailed, match="due schede diverse"):
        PersonMergeService(clean).merge(a.id, a.id, admin.id)


def test_a_deleted_card_cannot_be_merged(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    b.deleted_at = NOW
    clean.commit()
    with pytest.raises(NotFound):
        PersonMergeService(clean).merge(a.id, b.id, admin.id)


def test_the_plan_counts_what_a_merge_would_move_and_writes_nothing(clean: Session) -> None:
    a, b, _, b_user, admin = _pair(clean)
    _everything_of(clean, b, b_user, admin)
    plan = PersonMergeService(clean).plan(a.id, b.id)
    assert plan == dict.fromkeys((relation.key for relation in RELATIONS), 1)
    assert clean.get(Freelancer, b.id).deleted_at is None  # type: ignore[union-attr]
    assert clean.scalar(select(AdminAction).where(AdminAction.kind == "merged")) is None


def test_undo_gives_every_row_back_and_makes_the_card_live_again(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    b_user.linkedin_url = "https://www.linkedin.com/in/ada-vecchia"
    a_user.linkedin_url = None
    clean.commit()
    ids = _everything_of(clean, b, b_user, admin)
    service = PersonMergeService(clean)
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
    b.posizione, a.posizione = "Backend", None
    clean.add(FreelancerCard(freelancer_id=b.id, cv_sha256="b" * 64, card={"ruolo": "Dev"}))
    clean.commit()
    service = PersonMergeService(clean)
    done = service.merge(a.id, b.id, admin.id)
    service.undo(done.action_id, admin.id)
    clean.expire_all()
    assert clean.get(Freelancer, a.id).cv_bytes == PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, b.id).cv_bytes == OLD_PDF  # type: ignore[union-attr]
    assert clean.get(Freelancer, a.id).posizione is None  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, b.id).card == {"ruolo": "Dev"}  # type: ignore[union-attr]
    assert clean.get(FreelancerCard, a.id) is None


def test_undo_leaves_what_the_survivor_got_after_the_merge(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    clean.add(Login(user_id=b_user.id))
    clean.commit()
    service = PersonMergeService(clean)
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


def test_a_note_of_the_absorbed_card_goes_to_the_thread_not_into_the_survivors_note(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    a.note, b.note = "nota di A", "nota di B " * 600
    clean.commit()
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    clean.expire_all()
    assert clean.get(Freelancer, a.id).note == "nota di A"  # type: ignore[union-attr]
    thread = clean.scalars(select(Comment.testo).where(Comment.entity_id == a.id)).all()
    assert any(t.startswith("Nota della scheda unita: nota di B") for t in thread)


def test_a_discarded_absorbed_card_does_not_discard_the_survivor(clean: Session) -> None:
    a, b, _, _, admin = _pair(clean)
    b.stato = "scartato"
    clean.commit()
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    assert clean.get(Freelancer, a.id).stato == "nuovo"  # type: ignore[union-attr]


def test_a_refusal_to_be_mailed_follows_the_person_and_the_undo_takes_it_back(
    clean: Session,
) -> None:
    a, b, _, _, admin = _pair(clean)
    clean.add(CampaignOptout(email="vecchia@studio.it", fonte="link"))
    clean.commit()
    service = PersonMergeService(clean)
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
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    owner = ReferralService(clean).resolve_referrer("abcdefghjk")
    assert owner is not None and owner.id == a_user.id


def test_undo_keeps_what_the_survivor_changed_since_the_merge(clean: Session) -> None:
    a, b, a_user, b_user, admin = _pair(clean)
    b_user.telefono = "333"
    b.posizione, a.posizione = "Backend", None
    clean.commit()
    service = PersonMergeService(clean)
    done = service.merge(a.id, b.id, admin.id)
    # After the merge the survivor's own edits: a new phone number and a new position.
    clean.get(User, a_user.id).telefono = "444"  # type: ignore[union-attr]
    clean.get(Freelancer, a.id).posizione = "Tech lead"  # type: ignore[union-attr]
    clean.commit()
    back = service.undo(done.action_id, admin.id)
    clean.expire_all()
    assert clean.get(User, a_user.id).telefono == "444"  # type: ignore[union-attr]
    assert clean.get(Freelancer, a.id).posizione == "Tech lead"  # type: ignore[union-attr]
    assert back.lasciati == {"telefono": 1, "campi": 1}


def test_undo_is_refused_once_the_survivor_was_itself_merged_away(clean: Session) -> None:
    a, b, a_user, _, admin = _pair(clean)
    c_user = _user(clean, "terza@studio.it", nome="Terza")
    c = _card(clean, c_user, uploaded=NOW + timedelta(days=1), created=NOW + timedelta(days=1))
    clean.commit()
    service = PersonMergeService(clean)
    first = service.merge(a.id, b.id, admin.id)
    service.merge(c.id, a.id, admin.id)
    with pytest.raises(InvalidState, match="a sua volta unita"):
        service.undo(first.action_id, admin.id)


def test_a_role_is_not_given_to_an_address_merged_into_somebody(
    clean: Session, settings: Settings
) -> None:
    a, b, _, _, admin = _pair(clean)
    PersonMergeService(clean).merge(a.id, b.id, admin.id)
    with pytest.raises(ValidationFailed, match="unito a un'altra scheda"):
        UserService(clean, settings).set_role("vecchia@studio.it", "admin")
