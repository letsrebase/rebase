from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.schemas import CustomerCreate, CustomerListQuery, CustomerUpdate
from pigrocrm.core.customers.service import CustomerService
from pigrocrm.core.errors import NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.fields.schemas import FieldDefinitionCreate
from pigrocrm.core.fields.service import FieldDefinitionService

ADMIN = Actor(id=None, type="system", role="admin")
COLLAB = Actor(id=None, type="user", role="collaboratore")
READONLY = Actor(id=None, type="user", role="readonly")


def test_create_requires_only_the_company_name(db_session: Session) -> None:
    customer = CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME Srl"), ADMIN)
    assert customer.ragione_sociale == "ACME Srl"
    assert customer.nazione == "IT", "Italian default, because that is the target market"
    assert customer.custom_fields == {}


def test_insert_flushes_but_does_not_commit_leaving_a_rollback_with_no_customer(
    db_session: Session,
) -> None:
    """REB-367 (design record §7 item 5): `_insert` is the write half of `create`
    without the commit, so a caller sharing this session inside a larger
    transaction -- `InvoiceService.confirm_import`, creating the matched customer
    alongside the invoice it belongs to -- can still roll back and leave neither
    behind. `_insert` alone, followed by a rollback, must leave no row; `create`
    itself (which calls `_insert` and then commits) must be unaffected."""
    service = CustomerService(db_session)
    customer = service._insert(CustomerCreate(ragione_sociale="Rolled Back Srl"), ADMIN)
    assert customer.id is not None, "flushed, so the id is already assigned"

    db_session.rollback()

    assert service.repo.get(customer.id) is None

    created = service.create(CustomerCreate(ragione_sociale="Committed Srl"), ADMIN)
    db_session.expire_all()
    assert service.repo.get(created.id) is not None


def test_fiscal_fields_are_first_class_columns(db_session: Session) -> None:
    """The previous system guessed among vat_number / vat / piva because these were external
    attributes. Here they are columns, so slice 3 can build FatturaPA on them."""
    customer = CustomerService(db_session).create(
        CustomerCreate(
            ragione_sociale="ACME Srl",
            partita_iva="12345678901",
            codice_fiscale="RSSMRA80A01H501U",
            codice_sdi="ABCDEFG",
            pec="acme@pec.it",
        ),
        ADMIN,
    )
    assert customer.partita_iva == "12345678901"
    assert customer.codice_sdi == "ABCDEFG"


def test_payment_terms_are_columns_of_the_customer(db_session: Session) -> None:
    """REB-326: «30 giorni data fattura fine mese» is a fact about Emisfera, not about
    the emitter, so it lives on the customer and `InvoiceService.issue` reads it there.
    Absent, both fall back: the days to the fiscal profile's, the end-of-month to no."""
    service = CustomerService(db_session)
    plain = service.create(CustomerCreate(ragione_sociale="Senza termini"), ADMIN)
    assert plain.giorni_pagamento is None
    assert plain.pagamento_fine_mese is False

    agreed = service.create(
        CustomerCreate(ragione_sociale="Emisfera", giorni_pagamento=30, pagamento_fine_mese=True),
        ADMIN,
    )
    assert (agreed.giorni_pagamento, agreed.pagamento_fine_mese) == (30, True)

    # Cleared with an explicit null, the days go back to the profile's; the switch is
    # NOT NULL and a null aimed at it is refused like any other column's.
    cleared = service.update(agreed.id, CustomerUpdate(giorni_pagamento=None), ADMIN)
    assert cleared.giorni_pagamento is None
    with pytest.raises(ValidationFailed) as exc:
        service.update(agreed.id, CustomerUpdate(pagamento_fine_mese=None), ADMIN)
    assert exc.value.details["field"] == "pagamento_fine_mese"


@pytest.mark.parametrize("bad", [-1, 366])
def test_payment_days_stay_within_a_year(bad: int) -> None:
    with pytest.raises(ValidationError):
        CustomerCreate(ragione_sociale="X", giorni_pagamento=bad)


@pytest.mark.parametrize("bad", ["1234567890", "123456789012", "1234567890A"])
def test_partita_iva_must_be_eleven_digits(db_session: Session, bad: str) -> None:
    with pytest.raises(ValidationFailed) as exc:
        CustomerService(db_session).create(
            CustomerCreate(ragione_sociale="X", partita_iva=bad), ADMIN
        )
    assert exc.value.details["field"] == "partita_iva"


