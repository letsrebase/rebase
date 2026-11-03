"""The proposals as an admin reads and regenerates them (REB-511, spec § 3.4): the list with its
filters, a deleted talent kept by id, and «Rigenera» carrying the previous team and its note.
"""

from datetime import timedelta
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL, Scripted
from freelancer_fixtures import user_text
from sqlalchemy.orm import Session
from team_fixtures import (
    DESCRIPTION,
    NOW,
    SUMMARY,
    a_talent,
    a_user,
    catalogue_positions,
    make_builder,
    proposal_response,
    proposal_rows,
    team_member,
)

from rebase_core.db import uuid7
from rebase_core.errors import (
    LlmUnavailable,
    ValidationFailed,
)
from rebase_core.llm import (
    UNAVAILABLE_SENTENCE,
    RecordingCall,
)
from rebase_core.models import Freelancer, TeamProposal, TeamRequest
from rebase_core.team_schemas import TeamProposalCreate


def test_list_recent_pages_filters_and_names_the_request(clean_hub: Session) -> None:
    """«Proposte» (0028): newest first by cursor, every origin and outcome together
    unless filtered, each row with how many it held, who they are (REB-607) and the
    request filed on it."""
    first = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    owner = a_user(clean_hub, "referente@acme.it")
    builder = make_builder(
        clean_hub,
        Scripted(
            [
                proposal_response([team_member(ids[first])]),
                proposal_response([]),
                LlmUnavailable(UNAVAILABLE_SENTENCE),
            ]
        ),
    )
    public = builder.propose(
        TeamProposalCreate(description=DESCRIPTION, people=1), origin="public", user_id=None
    )
    cloud = builder.propose(
        TeamProposalCreate(description=DESCRIPTION, people=2, note="senza designer"),
        origin="cloud",
        user_id=owner,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(description=DESCRIPTION),
            origin="admin",
            user_id=owner,
            now=NOW + timedelta(minutes=2),
        )
    request_id = uuid7()
    clean_hub.add(
        TeamRequest(
            id=request_id,
            proposal_id=public.id,
            origin="public",
            company="Acme S.r.l.",
            email="wile@acme.it",
            phone="+39 345 1234567",
        )
    )
    clean_hub.commit()

    page = builder.list_recent(limit=2)
    assert [item.origin for item in page.items] == ["admin", "cloud"]
    assert page.next_cursor is not None
    attempt, regenerated = page.items
    assert (attempt.error, attempt.members, attempt.request_id, attempt.people) == (
        "llm_unavailable",
        0,
        None,
        None,
    )
    assert (regenerated.error, regenerated.members, regenerated.note, regenerated.user_id) == (
        None,
        0,
        "senza designer",
        owner,
    )
    assert (attempt.summary, regenerated.summary) == (None, SUMMARY)
    rest = builder.list_recent(limit=2, cursor=page.next_cursor)
    assert rest.next_cursor is None
    [filed] = rest.items
    assert (filed.id, filed.description, filed.people, filed.members, filed.request_id) == (
        public.id,
        DESCRIPTION,
        1,
        1,
        request_id,
    )
    assert filed.created_at == NOW
    # REB-607: the row names who it held, so the admin can open the profile from the
    # list; an attempt and a team nobody fit name nobody.
    [held] = filed.team
    assert (held.posizione, held.freelancer_id, held.role, held.nome, held.last_name) == (
        1,
        first,
        "Backend developer",
        "Ada",
        "Lovelace1",
    )
    assert (attempt.team, regenerated.team) == ([], [])

    # The limit is clamped, never refused: the MCP tool passes it through unchecked.
    assert len(builder.list_recent(limit=0).items) == 1
    assert len(builder.list_recent(limit=500).items) == 3
    assert [item.id for item in builder.list_recent(outcome="error").items] == [attempt.id]
    assert [item.id for item in builder.list_recent(outcome="ok").items] == [cloud.id, public.id]
    assert [item.id for item in builder.list_recent(origin="cloud").items] == [cloud.id]
    for field, kwargs in (
        ("origin", {"origin": "sito"}),
        ("outcome", {"outcome": "forse"}),
        ("cursor", {"cursor": "non-un-cursore"}),
    ):
        with pytest.raises(ValidationFailed) as refused:
            builder.list_recent(**kwargs)
        assert refused.value.details["field"] == field


