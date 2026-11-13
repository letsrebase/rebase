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
from fakes_crm_door import FakeCrmDoor
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core.cli.members import talent_duplicates
from rebase_core.duplicates import (
    FIRST_NAME,
    FLAGS_PER_ROW,
    LINKEDIN,
    PHONE,
    UNIQUE_INDEXES,
    IndexesEnsured,
    duplicate_list,
    duplicates_of,
    ensure_unique_indexes,
    find_duplicates,
)
from rebase_core.errors import ValidationFailed
from rebase_core.freelancer_schemas import (
    FreelancerCreate,
    FreelancerDraft,
    FreelancerOverride,
    MemberUpdate,
)
from rebase_core.freelancers import LINKEDIN_HAS_CARD, FreelancerService
from rebase_core.linkedin import linkedin_key
from rebase_core.members import MemberService
from rebase_core.merge import PersonMergeService
from rebase_core.models import Freelancer, Signup, User, UserContactPoint
from rebase_core.service import SignupService
from rebase_core.signup_schemas import SignupCreate
from rebase_core.talents import TalentsService
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
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": email,
        "linkedin_url": linkedin,
        "daily_rate": Decimal("450"),
        "job_title": "Backend developer",
        "remote": "remote",
    }
    payload.update(extra)
    return FreelancerService(session).apply(FreelancerCreate(**payload))


def _user(session: Session, email: str, **extra: Any) -> User:
    phone = extra.pop("phone", None)
    row = User(
        email=email,
        first_name=extra.pop("first_name", "Marco"),
        last_name=extra.pop("last_name", "Rossi"),
        **extra,
    )
    session.add(row)
    session.flush()
    if phone is not None:
        session.add(
            UserContactPoint(
                user_id=row.id, kind="phone", value=phone, label="work", is_primary=True
            )
        )
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
        SignupCreate(email="a@studio.it", first_name="Ada", last_name="Lovelace", linkedin_url=ADA)
    )
    second = service.subscribe(
        SignupCreate(
            email="b@studio.it",
            first_name="Ada",
            last_name="Lovelace",
            linkedin_url="linkedin.com/in/ADA-lovelace/",
        )
    )
    assert (first.new, second.new, second.id) == (True, False, first.id)
    assert clean.scalar(text("SELECT count(*) FROM signups")) == 1


def test_a_repeat_signup_does_not_fill_in_a_profile_another_row_holds(clean: Session) -> None:
    service = SignupService(clean)
    service.subscribe(
        SignupCreate(email="a@studio.it", first_name="A", last_name="A", linkedin_url=ADA)
    )
    service.subscribe(SignupCreate(email="b@studio.it", first_name="B", last_name="B"))
    service.subscribe(
        SignupCreate(email="b@studio.it", first_name="B", last_name="B", linkedin_url=ADA)
    )
    other = clean.scalar(select(Signup).where(Signup.email == "b@studio.it"))
    assert other is not None and other.linkedin_url is None


def test_the_database_refuses_two_people_with_one_profile(clean: Session) -> None:
    _user(clean, "a@b.it", linkedin_url=ADA)
    clean.commit()
    clean.add(User(email="c@b.it", first_name="C", last_name="C", linkedin_url=ADA.upper()))
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
        SignupCreate(email="altra@studio.it", first_name="Ada", last_name="Lovelace")
    )
    draft = FreelancerDraft(
        first_name="Ada",
        last_name="Lovelace",
        linkedin_url=ADA,
        job_title="Backend",
        sources=["https://www.linkedin.com/in/ada-lovelace"],
    )
    with pytest.raises(ValidationFailed) as refused:
        FreelancerService(clean).draft_from_signup(signup.id, draft, an_admin(clean), "web")
    assert refused.value.details["reason"] == LINKEDIN_HAS_CARD


def test_a_member_cannot_take_the_profile_of_another_card(clean: Session) -> None:
    ada, _ = _apply(clean, "ada@studio.it", ADA)
    bob, _ = _apply(clean, "bob@studio.it", "https://www.linkedin.com/in/bob")
    update = MemberUpdate(
        first_name="Bob",
        last_name="Lovelace",
        linkedin_url=ADA,
        daily_rate=Decimal("450"),
        job_title="Backend",
        remote="remote",
        links=[],
    )
    with pytest.raises(ValidationFailed) as refused:
        MemberService(clean).update(bob.id, update)
    assert refused.value.details["reason"] == LINKEDIN_TAKEN
    assert ada.id != bob.id


