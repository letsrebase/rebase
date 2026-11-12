"""REB-1076: due items grouped into an F24 and settled by one movement (taxes design § 2 and § 5).
REB-1093: a group that nets to zero or to a credit is settled by a person filing it.

What is proved here: an F24 that holds a credit is settled by the one movement equal to its net,
and undoing the match reopens it; each line carries the code the pack gives it, and a line the pack
has no code for reads «codice da verificare»; ungrouping frees the items and a grouped item is not
touched by anything else; a tax forecast becomes the item that is paid when it is grouped; and who
may group, read and ungroup. A group of nothing to pay is filed, not matched: it settles its lines
and spends its credits as a match does, a net credit leaves the rest of its credits open for another
F24, and who may file and reopen one.
"""

import datetime as dt
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fakes.cash_fixtures import ADMIN, AGENT, AGENT_FULL, COLLABORATOR, READONLY, CashWorld
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_bank_matching import _movement

from pigrocrm.core.bank.match_schemas import (
    MatchConfirm,
    MatchProposalCreate,
    MatchSelection,
    MatchUndo,
)
from pigrocrm.core.bank.matching import MovementMatchService
from pigrocrm.core.bank.models import BankMovement
from pigrocrm.core.db import today_local
from pigrocrm.core.due_items.models import DueItem, DueItemSettlement
from pigrocrm.core.due_items.schemas import DueItemCreate, SettlementCreate
from pigrocrm.core.due_items.service import DueItemService
from pigrocrm.core.errors import (
    AgentForbidden,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from pigrocrm.core.fiscal.f24 import F24GroupService
from pigrocrm.core.fiscal.f24_models import F24Group, F24GroupItem, TaxCredit
from pigrocrm.core.fiscal.f24_schemas import (
    CODE_TO_VERIFY,
    F24GroupCreate,
    F24GroupFile,
    F24GroupListQuery,
    F24GroupRead,
)
from pigrocrm.core.fiscal.models import FiscalProfile
from pigrocrm.core.fiscal.tax_items import slot_id
from pigrocrm.core.inbox.models import InboxItem
from pigrocrm.core.legal_entities.models import LegalEntity

D = Decimal
TODAY = today_local()
DUE = TODAY + dt.timedelta(days=10)


def _entity(session: Session, pack_id: str = "it-standard") -> UUID:
    entity = LegalEntity(name=f"f24 {uuid4().hex[:6]}", legal_name="F24 srl", country="IT")
    session.add(entity)
    session.flush()
    session.add(
        FiscalProfile(
            legal_entity_id=entity.id,
            pack_id=pack_id,
            regime_code="RF19" if pack_id == "it-flat-rate" else "RF01",
            default_vat_rate=D("0.00") if pack_id == "it-flat-rate" else D("22.00"),
            profitability_coefficient=D("78.00") if pack_id == "it-flat-rate" else None,
            substitute_tax_rate=D("5.00") if pack_id == "it-flat-rate" else None,
        )
    )
    session.flush()
    return entity.id


def _item(
    session: Session,
    entity: UUID,
    amount: str,
    *,
    kind: str,
    tax: str,
    source_id: UUID | None = None,
    due: dt.date = DUE,
    state: str = "expected",
    description: str | None = None,
) -> UUID:
    values: dict[str, object] = {
        "legal_entity_id": entity,
        "direction": "out",
        "counterparty_type": "tax_office",
        "tax": tax,
        "amount": D(amount),
        "description": description,
        "state": state,
        "source_kind": kind,
        "source_id": source_id or uuid4(),
        "own_date": due,
    }
    if state == "forecast":
        values.update(source_rule=f"{kind}:test", forecast_key=values["source_id"])
    return DueItemService(session).create(DueItemCreate(**values)).id  # type: ignore[arg-type]


def _tax(
    session: Session,
    entity: UUID,
    tax: str,
    year: int,
    part: str,
    amount: str,
    *,
    due: dt.date = DUE,
    state: str = "expected",
) -> UUID:
    """A balance or an advance as the tax writer makes it: its slot is its `source_id`."""
    kind = "tax_balance" if part == "balance" else "tax_advance"
    return _item(
        session,
        entity,
        amount,
        kind=kind,
        tax=tax,
        source_id=slot_id(kind, entity, tax, year, part),  # type: ignore[arg-type]
        due=due,
        state=state,
        description=f"{tax} {year} {part}",
    )


def _credit(
    session: Session, entity: UUID, amount: str, *, year: int = 2025, due: dt.date = DUE
) -> UUID:
    """A withholding credit and the negative item that points at it."""
    credit = TaxCredit(
        legal_entity_id=entity,
        origin="withholding_suffered",
        tax="irpef",
        tax_year=year,
        amount=D(amount),
    )
    session.add(credit)
    session.flush()
    return _item(
        session,
        entity,
        f"-{amount}",
        kind="tax_credit",
        tax="irpef",
        source_id=credit.id,
        due=due,
        description=f"Ritenute subite {year}",
    )


def _plain(session: Session, entity: UUID, amount: str, *, due: dt.date = DUE) -> UUID:
    """A tax item that no recompute rewrites (an instalment), for what ungroups it."""
    return _item(session, entity, amount, kind="tax_instalment", tax="irpef", due=due)


def _group(session: Session, ids: list[UUID], actor=ADMIN) -> F24GroupRead:  # noqa: ANN001
    return F24GroupService(session).group_f24(F24GroupCreate(due_item_ids=ids), actor)


def _by_item(group: F24GroupRead) -> dict[UUID, object]:
    return {line.due_item_id: line for line in group.lines}


# --- the acceptance: an F24 with a credit, settled by one movement -------------------------------


def test_an_f24_with_a_credit_is_settled_by_one_movement_and_undone_with_it(
    db_session: Session,
) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    balance = _tax(db_session, entity, "irpef", 2025, "balance", "1000.00")
    advance = _tax(db_session, entity, "irpef", 2026, "first_advance", "300.00")
    credit = _credit(db_session, entity, "200.00")
    ids = [balance, advance, credit]

    group = _group(db_session, ids)

    assert (group.state, group.payment_date) == ("open", DUE)
    assert (group.debit_total, group.credit_total, group.total) == (
        D("1300.00"),
        D("200.00"),
        D("1100.00"),
    )
    lines = _by_item(group)
    assert (lines[balance].code, lines[balance].line, lines[balance].reference_year) == (  # type: ignore[attr-defined]
        "4001",
        "irpef_balance",
        2025,
    )
    assert (lines[advance].code, lines[advance].reference_year) == ("4033", 2026)  # type: ignore[attr-defined]
    assert lines[credit].is_credit and lines[credit].credit_origin.origin == "withholding_suffered"  # type: ignore[attr-defined]

    # The one movement equal to the net: the group is the candidate, line by line with the credit
    # taken off.
    movement = _movement(db_session, account, "-1100.00")
    service = MovementMatchService(db_session)
    (candidate, *_) = service.get_movement(movement.id, ADMIN).candidates
    assert {item.due_item_id for item in candidate.items} == set(ids)
    assert {item.due_item_id for item in candidate.items if item.credit} == {credit}
    assert (candidate.allocated, candidate.difference, candidate.reasons) == (
        D("1100.00"),
        D("0.00"),
        ["items_total"],
    )

    preview = service.preview(movement.id, MatchSelection(due_item_ids=ids), ADMIN)
    assert (preview.allocated, preview.difference, preview.can_confirm) == (
        D("1100.00"),
        D("0.00"),
        True,
    )
    service.confirm(movement.id, MatchConfirm(due_item_ids=ids), ADMIN)

    db_session.expire_all()
    assert db_session.get(BankMovement, movement.id).state == "matched"  # type: ignore[union-attr]
    settled = F24GroupService(db_session).get_f24_group(group.id, ADMIN)
    assert (settled.state, settled.bank_movement_id) == ("settled", movement.id)
    amounts = {
        row.due_item_id: row.amount
        for row in db_session.scalars(
            select(DueItemSettlement).where(DueItemSettlement.due_item_id.in_(ids))
        )
    }
    assert amounts == {balance: D("1000.00"), advance: D("300.00"), credit: D("-200.00")}
    assert {db_session.get(DueItem, i).state for i in ids} == {"settled"}  # type: ignore[union-attr]
    # Settled, the group cannot be ungrouped; it is reopened by undoing the match.
    with pytest.raises(Conflict):
        F24GroupService(db_session).ungroup_f24(group.id, ADMIN)

    service.undo(movement.id, MatchUndo(), ADMIN)
    db_session.expire_all()
    reopened = F24GroupService(db_session).get_f24_group(group.id, ADMIN)
    assert (reopened.state, reopened.bank_movement_id, reopened.total) == (
        "open",
        None,
        D("1100.00"),
    )
    assert {db_session.get(DueItem, i).state for i in ids} == {"expected"}  # type: ignore[union-attr]


def test_a_movement_that_is_not_the_net_does_not_settle_the_group(db_session: Session) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    ids = [
        _tax(db_session, entity, "irpef", 2025, "balance", "1000.00"),
        _credit(db_session, entity, "200.00"),
    ]
    _group(db_session, ids)
    # The gross, as if the credit were not in it.
    movement = _movement(db_session, account, "-1000.00")
    service = MovementMatchService(db_session)

    assert service.get_movement(movement.id, ADMIN).candidates == []
    with pytest.raises(ValidationFailed, match="mancano|superano"):
        service.confirm(movement.id, MatchConfirm(due_item_ids=ids), ADMIN)
    # Money in does not pay an F24.
    incoming = _movement(db_session, account, "800.00")
    with pytest.raises(ValidationFailed):
        service.confirm(incoming.id, MatchConfirm(due_item_ids=ids), ADMIN)


def test_a_line_of_a_group_is_matched_with_the_whole_group_only(db_session: Session) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    balance = _tax(db_session, entity, "irpef", 2025, "balance", "1000.00")
    advance = _tax(db_session, entity, "irpef", 2026, "first_advance", "300.00")
    _group(db_session, [balance, advance])
    movement = _movement(db_session, account, "-1000.00")
    service = MovementMatchService(db_session)

    with pytest.raises(ValidationFailed, match="tutto il suo gruppo"):
        service.confirm(movement.id, MatchConfirm(due_item_ids=[balance]), ADMIN)
    # And it is not offered on its own: the open items leave a grouped line out.
    assert service.list_open_items(movement.id, ADMIN) == []


# --- the codes ------------------------------------------------------------------------------


def test_each_line_carries_the_pack_code_and_a_line_without_one_says_to_verify(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-flat-rate")
    balance = _tax(db_session, entity, "substitute_tax", 2025, "balance", "100.00")
    first = _tax(db_session, entity, "substitute_tax", 2026, "first_advance", "40.00")
    second = _tax(db_session, entity, "substitute_tax", 2026, "second_advance", "40.00")
    iva = _item(db_session, entity, "50.00", kind="iva_settlement", tax="iva")
    inail = _item(db_session, entity, "30.00", kind="payroll", tax="payroll_inail")

    lines = _by_item(_group(db_session, [balance, first, second, iva, inail]))

    assert [lines[i].code for i in (balance, first, second)] == ["1792", "1790", "1791"]  # type: ignore[attr-defined]
    assert {lines[i].code_kind for i in (balance, first, second)} == {"tax_code"}  # type: ignore[attr-defined]
    assert "forfetario" in lines[balance].code_label  # type: ignore[attr-defined]
    # An IVA item that names no settlement, and INAIL's premium (no source read gives its F24
    # fields), have no code: none is invented.
    for item in (iva, inail):
        line = lines[item]
        assert (line.code, line.line, line.code_kind) == (None, None, None)  # type: ignore[attr-defined]
        assert (line.code_label, line.code_to_verify) == (CODE_TO_VERIFY, True)  # type: ignore[attr-defined]
    assert lines[balance].code_to_verify is False  # type: ignore[attr-defined]


def test_a_company_pays_the_chamber_fee_on_its_own_code(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    fee = _item(db_session, entity, "120.00", kind="tax_instalment", tax="chamber_fee")
    ires = _tax(db_session, entity, "ires", 2026, "first_advance", "500.00")

    lines = _by_item(_group(db_session, [fee, ires]))

    assert lines[fee].code == "3850"  # type: ignore[attr-defined]
    assert lines[ires].code == "2001"  # type: ignore[attr-defined]


def test_an_f24_says_to_tick_the_box_when_the_fiscal_year_is_not_the_calendar_year(
    db_session: Session,
) -> None:
    """The F24's header box «non coincidente con anno solare» (Agenzia, «Avvertenze per la
    compilazione del mod. F24»): a legal entity whose year ends on 30 June has it, one whose year
    ends on 31 December does not."""
    broken = _entity(db_session, "it-company")
    db_session.query(LegalEntity).filter_by(id=broken).update(
        {"fiscal_year_end_month": 6, "fiscal_year_end_day": 30}
    )
    calendar = _entity(db_session, "it-company")
    for entity, expected in ((broken, True), (calendar, False)):
        ires = _tax(db_session, entity, "ires", 2026, "first_advance", "500.00")
        group = _group(db_session, [ires])
        assert group.fiscal_year_not_calendar_year is expected
        listed = F24GroupService(db_session).list_f24_groups(
            F24GroupListQuery(legal_entity_id=entity), ADMIN
        )
        assert [item.fiscal_year_not_calendar_year for item in listed.items] == [expected]


def test_a_code_is_frozen_when_the_line_is_grouped(db_session: Session) -> None:
    entity = _entity(db_session)
    item = _tax(db_session, entity, "irpef", 2026, "second_advance", "100.00")
    group = _group(db_session, [item])

    row = db_session.scalars(select(F24GroupItem).where(F24GroupItem.group_id == group.id)).one()
    assert (row.code, row.line, row.amount) == ("4034", "irpef_second_advance", D("100.00"))


def test_an_entity_with_no_fiscal_profile_has_no_codes_and_still_groups(
    db_session: Session,
) -> None:
    world = CashWorld(db_session)
    item = _tax(db_session, world.entity, "irpef", 2026, "balance", "100.00")

    (line,) = _group(db_session, [item]).lines

    assert (line.code, line.code_to_verify) == (None, True)


# --- what may be grouped ---------------------------------------------------------------------


def test_a_tax_forecast_becomes_the_item_that_is_paid_when_it_is_grouped(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-flat-rate")
    forecast = _tax(
        db_session, entity, "substitute_tax", 2026, "first_advance", "40.00", state="forecast"
    )

    _group(db_session, [forecast])

    item = db_session.get(DueItem, forecast)
    assert item is not None and (item.state, item.forecast_key) == ("expected", None)


def test_a_forecast_that_is_not_a_tax_is_not_yet_due_and_is_refused(db_session: Session) -> None:
    entity = _entity(db_session)
    payroll = _item(
        db_session, entity, "30.00", kind="payroll", tax="payroll_inps", state="forecast"
    )

    with pytest.raises(Conflict, match="previsione"):
        _group(db_session, [payroll])


def test_items_that_fall_on_different_days_are_refused(db_session: Session) -> None:
    entity = _entity(db_session)
    first = _tax(db_session, entity, "irpef", 2026, "balance", "100.00")
    other = _tax(
        db_session,
        entity,
        "irpef",
        2026,
        "second_advance",
        "100.00",
        due=DUE + dt.timedelta(days=1),
    )

    with pytest.raises(ValidationFailed, match="giorni diversi"):
        _group(db_session, [first, other])


def test_only_what_is_owed_to_the_tax_office_goes_in_an_f24(db_session: Session) -> None:
    world = CashWorld(db_session)
    tax = _tax(db_session, world.entity, "irpef", 2026, "balance", "100.00")
    supplier = world.item("50.00", DUE, direction="out")
    incoming = world.item("50.00", DUE, direction="in")

    for other in (supplier, incoming):
        with pytest.raises(ValidationFailed, match="erario"):
            _group(db_session, [tax, other])


def test_an_item_is_in_one_live_group_and_a_paid_one_is_in_none(db_session: Session) -> None:
    entity = _entity(db_session)
    first = _tax(db_session, entity, "irpef", 2026, "balance", "100.00")
    second = _tax(db_session, entity, "irpef", 2026, "second_advance", "80.00")
    paid = _tax(db_session, entity, "irpef", 2026, "first_advance", "60.00")
    DueItemService(db_session).settle(
        paid, SettlementCreate(amount=D("20.00"), settled_on=TODAY), ADMIN
    )
    _group(db_session, [first])

    with pytest.raises(Conflict, match="gia' in un F24"):
        _group(db_session, [first, second])
    with pytest.raises(Conflict, match="pagata"):
        _group(db_session, [second, paid])


def test_the_items_of_one_company_only_and_a_missing_item_is_not_found(
    db_session: Session,
) -> None:
    world = CashWorld(db_session)
    here = _tax(db_session, world.entity, "irpef", 2026, "balance", "100.00")
    there_entity = _entity(db_session)
    there = _tax(db_session, there_entity, "irpef", 2026, "second_advance", "100.00")

    with pytest.raises(ValidationFailed, match="una sola azienda"):
        _group(db_session, [here, there])
    with pytest.raises(NotFound):
        _group(db_session, [here, uuid4()])


def test_a_group_of_a_module_that_is_off_is_refused(db_session: Session) -> None:
    entity = _entity(db_session)
    item = _item(db_session, entity, "50.00", kind="iva_settlement", tax="iva")

    with pytest.raises(ValidationFailed, match="modulo spento"):
        F24GroupService(db_session).group_f24(
            F24GroupCreate(due_item_ids=[item]), ADMIN, modules={"taxes"}
        )


# --- ungrouping and what a grouped item is shielded from -------------------------------------


def test_ungrouping_frees_the_items_and_the_group_stays_as_a_trace(db_session: Session) -> None:
    entity = _entity(db_session)
    first = _plain(db_session, entity, "100.00")
    second = _plain(db_session, entity, "80.00")
    group = _group(db_session, [first, second])

    dissolved = F24GroupService(db_session).ungroup_f24(group.id, ADMIN)

    assert dissolved.state == "dissolved" and all(line.released for line in dissolved.lines)
    # Free again: paid by hand, grouped anew, listed in neither as open.
    DueItemService(db_session).settle(
        first, SettlementCreate(amount=D("100.00"), settled_on=TODAY), ADMIN
    )
    again = _group(db_session, [second])
    assert again.id != group.id
    page = F24GroupService(db_session).list_f24_groups(F24GroupListQuery(), ADMIN)
    assert [g.id for g in page.items] == [again.id]
    # Ungrouping twice is idempotent.
    assert F24GroupService(db_session).ungroup_f24(group.id, ADMIN).state == "dissolved"


def test_a_grouped_item_is_not_dropped_restated_rescheduled_or_paid_by_hand(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    item = _plain(db_session, entity, "100.00")
    _group(db_session, [item])
    service = DueItemService(db_session)

    with pytest.raises(Conflict, match="F24"):
        service.drop(item)
    with pytest.raises(Conflict, match="F24"):
        service.restate(item, amount=D("90.00"), own_date=DUE)
    with pytest.raises(Conflict, match="F24"):
        service.reschedule(item, DUE + dt.timedelta(days=3))
    with pytest.raises(Conflict, match="F24"):
        service.settle(item, SettlementCreate(amount=D("100.00"), settled_on=TODAY), ADMIN)


# --- who may group, read and ungroup ---------------------------------------------------------


def test_only_an_administrator_groups_reads_and_ungroups(db_session: Session) -> None:
    entity = _entity(db_session)
    item = _plain(db_session, entity, "100.00")
    other = _plain(db_session, entity, "80.00")
    service = F24GroupService(db_session)

    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.group_f24(F24GroupCreate(due_item_ids=[item]), refused)
        with pytest.raises(PermissionDenied):
            service.list_f24_groups(F24GroupListQuery(), refused)
    # An agent needs `full_access`, for every one of them.
    with pytest.raises(AgentForbidden):
        service.group_f24(F24GroupCreate(due_item_ids=[item]), AGENT)
    group = service.group_f24(F24GroupCreate(due_item_ids=[item]), AGENT_FULL)
    with pytest.raises(AgentForbidden):
        service.list_f24_groups(F24GroupListQuery(), AGENT)
    with pytest.raises(AgentForbidden):
        service.get_f24_group(group.id, AGENT)
    assert [g.id for g in service.list_f24_groups(F24GroupListQuery(), AGENT_FULL).items] == [
        group.id
    ]
    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.get_f24_group(group.id, refused)
        with pytest.raises(PermissionDenied):
            service.ungroup_f24(group.id, refused)
    with pytest.raises(AgentForbidden):
        service.ungroup_f24(group.id, AGENT)
    assert service.ungroup_f24(group.id, AGENT_FULL).state == "dissolved"
    assert service.group_f24(F24GroupCreate(due_item_ids=[other]), ADMIN).state == "open"


def test_the_list_filters_by_state_company_and_date(db_session: Session) -> None:
    entity = _entity(db_session)
    other_entity = _entity(db_session)
    early = _plain(db_session, entity, "100.00", due=DUE)
    late = _plain(db_session, entity, "80.00", due=DUE + dt.timedelta(days=30))
    elsewhere = _plain(db_session, other_entity, "70.00")
    groups = {
        key: _group(db_session, [item])
        for key, item in (("a", early), ("b", late), ("c", elsewhere))
    }
    service = F24GroupService(db_session)

    def listed(**filters: object) -> list[UUID]:
        return [g.id for g in service.list_f24_groups(F24GroupListQuery(**filters), ADMIN).items]  # type: ignore[arg-type]

    assert listed(legal_entity_id=entity) == [groups["a"].id, groups["b"].id]
    assert listed(legal_entity_id=entity, due_from=DUE + dt.timedelta(days=1)) == [groups["b"].id]
    assert listed(legal_entity_id=entity, limit=1) == [groups["a"].id]
    page = service.list_f24_groups(F24GroupListQuery(legal_entity_id=entity, limit=1), ADMIN)
    assert page.next_cursor == groups["a"].id
    after = service.list_f24_groups(
        F24GroupListQuery(legal_entity_id=entity, limit=1, cursor=page.next_cursor), ADMIN
    )
    assert [g.id for g in after.items] == [groups["b"].id] and after.next_cursor is None
    service.ungroup_f24(groups["a"].id, ADMIN)
    assert listed(legal_entity_id=entity, state="dissolved") == [groups["a"].id]  # type: ignore[arg-type]


# --- receipts --------------------------------------------------------------------------------


def _inbox(session: Session, entity: UUID | None, kind: str | None = "tax_document") -> UUID:
    item = InboxItem(
        original_name="f24-ricevuta.pdf",
        file_type="pdf",
        size_bytes=10,
        sha256="a" * 64,
        content_sha256="b" * 64,
        kind=kind,
        state="filed",
        legal_entity_id=entity,
    )
    session.add(item)
    session.flush()
    return item.id


def test_a_receipt_is_an_inbox_tax_document_the_group_points_at(db_session: Session) -> None:
    entity = _entity(db_session)
    group = _group(db_session, [_tax(db_session, entity, "irpef", 2026, "balance", "100.00")])
    service = F24GroupService(db_session)
    receipt = _inbox(db_session, entity)

    attached = service.attach_f24_receipt(group.id, receipt, ADMIN)

    (read,) = attached.receipts
    assert (read.inbox_item_id, read.original_name) == (receipt, "f24-ricevuta.pdf")
    with pytest.raises(Conflict, match="gia' collegata"):
        service.attach_f24_receipt(group.id, receipt, ADMIN)
    # Not a tax document, or another company's: refused.
    with pytest.raises(ValidationFailed, match="tipo"):
        service.attach_f24_receipt(group.id, _inbox(db_session, entity, "receipt"), ADMIN)
    with pytest.raises(ValidationFailed, match="altra azienda"):
        service.attach_f24_receipt(group.id, _inbox(db_session, _entity(db_session)), ADMIN)
    with pytest.raises(NotFound):
        service.attach_f24_receipt(group.id, uuid4(), ADMIN)
    # Only an administrator, an agent with `full_access`.
    with pytest.raises(PermissionDenied):
        service.attach_f24_receipt(group.id, receipt, COLLABORATOR)
    with pytest.raises(AgentForbidden):
        service.detach_f24_receipt(group.id, receipt, AGENT)

    assert service.detach_f24_receipt(group.id, receipt, ADMIN).receipts == []
    with pytest.raises(NotFound):
        service.detach_f24_receipt(group.id, receipt, ADMIN)


def test_a_dissolved_group_takes_no_receipt(db_session: Session) -> None:
    entity = _entity(db_session)
    group = _group(db_session, [_tax(db_session, entity, "irpef", 2026, "balance", "100.00")])
    F24GroupService(db_session).ungroup_f24(group.id, ADMIN)

    with pytest.raises(Conflict, match="sciolto"):
        F24GroupService(db_session).attach_f24_receipt(group.id, _inbox(db_session, entity), ADMIN)


def test_the_group_row_keeps_who_grouped_it(db_session: Session) -> None:
    entity = _entity(db_session)
    group = _group(db_session, [_tax(db_session, entity, "irpef", 2026, "balance", "100.00")])

    row = db_session.get(F24Group, group.id)
    assert row is not None and (row.created_by_id, row.created_by_type) == (ADMIN.id, "user")


def test_a_proposal_for_a_group_stops_being_current_when_the_group_is_ungrouped(
    db_session: Session,
) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    first = _plain(db_session, entity, "300.00")
    credit = _credit(db_session, entity, "50.00")
    ids = [first, credit]
    group = _group(db_session, ids)
    movement = _movement(db_session, account, "-250.00")
    service = MovementMatchService(db_session)

    proposed = service.propose_match(movement.id, MatchProposalCreate(due_item_ids=ids), ADMIN)
    assert proposed.movement.match is not None and proposed.movement.match.is_current

    F24GroupService(db_session).ungroup_f24(group.id, ADMIN)

    stale = service.get_movement(movement.id, ADMIN)
    assert stale.movement.match is not None and not stale.movement.match.is_current


# --- REB-1093: a group of nothing to pay is filed, not matched -----------------------------------
# The credit in these tests is the fixture's `_credit`; nothing in filing reads its origin, only its
# amount and its tax year (the order the credits are spent in), so any credit kind behaves the same.


def _file(
    session: Session,
    group_id: UUID,
    *,
    receipt: UUID | None = None,
    actor=ADMIN,  # noqa: ANN001
) -> F24GroupRead:
    return F24GroupService(session).file_f24(
        group_id, F24GroupFile(filed_on=TODAY, receipt_inbox_item_id=receipt), actor
    )


def _payments(session: Session, ids: list[UUID]) -> dict[UUID, Decimal]:
    """What is live-paid on each item, summed over its payments."""
    paid: dict[UUID, Decimal] = {}
    for row in session.scalars(
        select(DueItemSettlement).where(
            DueItemSettlement.due_item_id.in_(ids), DueItemSettlement.reversed_at.is_(None)
        )
    ):
        paid[row.due_item_id] = paid.get(row.due_item_id, D("0.00")) + row.amount
    return paid


def test_a_group_that_nets_to_zero_is_settled_by_filing_it(db_session: Session) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    owed = _tax(db_session, entity, "irpef", 2026, "balance", "200.00")
    credit = _credit(db_session, entity, "200.00")
    receipt = _inbox(db_session, entity)

    group = _group(db_session, [owed, credit])

    assert (group.state, group.total, group.credit_left, group.settled_by) == (
        "open",
        D("0.00"),
        None,
        None,
    )
    # No movement can settle it: it is no candidate and a selection of its lines is refused.
    movement = _movement(db_session, account, "-200.00")
    service = MovementMatchService(db_session)
    assert service.get_movement(movement.id, ADMIN).candidates == []
    with pytest.raises(ValidationFailed, match="presentato"):
        service.preview(movement.id, MatchSelection(due_item_ids=[owed, credit]), ADMIN)

    filed = _file(db_session, group.id, receipt=receipt)

    assert (filed.state, filed.settled_by, filed.settled_on, filed.bank_movement_id) == (
        "settled",
        "filing",
        TODAY,
        None,
    )
    assert filed.credit_left == D("0.00")
    assert [line.used_amount for line in filed.lines] == [D("200.00"), D("-200.00")]
    assert [r.inbox_item_id for r in filed.receipts] == [receipt]
    assert _payments(db_session, [owed, credit]) == {owed: D("200.00"), credit: D("-200.00")}
    assert {db_session.get(DueItem, i).state for i in (owed, credit)} == {"settled"}  # type: ignore[union-attr]
    # Settled once: neither filed again nor taken apart.
    with pytest.raises(Conflict, match="gia' segnato"):
        _file(db_session, group.id)
    with pytest.raises(Conflict, match="riapre"):
        F24GroupService(db_session).ungroup_f24(group.id, ADMIN)


def test_a_group_with_an_amount_to_pay_cannot_be_filed(db_session: Session) -> None:
    entity = _entity(db_session)
    group = _group(
        db_session,
        [
            _tax(db_session, entity, "irpef", 2026, "balance", "200.00"),
            _credit(db_session, entity, "50.00"),
        ],
    )

    with pytest.raises(ValidationFailed, match="movimento in banca"):
        _file(db_session, group.id)

    assert F24GroupService(db_session).get_f24_group(group.id, ADMIN).state == "open"


def test_a_net_credit_spends_only_what_covers_the_debits_and_leaves_the_rest(
    db_session: Session,
) -> None:
    entity = _entity(db_session)
    owed = _plain(db_session, entity, "100.00")
    old_credit = _credit(db_session, entity, "80.00", year=2024)
    new_credit = _credit(db_session, entity, "150.00", year=2025)
    group = _group(db_session, [owed, old_credit, new_credit])
    assert group.total == D("-130.00")

    filed = _file(db_session, group.id)

    # The older credit goes first and whole; the newer one covers the last 20 of the debits.
    assert _payments(db_session, [owed, old_credit, new_credit]) == {
        owed: D("100.00"),
        old_credit: D("-80.00"),
        new_credit: D("-20.00"),
    }
    lines = _by_item(filed)
    assert lines[new_credit].used_amount == D("-20.00")  # type: ignore[attr-defined]
    assert (lines[old_credit].released, lines[new_credit].released) == (False, True)  # type: ignore[attr-defined]
    assert filed.credit_left == D("130.00")
    states = {i: db_session.get(DueItem, i).state for i in (owed, old_credit, new_credit)}  # type: ignore[union-attr]
    assert states == {owed: "settled", old_credit: "settled", new_credit: "expected"}

    # What is left of the credit goes in the next F24, with what is left of it as the line.
    next_owed = _plain(db_session, entity, "50.00")
    second = _group(db_session, [next_owed, new_credit])
    assert (second.debit_total, second.credit_total, second.total) == (
        D("50.00"),
        D("130.00"),
        D("-80.00"),
    )
    again = _file(db_session, second.id)
    assert again.credit_left == D("80.00")
    assert _payments(db_session, [new_credit]) == {new_credit: D("-70.00")}
    assert F24GroupService(db_session).get_f24_group(group.id, ADMIN).credit_left == D("130.00")


def test_a_group_of_credits_alone_is_filed_and_spends_nothing(db_session: Session) -> None:
    entity = _entity(db_session)
    credit = _credit(db_session, entity, "90.00")
    group = _group(db_session, [credit])

    filed = _file(db_session, group.id)

    assert (filed.state, filed.total, filed.credit_left) == ("settled", D("-90.00"), D("90.00"))
    assert _payments(db_session, [credit]) == {}
    assert db_session.get(DueItem, credit).state == "expected"  # type: ignore[union-attr]
    assert _group(db_session, [credit]).state == "open"


def test_filing_is_a_persons_act_dated_no_later_than_today(db_session: Session) -> None:
    entity = _entity(db_session)
    owed = _tax(db_session, entity, "irpef", 2026, "balance", "100.00")
    group = _group(db_session, [owed, _credit(db_session, entity, "100.00")])
    service = F24GroupService(db_session)
    data = F24GroupFile(filed_on=TODAY)

    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.file_f24(group.id, data, refused)
    for agent in (AGENT, AGENT_FULL):
        with pytest.raises(AgentForbidden):
            service.file_f24(group.id, data, agent)
    with pytest.raises(ValidationFailed, match="futura"):
        service.file_f24(group.id, F24GroupFile(filed_on=TODAY + dt.timedelta(days=1)), ADMIN)
    with pytest.raises(ValidationFailed, match="tipo"):
        service.file_f24(
            group.id,
            F24GroupFile(
                filed_on=TODAY, receipt_inbox_item_id=_inbox(db_session, entity, "receipt")
            ),
            ADMIN,
        )
    # Nothing of the refused attempts stuck.
    assert service.get_f24_group(group.id, ADMIN).state == "open"
    assert _payments(db_session, [owed]) == {}


def test_reopening_a_filed_group_reverses_what_it_paid(db_session: Session) -> None:
    entity = _entity(db_session)
    owed = _plain(db_session, entity, "100.00")
    credit = _credit(db_session, entity, "150.00")
    group = _group(db_session, [owed, credit])
    service = F24GroupService(db_session)
    _file(db_session, group.id)
    # The rest of the credit goes in another F24, which keeps the first from reopening.
    second = _group(db_session, [_plain(db_session, entity, "30.00"), credit])
    with pytest.raises(Conflict, match="altro F24"):
        service.reopen_f24(group.id, ADMIN)
    service.ungroup_f24(second.id, ADMIN)

    for refused in (COLLABORATOR, READONLY):
        with pytest.raises(PermissionDenied):
            service.reopen_f24(group.id, refused)
    with pytest.raises(AgentForbidden):
        service.reopen_f24(group.id, AGENT_FULL)
    reopened = service.reopen_f24(group.id, ADMIN)

    assert (reopened.state, reopened.settled_on, reopened.credit_left, reopened.total) == (
        "open",
        None,
        None,
        D("-50.00"),
    )
    assert all(not line.released and line.used_amount is None for line in reopened.lines)
    assert _payments(db_session, [owed, credit]) == {}
    assert {db_session.get(DueItem, i).state for i in (owed, credit)} == {"expected"}  # type: ignore[union-attr]
    # It can be filed again, and a group a movement paid is not reopened from here.
    assert _file(db_session, group.id).state == "settled"
    with pytest.raises(Conflict, match="presentato"):
        service.reopen_f24(_group(db_session, [_plain(db_session, entity, "10.00")]).id, ADMIN)


def test_what_is_left_of_a_credit_is_paid_with_the_next_f24_by_a_movement(
    db_session: Session,
) -> None:
    world = CashWorld(db_session)
    entity = _entity(db_session)
    account = world.account("5000.00", entity=entity)
    credit = _credit(db_session, entity, "150.00")
    _file(db_session, _group(db_session, [_plain(db_session, entity, "100.00"), credit]).id)
    owed = _plain(db_session, entity, "300.00")
    ids = [owed, credit]
    group = _group(db_session, ids)
    assert group.total == D("250.00")

    movement = _movement(db_session, account, "-250.00")
    service = MovementMatchService(db_session)
    (candidate, *_) = service.get_movement(movement.id, ADMIN).candidates
    assert {item.due_item_id for item in candidate.items} == set(ids)
    service.confirm(movement.id, MatchConfirm(due_item_ids=ids), ADMIN)

    db_session.expire_all()
    settled = F24GroupService(db_session).get_f24_group(group.id, ADMIN)
    assert (settled.state, settled.settled_by, settled.credit_left) == ("settled", "movement", 0)
    assert _payments(db_session, [credit]) == {credit: D("-150.00")}
    assert db_session.get(DueItem, credit).state == "settled"  # type: ignore[union-attr]


def _settlement(session: Session, entity: UUID, frequency: str, year: int, number: int) -> UUID:
    from pigrocrm.core.fiscal.models import IvaSettlement

    row = IvaSettlement(
        legal_entity_id=entity,
        frequency=frequency,
        year=year,
        period_number=number,
        period_start=dt.date(year, 1, 1),
        period_end=dt.date(year, 1, 31),
        pack_id="it-standard",
        pack_version="1",
        sales_vat=D("100.00"),
        reverse_charge_vat=D("0.00"),
        purchase_vat=D("0.00"),
        credit_in=D("0.00"),
        debit_in=D("0.00"),
        surcharge=D("0.00"),
        result=D("100.00"),
        outcome="due",
        payment_due_on=DUE,
    )
    session.add(row)
    session.flush()
    return row.id


def test_an_iva_payment_is_written_on_the_agenzias_code_of_its_period(
    db_session: Session,
) -> None:
    """REB-1100: a month has its code (6001 to 6012), a quarter its own (6031 to 6033) and the
    fourth quarter of a quarterly payer goes with the annual settlement (6099); the reference
    year is the year of the period."""
    entity = _entity(db_session)
    cases = {
        ("monthly", 2025, 12): ("6012", "iva_monthly"),
        ("monthly", 2026, 3): ("6003", "iva_monthly"),
        ("quarterly", 2026, 2): ("6032", "iva_quarterly"),
        ("quarterly", 2025, 4): ("6099", "iva_annual"),
    }
    ids = {
        key: _item(
            db_session,
            entity,
            "10.00",
            kind="iva_settlement",
            tax="iva",
            source_id=_settlement(db_session, entity, *key),
        )
        for key in cases
    }
    lines = _by_item(_group(db_session, list(ids.values())))
    for key, (code, line) in cases.items():
        found = lines[ids[key]]
        assert (found.code, found.line, found.reference_year) == (  # type: ignore[attr-defined]
            code,
            line,
            key[1],
        )
        assert found.code_to_verify is False  # type: ignore[attr-defined]


def test_a_flat_rate_payment_has_no_iva_code(db_session: Session) -> None:
    entity = _entity(db_session, "it-flat-rate")
    item = _item(db_session, entity, "10.00", kind="iva_settlement", tax="iva")
    (line,) = _group(db_session, [item]).lines
    assert (line.code, line.code_to_verify) == (None, True)


def test_withholding_and_payroll_lines_carry_the_month_they_pay_for(db_session: Session) -> None:
    """REB-1100: paid on the 16th of the month after, so a payment on 16 March 2027 is for
    February: 1001 and 1040 write `02` in the `mese rif.` column with year 2027, and DM10 writes
    the «periodo da» `02/2027` and no reference year."""
    entity = _entity(db_session, "it-company")
    paid = dt.date(2027, 3, 16)
    employees = _item(
        db_session, entity, "40.00", kind="payroll", tax="payroll_withholding", due=paid
    )
    inps = _item(db_session, entity, "60.00", kind="payroll", tax="payroll_inps", due=paid)
    fees = _item(db_session, entity, "20.00", kind="withholding", tax="withholding", due=paid)
    lines = _by_item(_group(db_session, [employees, inps, fees]))
    got = {
        item: (line.code, line.reference_year, line.reference_period)  # type: ignore[attr-defined]
        for item, line in lines.items()
    }
    assert got == {
        employees: ("1001", 2027, "02"),
        inps: ("DM10", None, "02/2027"),
        fees: ("1040", 2027, "02"),
    }
    assert lines[inps].code_kind == "inps_causale"  # type: ignore[attr-defined]


def test_a_payment_in_january_is_for_december_of_the_year_before(db_session: Session) -> None:
    entity = _entity(db_session, "it-company")
    item = _item(
        db_session,
        entity,
        "40.00",
        kind="payroll",
        tax="payroll_withholding",
        due=dt.date(2027, 1, 18),
    )
    (line,) = _group(db_session, [item]).lines
    assert (line.reference_year, line.reference_period) == (2026, "12")


def test_a_flat_rate_taxpayer_owes_the_employee_withholding_but_not_the_fee_withholding(
    db_session: Session,
) -> None:
    entity = _entity(db_session, "it-flat-rate")
    paid = dt.date(2027, 3, 16)
    employees = _item(
        db_session, entity, "40.00", kind="payroll", tax="payroll_withholding", due=paid
    )
    fees = _item(db_session, entity, "20.00", kind="withholding", tax="withholding", due=paid)
    lines = _by_item(_group(db_session, [employees, fees]))
    assert lines[employees].code == "1001"  # type: ignore[attr-defined]
    assert lines[fees].code is None  # type: ignore[attr-defined]
    assert lines[fees].reference_period == "02"  # type: ignore[attr-defined]
