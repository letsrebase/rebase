"""REB-1090: what a staff record accepts as a seasonal worker's contract history. Pure shapes, no
database: the dates must make sense and the days of effective work cannot exceed the contract's
own days in the tax year."""

import datetime as dt
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pigrocrm.core.staff.schemas import EmployeeCreate, SeasonalContract, StaffMemberUpdate


def _employee(**extra: object) -> EmployeeCreate:
    return EmployeeCreate(
        legal_entity_id=uuid4(),
        name="Sara Stagionale",
        start_date=dt.date(2026, 1, 1),
        gross_annual_pay=Decimal("24000.00"),
        monthly_payments=13,
        contract_type="seasonal",
        **extra,  # type: ignore[arg-type]
    )


def test_a_history_is_stored_as_json_and_read_back_as_contracts() -> None:
    employee = _employee(
        seasonal_contracts=[
            {"start_date": "2026-06-01", "end_date": "2026-09-30", "worked_days": {"2026": 100}},
            {"start_date": "2025-06-01", "end_date": "2025-09-30", "worked_days": {"2025": 90}},
        ]
    )
    stored = employee.model_dump()["seasonal_contracts"]
    # In order of start, dates as text and the years as text keys: what JSONB holds.
    assert [contract["start_date"] for contract in stored] == ["2025-06-01", "2026-06-01"]
    assert stored[1]["worked_days"] == {"2026": 100}
    read = SeasonalContract.model_validate(stored[1])
    assert read.worked_days == {2026: 100} and read.end_date == dt.date(2026, 9, 30)


def test_an_update_dumps_the_history_the_same_way_and_leaves_it_out_when_not_sent() -> None:
    assert "seasonal_contracts" not in StaffMemberUpdate().model_dump(exclude_unset=True)
    update = StaffMemberUpdate(
        seasonal_contracts=[{"start_date": "2026-06-01", "worked_days": {2026: 10}}]  # type: ignore[list-item]
    )
    assert update.model_dump(exclude_unset=True)["seasonal_contracts"] == [
        {"start_date": "2026-06-01", "end_date": None, "worked_days": {"2026": 10}}
    ]


def test_more_days_than_the_contract_has_in_the_year_are_refused() -> None:
    with pytest.raises(ValidationError, match="piu' dei 30 giorni"):
        SeasonalContract(
            start_date=dt.date(2026, 6, 1), end_date=dt.date(2026, 6, 30), worked_days={2026: 31}
        )


def test_a_contract_cannot_end_before_it_starts() -> None:
    with pytest.raises(ValidationError, match="non puo' precedere"):
        SeasonalContract(start_date=dt.date(2026, 6, 2), end_date=dt.date(2026, 6, 1))


def test_contracts_of_one_worker_do_not_overlap() -> None:
    with pytest.raises(ValidationError, match="sovrapporre"):
        _employee(
            seasonal_contracts=[
                {"start_date": "2026-06-01", "end_date": "2026-07-31"},
                {"start_date": "2026-07-31", "end_date": "2026-09-30"},
            ]
        )
    with pytest.raises(ValidationError, match="sovrapporre"):
        _employee(
            seasonal_contracts=[
                {"start_date": "2026-06-01"},
                {"start_date": "2026-09-01", "end_date": "2026-09-30"},
            ]
        )
