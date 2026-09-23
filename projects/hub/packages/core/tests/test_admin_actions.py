"""REB-347: an admin can override, clear or delete a `Freelancer`/`Company` record,
sees who changed what and when, and can reverse any of those actions."""

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.audit import (
    AdminActionService,
    coerce_stored_value,
    field_changes,
    reject_cleared_columns,
    sanitize_payload,
)
from rebase_core.companies import CompanyService
from rebase_core.errors import NotFound, ValidationFailed
from rebase_core.freelancers import FreelancerService
from rebase_core.models import Company, Freelancer, User
from rebase_core.schemas import (
    CompanyCreate,
    CompanyOverride,
    FreelancerCreate,
    FreelancerOverride,
)

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _application(email: str = "ada@studio.it", **extra: object) -> FreelancerCreate:
    payload: dict[str, object] = {
        "nome": "Ada",
        "cognome": "Lovelace",
        "email": email,
        "tariffa_giornaliera": Decimal("450.00"),
        "posizione": "Backend developer",
        "remoto": "remoto",
        "links": ["https://github.com/ada"],
    }
    payload.update(extra)
    return FreelancerCreate(**payload)  # type: ignore[arg-type]


def _company_request(email: str = "wile@acme.it", **extra: object) -> CompanyCreate:
    payload: dict[str, object] = {
        "nome_azienda": "ACME Srl",
        "referente_nome": "Wile",
        "referente_cognome": "E.",
        "email": email,
        "progetto": "Serve un backend developer per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("500"),
    }
    payload.update(extra)
    return CompanyCreate(**payload)  # type: ignore[arg-type]


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM admin_actions"))
    hub_session.execute(text("DELETE FROM comments"))
    hub_session.execute(text("DELETE FROM freelancers"))
    hub_session.execute(text("DELETE FROM companies"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _an_admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


# ---- the diff/sanitize/coerce building blocks ----------------------------------------


def test_field_changes_is_empty_when_nothing_really_moved() -> None:
    assert field_changes({"posizione": "Dev"}, {"posizione": "Dev"}) == {}


def test_field_changes_records_only_the_keys_that_differ() -> None:
    delta = field_changes(
        {"posizione": "Dev", "note": "vecchia"}, {"posizione": "Tech lead", "note": "vecchia"}
    )
    assert delta == {
        "changed": ["posizione"],
        "before": {"posizione": "Dev"},
        "after": {"posizione": "Tech lead"},
    }


def test_field_changes_reads_a_first_time_set_as_none_to_value() -> None:
    delta = field_changes({}, {"note": "prima nota"})
    assert delta == {"changed": ["note"], "before": {"note": None}, "after": {"note": "prima nota"}}


def test_sanitize_payload_converts_what_json_cannot_carry() -> None:
    sanitized = sanitize_payload(
        {
            "before": {"tariffa_giornaliera": Decimal("450.00"), "periodo_da": date(2026, 10, 1)},
            "after": {"tariffa_giornaliera": Decimal("600.00"), "periodo_da": None},
        }
    )
    assert sanitized["before"] == {"tariffa_giornaliera": "450.00", "periodo_da": "2026-10-01"}
    assert sanitized["after"] == {"tariffa_giornaliera": "600.00", "periodo_da": None}


def test_sanitize_payload_strips_nul_and_truncates_only_past_the_widest_column() -> None:
    sanitized = sanitize_payload({"before": {"note": "a\x00b" + "x" * 4100}})
    value = sanitized["before"]["note"]
    assert "\x00" not in value
    assert value.endswith("…") and len(value) == 4001


def test_sanitize_payload_never_truncates_a_value_the_widest_column_can_hold() -> None:
    """A `note`/`progetto` at its own maximum (`PROGETTO_MAX_LENGTH`, 4000) must
    survive sanitization whole, or a revert would replace it with a shorter one."""
    full_width = "x" * 4000
    assert sanitize_payload({"before": {"note": full_width}})["before"]["note"] == full_width


def test_reject_cleared_columns_refuses_null_on_a_not_null_column() -> None:
    with pytest.raises(ValidationFailed):
        reject_cleared_columns("freelancer", Freelancer, {"stato": None})


def test_reject_cleared_columns_allows_null_on_a_nullable_column() -> None:
    reject_cleared_columns("freelancer", Freelancer, {"posizione": None})


def test_coerce_stored_value_rebuilds_decimal_and_date_from_their_stored_strings() -> None:
    assert coerce_stored_value(Freelancer, "tariffa_giornaliera", "450.00") == Decimal("450.00")
    assert coerce_stored_value(Company, "periodo_da", "2026-10-01") == date(2026, 10, 1)
    assert coerce_stored_value(Freelancer, "posizione", None) is None


# ---- overriding a freelancer's fields --------------------------------------------------


def test_override_sets_a_field_and_records_who_changed_what(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    overridden = service.override(
        row.id,
        FreelancerOverride(posizione="Tech lead", tariffa_giornaliera=Decimal("600")),
        admin_id,
    )
    assert (overridden.posizione, overridden.tariffa_giornaliera) == (
        "Tech lead",
        Decimal("600.00"),
    )

    trail = service.audit_timeline(row.id)
    assert len(trail) == 1
    entry = trail[0]
    assert (entry.kind, entry.admin_id, entry.admin_nome) == ("overridden", admin_id, "Ivan")
    assert set(entry.payload["changed"]) == {"posizione", "tariffa_giornaliera"}
    assert entry.payload["before"]["posizione"] == "Backend developer"
    assert entry.payload["after"]["posizione"] == "Tech lead"


def test_override_moves_identity_onto_the_linked_user_row(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    overridden = service.override(row.id, FreelancerOverride(nome="Grace"), admin_id)
    assert overridden.nome == "Grace"
    user = clean.get(User, clean.get(Freelancer, row.id).user_id)
    assert user is not None and user.nome == "Grace"


def test_override_clears_a_nullable_field_and_refuses_to_clear_a_required_one(
    clean: Session,
) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    cleared = service.override(row.id, FreelancerOverride(posizione=None), admin_id)
    assert cleared.posizione is None

    with pytest.raises(ValidationFailed):
        service.override(row.id, FreelancerOverride(stato=None), admin_id)


def test_override_with_no_real_change_writes_no_audit_entry(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    service.override(row.id, FreelancerOverride(posizione="Backend developer"), admin_id)
    assert service.audit_timeline(row.id) == []


def test_a_recording_failure_never_blocks_the_change_it_would_have_recorded(
    clean: Session,
) -> None:
    """`AdminActionService.record` commits on its own, after the caller's own change is
    already in the database: an admin id nobody can resolve (no such `users` row) fails
    the audit insert on the foreign key, and the field change stands anyway."""
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    ghost_admin = uuid4()

    overridden = service.override(row.id, FreelancerOverride(posizione="Tech lead"), ghost_admin)
    assert overridden.posizione == "Tech lead"
    assert clean.get(Freelancer, row.id).posizione == "Tech lead"
    assert AdminActionService(clean).timeline("freelancer", row.id) == []


def test_clear_cv_drops_the_bytes_and_never_puts_them_in_the_audit_entry(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application(), PDF, "Ada CV.pdf", "application/pdf")
    admin_id = _an_admin(clean)

    cleared = service.clear_cv(row.id, admin_id)
    assert cleared.cv_filename is None
    trail = service.audit_timeline(row.id)
    assert trail[0].kind == "cleared"
    assert trail[0].payload["before"] == {
        "cv_filename": "Ada CV.pdf",
        "cv_mime": "application/pdf",
        "cv_size": len(PDF),
    }
    assert "cv_bytes" not in str(trail[0].payload)

    # A card with no CV clears to nothing worth recording.
    service.clear_cv(row.id, admin_id)
    assert len(service.audit_timeline(row.id)) == 1


# ---- delete/restore, and the reversibility of both ------------------------------------


def test_a_deleted_freelancer_disappears_from_the_working_surfaces_until_restored(
    clean: Session,
) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    deleted = service.soft_delete(row.id, admin_id)
    assert deleted.deleted_at is not None
    assert service.list_recent().totale == 0
    with pytest.raises(NotFound):
        service.get(row.id)

    restored = service.restore(row.id, admin_id)
    assert restored.deleted_at is None
    assert service.list_recent().totale == 1

    kinds = [entry.kind for entry in service.audit_timeline(row.id)]
    assert kinds == ["restored", "deleted"]


def test_restoring_a_freelancer_that_is_not_deleted_is_a_silent_no_op(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)

    service.restore(row.id, admin_id)
    assert service.audit_timeline(row.id) == []


def test_the_audit_timeline_reads_a_deleted_freelancer_by_id(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)
    service.soft_delete(row.id, admin_id)
    assert len(service.audit_timeline(row.id)) == 1


def test_revert_restores_a_field_to_its_value_before_the_named_action(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)
    service.override(row.id, FreelancerOverride(posizione="Tech lead"), admin_id)
    action_id = service.audit_timeline(row.id)[0].id

    reverted = service.revert(row.id, action_id, admin_id)
    assert reverted.posizione == "Backend developer"
    trail = service.audit_timeline(row.id)
    assert len(trail) == 2 and trail[0].payload["after"]["posizione"] == "Backend developer"


def test_revert_refuses_on_a_delete_or_restore_entry(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)
    service.soft_delete(row.id, admin_id)
    service.restore(row.id, admin_id)
    delete_action = next(e for e in service.audit_timeline(row.id) if e.kind == "deleted")

    with pytest.raises(ValidationFailed):
        service.revert(row.id, delete_action.id, admin_id)


def test_revert_refuses_an_action_that_belongs_to_a_different_entity(clean: Session) -> None:
    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    other, _ = service.apply(_application(email="bob@studio.it"))
    admin_id = _an_admin(clean)
    service.override(other.id, FreelancerOverride(posizione="Tech lead"), admin_id)
    other_action = service.audit_timeline(other.id)[0].id

    with pytest.raises(NotFound):
        service.revert(row.id, other_action, admin_id)


# ---- companies: the same discipline, on the row and on the referente ------------------


def test_company_override_touches_the_row_and_the_referente_identity(clean: Session) -> None:
    service = CompanyService(clean)
    row = service.request(_company_request())
    admin_id = _an_admin(clean)

    overridden = service.override(
        row.id, CompanyOverride(nome="Coyote", budget_giornaliero=Decimal("650")), admin_id
    )
    assert overridden.referente.startswith("Coyote")
    assert overridden.budget_giornaliero == Decimal("650.00")
    assert len(service.audit_timeline(row.id)) == 1


def test_company_delete_restore_and_revert(clean: Session) -> None:
    service = CompanyService(clean)
    row = service.request(_company_request())
    admin_id = _an_admin(clean)

    service.override(row.id, CompanyOverride(progetto="Un progetto nuovo"), admin_id)
    action_id = service.audit_timeline(row.id)[0].id

    deleted = service.soft_delete(row.id, admin_id)
    assert deleted.deleted_at is not None
    assert service.list_recent().totale == 0
    restored = service.restore(row.id, admin_id)
    assert restored.deleted_at is None

    reverted = service.revert(row.id, action_id, admin_id)
    assert "Un progetto nuovo" not in reverted.progetto
    assert len(service.audit_timeline(row.id)) == 4


# ---- a deleted record must disappear from the member area too (Greptile, PR #280) ----


def test_a_deleted_freelancer_is_invisible_to_its_own_member_area(clean: Session) -> None:
    from rebase_core.members import MemberService

    service = FreelancerService(clean)
    row, _ = service.apply(_application())
    admin_id = _an_admin(clean)
    user_id = clean.get(Freelancer, row.id).user_id

    service.soft_delete(row.id, admin_id)

    member = MemberService(clean)
    assert member.card_for_user(user_id) is None
    with pytest.raises(NotFound):
        member.require_card(user_id)
    assert member.me_read(user_id).ha_scheda is False
    assert member.lookup("ada@studio.it").membro is False


def test_a_deleted_company_request_is_invisible_to_its_own_member_area(clean: Session) -> None:
    from rebase_core.members import MemberService

    service = CompanyService(clean)
    row = service.request(_company_request())
    admin_id = _an_admin(clean)
    user_id = clean.get(Company, row.id).user_id

    service.soft_delete(row.id, admin_id)

    member = MemberService(clean)
    assert member.company_for_user(user_id) is None
    with pytest.raises(NotFound):
        member.require_company(user_id)
    assert member.me_read(user_id).ha_azienda is False


def test_deleting_the_newest_company_request_never_exposes_an_older_one_to_self_edit(
    clean: Session,
) -> None:
    """Greptile, PR #280: an older request must stay admin-editable-only even once
    the newest one is deleted -- `company_for_user` must not silently fall back to
    it."""
    from rebase_core.members import MemberService

    service = CompanyService(clean)
    older = service.request(_company_request())
    newest = service.request(_company_request())
    admin_id = _an_admin(clean)
    user_id = clean.get(Company, older.id).user_id

    service.soft_delete(newest.id, admin_id)

    member = MemberService(clean)
    assert member.company_for_user(user_id) is None
    with pytest.raises(NotFound):
        member.require_company(user_id)

    service.restore(newest.id, admin_id)
    restored_current = member.company_for_user(user_id)
    assert restored_current is not None and restored_current.id == newest.id
