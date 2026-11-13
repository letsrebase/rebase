"""REB-868: a completed company request filed in rebase's CRM.

The CRM's door is a recorded `HttpCall` (`fakes_pigro`, as the engagements' tests have it) and
the model a `RecordingCall`: nothing here reaches a network. The row locks are checked from a
second connection while the fake door answers.
"""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fakes_pigro import CUSTOMER, DEAL, DEAL_URL, PIGRO, TOKEN, RecordedPigro
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rebase_core.companies import CompanyService
from rebase_core.company_schemas import CompanyCreate, CompanyFields
from rebase_core.config import Settings
from rebase_core.contact_points import ContactPointInput, ContactPointService
from rebase_core.crm_lead import LeadDraftOut
from rebase_core.crm_link import CrmLeadService, contact_point_payload, link_after_response
from rebase_core.crm_words import HTTPS_ONLY
from rebase_core.errors import InvalidState, LlmCapReached, NotFound
from rebase_core.llm import LlmCall, LlmRequest, LlmResponse, RecordingCall
from rebase_core.members import MemberService
from rebase_core.models import AdminAction, Company, User, UserContactPoint
from rebase_core.pigro_door import CAUSE_TIMEOUT

NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 2)
START = date(2026, 12, 1)
TABLES = ("admin_actions", "notes", "referrals", "companies", "users")
LEAD_URL = f"{PIGRO}/api/rebase/leads/"


def _lead_answer(*, created: bool = True) -> bytes:
    return json.dumps(
        {
            "customer_id": str(CUSTOMER),
            "person_id": str(uuid4()),
            "deal_id": str(DEAL),
            "deal_url": DEAL_URL,
            "created": created,
        }
    ).encode()


OK = (201, _lead_answer())


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _settings(token: str = TOKEN, url: str = PIGRO) -> Settings:
    return Settings(
        pigro_api_url=url,
        pigro_engagements_token=token,
        hub_url="https://hub.test/hub",
        _env_file=None,  # type: ignore[call-arg]
    )


def _draft_response(**fields: Any) -> LlmResponse:
    return LlmResponse(
        text="{}",
        parsed=LeadDraftOut.model_validate(fields),
        stop_reason="end_turn",
        refusal_category=None,
        model="claude-opus-5",
        provider="anthropic",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
    )


DRAFT = {
    "role": "Sviluppatore backend",
    "week_days": 3,
    "duration_weeks": 12,
    "skills": ["Python", "Docker"],
    "profile_summary": "Serve una persona per migrare un gestionale.",
}


def _service(
    session: Session,
    http: RecordedPigro,
    llm: LlmCall | None = None,
    *,
    settings: Settings | None = None,
    now: datetime = NOW,
) -> CrmLeadService:
    return CrmLeadService(
        session,
        settings or _settings(),
        http,
        (lambda: llm) if llm is not None else None,
        now=lambda: now,
        today=lambda: TODAY,
    )


def _create(session: Session, **over: Any) -> Company:
    fields: dict[str, Any] = {
        "company_name": "Acme S.r.l.",
        "contact_first_name": "Mario",
        "contact_last_name": "Rossi",
        "email": "mario@acme.it",
        "phone": "+39 333 1234567",
        "requested_role": "Backend developer",
        "project": "Portare un gestionale su servizi più piccoli.",
        "period_from": START,
        "duration": "3 mesi",
        "daily_budget": Decimal("400"),
        "remote": "remote",
        "headcount": 2,
    }
    fields.update(over)
    read, _ = CompanyService(session).request(CompanyCreate(**fields))
    row = session.get(Company, read.id)
    assert row is not None
    return row


def _row(session: Session, company_id: UUID) -> Company:
    session.expire_all()
    row = session.get(Company, company_id)
    assert row is not None
    return row


def _body(http: RecordedPigro, n: int = 0) -> dict[str, Any]:
    return json.loads(http.calls[n][3])


