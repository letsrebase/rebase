"""The team builder's engine (REB-511, spec § 3.3, § 3.4): a project's description and
the catalogue of anonymous cards go to Claude, and the answer comes back as a team of
freelancers with their price bands, written as a proposal row.

Claude is a `RecordingCall` answering canned proposals, or `Scripted` where a test
needs an outage or something to happen during the call; the freelancers and their cards
are written straight into the tables (`team_fixtures.py`), since what is under test is what
the engine reads from them, not how a card is written (`test_cards.py`).
"""

import json
import logging
from decimal import Decimal
from typing import Any

import pytest
from fakes_cards import CARD, MODEL, Scripted
from freelancer_fixtures import user_text
from sqlalchemy.orm import Session
from team_fixtures import (
    DESCRIZIONE,
    NOW,
    RIASSUNTO,
    SETTINGS,
    FakeCapture,
    a_talent,
    a_user,
    catalogue_positions,
    make_builder,
    make_settings,
    proposal_response,
    proposal_rows,
    team_member,
)

from rebase_core.analytics import TEAM_PROPOSAL_GENERATED, Tracker
from rebase_core.bands import Band
from rebase_core.config import Settings
from rebase_core.errors import (
    LlmUnavailable,
    NotFound,
    TeamBuilderBusy,
    TeamBuilderOff,
    ValidationFailed,
)
from rebase_core.llm import (
    UNAVAILABLE_SENTENCE,
    LlmResponse,
    RecordingCall,
)
from rebase_core.models import Freelancer
from rebase_core.team_builder import (
    BUSY_SENTENCE,
    NO_FIT_SENTENCE,
    OFF_SENTENCE,
)
from rebase_core.team_catalogue import catalogue_lines
from rebase_core.team_prompt import ProposalAnswer
from rebase_core.team_schemas import CardRead, TeamEconomics, TeamProposalCreate


