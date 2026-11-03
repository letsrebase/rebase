"""The catalogue the team builder shows Claude (REB-511, spec § 3.3): the anonymous cards, one
position each and nothing that names a person, and the prompt that carries it.
"""

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from fakes_cards import CARD
from sqlalchemy import JSON, func, null, select
from sqlalchemy.orm import Session
from team_fixtures import (
    DESCRIZIONE,
    FakeCapture,
    a_talent,
    catalogue_positions,
    make_builder,
    proposal_response,
    proposal_rows,
    team_member,
)

from rebase_core.analytics import Tracker
from rebase_core.db import uuid7
from rebase_core.llm import (
    LlmMessage,
    RecordingCall,
    _parameters,
)
from rebase_core.models import Freelancer, FreelancerCard
from rebase_core.team_builder import (
    NO_FIT_SENTENCE,
)
from rebase_core.team_catalogue import catalogue_lines, cloud_visible
from rebase_core.team_prompt import proposal_prompt
from rebase_core.team_schemas import TeamProposalCreate

# ---- the catalogue -----------------------------------------------------------------------


def test_catalogue_is_stable_and_anonymous(clean_hub: Session) -> None:
    first = a_talent(clean_hub, 1, tariffa=Decimal("450.00"), remoto="ibrido")
    second = a_talent(clean_hub, 2, tariffa=None, remoto=None)
    third = a_talent(
        clean_hub, 3, tariffa=Decimal("200.00"), card={**CARD, "ruolo": "Designer", "luogo": None}
    )

    catalogue, positions = catalogue_lines(clean_hub)

    assert positions == sorted([first, second, third])
    lines = [json.loads(line) for line in catalogue.splitlines()]
    assert [line["id"] for line in lines] == ["t1", "t2", "t3"]
    assert list(lines[0]) == [
        "id",
        "ruolo",
        "seniority",
        "anni",
        "seniority_incerta",
        "competenze",
        "settori",
        "lingue",
        "luogo",
        "modalita",
        "fascia",
    ]
    by_id = dict(zip(positions, lines, strict=True))
    assert by_id[first] == {
        "id": by_id[first]["id"],
        "ruolo": "Backend developer",
        "seniority": "senior",
        "anni": 9,
        "seniority_incerta": False,
        "competenze": ["Python", "FastAPI", "PostgreSQL", "AWS"],
        "settori": ["fintech", "e-commerce"],
        "lingue": ["italiano", "inglese"],
        "luogo": "Torino",
        "modalita": "ibrido",
        "fascia": "500–650",  # 450 x 1.4 = 630
    }
    assert (by_id[second]["modalita"], by_id[second]["fascia"]) == (None, None)
    assert (by_id[third]["ruolo"], by_id[third]["luogo"]) == ("Designer", None)
    assert by_id[third]["fascia"] == "fino a 300"  # 200 x 1.4 = 280
    # No name, no link, no summary, no rate, no vetted flag, no id of ours.
    for secret in ("Lovelace", "Ada", "github", "http", "@", "sintesi", "450", "200", "vetted"):
        assert secret not in catalogue
    for freelancer_id in positions:
        assert str(freelancer_id) not in catalogue
    # The same text for every visitor: the prompt's cached prefix.
    assert catalogue_lines(clean_hub) == (catalogue, positions)


def test_the_catalogue_reads_the_level_an_admin_set_and_marks_a_guess(clean_hub: Session) -> None:
    """REB-799: the engine picks people on seniority and years, so it reads the admin's
    over Claude's, and a card with no dated role says `anni` null and the seniority
    uncertain, until an admin sets one."""
    corrected = a_talent(clean_hub, 1)
    clean_hub.get_one(Freelancer, corrected).seniority_admin = "lead"
    clean_hub.get_one(Freelancer, corrected).anni_admin = 14
    undated = a_talent(clean_hub, 2, card={**CARD, "anni": None, "seniority": "mid"})
    clean_hub.commit()

    catalogue, positions = catalogue_lines(clean_hub)

    by_id = dict(zip(positions, (json.loads(line) for line in catalogue.splitlines()), strict=True))
    assert (by_id[corrected]["seniority"], by_id[corrected]["anni"]) == ("lead", 14)
    assert by_id[corrected]["seniority_incerta"] is False
    assert (by_id[undated]["seniority"], by_id[undated]["anni"]) == ("mid", None)
    assert by_id[undated]["seniority_incerta"] is True

    clean_hub.get_one(Freelancer, undated).seniority_admin = "senior"
    clean_hub.commit()
    catalogue, positions = catalogue_lines(clean_hub)
    assert (
        json.loads(catalogue.splitlines()[positions.index(undated)])["seniority_incerta"] is False
    )


def test_catalogue_positions_are_unique(clean_hub: Session) -> None:
    """Two freelancers who signed up in the same millisecond share a UUIDv7's leading
    characters, so a slice of the id could collide; a line's position never does."""
    earlier = uuid7()
    later = UUID(int=earlier.int + 1)
    assert str(earlier)[:13] == str(later)[:13]
    a_talent(clean_hub, 2, freelancer_id=later)
    a_talent(clean_hub, 1, freelancer_id=earlier)

    catalogue, positions = catalogue_lines(clean_hub)

    assert positions == [earlier, later]
    assert [json.loads(line)["id"] for line in catalogue.splitlines()] == ["t1", "t2"]


