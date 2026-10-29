"""One person, once (REB-800): the LinkedIn profile is an identity beside the address, so
a second form, a second wizard run or an admin's draft that names a profile already on the
list lands on that person; the finder lists the pairs that got in before the rule, and the
signals that only flag are never merged."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from admins import an_admin
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.cli import talent_duplicates
from rebase_core.duplicates import (
    LINKEDIN,
    NOME,
    TELEFONO,
    UNIQUE_INDEXES,
    IndexesEnsured,
    duplicate_list,
    ensure_unique_indexes,
    find_duplicates,
)
from rebase_core.errors import ValidationFailed
from rebase_core.freelancers import LINKEDIN_HAS_CARD, FreelancerService
from rebase_core.linkedin import linkedin_key
from rebase_core.members import MemberService
from rebase_core.merge import PersonMergeService
from rebase_core.models import Freelancer, Signup, User
from rebase_core.schemas import (
    FreelancerCreate,
    FreelancerDraft,
    FreelancerOverride,
    MemberUpdate,
    SignupCreate,
)
from rebase_core.service import SignupService
from rebase_core.talenti import TalentiService
from rebase_core.users import LINKEDIN_TAKEN

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ADA = "https://www.linkedin.com/in/ada-lovelace"
TABLES = (
    "admin_actions",
    "notes",
    "freelancer_cards",
    "freelancers",
    "users",
    "signups",
)


def _wipe(session: Session) -> None:
    session.rollback()
    for table in TABLES:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    _wipe(hub_session)


@pytest.fixture
def twice(hub_session: Session) -> Iterator[Session]:
    """A database as production's was before the rule: the two LinkedIn indexes are not
    there, so a pair that shares a profile can exist. Put back, empty, afterwards."""
    for name, _ in UNIQUE_INDEXES:
        hub_session.execute(text(f"DROP INDEX IF EXISTS {name}"))
    hub_session.commit()
    yield hub_session
    _wipe(hub_session)
    ensure_unique_indexes(hub_session)


def _apply(session: Session, email: str, linkedin: str | None = ADA, **extra: Any) -> Any:
    payload: dict[str, Any] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": email,
        "linkedin_url": linkedin,
        "tariffa_giornaliera": Decimal("450"),
        "posizione": "Backend developer",
        "remoto": "remoto",
    }
    payload.update(extra)
    return FreelancerService(session).apply(FreelancerCreate(**payload))


def _user(session: Session, email: str, **extra: Any) -> User:
    row = User(
        email=email,
        nome=extra.pop("nome", "Marco"),
        cognome=extra.pop("cognome", "Rossi"),
        **extra,
    )
    session.add(row)
    session.flush()
    return row


def _card(session: Session, user: User, uploaded: datetime | None = NOW) -> Freelancer:
    row = Freelancer(user_id=user.id, created_at=uploaded or NOW)
    if uploaded is not None:
        row.cv_bytes, row.cv_filename, row.cv_mime = b"%PDF-1", "cv.pdf", "application/pdf"
        row.cv_size, row.cv_uploaded_at = 6, uploaded
    session.add(row)
    session.flush()
    return row


# ---- the stored shape --------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    [
        "https://www.linkedin.com/in/Ada-Lovelace/",
        "http://it.linkedin.com/in/ada-lovelace?utm_source=share",
        "linkedin.com/in/ADA-LOVELACE/details/experience/",
        "https://www.linkedin.com/in/ada-lovelace/it",
    ],
)
def test_a_profile_is_stored_lower_case_without_query_slash_or_locale(
    clean: Session, typed: str
) -> None:
    read, _ = _apply(clean, "ada@studio.it", typed)
    assert read.linkedin_url == ADA


def test_the_model_normalises_a_profile_written_without_the_schema(clean: Session) -> None:
    user = _user(clean, "a@b.it", linkedin_url="LinkedIn.com/in/Ada-Lovelace/")
    assert user.linkedin_url == ADA
    signup = Signup(email="s@b.it", linkedin_url="http://it.linkedin.com/in/Ada-Lovelace?x=1")
    assert signup.linkedin_url == ADA
    odd = _user(clean, "o@b.it", linkedin_url="https://www.linkedin.com/company/Acme/")
    assert odd.linkedin_url == "https://www.linkedin.com/company/Acme/"


# ---- prevention -------------------------------------------------------------------------


def test_the_wizard_with_a_second_address_and_the_same_profile_lands_on_the_same_person(
    clean: Session,
) -> None:
    first, created = _apply(clean, "vecchia@studio.it", ADA)
    second, created_again = _apply(
        clean, "nuova@studio.it", "https://it.linkedin.com/in/Ada-Lovelace/"
    )
    assert (created, created_again) == (True, False)
    assert second.id == first.id and second.email == "vecchia@studio.it"
    assert clean.scalar(text("SELECT count(*) FROM users")) == 1
    assert clean.scalar(text("SELECT count(*) FROM freelancers")) == 1


def test_a_second_address_with_no_profile_is_still_a_second_person(clean: Session) -> None:
    _apply(clean, "uno@studio.it", None)
    _, created = _apply(clean, "due@studio.it", None)
    assert created is True


def test_the_signup_form_attaches_to_the_row_that_holds_the_profile(clean: Session) -> None:
    service = SignupService(clean)
    first = service.subscribe(
        SignupCreate(email="a@studio.it", nome="Ada", cognome="Lovelace", linkedin_url=ADA)
    )
    second = service.subscribe(
        SignupCreate(
            email="b@studio.it",
            nome="Ada",
            cognome="Lovelace",
            linkedin_url="linkedin.com/in/ADA-lovelace/",
        )
    )
    assert (first.nuova, second.nuova, second.id) == (True, False, first.id)
    assert clean.scalar(text("SELECT count(*) FROM signups")) == 1


def test_a_repeat_signup_does_not_fill_in_a_profile_another_row_holds(clean: Session) -> None:
    service = SignupService(clean)
    service.subscribe(SignupCreate(email="a@studio.it", nome="A", cognome="A", linkedin_url=ADA))
    service.subscribe(SignupCreate(email="b@studio.it", nome="B", cognome="B"))
    service.subscribe(SignupCreate(email="b@studio.it", nome="B", cognome="B", linkedin_url=ADA))
    other = clean.scalar(select(Signup).where(Signup.email == "b@studio.it"))
    assert other is not None and other.linkedin_url is None


def test_the_database_refuses_two_people_with_one_profile(clean: Session) -> None:
    _user(clean, "a@b.it", linkedin_url=ADA)
    clean.commit()
    clean.add(User(email="c@b.it", nome="C", cognome="C", linkedin_url=ADA.upper()))
    with pytest.raises(IntegrityError):
        clean.commit()
    clean.rollback()
    clean.add(Signup(email="x@b.it", linkedin_url=ADA))
    clean.add(Signup(email="y@b.it", linkedin_url=ADA))
    with pytest.raises(IntegrityError):
        clean.commit()


def test_an_admin_cannot_draft_a_card_with_a_profile_that_is_already_on_one(
    clean: Session,
) -> None:
    _apply(clean, "ada@studio.it", ADA)
    signup = SignupService(clean).subscribe(
        SignupCreate(email="altra@studio.it", nome="Ada", cognome="Lovelace")
    )
    draft = FreelancerDraft(
        nome="Ada",
        cognome="Lovelace",
        linkedin_url=ADA,
        posizione="Backend",
        fonti=["https://www.linkedin.com/in/ada-lovelace"],
    )
    with pytest.raises(ValidationFailed) as refused:
        FreelancerService(clean).draft_from_signup(signup.id, draft, an_admin(clean), "web")
    assert refused.value.details["reason"] == LINKEDIN_HAS_CARD


def test_a_member_cannot_take_the_profile_of_another_card(clean: Session) -> None:
    ada, _ = _apply(clean, "ada@studio.it", ADA)
    bob, _ = _apply(clean, "bob@studio.it", "https://www.linkedin.com/in/bob")
    update = MemberUpdate(
        nome="Bob",
        cognome="Lovelace",
        linkedin_url=ADA,
        tariffa_giornaliera=Decimal("450"),
        posizione="Backend",
        remoto="remoto",
        links=[],
    )
    with pytest.raises(ValidationFailed) as refused:
        MemberService(clean).update(bob.id, update)
    assert refused.value.details["reason"] == LINKEDIN_TAKEN
    assert ada.id != bob.id


def test_an_admin_override_cannot_take_the_profile_of_another_card(clean: Session) -> None:
    _apply(clean, "ada@studio.it", ADA)
    bob, _ = _apply(clean, "bob@studio.it", "https://www.linkedin.com/in/bob")
    admin = _user(clean, "admin@rebase.it", role="admin", nome="Root")
    clean.commit()
    with pytest.raises(ValidationFailed):
        FreelancerService(clean).override(bob.id, FreelancerOverride(linkedin_url=ADA), admin.id)


def test_a_sign_up_whose_profile_is_on_a_card_is_no_lead(clean: Session) -> None:
    _apply(clean, "ada@studio.it", ADA)
    SignupService(clean).subscribe(
        SignupCreate(email="ada@studio.it", nome="Ada", cognome="Lovelace")
    )
    clean.add(Signup(email="altra@studio.it", nome="Ada", cognome="L", linkedin_url=None))
    clean.commit()
    # The same profile under another address, written the way a row from before the
    # rule reads: it is that person, not a lead to chase.
    clean.execute(
        text(
            "INSERT INTO signups (id, email, linkedin_url, created_at) "
            "VALUES (gen_random_uuid(), 'fantasma@studio.it', NULL, now())"
        )
    )
    clean.execute(
        text("UPDATE signups SET linkedin_url = :u WHERE email = 'altra@studio.it'"), {"u": ADA}
    )
    clean.commit()
    leads = FreelancerService(clean)._leads(50)
    assert sorted(item.email for item in leads[0]) == ["fantasma@studio.it"]
    assert leads[1] == 1
    talenti = TalentiService(clean).list_recent(limit=50)
    assert sorted(i.email for i in talenti.items if i.stato == "lead") == ["fantasma@studio.it"]


# ---- the finder --------------------------------------------------------------------------


def test_two_cards_with_one_profile_are_one_unibile_pair_and_the_newest_cv_wins(
    twice: Session,
) -> None:
    older = _user(twice, "vecchia@studio.it", cognome="Lanzo", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", cognome="Lanzo", linkedin_url=ADA.upper())
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    twice.commit()

    report = find_duplicates(twice)

    assert len(report.unibili) == 1 and report.da_rivedere == []
    pair = report.unibili[0]
    assert pair.survivor.freelancer_id == new_card.id
    assert pair.assorbito.freelancer_id == old_card.id
    assert LINKEDIN in pair.segnali and NOME in pair.segnali


def test_the_same_name_or_telephone_only_flags_and_is_never_unibile(twice: Session) -> None:
    one = _user(twice, "uno@studio.it", telefono="+39 333 1234567")
    two = _user(twice, "due@studio.it", telefono="333-1234567", nome="Luca", cognome="Bianchi")
    three = _user(twice, "tre@studio.it", nome="rossi", cognome="MARCO")
    for user in (one, two, three):
        _card(twice, user)
    twice.commit()

    report = find_duplicates(twice)

    assert report.unibili == []
    signals = sorted(p.segnali for p in report.da_rivedere)
    assert signals == [(NOME,), (TELEFONO,)]
    assert not any(p.unibile for p in report.da_rivedere)


def test_a_pair_with_an_admins_card_is_not_unibile_and_keeps_the_admin(twice: Session) -> None:
    admin = _user(twice, "admin@rebase.it", role="admin", linkedin_url=ADA)
    member = _user(twice, "membro@rebase.it", nome="Ada", cognome="Lovelace", linkedin_url=ADA)
    _card(twice, admin, NOW - timedelta(days=90))
    _card(twice, member, NOW)
    twice.commit()
    report = find_duplicates(twice)
    assert report.unibili != [] or report.da_rivedere != []
    pair = (report.unibili + report.da_rivedere)[0]
    assert pair.survivor.admin is True and pair.assorbito.admin is False


def test_a_merged_card_is_no_longer_a_pair(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA.upper())
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    admin = _user(twice, "admin@rebase.it", role="admin", nome="Root", cognome="Admin")
    twice.commit()
    PersonMergeService(twice).merge(new_card.id, old_card.id, admin.id)
    report = find_duplicates(twice)
    assert report.unibili == [] and report.da_rivedere == []


def test_sign_ups_that_repeat_a_profile_are_listed_and_never_merged(twice: Session) -> None:
    card_user = _user(twice, "ada@studio.it", linkedin_url=ADA)
    _card(twice, card_user)
    twice.add(Signup(email="altra@studio.it", linkedin_url=ADA))
    twice.add(Signup(email="x@studio.it", linkedin_url="https://www.linkedin.com/in/x"))
    twice.add(Signup(email="y@studio.it", linkedin_url="https://www.linkedin.com/in/x"))
    twice.add(Signup(email="ada@studio.it", linkedin_url=ADA))
    twice.commit()
    report = find_duplicates(twice)
    assert report.unibili == []
    reasons = sorted("card" if item.card_user_id else "signup" for item in report.iscrizioni)
    # `altra` repeats the card's profile; `y` repeats `x`; and `ada@`, the card's own
    # address, repeats `altra`: two sign-ups on one profile either way.
    assert reasons == ["card", "signup", "signup"]


def test_the_unique_indexes_wait_for_the_merge_and_are_then_created(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA)
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    admin = _user(twice, "admin@rebase.it", role="admin", nome="Root", cognome="Admin")
    twice.commit()
    # While the pair is there the users' index is refused, by name and count, not raised.
    waiting = ensure_unique_indexes(twice)
    assert waiting.blocked == {"uq_users_linkedin_lower": 1}
    assert waiting.created == ["uq_signups_linkedin_lower"]
    PersonMergeService(twice).merge(new_card.id, old_card.id, admin.id)
    done = ensure_unique_indexes(twice)
    assert done.created == ["uq_users_linkedin_lower"] and done.blocked == {}
    assert ensure_unique_indexes(twice) == IndexesEnsured()


def test_a_talenti_row_names_the_cards_it_may_be(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", cognome="Lanzo", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", cognome="Lanzo", linkedin_url=ADA)
    alone = _user(twice, "sola@studio.it", nome="Sola", cognome="Persona")
    old_card, new_card, alone_card = (
        _card(twice, older, NOW - timedelta(days=40)),
        _card(twice, newer, NOW),
        _card(twice, alone),
    )
    twice.commit()
    rows = {row.id: row for row in TalentiService(twice).list_recent(limit=50).items}
    assert rows[old_card.id].possibile_duplicato_di == [new_card.id]
    assert rows[new_card.id].possibile_duplicato_di == [old_card.id]
    assert rows[alone_card.id].possibile_duplicato_di == []
    one = TalentiService(twice).row_for_freelancer(old_card.id)
    assert one.possibile_duplicato_di == [new_card.id]


def test_the_admin_list_names_both_sides_of_a_pair(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA)
    _card(twice, older, NOW - timedelta(days=40))
    _card(twice, newer, NOW)
    twice.commit()
    listed = duplicate_list(twice)
    assert len(listed.unibili) == 1 and listed.da_rivedere == [] and listed.iscrizioni == 0
    pair = listed.unibili[0]
    assert (pair.survivor.email, pair.assorbito.email) == ("nuova@studio.it", "vecchia@studio.it")
    assert pair.unibile is True and pair.survivor.ha_cv is True


# ---- `rebase talent-duplicates` ---------------------------------------------------------


def _production_like(session: Session) -> tuple[Freelancer, Freelancer, User]:
    older = _user(session, "vecchia@studio.it", cognome="Lanzo", linkedin_url=ADA)
    newer = _user(session, "nuova@studio.it", cognome="Lanzo", linkedin_url=ADA)
    admin = _user(session, "admin@rebase.it", role="admin", nome="Root", cognome="Admin")
    old_card = _card(session, older, NOW - timedelta(days=40))
    new_card = _card(session, newer, NOW)
    session.commit()
    return new_card, old_card, admin


def test_the_dry_run_prints_counts_and_ids_never_a_name_an_address_or_a_profile(
    twice: Session,
) -> None:
    survivor, absorbed, _ = _production_like(twice)
    code, lines = talent_duplicates(twice)
    text_ = "\n".join(lines)
    assert code == 0
    assert lines[0].startswith("1 coppie unibili (stesso LinkedIn), 0 da rivedere")
    assert f"tieni {survivor.id} assorbi {absorbed.id}" in text_
    assert "sposterebbe: niente" in text_
    assert "indici unici mancanti: uq_users_linkedin_lower" in text_
    for private in ("studio.it", "Lanzo", "Marco", "linkedin.com", ADA):
        assert private not in text_
    # Reading wrote nothing.
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]


def test_apply_merges_the_pair_with_the_newest_cv_and_then_creates_the_indexes(
    twice: Session,
) -> None:
    survivor, absorbed, _ = _production_like(twice)
    code, lines = talent_duplicates(twice, apply=True, admin_email="ADMIN@rebase.it")
    assert code == 0, lines
    twice.expire_all()
    assert twice.get(Freelancer, absorbed.id).deleted_at is not None  # type: ignore[union-attr]
    assert twice.get(Freelancer, survivor.id).deleted_at is None  # type: ignore[union-attr]
    assert any("unite (azione " in line for line in lines)
    assert lines[-1].startswith("indici unici creati: uq_users_linkedin_lower")
    code, again = talent_duplicates(twice)
    assert again[0].startswith("0 coppie unibili") and again[-1] == "indici unici mancanti: nessuno"


def test_apply_and_undo_need_an_admin_and_say_so(twice: Session) -> None:
    _production_like(twice)
    assert talent_duplicates(twice, apply=True)[0] == 2
    code, lines = talent_duplicates(twice, apply=True, admin_email="vecchia@studio.it")
    assert code == 1 and "Nessun amministratore" in lines[0]


def test_a_pair_a_merge_refuses_is_reported_and_the_rest_goes_on(twice: Session) -> None:
    survivor, absorbed, admin = _production_like(twice)
    for card, code_ in ((survivor, "RSSMRA80A01H501U"), (absorbed, "VRDLGU85M01F205X")):
        twice.execute(
            text(
                "INSERT INTO freelancer_fiscal (id, freelancer_id, codice_fiscale, domicilio, "
                "updated_by, created_at, updated_at) VALUES (gen_random_uuid(), :f, :c, 'Via 1', "
                ":a, now(), now())"
            ),
            {"f": card.id, "c": code_, "a": admin.id},
        )
    twice.commit()
    _, dry = talent_duplicates(twice)
    assert any("bloccata: " in line and "codici fiscali diversi" in line for line in dry)
    code, lines = talent_duplicates(twice, apply=True, admin_email="admin@rebase.it")
    assert code == 1
    assert any("bloccata: " in line for line in lines)
    assert lines[-1].startswith("indice uq_users_linkedin_lower non creato: 1 profili")
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]


def test_an_admin_can_choose_the_survivor_and_merge_a_pair_that_only_looks_alike(
    twice: Session,
) -> None:
    one = _user(twice, "uno@studio.it", telefono="333 1234567")
    two = _user(twice, "due@studio.it", nome="Luca", cognome="Bianchi", telefono="3331234567")
    admin = _user(twice, "admin@rebase.it", role="admin", nome="Root", cognome="Admin")
    keep, other = _card(twice, one, NOW), _card(twice, two, NOW + timedelta(days=1))
    twice.commit()
    code, lines = talent_duplicates(
        twice, apply=True, admin_email="admin@rebase.it", survivor=keep.id, absorbed=other.id
    )
    assert code == 0, lines
    twice.expire_all()
    assert twice.get(Freelancer, other.id).deleted_at is not None  # type: ignore[union-attr]
    assert admin.id  # the actor


def test_undo_by_action_id_puts_the_merge_back(twice: Session) -> None:
    survivor, absorbed, _ = _production_like(twice)
    _, lines = talent_duplicates(twice, apply=True, admin_email="admin@rebase.it")
    action = (
        next(line for line in lines if "unite (azione " in line).split("azione ")[1].split(")")[0]
    )
    code, undone = talent_duplicates(twice, undo=UUID(action), admin_email="admin@rebase.it")
    assert code == 0 and "annullata" in undone[0]
    twice.expire_all()
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]
    # Both cards held one profile: the survivor keeps it (the unique index wants one holder),
    # so the card that came back is only a «da rivedere» pair, by name.
    assert "indice unico" in undone[0]
    assert talent_duplicates(twice)[1][0].startswith(
        "0 coppie unibili (stesso LinkedIn), 1 da rivedere"
    )


def test_a_name_is_compared_without_accents_case_or_order_and_digits_count() -> None:
    from rebase_core.duplicates import _name_key

    assert _name_key("José", "Núñez") == _name_key("nunez", "JOSE")
    assert _name_key("Persona1", "Lovelace") != _name_key("Persona2", "Lovelace")
    assert _name_key("Иван", "Петров") == _name_key("иван", "ПЕТРОВ") is not None
    assert _name_key("", "Rossi") is None


def test_two_sign_ups_on_one_profile_block_only_their_own_index(twice: Session) -> None:
    twice.add(Signup(email="x@studio.it", linkedin_url=ADA))
    twice.add(Signup(email="y@studio.it", linkedin_url=ADA))
    twice.commit()
    ensured = ensure_unique_indexes(twice)
    assert ensured.blocked == {"uq_signups_linkedin_lower": 1}
    assert ensured.created == ["uq_users_linkedin_lower"]


def test_a_page_that_is_not_a_personal_profile_never_identifies_anybody(clean: Session) -> None:
    company = "https://www.linkedin.com/company/acme"
    assert linkedin_key(company) is None and linkedin_key("linkedin.com") is None
    assert linkedin_key("LinkedIn.com/in/Ada-Lovelace/") == ADA
    one, created = _apply(clean, "uno@studio.it", company)
    two, created_two = _apply(clean, "due@studio.it", company)
    assert (created, created_two) == (True, True) and one.id != two.id
    service = SignupService(clean)
    first = service.subscribe(
        SignupCreate(email="a@studio.it", nome="A", cognome="A", linkedin_url=company)
    )
    second = service.subscribe(
        SignupCreate(email="b@studio.it", nome="B", cognome="B", linkedin_url=company)
    )
    assert first.nuova and second.nuova
    assert find_duplicates(clean).unibili == []


def test_a_profile_held_by_somebody_with_no_card_is_not_taken_by_a_wizard_run(
    clean: Session,
) -> None:
    contact = _user(clean, "referente@acme.it", nome="Wile", cognome="Coyote", linkedin_url=ADA)
    clean.commit()
    read, created = _apply(clean, "ada@studio.it", ADA)
    assert created is True and read.email == "ada@studio.it"
    assert clean.get(User, contact.id).linkedin_url == ADA  # type: ignore[union-attr]
    mine = clean.scalar(select(User).where(User.email == "ada@studio.it"))
    assert mine is not None and mine.id != contact.id and mine.linkedin_url is None


def test_an_address_with_no_card_does_not_hide_a_profile_that_is_on_one(clean: Session) -> None:
    card, _ = _apply(clean, "vecchia@studio.it", ADA)
    _user(clean, "nuova@studio.it")
    clean.commit()
    read, created = _apply(clean, "nuova@studio.it", ADA)
    assert (read.id, created) == (card.id, False)