def test_an_admin_override_cannot_take_the_profile_of_another_card(clean: Session) -> None:
    _apply(clean, "ada@studio.it", ADA)
    bob, _ = _apply(clean, "bob@studio.it", "https://www.linkedin.com/in/bob")
    admin = _user(clean, "admin@rebase.it", role="admin", first_name="Root")
    clean.commit()
    with pytest.raises(ValidationFailed):
        FreelancerService(clean).override(bob.id, FreelancerOverride(linkedin_url=ADA), admin.id)


def test_a_sign_up_whose_profile_is_on_a_card_is_no_lead(clean: Session) -> None:
    _apply(clean, "ada@studio.it", ADA)
    SignupService(clean).subscribe(
        SignupCreate(email="ada@studio.it", first_name="Ada", last_name="Lovelace")
    )
    clean.add(Signup(email="altra@studio.it", first_name="Ada", last_name="L", linkedin_url=None))
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
    talents = TalentsService(clean).list_recent(limit=50)
    assert sorted(i.email for i in talents.items if i.status == "lead") == ["fantasma@studio.it"]


# ---- the finder --------------------------------------------------------------------------


def test_two_cards_with_one_profile_are_one_mergeable_pair_and_the_newest_cv_wins(
    twice: Session,
) -> None:
    older = _user(twice, "vecchia@studio.it", last_name="Lanzo", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", last_name="Lanzo", linkedin_url=ADA.upper())
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    twice.commit()

    report = find_duplicates(twice)

    assert len(report.mergeable) == 1 and report.to_review == []
    pair = report.mergeable[0]
    assert pair.survivor.freelancer_id == new_card.id
    assert pair.absorbed.freelancer_id == old_card.id
    assert LINKEDIN in pair.signals and FIRST_NAME in pair.signals


def test_the_same_name_or_telephone_only_flags_and_is_never_mergeable(twice: Session) -> None:
    one = _user(twice, "uno@studio.it", phone="+39 333 1234567")
    two = _user(twice, "due@studio.it", phone="333-1234567", first_name="Luca", last_name="Bianchi")
    three = _user(twice, "tre@studio.it", first_name="rossi", last_name="MARCO")
    for user in (one, two, three):
        _card(twice, user)
    twice.commit()

    report = find_duplicates(twice)

    assert report.mergeable == []
    signals = sorted(p.signals for p in report.to_review)
    assert signals == [(FIRST_NAME,), (PHONE,)]
    assert not any(p.mergeable for p in report.to_review)


def test_to_pair_with_an_admins_card_is_not_mergeable_and_keeps_the_admin(twice: Session) -> None:
    admin = _user(twice, "admin@rebase.it", role="admin", linkedin_url=ADA)
    member = _user(
        twice, "membro@rebase.it", first_name="Ada", last_name="Lovelace", linkedin_url=ADA
    )
    _card(twice, admin, NOW - timedelta(days=90))
    _card(twice, member, NOW)
    twice.commit()
    report = find_duplicates(twice)
    assert report.mergeable != [] or report.to_review != []
    pair = (report.mergeable + report.to_review)[0]
    assert pair.survivor.admin is True and pair.absorbed.admin is False


def test_a_merged_card_is_no_longer_a_pair(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA.upper())
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    admin = _user(twice, "admin@rebase.it", role="admin", first_name="Root", last_name="Admin")
    twice.commit()
    PersonMergeService(twice, FakeCrmDoor().door()).merge(new_card.id, old_card.id, admin.id)
    report = find_duplicates(twice)
    assert report.mergeable == [] and report.to_review == []


def test_sign_ups_that_repeat_a_profile_are_listed_and_never_merged(twice: Session) -> None:
    card_user = _user(twice, "ada@studio.it", linkedin_url=ADA)
    _card(twice, card_user)
    twice.add(Signup(email="altra@studio.it", linkedin_url=ADA))
    twice.add(Signup(email="x@studio.it", linkedin_url="https://www.linkedin.com/in/x"))
    twice.add(Signup(email="y@studio.it", linkedin_url="https://www.linkedin.com/in/x"))
    twice.add(Signup(email="ada@studio.it", linkedin_url=ADA))
    twice.commit()
    report = find_duplicates(twice)
    assert report.mergeable == []
    reasons = sorted("card" if item.card_user_id else "signup" for item in report.signups)
    # `altra` repeats the card's profile; `y` repeats `x`; and `ada@`, the card's own
    # address, repeats `altra`: two sign-ups on one profile either way.
    assert reasons == ["card", "signup", "signup"]


def test_the_unique_indexes_wait_for_the_merge_and_are_then_created(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA)
    old_card = _card(twice, older, NOW - timedelta(days=40))
    new_card = _card(twice, newer, NOW)
    admin = _user(twice, "admin@rebase.it", role="admin", first_name="Root", last_name="Admin")
    twice.commit()
    # While the pair is there the users' index is refused, by name and count, not raised.
    waiting = ensure_unique_indexes(twice)
    assert waiting.blocked == {"uq_users_linkedin_lower": 1}
    assert waiting.created == ["uq_signups_linkedin_lower"]
    PersonMergeService(twice, FakeCrmDoor().door()).merge(new_card.id, old_card.id, admin.id)
    done = ensure_unique_indexes(twice)
    assert done.created == ["uq_users_linkedin_lower"] and done.blocked == {}
    assert ensure_unique_indexes(twice) == IndexesEnsured()


def test_to_talents_row_names_the_cards_it_may_be(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", last_name="Lanzo", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", last_name="Lanzo", linkedin_url=ADA)
    alone = _user(twice, "sola@studio.it", first_name="Sola", last_name="Persona")
    old_card, new_card, alone_card = (
        _card(twice, older, NOW - timedelta(days=40)),
        _card(twice, newer, NOW),
        _card(twice, alone),
    )
    twice.commit()
    rows = {row.id: row for row in TalentsService(twice).list_recent(limit=50).items}
    assert rows[old_card.id].duplicate_possible_of == [new_card.id]
    assert rows[new_card.id].duplicate_possible_of == [old_card.id]
    assert rows[alone_card.id].duplicate_possible_of == []
    one = TalentsService(twice).row_for_freelancer(old_card.id)
    assert one.duplicate_possible_of == [new_card.id]


def test_the_admin_list_names_both_sides_of_a_pair(twice: Session) -> None:
    older = _user(twice, "vecchia@studio.it", linkedin_url=ADA)
    newer = _user(twice, "nuova@studio.it", linkedin_url=ADA)
    _card(twice, older, NOW - timedelta(days=40))
    _card(twice, newer, NOW)
    twice.commit()
    listed = duplicate_list(twice)
    assert len(listed.mergeable) == 1 and listed.to_review == [] and listed.signups == 0
    pair = listed.mergeable[0]
    assert (pair.survivor.email, pair.absorbed.email) == ("nuova@studio.it", "vecchia@studio.it")
    assert pair.mergeable is True and pair.survivor.has_cv is True


# ---- `rebase talent-duplicates` ---------------------------------------------------------


def _production_like(session: Session) -> tuple[Freelancer, Freelancer, User]:
    older = _user(session, "vecchia@studio.it", last_name="Lanzo", linkedin_url=ADA)
    newer = _user(session, "nuova@studio.it", last_name="Lanzo", linkedin_url=ADA)
    admin = _user(session, "admin@rebase.it", role="admin", first_name="Root", last_name="Admin")
    old_card = _card(session, older, NOW - timedelta(days=40))
    new_card = _card(session, newer, NOW)
    session.commit()
    return new_card, old_card, admin


def test_the_dry_run_prints_counts_and_ids_never_a_name_an_address_or_a_profile(
    twice: Session,
) -> None:
    survivor, absorbed, _ = _production_like(twice)
    code, lines = talent_duplicates(twice, FakeCrmDoor().door())
    text_ = "\n".join(lines)
    assert code == 0
    assert lines[0].startswith("1 mergeable pairs (same LinkedIn), 0 to review")
    assert f"keep {survivor.id} absorb {absorbed.id}" in text_
    assert "would move: nothing" in text_
    assert "unique indexes missing: uq_users_linkedin_lower" in text_
    for private in ("studio.it", "Lanzo", "Marco", "linkedin.com", ADA):
        assert private not in text_
    # Reading wrote nothing.
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]


