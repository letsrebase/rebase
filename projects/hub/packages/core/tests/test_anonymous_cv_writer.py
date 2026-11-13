"""The anonymous CV's writer (REB-862): one call per CV file, checked by the guard, kept and
reused until the CV or the prompt changes, and gone with the CV.

The model is a `RecordingCall` answering canned CVs, or `_During` where a test needs an outage
or something to happen while the model writes; the CVs are real PDFs with a line of text
(`fakes_cards.text_pdf`), so the text goes through `FreelancerService.cv_text` exactly as a
real upload's would. Every answer's `identity` is empty unless a test hands one, so each test
proves which reading of the person (the profile, the card's persona, the answer) caught it.
"""

import copy
import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_cards import text_pdf
from fakes_crm_door import FakeCrmDoor
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.anonymous_cv import writer
from rebase_core.anonymous_cv.schema import AnonymousCv, AnonymousCvDraft
from rebase_core.anonymous_cv.writer import (
    CV_PROMPT_VERSION,
    AnonymousCvWriter,
    Written,
    delete_anonymous_cv,
)
from rebase_core.card_person import PERSON_CV
from rebase_core.card_types import NO_TEXT
from rebase_core.db import session_factory
from rebase_core.errors import AnonymousCvFailed, LlmNotConfigured, LlmUnavailable, NotFound
from rebase_core.freelancer_schemas import FreelancerCreate
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmRequest, LlmResponse, RecordingCall
from rebase_core.members import MemberService
from rebase_core.models import AnonymousCvRow, FreelancerCard, User
from rebase_core.pigro_door import PigroMisconfigured, PigroUnavailable

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
MODEL = "claude-opus-5"
CV = text_pdf("Mario Messina Backend developer TIM 2019-2024")
CV_2025 = text_pdf("Mario Messina Backend lead TIM 2019-2025")
# A header on nothing: `extract_text` reads no page and no text, which is a scan's answer.
SCAN = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"

BODY: dict[str, Any] = {
    "language": "it",
    "title": "Backend developer",
    "summary": "Backend developer con cinque anni di servizi di fatturazione in TIM.",
    "experience": [
        {
            "role": "Backend developer",
            "company": "TIM S.p.A.",
            "location": "Roma, IT",
            "period": "2019 – 2024",
            "points": [{"title": None, "text": "Ha sviluppato i servizi di fatturazione."}],
        }
    ],
    "skills": [{"area": "Competenze", "entries": ["Python", "PostgreSQL"]}],
    "education": [],
    "certifications": [],
    "languages": [{"language": "Italiano", "level": "Madrelingua"}],
    "other_sections": [],
}
NO_IDENTITY: dict[str, Any] = {
    "first_name": None,
    "last_name": None,
    "email": None,
    "phones": [],
    "link": [],
}
# What the system prompt must name, each in its words: what never goes in the CV.
EXCLUSIONS = (
    "nome",
    "cognome",
    "email",
    "telefono",
    "indirizzo postale",
    "data e il luogo di nascita",
    "nazionalità",
    "stato civile",
    "foto",
    "consenso al trattamento dei dati",
    "link",
    "url",
    "handle",
    "nome utente",
    "linkedin",
    "github",
    "sito personale",
    "portfolio",
    "sito di un'azienda",
    "[nome]",
    "partita iva",
    "vat number",
    "codice fiscale",
    "tax code",
    "pec",
    "iban",
    "numero di un documento",
)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _with(**fields: Any) -> dict[str, Any]:
    return {**copy.deepcopy(BODY), **fields}


def _with_bullet(text: str) -> dict[str, Any]:
    body = copy.deepcopy(BODY)
    body["experience"][0]["points"][0]["text"] = text
    return body