def test_catalogue_is_every_live_freelancer_with_a_card(clean_hub: Session) -> None:
    visible = a_talent(clean_hub, 1)
    a_talent(clean_hub, 2, deleted=True)
    a_talent(clean_hub, 3, stato="scartato")
    a_talent(clean_hub, 4, card=None)  # no card row at all
    sql_null = a_talent(clean_hub, 5, card=null())  # a row with no card yet, only an error
    json_null = a_talent(clean_hub, 6, card=JSON.NULL)  # a card retired to JSON `null`
    contacted = a_talent(clean_hub, 7, stato="contattato")
    kinds: dict[UUID, str | None] = {
        freelancer_id: kind
        for freelancer_id, kind in clean_hub.execute(
            select(FreelancerCard.freelancer_id, func.jsonb_typeof(FreelancerCard.card))
        )
    }
    assert (kinds[sql_null], kinds[json_null]) == (None, "null")

    assert sorted(clean_hub.scalars(cloud_visible(select(Freelancer.id)))) == sorted(
        [visible, contacted]
    )
    _, positions = catalogue_lines(clean_hub)
    assert positions == sorted([visible, contacted])


def test_proposal_prompt_is_byte_identical_in_substance_to_before() -> None:
    """REB-831 moved the proposal onto the provider-neutral request: the model still reads
    the same words, in the same blocks. Taken from the version before it (`proposal_prompt`
    of 1ccb4167, run on these inputs): the rules by their hash, the catalogue block, the
    user turn on «Rigenera» with a number and a note, the empty catalogue, and the schema."""
    member = UUID("00000000-0000-0000-0000-000000000001")
    previous = SimpleNamespace(
        riassunto="Un team di due", team=[{"freelancer_id": str(member), "ruolo": "Backend"}]
    )
    description = "Un portale clienti per un'assicurazione che vuole gestire i sinistri online"

    request = proposal_prompt(
        "t1: backend, senior",
        TeamProposalCreate(descrizione=description, nota="togli il designer", persone=3),
        previous,  # type: ignore[arg-type]
        positions=[member],
    )

    assert hashlib.sha256(request.system.encode()).hexdigest() == (
        "0728091ef3a44d4e0491cb09dc830d018d6f140aa50d6fab2b87bfd790918924"
    )
    assert request.system_cached == "The catalogue:\nt1: backend, senior"
    assert [(m.role, m.text) for m in request.messages] == [
        (
            "user",
            "The project's description:\n<descrizione>\n" + description + "\n</descrizione>"
            "\n\nThe visitor wants a team of exactly 3 people."
            "\n\nYou already proposed a team for this project, and the visitor asks for "
            "another one. Your previous proposal (people no longer in the catalogue are "
            "left out):\n<proposta_precedente>\nriassunto: Un team di due\nteam:\n- t1: "
            "Backend\n</proposta_precedente>"
            "\n\nThe visitor's note:\n<nota>\ntogli il designer\n</nota>\nFollow the "
            "note, and keep what it does not ask to change.",
        )
    ]
    empty = proposal_prompt("", TeamProposalCreate(descrizione=description), None)
    assert empty.system_cached == "The catalogue:\n(empty: no freelancer has a card yet)"
    assert empty.messages == [
        LlmMessage(
            role="user",
            text="The project's description:\n<descrizione>\n" + description + "\n</descrizione>",
        )
    ]
    schema = _parameters(request).output_object
    assert schema is not None
    assert hashlib.sha256(json.dumps(schema.json_schema, sort_keys=True).encode()).hexdigest() == (
        "25135283e23511e59d89e9b22b365c693f67206a7fc6581bd569ef1d8a765409"
    )


def test_the_catalogue_block_is_the_same_for_two_calls(clean_hub: Session) -> None:
    first = a_talent(clean_hub, 1)
    a_talent(clean_hub, 2)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[first])])] * 2)
    builder = make_builder(clean_hub, llm)

    builder.propose(TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None)
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE + " Anche un designer."),
        origine="pubblico",
        user_id=None,
    )

    one, two = llm.requests
    assert one.system == two.system
    assert one.messages != two.messages


def test_an_empty_catalogue_asks_nobody(clean_hub: Session) -> None:
    a_talent(clean_hub, 1, deleted=True)  # a card, but not in the catalogue
    llm = RecordingCall([])  # any call would fail on an empty script
    capture = FakeCapture()

    read = make_builder(clean_hub, llm, tracker=Tracker(capture)).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert llm.requests == []
    assert (read.riassunto, read.team) == (NO_FIT_SENTENCE, [])
    assert read.luogo.model_dump() == {"locale": False, "dove": None}
    assert read.economia.model_dump() == {"giorno": None, "mese": None, "giorni_mese": 22}
    # The row is written all the same, so «Rigenera» and a later read find it; it cost
    # nothing, and says so.
    [row] = proposal_rows(clean_hub)
    assert row.id == read.id
    assert (row.model, row.input_tokens, row.output_tokens, row.cache_read_tokens) == ("", 0, 0, 0)
    [(_, _, properties)] = capture.calls
    assert (properties["persone"], properties["input_tokens"], properties["output_tokens"]) == (
        0,
        0,
        0,
    )
