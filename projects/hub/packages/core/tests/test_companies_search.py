"""Companies: search, cursor pagination and the new filters beside `stato` (REB-285)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.errors import ValidationFailed
from rebase_core.schemas import CompanyCreate


@pytest.fixture
def clean(hub_session: Session) -> Session:
    yield hub_session  # type: ignore[misc]
    hub_session.rollback()
    hub_session.execute(text("DELETE FROM comments"))
    hub_session.execute(text("DELETE FROM companies"))
    hub_session.execute(text("DELETE FROM users"))
    hub_session.commit()


def _request(
    nome_azienda: str = "ACME Srl",
    email: str = "wile@acme.it",
    **extra: object,
) -> CompanyCreate:
    payload: dict[str, object] = {
        "nome_azienda": nome_azienda,
        "referente_nome": "Wile",
        "referente_cognome": "Coyote",
        "email": email,
        "telefono": "+39 345 1234567",
        "figura_richiesta": "Backend developer",
        "progetto": "Serve un backend developer per tre mesi.",
        "periodo_da": date(2026, 10, 1),
        "durata": "3 mesi",
        "budget_giornaliero": Decimal("500"),
        "remoto": "remoto",
        "numero_risorse": 1,
    }
    payload.update(extra)
    return CompanyCreate(**payload)  # type: ignore[arg-type]


def test_search_hits_a_partial_surname_and_an_email_domain(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(_request("Rossi Consulting", "wile@rossilab.it", referente_cognome="Rossi"))
    service.request(_request("Bianchi SRL", "bob@other.it", referente_cognome="Bianchi"))

    by_surname = service.list_recent(q="oss")
    assert [item.email for item in by_surname.items] == ["wile@rossilab.it"]

    by_domain = service.list_recent(q="rossilab.it")
    assert [item.email for item in by_domain.items] == ["wile@rossilab.it"]


def test_search_hits_the_role_the_request_names(clean: Session) -> None:
    """REB-380: `figura_richiesta` is a searchable field on the row, like `progetto`,
    not only a display column -- the model's own docstring says so, and the trigram
    index migration 0016 creates would otherwise back nothing."""
    service = CompanyService(clean)
    service.request(_request("A Srl", "a@studio.it", figura_richiesta="Fractional CTO"))
    service.request(_request("B Srl", "b@studio.it", figura_richiesta="Backend developer"))

    by_role = service.list_recent(q="Fractional CTO")
    assert [item.email for item in by_role.items] == ["a@studio.it"]


def test_the_cursor_walks_every_row_once_with_no_dupes_or_gaps(clean: Session) -> None:
    service = CompanyService(clean)
    for i in range(7):
        service.request(_request(f"Company {i}", f"referente{i}@studio.it"))

    full = service.list_recent(limit=100)
    assert full.totale == 7 and len(full.items) == 7

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


def test_budget_and_periodo_da_filters_narrow_the_list(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(
        _request(
            "Cheap Co",
            "cheap@studio.it",
            budget_giornaliero=Decimal("200"),
            periodo_da=date(2026, 6, 1),
        )
    )
    service.request(
        _request(
            "Premium Co",
            "premium@studio.it",
            budget_giornaliero=Decimal("900"),
            periodo_da=date(2026, 11, 1),
        )
    )

    by_budget = service.list_recent(budget_min=Decimal("500"))
    assert [item.email for item in by_budget.items] == ["premium@studio.it"]

    by_periodo = service.list_recent(periodo_da=date(2026, 9, 1))
    assert [item.email for item in by_periodo.items] == ["premium@studio.it"]


def test_per_stato_respects_the_other_active_filters(clean: Session) -> None:
    service = CompanyService(clean)
    service.request(_request("A", "a@studio.it", budget_giornaliero=Decimal("200")))
    service.request(_request("B", "b@studio.it", budget_giornaliero=Decimal("900")))

    filtered = service.list_recent(budget_min=Decimal("500"))
    assert filtered.per_stato == {"nuovo": 1, "contattato": 0, "in_corso": 0, "chiuso": 0}
    assert filtered.totale == 1


def test_an_unknown_stato_is_an_empty_list_not_a_422(clean: Session) -> None:
    """Pass-through, like every other filter in this package -- see
    `talenti.TalentiService.list_recent`'s own reasoning."""
    service = CompanyService(clean)
    service.request(_request())
    unknown = service.list_recent(stato="forse")
    assert unknown.items == [] and unknown.totale == 0