def cv_response(
    body: dict[str, Any] | None = None,
    *,
    identity: dict[str, Any] | None = None,
    raw: str | None = None,
    stop_reason: str = "end_turn",
    input_tokens: int = 3000,
    output_tokens: int = 2500,
    thinking_tokens: int = 400,
) -> LlmResponse:
    """The model's answer as `AiCall` hands it over: the JSON text, and `parsed` validated as
    `AnonymousCvDraft`, `None` when it is not one; `raw` is a body verbatim."""
    answer = (
        raw
        if raw is not None
        else json.dumps({"cv": body or BODY, "identity": identity or NO_IDENTITY})
    )
    try:
        parsed = AnonymousCvDraft.model_validate_json(answer) if stop_reason == "end_turn" else None
    except ValueError:
        parsed = None
    return LlmResponse(
        text=None if stop_reason == "refusal" else answer,
        parsed=parsed,
        stop_reason=stop_reason,
        refusal_category="cyber" if stop_reason == "refusal" else None,
        model=MODEL,
        provider="anthropic",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        thinking_tokens=thinking_tokens,
    )


class _During(RecordingCall):
    """`RecordingCall` that runs `during` inside the call, before it answers, for what
    happens elsewhere while the model writes; `error` is raised instead of answering."""

    def __init__(
        self,
        responses: list[LlmResponse],
        during: Callable[[], None] | None = None,
        error: BaseException | None = None,
    ) -> None:
        super().__init__(responses)
        self._during = during
        self._error = error

    def complete(self, request: LlmRequest) -> LlmResponse:
        if self._during is not None:
            self._during()
        if self._error is not None:
            self.requests.append(request)
            raise self._error
        return super().complete(request)


def _apply(
    session: Session,
    cv: bytes | None = CV,
    links: list[str] | None = None,
    email: str = "mario@studio.it",
) -> UUID:
    data = FreelancerCreate(
        first_name="Mario",
        last_name="Messina",
        email=email,
        daily_rate=Decimal("450.00"),
        job_title="Backend developer",
        remote="remote",
        links=links or [],
    )
    service = FreelancerService(session)
    read, _ = (
        service.apply(data) if cv is None else service.apply(data, cv, "Mario Messina.pdf", "")
    )
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", first_name="Ivan", last_name="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _stored(session: Session, freelancer_id: UUID) -> AnonymousCvRow | None:
    return session.get(AnonymousCvRow, freelancer_id, populate_existing=True)


def _writer(
    session: Session, llm: RecordingCall | None, crm: FakeCrmDoor | None = None
) -> AnonymousCvWriter:
    """The writer over a fake CRM door that knows nothing about the person unless `crm` does."""
    return AnonymousCvWriter(session, llm, (crm or FakeCrmDoor()).door(), now=lambda: NOW)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM notes"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