@pytest.fixture
def logs(clean_hub: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on the engine's logger, after `clean_hub`: Alembic's `fileConfig` disables
    every logger `alembic.ini` does not list, this one among them."""
    logging.getLogger("rebase_core.team_builder").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.team_builder")
    return caplog


# ---- the engine --------------------------------------------------------------------------


def test_engine_maps_the_answer_to_freelancers(clean_hub: Session) -> None:
    backend = a_talent(clean_hub, 1, tariffa=Decimal("450.00"), remoto="remoto")
    frontend = a_talent(
        clean_hub,
        2,
        tariffa=Decimal("300.00"),
        remoto="ibrido",
        card={**CARD, "ruolo": "Frontend developer", "luogo": "Milano"},
    )
    a_talent(clean_hub, 3)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    team_member(ids[frontend], "Frontend developer", "React da sei anni.", 3),
                    team_member(ids[backend], giorni=None),
                ]
            )
        ]
    )

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert read.riassunto == RIASSUNTO
    assert read.luogo.model_dump() == {"locale": False, "dove": None}
    assert read.origine == "admin"
    assert read.previous_id is None
    assert read.created_at == NOW
    first, second = read.team
    assert (first.posizione, first.freelancer_id, first.ruolo) == (
        1,
        frontend,
        "Frontend developer",
    )
    assert (first.motivazione, first.giorni_settimana) == ("React da sei anni.", 3)
    assert first.scheda == CardRead.model_validate(
        {**CARD, "ruolo": "Frontend developer", "luogo": "Milano"}
    )
    assert (first.modalita, first.fascia) == ("ibrido", Band(min=400, max=500))  # 420
    assert (second.posizione, second.freelancer_id, second.giorni_settimana) == (2, backend, None)
    assert (second.modalita, second.fascia) == ("remoto", Band(min=500, max=650))  # 630
    assert read.economia == TeamEconomics(
        giorno=Band(min=900, max=1150), mese=Band(min=900 * 22, max=1150 * 22), giorni_mese=22
    )
    assert read.model_dump(mode="json")["economia"]["giorno"] == {"min": 900, "max": 1150}

    # One call, shaped for a proposal: the rules, then the catalogue as a cached block, and
    # an answer of `ProposalAnswer`, closed at every level, every property required, with
    # no docstring on it for the schema to carry to the model. The ceiling is the
    # `team_proposal` task's.
    [request] = llm.requests
    assert request.output_model is ProposalAnswer
    assert request.max_tokens is None
    schema = ProposalAnswer.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["riassunto", "luogo", "team"]
    assert "description" not in json.dumps(schema)
    rules, catalogue = request.system, request.system_cached
    assert catalogue is not None
    assert catalogue_lines(clean_hub)[0] in catalogue
    assert "Italian" in rules
    assert DESCRIZIONE not in rules + catalogue
    assert DESCRIZIONE in user_text(request)


def test_engine_drops_unknown_and_repeated_positions(
    clean_hub: Session, logs: pytest.LogCaptureFixture
) -> None:
    first = a_talent(clean_hub, 1)
    second = a_talent(clean_hub, 2)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    team_member(ids[first]),
                    team_member("t99", "Designer"),
                    team_member("Ada Lovelace", "Designer"),
                    team_member("t0", "Designer"),
                    team_member(ids[first], "Tech lead"),
                    team_member(ids[second], "Frontend developer"),
                ]
            )
        ]
    )

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert [(m.posizione, m.freelancer_id, m.ruolo) for m in read.team] == [
        (1, first, "Backend developer"),
        (2, second, "Frontend developer"),
    ]
    [row] = proposal_rows(clean_hub)
    assert [member["posizione"] for member in row.team] == [1, 2]
    assert "'t99'" in logs.text and "'t0'" in logs.text and "not in the catalogue" in logs.text
    # An id that is not a position is the model's own text: it is not written down.
    assert "not a position" in logs.text and "Lovelace" not in logs.text
    assert f"{ids[first]!r}" in logs.text and "twice" in logs.text
    assert DESCRIZIONE not in logs.text


def test_engine_keeps_remote_members_on_a_local_need(
    clean_hub: Session, logs: pytest.LogCaptureFixture
) -> None:
    """Where a person works is the model's judgement, not a check (REB-598): on a need
    on site the summary says the people proposed work remotely, and every one of them
    stays, whatever their work mode, the one who never said how they work included."""
    remote = a_talent(clean_hub, 1, remoto="remoto")
    unknown = a_talent(clean_hub, 2, remoto=None)
    hybrid = a_talent(clean_hub, 3, remoto="ibrido")
    on_site = a_talent(clean_hub, 4, remoto="in_sede")
    ids = catalogue_positions(clean_hub)
    team = [team_member(ids[who]) for who in (remote, unknown, hybrid, on_site)]
    sentence = (
        "Un'azienda cerca un backend developer in sede a Torino: nessuno con queste "
        "competenze lavora in sede, quindi le persone proposte lavorano da remoto o non "
        "hanno indicato come lavorano."
    )
    llm = RecordingCall([proposal_response(team, riassunto=sentence, locale=True, dove="Torino")])

    local = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert local.luogo.model_dump() == {"locale": True, "dove": "Torino"}
    assert local.riassunto == sentence
    assert [(m.posizione, m.freelancer_id, m.modalita) for m in local.team] == [
        (1, remote, "remoto"),
        (2, unknown, None),
        (3, hybrid, "ibrido"),
        (4, on_site, "in_sede"),
    ]
    assert "dropped" not in logs.text


def test_a_team_dropped_whole_carries_the_hubs_sentence(clean_hub: Session) -> None:
    """The model's summary describes the team it chose: when the checks leave nobody of
    it, the page says nobody fits rather than describing people who are not there."""
    a_talent(clean_hub, 1, remoto="remoto")
    team = [team_member("t9"), team_member("t12")]
    llm = RecordingCall([proposal_response(team, locale=False, dove="Bari")])

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert (read.riassunto, read.team) == (NO_FIT_SENTENCE, [])
    assert read.economia.model_dump() == {"giorno": None, "mese": None, "giorni_mese": 22}
    [row] = proposal_rows(clean_hub)
    assert (row.riassunto, row.team) == (NO_FIT_SENTENCE, [])
    assert row.luogo == {"locale": False, "dove": "Bari"}


def test_a_member_gone_during_the_call_is_dropped(clean_hub: Session) -> None:
    staying = a_talent(clean_hub, 1)
    leaving = a_talent(clean_hub, 2)
    ids = catalogue_positions(clean_hub)

    def discard() -> None:
        row = clean_hub.get(Freelancer, leaving)
        assert row is not None
        row.stato = "scartato"
        clean_hub.commit()

    llm = Scripted(
        [proposal_response([team_member(ids[staying]), team_member(ids[leaving])])], during=discard
    )

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert [m.freelancer_id for m in read.team] == [staying]


def test_an_empty_team_when_nobody_fits(clean_hub: Session) -> None:
    a_talent(clean_hub, 1)
    sentence = "Nessun profilo lavora in sede a Bari: il team resta da comporre."
    llm = RecordingCall([proposal_response([], riassunto=sentence, locale=True, dove="Bari")])

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(
            descrizione="Cerchiamo un backend developer in sede a Bari, per sei mesi."
        ),
        origine="pubblico",
        user_id=None,
    )

    assert (read.riassunto, read.team) == (sentence, [])
    assert read.luogo.model_dump() == {"locale": True, "dove": "Bari"}  # the visitor's own word
    assert read.economia.model_dump() == {"giorno": None, "mese": None, "giorni_mese": 22}


def test_a_member_without_a_rate_leaves_the_team_without_a_band(clean_hub: Session) -> None:
    priced = a_talent(clean_hub, 1)
    unpriced = a_talent(clean_hub, 2, tariffa=None)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[priced]), team_member(ids[unpriced])])])

    read = make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [m.fascia for m in read.team] == [Band(min=500, max=650), None]
    assert read.economia.model_dump() == {"giorno": None, "mese": None, "giorni_mese": 22}


@pytest.mark.parametrize(
    "settings, llm",
    [
        (make_settings(team_builder_enabled=False), RecordingCall([proposal_response()])),
        (SETTINGS, None),
    ],
    ids=["switched off", "no key"],
)
def test_engine_refuses_when_off(
    clean_hub: Session, settings: Settings, llm: RecordingCall | None
) -> None:
    a_talent(clean_hub, 1)

    with pytest.raises(TeamBuilderOff) as refused:
        make_builder(clean_hub, llm, settings=settings).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
        )

    assert refused.value.message == "Il team builder è spento."
    assert llm is None or llm.requests == []
    assert proposal_rows(clean_hub) == []


@pytest.mark.parametrize(
    ("answer", "logged"),
    [
        (proposal_response(stop_reason="refusal", refusal_category="cyber"), "cyber"),
        (
            proposal_response(text='{"riassunto": "Un\'azienda', stop_reason="max_tokens"),
            "max_tokens",
        ),
        (proposal_response(text="Ecco il team che propongo"), "not the shape"),
        (proposal_response(text='{"riassunto": "Un team", "team": []}'), "not the shape"),
        (
            proposal_response(
                text=json.dumps(
                    {
                        "riassunto": RIASSUNTO,
                        "luogo": {"locale": False, "dove": None},
                        "team": [],
                        "costo": 1000,
                    }
                )
            ),
            "not the shape",
        ),
        (proposal_response([team_member("t1", giorni=9)]), "not the shape"),
        (
            LlmResponse(
                text=None,
                stop_reason="end_turn",
                refusal_category=None,
                model=MODEL,
                input_tokens=1,
                output_tokens=0,
                cache_read_tokens=0,
            ),
            "not the shape",
        ),
    ],
    ids=["refusal", "max_tokens", "not json", "missing key", "extra key", "over limit", "no text"],
)
def test_engine_turns_a_refusal_and_max_tokens_and_bad_json_into_unavailable(
    clean_hub: Session, logs: pytest.LogCaptureFixture, answer: LlmResponse, logged: str
) -> None:
    a_talent(clean_hub, 1)

    with pytest.raises(LlmUnavailable) as failed:
        make_builder(clean_hub, RecordingCall([answer])).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, nota="togli il designer"),
            origine="pubblico",
            user_id=None,
        )

    assert failed.value.message == UNAVAILABLE_SENTENCE
    # No proposal, but the ask itself is kept (0028), and nothing the model wrote with it.
    [attempt] = proposal_rows(clean_hub)
    assert (attempt.errore, attempt.riassunto, attempt.team, attempt.model) == (
        "llm_unavailable",
        "",
        [],
        "",
    )
    assert logged in logs.text
    for secret in (DESCRIZIONE, "togli il designer", "Ecco il team", "Un'azienda"):
        assert secret not in logs.text


def test_an_outage_reaches_the_caller_and_keeps_the_ask(clean_hub: Session) -> None:
    """0028: the refusal still reaches the caller, and the ask is kept as an attempt
    row, with the refusal's code, an empty summary, nobody in it and no call counted;
    no proposal was made, so no event is sent either. Its id never left the hub: `get`
    answers `NotFound` and «Rigenera» refuses it as it refuses a proposal that does not
    exist."""
    a_talent(clean_hub, 1)
    llm = Scripted([LlmUnavailable(UNAVAILABLE_SENTENCE)])
    capture = FakeCapture()
    builder = make_builder(clean_hub, llm, tracker=Tracker(capture))

    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, persone=2, nota="più backend"),
            origine="pubblico",
            user_id=None,
        )

    [row] = proposal_rows(clean_hub)
    assert (row.errore, row.descrizione, row.persone, row.nota) == (
        "llm_unavailable",
        DESCRIZIONE,
        2,
        "più backend",
    )
    assert (row.riassunto, row.team, row.model, row.input_tokens, row.origine) == (
        "",
        [],
        "",
        0,
        "pubblico",
    )
    assert row.created_at == NOW
    assert capture.calls == []
    with pytest.raises(NotFound):
        builder.get(row.id, public=True)
    with pytest.raises(ValidationFailed) as refused:
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, previous_id=row.id),
            origine="pubblico",
            user_id=None,
        )
    assert refused.value.details["field"] == "previous_id"


def test_an_attempt_that_cannot_be_written_still_answers_the_outage(
    clean_hub: Session, logs: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 0028: the record is a metric and the refusal is the product, so a
    database that refuses the attempt row is logged and the caller still gets
    `LlmUnavailable`, never the database's own error."""
    a_talent(clean_hub, 1)
    builder = make_builder(clean_hub, Scripted([LlmUnavailable(UNAVAILABLE_SENTENCE)]))

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("il database non risponde")

    monkeypatch.setattr(builder, "_write_attempt", broken)
    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
        )

    assert "team proposal attempt not kept" in logs.text
    assert proposal_rows(clean_hub) == []