def test_every_path_writes_the_request_to_link(clean: Session) -> None:
    row = _create(clean)
    assert (row.crm_status, row.crm_attempted_at, row.crm_draft) == ("to_link", None, None)

    fields = CompanyFields(
        requested_role="Data engineer",
        project="Serve una pipeline dati.",
        period_from=START,
        duration="2 mesi",
        daily_budget=Decimal("300"),
        remote="remote",
        headcount=1,
    )
    _, second_id = MemberService(clean).create_additional_request(row.user_id, fields)
    assert _row(clean, second_id).crm_status == "to_link"


def test_a_linked_request_keeps_the_deal_and_sends_the_body_of_the_spec(clean: Session) -> None:
    company = _create(clean, project="Un gestionale.")
    http = RecordedPigro([OK])
    llm = RecordingCall([_draft_response(**DRAFT)])

    result = _service(clean, http, llm).link(company.id)

    assert (result.status, result.deal_url, result.error) == ("linked", DEAL_URL, None)
    method, url, headers, _ = http.calls[0]
    assert (method, url) == ("PUT", f"{LEAD_URL}{company.id}")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    body = _body(http)
    assert set(body) == {"company", "contact", "deal", "deal_id"}
    assert body["company"] == "Acme S.r.l."
    assert body["deal_id"] is None
    assert body["contact"] == {
        "first_name": "Mario",
        "last_name": "Rossi",
        "email": "mario@acme.it",
        "phone": "+39 333 1234567",
        "linkedin_url": None,
        "contact_points": [
            {
                "kind": "phone",
                "value": "+39 333 1234567",
                "label": "work",
                "note": None,
                "is_primary": True,
            }
        ],
    }
    deal = body["deal"]
    assert set(deal) == {"name", "value_lines", "expected_close_date", "notes"}
    assert deal["name"] == "Acme S.r.l.: Sviluppatore backend"
    # 400 a day, 3 days a week, 12 weeks, 2 people: 72 days in 3 months, 28.800 in the CRM.
    assert deal["value_lines"] == [
        {
            "unit": "day",
            "delivery_period": "one_off",
            "unit_price": "400.00",
            "quantity": "72.00",
            "start": "2026-12-01",
            "duration_months": 3,
        }
    ]
    assert deal["expected_close_date"] == "2026-12-01"
    note = deal["notes"]
    assert "Progetto, come l'ha scritto l'azienda:" in note
    assert "Un gestionale." in note
    assert "Bozza AI, da rivedere" in note
    assert "Competenze: Python, Docker" in note
    assert f"https://hub.test/hub/admin/companies/{company.id}" in note
    row = _row(clean, company.id)
    assert (row.crm_customer_id, row.crm_deal_id, row.crm_deal_url) == (CUSTOMER, DEAL, DEAL_URL)
    assert row.crm_error is None and row.crm_attempted_at == NOW


def _point(
    kind: str, value: str, label: str, *, note: str | None = None, primary: bool = False
) -> dict[str, Any]:
    return {"kind": kind, "value": value, "label": label, "note": note, "is_primary": primary}


def test_the_contact_the_crm_gets_is_the_login_address_the_primary_phone_and_every_point(
    clean: Session,
) -> None:
    """REB-943, REB-945: `email` is the login address and `phone` the primary phone point,
    as before; `contact_points` is every stored point (extra emails, every phone, LinkedIn
    points), never the login address."""
    company = _create(clean, project="Un gestionale.")
    contact = clean.get(User, company.user_id)
    assert contact is not None
    ContactPointService(clean).replace(
        contact,
        [
            ContactPointInput(kind="email", value="mario.casa@gmail.com", label="personal"),
            ContactPointInput(kind="phone", value="+39 333 1234567", label="work"),
            ContactPointInput(
                kind="phone",
                value="02 5550199",
                label="billing",
                note="centralino",
                is_primary=True,
            ),
            ContactPointInput(
                kind="linkedin", value="https://www.linkedin.com/in/mario-rossi", label="work"
            ),
        ],
    )
    clean.commit()
    http = RecordedPigro([OK])

    _service(clean, http, RecordingCall([_draft_response(**DRAFT)])).link(company.id)

    assert _body(http)["contact"] == {
        "first_name": "Mario",
        "last_name": "Rossi",
        "email": "mario@acme.it",
        "phone": "02 5550199",
        "linkedin_url": None,
        "contact_points": [
            _point("email", "mario.casa@gmail.com", "personal"),
            _point("phone", "+39 333 1234567", "work"),
            _point("phone", "02 5550199", "billing", note="centralino", primary=True),
            _point("linkedin", "https://www.linkedin.com/in/mario-rossi", "work", primary=True),
        ],
    }


