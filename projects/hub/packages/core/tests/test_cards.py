"""The anonymous card (REB-510, spec § 2.1, § 5.1): one call to Claude per CV, stored
with the CV's hash, never paid for twice for the same file, and gone with the CV.

Claude is a `RecordingCall` answering canned cards (`fakes_cards.py`), or `Scripted`
where a test needs an outage or something to happen during the call; the CVs are real
PDFs with a line of text in them, so the text goes through `FreelancerService.cv_text`
exactly as a real upload's would. The writer's other families are in
`test_card_gendered.py`, `test_card_outages.py` and `test_card_level_writer.py`.
"""

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime

import pytest
from admins import an_admin
from fakes_cards import CARD, MODEL, SOURCES, Scripted, card_response, text_pdf
from freelancer_fixtures import (
    CV,
    CV_2026,
    IDENTIFYING,
    SCAN,
    apply_freelancer,
    card_is_sql_null,
    card_logs,
    card_schema,
    llm_down,
    sha,
    stored_card,
    user_text,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.bands import Band
from rebase_core.card_draft import CardDraft
from rebase_core.card_prompt import card_prompt
from rebase_core.card_types import NO_TEXT
from rebase_core.card_writer import CardWriter, write_after_response
from rebase_core.cv_text import CvText
from rebase_core.errors import LlmNotConfigured
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import (
    LlmMessage,
    LlmResponse,
    RecordingCall,
)
from rebase_core.members import MemberService
from rebase_core.models import Freelancer
from rebase_core.team_schemas import Card, CardRead, CardsRefreshed

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)


