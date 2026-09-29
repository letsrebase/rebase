"""The team builder's engine (REB-511, spec § 3.3, § 3.4): a project's description and
the catalogue of anonymous cards go to Claude, and the answer comes back as a team of
freelancers with their price bands, written as a proposal row.

Claude is a `RecordingCall` answering canned proposals, or `_Scripted` where a test
needs an outage or something to happen during the call; the freelancers and their cards
are written straight into the tables, since what is under test is what the engine reads
from them, not how a card is written (`test_cards.py`).
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL
from sqlalchemy import JSON, func, null, select, text
from sqlalchemy.orm import Session

from rebase_core.analytics import TEAM_PROPOSAL_GENERATED, Tracker
from rebase_core.bands import Band
from rebase_core.config import Settings
from rebase_core.db import uuid7
from rebase_core.errors import (
    LlmUnavailable,
    NotFound,
    TeamBuilderBusy,
    TeamBuilderOff,
    ValidationFailed,
)
from rebase_core.llm import UNAVAILABLE_SENTENCE, LlmRequest, LlmResponse, RecordingCall
from rebase_core.models import Freelancer, FreelancerCard, TeamProposal, TeamRequest, User
from rebase_core.team_builder import (
    NO_FIT_SENTENCE,
    OFF_SENTENCE,
    PLACE_WITHHELD_REASON,
    PLACE_WITHHELD_RIASSUNTO,
    PLACE_WITHHELD_SUMMARY,
    PROPOSAL_MAX_TOKENS,
    PROPOSAL_SCHEMA,
    TeamBuilder,
    catalogue_lines,
    cloud_visible,
)
from rebase_core.team_caps import BUSY_SENTENCE
from rebase_core.team_schemas import Card, TeamProposalCreate

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)
DESCRIZIONE = (
    "Acme Logistica rifà il gestionale degli ordini: un backend in Python con FastAPI, "
    "un frontend in React, sei mesi di lavoro, da remoto."
)
RIASSUNTO = (
    "Un'azienda di logistica rifà il gestionale degli ordini: backend Python e frontend "
    "React, sei mesi da remoto."
)
_NO_CARD = object()


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


SETTINGS = _settings()


class FakeCapture:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, event: str, *, distinct_id: str, properties: dict[str, Any]) -> None:
        if self.raises:
            raise RuntimeError("la rete non c'e'")
        self.calls.append((event, distinct_id, properties))


class _Scripted:
    """`RecordingCall` that can also fail: each answer is a response or an exception to
    raise, and `during` runs inside the call, before it answers."""

    def __init__(
        self,
        answers: list[LlmResponse | BaseException],
        during: Callable[[], None] | None = None,
    ) -> None:
        self._answers = list(answers)
        self._during = during
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if self._during is not None:
            self._during()
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _member(
    position: str,
    ruolo: str = "Backend developer",
    motivazione: str = "Nove anni di API in Python e FastAPI, quello che serve al gestionale.",
    giorni: int | None = 5,
) -> dict[str, Any]:
    return {"id": position, "ruolo": ruolo, "motivazione": motivazione, "giorni_settimana": giorni}


def proposal_response(
    team: list[dict[str, Any]] | None = None,
    *,
    riassunto: str = RIASSUNTO,
    locale: bool = False,
    dove: str | None = None,
    text: str | None = None,
    stop_reason: str = "end_turn",
    refusal_category: str | None = None,
) -> LlmResponse:
    body = {
        "riassunto": riassunto,
        "luogo": {"locale": locale, "dove": dove},
        "team": team if team is not None else [],
    }
    return LlmResponse(
        text=None if stop_reason == "refusal" else (text if text is not None else json.dumps(body)),
        stop_reason=stop_reason,
        refusal_category=refusal_category,
        model=MODEL,
        input_tokens=5200,
        output_tokens=640,
        cache_read_tokens=4800,
    )


def _talent(
    session: Session,
    n: int,
    *,
    card: Any = _NO_CARD,
    remoto: str | None = "remoto",
    tariffa: Decimal | None = Decimal("450.00"),
    stato: str = "nuovo",
    deleted: bool = False,
    freelancer_id: UUID | None = None,
) -> UUID:
    """A freelancer with a name, a link and, unless `card` says otherwise, the card of
    `fakes_cards`: everything the catalogue must leave out sits in the row beside it."""
    user = User(email=f"talento{n}@studio.it", nome="Ada", cognome=f"Lovelace{n}")
    session.add(user)
    session.flush()
    row = Freelancer(
        user_id=user.id,
        remoto=remoto,
        tariffa_giornaliera=tariffa,
        stato=stato,
        deleted_at=NOW if deleted else None,
        links=[f"https://github.com/ada{n}"],
        posizione="Backend developer",
    )
    if freelancer_id is not None:
        row.id = freelancer_id
    session.add(row)
    session.flush()
    if card is not None:
        session.add(
            FreelancerCard(
                freelancer_id=row.id,
                cv_sha256="0" * 64,
                card=CARD if card is _NO_CARD else card,
                model=MODEL,
                input_tokens=1200,
                output_tokens=180,
                generated_at=NOW,
            )
        )
    session.commit()
    return row.id


def _user(session: Session, email: str) -> UUID:
    user = User(email=email, nome="Referente", cognome="Azienda")
    session.add(user)
    session.commit()
    return user.id


def _positions(session: Session) -> dict[UUID, str]:
    _, positions = catalogue_lines(session)
    return {freelancer_id: f"t{index}" for index, freelancer_id in enumerate(positions, start=1)}


def _user_text(request: LlmRequest) -> str:
    return "\n".join(block["text"] for message in request.messages for block in message["content"])


def _builder(
    session: Session,
    llm: Any,
    *,
    settings: Settings = SETTINGS,
    tracker: Tracker | None = None,
    now: datetime = NOW,
) -> TeamBuilder:
    return TeamBuilder(session, llm, settings, tracker=tracker, now=lambda: now)


def _rows(session: Session) -> list[TeamProposal]:
    session.expire_all()
    return list(session.scalars(select(TeamProposal).order_by(TeamProposal.created_at)))


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM team_requests"))
    hub_session.execute(text("DELETE FROM team_proposals"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


@pytest.fixture
def logs(clean: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on the engine's logger, after `clean`: Alembic's `fileConfig` disables
    every logger `alembic.ini` does not list, this one among them."""
    logging.getLogger("rebase_core.team_builder").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.team_builder")
    return caplog


