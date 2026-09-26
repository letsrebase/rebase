"""The anonymous card (REB-510, spec § 2.1, § 5.1): one call to Claude per CV, stored
with the CV's hash, never paid for twice for the same file, and gone with the CV.

Claude is a `RecordingCall` answering canned cards (`fakes_cards.py`), or `_Scripted`
where a test needs an outage or something to happen during the call; the CVs are real
PDFs with a line of text in them, so the text goes through `FreelancerService.cv_text`
exactly as a real upload's would.
"""

import hashlib
import logging
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fakes_cards import CARD, MODEL, card_response, text_pdf
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.cards import (
    CARD_MAX_TOKENS,
    CARD_SCHEMA,
    NO_TEXT,
    CardWriter,
    write_after_response,
)
from rebase_core.db import session_factory
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import LlmRequest, LlmResponse, LlmUnavailable, RecordingCall
from rebase_core.members import MemberService
from rebase_core.models import Freelancer, FreelancerCard, User
from rebase_core.schemas import FreelancerCreate
from rebase_core.team_schemas import Card, CardsRefreshed

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)
CV = text_pdf("Ada Lovelace, backend developer a Torino da nove anni: Python, FastAPI, AWS.")
CV_2026 = text_pdf("Ada Lovelace, backend lead a Torino: Python, Kubernetes, dieci anni.")
# A header on nothing: `extract_text` reads no page and no text, which is a scan's answer.
SCAN = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _apply(
    session: Session,
    email: str = "ada@studio.it",
    cv: bytes | None = CV,
    cognome: str = "Lovelace",
) -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        cognome=cognome,
        email=email,
        tariffa_giornaliera=Decimal("450.00"),
        posizione="Backend developer",
        remoto="remoto",
    )
    service = FreelancerService(session)
    read, _ = service.apply(data) if cv is None else service.apply(data, cv, "cv.pdf", "")
    return read.id


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _stored(session: Session, freelancer_id: UUID) -> FreelancerCard | None:
    return session.get(FreelancerCard, freelancer_id, populate_existing=True)


def _user_text(request: LlmRequest) -> str:
    return "\n".join(block["text"] for message in request.messages for block in message["content"])


def _down() -> LlmUnavailable:
    """The seam failing the way `AnthropicCall` does on an outage or a run of 429s."""
    return LlmUnavailable("Non riesco a proporre un team adesso: riprova tra poco.")


class _Scripted:
    """`RecordingCall` that can also fail: each answer is a response or an exception to
    raise, and `during` runs inside the call, before it answers, for what happens
    elsewhere while Claude is writing."""

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


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM comments"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