@pytest.fixture
def logs(clean: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on the writer's logger, after `clean`: Alembic's `fileConfig` disables every
    logger `alembic.ini` does not list (the trap `test_llm.py` documents)."""
    logging.getLogger("rebase_core.anonymous_cv.writer").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.anonymous_cv.writer")
    return caplog


def test_first_download_writes_and_stores(clean: Session) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall([cv_response()])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert written == Written(AnonymousCv.model_validate(BODY), reused=False, cv_sha256=_sha(CV))
    [request] = llm.requests
    assert request.output_model is AnonymousCvDraft
    system = request.system.lower()
    for exclusion in EXCLUSIONS:
        assert exclusion in system, exclusion
    # The CV's text alone, as an inert data block; never its filename, which names the person.
    [turn] = request.messages
    assert turn.role == "user"
    assert '<source key="cv">' in turn.text
    assert "Mario Messina Backend developer TIM 2019-2024" in turn.text
    assert "Mario Messina.pdf" not in turn.text
    # The person the call is about goes on its usage row (REB-861's seam), never to the model.
    assert request.freelancer_id == freelancer_id
    assert str(freelancer_id) not in request.system + turn.text

    stored = _stored(clean, freelancer_id)
    assert stored is not None
    assert (stored.cv_sha256, stored.prompt_version) == (_sha(CV), CV_PROMPT_VERSION)
    assert stored.body == BODY
    assert (stored.provider, stored.model, stored.generated_at) == ("anthropic", MODEL, NOW)
    assert (stored.input_tokens, stored.output_tokens, stored.thinking_tokens) == (3000, 2500, 400)


def test_the_database_is_released_during_the_call(clean: Session) -> None:
    """The model takes seconds: no transaction, and so no pooled connection, is held open
    across the call."""
    freelancer_id = _apply(clean)
    held: list[bool] = []
    llm = _During([cv_response()], during=lambda: held.append(clean.in_transaction()))

    _writer(clean, llm).get_or_write(freelancer_id)

    assert held == [False]


def test_second_download_reuses_without_a_call(clean: Session) -> None:
    freelancer_id = _apply(clean)
    first = _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    llm = RecordingCall([])

    again = _writer(clean, llm).get_or_write(freelancer_id)

    assert llm.requests == []
    assert again == Written(first.cv, reused=True, cv_sha256=_sha(CV))
    # With no model configured, a body already written for this CV is still served.
    assert _writer(clean, None).get_or_write(freelancer_id) == again


def _keep(session: Session, freelancer_id: UUID, body: dict[str, Any]) -> None:
    """A body kept for the CV on file under this prompt version, as an older guard let it
    through."""
    session.add(
        AnonymousCvRow(
            freelancer_id=freelancer_id,
            cv_sha256=_sha(CV),
            prompt_version=CV_PROMPT_VERSION,
            body=body,
            provider="anthropic",
            model=MODEL,
            generated_at=NOW,
        )
    )
    session.commit()


def test_reused_body_the_guard_now_refuses_is_written_again(clean: Session) -> None:
    """A guard fixed later covers the bodies already kept: one it refuses now is stale, and
    is written again rather than served."""
    freelancer_id = _apply(clean)
    _keep(clean, freelancer_id, _with(summary="Mario Messina, backend developer in TIM."))
    llm = RecordingCall([cv_response()])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 1
    assert written == Written(AnonymousCv.model_validate(BODY), reused=False, cv_sha256=_sha(CV))
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.body == BODY


def test_reused_body_the_guard_now_refuses_is_not_served_without_a_model(
    clean: Session,
) -> None:
    freelancer_id = _apply(clean)
    _keep(clean, freelancer_id, _with(summary="Mario Messina, backend developer in TIM."))

    with pytest.raises(LlmNotConfigured) as error:
        _writer(clean, None).get_or_write(freelancer_id)

    assert error.value.message == "Il compito «CV anonimo» non è configurato in «Modelli AI»."


def test_a_kept_body_that_is_no_longer_a_cv_is_written_again(clean: Session) -> None:
    """A body kept under this version that the shape no longer reads (a field renamed by
    hand, a limit lowered) is stale: written again, never a 500."""
    freelancer_id = _apply(clean)
    _keep(clean, freelancer_id, {**BODY, "removed_field": "un campo che non c'è più"})
    llm = RecordingCall([cv_response()])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 1
    assert written.cv == AnonymousCv.model_validate(BODY)
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.body == BODY


def test_a_kept_body_that_is_no_longer_a_cv_is_not_served_without_a_model(
    clean: Session,
) -> None:
    freelancer_id = _apply(clean)
    _keep(clean, freelancer_id, {"language": "fr"})

    with pytest.raises(LlmNotConfigured):
        _writer(clean, None).get_or_write(freelancer_id)


def test_new_cv_writes_again(clean: Session) -> None:
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, CV_2025, "cv 2025.pdf", "application/pdf")
    newer = _with(title="Backend lead")
    llm = RecordingCall([cv_response(newer)])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 1
    assert "Backend lead TIM 2019-2025" in llm.requests[0].messages[0].text
    assert written == Written(
        AnonymousCv.model_validate(newer), reused=False, cv_sha256=_sha(CV_2025)
    )
    stored = _stored(clean, freelancer_id)
    assert stored is not None
    assert (stored.cv_sha256, stored.body) == (_sha(CV_2025), newer)


def test_prompt_version_bump_writes_again(clean: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    monkeypatch.setattr(writer, "CV_PROMPT_VERSION", 2)
    llm = RecordingCall([cv_response()])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 1
    assert written.reused is False
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.prompt_version == 2


def test_no_model_and_nothing_to_reuse(clean: Session) -> None:
    freelancer_id = _apply(clean)

    with pytest.raises(LlmNotConfigured) as error:
        _writer(clean, None).get_or_write(freelancer_id)

    assert error.value.message == "Il compito «CV anonimo» non è configurato in «Modelli AI»."
    assert _stored(clean, freelancer_id) is None


def test_identifying_answer_is_rewritten_once(clean: Session) -> None:
    """The profile's name, in the summary: one rewrite, asked with the answer as the model's
    turn and the fields to fix (never the words) as the next user turn."""
    freelancer_id = _apply(clean)
    named = _with(summary="Mario Messina è un backend developer con cinque anni in TIM.")
    clean_body = _with(summary="Backend developer con cinque anni in TIM.")
    first = cv_response(named, input_tokens=3000, output_tokens=2500, thinking_tokens=400)
    second = cv_response(clean_body, input_tokens=5600, output_tokens=2400, thinking_tokens=300)
    llm = RecordingCall([first, second])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 2
    retry = llm.requests[1]
    assert retry.freelancer_id == freelancer_id
    assert retry.system == llm.requests[0].system
    assert retry.output_model is AnonymousCvDraft
    assert [message.role for message in retry.messages] == ["user", "assistant", "user"]
    assert retry.messages[1].text == first.text
    fix = retry.messages[-1].text
    assert "summary" in fix
    assert "Messina" not in fix and "Mario" not in fix
    assert written == Written(
        AnonymousCv.model_validate(clean_body), reused=False, cv_sha256=_sha(CV)
    )
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.body == clean_body
    # Both calls were paid for.
    assert (stored.input_tokens, stored.output_tokens, stored.thinking_tokens) == (8600, 4900, 700)


def test_identifying_twice_fails_and_stores_nothing(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    freelancer_id = _apply(clean)
    named = _with(summary="Mario Messina, backend developer.")
    llm = RecordingCall([cv_response(named), cv_response(_with_bullet("Lavora con Messina."))])

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == (
        "Il CV contiene ancora il nome, un recapito, un link, un codice fiscale, una partita "
        "IVA o la data di nascita della persona anche dopo la riscrittura."
    )
    assert len(llm.requests) == 2
    assert _stored(clean, freelancer_id) is None
    # The log names the freelancer and the kind of failure, never the person or the CV.
    assert str(freelancer_id) in logs.text
    for secret in ("Mario", "Messina", "backend developer", "summary"):
        assert secret not in logs.text


@pytest.mark.parametrize(
    ("again", "sentence"),
    [
        (cv_response(stop_reason="refusal"), "Il modello ha rifiutato di scrivere il CV."),
        (
            cv_response(stop_reason="max_tokens"),
            "Il CV è troppo lungo per il tetto di risposta del compito: alzalo in «Modelli AI».",
        ),
        (cv_response(raw='{"cv": "un testo"}'), "La risposta del modello non era un CV valido."),
    ],
)
def test_a_rewrite_that_fails_says_why_and_stores_nothing(
    clean: Session, again: LlmResponse, sentence: str
) -> None:
    """The rewrite is a call like the first: refused, cut or not the shape, it fails with
    its own sentence rather than «still identifying»."""
    freelancer_id = _apply(clean)
    llm = RecordingCall([cv_response(_with(summary="Mario Messina, backend developer.")), again])

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == sentence
    assert len(llm.requests) == 2
    assert _stored(clean, freelancer_id) is None


def test_an_outage_on_the_rewrite_propagates_and_stores_nothing(clean: Session) -> None:
    freelancer_id = _apply(clean)
    down = LlmUnavailable("Il modello non ha risposto: riprova tra poco.")

    class _DownOnTheRewrite(RecordingCall):
        def complete(self, request: LlmRequest) -> LlmResponse:
            if self.requests:
                self.requests.append(request)
                raise down
            return super().complete(request)

    llm = _DownOnTheRewrite([cv_response(_with(summary="Mario Messina, backend developer."))])

    with pytest.raises(LlmUnavailable) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value is down
    assert len(llm.requests) == 2
    assert _stored(clean, freelancer_id) is None


def test_identity_from_the_answer_is_checked(clean: Session) -> None:
    """The profile has no phone: the numbers the CV itself states, read back in `identity`,
    are what the guard finds in a bullet, the second as surely as the first."""
    freelancer_id = _apply(clean)
    identity = {**NO_IDENTITY, "phones": ["333 1234567", "+39 06 9876 5432"]}
    leaking = cv_response(_with_bullet("Ufficio: 06 98765432."), identity=identity)
    llm = RecordingCall([leaking, cv_response(identity=identity)])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 2
    assert "experience[0].points[0].text" in llm.requests[1].messages[-1].text
    assert written.cv == AnonymousCv.model_validate(BODY)


def test_the_cards_person_is_checked(clean: Session) -> None:
    """The card's stored persona for this CV (REB-855) is the third reading of the person:
    its phone, which neither the profile nor the answer has, is found in a bullet."""
    freelancer_id = _apply(clean)
    person = {
        "first_name": "Mario",
        "last_name": "Messina",
        "email": None,
        "phone": "+39 347 765 4321",
        "city": None,
        "linkedin_url": None,
        "other_links": [],
        "companies": [],
        PERSON_CV: _sha(CV),
    }
    clean.add(FreelancerCard(freelancer_id=freelancer_id, person=person))
    clean.commit()
    llm = RecordingCall([cv_response(_with_bullet("Chiamare il 3477654321.")), cv_response()])

    _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 2
    assert "experience[0].points[0].text" in llm.requests[1].messages[-1].text


def _rewritten_for(llm: RecordingCall, path: str) -> None:
    """The answer was asked again once, for `path`."""
    assert len(llm.requests) == 2
    assert path in llm.requests[1].messages[-1].text


def test_the_profiles_link_is_checked(clean: Session) -> None:
    """The handle of a link on the profile (`freelancers.links`) is found in a bullet that
    writes it bare, with no host around it."""
    freelancer_id = _apply(clean, links=["https://github.com/mmessina90"])
    llm = RecordingCall(
        [cv_response(_with_bullet("Mantiene il progetto mmessina90 su fatture.")), cv_response()]
    )

    _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "experience[0].points[0].text")


def test_the_profiles_email_is_checked(clean: Session) -> None:
    """The profile's email, by its handle: an answer that signs with it is asked again."""
    freelancer_id = _apply(clean, email="zorro.dev@studio.it")
    llm = RecordingCall(
        [
            cv_response(_with(summary="Backend developer, firma i commit come zorro.dev.")),
            cv_response(),
        ]
    )

    _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "summary")