@pytest.fixture
def logs(clean_hub: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    return card_logs(caplog)


def test_card_from_a_cv(clean_hub: Session) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([card_response()])

    read = CardWriter(clean_hub, llm, now=lambda: NOW).write(freelancer_id)

    assert read.freelancer_id == freelancer_id
    assert read.card == CardRead.model_validate(CARD)
    assert (read.cv_sha256, read.model, read.generated_at, read.error) == (
        sha(CV),
        MODEL,
        NOW,
        None,
    )
    assert read.mode == "remote"
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None
    assert (stored.input_tokens, stored.output_tokens) == (1200, 180)
    assert (stored.error, stored.error_cv_sha256) == (None, None)
    assert stored.card == CARD
    # What it was written from and by what (REB-839): version 3, the position as the only
    # input, the model's claim for each field, and no conflict. The named card is the
    # persona of the answer (an empty one here), tied to the CV it was read from.
    assert (stored.prompt_version, stored.provider, stored.thinking_tokens) == (3, None, 0)
    assert stored.person == {
        "first_name": None,
        "last_name": None,
        "email": None,
        "phone": None,
        "city": None,
        "linkedin_url": None,
        "other_links": [],
        "companies": [],
        "cv_sha256": sha(CV),
    }
    assert stored.sources == SOURCES
    assert stored.source_hashes == {"wizard": sha(b"Backend developer")}
    assert stored.conflicts == []

    # One call, shaped for a card: the answer is `CardDraft` (the `Card`, and the private
    # `sources` and `employers` beside it), whose schema the seam sends closed, every
    # property required, without its docstring (`test_llm.py`), and which still enforces
    # every limit on the answer (the over-limit cases below). No ceiling of its own: the
    # `card` task's is the one that applies.
    [request] = llm.requests
    assert request.output_model is CardDraft
    assert request.max_tokens is None
    assert request.system_cached is None
    system = request.system
    assert "Italian" in system and "null" in system
    user = user_text(request)
    assert "Backend developer" in user
    assert "Ada Lovelace, backend developer a Torino" in user
    # The rate never reaches Claude: the band is computed from it when a card is shown.
    assert "450" not in user and "450" not in system

    # The work mode is read from the profile when the card is shown, never stored on it.
    row = clean_hub.get(Freelancer, freelancer_id)
    assert row is not None
    row.remote = "hybrid"
    clean_hub.commit()
    assert CardWriter(clean_hub, None).read(freelancer_id).mode == "hybrid"


CV_TEXT = CvText(filename="cv.pdf", pages=1, text="Ada Lovelace", truncated=False)


def test_compose_does_not_write_the_card_row(clean_hub: Session) -> None:
    """The seam the evaluation set measures through: the card and the tokens of every
    call, and no `freelancer_cards` row, whether there was one or not."""
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([card_response(input_tokens=1000, output_tokens=150)])

    composed = CardWriter(clean_hub, llm, now=lambda: NOW).compose(freelancer_id)

    assert composed.failure is None
    assert composed.card == Card.model_validate(CARD)
    assert (composed.model, composed.input_tokens, composed.output_tokens) == (MODEL, 1000, 150)
    assert stored_card(clean_hub, freelancer_id) is None
    assert len(llm.requests) == 1


def test_compose_adds_up_the_rewrite_and_reports_a_failure_without_storing_it(
    clean_hub: Session,
) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    gendered = {**CARD, "profile_summary": "Lei è una sviluppatrice con nove anni di esperienza."}
    llm = RecordingCall(
        [
            card_response(gendered, input_tokens=1000, output_tokens=100),
            card_response(gendered, input_tokens=1100, output_tokens=120),
        ]
    )

    composed = CardWriter(clean_hub, llm, now=lambda: NOW).compose(freelancer_id)

    assert composed.card is None
    assert composed.failure == "gendered"
    assert (composed.input_tokens, composed.output_tokens) == (2100, 220)
    assert stored_card(clean_hub, freelancer_id) is None


def test_compose_says_no_text_for_a_scan_and_a_task_with_no_model_is_refused(
    clean_hub: Session,
) -> None:
    scan = apply_freelancer(clean_hub, "scan@studio.it", SCAN, "Rossi", "Mario")
    composed = CardWriter(clean_hub, RecordingCall([]), now=lambda: NOW).compose(scan)
    assert (composed.card, composed.failure, composed.model) == (None, "no_text", "")

    with pytest.raises(LlmNotConfigured):
        CardWriter(clean_hub, None).compose(scan)


def test_card_prompt_is_byte_identical_in_substance_to_before() -> None:
    """REB-831 moved the card onto the provider-neutral request: the model still reads
    the same words. The hashes and the user turn were taken from the version before it
    (`card_prompt` of 1ccb4167, run on this CV). Version 2 (REB-839) keeps them for a
    person with the CV alone: the system text is version 1's up to the version line, the
    user turn is the same bytes, and the schema of `Card` is untouched. What version 2
    adds is the version line and the description of the private answer, and nothing else.
    The hash was taken again when the schema's keys became English (`role`, `skills`, ...):
    the prompt names them, and nothing else changed."""
    cv = CvText(
        filename="cv.pdf",
        pages=1,
        text="Ada Lovelace, backend developer a Torino",
        truncated=True,
    )

    request = card_prompt(cv, "Backend developer")

    version_1, line, added = request.system.partition("\n\nPrompt version: 3\n\n")
    assert line
    assert hashlib.sha256(version_1.encode()).hexdigest() == (
        "30c5bbf87dae08f79a44f08742d340888c2305e61eed450a73c40af90468e137"
    )
    assert added.startswith("The answer has four parts")
    assert "<source" not in request.system
    assert request.messages == [
        LlmMessage(
            role="user",
            text=(
                "Position on the profile: Backend developer\n\nThe CV's text (cut short: "
                "the file is longer than what follows):\n<cv>\nAda Lovelace, backend "
                "developer a Torino\n</cv>"
            ),
        )
    ]
    schema = card_schema()
    assert hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest() == (
        "c3316bc6931793027340aeafd3c1e636604542992dd05345b71edc9752592038"
    )
    assert "description" not in schema


def test_the_read_carries_the_clients_band_from_the_rate(clean_hub: Session) -> None:
    """REB-514: the admin's talent page shows the band a company reads, from the rate on
    file when the card is shown and never stored with it: 450 € a day plus 40% is 630,
    in «500–650». No rate, no band; and a talent with no card yet still has one."""
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()]), now=lambda: NOW).write(freelancer_id)
    assert CardWriter(clean_hub, None).read(freelancer_id).band == Band(min=500, max=650)

    row = clean_hub.get(Freelancer, freelancer_id)
    assert row is not None
    row.daily_rate = None
    clean_hub.commit()
    assert CardWriter(clean_hub, None).read(freelancer_id).band is None

    without_cv = apply_freelancer(clean_hub, email="grace@studio.it", cv=None)
    read = CardWriter(clean_hub, None).read(without_cv)
    assert read.card is None
    assert read.band == Band(min=500, max=650)