def test_a_point_the_crm_would_refuse_is_left_out_and_never_cut() -> None:
    """The columns hold what the CRM's schema takes, so this is the guard a value from
    outside them (or a wider column one day) meets: left out whole, never shortened."""

    def point(value: str, note: str | None = None) -> UserContactPoint:
        return UserContactPoint(
            kind="email", value=value, label="work", note=note, is_primary=False
        )

    kept = point("ok@acme.it", "centralino")
    sent = contact_point_payload(
        [
            point("m" * 330 + "@acme.it"),
            point("nul\x00@acme.it"),
            point("note@acme.it", "x" * 201),
            point("nulnote@acme.it", "a\x00b"),
            point("   "),
            kept,
        ]
    )

    assert sent == [_point("email", "ok@acme.it", "work", note="centralino")]
    assert len(contact_point_payload([point(f"p{n}@acme.it") for n in range(40)])) == 30


def test_a_contact_with_no_points_sends_an_empty_list(clean: Session) -> None:
    company = _create(clean)
    contact = clean.get(User, company.user_id)
    assert contact is not None
    ContactPointService(clean).replace(contact, [])
    clean.commit()
    http = RecordedPigro([OK])

    _service(clean, http, RecordingCall([_draft_response(**DRAFT)])).link(company.id)

    assert _body(http)["contact"]["contact_points"] == []


def test_the_model_sees_the_four_fields_and_never_the_company_or_the_contact_person(
    clean: Session,
) -> None:
    company = _create(clean)
    llm = RecordingCall([_draft_response(**DRAFT)])

    _service(clean, RecordedPigro([OK]), llm).link(company.id)

    request: LlmRequest = llm.requests[0]
    sent = request.system + "".join(m.text for m in request.messages)
    for secret in ("Acme", "Mario", "Rossi", "mario@acme.it", "1234567", "400"):
        assert secret not in sent
    for seen in ("Backend developer", "Portare un gestionale", "3 mesi", "2026-12-01"):
        assert seen in sent


def test_no_database_lock_is_held_while_the_ai_or_the_crm_answers(
    clean: Session, hub_engine: Engine
) -> None:
    company = _create(clean)
    held: list[str] = []

    def probe(stage: str) -> None:
        """A second connection locks the row with NOWAIT: it fails if `link` still holds it."""
        with hub_engine.connect() as other:
            try:
                other.execute(
                    text("SELECT id FROM companies WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": company.id},
                )
            except OperationalError:
                held.append(stage)
            other.rollback()

    class ProbedLlm:
        def complete(self, request: LlmRequest) -> LlmResponse:
            probe("ai")
            return _draft_response(**DRAFT)

    class ProbedHttp(RecordedPigro):
        def __call__(self, *args: Any) -> tuple[int, bytes]:
            probe("http")
            return super().__call__(*args)

    _service(clean, ProbedHttp([OK]), ProbedLlm()).link(company.id)  # type: ignore[arg-type]

    assert held == []


def test_the_draft_is_made_once_and_kept_for_every_retry(clean: Session) -> None:
    company = _create(clean)
    llm = RecordingCall([_draft_response(**DRAFT)])
    http = RecordedPigro([(503, b'{"detail": "Spazio dei lead non configurato"}'), OK])
    service = _service(clean, http, llm)

    first = service.link(company.id)
    assert first.status == "error"
    kept = _row(clean, company.id).crm_draft
    assert kept is not None and kept["role"] == "Sviluppatore backend"

    second = service.link(company.id)

    assert second.status == "linked"
    assert len(llm.requests) == 1  # the cap is not spent again
    assert _body(http, 1)["deal"]["name"] == "Acme S.r.l.: Sviluppatore backend"


