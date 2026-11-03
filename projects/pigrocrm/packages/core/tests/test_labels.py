"""The Italian words a person reads for the persisted English codes."""

import pytest

from pigrocrm.core.cli.cron import _print_digest_outcome
from pigrocrm.core.digest.run import (
    OWNER_DISABLED,
    OWNER_MISSING,
    SEND_NOT_CONFIGURED,
    SEND_REJECTED,
    DigestOutcome,
)
from pigrocrm.core.gmail.send import _already
from pigrocrm.core.labels import (
    ACTIVITY_KIND,
    INVOICE_STATUS,
    NOTE_SOURCE,
    SEND_STATE,
    WORK_UNIT_STATUS,
    label,
)


def test_a_code_reads_as_the_word_it_had_before_the_rename() -> None:
    assert label(WORK_UNIT_STATUS, "worked_without_approval") == "lavorato_senza_approvazione"
    assert label(INVOICE_STATUS, "issued") == "emessa"
    assert label(SEND_STATE, "sending") == "in_invio"


def test_an_unknown_code_or_none_does_not_raise() -> None:
    assert label(INVOICE_STATUS, "unheard_of") == "unheard_of"
    assert label(INVOICE_STATUS, None) == ""


def test_a_refused_send_names_the_state_in_italian() -> None:
    assert _already("failed").endswith("nello stato fallito")


def test_a_note_source_and_an_event_kind_read_as_they_did() -> None:
    assert label(NOTE_SOURCE, "system") == "sistema"
    assert label(NOTE_SOURCE, "mcp") == "mcp"
    assert label(ACTIVITY_KIND, "gmail.draft_created") == "gmail.bozza_creata"
    assert label(ACTIVITY_KIND, "digest.sent") == "digest.inviato"
    assert label(ACTIVITY_KIND, "created") == "created"


@pytest.mark.parametrize(
    ("reason", "word"),
    [
        (OWNER_MISSING, "titolare_mancante"),
        (OWNER_DISABLED, "titolare_disattivato"),
        (SEND_REJECTED, "invio_rifiutato"),
        (SEND_NOT_CONFIGURED, "invio_non_configurato"),
        ("OperationalError", "OperationalError"),
    ],
)
def test_the_crons_skipped_line_names_the_reason_in_italian(
    capsys: pytest.CaptureFixture[str], reason: str, word: str
) -> None:
    outcome = DigestOutcome(slug="studio", outcome="skipped", week="2026-W44", reason=reason)

    _print_digest_outcome(outcome, dry_run=False)

    assert capsys.readouterr().err == f"studio: saltato ({word})\n"