def test_refusal(clean: Session) -> None:
    freelancer_id = _apply(clean)

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, RecordingCall([cv_response(stop_reason="refusal")])).get_or_write(
            freelancer_id
        )

    assert error.value.message == "Il modello ha rifiutato di scrivere il CV."
    assert _stored(clean, freelancer_id) is None


def test_max_tokens(clean: Session) -> None:
    freelancer_id = _apply(clean)

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, RecordingCall([cv_response(stop_reason="max_tokens")])).get_or_write(
            freelancer_id
        )

    assert error.value.message == (
        "Il CV è troppo lungo per il tetto di risposta del compito: alzalo in «Modelli AI»."
    )
    assert _stored(clean, freelancer_id) is None


def test_not_a_cv(clean: Session) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall([cv_response(raw='{"cv": "Mario Messina"}')])

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == "La risposta del modello non era un CV valido."
    # The model's words are never the sentence.
    assert "Mario" not in error.value.message
    assert _stored(clean, freelancer_id) is None


def test_no_text(clean: Session) -> None:
    freelancer_id = _apply(clean, cv=SCAN)
    llm = RecordingCall([])

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == NO_TEXT
    assert llm.requests == []
    assert _stored(clean, freelancer_id) is None


def test_no_cv(clean: Session) -> None:
    freelancer_id = _apply(clean, cv=None)
    llm = RecordingCall([])

    with pytest.raises(NotFound) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == "Nessun CV caricato"
    assert llm.requests == []