def test_a_refusal_of_the_caps_is_kept_as_an_attempt(clean_hub: Session) -> None:
    """0028: the route keeps a «Troppe richieste» through `record_refusal`. A
    `previous_id` that names the caller's own proposal is linked; one that names
    nothing, or somebody else's, is left `NULL` rather than refused, since the caller
    already has the refusal to answer."""
    first = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    builder = make_builder(clean_hub, RecordingCall([proposal_response([team_member(ids[first])])]))
    own = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )
    busy = TeamBuilderBusy(BUSY_SENTENCE)

    builder.record_refusal(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=3, previous_id=own.id),
        origine="pubblico",
        user_id=None,
        error=busy,
    )
    builder.record_refusal(
        TeamProposalCreate(descrizione=DESCRIZIONE, previous_id=own.id),
        origine="cloud",
        user_id=a_user(clean_hub, "referente@acme.it"),
        error=busy,
    )

    _proposal, linked, unlinked = proposal_rows(clean_hub)
    assert (linked.errore, linked.persone, linked.previous_id) == ("team_builder_busy", 3, own.id)
    assert (unlinked.errore, unlinked.origine, unlinked.previous_id) == (
        "team_builder_busy",
        "cloud",
        None,
    )
    # An attempt is not a proposal to regenerate: a refusal that names one links nothing.
    builder.record_refusal(
        TeamProposalCreate(descrizione=DESCRIZIONE, previous_id=linked.id),
        origine="pubblico",
        user_id=None,
        error=busy,
    )
    # Exactly four rows, and the fourth is this refusal: had the write been skipped,
    # `[-1]` alone would read `unlinked`, whose `previous_id` is already `None`
    # (REB-673). `origine` tells it from the cloud refusal above.
    rows = proposal_rows(clean_hub)
    assert len(rows) == 4
    names_attempt = rows[3]
    assert (names_attempt.origine, names_attempt.errore, names_attempt.previous_id) == (
        "pubblico",
        "team_builder_busy",
        None,
    )
    # No attempt costs the day anything (`team_caps` counts `model`).
    assert {row.model for row in (linked, unlinked, names_attempt)} == {""}
    # A refusal the table does not take is the caller's bug, not a row.
    with pytest.raises(ValueError):
        builder.record_refusal(
            TeamProposalCreate(descrizione=DESCRIZIONE),
            origine="pubblico",
            user_id=None,
            error=TeamBuilderOff(OFF_SENTENCE),
        )