# ---- the catalogue -----------------------------------------------------------------------


def test_catalogue_is_stable_and_anonymous(clean: Session) -> None:
    first = _talent(clean, 1, tariffa=Decimal("450.00"), remoto="ibrido")
    second = _talent(clean, 2, tariffa=None, remoto=None)
    third = _talent(
        clean, 3, tariffa=Decimal("200.00"), card={**CARD, "ruolo": "Designer", "luogo": None}
    )

    catalogue, positions = catalogue_lines(clean)

    assert positions == sorted([first, second, third])
    lines = [json.loads(line) for line in catalogue.splitlines()]
    assert [line["id"] for line in lines] == ["t1", "t2", "t3"]
    assert list(lines[0]) == [
        "id",
        "ruolo",
        "seniority",
        "anni",
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
    assert catalogue_lines(clean) == (catalogue, positions)


def test_catalogue_positions_are_unique(clean: Session) -> None:
    """Two freelancers who signed up in the same millisecond share a UUIDv7's leading
    characters, so a slice of the id could collide; a line's position never does."""
    earlier = uuid7()
    later = UUID(int=earlier.int + 1)
    assert str(earlier)[:13] == str(later)[:13]
    _talent(clean, 2, freelancer_id=later)
    _talent(clean, 1, freelancer_id=earlier)

    catalogue, positions = catalogue_lines(clean)

    assert positions == [earlier, later]
    assert [json.loads(line)["id"] for line in catalogue.splitlines()] == ["t1", "t2"]


def test_catalogue_is_every_live_freelancer_with_a_card(clean: Session) -> None:
    visible = _talent(clean, 1)
    _talent(clean, 2, deleted=True)
    _talent(clean, 3, stato="scartato")
    _talent(clean, 4, card=None)  # no card row at all
    sql_null = _talent(clean, 5, card=null())  # a row with no card yet, only an error
    json_null = _talent(clean, 6, card=JSON.NULL)  # a card retired to JSON `null`
    contacted = _talent(clean, 7, stato="contattato")
    kinds: dict[UUID, str | None] = {
        freelancer_id: kind
        for freelancer_id, kind in clean.execute(
            select(FreelancerCard.freelancer_id, func.jsonb_typeof(FreelancerCard.card))
        )
    }
    assert (kinds[sql_null], kinds[json_null]) == (None, "null")

    assert sorted(clean.scalars(cloud_visible(select(Freelancer.id)))) == sorted(
        [visible, contacted]
    )
    _, positions = catalogue_lines(clean)
    assert positions == sorted([visible, contacted])


# ---- the engine --------------------------------------------------------------------------


def test_engine_maps_the_answer_to_freelancers(clean: Session) -> None:
    backend = _talent(clean, 1, tariffa=Decimal("450.00"), remoto="remoto")
    frontend = _talent(
        clean,
        2,
        tariffa=Decimal("300.00"),
        remoto="ibrido",
        card={**CARD, "ruolo": "Frontend developer", "luogo": "Milano"},
    )
    _talent(clean, 3)
    ids = _positions(clean)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    _member(ids[frontend], "Frontend developer", "React da sei anni.", 3),
                    _member(ids[backend], giorni=None),
                ]
            )
        ]
    )

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert read.riassunto == RIASSUNTO
    assert read.luogo == {"locale": False, "dove": None}
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
    assert first.scheda == Card.model_validate(
        {**CARD, "ruolo": "Frontend developer", "luogo": "Milano"}
    )
    assert (first.modalita, first.fascia) == ("ibrido", Band(min=400, max=500))  # 420
    assert (second.posizione, second.freelancer_id, second.giorni_settimana) == (2, backend, None)
    assert (second.modalita, second.fascia) == ("remoto", Band(min=500, max=650))  # 630
    assert read.economia == {
        "giorno": Band(min=900, max=1150),
        "mese": Band(min=900 * 22, max=1150 * 22),
        "giorni_mese": 22,
    }
    assert read.model_dump(mode="json")["economia"]["giorno"] == {"min": 900, "max": 1150}

    # One call, shaped for a proposal: the rules, then the catalogue as a cached block.
    [request] = llm.requests
    assert request.max_tokens == PROPOSAL_MAX_TOKENS == 8000
    assert request.json_schema == PROPOSAL_SCHEMA
    assert PROPOSAL_SCHEMA["additionalProperties"] is False
    assert PROPOSAL_SCHEMA["required"] == ["riassunto", "luogo", "team"]
    assert "description" not in json.dumps(PROPOSAL_SCHEMA)  # no docstring reaches Claude
    rules, catalogue = request.system
    assert "cache_control" not in rules
    assert catalogue["cache_control"] == {"type": "ephemeral"}
    assert catalogue_lines(clean)[0] in catalogue["text"]
    assert "Italian" in rules["text"]
    assert DESCRIZIONE not in rules["text"] + catalogue["text"]
    assert DESCRIZIONE in _user_text(request)