def test_codice_sdi_must_be_seven_characters(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as exc:
        CustomerService(db_session).create(
            CustomerCreate(ragione_sociale="X", codice_sdi="ABC"), ADMIN
        )
    assert exc.value.details["field"] == "codice_sdi"


def test_partita_iva_with_a_trailing_newline_is_rejected(db_session: Session) -> None:
    """`^\\d{11}$` checked with `.match()` accepts a trailing "\\n": `$` matches just
    before a final newline, not only at the true end of the string, so
    "12345678901\\n" (12 characters -- one more than the `String(11)` column) passed
    the check and reached `flush()` as a raw, session-poisoning `DataError`. A VAT
    number pasted from a PDF or supplied by an MCP agent is not a lab-only case for a
    trailing newline. `.fullmatch()` requires the entire string to be consumed, which
    closes the gap."""
    with pytest.raises(ValidationFailed) as exc:
        CustomerService(db_session).create(
            CustomerCreate(ragione_sociale="X", partita_iva="12345678901\n"), ADMIN
        )
    assert exc.value.details["field"] == "partita_iva"


def test_codice_sdi_with_a_trailing_newline_is_rejected(db_session: Session) -> None:
    """`len(sdi) != 7` has no equivalent gap -- a trailing newline simply makes the
    string 8 characters long, regardless of anchoring. Tested for symmetry with the
    partita_iva case above, and as a regression guard for whoever changes this check
    later."""
    with pytest.raises(ValidationFailed) as exc:
        CustomerService(db_session).create(
            CustomerCreate(ragione_sociale="X", codice_sdi="ABCDEFG\n"), ADMIN
        )
    assert exc.value.details["field"] == "codice_sdi"


def test_empty_string_partita_iva_is_normalized_to_none(db_session: Session) -> None:
    """An empty string is falsy, so a bare `if piva` skipped the format check
    entirely and let "" reach storage as an empty string -- a different thing from
    "not provided" that would, for instance, wrongly satisfy a future "has a VAT
    number" filter."""
    customer = CustomerService(db_session).create(
        CustomerCreate(ragione_sociale="X", partita_iva=""), ADMIN
    )
    assert customer.partita_iva is None


def test_empty_string_codice_sdi_is_normalized_to_none(db_session: Session) -> None:
    customer = CustomerService(db_session).create(
        CustomerCreate(ragione_sociale="X", codice_sdi=""), ADMIN
    )
    assert customer.codice_sdi is None


def test_custom_fields_are_validated_against_the_definitions(db_session: Session) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="stato_cliente",
            label="Stato",
            field_type="select",
            options=["attivo", "sospeso"],
        ),
        ADMIN,
    )
    service = CustomerService(db_session)

    ok = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"stato_cliente": "attivo"}), ADMIN
    )
    assert ok.custom_fields == {"stato_cliente": "attivo"}

    with pytest.raises(ValidationFailed):
        service.create(
            CustomerCreate(ragione_sociale="B", custom_fields={"stato_cliente": "chiuso"}), ADMIN
        )


def test_undefined_custom_field_is_rejected(db_session: Session) -> None:
    with pytest.raises(ValidationFailed):
        CustomerService(db_session).create(
            CustomerCreate(ragione_sociale="X", custom_fields={"inventato": "v"}), ADMIN
        )


def test_archived_custom_field_value_survives_unrelated_updates_and_can_still_be_cleared(
    db_session: Session,
) -> None:
    """Task 7's contract for archiving a field definition is 'hide it, keep the data
    readable' (see `FieldDefinitionService.archive`'s own docstring). Validating the
    *union* of a row's stored custom_fields and the caller's incoming values against
    only the active definitions broke that contract: an archived key still sitting in
    `custom_fields` made every future update -- even one that never mentions that key
    -- fail with "campo non definito", and passing `None` for it didn't help, because
    the unknown-key check ran before any notion of removal. `update()` must validate
    only the keys the caller actually supplies, never the union with what is already
    stored."""
    fields = FieldDefinitionService(db_session)
    settore = fields.create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    customer = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(settore.id, ADMIN)

    # Updating a different, active custom field must not be blocked by the archived
    # key still sitting in custom_fields, and the archived value must survive.
    updated = service.update(customer.id, CustomerUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}

    # Explicitly clearing the archived key -- the one way left to remove an obsolete
    # value once its definition is gone -- still works.
    cleared = service.update(customer.id, CustomerUpdate(custom_fields={"settore": None}), ADMIN)
    assert cleared.custom_fields == {"priorita": "alta"}

    # An active key's own validation still runs normally: an invalid value is
    # rejected exactly as it was before this fix.
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="stato_cliente",
            label="Stato",
            field_type="select",
            options=["attivo", "sospeso"],
        ),
        ADMIN,
    )
    with pytest.raises(ValidationFailed):
        service.update(
            customer.id, CustomerUpdate(custom_fields={"stato_cliente": "chiuso"}), ADMIN
        )


