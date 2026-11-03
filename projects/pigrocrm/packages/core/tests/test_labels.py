"""The Italian words a person reads for the persisted English codes."""

from pigrocrm.core.gmail.send import _already
from pigrocrm.core.labels import (
    INVOICE_STATUS,
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
