"""The anonymous card (REB-510, spec § 2.1, § 5.1): one call to Claude per CV, stored
with the CV's hash, never paid for twice for the same file, and gone with the CV.

Claude is a `RecordingCall` answering canned cards (`fakes_cards.py`); the CVs are real
PDFs with a line of text in them, so the text goes through `FreelancerService.cv_text`
exactly as a real upload's would.
"""

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
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
)
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


def _apply(session: Session, email: str = "ada@studio.it", cv: bytes | None = CV) -> UUID:
    data = FreelancerCreate(
        nome="Ada",
        cognome="Lovelace",
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


def _keywords(node: Any) -> set[str]:
    """Every key of every schema node, property names left out."""
    if isinstance(node, list):
        return set().union(*(_keywords(item) for item in node)) if node else set()
    if not isinstance(node, dict):
        return set()
    found = set(node) - {"properties"}
    for key, value in node.items():
        if key == "properties":
            found |= set().union(*(_keywords(sub) for sub in value.values()))
        else:
            found |= _keywords(value)
    return found


def _user_text(request: LlmRequest) -> str:
    return "\n".join(block["text"] for message in request.messages for block in message["content"])


class _Unavailable:
    """The seam failing the way `AnthropicCall` does on an outage."""

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        raise LlmUnavailable("Non riesco a proporre un team adesso: riprova tra poco.")


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

    # One call, shaped for a card: the schema Claude answers in is `Card`'s, strict, and
    # carries no keyword the structured-output API refuses (the model validates them).
    [request] = llm.requests
    assert request.max_tokens == CARD_MAX_TOKENS == 2000
    assert request.schema == CARD_SCHEMA
    assert CARD_SCHEMA["additionalProperties"] is False
    assert CARD_SCHEMA["required"] == list(Card.model_fields)
    assert set(CARD_SCHEMA["properties"]) == set(Card.model_fields)
    assert {"type": "null"} in CARD_SCHEMA["properties"]["luogo"]["anyOf"]
    assert CARD_SCHEMA["properties"]["seniority"]["enum"] == ["junior", "mid", "senior", "lead"]
    unsupported = {"minLength", "maxLength", "minimum", "maximum", "maxItems"}
    assert not _keywords(CARD_SCHEMA) & unsupported
    system = request.system[0]["text"]
    assert "Italian" in system and "null" in system
    user = _user_text(request)
    assert "Backend developer" in user
    assert "Ada Lovelace, backend developer a Torino" in user

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

    # The previous card stays; the failure is written beside it, with the failed CV.
    assert refused.card == Card.model_validate(CARD)
    assert refused.cv_sha256 == _sha(CV)
    assert refused.error is not None and refused.error.strip()
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(CV_2026)
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


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (card_response(stop_reason="max_tokens"), "max_tokens"),
        (card_response(text="Ecco la scheda: ruolo backend"), "shape"),
        (card_response({key: value for key, value in CARD.items() if key != "luogo"}), "shape"),
        (card_response({**CARD, "nome": "Ada Lovelace"}), "shape"),
        (card_response({**CARD, "seniority": "guru"}), "shape"),
        (card_response(["non", "una", "scheda"]), "shape"),  # type: ignore[arg-type]
        (None, "unavailable"),
    ],
    ids=["max_tokens", "not_json", "missing_luogo", "extra_key", "bad_seniority", "a_list", "down"],
)
def test_max_tokens_and_bad_json_are_errors(
    clean: Session, logs: pytest.LogCaptureFixture, answer: LlmResponse | None, kind: str
) -> None:
    freelancer_id = _apply(clean)
    llm: RecordingCall | _Unavailable = RecordingCall([answer]) if answer else _Unavailable()

    read = CardWriter(clean, llm).write(freelancer_id)

    assert len(llm.requests) == 1
    assert read.card is None and read.cv_sha256 is None and read.generated_at is None
    assert read.error is not None
    # A sentence of ours, never the model's words.
    assert "ruolo backend" not in read.error and "Ada" not in read.error
    stored = _stored(clean, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == _sha(CV)
    assert stored.input_tokens is None
    # The log names the freelancer and the kind of failure, and nothing else.
    assert str(freelancer_id) in logs.text and kind in logs.text
    for secret in ("ruolo backend", "Lovelace", "Torino"):
        assert secret not in logs.text


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