def test_required_active_custom_field_set_to_none_is_rejected_like_empty_string(
    db_session: Session,
) -> None:
    """`None` and `""` are two spellings of the same intent -- "this field has no
    value" -- and must be rejected identically on a currently active, required
    field. Before this fix, `""` was rejected by `validate_custom_fields`'s own
    is_blank/required check, but `None` took a different path straight into
    `to_remove` and silently stripped the value with no error at all: the exact
    same clearing intent, spelled two ways, landing on opposite outcomes."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    customer = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"settore": "IT"}), ADMIN
    )

    with pytest.raises(ValidationFailed) as via_none:
        service.update(customer.id, CustomerUpdate(custom_fields={"settore": None}), ADMIN)
    assert via_none.value.details["field"] == "settore"
    assert via_none.value.details["reason"] == "campo obbligatorio"

    with pytest.raises(ValidationFailed) as via_empty:
        service.update(customer.id, CustomerUpdate(custom_fields={"settore": ""}), ADMIN)
    assert via_empty.value.details["reason"] == via_none.value.details["reason"]


def test_non_required_active_custom_field_set_to_none_is_removed(db_session: Session) -> None:
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    customer = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(customer.id, CustomerUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_archived_custom_field_set_to_none_is_removed_even_if_it_was_required(
    db_session: Session,
) -> None:
    """The round 1 fix deliberately unblocked clearing an archived field's stored
    value via `None`, even when the definition was required back when it was
    active. This finding's fix must not re-close that: requiredness only ever
    blocks removal for a currently active definition."""
    fields = FieldDefinitionService(db_session)
    settore = fields.create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    customer = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"settore": "IT"}), ADMIN
    )
    fields.archive(settore.id, ADMIN)

    updated = service.update(customer.id, CustomerUpdate(custom_fields={"settore": None}), ADMIN)
    assert updated.custom_fields == {}


def test_required_active_custom_field_not_mentioned_in_a_partial_update_is_unaffected(
    db_session: Session,
) -> None:
    """A required, active field the caller never mentions in this update is neither
    an omission (create-time semantics) nor a removal (a None was never sent) --
    it must be left exactly as stored, with no error."""
    fields = FieldDefinitionService(db_session)
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer",
            key="settore",
            label="Settore",
            field_type="text",
            required=True,
        ),
        ADMIN,
    )
    fields.create(
        FieldDefinitionCreate(
            entity_type="customer", key="priorita", label="Priorita", field_type="text"
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    customer = service.create(
        CustomerCreate(ragione_sociale="ACME", custom_fields={"settore": "IT"}), ADMIN
    )

    updated = service.update(customer.id, CustomerUpdate(custom_fields={"priorita": "alta"}), ADMIN)
    assert updated.custom_fields == {"settore": "IT", "priorita": "alta"}


def test_create_records_a_timeline_entry_naming_the_actor(db_session: Session) -> None:
    from pigrocrm.core.activities.service import ActivityService

    customer = CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME"), ADMIN)
    entries = ActivityService(db_session).timeline("customer", customer.id)
    assert [e.kind for e in entries] == ["created"]
    assert entries[0].actor_type == "system"


def test_update_records_only_the_changed_fields(db_session: Session) -> None:
    from pigrocrm.core.activities.service import ActivityService

    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME"), ADMIN)
    service.update(customer.id, CustomerUpdate(telefono="0212345"), ADMIN)

    updates = [
        e
        for e in ActivityService(db_session).timeline("customer", customer.id)
        if e.kind == "updated"
    ]
    assert updates[0].payload["changed"] == ["telefono"]


def test_readonly_cannot_write_but_can_read(db_session: Session) -> None:
    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME"), ADMIN)

    assert service.get(customer.id, READONLY).id == customer.id
    with pytest.raises(PermissionDenied):
        service.create(CustomerCreate(ragione_sociale="B"), READONLY)


def test_collaborator_can_write(db_session: Session) -> None:
    assert CustomerService(db_session).create(CustomerCreate(ragione_sociale="ACME"), COLLAB)


def test_soft_delete_hides_the_row_without_removing_it(db_session: Session) -> None:
    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME"), ADMIN)
    service.soft_delete(customer.id, ADMIN)

    with pytest.raises(NotFound):
        service.get(customer.id, ADMIN)
    assert service.list(CustomerListQuery(), ADMIN).items == []
    assert service.restore(customer.id, ADMIN).ragione_sociale == "ACME"


def test_restore_on_a_customer_that_was_never_deleted_does_not_log_a_restored_entry(
    db_session: Session,
) -> None:
    """`restore()` unconditionally recorded a "restored" activity, even for a customer
    that was never soft-deleted -- a misleading timeline entry claiming a recovery that
    never happened. Only log it when the customer actually was deleted."""
    from pigrocrm.core.activities.service import ActivityService

    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME"), ADMIN)

    service.restore(customer.id, ADMIN)

    kinds = [e.kind for e in ActivityService(db_session).timeline("customer", customer.id)]
    assert "restored" not in kinds


def test_soft_delete_fails_loudly_if_the_deals_table_lacks_the_expected_column(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Returning 0 from `count_active_deals` is only correct when `deals` does not
    exist yet, or exists with the expected column. If it exists without `customer_id`,
    that is a bug in this code, not "no deals" -- silently returning 0 would let
    `soft_delete` remove a customer that might still have deals attached.

    Task 12 registered the real `Deal` model, which -- by construction -- always has
    `customer_id`, so the pre-Task-12 technique this test used (registering a second,
    bare `Table("deals", Base.metadata, ...)` directly alongside the real one) no
    longer works: a `MetaData` instance rejects two tables sharing the same name with
    `InvalidRequestError`, and that collision now fires for *every* test in the suite,
    not just this one, because collecting `test_deals.py` alone -- regardless of
    execution order -- imports `pigrocrm.core.deals.models` and registers the real
    table on the shared `Base.metadata` as a side effect. Patching the module-level
    `Base` name that `count_active_deals` actually looks up
    (`pigrocrm.core.customers.repository.Base`) substitutes a throwaway, unrelated
    `MetaData` for the duration of this test only -- the real, shared `Base.metadata`
    every other test depends on is never touched, and `monkeypatch` reverts the
    substitution automatically, with no `finally` needed. Mirrors
    `test_delete_fails_loudly_if_the_deals_table_lacks_the_expected_column` in
    test_pipeline.py."""
    fake_metadata = MetaData()
    Table("deals", fake_metadata, Column("id", Integer, primary_key=True))
    monkeypatch.setattr(
        "pigrocrm.core.customers.repository.Base", SimpleNamespace(metadata=fake_metadata)
    )

    service = CustomerService(db_session)
    customer = service.create(CustomerCreate(ragione_sociale="ACME"), ADMIN)

    with pytest.raises(RuntimeError):
        service.soft_delete(customer.id, ADMIN)