def test_no_talent(clean: Session) -> None:
    with pytest.raises(NotFound):
        _writer(clean, RecordingCall([])).get_or_write(uuid4())

    freelancer_id = _apply(clean)
    FreelancerService(clean).soft_delete(freelancer_id, _admin(clean))
    llm = RecordingCall([])
    with pytest.raises(NotFound):
        _writer(clean, llm).get_or_write(freelancer_id)
    assert llm.requests == []


def test_cv_replaced_during_the_call_stores_nothing(clean: Session) -> None:
    """The body is stored only if the CV it came from is still the talent's when the answer
    arrives: a new CV gets its own on its own download."""
    freelancer_id = _apply(clean)

    def elsewhere() -> None:
        other = session_factory(clean.get_bind())()  # type: ignore[arg-type]
        try:
            MemberService(other).replace_cv(freelancer_id, CV_2025, "cv 2025.pdf", "")
        finally:
            other.close()

    llm = _During([cv_response()], during=elsewhere)

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == "Il CV è cambiato mentre lo scrivevo: riprova."
    assert len(llm.requests) == 1
    assert _stored(clean, freelancer_id) is None


def test_clear_cv_deletes_the_row(clean: Session) -> None:
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    assert _stored(clean, freelancer_id) is not None

    FreelancerService(clean).clear_cv(freelancer_id, _admin(clean))

    assert _stored(clean, freelancer_id) is None