def test_apply_merges_the_pair_with_the_newest_cv_and_then_creates_the_indexes(
    twice: Session,
) -> None:
    survivor, absorbed, _ = _production_like(twice)
    code, lines = talent_duplicates(
        twice, FakeCrmDoor().door(), apply=True, admin_email="ADMIN@rebase.it"
    )
    assert code == 0, lines
    twice.expire_all()
    assert twice.get(Freelancer, absorbed.id).deleted_at is not None  # type: ignore[union-attr]
    assert twice.get(Freelancer, survivor.id).deleted_at is None  # type: ignore[union-attr]
    assert any("merged (action " in line for line in lines)
    assert lines[-1].startswith("unique indexes created: uq_users_linkedin_lower")
    code, again = talent_duplicates(twice, FakeCrmDoor().door())
    assert again[0].startswith("0 mergeable pairs") and again[-1] == "unique indexes missing: none"


def test_apply_and_undo_need_an_admin_and_say_so(twice: Session) -> None:
    _production_like(twice)
    assert talent_duplicates(twice, FakeCrmDoor().door(), apply=True)[0] == 2
    code, lines = talent_duplicates(
        twice, FakeCrmDoor().door(), apply=True, admin_email="vecchia@studio.it"
    )
    assert code == 1 and "No administrator" in lines[0]