def test_search_matches_name_vat_and_email(db_session: Session) -> None:
    service = CustomerService(db_session)
    service.create(
        CustomerCreate(ragione_sociale="ACME Srl", partita_iva="12345678901", email="a@acme.it"),
        ADMIN,
    )
    service.create(CustomerCreate(ragione_sociale="Beta Spa"), ADMIN)

    assert len(service.list(CustomerListQuery(search="acme"), ADMIN).items) == 1
    assert len(service.list(CustomerListQuery(search="12345678901"), ADMIN).items) == 1
    assert len(service.list(CustomerListQuery(search="a@acme.it"), ADMIN).items) == 1
    assert len(service.list(CustomerListQuery(search="zzz"), ADMIN).items) == 0


def test_search_treats_underscore_as_a_literal_character_not_a_wildcard(
    db_session: Session,
) -> None:
    """In LIKE/ILIKE, "_" means "any one character". An unescaped search term makes a
    literal underscore in the query match every row with any character in that
    position -- here, searching "a_b" would also match "axb"."""
    service = CustomerService(db_session)
    service.create(CustomerCreate(ragione_sociale="A_B Srl"), ADMIN)
    service.create(CustomerCreate(ragione_sociale="AXB Srl"), ADMIN)

    result = service.list(CustomerListQuery(search="a_b"), ADMIN)
    assert [c.ragione_sociale for c in result.items] == ["A_B Srl"]


