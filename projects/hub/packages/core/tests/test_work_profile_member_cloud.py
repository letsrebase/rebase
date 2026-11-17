"""What an approved work profile does outside the admin (REB-1127, spec 2026-11-17 §§ 5 and 7):
the cloud reads the summary of an approved, unexpired profile and nothing else of it; the person
reads the state of their own, and the note only of a rejected one; deleting it leaves the
vetted flag alone; the two decision mails say what they must."""

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD
from sqlalchemy import select
from sqlalchemy.orm import Session
from work_profile_helpers import START, Clock, person, sample, settings, sign_in

from rebase_core.cloud import CloudTalentService
from rebase_core.mail_member import italian_date
from rebase_core.members import MemberService
from rebase_core.models import Freelancer, FreelancerCard, User, WorkProfile
from rebase_core.team_schemas import CloudTalentQuery
from rebase_core.work_profiles.intake import WorkProfileService


def _admin(session: Session) -> UUID:
    user = User(email="ivan@rebase.it", first_name="Ivan", last_name="Rossi", role="admin")
    session.add(user)
    session.commit()
    return user.id


def _in_the_cloud(session: Session, clock: Clock, email: str) -> tuple[UUID, UUID]:
    """A person with a live anonymous card who sent a work profile (a draft)."""
    user_id, freelancer_id = person(session, email)
    assert freelancer_id is not None
    session.add(FreelancerCard(freelancer_id=freelancer_id, cv_sha256="0" * 64, card=dict(CARD)))
    session.commit()
    token = sign_in(session, clock, user_id)
    WorkProfileService(session, settings(), clock).submit(token, json.dumps(sample()).encode())
    return user_id, freelancer_id


def _cloud_profile(session: Session) -> Any:
    [read] = CloudTalentService(session).list(CloudTalentQuery()).items
    return read.work_profile


def test_the_cloud_reads_the_summary_of_an_approved_profile_only(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    _, freelancer_id = _in_the_cloud(hub_session, clock, "ada@studio.it")
    assert _cloud_profile(hub_session) is None  # a draft counts for nothing

    service = WorkProfileService(hub_session, settings(), clock)
    service.approve(freelancer_id, admin_id)
    shown = _cloud_profile(hub_session)

    assert shown.summary == sample()["summary"]
    assert shown.domains == [] and len(shown.project_domains) == 4
    dumped = shown.model_dump_json()
    for private in ("cognitive", "orchestrator", "Supabase", "subagentDelegations", "1125"):
        assert private not in dumped, private
    row = hub_session.scalars(select(WorkProfile)).one()
    row.expires_at = START - timedelta(days=1)  # past its 12 months
    hub_session.commit()
    assert _cloud_profile(hub_session) is None


def test_a_rejected_profile_is_not_in_the_cloud(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    _, freelancer_id = _in_the_cloud(hub_session, clock, "ada@studio.it")
    WorkProfileService(hub_session, settings(), clock).reject(freelancer_id, admin_id, "no")
    assert _cloud_profile(hub_session) is None


def test_the_person_reads_their_state_and_only_a_rejections_note(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    user_id, freelancer_id = _in_the_cloud(hub_session, clock, "ada@studio.it")
    members = MemberService(hub_session)

    waiting = members.me_read(user_id).work_profile
    assert waiting is not None and (waiting.state, waiting.decision_note) == ("draft", None)

    service = WorkProfileService(hub_session, settings(), clock)
    service.approve(freelancer_id, admin_id, "solo per noi")
    approved = members.me_read(user_id).work_profile
    assert approved is not None and (approved.state, approved.decision_note) == ("approved", None)

    other_user, other = _in_the_cloud(hub_session, clock, "grace@studio.it")
    service.reject(other, admin_id, "Manca un link.")
    rejected = members.me_read(other_user).work_profile
    assert rejected is not None and rejected.decision_note == "Manca un link."
    nobody, _ = person(hub_session, "none@studio.it")
    assert members.me_read(nobody).work_profile is None


def test_deleting_the_profile_removes_it_and_keeps_the_vetted_flag(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    user_id, freelancer_id = _in_the_cloud(hub_session, clock, "ada@studio.it")
    WorkProfileService(hub_session, settings(), clock).approve(freelancer_id, admin_id)

    after = MemberService(hub_session).delete_work_profile(user_id)

    assert after.work_profile is None
    assert hub_session.scalars(select(WorkProfile)).all() == []
    card = hub_session.get_one(Freelancer, freelancer_id, populate_existing=True)
    assert card.vetted_at is not None
    assert _cloud_profile(hub_session) is None
    MemberService(hub_session).delete_work_profile(user_id)  # asking twice is no error


def test_the_decision_mails_say_the_date_or_the_note_and_escape_what_was_typed(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    _, approved_id = _in_the_cloud(hub_session, clock, "ada@studio.it")
    _, rejected_id = _in_the_cloud(hub_session, clock, "grace@studio.it")
    service = WorkProfileService(hub_session, settings(), clock)
    assert service.decision_mail(approved_id) is None  # still in review: nobody's news

    service.approve(approved_id, admin_id)
    service.reject(rejected_id, admin_id, "Serve <un link> a un progetto.")

    yes = service.decision_mail(approved_id)
    no = service.decision_mail(rejected_id)
    assert yes is not None and no is not None
    assert yes.to == "ada@studio.it" and yes.subject == "Il tuo profilo di lavoro è stato approvato"
    assert "17 novembre 2027" in yes.text and "17 novembre 2027" in yes.html
    assert "https://letsrebase.com/hub/me" in yes.text
    assert no.subject == "Il tuo profilo di lavoro non è stato approvato"
    assert "Serve <un link> a un progetto." in no.text
    assert "&lt;un link&gt;" in no.html and "<un link>" not in no.html
    assert "—" not in yes.text + no.text  # no em dashes in what people read


@pytest.mark.parametrize(("month", "word"), [(1, "gennaio"), (8, "agosto"), (12, "dicembre")])
def test_italian_dates_use_the_month_names(month: int, word: str) -> None:
    assert italian_date(START.date().replace(month=month, day=5)) == f"5 {word} 2026"