@pytest.mark.parametrize(
    "llm",
    [
        None,
        RecordingCall([]),  # raises IndexError: a provider that fell over
    ],
    ids=["no seam", "raising"],
)
def test_without_a_draft_the_filing_goes_ahead(clean: Session, llm: LlmCall | None) -> None:
    company = _create(clean)
    http = RecordedPigro([OK])

    assert _service(clean, http, llm).link(company.id).status == "linked"

    deal = _body(http)["deal"]
    assert deal["name"] == "Acme S.r.l.: Backend developer"
    assert deal["value_lines"] is None
    assert "Nessuna bozza AI" in deal["notes"]
    assert _row(clean, company.id).crm_draft is None  # asked again at the next attempt


def test_a_cap_reached_files_without_a_draft(clean: Session) -> None:
    company = _create(clean)

    class Capped:
        def complete(self, request: LlmRequest) -> LlmResponse:
            raise LlmCapReached("Tetto giornaliero raggiunto.")

    http = RecordedPigro([OK])
    assert _service(clean, http, Capped()).link(company.id).status == "linked"
    assert "Nessuna bozza AI" in _body(http)["deal"]["notes"]


def _line(period: str, quantity: str, months: int | None, price: str = "400.00") -> dict[str, Any]:
    return {
        "unit": "day",
        "delivery_period": period,
        "unit_price": price,
        "quantity": quantity,
        "start": "2026-12-01",
        "duration_months": months,
    }


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        (DRAFT, _line("one_off", "72.00", 3)),
        ({**DRAFT, "week_days": None}, None),
        ({**DRAFT, "week_days": 9}, None),  # out of bounds, dropped by the draft
        ({**DRAFT, "week_days": 2.5}, _line("one_off", "60.00", 3)),
        # No duration: a monthly open-ended line, 3 days x 2 people x 52 / 12 a month.
        ({**DRAFT, "duration_weeks": None}, _line("monthly", "26.00", None)),
        ({**DRAFT, "duration_weeks": None, "week_days": 2.5}, _line("monthly", "21.67", None)),
        # The months are the weeks' months rounded up: 12 weeks 3, 14 weeks 4, one week 1.
        ({**DRAFT, "duration_weeks": 14}, _line("one_off", "84.00", 4)),
        ({**DRAFT, "duration_weeks": 1}, _line("one_off", "6.00", 1)),
        ({**DRAFT, "duration_weeks": 52}, _line("one_off", "312.00", 12)),
    ],
)
def test_the_value_is_one_day_line(
    clean: Session, draft: dict[str, Any], expected: dict[str, Any] | None
) -> None:
    company = _create(clean)
    http = RecordedPigro([OK])

    _service(clean, http, RecordingCall([_draft_response(**draft)])).link(company.id)

    deal = _body(http)["deal"]
    assert "expected_value" not in deal
    assert deal["value_lines"] == (None if expected is None else [expected])


def test_a_line_starts_on_the_requested_date_even_when_it_is_past(clean: Session) -> None:
    company = _create(clean, period_from=TODAY - timedelta(days=30))
    http = RecordedPigro([OK])

    _service(clean, http, RecordingCall([_draft_response(**DRAFT)])).link(company.id)

    deal = _body(http)["deal"]
    assert deal["expected_close_date"] is None
    assert deal["value_lines"][0]["start"] == (TODAY - timedelta(days=30)).isoformat()


