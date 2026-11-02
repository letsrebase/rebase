"""A card's write when the model or the CV cannot give one: an outage, a refused call, a
scan, a key that is missing, a CV that changes during the call. What is kept and what is
retired, and what the next run asks again.
"""

from datetime import UTC, datetime

import pytest
from admins import an_admin
from fakes_cards import CARD, Scripted, card_response, text_pdf
from freelancer_fixtures import (
    CV,
    CV_2026,
    SCAN,
    SVILUPPATRICE,
    apply_freelancer,
    card_is_sql_null,
    card_logs,
    llm_down,
    sha,
    stored_card,
    user_text,
)
from sqlalchemy.orm import Session

from rebase_core.card_types import NO_TEXT
from rebase_core.card_writer import CardWriter
from rebase_core.db import session_factory
from rebase_core.errors import LlmCapReached, LlmNotConfigured
from rebase_core.freelancers import FreelancerService
from rebase_core.llm import (
    LlmUnavailable,
    RecordingCall,
)
from rebase_core.members import MemberService
from rebase_core.team_schemas import CardRead, CardsRefreshed

NOW = datetime(2026, 9, 26, 9, 30, tzinfo=UTC)


@pytest.fixture
def logs(clean_hub: Session, caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    return card_logs(caplog)


def test_an_outage_on_the_rewrite_writes_nothing_and_stops_the_batch(clean_hub: Session) -> None:
    """A 429 on the rewrite: the first card is still gendered, so nothing is written and
    no hash is kept, and the outage stops the batch there, as one on a first call does,
    rather than paying a first call for every CV after it."""
    first = apply_freelancer(clean_hub, "a@studio.it", text_pdf("Primo CV."))
    second = apply_freelancer(clean_hub, "b@studio.it", text_pdf("Secondo CV."))
    llm = Scripted([card_response(SVILUPPATRICE), llm_down(), card_response()])

    result = CardWriter(clean_hub, llm).refresh_stale(limit=2)

    assert result == CardsRefreshed(written=0, failed=1, stopped=True)
    assert len(llm.requests) == 2
    assert "Primo CV" in user_text(llm.requests[1])
    stored = stored_card(clean_hub, first)
    assert stored is not None and stored.card is None and stored.error_cv_sha256 is None
    assert stored_card(clean_hub, second) is None

    # The next run starts from the first CV again, and the one after it.
    again = Scripted([card_response(), card_response()])
    assert CardWriter(clean_hub, again).refresh_stale(limit=2) == CardsRefreshed(
        written=2, failed=0
    )
    assert "Primo CV" in user_text(again.requests[0])


def test_an_outage_on_a_new_cv_retires_the_old_card(clean_hub: Session) -> None:
    """Claude down says nothing about the new CV, so no hash parks it; but the old
    card describes a CV the person replaced, so it leaves the catalogue until the next
    answer, which writes the new CV's card."""
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean_hub).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")

    down = CardWriter(clean_hub, Scripted([llm_down()])).write(freelancer_id)

    assert (down.card, down.cv_sha256) == (None, None) and down.error
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None
    assert card_is_sql_null(clean_hub, freelancer_id)

    again = CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    assert again.card == CardRead.model_validate(CARD) and again.cv_sha256 == sha(CV_2026)
    assert again.error is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (LlmCapReached("Tetto giornaliero raggiunto"), "Tetto giornaliero raggiunto"),
        (LlmNotConfigured("Manca la chiave del fornitore"), "Manca la chiave del fornitore"),
    ],
    ids=["cap", "not configured"],
)
def test_a_call_refused_for_the_cap_or_the_config_says_why_and_retires_nothing(
    clean_hub: Session, error: LlmUnavailable, reason: str
) -> None:
    """Review of #666, minor 6: not «Claude non ha risposto», and not an outage's side
    effect: a new CV's previous card stays until a model answers, the sentence is the
    reason «Modelli AI» shows, the CV is not parked, and the batch stops."""
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean_hub).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")

    refused = CardWriter(clean_hub, Scripted([error])).write(freelancer_id)

    assert refused.error == reason
    assert refused.card == CardRead.model_validate(CARD)  # nothing was retired
    assert not card_is_sql_null(clean_hub, freelancer_id)
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None

    assert CardWriter(clean_hub, Scripted([error])).refresh_stale() == CardsRefreshed(
        written=0, failed=1, stopped=True
    )
    again = CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    assert again.error is None and again.cv_sha256 == sha(CV_2026)


