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
from rebase_core.errors import LlmUnavailable, NotFound, TeamBuilderOff, ValidationFailed
from rebase_core.llm import UNAVAILABLE_SENTENCE, LlmRequest, LlmResponse, RecordingCall
from rebase_core.models import Freelancer, FreelancerCard, TeamProposal, User
from rebase_core.team_builder import (
    NO_FIT_SENTENCE,
    PROPOSAL_MAX_TOKENS,
    PROPOSAL_SCHEMA,
    TeamBuilder,
    catalogue_lines,
    cloud_visible,
)
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
    assert request.schema == PROPOSAL_SCHEMA
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


def test_engine_drops_remote_members_on_a_local_need(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    remote = _talent(clean, 1, remoto="remoto")
    unknown = _talent(clean, 2, remoto=None)
    hybrid = _talent(clean, 3, remoto="ibrido")
    on_site = _talent(clean, 4, remoto="in_sede")
    ids = _positions(clean)
    team = [_member(ids[who]) for who in (remote, unknown, hybrid, on_site)]
    llm = RecordingCall(
        [
            proposal_response(team, locale=True, dove="Torino"),
            proposal_response(team, locale=False, dove="Torino"),
        ]
    )
    builder = _builder(clean, llm)

    local = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )
    remote_ok = builder.propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="admin", user_id=None
    )

    assert local.luogo == {"locale": True, "dove": "Torino"}
    assert [(m.posizione, m.freelancer_id) for m in local.team] == [(1, hybrid), (2, on_site)]
    assert [m.freelancer_id for m in remote_ok.team] == [remote, unknown, hybrid, on_site]
    assert "local need" in logs.text
    assert f"{ids[remote]!r}" in logs.text and f"{ids[unknown]!r}" in logs.text


@pytest.mark.parametrize("dropped_by", ["on site", "unknown ids"])
def test_a_team_dropped_whole_carries_the_hubs_sentence(clean: Session, dropped_by: str) -> None:
    """The model's summary describes the team it chose: when the checks leave nobody of
    it, the page says nobody fits rather than describing people who are not there."""
    remote = _talent(clean, 1, remoto="remoto")
    unknown = _talent(clean, 2, remoto=None)
    ids = _positions(clean)
    team = (
        [_member(ids[remote]), _member(ids[unknown])]
        if dropped_by == "on site"
        else [_member("t9"), _member("t12")]
    )
    llm = RecordingCall([proposal_response(team, locale=dropped_by == "on site", dove="Bari")])

    read = _builder(clean, llm).propose(
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert (read.riassunto, read.team) == (NO_FIT_SENTENCE, [])
    assert read.economia == {"giorno": None, "mese": None, "giorni_mese": 22}
    [row] = _rows(clean)
    assert (row.riassunto, row.team) == (NO_FIT_SENTENCE, [])
    assert row.luogo == {"locale": dropped_by == "on site", "dove": "Bari"}


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
        TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
    )

    assert (read.riassunto, read.team) == (sentence, [])
    assert read.luogo == {"locale": True, "dove": "Bari"}
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
    assert _rows(clean) == []
    assert logged in logs.text
    for secret in (DESCRIZIONE, "togli il designer", "Ecco il team", "Un'azienda"):
        assert secret not in logs.text


def test_an_outage_reaches_the_caller_and_writes_nothing(clean: Session) -> None:
    _talent(clean, 1)
    llm = _Scripted([LlmUnavailable(UNAVAILABLE_SENTENCE)])

    with pytest.raises(LlmUnavailable):
        _builder(clean, llm).propose(
            TeamProposalCreate(descrizione=DESCRIZIONE), origine="pubblico", user_id=None
        )

    assert _rows(clean) == []


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
    ],
    ids=["short", "long", "blank", "long note", "unknown field"],
)
def test_the_description_is_forty_to_four_thousand_characters(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        TeamProposalCreate.model_validate(fields)