@pytest.mark.parametrize(
    ("budget", "headcount", "draft"),
    [
        # The line's total does not fit Numeric(12, 2).
        (Decimal("99999999.99"), 1000, DRAFT),
        (Decimal("99999999.99"), 1000, {**DRAFT, "duration_weeks": None}),
        # The quantity does not fit Numeric(8, 2).
        (Decimal("1"), 2000, {**DRAFT, "week_days": 7, "duration_weeks": 104}),
    ],
    ids=["total", "monthly total", "quantity"],
)
def test_a_line_that_does_not_fit_the_crm_is_not_sent(
    clean: Session, budget: Decimal, headcount: int, draft: dict[str, Any]
) -> None:
    company = _create(clean)
    company.daily_budget = budget
    company.headcount = headcount
    clean.commit()
    http = RecordedPigro([OK])

    _service(clean, http, RecordingCall([_draft_response(**draft)])).link(company.id)

    assert _body(http)["deal"]["value_lines"] is None


def test_the_total_guard_rounds_once_over_every_occurrence_as_the_crm_does(
    clean: Session,
) -> None:
    # 22 people x 1.1 days a week x 52 / 12 is 104.87 days a month. At 7,946,346.27 a day
    # the CRM's total, rounded once over its 12 occurrences, is 10,000,000,000.02 and it
    # refuses the line; rounding the month first and multiplying by 12 would have let it
    # through. One cent less a day fits.
    refused, fits = _create(clean), _create(clean)
    for company, budget in ((refused, "7946346.27"), (fits, "7946346.26")):
        company.daily_budget = Decimal(budget)
        company.headcount = 22
    clean.commit()
    http = RecordedPigro([OK, OK])
    draft = {**DRAFT, "week_days": 1.1, "duration_weeks": None}

    _service(clean, http, RecordingCall([_draft_response(**draft)])).link(refused.id)
    _service(clean, http, RecordingCall([_draft_response(**draft)])).link(fits.id)

    assert _body(http, 0)["deal"]["value_lines"] is None
    [line] = _body(http, 1)["deal"]["value_lines"]
    assert (line["quantity"], line["unit_price"]) == ("104.87", "7946346.26")


@pytest.mark.parametrize(
    ("start", "duration", "sent"),
    [
        (date(9999, 9, 1), "3 mesi", True),  # runs to 9999-11-30
        (date(9999, 10, 1), "3 mesi", False),  # the CRM's date would be in year 10000
        (date(9999, 1, 1), None, False),  # open-ended: counted at 12 months, 10000-01-01
        (date(9998, 12, 31), None, True),
    ],
)
def test_a_line_that_would_run_past_year_9999_is_not_sent(
    clean: Session, start: date, duration: str | None, sent: bool
) -> None:
    company = _create(clean, period_from=start)
    http = RecordedPigro([OK])
    draft = DRAFT if duration else {**DRAFT, "duration_weeks": None}

    _service(clean, http, RecordingCall([_draft_response(**draft)])).link(company.id)

    assert (_body(http)["deal"]["value_lines"] is not None) is sent


def test_the_closing_date_is_the_start_only_when_it_is_in_the_future(clean: Session) -> None:
    future, past = _create(clean), _create(clean, period_from=TODAY)
    http = RecordedPigro([OK])

    _service(clean, http).link(future.id)
    _service(clean, http).link(past.id)

    assert _body(http, 0)["deal"]["expected_close_date"] == "2026-12-01"
    assert _body(http, 1)["deal"]["expected_close_date"] is None


def test_the_deal_name_is_cut_to_the_crms_limit(clean: Session) -> None:
    """The hub allows 200 characters of company and 160 of role, the CRM 255 for the name
    that joins them: cut, never a permanent 422."""
    company = _create(clean, company_name="A" * 200, requested_role="B" * 160)
    http = RecordedPigro([OK])

    _service(clean, http).link(company.id)

    body = _body(http)
    assert len(body["company"]) == 200
    assert body["deal"]["name"] == ("A" * 200 + ": " + "B" * 160)[:255]