def test_card_follows_the_cv(clean_hub: Session) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([card_response(), card_response({**CARD, "seniority": "lead"})])
    writer = CardWriter(clean_hub, llm, now=lambda: NOW)
    writer.write(freelancer_id)

    # The same CV again: nothing to pay for.
    again = writer.write(freelancer_id)
    assert len(llm.requests) == 1
    assert again.cv_sha256 == sha(CV)

    # New bytes: a new card, with the new hash.
    MemberService(clean_hub).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")
    rewritten = writer.write(freelancer_id)
    assert len(llm.requests) == 2
    assert "backend lead" in user_text(llm.requests[1])
    assert rewritten.cv_sha256 == sha(CV_2026)
    assert rewritten.card is not None and rewritten.card.seniority == "lead"

    # The CV goes, and the card with it, in the same commit.
    FreelancerService(clean_hub).clear_cv(freelancer_id, an_admin(clean_hub))
    assert stored_card(clean_hub, freelancer_id) is None
    gone = writer.write(freelancer_id)
    assert len(llm.requests) == 2
    assert (gone.card, gone.cv_sha256, gone.error) == (None, None, None)


def test_failed_cv_is_not_retried(clean_hub: Session, logs: pytest.LogCaptureFixture) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall(
        [card_response(), card_response(stop_reason="refusal"), card_response(CARD)]
    )
    writer = CardWriter(clean_hub, llm, now=lambda: NOW)
    writer.write(freelancer_id)
    MemberService(clean_hub).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")

    refused = writer.write(freelancer_id)

    # The failure is recorded with the failed CV, and the card of the CV that is gone
    # is retired with it: it must not outlive the file it describes.
    assert (refused.card, refused.cv_sha256, refused.generated_at) == (None, None, None)
    assert refused.error is not None and refused.error.strip()
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == sha(CV_2026)
    assert (stored.card, stored.model, stored.input_tokens) == (None, None, None)
    assert card_is_sql_null(clean_hub, freelancer_id)
    assert str(freelancer_id) in logs.text and "refusal" in logs.text
    assert "Lovelace" not in logs.text

    # The same failed CV is not sent again...
    writer.write(freelancer_id)
    assert len(llm.requests) == 2

    # ...unless an admin asks («Rigenera scheda»).
    forced = writer.write(freelancer_id, force=True)
    assert len(llm.requests) == 3
    assert (forced.cv_sha256, forced.error) == (sha(CV_2026), None)
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None