def test_the_catalogue_block_is_the_same_for_two_calls(clean: Session) -> None:
    first = _talent(clean, 1)
    _talent(clean, 2)
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[first])])] * 2)
    builder = _builder(clean, llm)

    builder.propose(TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None)
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE + " Anche un designer."),
        origine="pubblico",
        user_id=None,
    )

    one, two = llm.requests
    assert one.system == two.system
    assert one.messages != two.messages


def test_engine_drops_unknown_and_repeated_positions(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    first = _talent(clean, 1)
    second = _talent(clean, 2)
    ids = _positions(clean)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    _member(ids[first]),
                    _member("t99", "Designer"),
                    _member("Ada Lovelace", "Designer"),
                    _member("t0", "Designer"),
                    _member(ids[first], "Tech lead"),
                    _member(ids[second], "Frontend developer"),
                ]
            )
        ]
    )

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert [(m.posizione, m.freelancer_id, m.ruolo) for m in read.team] == [
        (1, first, "Backend developer"),
        (2, second, "Frontend developer"),
    ]
    [row] = _rows(clean)
    assert [member["posizione"] for member in row.team] == [1, 2]
    assert "'t99'" in logs.text and "'t0'" in logs.text and "not in the catalogue" in logs.text
    # An id that is not a position is the model's own text: it is not written down.
    assert "not a position" in logs.text and "Lovelace" not in logs.text
    assert f"{ids[first]!r}" in logs.text and "twice" in logs.text
    assert DESCRIZIONE not in logs.text


def test_engine_keeps_remote_members_on_a_local_need(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    """Where a person works is the model's judgement, not a check (REB-598): on a need
    on site the summary says the people proposed work remotely, and every one of them
    stays, whatever their work mode, the one who never said how they work included."""
    remote = _talent(clean, 1, remoto="remoto")
    unknown = _talent(clean, 2, remoto=None)
    hybrid = _talent(clean, 3, remoto="ibrido")
    on_site = _talent(clean, 4, remoto="in_sede")
    ids = _positions(clean)
    team = [_member(ids[who]) for who in (remote, unknown, hybrid, on_site)]
    sentence = (
        "Un'azienda cerca un backend developer in sede a Torino: nessuno con queste "
        "competenze lavora in sede, quindi le persone proposte lavorano da remoto o non "
        "hanno indicato come lavorano."
    )
    llm = RecordingCall([proposal_response(team, riassunto=sentence, locale=True, dove="Torino")])

    local = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert local.luogo == {"locale": True, "dove": "Torino"}
    assert local.riassunto == sentence
    assert [(m.posizione, m.freelancer_id, m.modalita) for m in local.team] == [
        (1, remote, "remoto"),
        (2, unknown, None),
        (3, hybrid, "ibrido"),
        (4, on_site, "in_sede"),
    ]
    assert "dropped" not in logs.text


def test_a_team_dropped_whole_carries_the_hubs_sentence(clean: Session) -> None:
    """The model's summary describes the team it chose: when the checks leave nobody of
    it, the page says nobody fits rather than describing people who are not there."""
    _talent(clean, 1, remoto="remoto")
    team = [_member("t9"), _member("t12")]
    llm = RecordingCall([proposal_response(team, locale=False, dove="Bari")])

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert (read.riassunto, read.team) == (NO_FIT_SENTENCE, [])
    assert read.economia == {"giorno": None, "mese": None, "giorni_mese": 22}
    [row] = _rows(clean)
    assert (row.riassunto, row.team) == (NO_FIT_SENTENCE, [])
    assert row.luogo == {"locale": False, "dove": "Bari"}


def test_an_empty_catalogue_asks_nobody(clean: Session) -> None:
    _talent(clean, 1, deleted=True)  # a card, but not in the catalogue
    llm = RecordingCall([])  # any call would fail on an empty script
    capture = FakeCapture()

    read = _builder(clean, llm, tracker=Tracker(capture)).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert llm.requests == []
    assert (read.riassunto, read.team) == (NO_FIT_SENTENCE, [])
    assert read.luogo == {"locale": False, "dove": None}
    assert read.economia == {"giorno": None, "mese": None, "giorni_mese": 22}
    # The row is written all the same, so «Rigenera» and a later read find it; it cost
    # nothing, and says so.
    [row] = _rows(clean)
    assert row.id == read.id
    assert (row.model, row.input_tokens, row.output_tokens, row.cache_read_tokens) == ("", 0, 0, 0)
    [(_, _, properties)] = capture.calls
    assert (properties["persone"], properties["input_tokens"], properties["output_tokens"]) == (
        0,
        0,
        0,
    )


def test_a_member_gone_during_the_call_is_dropped(clean: Session) -> None:
    staying = _talent(clean, 1)
    leaving = _talent(clean, 2)
    ids = _positions(clean)

    def discard() -> None:
        row = clean.get(Freelancer, leaving)
        assert row is not None
        row.stato = "scartato"
        clean.commit()

    llm = _Scripted(
        [proposal_response([_member(ids[staying]), _member(ids[leaving])])], during=discard
    )

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert [m.freelancer_id for m in read.team] == [staying]


def test_an_empty_team_when_nobody_fits(clean: Session) -> None:
    _talent(clean, 1)
    sentence = "Nessun profilo lavora in sede a Bari: il team resta da comporre."
    llm = RecordingCall([proposal_response([], riassunto=sentence, locale=True, dove="Bari")])

    read = _builder(clean, llm).propose(
        TeamProposalCreate(
            descrizione="Cerchiamo un backend developer in sede a Bari, per sei mesi."
        ),
        origine="pubblico",
        user_id=None,
    )

    assert (read.riassunto, read.team) == (sentence, [])
    assert read.luogo == {"locale": True, "dove": "Bari"}  # the visitor's own word
    assert read.economia == {"giorno": None, "mese": None, "giorni_mese": 22}


def test_a_member_without_a_rate_leaves_the_team_without_a_band(clean: Session) -> None:
    priced = _talent(clean, 1)
    unpriced = _talent(clean, 2, tariffa=None)
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[priced]), _member(ids[unpriced])])])

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [m.fascia for m in read.team] == [Band(min=500, max=650), None]
    assert read.economia == {"giorno": None, "mese": None, "giorni_mese": 22}