@pytest.fixture
def logs(clean: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """`caplog` on this module's logger. After `clean`, on purpose: the database fixture
    runs Alembic's `env.py`, whose `fileConfig` disables every logger `alembic.ini` does
    not list, this one among them (the trap `test_llm.py` documents)."""
    logging.getLogger("rebase_core.cards").disabled = False
    caplog.set_level(logging.INFO, logger="rebase_core.cards")
    return caplog


def test_card_from_a_cv(clean: Session) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall([card_response()])

    read = CardWriter(clean, llm, now=lambda: NOW).write(freelancer_id)

    assert read.freelancer_id == freelancer_id
    assert read.card == Card.model_validate(CARD)
    assert (read.cv_sha256, read.model, read.generated_at, read.error) == (
        _sha(CV),
        MODEL,
        NOW,
        None,
    )
    assert read.modalita == "remoto"
    stored = _stored(clean, freelancer_id)
    assert stored is not None
    assert (stored.input_tokens, stored.output_tokens) == (1200, 180)
    assert (stored.error, stored.error_cv_sha256) == (None, None)
    assert stored.card == CARD

    # One call, shaped for a card: the schema is `Card`'s own, closed, every property
    # required; the seam strips what the API refuses (`test_llm.py`), and `Card` still
    # enforces it on the answer (the over-limit cases below).
    [request] = llm.requests
    assert request.max_tokens == CARD_MAX_TOKENS == 2000
    assert request.schema == CARD_SCHEMA == Card.model_json_schema()
    assert CARD_SCHEMA["additionalProperties"] is False
    assert CARD_SCHEMA["required"] == list(Card.model_fields)
    assert {"type": "null"} in CARD_SCHEMA["properties"]["luogo"]["anyOf"]
    system = request.system[0]["text"]
    assert "Italian" in system and "null" in system
    user = _user_text(request)
    assert "Backend developer" in user
    assert "Ada Lovelace, backend developer a Torino" in user
    # The rate never reaches Claude: the band is computed from it when a card is shown.
    assert "450" not in user and "450" not in system

    # The work mode is read from the profile when the card is shown, never stored on it.
    row = clean.get(Freelancer, freelancer_id)
    assert row is not None
    row.remoto = "ibrido"
    clean.commit()
    assert CardWriter(clean, None).read(freelancer_id).modalita == "ibrido"


def test_card_follows_the_cv(clean: Session) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall([card_response(), card_response({**CARD, "seniority": "lead"})])
    writer = CardWriter(clean, llm, now=lambda: NOW)
    writer.write(freelancer_id)

    # The same CV again: nothing to pay for.
    again = writer.write(freelancer_id)
    assert len(llm.requests) == 1
    assert again.cv_sha256 == _sha(CV)

    # New bytes: a new card, with the new hash.
    MemberService(clean).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")
    rewritten = writer.write(freelancer_id)
    assert len(llm.requests) == 2
    assert "backend lead" in _user_text(llm.requests[1])
    assert rewritten.cv_sha256 == _sha(CV_2026)
    assert rewritten.card is not None and rewritten.card.seniority == "lead"

    # The CV goes, and the card with it, in the same commit.
    FreelancerService(clean).clear_cv(freelancer_id, _admin(clean))
    assert _stored(clean, freelancer_id) is None
    gone = writer.write(freelancer_id)
    assert len(llm.requests) == 2
    assert (gone.card, gone.cv_sha256, gone.error) == (None, None, None)


def test_failed_cv_is_not_retried(clean: Session, logs: pytest.LogCaptureFixture) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall(
        [card_response(), card_response(stop_reason="refusal"), card_response(CARD)]
    )
    writer = CardWriter(clean, llm, now=lambda: NOW)
    writer.write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")

    refused = writer.write(freelancer_id)

    # The failure is recorded with the failed CV, and the card of the CV that is gone
    # is retired with it: it must not outlive the file it describes.
    assert (refused.card, refused.cv_sha256, refused.generated_at) == (None, None, None)
    assert refused.error is not None and refused.error.strip()
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(CV_2026)
    assert (stored.card, stored.model, stored.input_tokens) == (None, None, None)
    assert str(freelancer_id) in logs.text and "refusal" in logs.text
    assert "Lovelace" not in logs.text

    # The same failed CV is not sent again...
    writer.write(freelancer_id)
    assert len(llm.requests) == 2

    # ...unless an admin asks («Rigenera scheda»).
    forced = writer.write(freelancer_id, force=True)
    assert len(llm.requests) == 3
    assert (forced.cv_sha256, forced.error) == (_sha(CV_2026), None)
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None


def test_scanned_cv_makes_no_call(clean: Session) -> None:
    freelancer_id = _apply(clean, cv=SCAN)
    llm = RecordingCall([])

    read = CardWriter(clean, llm).write(freelancer_id)

    assert llm.requests == []
    assert read.card is None and read.error == NO_TEXT == "Il CV non ha testo leggibile."
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(SCAN)
    # Not retried: the same scan answers the same without reading it again.
    assert CardWriter(clean, llm).write(freelancer_id).error == NO_TEXT


IDENTIFYING = "La scheda cita la persona o un indirizzo."


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (card_response(stop_reason="max_tokens"), "max_tokens"),
        (card_response(text="Ecco la scheda: ruolo backend"), "shape"),
        (card_response({key: value for key, value in CARD.items() if key != "luogo"}), "shape"),
        (card_response({**CARD, "nome": "Ada Lovelace"}), "shape"),
        (card_response({**CARD, "seniority": "guru"}), "shape"),
        (card_response(["non", "una", "scheda"]), "shape"),  # type: ignore[arg-type]
        # The limits the seam strips from the schema are still `Card`'s.
        (card_response({**CARD, "anni": 61}), "shape"),
        (card_response({**CARD, "competenze": [f"skill {n}" for n in range(21)]}), "shape"),
        (card_response({**CARD, "sintesi": "x" * 401}), "shape"),
        # A valid card that names the person or carries an address (spec § 2.1).
        (card_response({**CARD, "sintesi": "Il profilo di lovelace, backend."}), "identifying"),
        (card_response({**CARD, "competenze": ["Python", "www.ada.dev"]}), "identifying"),
        (card_response({**CARD, "luogo": "https://maps.example/torino"}), "identifying"),
        (card_response({**CARD, "sintesi": "Scrivete a ada@studio.it."}), "identifying"),
    ],
    ids=[
        "max_tokens",
        "not_json",
        "missing_luogo",
        "extra_key",
        "bad_seniority",
        "a_list",
        "anni_61",
        "21_competenze",
        "sintesi_401",
        "surname_lower_case",
        "www_in_competenze",
        "http_in_luogo",
        "at_in_sintesi",
    ],
)
def test_max_tokens_and_bad_json_are_errors(
    clean: Session, logs: pytest.LogCaptureFixture, answer: LlmResponse, kind: str
) -> None:
    freelancer_id = _apply(clean)
    llm = RecordingCall([answer])

    read = CardWriter(clean, llm).write(freelancer_id)

    assert len(llm.requests) == 1
    assert read.card is None and read.cv_sha256 is None and read.generated_at is None
    assert read.error is not None
    assert (read.error == IDENTIFYING) is (kind == "identifying")
    # A sentence of ours, never the model's words.
    assert "ruolo backend" not in read.error and "Ada" not in read.error
    # Recorded against the CV, so the same file is not paid for again.
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(CV)
    assert stored.input_tokens is None
    assert CardWriter(clean, llm).write(freelancer_id).error == read.error
    assert len(llm.requests) == 1
    # The log names the freelancer and the kind of failure, and nothing else.
    assert str(freelancer_id) in logs.text and kind in logs.text
    for secret in ("ruolo backend", "Lovelace", "lovelace", "Torino", "www.ada", "studio.it"):
        assert secret not in logs.text


