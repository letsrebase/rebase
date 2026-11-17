"""The review of a work profile (REB-1126, spec 2026-11-17 § 4): approval vets the person only
when they are not vetted, signs its own entry every time, rejection needs a note and leaves the
flag alone, an expired profile counts for nothing, and the «Talenti» filter reads the review
state."""

import json
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_hub_facts import _match
from work_profile_helpers import START, Clock, person, sample, seen, settings, sign_in

from rebase_core.errors import InvalidState, NotFound, ValidationFailed, WorkProfileGuardBlocked
from rebase_core.freelancers import FreelancerService
from rebase_core.models import AdminAction, Freelancer, User, WorkProfile
from rebase_core.talents import TalentsService
from rebase_core.work_profiles.intake import WorkProfileService
from rebase_core.work_profiles.schemas import WorkProfileApproval, WorkProfileRejection
from rebase_core.work_profiles.view import render_profile


def _admin(session: Session) -> UUID:
    user = User(email="ivan@rebase.it", first_name="Ivan", last_name="Rossi", role="admin")
    session.add(user)
    session.commit()
    return user.id


def _submitted(session: Session, clock: Clock, email: str = "ada@studio.it") -> UUID:
    user_id, freelancer_id = person(session, email)
    token = sign_in(session, clock, user_id)
    WorkProfileService(session, settings(), clock).submit(token, json.dumps(sample()).encode())
    assert freelancer_id is not None
    return freelancer_id


def _kinds(session: Session, freelancer_id: UUID) -> list[str]:
    return [
        action.kind
        for action in session.scalars(
            select(AdminAction)
            .where(AdminAction.entity_id == freelancer_id)
            .order_by(AdminAction.created_at, AdminAction.id)
        )
    ]


def test_approval_vets_the_person_and_signs_both_entries(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)

    read = WorkProfileService(hub_session, settings(), clock).approve(
        freelancer_id, admin_id, "ottimo", seen(hub_session, freelancer_id)
    )

    assert (read.state, read.decided_by_name, read.decision_note) == (
        "approved",
        "Ivan Rossi",
        "ottimo",
    )
    card = hub_session.get_one(Freelancer, freelancer_id, populate_existing=True)
    assert card.vetted_at is not None and card.vetted_by == admin_id
    assert sorted(_kinds(hub_session, freelancer_id)) == ["vetted", "work_profile_decided"]
    [decided] = [
        a for a in hub_session.scalars(select(AdminAction)) if a.kind == "work_profile_decided"
    ]
    assert decided.payload == {"decision": "approved", "vetted": True}
    assert "ottimo" not in json.dumps(decided.payload)


def test_approval_of_an_already_vetted_person_keeps_their_date_and_still_signs(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)
    FreelancerService(hub_session).set_vetted(freelancer_id, True, admin_id)
    first = hub_session.get_one(Freelancer, freelancer_id, populate_existing=True).vetted_at

    WorkProfileService(hub_session, settings(), clock).approve(
        freelancer_id, admin_id, None, seen(hub_session, freelancer_id)
    )

    card = hub_session.get_one(Freelancer, freelancer_id, populate_existing=True)
    assert card.vetted_at == first
    assert sorted(_kinds(hub_session, freelancer_id)) == ["vetted", "work_profile_decided"]
    decided = hub_session.scalars(
        select(AdminAction).where(AdminAction.kind == "work_profile_decided")
    ).one()
    assert decided.payload["vetted"] is False


def test_rejection_keeps_the_note_for_the_person_and_leaves_the_flag_alone(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)

    read = WorkProfileService(hub_session, settings(), clock).reject(
        freelancer_id, admin_id, "Manca un progetto con un link.", seen(hub_session, freelancer_id)
    )

    assert (read.state, read.decision_note) == ("rejected", "Manca un progetto con un link.")
    card = hub_session.get_one(Freelancer, freelancer_id, populate_existing=True)
    assert card.vetted_at is None
    assert _kinds(hub_session, freelancer_id) == ["work_profile_decided"]
    with pytest.raises(ValueError):  # the request body refuses a blank note
        WorkProfileRejection.model_validate({"note": "   ", "submitted_at": START})
    assert WorkProfileApproval.model_validate({"note": "  ", "submitted_at": START}).note is None


def test_a_decided_expired_or_missing_profile_cannot_be_decided(hub_session: Session) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)
    service = WorkProfileService(hub_session, settings(), clock)
    service.reject(freelancer_id, admin_id, "no", seen(hub_session, freelancer_id))
    with pytest.raises(InvalidState):
        service.approve(freelancer_id, admin_id, None, seen(hub_session, freelancer_id))

    other = _submitted(hub_session, clock, "grace@studio.it")
    clock.advance(days=366)
    with pytest.raises(InvalidState):
        service.approve(other, admin_id, None, seen(hub_session, other))
    assert service.admin_read(other).expired is True
    with pytest.raises(NotFound):
        service.admin_read(other, live_only=True)
    with pytest.raises(NotFound):
        service.approve(UUID(int=1), admin_id, None, seen(hub_session, UUID(int=1)))