@pytest.mark.parametrize(
    "settings, llm",
    [
        (_settings(team_builder_enabled=False), RecordingCall([proposal_response()])),
        (SETTINGS, None),
    ],
    ids=["switched off", "no key"],
)
def test_engine_refuses_when_off(
    clean: Session, settings: Settings, llm: RecordingCall | None
) -> None:
    _talent(clean, 1)

    with pytest.raises(TeamBuilderOff) as refused:
        _builder(clean, llm, settings=settings).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
        )

    assert refused.value.message == "Il team builder è spento."
    assert llm is None or llm.requests == []
    assert _rows(clean) == []


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
        (proposal_response([_member("t1", giorni=9)]), "not the shape"),
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
    clean: Session, logs: pytest.LogCaptureFixture, answer: LlmResponse, logged: str
) -> None:
    _talent(clean, 1)

    with pytest.raises(LlmUnavailable) as failed:
        _builder(clean, RecordingCall([answer])).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, nota="togli il designer"),
            origine="pubblico",
            user_id=None,
        )

    assert failed.value.message == UNAVAILABLE_SENTENCE
    # No proposal, but the ask itself is kept (0028), and nothing the model wrote with it.
    [attempt] = _rows(clean)
    assert (attempt.errore, attempt.riassunto, attempt.team, attempt.model) == (
        "llm_unavailable",
        "",
        [],
        "",
    )
    assert logged in logs.text
    for secret in (DESCRIZIONE, "togli il designer", "Ecco il team", "Un'azienda"):
        assert secret not in logs.text


def test_an_outage_reaches_the_caller_and_keeps_the_ask(clean: Session) -> None:
    """0028: the refusal still reaches the caller, and the ask is kept as an attempt
    row, with the refusal's code, an empty summary, nobody in it and no call counted;
    no proposal was made, so no event is sent either. Its id never left the hub: `get`
    answers `NotFound` and «Rigenera» refuses it as it refuses a proposal that does not
    exist."""
    _talent(clean, 1)
    llm = _Scripted([LlmUnavailable(UNAVAILABLE_SENTENCE)])
    capture = FakeCapture()
    builder = _builder(clean, llm, tracker=Tracker(capture))

    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, persone=2, nota="più backend"),
            origine="pubblico",
            user_id=None,
        )

    [row] = _rows(clean)
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
    clean: Session, logs: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of 0028: the record is a metric and the refusal is the product, so a
    database that refuses the attempt row is logged and the caller still gets
    `LlmUnavailable`, never the database's own error."""
    _talent(clean, 1)
    builder = _builder(clean, _Scripted([LlmUnavailable(UNAVAILABLE_SENTENCE)]))

    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("il database non risponde")

    monkeypatch.setattr(builder, "_write_attempt", broken)
    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
        )

    assert "team proposal attempt not kept" in logs.text
    assert _rows(clean) == []


def test_a_refusal_of_the_caps_is_kept_as_an_attempt(clean: Session) -> None:
    """0028: the route keeps a «Troppe richieste» through `record_refusal`. A
    `previous_id` that names the caller's own proposal is linked; one that names
    nothing, or somebody else's, is left `NULL` rather than refused, since the caller
    already has the refusal to answer."""
    first = _talent(clean, 1)
    ids = _positions(clean)
    builder = _builder(clean, RecordingCall([proposal_response([_member(ids[first])])]))
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
        user_id=_user(clean, "referente@acme.it"),
        error=busy,
    )

    _proposal, linked, unlinked = _rows(clean)
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
    assert _rows(clean)[-1].previous_id is None
    # Neither attempt costs the day anything (`team_caps` counts `model`).
    assert {row.model for row in (linked, unlinked)} == {""}
    # A refusal the table does not take is the caller's bug, not a row.
    with pytest.raises(ValueError):
        builder.record_refusal(
            TeamProposalCreate(descrizione=DESCRIZIONE),
            origine="pubblico",
            user_id=None,
            error=TeamBuilderOff(OFF_SENTENCE),
        )