def test_a_pair_a_merge_refuses_is_reported_and_the_rest_goes_on(twice: Session) -> None:
    survivor, absorbed, admin = _production_like(twice)
    said = {
        "framework_regimes": [],
        "shared_clients": 0,
        "tax_code_conflict": True,
        "organization": False,
        "other_organization": False,
    }
    crm = FakeCrmDoor(merge={(survivor.id, absorbed.id): said, (absorbed.id, survivor.id): said})
    _, dry = talent_duplicates(twice, crm.door())
    assert any("blocked: " in line and "codici fiscali diversi" in line for line in dry)
    code, lines = talent_duplicates(twice, crm.door(), apply=True, admin_email="admin@rebase.it")
    assert code == 1
    assert any("blocked: " in line for line in lines)
    assert lines[-1].startswith("index uq_users_linkedin_lower not created: 1 profiles")
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]


def test_an_admin_can_choose_the_survivor_and_merge_a_pair_that_only_looks_alike(
    twice: Session,
) -> None:
    one = _user(twice, "uno@studio.it", phone="333 1234567")
    two = _user(twice, "due@studio.it", first_name="Luca", last_name="Bianchi", phone="3331234567")
    admin = _user(twice, "admin@rebase.it", role="admin", first_name="Root", last_name="Admin")
    keep, other = _card(twice, one, NOW), _card(twice, two, NOW + timedelta(days=1))
    twice.commit()
    code, lines = talent_duplicates(
        twice,
        FakeCrmDoor().door(),
        apply=True,
        admin_email="admin@rebase.it",
        survivor=keep.id,
        absorbed=other.id,
    )
    assert code == 0, lines
    twice.expire_all()
    assert twice.get(Freelancer, other.id).deleted_at is not None  # type: ignore[union-attr]
    assert admin.id  # the actor


def test_undo_by_action_id_puts_the_merge_back(twice: Session) -> None:
    survivor, absorbed, _ = _production_like(twice)
    _, lines = talent_duplicates(
        twice, FakeCrmDoor().door(), apply=True, admin_email="admin@rebase.it"
    )
    action = (
        next(line for line in lines if "merged (action " in line).split("action ")[1].split(")")[0]
    )
    code, undone = talent_duplicates(
        twice, FakeCrmDoor().door(), undo=UUID(action), admin_email="admin@rebase.it"
    )
    assert code == 0 and "undone" in undone[0]
    twice.expire_all()
    assert twice.get(Freelancer, absorbed.id).deleted_at is None  # type: ignore[union-attr]
    # Both cards held one profile: the survivor keeps it (the unique index wants one holder),
    # so the card that came back is only a «to review» pair, by name.
    assert "unique index" in undone[0]
    assert talent_duplicates(twice, FakeCrmDoor().door())[1][0].startswith(
        "0 mergeable pairs (same LinkedIn), 1 to review"
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
        SignupCreate(email="a@studio.it", first_name="A", last_name="A", linkedin_url=company)
    )
    second = service.subscribe(
        SignupCreate(email="b@studio.it", first_name="B", last_name="B", linkedin_url=company)
    )
    assert first.new and second.new
    assert find_duplicates(clean).mergeable == []


def test_a_profile_held_by_somebody_with_no_card_is_not_taken_by_a_wizard_run(
    clean: Session,
) -> None:
    contact = _user(
        clean, "referente@acme.it", first_name="Wile", last_name="Coyote", linkedin_url=ADA
    )
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


# ---- a Talenti page reads its own cards' neighbours, not the table (REB-891) -------------


def _full_scan_flags(session: Session, ids: list[UUID]) -> dict[UUID, list[UUID]]:
    """What `duplicates_of` answered before it stopped reading the table: the whole report,
    then the cards of the page picked out of its pairs, mergeable pairs first."""
    wanted = set(ids)
    answer: dict[UUID, list[UUID]] = {}
    report = find_duplicates(session)
    for pair in (*report.mergeable, *report.to_review):
        for mine, other in (
            (pair.survivor, pair.absorbed),
            (pair.absorbed, pair.survivor),
        ):
            if mine.freelancer_id in wanted:
                flagged = answer.setdefault(mine.freelancer_id, [])
                if len(flagged) < FLAGS_PER_ROW:
                    flagged.append(other.freelancer_id)
    return answer