def test_an_outage_on_the_same_cv_keeps_its_card(clean_hub: Session) -> None:
    """«Rigenera scheda» while Claude is down: the card still describes the CV on
    record, so it stays, with the outage beside it."""
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)

    down = CardWriter(clean_hub, Scripted([llm_down()])).write(freelancer_id, force=True)

    assert down.card == CardRead.model_validate(CARD) and down.cv_sha256 == sha(CV)
    assert down.error


def test_a_scan_replacing_a_cv_retires_its_card(clean_hub: Session) -> None:
    freelancer_id = apply_freelancer(clean_hub)
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean_hub).replace_cv(freelancer_id, SCAN, "scansione.pdf", "application/pdf")

    read = CardWriter(clean_hub, RecordingCall([])).write(freelancer_id)

    assert (read.card, read.cv_sha256, read.error) == (None, None, NO_TEXT)
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == sha(SCAN)
    assert card_is_sql_null(clean_hub, freelancer_id)


def test_the_same_cv_refused_on_regenerate_keeps_its_card(clean_hub: Session) -> None:
    """«Rigenera scheda» refused on the CV the card came from: that card still describes
    the file on record, so it stays, with the failure beside it."""
    freelancer_id = apply_freelancer(clean_hub)
    llm = RecordingCall([card_response(), card_response(stop_reason="refusal")])
    CardWriter(clean_hub, llm).write(freelancer_id)

    read = CardWriter(clean_hub, llm).write(freelancer_id, force=True)

    assert read.card == CardRead.model_validate(CARD) and read.cv_sha256 == sha(CV)
    assert read.error
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 == sha(CV)


def test_an_outage_is_tried_again(clean_hub: Session, logs: pytest.LogCaptureFixture) -> None:
    """Claude down is not the CV's fault: the row says what happened, and no hash parks
    the CV, so the next write asks again."""
    freelancer_id = apply_freelancer(clean_hub)
    llm = Scripted([llm_down(), card_response()])

    down = CardWriter(clean_hub, llm).write(freelancer_id)

    assert down.card is None and down.error
    stored = stored_card(clean_hub, freelancer_id)
    assert stored is not None and stored.error_cv_sha256 is None
    assert str(freelancer_id) in logs.text and "unavailable" in logs.text

    again = CardWriter(clean_hub, llm).write(freelancer_id)
    assert len(llm.requests) == 2
    assert again.card == CardRead.model_validate(CARD) and again.error is None


def test_no_key_writes_nothing(clean_hub: Session) -> None:
    freelancer_id = apply_freelancer(clean_hub)

    read = CardWriter(clean_hub, None).write(freelancer_id)

    assert read.card is None and read.error is None
    assert stored_card(clean_hub, freelancer_id) is None
    assert CardWriter(clean_hub, None).refresh_stale() == CardsRefreshed(written=0, failed=0)

    # A card written before the key was removed stays exactly as it was.
    CardWriter(clean_hub, RecordingCall([card_response()])).write(freelancer_id)
    MemberService(clean_hub).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "application/pdf")
    kept = CardWriter(clean_hub, None).write(freelancer_id, force=True)
    assert kept.cv_sha256 == sha(CV) and kept.card == CardRead.model_validate(CARD)


def test_the_database_is_released_during_the_call(clean_hub: Session) -> None:
    """Claude takes seconds: no transaction, and so no pooled connection, is held open
    across the call."""
    freelancer_id = apply_freelancer(clean_hub)
    held: list[bool] = []
    llm = Scripted([card_response()], during=lambda: held.append(clean_hub.in_transaction()))

    CardWriter(clean_hub, llm).write(freelancer_id)

    assert held == [False]


@pytest.mark.parametrize("change", ["replaced", "cleared"])
def test_a_cv_that_changes_during_the_call_gets_no_card(clean_hub: Session, change: str) -> None:
    """The card is stored only if the CV it came from is still the freelancer's when the
    answer arrives: a CV cleared meanwhile must not get its description back, and a new
    one gets its own card on its own write."""
    freelancer_id = apply_freelancer(clean_hub)
    admin_id = an_admin(clean_hub)

    def elsewhere() -> None:
        other = session_factory(clean_hub.get_bind())()  # type: ignore[arg-type]
        try:
            if change == "replaced":
                MemberService(other).replace_cv(freelancer_id, CV_2026, "cv 2026.pdf", "")
            else:
                FreelancerService(other).clear_cv(freelancer_id, admin_id)
        finally:
            other.close()

    llm = Scripted([card_response()], during=elsewhere)

    read = CardWriter(clean_hub, llm).write(freelancer_id)

    assert len(llm.requests) == 1
    assert stored_card(clean_hub, freelancer_id) is None
    assert (read.card, read.cv_sha256, read.error) == (None, None, None)