def test_list_recent_pages_filters_and_names_the_request(clean: Session) -> None:
    """«Proposte» (0028): newest first by cursor, every origin and outcome together
    unless filtered, each row with how many it held, who they are (REB-607) and the
    request filed on it."""
    first = _talent(clean, 1)
    ids = _positions(clean)
    owner = _user(clean, "referente@acme.it")
    builder = _builder(
        clean,
        _Scripted(
            [
                proposal_response([_member(ids[first])]),
                proposal_response([]),
                LlmUnavailable(UNAVAILABLE_SENTENCE),
            ]
        ),
    )
    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=1), origine="pubblico", user_id=None
    )
    cloud = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=2, nota="senza designer"),
        origine="cloud",
        user_id=owner,
        now=NOW + timedelta(minutes=1),
    )
    with pytest.raises(LlmUnavailable):
        builder.propose(
            TeamProposalCreate(descrizione=DESCRIZIONE),
            origine="admin",
            user_id=owner,
            now=NOW + timedelta(minutes=2),
        )
    request_id = uuid7()
    clean.add(
        TeamRequest(
            id=request_id,
            proposal_id=public.id,
            origine="pubblico",
            azienda="Acme S.r.l.",
            email="wile@acme.it",
            telefono="+39 345 1234567",
        )
    )
    clean.commit()

    page = builder.list_recent(limit=2)
    assert [item.origine for item in page.items] == ["admin", "cloud"]
    assert page.next_cursor is not None
    attempt, regenerated = page.items
    assert (attempt.errore, attempt.membri, attempt.request_id, attempt.persone) == (
        "llm_unavailable",
        0,
        None,
        None,
    )
    assert (regenerated.errore, regenerated.membri, regenerated.nota, regenerated.user_id) == (
        None,
        0,
        "senza designer",
        owner,
    )
    assert (attempt.riassunto, regenerated.riassunto) == (None, RIASSUNTO)
    rest = builder.list_recent(limit=2, cursor=page.next_cursor)
    assert rest.next_cursor is None
    [filed] = rest.items
    assert (filed.id, filed.descrizione, filed.persone, filed.membri, filed.request_id) == (
        public.id,
        DESCRIZIONE,
        1,
        1,
        request_id,
    )
    assert filed.created_at == NOW
    # REB-607: the row names who it held, so the admin can open the profile from the
    # list; an attempt and a team nobody fit name nobody.
    [held] = filed.team
    assert (held.posizione, held.freelancer_id, held.ruolo, held.nome, held.cognome) == (
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
    assert [item.id for item in builder.list_recent(esito="errore").items] == [attempt.id]
    assert [item.id for item in builder.list_recent(esito="ok").items] == [cloud.id, public.id]
    assert [item.id for item in builder.list_recent(origine="cloud").items] == [cloud.id]
    for field, kwargs in (
        ("origine", {"origine": "sito"}),
        ("esito", {"esito": "forse"}),
        ("cursor", {"cursor": "non-un-cursore"}),
    ):
        with pytest.raises(ValidationFailed) as refused:
            builder.list_recent(**kwargs)
        assert refused.value.details["field"] == field


def test_list_recent_keeps_a_deleted_talent_by_id_and_names_the_rest(clean: Session) -> None:
    """REB-607: the list is about what was proposed, so a member removed with «Elimina»
    since (`deleted_at` set, the product's only delete: their admin page answers 404)
    stays in the row, unnamed, next to one still on file; a freelancer id the row
    holds and no row answers, which nothing in the product produces, reads the same."""
    kept = _talent(clean, 1)
    soft = _talent(clean, 2, deleted=True)
    gone = uuid7()
    clean.add(
        TeamProposal(
            descrizione=DESCRIZIONE,
            riassunto=RIASSUNTO,
            luogo={"locale": False, "dove": None},
            team=[
                {
                    "posizione": index,
                    "freelancer_id": str(freelancer_id),
                    "ruolo": ruolo,
                    "motivazione": "Serve.",
                    "giorni_settimana": 5,
                }
                for index, (freelancer_id, ruolo) in enumerate(
                    ((gone, "Designer"), (kept, "Backend developer"), (soft, "Data engineer")),
                    start=1,
                )
            ],
            economia={"giorno": None, "mese": None, "giorni_mese": 22},
            model=MODEL,
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            origine="pubblico",
        )
    )
    clean.commit()

    [row] = _builder(clean, _Scripted([])).list_recent().items
    assert row.membri == 3
    assert [(m.posizione, m.freelancer_id, m.ruolo, m.nome, m.cognome) for m in row.team] == [
        (1, gone, "Designer", None, None),
        (2, kept, "Backend developer", "Ada", "Lovelace1"),
        (3, soft, "Data engineer", None, None),
    ]


def test_the_database_is_released_during_the_call(clean: Session) -> None:
    """Claude takes seconds to tens of seconds: no transaction, and so no pooled
    connection, is held open across the call."""
    first = _talent(clean, 1)
    ids = _positions(clean)
    held: list[bool] = []
    llm = _Scripted(
        [proposal_response([_member(ids[first])])],
        during=lambda: held.append(clean.in_transaction()),
    )

    _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert held == [False]


# ---- «Rigenera» --------------------------------------------------------------------------


def test_regenerate_carries_the_previous_team_and_note(clean: Session) -> None:
    backend = _talent(clean, 1)
    designer = _talent(clean, 2, card={**CARD, "ruolo": "Designer"})
    gone = _talent(clean, 3)
    ids = _positions(clean)
    llm = RecordingCall(
        [
            proposal_response(
                [
                    _member(ids[backend]),
                    _member(ids[designer], "Product designer"),
                    _member(ids[gone], "Tech lead"),
                ]
            ),
            proposal_response([_member("t2")]),
        ]
    )
    builder = _builder(clean, llm)
    first = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )
    # Between the two asks the catalogue changes: someone is placed before everyone
    # (the positions shift by one), and a member of the first team leaves it.
    newcomer = _talent(clean, 4, freelancer_id=UUID("00000000-0000-7000-8000-000000000001"))
    row = clean.get(Freelancer, gone)
    assert row is not None
    row.deleted_at = NOW
    clean.commit()

    second = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, nota="togli il designer", previous_id=first.id),
        origine="pubblico",
        user_id=None,
    )

    assert second.previous_id == first.id
    assert _positions(clean)[newcomer] == "t1"
    turn = _user_text(llm.requests[1])
    assert DESCRIZIONE in turn
    assert RIASSUNTO in turn
    assert "t2: Backend developer" in turn  # t1 in the first call's catalogue
    assert "t3: Product designer" in turn  # t2 then
    assert "Tech lead" not in turn  # no longer in the catalogue
    assert "togli il designer" in turn
    assert llm.requests[1].system[0] == llm.requests[0].system[0]  # the rules never move
    rows = _rows(clean)
    assert [(r.previous_id, r.nota) for r in rows] == [
        (None, None),
        (first.id, "togli il designer"),
    ]
    # The first ask carries no previous team and no note.
    assert "togli" not in _user_text(llm.requests[0])