def test_scanned_cv_makes_no_call(clean_hub: Session) -> None:
    freelancer_id = apply_freelancer(clean_hub, cv=SCAN)
    llm = RecordingCall([])

    read = CardWriter(clean_hub, llm).write(freelancer_id)

    assert llm.requests == []
    assert read.card is None and read.error == NO_TEXT == "Il CV non ha testo leggibile."
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == sha(SCAN)
    # Not retried: the same scan answers the same without reading it again.
    assert CardWriter(clean_hub, llm).write(freelancer_id).error == NO_TEXT


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (card_response(stop_reason="max_tokens"), "max_tokens"),
        (card_response(text="Ecco la scheda: ruolo backend"), "shape"),
        (card_response({key: value for key, value in CARD.items() if key != "location"}), "shape"),
        (card_response({**CARD, "first_name": "Ada Lovelace"}), "shape"),
        (card_response({**CARD, "seniority": "guru"}), "shape"),
        (card_response(["non", "una", "card"]), "shape"),  # type: ignore[arg-type]
        # The limits the seam strips from the schema are still `Card`'s.
        (card_response({**CARD, "experience_years": 61}), "shape"),
        (card_response({**CARD, "skills": [f"skill {n}" for n in range(21)]}), "shape"),
        (card_response({**CARD, "profile_summary": "x" * 401}), "shape"),
        # A valid card that names the person or carries an address (spec § 2.1).
        (
            card_response({**CARD, "profile_summary": "Il profilo di Lovelace, backend."}),
            "identifying",
        ),
        (card_response({**CARD, "skills": ["Python", "www.ada.dev"]}), "identifying"),
        (card_response({**CARD, "location": "https://maps.example/torino"}), "identifying"),
        (card_response({**CARD, "profile_summary": "Scrivete a ada@studio.it."}), "identifying"),
    ],
    ids=[
        "max_tokens",
        "not_json",
        "missing_location",
        "extra_key",
        "bad_seniority",
        "a_list",
        "experience_years_61",
        "21_skills",
        "profile_summary_401",
        "surname",
        "www_in_skills",
        "http_in_location",
        "at_in_profile_summary",
    ],
)
def test_max_tokens_and_bad_json_are_errors(
    clean_hub: Session, logs: pytest.LogCaptureFixture, answer: LlmResponse, kind: str
) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([answer])

    read = CardWriter(clean_hub, llm).write(freelancer_id)

    assert len(llm.requests) == 1
    assert read.card is None and read.cv_sha256 is None and read.generated_at is None
    assert read.error is not None
    assert (read.error == IDENTIFYING) is (kind == "identifying")
    # A sentence of ours, never the model's words.
    assert "ruolo backend" not in read.error and "Ada" not in read.error
    # Recorded against the CV, so the same file is not paid for again.
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == sha(CV)
    assert stored.input_tokens is None
    assert card_is_sql_null(clean_hub, freelancer_id)
    assert CardWriter(clean_hub, llm).write(freelancer_id).error == read.error
    assert len(llm.requests) == 1
    # The log names the freelancer and the kind of failure, and nothing else.
    assert str(freelancer_id) in logs.text and kind in logs.text
    for secret in ("ruolo backend", "Lovelace", "lovelace", "Torino", "www.ada", "studio.it"):
        assert secret not in logs.text


def test_clear_cv_drops_a_card_even_without_a_cv(clean_hub: Session) -> None:
    """A card left behind by any path (a write that lost a race) goes the next time an
    admin clears the CV, whether or not the freelancer still has one."""
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    clean_hub.execute(
        text("UPDATE freelancers SET cv_bytes = NULL, cv_filename = NULL WHERE id = :id"),
        {"id": freelancer_id},
    )
    clean_hub.commit()
    clean_hub.expire_all()
    assert stored_card(clean_hub, freelancer_id) is not None

    FreelancerService(clean_hub).clear_cv(freelancer_id, an_admin(clean_hub))

    assert stored_card(clean_hub, freelancer_id) is None


def test_write_after_response_never_raises_and_logs_only_the_class(
    clean_hub: Session, logs: pytest.LogCaptureFixture
) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    llm = Scripted([RuntimeError("Ada Lovelace, Torino: il testo del CV")])

    write_after_response(lambda: nullcontext(clean_hub), llm, freelancer_id)

    assert len(llm.requests) == 1
    assert str(freelancer_id) in logs.text and "RuntimeError" in logs.text
    for secret in ("Lovelace", "Torino", "testo del CV"):
        assert secret not in logs.text


def test_write_after_response_without_a_key_opens_nothing(clean_hub: Session) -> None:
    opened: list[bool] = []

    def opener() -> nullcontext[Session]:
        opened.append(True)
        return nullcontext(clean_hub)

    write_after_response(opener, None, apply_freelancer(clean_hub))

    assert opened == []