def test_list_recent_keeps_a_deleted_talent_by_id_and_names_the_rest(clean_hub: Session) -> None:
    """REB-607: the list is about what was proposed, so a member removed with «Elimina»
    since (`deleted_at` set, the product's only delete: their admin page answers 404)
    stays in the row, unnamed, next to one still on file; a freelancer id the row
    holds and no row answers, which nothing in the product produces, reads the same."""
    kept = a_talent(clean_hub, 1)
    soft = a_talent(clean_hub, 2, deleted=True)
    gone = uuid7()
    clean_hub.add(
        TeamProposal(
            description=DESCRIPTION,
            summary=SUMMARY,
            location={"locale": False, "dove": None},
            team=[
                {
                    "posizione": index,
                    "freelancer_id": str(freelancer_id),
                    "role": role,
                    "reasoning": "Serve.",
                    "week_days": 5,
                }
                for index, (freelancer_id, role) in enumerate(
                    ((gone, "Designer"), (kept, "Backend developer"), (soft, "Data engineer")),
                    start=1,
                )
            ],
            economics={"day": None, "month": None, "days_month": 22},
            model=MODEL,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            origin="public",
        )
    )
    clean_hub.commit()

    [row] = make_builder(clean_hub, Scripted([])).list_recent().items
    assert row.members == 3
    assert [(m.posizione, m.freelancer_id, m.role, m.nome, m.last_name) for m in row.team] == [
        (1, gone, "Designer", None, None),
        (2, kept, "Backend developer", "Ada", "Lovelace1"),
        (3, soft, "Data engineer", None, None),
    ]


# ---- «Rigenera» --------------------------------------------------------------------------


def test_regenerate_carries_the_previous_team_and_note(clean_hub: Session) -> None:
    backend = a_talent(clean_hub, 1)
    designer = a_talent(clean_hub, 2, card={**CARD, "role": "Designer"})
    gone = a_talent(clean_hub, 3)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    team_member(ids[backend]),
                    team_member(ids[designer], "Product designer"),
                    team_member(ids[gone], "Tech lead"),
                ]
            ),
            proposal_response([team_member("t2")]),
        ]
    )
    builder = make_builder(clean_hub, llm)
    first = builder.propose(
        TeamProposalCreate(description=DESCRIPTION), origin="public", user_id=None
    )
    # Between the two asks the catalogue changes: someone is placed before everyone
    # (the positions shift by one), and a member of the first team leaves it.
    newcomer = a_talent(clean_hub, 4, freelancer_id=UUID("00000000-0000-7000-8000-000000000001"))
    row = clean_hub.get(Freelancer, gone)
    assert row is not None
    row.deleted_at = NOW
    clean_hub.commit()

    second = builder.propose(
        TeamProposalCreate(description=DESCRIPTION, note="togli il designer", previous_id=first.id),
        origin="public",
        user_id=None,
    )

    assert second.previous_id == first.id
    assert catalogue_positions(clean_hub)[newcomer] == "t1"
    turn = user_text(llm.requests[1])
    assert DESCRIPTION in turn
    assert SUMMARY in turn
    assert "t2: Backend developer" in turn  # t1 in the first call's catalogue
    assert "t3: Product designer" in turn  # t2 then
    assert "Tech lead" not in turn  # no longer in the catalogue
    assert "togli il designer" in turn
    assert llm.requests[1].system == llm.requests[0].system  # the rules never move
    rows = proposal_rows(clean_hub)
    assert [(r.previous_id, r.note) for r in rows] == [
        (None, None),
        (first.id, "togli il designer"),
    ]
    # The first ask carries no previous team and no note.
    assert "togli" not in user_text(llm.requests[0])


@pytest.mark.parametrize(
    "case", ["unknown", "other origin", "another cloud user", "older than a day"]
)
def test_previous_id_must_be_the_callers_and_younger_than_a_day(
    clean_hub: Session, case: str
) -> None:
    first_talent = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    owner = a_user(clean_hub, "referente@acme.it")
    stranger = a_user(clean_hub, "altro@beta.it")
    llm = RecordingCall([proposal_response([team_member(ids[first_talent])])] * 2)
    builder = make_builder(clean_hub, llm)
    origin = "public" if case == "other origin" else "cloud"
    previous = builder.propose(
        TeamProposalCreate(description=DESCRIPTION), origin=origin, user_id=owner
    )
    previous_id = uuid7() if case == "unknown" else previous.id
    caller = stranger if case == "another cloud user" else owner
    later = NOW + (timedelta(days=1) if case == "older than a day" else timedelta(hours=23))

    with pytest.raises(ValidationFailed) as refused:
        make_builder(clean_hub, llm, now=later).propose(
            TeamProposalCreate(description=DESCRIPTION, previous_id=previous_id),
            origin="cloud",
            user_id=caller,
        )

    assert refused.value.details["field"] == "previous_id"
    assert len(llm.requests) == 1  # the refusal pays for nothing
    assert len(proposal_rows(clean_hub)) == 1


def test_previous_id_of_the_same_cloud_user_within_the_day(clean_hub: Session) -> None:
    first_talent = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    owner = a_user(clean_hub, "referente@acme.it")
    llm = RecordingCall([proposal_response([team_member(ids[first_talent])])] * 2)
    previous = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(description=DESCRIPTION), origin="cloud", user_id=owner
    )

    read = make_builder(clean_hub, llm, now=NOW + timedelta(hours=23, minutes=59)).propose(
        TeamProposalCreate(description=DESCRIPTION, previous_id=previous.id),
        origin="cloud",
        user_id=owner,
    )

    assert read.previous_id == previous.id