def test_a_surname_inside_a_longer_word_is_not_the_person(clean: Session) -> None:
    """The guard reads whole words: «Neri» is the person, «ingegneria» is not."""
    freelancer_id = _apply(clean, cognome="Neri")
    sintesi = "Backend developer senior, esperienza in ingegneria del software e fintech."
    llm = RecordingCall([card_response({**CARD, "sintesi": sintesi})])

    read = CardWriter(clean, llm).write(freelancer_id)

    assert read.error is None and read.card is not None and read.card.sintesi == sintesi


def test_an_outage_on_a_new_cv_keeps_the_old_card(clean: Session) -> None:
    """Claude down says nothing about the new CV: the old card stays until an answer
    comes, and no hash parks the new file."""
    freelancer_id = _apply(clean)
    CardWriter(clean, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")

    down = CardWriter(clean, _Scripted([_down()])).write(freelancer_id)

    assert down.card == Card.model_validate(CARD) and down.cv_sha256 == _sha(CV)
    assert down.error
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None


def test_a_scan_replacing_a_cv_retires_its_card(clean: Session) -> None:
    freelancer_id = _apply(clean)
    CardWriter(clean, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, SCAN, "scansione.pdf", "application/pdf")

    read = CardWriter(clean, RecordingCall([])).write(freelancer_id)

    assert (read.card, read.cv_sha256, read.error) == (None, None, NO_TEXT)
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(SCAN)


def test_the_same_cv_refused_on_regenerate_keeps_its_card(clean: Session) -> None:
    """«Rigenera scheda» refused on the CV the card came from: that card still describes
    the file on record, so it stays, with the failure beside it."""
    freelancer_id = _apply(clean)
    llm = RecordingCall([card_response(), card_response(stop_reason="refusal")])
    CardWriter(clean, llm).write(freelancer_id)

    read = CardWriter(clean, llm).write(freelancer_id, force=True)

    assert read.card == Card.model_validate(CARD) and read.cv_sha256 == _sha(CV)
    assert read.error
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(CV)


def test_http_as_a_skill_is_not_a_link(clean: Session) -> None:
    """A link has a scheme: «HTTP/2» and «REST/HTTP» are a backend developer's skills,
    and a card listing them is not one to park until the CV changes."""
    freelancer_id = _apply(clean)
    competenze = ["Python", "HTTP/2", "REST/HTTP"]
    llm = RecordingCall([card_response({**CARD, "competenze": competenze})])

    read = CardWriter(clean, llm).write(freelancer_id)

    assert read.error is None and read.card is not None and read.card.competenze == competenze


def test_an_outage_is_tried_again(clean: Session, logs: pytest.LogCaptureFixture) -> None:
    """Claude down is not the CV's fault: the row says what happened, and no hash parks
    the CV, so the next write asks again."""
    freelancer_id = _apply(clean)
    llm = _Scripted([_down(), card_response()])

    down = CardWriter(clean, llm).write(freelancer_id)

    assert down.card is None and down.error
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None
    assert str(freelancer_id) in logs.text and "unavailable" in logs.text

    again = CardWriter(clean, llm).write(freelancer_id)
    assert len(llm.requests) == 2
    assert again.card == Card.model_validate(CARD) and again.error is None


def test_no_key_writes_nothing(clean: Session) -> None:
    freelancer_id = _apply(clean)

    read = CardWriter(clean, None).write(freelancer_id)

    assert read.card is None and read.error is None
    assert _stored(clean, freelancer_id) is None
    assert CardWriter(clean, None).refresh_stale() == CardsRefreshed(written=0, failed=0)

    # A card written before the key was removed stays exactly as it was.
    CardWriter(clean, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")
    kept = CardWriter(clean, None).write(freelancer_id, force=True)
    assert kept.cv_sha256 == _sha(CV) and kept.card == Card.model_validate(CARD)


def test_the_database_is_released_during_the_call(clean: Session) -> None:
    """Claude takes seconds: no transaction, and so no pooled connection, is held open
    across the call."""
    freelancer_id = _apply(clean)
    held: list[bool] = []
    llm = _Scripted([card_response()], during=lambda: held.append(clean.in_transaction()))

    CardWriter(clean, llm).write(freelancer_id)

    assert held == [False]


@pytest.mark.parametrize("change", ["replaced", "cleared"])
def test_a_cv_that_changes_during_the_call_gets_no_card(clean: Session, change: str) -> None:
    """The card is stored only if the CV it came from is still the freelancer's when the
    answer arrives: a CV cleared meanwhile must not get its description back, and a new
    one gets its own card on its own write."""
    freelancer_id = _apply(clean)
    admin_id = _admin(clean)

    def elsewhere() -> None:
        other = session_factory(clean.get_bind())()  # type: ignore[arg-type]
        try:
            if change == "replaced":
                MemberService(other).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "")
            else:
                FreelancerService(other).clear_cv(freelancer_id, admin_id)
        finally:
            other.close()

    llm = _Scripted([card_response()], during=elsewhere)

    read = CardWriter(clean, llm).write(freelancer_id)

    assert len(llm.requests) == 1
    assert _stored(clean, freelancer_id) is None
    assert (read.card, read.cv_sha256, read.error) == (None, None, None)


def test_clear_cv_drops_a_card_even_without_a_cv(clean: Session) -> None:
    """A card left behind by any path (a write that lost a race) goes the next time an
    admin clears the CV, whether or not the freelancer still has one."""
    freelancer_id = _apply(clean)
    CardWriter(clean, RecordingCall([card_response()])).write(freelancer_id)
    clean.execute(
        text("UPDATE freelancers SET cv_bytes = NULL, cv_filename = NULL WHERE id = :id"),
        {"id": freelancer_id},
    )
    clean.commit()
    clean.expire_all()
    assert _stored(clean, freelancer_id) is not None

    FreelancerService(clean).clear_cv(freelancer_id, _admin(clean))

    assert _stored(clean, freelancer_id) is None


def test_write_after_response_never_raises_and_logs_only_the_class(
    clean: Session, logs: pytest.LogCaptureFixture
) -> None:
    freelancer_id = _apply(clean)
    llm = _Scripted([RuntimeError("Ada Lovelace, Torino: il testo del CV")])

    write_after_response(lambda: nullcontext(clean), llm, freelancer_id)

    assert len(llm.requests) == 1
    assert str(freelancer_id) in logs.text and "RuntimeError" in logs.text
    for secret in ("Lovelace", "Torino", "testo del CV"):
        assert secret not in logs.text


def test_write_after_response_without_a_key_opens_nothing(clean: Session) -> None:
    opened: list[bool] = []

    def opener() -> nullcontext[Session]:
        opened.append(True)
        return nullcontext(clean)

    write_after_response(opener, None, _apply(clean))

    assert opened == []


def test_refresh_stale_limits_and_counts(clean: Session) -> None:
    oldest = _apply(clean, "a@studio.it", text_pdf("Primo CV, il più vecchio."))
    refused = _apply(clean, "b@studio.it", text_pdf("Secondo CV, rifiutato."))
    scanned = _apply(clean, "c@studio.it", SCAN)
    _apply(clean, "d@studio.it", cv=None)
    deleted = _apply(clean, "e@studio.it", text_pdf("Un CV di una scheda cancellata."))
    current = _apply(clean, "f@studio.it", text_pdf("Un CV con la scheda già scritta."))
    FreelancerService(clean).soft_delete(deleted, _admin(clean))
    CardWriter(clean, RecordingCall([card_response()])).write(current)

    llm = RecordingCall([card_response(), card_response(stop_reason="refusal")])
    writer = CardWriter(clean, llm)

    # Oldest first, two at a time: the first CV written, the second refused.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=1, failed=1)
    assert len(llm.requests) == 2
    assert "Primo CV" in _user_text(llm.requests[0])
    assert "Secondo CV" in _user_text(llm.requests[1])
    assert (stored := _stored(clean, oldest)) is not None and stored.card == CARD
    assert (stored := _stored(clean, refused)) is not None and stored.error is not None

    # Then the scan, a failure that makes no call; the refused CV is not tried again.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=0, failed=1)
    assert len(llm.requests) == 2
    assert (stored := _stored(clean, scanned)) is not None and stored.error == NO_TEXT

    # Nothing left: no CV, a deleted card and a current one are never picked.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=0, failed=0)


def test_refresh_stale_stops_at_an_outage(clean: Session) -> None:
    """A run of 429s must not park the backlog: the batch stops at the first outage,
    counts it as not done, records no hash for it or for anything after it, and the next
    run starts again from that CV."""
    first = _apply(clean, "a@studio.it", text_pdf("Primo CV."))
    second = _apply(clean, "b@studio.it", text_pdf("Secondo CV."))
    third = _apply(clean, "c@studio.it", text_pdf("Terzo CV."))
    llm = _Scripted([card_response(), _down()])

    assert CardWriter(clean, llm).refresh_stale(limit=3) == CardsRefreshed(written=1, failed=1)

    assert len(llm.requests) == 2
    assert (stored := _stored(clean, first)) is not None and stored.card == CARD
    assert (stored := _stored(clean, second)) is not None and stored.error_cv_sha256 is None
    assert _stored(clean, third) is None

    retry = _Scripted([card_response(), card_response()])
    assert CardWriter(clean, retry).refresh_stale(limit=3) == CardsRefreshed(written=2, failed=0)
    assert "Secondo CV" in _user_text(retry.requests[0])
    assert "Terzo CV" in _user_text(retry.requests[1])
    assert CardWriter(clean, retry).refresh_stale(limit=3) == CardsRefreshed(written=0, failed=0)