def test_the_profile_is_rendered_from_its_payload(hub_session: Session) -> None:
    clock = Clock()
    freelancer_id = _submitted(hub_session, clock)

    read = WorkProfileService(hub_session, settings(), clock).admin_read(freelancer_id)

    view = read.profile
    assert read.groundedness == 89 and len(read.unsupported_anchors) == 3
    assert view.summary == sample()["summary"] and (view.products, view.sessions) == (23, 44)
    assert [lens.key for lens in view.lenses] == [
        "cognitive",
        "aiRelationship",
        "agenticLiteracy",
        "intensity",
    ]
    literacy = next(lens for lens in view.lenses if lens.key == "agenticLiteracy")
    assert ("Deleghe a sub-agenti", "70") in [(c.label, c.value) for c in literacy.counts]
    assert view.trajectory is not None and len(view.trajectory.principles) == 3
    assert [p.id for p in view.projects] == ["p1", "p2", "p3", "p4"]
    assert view.other_projects == len(sample()["otherProjects"])


def test_a_payload_of_odd_types_renders_without_failing(hub_session: Session) -> None:
    odd: dict[str, Any] = {
        "summary": 3,
        "domains": [{"label": "x", "products": "many"}, "nope"],
        "projects": [{"id": "p1", "artifact": {"type": "file", "url": "u"}}, {"no": "id"}],
        "cognitive": [],
    }
    view = render_profile(odd)
    assert view.summary is None and view.domains[0].products is None
    assert [p.artifact for p in view.projects] == [None] and view.lenses == []


def test_the_talents_filter_reads_the_review_state_of_a_live_profile(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    waiting = _submitted(hub_session, clock, "wait@studio.it")
    approved = _submitted(hub_session, clock, "yes@studio.it")
    _, nobody = person(hub_session, "none@studio.it")
    WorkProfileService(hub_session, settings(), clock).approve(
        approved, admin_id, None, seen(hub_session, approved)
    )
    talents = TalentsService(hub_session)

    def ids(state: str) -> set[UUID]:
        return {item.id for item in talents.list_recent(work_profile=state).items}

    assert ids("draft") == {waiting} and ids("approved") == {approved} and ids("none") == {nobody}
    row = talents.row_for_freelancer(waiting)
    assert row.work_profile_state == "draft"
    assert {i.id: i.work_profile_state for i in talents.list_recent().items}[approved] == "approved"
    with pytest.raises(ValidationFailed):
        talents.list_recent(work_profile="maybe")


def test_a_decision_names_the_version_read_and_a_resubmission_makes_it_a_409(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    user_id, freelancer_id = person(hub_session, "ada@studio.it")
    service = WorkProfileService(hub_session, settings(), clock)
    service.submit(sign_in(hub_session, clock, user_id), json.dumps(sample()).encode())
    old = seen(hub_session, freelancer_id)
    clock.advance(days=1)
    service.submit(sign_in(hub_session, clock, user_id), json.dumps(sample()).encode())

    with pytest.raises(InvalidState, match="rileggilo"):
        service.approve(freelancer_id, admin_id, None, old)
    with pytest.raises(InvalidState, match="rileggilo"):
        service.reject(freelancer_id, admin_id, "no", old)
    assert service.approve(freelancer_id, admin_id, None, seen(hub_session, freelancer_id))


def test_a_summary_naming_a_client_blocks_the_approval_and_an_override_audits_its_note(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)
    card = hub_session.get_one(Freelancer, freelancer_id)
    _match(hub_session, card, company_name="Initech Italia srl")
    row = hub_session.scalars(select(WorkProfile)).one()
    row.payload = {**row.payload, "summary": "Rebuilt the billing of Initech Italia with agents."}
    hub_session.commit()
    service = WorkProfileService(hub_session, settings(), clock)
    version = seen(hub_session, freelancer_id)

    with pytest.raises(WorkProfileGuardBlocked):
        service.approve(freelancer_id, admin_id, None, version)
    hub_session.expire_all()
    assert service.admin_read(freelancer_id).guard_hits[0].field == "summary"
    assert service.admin_read(freelancer_id).state == "draft" and card.vetted_at is None
    with pytest.raises(ValueError):  # the override needs a note
        WorkProfileApproval.model_validate({"override": True, "submitted_at": version})

    read = service.approve(
        freelancer_id, admin_id, "Il cliente è pubblico.", version, override=True
    )

    assert read.state == "approved"
    decided = hub_session.scalars(
        select(AdminAction).where(AdminAction.kind == "work_profile_decided")
    ).one()
    assert decided.payload["override"] is True
    assert decided.payload["override_note"] == "Il cliente è pubblico."


def test_a_clean_summary_approves_without_an_override(hub_session: Session) -> None:
    clock = Clock()
    freelancer_id = _submitted(hub_session, clock)
    assert (
        WorkProfileService(hub_session, settings(), clock).admin_read(freelancer_id).guard_hits
        == []
    )


def test_a_soft_deleted_person_loses_the_profile_and_cannot_be_decided(
    hub_session: Session,
) -> None:
    clock = Clock()
    admin_id = _admin(hub_session)
    freelancer_id = _submitted(hub_session, clock)
    version = seen(hub_session, freelancer_id)

    FreelancerService(hub_session).soft_delete(freelancer_id, admin_id)

    assert hub_session.scalars(select(WorkProfile)).all() == []
    service = WorkProfileService(hub_session, settings(), clock)
    with pytest.raises(NotFound):
        service.approve(freelancer_id, admin_id, None, version)
    assert service.decision_mail(freelancer_id) is None
