"""The anonymous CV's writer (REB-862): one call per CV file, checked by the guard, kept and
reused until the CV or the prompt changes, and gone with the CV.

The model is a `RecordingCall` answering canned CVs, or `_During` where a test needs an outage
or something to happen while the model writes; the CVs are real PDFs with a line of text
(`fakes_cards.text_pdf`), so the text goes through `FreelancerService.cv_text` exactly as a
real upload's would. Every answer's `identita` is empty unless a test hands one, so each test
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
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.anonymous_cv import writer
from rebase_core.anonymous_cv.schema import AnonymousCv, AnonymousCvDraft
from rebase_core.anonymous_cv.writer import (
    CV_PROMPT_VERSION,
    AnonymousCvWriter,
    Written,
)
from rebase_core.cards import NO_TEXT, PERSONA_CV
from rebase_core.contract_schemas import FiscalData
from rebase_core.db import session_factory
from rebase_core.errors import AnonymousCvFailed, LlmNotConfigured, LlmUnavailable, NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmRequest, LlmResponse, RecordingCall
from rebase_core.members import MemberService
from rebase_core.models import AnonymousCvRow, FreelancerCard, User
from rebase_core.schemas import FreelancerCreate

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=UTC)
MODEL = "claude-opus-5"
CV = text_pdf("Mario Messina Backend developer TIM 2019-2024")
CV_2025 = text_pdf("Mario Messina Backend lead TIM 2019-2025")
# A header on nothing: `extract_text` reads no page and no text, which is a scan's answer.
SCAN = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"

BODY: dict[str, Any] = {
    "lingua": "it",
    "titolo": "Backend developer",
    "sommario": "Backend developer con cinque anni di servizi di fatturazione in TIM.",
    "esperienze": [
        {
            "ruolo": "Backend developer",
            "azienda": "TIM S.p.A.",
            "luogo": "Roma, IT",
            "periodo": "2019 – 2024",
            "punti": [{"titolo": None, "testo": "Ha sviluppato i servizi di fatturazione."}],
        }
    ],
    "competenze": [{"area": "Competenze", "voci": ["Python", "PostgreSQL"]}],
    "formazione": [],
    "certificazioni": [],
    "lingue": [{"lingua": "Italiano", "livello": "Madrelingua"}],
    "altre_sezioni": [],
}
NO_IDENTITA: dict[str, Any] = {
    "nome": None,
    "cognome": None,
    "email": None,
    "telefono": None,
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


def _with_bullet(testo: str) -> dict[str, Any]:
    body = copy.deepcopy(BODY)
    body["esperienze"][0]["punti"][0]["testo"] = testo
    return body


def cv_response(
    body: dict[str, Any] | None = None,
    *,
    identita: dict[str, Any] | None = None,
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
        else json.dumps({"cv": body or BODY, "identita": identita or NO_IDENTITA})
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
        nome="Mario",
        cognome="Messina",
        email=email,
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
        links=links or [],
    )
    service = FreelancerService(session)
    read, _ = (
        service.apply(data) if cv is None else service.apply(data, cv, "Mario Messina.pdf", "")
    )
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _stored(session: Session, freelancer_id: UUID) -> AnonymousCvRow | None:
    return session.get(AnonymousCvRow, freelancer_id, populate_existing=True)


def _writer(session: Session, llm: RecordingCall | None) -> AnonymousCvWriter:
    return AnonymousCvWriter(session, llm, now=lambda: NOW)


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
    _keep(clean, freelancer_id, _with(sommario="Mario Messina, backend developer in TIM."))
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
    _keep(clean, freelancer_id, _with(sommario="Mario Messina, backend developer in TIM."))

    with pytest.raises(LlmNotConfigured) as error:
        _writer(clean, None).get_or_write(freelancer_id)

    assert error.value.message == "Il compito «CV anonimo» non è configurato in «Modelli AI»."


def test_new_cv_writes_again(clean: Session) -> None:
    freelancer_id = _apply(clean)
    _writer(clean, RecordingCall([cv_response()])).get_or_write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, CV_2025, "cv 2025.pdf", "application/pdf")
    newer = _with(titolo="Backend lead")
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
    named = _with(sommario="Mario Messina è un backend developer con cinque anni in TIM.")
    clean_body = _with(sommario="Backend developer con cinque anni in TIM.")
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
    assert "sommario" in fix
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
    named = _with(sommario="Mario Messina, backend developer.")
    llm = RecordingCall([cv_response(named), cv_response(_with_bullet("Lavora con Messina."))])

    with pytest.raises(AnonymousCvFailed) as error:
        _writer(clean, llm).get_or_write(freelancer_id)

    assert error.value.message == (
        "Il CV contiene ancora il nome, un recapito o un link della persona anche dopo "
        "la riscrittura."
    )
    assert len(llm.requests) == 2
    assert _stored(clean, freelancer_id) is None
    # The log names the freelancer and the kind of failure, never the person or the CV.
    assert str(freelancer_id) in logs.text
    for secret in ("Mario", "Messina", "backend developer", "sommario"):
        assert secret not in logs.text


def test_identity_from_the_answer_is_checked(clean: Session) -> None:
    """The profile has no phone: the number the CV itself states, read back in `identita`,
    is what the guard finds in a bullet."""
    freelancer_id = _apply(clean)
    identita = {**NO_IDENTITA, "telefono": "333 1234567"}
    leaking = cv_response(_with_bullet("Reperibile al 333 1234567."), identita=identita)
    llm = RecordingCall([leaking, cv_response(identita=identita)])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 2
    assert "esperienze[0].punti[0].testo" in llm.requests[1].messages[-1].text
    assert written.cv == AnonymousCv.model_validate(BODY)


def test_the_cards_persona_is_checked(clean: Session) -> None:
    """The card's stored persona for this CV (REB-855) is the third reading of the person:
    its phone, which neither the profile nor the answer has, is found in a bullet."""
    freelancer_id = _apply(clean)
    persona = {
        "nome": "Mario",
        "cognome": "Messina",
        "email": None,
        "telefono": "+39 347 765 4321",
        "citta": None,
        "linkedin_url": None,
        "altri_link": [],
        "aziende": [],
        PERSONA_CV: _sha(CV),
    }
    clean.add(FreelancerCard(freelancer_id=freelancer_id, persona=persona))
    clean.commit()
    llm = RecordingCall([cv_response(_with_bullet("Chiamare il 3477654321.")), cv_response()])

    _writer(clean, llm).get_or_write(freelancer_id)

    assert len(llm.requests) == 2
    assert "esperienze[0].punti[0].testo" in llm.requests[1].messages[-1].text


def _rewritten_for(llm: RecordingCall, path: str) -> None:
    """The answer was asked again once, for `path`."""
    assert len(llm.requests) == 2
    assert path in llm.requests[1].messages[-1].text


def test_the_tax_data_is_checked(clean: Session, logs: pytest.LogCaptureFixture) -> None:
    """The partita IVA and codice fiscale an admin saved for the contracts (REB-387) are
    the person: the partita IVA written with no label, which only the saved one can catch,
    is asked again, and neither number reaches a log."""
    freelancer_id = _apply(clean)
    FiscalService(clean).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="MSSMRA85M01F158K",
            partita_iva="01234567890",
            domicilio="Via Roma 1, Messina",
            pec="studio.fatture@pec.it",
        ),
        _admin(clean),
    )
    llm = RecordingCall([cv_response(_with_bullet("Fatture n. 0123 456 7890.")), cv_response()])

    written = _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "esperienze[0].punti[0].testo")
    assert written.cv == AnonymousCv.model_validate(BODY)
    for secret in ("01234567890", "0123 456 7890", "MSSMRA85M01F158K", "studio.fatture"):
        assert secret not in logs.text


def test_the_pec_is_checked_as_an_email(clean: Session) -> None:
    """The PEC's handle, written without its domain, is the person as an email's is."""
    freelancer_id = _apply(clean)
    FiscalService(clean).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="MSSMRA85M01F158K",
            partita_iva=None,
            domicilio="Via Roma 1, Messina",
            pec="studio.fatture@pec.it",
        ),
        _admin(clean),
    )
    llm = RecordingCall(
        [cv_response(_with_bullet("Firma le fatture come studio.fatture.")), cv_response()]
    )

    _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "esperienze[0].punti[0].testo")


def test_the_profiles_link_is_checked(clean: Session) -> None:
    """The handle of a link on the profile (`freelancers.links`) is found in a bullet that
    writes it bare, with no host around it."""
    freelancer_id = _apply(clean, links=["https://github.com/mmessina90"])
    llm = RecordingCall(
        [cv_response(_with_bullet("Mantiene il progetto mmessina90 su fatture.")), cv_response()]
    )

    _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "esperienze[0].punti[0].testo")


def test_the_profiles_email_is_checked(clean: Session) -> None:
    """The profile's email, by its handle: an answer that signs with it is asked again."""
    freelancer_id = _apply(clean, email="zorro.dev@studio.it")
    llm = RecordingCall(
        [
            cv_response(_with(sommario="Backend developer, firma i commit come zorro.dev.")),
            cv_response(),
        ]
    )

    _writer(clean, llm).get_or_write(freelancer_id)

    _rewritten_for(llm, "sommario")


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

    _writer(clean, None).delete(freelancer_id)
    clean.rollback()

    assert _stored(clean, freelancer_id) is not None