@pytest.mark.parametrize(
    "case", ["unknown", "other origin", "another cloud user", "older than a day"]
)
def test_previous_id_must_be_the_callers_and_younger_than_a_day(clean: Session, case: str) -> None:
    first_talent = _talent(clean, 1)
    ids = _positions(clean)
    owner = _user(clean, "referente@acme.it")
    stranger = _user(clean, "altro@beta.it")
    llm = RecordingCall([proposal_response([_member(ids[first_talent])])] * 2)
    builder = _builder(clean, llm)
    origin = "pubblico" if case == "other origin" else "cloud"
    previous = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine=origin, user_id=owner
    )
    previous_id = uuid7() if case == "unknown" else previous.id
    caller = stranger if case == "another cloud user" else owner
    later = NOW + (timedelta(days=1) if case == "older than a day" else timedelta(hours=23))

    with pytest.raises(ValidationFailed) as refused:
        _builder(clean, llm, now=later).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE, previous_id=previous_id),
            origine="cloud",
            user_id=caller,
        )

    assert refused.value.details["field"] == "previous_id"
    assert len(llm.requests) == 1  # the refusal pays for nothing
    assert len(_rows(clean)) == 1


def test_previous_id_of_the_same_cloud_user_within_the_day(clean: Session) -> None:
    first_talent = _talent(clean, 1)
    ids = _positions(clean)
    owner = _user(clean, "referente@acme.it")
    llm = RecordingCall([proposal_response([_member(ids[first_talent])])] * 2)
    previous = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="cloud", user_id=owner
    )

    read = _builder(clean, llm, now=NOW + timedelta(hours=23, minutes=59)).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, previous_id=previous.id),
        origine="cloud",
        user_id=owner,
    )

    assert read.previous_id == previous.id


# ---- the reads, the row, the event -------------------------------------------------------


def test_public_read_hides_ids_and_luogo(clean: Session) -> None:
    first = _talent(clean, 1)
    second = _talent(clean, 2, card={**CARD, "luogo": "Bologna"})
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[first]), _member(ids[second])])])
    builder = _builder(clean, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [(m.freelancer_id, m.scheda.luogo) for m in public.team] == [(None, None)] * 2
    body = public.model_dump_json()
    for secret in (str(first), str(second), "Torino", "Bologna"):
        assert secret not in body
    # The admin's read of the same proposal keeps both; the public read by id hides both.
    admin = builder.get(public.id, public=False)
    assert [(m.freelancer_id, m.scheda.luogo) for m in admin.team] == [
        (first, "Torino"),
        (second, "Bologna"),
    ]
    assert builder.get(public.id, public=True) == public
    with pytest.raises(NotFound):
        builder.get(uuid7(), public=True)


def test_the_cloud_read_names_each_member_and_the_public_read_never(clean: Session) -> None:
    """The cloud shows who each person is (spec § 4.2), so its read carries the name
    and the surname beside the id; the public read carries neither, of a public
    proposal or of a cloud one read by id."""
    first = _talent(clean, 1)
    second = _talent(clean, 2)
    ids = _positions(clean)
    user_id = _user(clean, "referente@acme.it")
    team = [_member(ids[first]), _member(ids[second])]
    llm = RecordingCall([proposal_response(team), proposal_response(team)])
    builder = _builder(clean, llm)

    cloud = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="cloud", user_id=user_id
    )
    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert [(m.freelancer_id, m.nome, m.cognome) for m in cloud.team] == [
        (first, "Ada", "Lovelace1"),
        (second, "Ada", "Lovelace2"),
    ]
    assert builder.get(cloud.id, public=False) == cloud
    for anonymous in (public, builder.get(cloud.id, public=True)):
        assert [(m.nome, m.cognome) for m in anonymous.team] == [(None, None)] * 2
        assert "Lovelace" not in anonymous.model_dump_json()