def test_clear_cv_deletes_the_row_even_without_a_cv(clean: Session) -> None:
    """A row left behind by any path goes the next time an admin clears the CV, as the
    card does."""
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    clean.execute(
        text("UPDATE freelancers SET cv_bytes = NULL, cv_filename = NULL WHERE id = :id"),
        {"id": freelancer_id},
    )
    clean.commit()
    clean.expire_all()

    FreelancerService(clean).clear_cv(freelancer_id, _admin(clean))

    assert _stored(clean, freelancer_id) is None


def test_unavailable_propagates(clean: Session) -> None:
    freelancer_id = _apply(clean)
    down = LlmUnavailable("Il modello non ha risposto: riprova tra poco.")
    llm = _During([], error=down)

    with pytest.raises(LlmUnavailable) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value is down
    assert _stored(clean, freelancer_id) is None


def test_delete_is_left_to_the_callers_commit(clean: Session) -> None:
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)

    delete_anonymous_cv(clean, freelancer_id)
    clean.rollback()

    assert _stored(clean, freelancer_id) is not None


# ---- the CRM's half of the redaction list (REB-1107) --------------------------------------


def _crm_knowing(freelancer_id: UUID) -> FakeCrmDoor:
    """A CRM whose organization for the person holds a tax code, a VAT number and a PEC
    the hub no longer holds anywhere."""
    return FakeCrmDoor(
        fiscal={
            freelancer_id: {
                "tax_code": "MSSMRA85M01F158K",
                "vat_number": "01234567890",
                "certified_email": "studio.fatture@pec.it",
            }
        }
    )