def test_the_database_is_released_during_the_call(clean_hub: Session) -> None:
    """Claude takes seconds to tens of seconds: no transaction, and so no pooled
    connection, is held open across the call."""
    first = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    held: list[bool] = []
    llm = Scripted(
        [proposal_response([team_member(ids[first])])],
        during=lambda: held.append(clean_hub.in_transaction()),
    )

    make_builder(clean_hub, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert held == [False]


def test_proposal_row_keeps_the_tokens_and_origin(clean_hub: Session) -> None:
    first = a_talent(clean_hub, 1)
    second = a_talent(clean_hub, 2, tariffa=Decimal("300.00"))
    ids = catalogue_positions(clean_hub)
    owner = a_user(clean_hub, "referente@acme.it")
    capture = FakeCapture()
    llm = RecordingCall(
        [
            proposal_response(
                [team_member(ids[first]), team_member(ids[second], "Tech lead", giorni=2)]
            )
        ]
    )

    read = make_builder(clean_hub, llm, tracker=Tracker(capture)).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE),
        origine="cloud",
        user_id=owner,
    )

    [row] = proposal_rows(clean_hub)
    assert row.id == read.id
    assert (row.model, row.input_tokens, row.output_tokens, row.cache_read_tokens) == (
        MODEL,
        5200,
        640,
        4800,
    )
    assert (row.origine, row.user_id, row.created_at) == ("cloud", owner, NOW)
    assert (row.descrizione, row.nota, row.previous_id) == (DESCRIZIONE, None, None)
    assert row.riassunto == RIASSUNTO
    assert row.luogo == {"locale": False, "dove": None}
    assert row.team == [
        {
            "posizione": 1,
            "freelancer_id": str(first),
            "ruolo": "Backend developer",
            "motivazione": team_member("t1")["motivazione"],
            "giorni_settimana": 5,
        },
        {
            "posizione": 2,
            "freelancer_id": str(second),
            "ruolo": "Tech lead",
            "motivazione": team_member("t1")["motivazione"],
            "giorni_settimana": 2,
        },
    ]
    assert row.economia == {
        "giorno": {"min": 900, "max": 1150},
        "mese": {"min": 19800, "max": 25300},
        "giorni_mese": 22,
    }
    [(event, distinct_id, properties)] = capture.calls
    assert event == TEAM_PROPOSAL_GENERATED == "team_proposta_generata"
    assert distinct_id
    assert properties == {
        "origine": "cloud",
        "persone": 2,
        "persone_richieste": None,
        "input_tokens": 5200,
        "output_tokens": 640,
        "$process_person_profile": False,
    }