@pytest.mark.parametrize(
    ("answer", "status", "sentence"),
    [
        (
            (422, b'{"detail": "deal_id: non valido"}'),
            "rejected",
            "Il CRM ha rifiutato la richiesta: deal_id: non valido.",
        ),
        (
            (503, b'{"detail": "Spazio dei lead non configurato"}'),
            "error",
            "Il CRM non ha risposto: Spazio dei lead non configurato.",
        ),
        ((500, b""), "error", "Il CRM non ha risposto: HTTP 500."),
        ((401, b""), "error", "Il CRM non ha risposto: HTTP 401."),
        ((200, b"not json"), "error", "Il CRM non ha risposto: risposta non leggibile."),
        (
            (201, json.dumps({"customer_id": str(CUSTOMER), "deal_id": str(DEAL)}).encode()),
            "error",
            "Il CRM non ha risposto: risposta non leggibile.",
        ),
        (
            TimeoutError("slow"),
            "error",
            f"Il CRM non ha risposto: {CAUSE_TIMEOUT}.",
        ),
    ],
    ids=["422", "503", "500", "401", "garbage", "no url", "timeout"],
)
def test_what_the_crm_refuses_or_fails_is_written_with_its_sentence(
    clean: Session, answer: Any, status: str, sentence: str
) -> None:
    company = _create(clean)

    result = _service(clean, RecordedPigro([answer])).link(company.id)

    assert result.status == status
    row = _row(clean, company.id)
    assert row.crm_status == status and row.crm_deal_id is None
    from rebase_core.crm_words import crm_state_sentence

    assert crm_state_sentence(row.crm_status, row.crm_error) == sentence


def test_a_crm_that_is_not_https_is_never_asked(clean: Session) -> None:
    company = _create(clean)
    http = RecordedPigro([OK])

    result = _service(clean, http, settings=_settings(url="http://pigro.test")).link(company.id)

    assert (result.status, result.error, http.calls) == ("error", HTTPS_ONLY, [])


def test_without_the_token_nothing_is_sent_and_the_request_waits(clean: Session) -> None:
    company = _create(clean)
    http = RecordedPigro([OK])

    result = _service(clean, http, settings=_settings(token="")).link(company.id)

    assert (result.status, http.calls) == ("to_link", [])
    assert _row(clean, company.id).crm_attempted_at is None


def test_to_contact_person_with_no_name_is_an_error_not_to_refusal(clean: Session) -> None:
    company = _create(clean)
    user = clean.get(User, company.user_id)
    assert user is not None
    user.first_name = " "
    clean.commit()
    http = RecordedPigro([OK])

    result = _service(clean, http).link(company.id)

    assert result.status == "error" and http.calls == []
    assert result.error == "Il referente non ha un nome: completalo e riprova."


def test_a_request_linked_by_another_caller_meanwhile_keeps_its_link(clean: Session) -> None:
    company = _create(clean)
    other = uuid4()

    class Racing(RecordedPigro):
        def __call__(self, *args: Any) -> tuple[int, bytes]:
            with_session = clean.get(Company, company.id)
            assert with_session is not None
            with_session.crm_status = "linked"
            with_session.crm_deal_id = other
            clean.commit()
            return super().__call__(*args)

    result = _service(clean, Racing([(503, b"")])).link(company.id)

    assert result.status == "linked"
    assert _row(clean, company.id).crm_deal_id == other


def test_a_soft_deleted_request_is_not_linked(clean: Session) -> None:
    company = _create(clean)
    company.deleted_at = NOW
    clean.commit()
    http = RecordedPigro([OK])

    result = _service(clean, http).link(company.id)

    assert (result.status, http.calls) == ("to_link", [])
    assert _row(clean, company.id).crm_attempted_at is None


def test_an_unknown_request_is_not_found(clean: Session) -> None:
    with pytest.raises(NotFound):
        _service(clean, RecordedPigro([OK])).link(uuid4())