def _seed_a_crowd(session: Session) -> list[UUID]:
    """Forty cards where every kind of match, and every kind of near miss, is present."""
    people: list[dict[str, Any]] = [
        # One profile on three cards, one of them an admin's.
        {"first_name": "Ada", "last_name": "Lovelace", "linkedin_url": ADA},
        {"first_name": "Augusta", "last_name": "King", "linkedin_url": ADA},
        {"first_name": "Lady", "last_name": "Byron", "linkedin_url": ADA, "role": "admin"},
        # One name written four ways: case, accents, the two words swapped, punctuation.
        {"first_name": "José", "last_name": "Álvarez"},
        {"first_name": "jose", "last_name": "alvarez"},
        {"first_name": "ÁLVAREZ", "last_name": "José"},
        {"first_name": "Jose'", "last_name": "Alvarez-"},
        # One number written four ways, and a short one nobody shares.
        {"first_name": "Uno", "last_name": "Telefono", "phone": "+39 333 1234567"},
        {"first_name": "Due", "last_name": "Telefono", "phone": "0039 3331234567"},
        {"first_name": "Tre", "last_name": "Telefono", "phone": "333-123-4567"},
        {"first_name": "Quattro", "last_name": "Telefono", "phone": "3331234567"},
        {"first_name": "Corto", "last_name": "Numero", "phone": "12345"},
        {"first_name": "Corto", "last_name": "Altro", "phone": "12345"},
        # More than the five flags a row carries: seven people called Maria Bianchi.
        *({"first_name": "Maria", "last_name": "Bianchi"} for _ in range(7)),
        # Not Latin letters, an empty word, and a number outside ASCII.
        {"first_name": "山田", "last_name": "太郎"},
        {"first_name": "山田", "last_name": "太郎"},
        {"first_name": "Solo", "last_name": ""},
        {"first_name": "Solo", "last_name": ""},
        {"first_name": "Arabo", "last_name": "Uno", "phone": "٠٣٣٣١٢٣٤٥٦٧"},
        {"first_name": "Arabo", "last_name": "Due", "phone": "٠٣٣٣١٢٣٤٥٦٧"},
        # Alone.
        *({"first_name": f"Sola{n}", "last_name": f"Persona{n}"} for n in range(6)),
    ]
    ids: list[UUID] = []
    for n, person in enumerate(people):
        user = _user(session, f"p{n}@studio.it", **person)
        ids.append(_card(session, user, NOW - timedelta(days=n)).id)
    # The same name as a card that was deleted, and as a person merged away: neither counts.
    gone = _user(session, "gone@studio.it", first_name="Maria", last_name="Bianchi")
    _card(session, gone).deleted_at = NOW
    merged = _user(session, "merged@studio.it", first_name="Maria", last_name="Bianchi")
    _card(session, merged)
    merged.merged_into_id = gone.id
    session.commit()
    return ids


def test_a_page_flags_exactly_what_a_scan_of_every_card_flags(twice: Session) -> None:
    ids = _seed_a_crowd(twice)
    pages = [ids[start : start + 7] for start in range(0, len(ids), 7)]
    pages += [[ids[0]], [ids[3], ids[-1]], ids[::2], ids]
    for page in pages:
        assert duplicates_of(twice, page) == _full_scan_flags(twice, page)
    flagged = duplicates_of(twice, ids)
    assert sum(1 for others in flagged.values() if others) >= 20  # the crowd really matches
    assert max(len(others) for others in flagged.values()) == FLAGS_PER_ROW


def test_the_list_flags_a_pair_that_straddles_two_pages(twice: Session) -> None:
    ids = _seed_a_crowd(twice)
    seen: dict[UUID, list[UUID]] = {}
    cursor = None
    pages = 0
    while True:
        page = TalentsService(twice).list_recent(limit=6, cursor=cursor)
        pages += 1
        seen.update({row.id: row.duplicate_possible_of for row in page.items})
        cursor = page.next_cursor
        if cursor is None:
            break
    assert pages > 3 and set(ids) <= set(seen)
    assert {i: seen[i] for i in ids} == {i: _full_scan_flags(twice, ids).get(i, []) for i in ids}


def test_a_page_reads_a_fixed_number_of_statements(twice: Session) -> None:
    ids = _seed_a_crowd(twice)
    statements: list[str] = []

    def count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = twice.get_bind()
    event.listen(engine, "before_cursor_execute", count)
    try:
        duplicates_of(twice, ids[:10])
        small = len(statements)
        statements.clear()
        duplicates_of(twice, ids)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert small == len(statements) == 2
