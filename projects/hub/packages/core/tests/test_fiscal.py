"""A freelancer's tax data: saved once, reused, audited by name only (REB-387)."""

import threading
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import FiscalData
from rebase_core.db import session_factory
from rebase_core.errors import NotFound
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.models import FreelancerFiscal, User
from rebase_core.schemas import FreelancerCreate

PDF = b"%PDF-1.7\n1 0 obj<<>>endobj\n%%EOF\n"
MISSING = UUID("00000000-0000-7000-8000-000000000000")


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in ("admin_actions", "freelancer_fiscal", "freelancers", "users"):
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _admin(session: Session) -> UUID:
    admin = User(email="ivan@rebase.it", nome="Ivan", cognome="", role="admin")
    session.add(admin)
    session.commit()
    return admin.id


def _card(session: Session) -> UUID:
    row, _ = FreelancerService(session).apply(
        FreelancerCreate(
            nome="Ada",
            cognome="Lovelace",
            email="ada@studio.it",
            tariffa_giornaliera=Decimal("450"),
            posizione="Backend developer",
            remoto="remoto",
        ),
        PDF,
        "cv.pdf",
        "application/pdf",
    )
    return row.id


def _data(**change: str | None) -> FiscalData:
    payload: dict[str, str | None] = {
        "codice_fiscale": "LVLDAA85T50H501Z",
        "partita_iva": "01234567890",
        "domicilio": "Via Roma 1, Milano",
        "pec": None,
    }
    payload.update(change)
    return FiscalData(**payload)  # type: ignore[arg-type]


def test_tax_data_are_saved_once_and_updated_in_place(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    fiscal = FiscalService(clean)
    assert fiscal.get(card) is None
    saved = fiscal.save(card, _data(), admin_id)
    assert (saved.codice_fiscale, saved.partita_iva, saved.updated_by) == (
        "LVLDAA85T50H501Z",
        "01234567890",
        admin_id,
    )
    fiscal.save(card, _data(domicilio="Corso Buenos Aires 2, Milano", pec="ada@pec.it"), admin_id)
    read = fiscal.get(card)
    assert read is not None
    assert (read.domicilio, read.pec) == ("Corso Buenos Aires 2, Milano", "ada@pec.it")
    assert clean.scalar(select(func.count()).select_from(FreelancerFiscal)) == 1


def test_the_audit_names_the_fields_that_changed_and_never_their_values(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    fiscal = FiscalService(clean)
    fiscal.save(card, _data(), admin_id)
    fiscal.save(card, _data(partita_iva="09876543210"), admin_id)
    fiscal.save(card, _data(partita_iva="09876543210"), admin_id)  # nothing moved
    trail = AdminActionService(clean).timeline("freelancer_fiscal", card)
    assert [action.kind for action in trail] == ["fiscal_updated", "fiscal_updated"]
    assert trail[0].payload == {"changed": ["partita_iva"]}
    assert trail[1].payload == {"changed": ["codice_fiscale", "partita_iva", "domicilio"]}
    assert "09876543210" not in str([action.payload for action in trail])


def test_an_unknown_or_deleted_card_has_no_tax_data(clean: Session) -> None:
    admin_id, card = _admin(clean), _card(clean)
    with pytest.raises(NotFound):
        FiscalService(clean).get(MISSING)
    FreelancerService(clean).soft_delete(card, admin_id)
    with pytest.raises(NotFound):
        FiscalService(clean).save(card, _data(), admin_id)


def test_two_admins_saving_one_cards_tax_data_at_once_run_one_after_the_other(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """Greptile 4092036024: two real `save()` calls, on two sessions, for one card -- the
    shape of `test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_
    frameworks`. Both admins opened the form on the same saved data; the first changes
    the address, the second the VAT number. The first is paused right after it has read
    the row it will diff its audit against. Without the lock the second reads the same
    old row, writes and audits `partita_iva` alone, and the first then writes its stale
    VAT number back over it and audits `domicilio` alone: a change nobody recorded. With
    the freelancer's row locked first, the second waits, then reads the first's committed
    row, so its audit names both fields it really moved."""
    admin_id, card = _admin(clean), _card(clean)
    FiscalService(clean).save(card, _data(), admin_id)
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    paused = threading.Event()
    release = threading.Event()
    real_row = FiscalService._row

    def paced_row(self: FiscalService, freelancer_id: UUID) -> FreelancerFiscal | None:
        row = real_row(self, freelancer_id)
        if self.session is first:
            paused.set()
            assert release.wait(timeout=5), "the test never released the first save()"
        return row

    monkeypatch.setattr(FiscalService, "_row", paced_row)
    errors: list[BaseException] = []

    def run(session: Session, data: FiscalData) -> None:
        try:
            FiscalService(session).save(card, data, admin_id)
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    try:
        first_worker = threading.Thread(
            target=run, args=(first, _data(domicilio="Corso Buenos Aires 2, Milano"))
        )
        first_worker.start()
        assert paused.wait(timeout=5), "the first save() never read the row"

        second_worker = threading.Thread(
            target=run, args=(second, _data(partita_iva="09876543210"))
        )
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), (
            "the second save() read the tax data while the first, which had already "
            "read them, had not committed yet"
        )

        release.set()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive()
        assert not second_worker.is_alive()
        assert not errors, errors

        saved = FiscalService(clean).get(card)
        assert saved is not None
        assert (saved.partita_iva, saved.domicilio) == ("09876543210", "Via Roma 1, Milano")
        trail = AdminActionService(clean).timeline("freelancer_fiscal", card)
        assert len(trail) == 3
        assert sorted(action.payload["changed"] for action in trail[:2]) == [
            ["domicilio"],
            ["partita_iva", "domicilio"],
        ]
    finally:
        first.close()
        second.close()