def test_the_sweep_files_waiting_and_failed_requests_and_counts_them(clean: Session) -> None:
    waiting = _create(clean)
    failed = _create(clean, email="anna@acme.it")
    refused = _create(clean, email="carlo@acme.it")
    old = _create(clean, email="dario@acme.it")
    fresh = _create(clean, email="eva@acme.it")
    broken = _create(clean, email="fabio@acme.it")
    for row, status in ((failed, "error"), (refused, "rejected")):
        row.crm_status = status
    old.crm_status = None  # written before the link existed
    for row in (waiting, failed, refused, old, broken):
        row.created_at = NOW - timedelta(hours=1)
    fresh.created_at = NOW - timedelta(minutes=1)  # the first attempt may still be running
    clean.commit()
    http = RecordedPigro([OK, (503, b""), OK])

    count = _service(clean, http).link_pending()

    asked = {c[1].rsplit("/", 1)[1] for c in http.calls}
    assert asked == {str(waiting.id), str(failed.id), str(broken.id)}
    assert (count.linked, count.failed) == (2, 1)
    for row in (refused, old, fresh):
        assert _row(clean, row.id).crm_attempted_at is None
    assert (
        _row(clean, old.id).crm_status is None and _row(clean, refused.id).crm_status == "rejected"
    )


def test_the_sweep_leaves_a_request_another_caller_is_attempting(clean: Session) -> None:
    company = _create(clean)
    company.created_at = NOW - timedelta(hours=1)
    company.crm_attempted_at = NOW - timedelta(minutes=1)
    clean.commit()
    http = RecordedPigro([OK])

    assert _service(clean, http).link_pending() == (0, 0)
    assert http.calls == []


def test_the_sweep_asks_nobody_without_the_token(clean: Session) -> None:
    company = _create(clean)
    company.created_at = NOW - timedelta(hours=1)
    clean.commit()
    http = RecordedPigro([OK])

    assert _service(clean, http, settings=_settings(token="")).link_pending() == (0, 0)
    assert http.calls == []


def test_a_failure_in_one_request_does_not_stop_the_round(clean: Session) -> None:
    first = _create(clean)
    second = _create(clean, email="anna@acme.it")
    for row in (first, second):
        row.created_at = NOW - timedelta(hours=1)
    clean.commit()
    http = RecordedPigro([OK])
    service = _service(clean, http)
    real = service.link

    def flaky(company_id: UUID, *args: Any, **kwargs: Any) -> Any:
        if company_id == first.id:
            raise RuntimeError("boom")
        return real(company_id, *args, **kwargs)

    service.link = flaky  # type: ignore[method-assign]

    assert service.link_pending() == (1, 1)


def test_retry_by_an_admin_asks_to_refused_request_again_and_signs_it(clean: Session) -> None:
    admin = User(email="ivan@rebase.it", first_name="Ivan", last_name="Sala", role="admin")
    clean.add(admin)
    company = _create(clean)
    company.crm_status = "rejected"
    company.crm_error = "deal_id: non valido"
    clean.commit()
    http = RecordedPigro([OK])

    result = _service(clean, http).link(company.id, admin.id)

    assert result.status == "linked"
    action = clean.scalars(select(AdminAction).where(AdminAction.kind == "crm_link")).one()
    assert (action.entity_type, action.entity_id, action.admin_id) == (
        "company",
        company.id,
        admin.id,
    )
    assert action.payload == {"outcome": "linked", "error": None}


def test_retry_files_to_request_from_before_the_link_existed(clean: Session) -> None:
    company = _create(clean)
    company.crm_status = None
    clean.commit()
    http = RecordedPigro([OK])

    assert _service(clean, http).link(company.id).status == "linked"


def test_linking_to_another_deal_sends_the_deal_and_asks_no_draft(clean: Session) -> None:
    company = _create(clean)
    company.crm_status = "linked"
    company.crm_deal_id = uuid4()
    clean.commit()
    other = uuid4()
    http = RecordedPigro([OK])
    llm = RecordingCall([])

    result = _service(clean, http, llm).link(company.id, deal_id=other)

    assert result.status == "linked" and llm.requests == []
    assert _body(http)["deal_id"] == str(other)
    assert _row(clean, company.id).crm_deal_id == DEAL