def test_search_treats_percent_as_a_literal_character_not_a_wildcard(
    db_session: Session,
) -> None:
    """Same bug, "%" instead of "_": unescaped, it means "any run of characters", so
    searching "50%off" would also match "50XXXoff"."""
    service = CustomerService(db_session)
    service.create(CustomerCreate(ragione_sociale="50%off Srl"), ADMIN)
    service.create(CustomerCreate(ragione_sociale="50XXXoff Srl"), ADMIN)

    result = service.list(CustomerListQuery(search="50%off"), ADMIN)
    assert [c.ragione_sociale for c in result.items] == ["50%off Srl"]


def test_search_term_with_a_trailing_backslash_still_matches(db_session: Session) -> None:
    """Before escaping, a trailing backslash in the search term combines with the "%"
    this method appends to build the pattern, forming an accidental escape sequence
    that swallows the trailing wildcard -- the match disappears entirely, even though
    the target genuinely contains that backslash."""
    service = CustomerService(db_session)
    service.create(CustomerCreate(ragione_sociale="ACME\\ Srl"), ADMIN)

    result = service.list(CustomerListQuery(search="acme\\"), ADMIN)
    assert [c.ragione_sociale for c in result.items] == ["ACME\\ Srl"]


def test_filter_by_custom_field_uses_jsonb_containment(db_session: Session) -> None:
    FieldDefinitionService(db_session).create(
        FieldDefinitionCreate(
            entity_type="customer", key="settore", label="Settore", field_type="text"
        ),
        ADMIN,
    )
    service = CustomerService(db_session)
    service.create(CustomerCreate(ragione_sociale="A", custom_fields={"settore": "IT"}), ADMIN)
    service.create(CustomerCreate(ragione_sociale="B", custom_fields={"settore": "Retail"}), ADMIN)

    page = service.list(CustomerListQuery(custom={"settore": "IT"}), ADMIN)
    assert [c.ragione_sociale for c in page.items] == ["A"]


def test_pagination_returns_a_cursor_and_does_not_repeat_rows(db_session: Session) -> None:
    service = CustomerService(db_session)
    for index in range(5):
        service.create(CustomerCreate(ragione_sociale=f"Cliente {index:02d}"), ADMIN)

    first = service.list(CustomerListQuery(limit=2), ADMIN)
    assert len(first.items) == 2
    assert first.next_cursor is not None

    second = service.list(CustomerListQuery(limit=2, cursor=first.next_cursor), ADMIN)
    assert {c.id for c in first.items}.isdisjoint({c.id for c in second.items})


def test_last_page_has_no_cursor(db_session: Session) -> None:
    service = CustomerService(db_session)
    service.create(CustomerCreate(ragione_sociale="Solo"), ADMIN)
    assert service.list(CustomerListQuery(limit=10), ADMIN).next_cursor is None


def test_list_query_limit_is_bounded() -> None:
    with pytest.raises(ValidationError):
        CustomerListQuery(limit=0)
    with pytest.raises(ValidationError):
        CustomerListQuery(limit=201)


def test_get_missing_customer_raises_not_found(db_session: Session) -> None:
    from uuid import uuid4

    with pytest.raises(NotFound):
        CustomerService(db_session).get(uuid4(), ADMIN)


# --- Final review item 1 (CRITICAL): a NUL byte in a native column -----------------
#
# `ragione_sociale` (String) and `note` (Text) both had no check at all before
# `SafeStr`: `apps/api/tests/test_input_bounds_sweep.py` sweeps every string field on
# this schema over real HTTP; these two exercise the same gap directly at the schema
# layer, including the "reject, don't strip" requirement.


def test_a_nul_byte_in_ragione_sociale_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CustomerCreate(ragione_sociale="ACME\x00Srl")


def test_a_nul_byte_in_note_a_text_column_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CustomerCreate(ragione_sociale="ACME", note="riga1\x00riga2")


def test_a_valid_ragione_sociale_is_never_mutated() -> None:
    """The fix must reject, not silently strip -- a stripped NUL byte is a lost
    character nobody notices. Confirmed the other way too: an unrelated valid value
    is returned byte-for-byte unchanged."""
    customer = CustomerCreate(ragione_sociale="Città Studi Srl")
    assert customer.ragione_sociale == "Città Studi Srl"