def test_refresh_stale_limits_and_counts(clean_hub: Session) -> None:
    oldest = apply_freelancer(clean_hub, "a@studio.it", text_pdf("Primo CV, il più vecchio."))
    refused = apply_freelancer(clean_hub, "b@studio.it", text_pdf("Secondo CV, rifiutato."))
    scanned = apply_freelancer(clean_hub, "c@studio.it", SCAN)
    apply_freelancer(clean_hub, "d@studio.it", cv=None)
    deleted = apply_freelancer(
        clean_hub, "e@studio.it", text_pdf("Un CV di una scheda cancellata.")
    )
    current = apply_freelancer(
        clean_hub, "f@studio.it", text_pdf("Un CV con la scheda già scritta.")
    )
    FreelancerService(clean_hub).soft_delete(deleted, an_admin(clean_hub))
    CardWriter(clean_hub, RecordingCall([card_response()])).write(current)

    llm = RecordingCall([card_response(), card_response(stop_reason="refusal")])
    writer = CardWriter(clean_hub, llm)

    # Oldest first, two at a time: the first CV written, the second refused.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=1, failed=1)
    assert len(llm.requests) == 2
    assert "Primo CV" in user_text(llm.requests[0])
    assert "Secondo CV" in user_text(llm.requests[1])
    assert (stored := stored_card(clean_hub, oldest)) is not None and stored.card == CARD
    assert (stored := stored_card(clean_hub, refused)) is not None and stored.error is not None

    # Then the scan, a failure that makes no call; the refused CV is not tried again.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=0, failed=1)
    assert len(llm.requests) == 2
    assert (stored := stored_card(clean_hub, scanned)) is not None and stored.error == NO_TEXT

    # Nothing left: no CV, a deleted card and a current one are never picked.
    assert writer.refresh_stale(limit=2) == CardsRefreshed(written=0, failed=0)


def test_refresh_stale_skips_a_turned_down_talent(clean_hub: Session) -> None:
    """The backfill sends no CV of a person turned down (`discarded`): the catalogue
    would never show the card, so the CV would reach Anthropic for nothing."""
    turned_down = apply_freelancer(
        clean_hub, "a@studio.it", text_pdf("Il CV di chi è stato scartato.")
    )
    kept = apply_freelancer(clean_hub, "b@studio.it", text_pdf("Il CV di chi resta."))
    row = clean_hub.get(Freelancer, turned_down)
    assert row is not None
    row.status = "discarded"
    clean_hub.commit()
    llm = RecordingCall([card_response()])

    assert CardWriter(clean_hub, llm).refresh_stale() == CardsRefreshed(written=1, failed=0)

    [request] = llm.requests
    assert "chi resta" in user_text(request) and "discarded" not in user_text(request)
    assert (stored := stored_card(clean_hub, kept)) is not None and stored.card == CARD
    assert stored_card(clean_hub, turned_down) is None
    assert CardWriter(clean_hub, llm).refresh_stale() == CardsRefreshed(written=0, failed=0)


def test_refresh_stale_stops_at_an_outage(clean_hub: Session) -> None:
    """A run of 429s must not park the backlog: the batch stops at the first outage,
    counts it as not done, records no hash for it or for anything after it, and the next
    run starts again from that CV."""
    first = apply_freelancer(clean_hub, "a@studio.it", text_pdf("Primo CV."))
    second = apply_freelancer(clean_hub, "b@studio.it", text_pdf("Secondo CV."))
    third = apply_freelancer(clean_hub, "c@studio.it", text_pdf("Terzo CV."))
    llm = Scripted([card_response(), llm_down()])

    assert CardWriter(clean_hub, llm).refresh_stale(limit=3) == CardsRefreshed(
        written=1, failed=1, stopped=True
    )

    assert len(llm.requests) == 2
    assert (stored := stored_card(clean_hub, first)) is not None and stored.card == CARD
    assert (stored := stored_card(clean_hub, second)) is not None and stored.error_cv_sha256 is None
    assert stored_card(clean_hub, third) is None

    retry = Scripted([card_response(), card_response()])
    assert CardWriter(clean_hub, retry).refresh_stale(limit=3) == CardsRefreshed(
        written=2, failed=0
    )
    assert "Secondo CV" in user_text(retry.requests[0])
    assert "Terzo CV" in user_text(retry.requests[1])
    assert CardWriter(clean_hub, retry).refresh_stale(limit=3) == CardsRefreshed(
        written=0, failed=0
    )