def test_a_public_reason_that_names_the_cards_place_is_withheld(clean: Session) -> None:
    """The prompt forbids a place in a motivazione; one that names the place on the
    member's card anyway would give back what the public read withholds, so that read
    says the hub's sentence, in any case and for any word of the place but its kind;
    the admin's read keeps the model's words, and a place the card does not hold stays."""
    places = ["Provincia di Bergamo", "Torino", "Verona", "Roma", "Alto Adige"]
    ids = {
        _talent(clean, n, card={**CARD, "luogo": luogo}): luogo
        for n, luogo in enumerate(places, start=1)
    }
    positions = _positions(clean)
    reasons = [
        "Lavora a BERGAMO, vicino al cliente, e conosce il dominio.",
        "Nove anni di API in Python, quello che serve al gestionale.",
        "Ha già lavorato in provincia con aziende come questa.",
        "Vive a Milano e conosce la logistica.",
        "Competenze di alto livello su Kafka, quello che serve alla pipeline.",
    ]
    llm = RecordingCall(
        [
            proposal_response(
                [
                    _member(positions[freelancer_id], motivazione=reason)
                    for freelancer_id, reason in zip(ids, reasons, strict=True)
                ]
            )
        ]
    )
    builder = _builder(clean, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    # «di alto livello» is no place: a word of the place counts only with a capital.
    assert [member.motivazione for member in public.team] == [
        PLACE_WITHHELD_REASON,
        reasons[1],
        reasons[2],
        reasons[3],
        reasons[4],
    ]
    assert PLACE_WITHHELD_REASON == "Profilo adatto al ruolo."
    assert "BERGAMO" not in public.model_dump_json()
    admin = builder.get(public.id, public=False)
    assert [member.motivazione for member in admin.team] == reasons


def test_a_public_summary_that_names_a_members_place_is_withheld(clean: Session) -> None:
    """The prompt lets the summary name the place the description names and no place of
    a person; a summary that names a member's card place anyway would give back what the
    public read withholds, unless that place is the one the description names as the
    client's (`luogo.dove`). The row and the admin's read keep the model's words."""
    torino = _talent(clean, 1, card={**CARD, "luogo": "Torino"})
    verona = _talent(clean, 2, card={**CARD, "luogo": "Provincia di Verona"})
    alba = _talent(clean, 3, card={**CARD, "luogo": "Alba"})
    rho = _talent(clean, 4, card={**CARD, "luogo": "Rho"})
    positions = _positions(clean)
    kept = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa lavora da remoto."
    leaked = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a VERONA."
    lowered = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a verona."
    dawn = "Un'azienda cerca un backend developer per un turno all'alba: chi lo fa vive ad Alba."
    copied = "Un'azienda cerca un backend developer in sede dal cliente: chi lo fa vive ad Alba."
    short = "Un'azienda cerca un backend developer in sede a Torino: chi lo fa vive a Rho."
    llm = RecordingCall(
        [
            proposal_response(
                [_member(positions[torino])], riassunto=kept, locale=True, dove="Torino"
            ),
            proposal_response(
                [_member(positions[verona])], riassunto=leaked, locale=True, dove="Torino"
            ),
            proposal_response(
                [_member(positions[verona])], riassunto=lowered, locale=True, dove="Torino"
            ),
            proposal_response([_member(positions[alba])], riassunto=dawn, locale=False, dove=None),
            proposal_response(
                [_member(positions[alba])], riassunto=copied, locale=True, dove="Alba"
            ),
            proposal_response(
                [_member(positions[rho])], riassunto=short, locale=True, dove="Torino"
            ),
        ]
    )
    builder = _builder(clean, llm)
    descrizione = "Cerchiamo un backend developer in sede a torino due giorni a settimana."

    named = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld_lower = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )
    withheld_dawn = builder.propose(
        TeamProposalCreate(
            descrizione="Cerchiamo un backend developer per le integrazioni che partono all'alba."
        ),
        origine="pubblico",
        user_id=None,
    )
    withheld_copied = builder.propose(
        TeamProposalCreate(descrizione="Cerchiamo un backend developer in sede dal cliente."),
        origine="pubblico",
        user_id=None,
    )
    withheld_short = builder.propose(
        TeamProposalCreate(descrizione=descrizione), origine="pubblico", user_id=None
    )

    # «Torino» is the place the description names as the client's, whatever case the
    # visitor typed it in; «Verona» is the catalogue's alone, and the summary is read in
    # any case, unlike a card's field; «all'alba» in a description is no pass for Alba,
    # and neither is an «Alba» the model wrote into `dove` on its own.
    assert named.riassunto == kept
    assert withheld.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_lower.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_dawn.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_copied.riassunto == PLACE_WITHHELD_RIASSUNTO
    assert withheld_short.riassunto == PLACE_WITHHELD_RIASSUNTO
    # The public `luogo` keeps a `dove` the visitor wrote and drops one the model made up.
    assert named.luogo == {"locale": True, "dove": "Torino"}
    assert withheld_copied.luogo == {"locale": True, "dove": None}
    assert builder.get(withheld_copied.id, public=False).luogo == {"locale": True, "dove": "Alba"}
    assert PLACE_WITHHELD_RIASSUNTO == (
        "Il riassunto di questa proposta non è pubblico: la modalità di lavoro di ogni "
        "persona proposta è sulla sua scheda."
    )
    assert "VERONA" not in withheld.model_dump_json()
    assert "Alba" not in withheld_dawn.model_dump_json()
    assert "Alba" not in withheld_copied.riassunto
    assert builder.get(withheld.id, public=False).riassunto == leaked
    assert builder.get(withheld.id, public=True).riassunto == PLACE_WITHHELD_RIASSUNTO


