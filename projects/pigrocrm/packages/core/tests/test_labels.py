"""The Italian words a person reads for the persisted English codes."""

import importlib.util
from pathlib import Path
from types import ModuleType

from pigrocrm.core import labels
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


def _migration_0068() -> ModuleType:
    path = Path(__file__).parent.parent / "migrations" / "versions" / "0068_english_names.py"
    spec = importlib.util.spec_from_file_location("migration_0068_english_names", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The vocabulary of each renamed column that the core words for a person, to the map that
# holds the words. Every other column of the migration is listed in `NOT_WORDED` below.
WORDED = {
    ("tasks", "status"): labels.TASK_STATUS,
    ("tasks", "origin"): labels.TASK_ORIGIN,
    ("tasks", "rule"): labels.TASK_RULE,
    ("approvals", "channel"): labels.APPROVAL_CHANNEL,
    ("contracts", "renewal_type"): labels.RENEWAL_TYPE,
    ("contracts", "status"): labels.CONTRACT_STATUS,
    ("rate_cards", "type"): labels.RATE_CARD_TYPE,
    ("rate_cards", "unit"): labels.RATE_CARD_UNIT,
    ("rate_cards", "delivery_period"): labels.DELIVERY_PERIOD,
    ("documents", "type"): labels.DOCUMENT_TYPE,
    ("documents", "status"): labels.DOCUMENT_STATUS,
    ("templates", "type"): labels.TEMPLATE_TYPE,
    ("invoices", "type"): labels.INVOICE_TYPE,
    ("invoices", "status"): labels.INVOICE_STATUS,
    ("invoices", "payment_status"): labels.PAYMENT_STATUS,
    ("notes", "source"): labels.NOTE_SOURCE,
    ("proposals", "target_type"): labels.PROPOSAL_TARGET_TYPE,
    ("proposals", "status"): labels.PROPOSAL_STATUS,
    ("work_units", "status"): labels.WORK_UNIT_STATUS,
    ("users", "role"): labels.USER_ROLE,
    ("invitations", "role"): labels.USER_ROLE,
    ("pipeline_stages", "code"): labels.PIPELINE_STAGE_CODE,
    ("email_drafts", "send_state"): labels.SEND_STATE,
}

# Renamed columns whose words the core never prints: the web reads them from the activity
# payloads (`components/payloadLabels.ts`, checked against the migration by its own test), or
# they are a value no sentence names.
NOT_WORDED = {
    ("invoices", "imported_from"),
    ("note_revisions", "source"),
    ("proposals", "excerpt_type"),
    ("time_entries", "rate_source"),
    ("time_entries", "cost_source"),
    ("work_unit_transitions", "previous_status"),
    ("work_unit_transitions", "new_status"),
    ("cost_categories", "code"),
    ("space_settings", "key"),
    ("field_definitions", "entity_type"),
    ("reminder_preference", "type"),
    ("reminder_ack", "type"),
    ("reminder_snooze", "type"),
    ("reminder_delivery", "type"),
}


def test_every_vocabulary_the_core_words_is_the_one_the_migration_renamed() -> None:
    column_values = _migration_0068().COLUMN_VALUES
    for column, vocabulary in WORDED.items():
        renamed = {new: old for old, new in column_values[column].items()}
        assert vocabulary == renamed, column


def test_no_renamed_vocabulary_is_left_without_words_or_a_reason() -> None:
    column_values = _migration_0068().COLUMN_VALUES
    assert set(column_values) == set(WORDED) | set(NOT_WORDED)
    for column in NOT_WORDED:
        assert column not in WORDED


def test_every_renamed_activity_kind_has_its_word_or_is_a_plain_event() -> None:
    """The kinds 0068 renamed that carry a dot are in `ACTIVITY_KIND`; the three task kinds
    share their English word with other entities' kinds and are deliberately not."""
    kinds = {new: old for _, _, old, new in _migration_0068().ACTIVITY_KINDS if "." in new}
    assert kinds == labels.ACTIVITY_KIND
