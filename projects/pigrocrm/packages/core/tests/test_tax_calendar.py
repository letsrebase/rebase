"""REB-1004: the tax calendar read from the fiscal packs (taxes design § 2).

Every date is a verified row of the frame's A5 register; what is proved here is that the pack
carries exactly those, that a year with no entry has no dates, that a non-working day moves
the due date and never the nominal one, and that only an administrator reads the calendar.
"""

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.analytics.service import AnalyticsService
from pigrocrm.core.errors import AgentForbidden, NotFound, PermissionDenied
from pigrocrm.core.fiscal.calendar import easter, next_working_day, tax_calendar
from pigrocrm.core.fiscal.law import TaxCalendarLaw
from pigrocrm.core.fiscal.pack import IT_FLAT_RATE_PACK, IT_STANDARD_PACK, NON_IT_PACK, FiscalPack
from pigrocrm.core.fiscal.schemas import FiscalProfileUpsert
from pigrocrm.core.fiscal.service import FiscalProfileService

ADMIN = Actor(id=None, type="system", role="admin")


def _nominal(pack: FiscalPack, year: int, tax: str, kind: str, cadence: str | None) -> list[date]:
    dates = tax_calendar(pack, year) or []
    return [
        entry.nominal_date
        for entry in dates
        if (entry.tax, entry.kind, entry.cadence) == (tax, kind, cadence)
    ]


def test_the_ordinary_pack_carries_the_iva_dates() -> None:
    pack = IT_STANDARD_PACK
    monthly = _nominal(pack, 2026, "iva", "periodic_payment", "monthly")
    assert [(d.month, d.day) for d in monthly] == [(m, 20 if m == 8 else 16) for m in range(1, 13)]
    assert _nominal(pack, 2026, "iva", "annual_balance", "quarterly") == [date(2026, 3, 16)]
    quarterly = _nominal(pack, 2026, "iva", "quarterly_payment", "quarterly")
    assert quarterly == [date(2026, 5, 16), date(2026, 8, 20), date(2026, 11, 16)]
    assert _nominal(pack, 2026, "iva", "advance", None) == [date(2026, 12, 27)]


def test_the_ordinary_pack_carries_the_income_tax_dates() -> None:
    pack = IT_STANDARD_PACK
    assert _nominal(pack, 2026, "income_tax", "balance_and_first_advance", None) == [
        date(2026, 6, 30)
    ]
    assert _nominal(pack, 2026, "income_tax", "second_advance", None) == [date(2026, 11, 30)]
    assert _nominal(pack, 2026, "income_tax", "instalments_end", None) == [date(2026, 12, 16)]


def test_the_flat_rate_pack_carries_the_substitute_tax_dates_and_no_iva() -> None:
    dates = tax_calendar(IT_FLAT_RATE_PACK, 2026)
    assert dates is not None
    assert {entry.tax for entry in dates} == {"substitute_tax"}
    assert [entry.nominal_date for entry in dates] == [date(2026, 6, 30), date(2026, 11, 30)]


def test_every_date_names_its_source_and_what_it_covers() -> None:
    for pack in (IT_STANDARD_PACK, IT_FLAT_RATE_PACK):
        for entry in tax_calendar(pack, 2026) or []:
            assert entry.source.startswith("Agenzia delle Entrate")
            assert entry.covers


def test_a_year_the_pack_has_no_dates_for_has_none_and_never_borrows() -> None:
    assert tax_calendar(IT_STANDARD_PACK, 2025) is None
    assert tax_calendar(IT_FLAT_RATE_PACK, 2025) is None
    assert tax_calendar(NON_IT_PACK, 2026) is None


def test_a_later_entry_governs_from_its_year() -> None:
    first = IT_STANDARD_PACK.calendars[0]
    second = TaxCalendarLaw(from_year=2028, rules=first.rules[:1])
    pack = FiscalPack(id="x", version="1", ceilings=(), charges=(), calendars=(first, second))
    assert pack.calendar_for(2027) is first
    assert pack.calendar_for(2028) is second
    assert len(tax_calendar(pack, 2028) or []) == 12
    with pytest.raises(ValueError, match="increasing"):
        FiscalPack(id="x", version="1", ceilings=(), charges=(), calendars=(second, first))


def test_a_deadline_on_a_non_working_day_moves_only_the_due_date() -> None:
    # 16 May 2026 is a Saturday; 16 Nov 2026 a Monday; 16 Aug 2026 is not a date of the pack.
    dates = {e.nominal_date: e for e in tax_calendar(IT_STANDARD_PACK, 2026) or []}
    saturday = dates[date(2026, 5, 16)]
    assert saturday.due_date == date(2026, 5, 18)
    assert dates[date(2026, 11, 16)].due_date == date(2026, 11, 16)
    assert dates[date(2026, 12, 27)].due_date == date(2026, 12, 28)  # a Sunday


def test_the_national_holidays_move_a_deadline() -> None:
    assert easter(2026) == date(2026, 4, 5)
    assert easter(2027) == date(2027, 3, 28)
    assert next_working_day(date(2026, 4, 6)) == date(2026, 4, 7)  # Easter Monday
    assert next_working_day(date(2026, 12, 25)) == date(2026, 12, 28)  # Fri, then the weekend
    assert next_working_day(date(2026, 8, 17)) == date(2026, 8, 17)
    assert next_working_day(date(2026, 6, 2)) == date(2026, 6, 3)
    assert next_working_day(date(2026, 6, 29)) == date(2026, 6, 29)  # a patron saint is local


def _profile(session: Session, pack_id: str, regime: str) -> None:
    FiscalProfileService(session).upsert(
        FiscalProfileUpsert(pack_id=pack_id, regime_code=regime), ADMIN
    )


def test_the_service_lists_the_dates_of_the_legal_entitys_pack(db_session: Session) -> None:
    _profile(db_session, "it-standard", "RF01")
    calendar = AnalyticsService(db_session).get_tax_calendar(2026, ADMIN)
    assert (calendar.pack_id, calendar.year) == ("it-standard", 2026)
    assert len(calendar.dates) == 12 + 1 + 3 + 1 + 3
    assert calendar.dates == sorted(calendar.dates, key=lambda e: e.nominal_date)


def test_the_service_answers_an_empty_list_for_a_year_without_dates(db_session: Session) -> None:
    _profile(db_session, "it-flat-rate", "RF19")
    flat = AnalyticsService(db_session).get_tax_calendar(2026, ADMIN)
    assert [e.tax for e in flat.dates] == ["substitute_tax"] * 2
    assert AnalyticsService(db_session).get_tax_calendar(2020, ADMIN).dates == []


def test_only_an_administrator_reads_the_calendar(db_session: Session) -> None:
    _profile(db_session, "it-standard", "RF01")
    for role in ("collaborator", "readonly"):
        actor = Actor(id=uuid4(), type="user", role=role)  # type: ignore[arg-type]
        with pytest.raises(PermissionDenied):
            AnalyticsService(db_session).get_tax_calendar(2026, actor)
    agent = Actor(id=uuid4(), type="mcp", role="admin")
    with pytest.raises(AgentForbidden):
        AnalyticsService(db_session).get_tax_calendar(2026, agent)
    full = Actor(id=uuid4(), type="mcp", role="admin", full_access=True)
    assert AnalyticsService(db_session).get_tax_calendar(2026, full).dates


def test_a_legal_entity_with_no_profile_is_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        AnalyticsService(db_session).get_tax_calendar(2026, ADMIN)