def test_a_public_card_names_its_place_nowhere(clean: Session) -> None:
    """The public read withholds `luogo`, so a card that names the place anywhere else
    (one written against the prompt, or before its rule) gives none of it back: the
    role reads as the role in this team, the summary as the hub's sentence, and a skill
    or a sector that names it is left out. The admin's read keeps the card whole."""
    card = {
        **CARD,
        "ruolo": "Backend developer a Bergamo",
        "competenze": ["Python", "Rete Bergamo Smart City"],
        "settori": ["fintech", "turismo bergamasco", "logistica di Bergamo"],
        "luogo": "Bergamo",
        "sintesi": "Backend developer senior di base a Bergamo, nove anni di fintech.",
    }
    freelancer_id = _talent(clean, 1, card=card)
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[freelancer_id], ruolo="Backend lead")])])
    builder = _builder(clean, llm)

    public = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    [scheda] = [member.scheda for member in public.team]
    assert scheda.luogo is None
    assert scheda.ruolo == "Backend lead"
    assert scheda.sintesi == PLACE_WITHHELD_SUMMARY
    assert PLACE_WITHHELD_SUMMARY == "La sintesi di questo profilo non è pubblica."
    assert scheda.competenze == ["Python"]
    # «bergamasco» is another word: the guard reads the place's own words.
    assert scheda.settori == ["fintech", "turismo bergamasco"]
    assert "Bergamo" not in public.model_dump_json()
    [admin] = [member.scheda for member in builder.get(public.id, public=False).team]
    assert admin == Card.model_validate(card)


@pytest.mark.parametrize("change", ["deleted", "scartato"])
def test_a_read_leaves_out_who_left_the_catalogue(clean: Session, change: str) -> None:
    staying = _talent(clean, 1)
    leaving = _talent(clean, 2, tariffa=Decimal("300.00"))
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[staying]), _member(ids[leaving])])])
    builder = _builder(clean, llm)
    proposal = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )
    row = clean.get(Freelancer, leaving)
    assert row is not None
    if change == "deleted":
        row.deleted_at = NOW
    else:
        row.stato = "scartato"
    clean.commit()

    read = builder.get(proposal.id, public=False)

    assert [(m.posizione, m.freelancer_id) for m in read.team] == [(1, staying)]
    assert read.economia["giorno"] == Band(min=500, max=650)  # the one left, alone
    [stored] = _rows(clean)
    assert len(stored.team) == 2  # the row keeps them


def test_a_read_skips_a_card_that_no_longer_validates(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    first = _talent(clean, 1)
    second = _talent(clean, 2)
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[first]), _member(ids[second])])])
    builder = _builder(clean, llm)
    proposal = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )
    card = clean.get(FreelancerCard, second)
    assert card is not None
    card.card = {"ruolo": "Ruolo segreto", "anni": "nove"}
    clean.commit()

    read = builder.get(proposal.id, public=True)

    assert [m.posizione for m in read.team] == [1]
    assert f"{proposal.id}: member 2" in logs.text and "not a card" in logs.text
    assert "Ruolo segreto" not in logs.text


def test_proposal_row_keeps_the_tokens_and_origin(clean: Session) -> None:
    first = _talent(clean, 1)
    second = _talent(clean, 2, tariffa=Decimal("300.00"))
    ids = _positions(clean)
    owner = _user(clean, "referente@acme.it")
    capture = FakeCapture()
    llm = RecordingCall(
        [proposal_response([_member(ids[first]), _member(ids[second], "Tech lead", giorni=2)])]
    )

    read = _builder(clean, llm, tracker=Tracker(capture)).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE),
        origine="cloud",
        user_id=owner,
    )

    [row] = _rows(clean)
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
            "motivazione": _member("t1")["motivazione"],
            "giorni_settimana": 5,
        },
        {
            "posizione": 2,
            "freelancer_id": str(second),
            "ruolo": "Tech lead",
            "motivazione": _member("t1")["motivazione"],
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


def test_a_tracker_that_fails_never_fails_the_proposal(clean: Session) -> None:
    first = _talent(clean, 1)
    ids = _positions(clean)
    llm = RecordingCall([proposal_response([_member(ids[first])])])

    read = _builder(clean, llm, tracker=Tracker(FakeCapture(raises=True))).propose(
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


def test_the_number_of_people_reaches_the_prompt_only_when_asked(clean: Session) -> None:
    """REB-591: the page sends how many people the visitor wants; the MCP tool sends
    nothing and the team is sized from the description, as before."""
    _talent(clean, 1)
    llm = RecordingCall([proposal_response([_member("t1")]) for _ in range(3)])
    capture = FakeCapture()
    builder = _builder(clean, llm, tracker=Tracker(capture))

    builder.propose(TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None)
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=1), origine="pubblico", user_id=None
    )
    builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE, persone=3), origine="pubblico", user_id=None
    )

    assert "exactly" not in _user_text(llm.requests[0])
    assert "The visitor wants a team of exactly 1 person." in _user_text(llm.requests[1])
    assert "The visitor wants a team of exactly 3 people." in _user_text(llm.requests[2])
    # The number is a sentence of the user turn: the cached system prefix never moves.
    assert llm.requests[2].system == llm.requests[0].system
    assert "The visitor wants a team of exactly N" in llm.requests[0].system[0]["text"]
    # Place is a preference, never an exclusion (REB-598): the rule says so in as many
    # words, and the old sentence is gone.
    rules = llm.requests[0].system[0]["text"]
    assert "excludes nobody" in rules and "propose nobody" not in rules
    assert "a null modalita is unknown, never remote" in rules
    # The event tells the asked number from the proposed one, and the row keeps the
    # asked one (0028), `NULL` when nobody asked.
    assert [(p["persone"], p["persone_richieste"]) for _, _, p in capture.calls] == [
        (1, None),
        (1, 1),
        (1, 3),
    ]
    assert [row.persone for row in _rows(clean)] == [None, 1, 3]
