"""Companies: search, cursor pagination and the new filters beside `status` (REB-285)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate
from rebase_core.errors import ValidationFailed


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM notes"))
    hub_session.execute(text("DELETE FROM companies"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _request(
    company_name: str = "ACME Srl",
    email: str = "wile@acme.it",
    **extra: object,
) -> CompanyCreate:
    payload: dict[str, object] = {
        "company_name": company_name,
        "contact_first_name": "Wile",
        "contact_last_name": "Coyote",
        "email": email,
        "phone": "+39 345 1234567",
        "requested_role": "Backend developer",
        "project": "Serve un backend developer per tre mesi.",
        "period_from": date(2026, 10, 1),
        "duration": "3 mesi",
        "daily_budget": Decimal("500"),
        "remote": "remote",
        "headcount": 1,
    }
    payload.update(extra)
    return CompanyCreate(**payload)  # type: ignore[arg-type]


def test_search_hits_a_partial_surname_and_an_email_domain(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(_request("Rossi Consulting", "wile@rossilab.it", contact_last_name="Rossi"))
    service.request(_request("Bianchi SRL", "bob@other.it", contact_last_name="Bianchi"))

    by_surname = service.list_recent(q="oss")
    assert [item.email for item in by_surname.items] == ["wile@rossilab.it"]

    by_domain = service.list_recent(q="rossilab.it")
    assert [item.email for item in by_domain.items] == ["wile@rossilab.it"]


def test_search_hits_the_role_the_request_names(clean: Session) -> None:
    """REB-380: `requested_role` is a searchable field on the row, like `project`,
    not only a display column -- the model's own docstring says so, and the trigram
    index migration 0016 creates would otherwise back nothing."""
    service = CompanyService(clean)
    service.request(_request("A Srl", "a@studio.it", requested_role="Fractional CTO"))
    service.request(_request("B Srl", "b@studio.it", requested_role="Backend developer"))

    by_role = service.list_recent(q="Fractional CTO")
    assert [item.email for item in by_role.items] == ["a@studio.it"]


def test_the_cursor_walks_every_row_once_with_no_dupes_or_gaps(clean: Session) -> None:
    service = CompanyService(clean)
    for i in range(7):
        service.request(_request(f"Company {i}", f"referente{i}@studio.it"))

    full = service.list_recent(limit=100)
    assert full.total == 7 and len(full.items) == 7

    seen: list[UUID] = []
    cursor: str | None = None
    for _ in range(20):  # generous upper bound: 7 rows over a page size of 2
        page = service.list_recent(limit=2, cursor=cursor)
        seen.extend(item.id for item in page.items)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("the cursor never reached its last page")

    assert len(seen) == len(set(seen)) == 7
    assert set(seen) == {item.id for item in full.items}


def test_a_malformed_cursor_is_refused(clean: Session) -> None:
    with pytest.raises(ValidationFailed):
        CompanyService(clean).list_recent(cursor="not-a-valid-cursor")


def test_budget_and_period_from_filters_narrow_the_list(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(
        _request(
            "Cheap Co",
            "cheap@studio.it",
            daily_budget=Decimal("200"),
            period_from=date(2026, 6, 1),
        )
    )
    service.request(
        _request(
            "Premium Co",
            "premium@studio.it",
            daily_budget=Decimal("900"),
            period_from=date(2026, 11, 1),
        )
    )

    by_budget = service.list_recent(budget_min=Decimal("500"))
    assert [item.email for item in by_budget.items] == ["premium@studio.it"]

    by_period = service.list_recent(period_from=date(2026, 9, 1))
    assert [item.email for item in by_period.items] == ["premium@studio.it"]


def test_by_status_respects_the_other_active_filters(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(_request("A", "a@studio.it", daily_budget=Decimal("200")))
    service.request(_request("B", "b@studio.it", daily_budget=Decimal("900")))

    filtered = service.list_recent(budget_min=Decimal("500"))
    assert filtered.by_status == {"new": 1, "contacted": 0, "in_progress": 0, "closed": 0}
    assert filtered.total == 1


def test_an_unknown_status_is_an_empty_list_not_to_422(clean: Session) -> None:
    """Pass-through, like every other filter in this package -- see
    `talents.TalentsService.list_recent`'s own reasoning."""
    service = CompanyService(clean)
    service.request(_request())
    unknown = service.list_recent(status="forse")
    assert unknown.items == [] and unknown.total == 0