def test_a_refused_relink_changes_nothing_stored_and_says_why(clean: Session) -> None:
    admin = User(email="ivan@rebase.it", first_name="Ivan", last_name="Sala", role="admin")
    clean.add(admin)
    company = _create(clean)
    kept = uuid4()
    company.crm_status = "linked"
    company.crm_deal_id = kept
    company.crm_deal_url = DEAL_URL
    clean.commit()
    other = uuid4()
    http = RecordedPigro([(422, b'{"detail": "deal_id: non valido"}')])

    with pytest.raises(InvalidState, match="Il CRM ha rifiutato la richiesta: deal_id"):
        _service(clean, http).link(company.id, admin.id, deal_id=other)

    row = _row(clean, company.id)
    assert (row.crm_status, row.crm_deal_id, row.crm_error) == ("linked", kept, None)
    action = clean.scalars(select(AdminAction).where(AdminAction.kind == "crm_link")).one()
    assert action.payload["deal_id"] == str(other) and action.payload["outcome"] == "rejected"


def test_the_first_attempt_after_the_response_never_raises(
    clean: Session, hub_engine: Engine
) -> None:
    company = _create(clean)
    http = RecordedPigro([OK])
    from contextlib import closing

    from rebase_core.db import session_factory

    opener = lambda: closing(session_factory(hub_engine)())  # noqa: E731

    link_after_response(opener, _settings(), http, None, company.id)
    assert _row(clean, company.id).crm_status == "linked"

    def broken() -> Any:
        raise RuntimeError("no database")

    link_after_response(broken, _settings(), http, None, company.id)  # logged, not raised


@pytest.mark.parametrize("answer", [(503, b""), (422, b'{"detail": "x: non valido"}')])
def test_a_failure_never_takes_a_link_away(clean: Session, answer: tuple[int, bytes]) -> None:
    """«Riprova» on a request that is already linked (the MCP tool and the route allow it):
    a CRM that fails or refuses leaves the link as it was."""
    company = _create(clean)
    company.crm_status = "linked"
    company.crm_deal_id = DEAL
    company.crm_deal_url = DEAL_URL
    clean.commit()

    result = _service(clean, RecordedPigro([answer])).link(company.id)

    assert result.status == "linked"
    row = _row(clean, company.id)
    assert (row.crm_status, row.crm_deal_id, row.crm_error) == ("linked", DEAL, None)


def test_the_sweep_looks_again_under_the_lock_and_skips_what_is_taken_care_of(
    clean: Session,
) -> None:
    linked = _create(clean)
    linked.created_at = NOW - timedelta(hours=1)
    linked.crm_status = "linked"
    deleted = _create(clean, email="anna@acme.it")
    deleted.created_at = NOW - timedelta(hours=1)
    deleted.deleted_at = NOW
    busy = _create(clean, email="carlo@acme.it")
    busy.created_at = NOW - timedelta(hours=1)
    busy.crm_attempted_at = NOW - timedelta(minutes=1)
    clean.commit()
    http = RecordedPigro([OK])
    service = _service(clean, http)

    results = [service.link(c.id, sweep=True) for c in (linked, deleted, busy)]

    assert [r.asked for r in results] == [False, False, False]
    assert http.calls == []


def test_a_refused_relink_of_a_request_with_no_state_leaves_it_out_of_the_sweep(
    clean: Session,
) -> None:
    company = _create(clean)
    company.crm_status = None
    clean.commit()
    http = RecordedPigro([(503, b"")])

    with pytest.raises(InvalidState):
        _service(clean, http).link(company.id, deal_id=uuid4())

    assert _row(clean, company.id).crm_status is None


def test_a_draft_with_a_value_that_is_not_a_number_still_files() -> None:
    from rebase_core.crm_lead import LeadDraft

    out = LeadDraftOut.model_construct(
        role="Dev",
        week_days=float("nan"),
        duration_weeks=float("inf"),
        skills=[],
        profile_summary="",
    )
    draft = LeadDraft.from_output(out)
    assert (draft.role, draft.week_days, draft.duration_weeks) == ("Dev", None, None)