def test_the_crms_identifiers_are_checked_with_none_of_them_in_the_hub(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    """The tax data that moved to the CRM is still the person: a VAT number written with no
    label, which only the CRM's copy can catch, is asked again, the CRM was asked by the
    card's id, and none of the numbers reaches a log."""
    freelancer_id = _apply(clean)
    crm = _crm_knowing(freelancer_id)
    llm = RecordingCall([cv_response(_with_bullet("Fatture n. 0123 456 7890.")), cv_response()])

    written = _writer(clean, llm, crm).get_or_write(freelancer_id)

    _rewritten_for(llm, "experience[0].points[0].text")
    assert written.cv == AnonymousCv.model_validate(BODY)
    assert crm.calls == [("GET", f"/api/rebase/freelancers/{freelancer_id}/fiscal-identifiers")]
    for secret in ("01234567890", "0123 456 7890", "MSSMRA85M01F158K", "studio.fatture"):
        assert secret not in logs.text


def test_the_crms_certified_email_and_tax_code_are_checked_too(clean: Session) -> None:
    freelancer_id = _apply(clean)
    crm = _crm_knowing(freelancer_id)
    handle = RecordingCall(
        [cv_response(_with_bullet("Firma le fatture come studio.fatture.")), cv_response()]
    )
    code = RecordingCall(
        [cv_response(_with_bullet("Codice MSSMRA85M01F158K nei fogli.")), cv_response()]
    )

    _writer(clean, handle, crm).get_or_write(freelancer_id)
    clean.execute(text("DELETE FROM anonymous_cvs"))
    clean.commit()
    _writer(clean, code, crm).get_or_write(freelancer_id)

    _rewritten_for(handle, "experience[0].points[0].text")
    _rewritten_for(code, "experience[0].points[0].text")


def test_a_crm_that_cannot_answer_ends_the_call_before_the_model_is_asked(
    clean: Session,
) -> None:
    """Fails closed: no model call, nothing stored, and no CV, whatever the failure."""
    freelancer_id = _apply(clean)
    for failure in (
        [ConnectionRefusedError(), ConnectionRefusedError()],
        [(500, b"")],
        [(200, b"not json")],
    ):
        crm = FakeCrmDoor()
        crm.down(*failure)
        llm = RecordingCall([cv_response()])

        with pytest.raises(PigroUnavailable):
            _writer(clean, llm, crm).get_or_write(freelancer_id)

        assert llm.requests == []
        assert _stored(clean, freelancer_id) is None


def test_a_kept_body_is_not_served_while_the_crm_cannot_answer(clean: Session) -> None:
    """A kept body is read again against who the person is, and without the CRM's half that
    reading is short of a tax code: it is not served, and served again once the CRM is back."""
    freelancer_id = _apply(clean)
    first = _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    crm = FakeCrmDoor()
    crm.down((503, b""), (503, b""))

    with pytest.raises(PigroUnavailable):
        _writer(clean, None, crm).get_or_write(freelancer_id)

    again = _writer(clean, None, crm).get_or_write(freelancer_id)
    assert again == Written(first.cv, reused=True, cv_sha256=_sha(CV))


def test_a_kept_body_the_crms_tax_code_now_catches_is_written_again(clean: Session) -> None:
    """The CRM's identifiers cover a body kept before the CRM knew them."""
    freelancer_id = _apply(clean)
    _keep(clean, freelancer_id, _with_bullet("Fatture n. 0123 456 7890."))
    llm = RecordingCall([cv_response()])

    written = _writer(clean, llm, _crm_knowing(freelancer_id)).get_or_write(freelancer_id)

    assert len(llm.requests) == 1
    assert written == Written(AnonymousCv.model_validate(BODY), reused=False, cv_sha256=_sha(CV))


def test_an_environment_with_no_token_cannot_write_a_cv(clean: Session) -> None:
    freelancer_id = _apply(clean)
    crm = FakeCrmDoor()
    llm = RecordingCall([cv_response()])
    writer_without_token = AnonymousCvWriter(clean, llm, crm.door(token=""), now=lambda: NOW)

    with pytest.raises(PigroMisconfigured):
        writer_without_token.get_or_write(freelancer_id)

    assert llm.requests == [] and crm.calls == []