def test_a_tracker_that_fails_never_fails_the_proposal(clean_hub: Session) -> None:
    first = a_talent(clean_hub, 1)
    ids = catalogue_positions(clean_hub)
    llm = RecordingCall([proposal_response([team_member(ids[first])])])

    read = make_builder(clean_hub, llm, tracker=Tracker(FakeCapture(raises=True))).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert len(read.team) == 1
    assert Tracker(FakeCapture(raises=True)).team_event("team_proposta_generata", {}) is False


@pytest.mark.parametrize(
    "fields",
    [
        {"descrizione": "Troppo corta."},
        {"descrizione": "x" * 4001},
        {"descrizione": " " * 60},
        {"descrizione": DESCRIZIONE, "nota": "n" * 501},
        {"descrizione": DESCRIZIONE, "costo": 10},
        {"descrizione": DESCRIZIONE, "persone": 0},
        {"descrizione": DESCRIZIONE, "persone": 11},
        {"descrizione": DESCRIZIONE, "persone": True},
        {"descrizione": DESCRIZIONE, "persone": "3"},
    ],
    ids=[
        "short",
        "long",
        "blank",
        "long note",
        "unknown field",
        "nobody",
        "too many",
        "a bool",
        "a string",
    ],
)
def test_a_request_outside_its_bounds_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TeamProposalCreate.model_validate(fields)


def test_the_number_of_people_reaches_the_prompt_only_when_asked(clean_hub: Session) -> None:
    """REB-591: the page sends how many people the visitor wants; the MCP tool sends
    nothing and the team is sized from the description, as before."""
    a_talent(clean_hub, 1)
    llm = RecordingCall([proposal_response([team_member("t1")]) for _ in range(3)])
    capture = FakeCapture()
    builder = make_builder(clean_hub, llm, tracker=Tracker(capture))

    builder.propose(TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None)
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=1), origine="pubblico", user_id=None
    )
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=3), origine="pubblico", user_id=None
    )

    assert "exactly" not in user_text(llm.requests[0])
    assert "The visitor wants a team of exactly 1 person." in user_text(llm.requests[1])
    assert "The visitor wants a team of exactly 3 people." in user_text(llm.requests[2])
    # The number is a sentence of the user turn: the cached system prefix never moves.
    assert llm.requests[2].system == llm.requests[0].system
    assert "The visitor wants a team of exactly N" in llm.requests[0].system
    # Place is a preference, never an exclusion (REB-598): the rule says so in as many
    # words, and the old sentence is gone.
    rules = llm.requests[0].system
    assert "excludes nobody" in rules and "propose nobody" not in rules
    assert "a null modalita is unknown, never remote" in rules
    # The event tells the asked number from the proposed one, and the row keeps the
    # asked one (0028), `NULL` when nobody asked.
    assert [(p["persone"], p["persone_richieste"]) for _, _, p in capture.calls] == [
        (1, None),
        (1, 1),
        (1, 3),
    ]
    assert [row.persone for row in proposal_rows(clean_hub)] == [None, 1, 3]
