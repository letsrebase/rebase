"""`MatchService.create`: what it writes (the framework agreement, the letter, the stored days,
dates and fee), the idempotent id (REB-406) and the numbering (REB-387 phase 2). Helpers:
`match_fixtures`."""

import hashlib
import json
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fakes_contracts import FailingRenderer, FakeRenderer
from match_fixtures import (
    FRAMEWORK_VERSION,
    SIGNER,
    TODAY,
    _body,
    _documents,
    _FakeOrig,
    _fiscal,
    _framework,
    _request,
    _second_card,
    _service,
    _setup,
    clean,  # noqa: F401  (fixture)
)
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rebase_core import matches as matches_module
from rebase_core.audit import AdminActionService
from rebase_core.contract_schemas import (
    CustomerDate,
    LetterFields,
    MatchCreate,
)
from rebase_core.contracts.fields import FIELD, TERM, ContractFailed
from rebase_core.contracts.render import text_path
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState
from rebase_core.matches import MatchService
from rebase_core.models import Match


def test_the_first_match_writes_the_framework_agreement_and_a_letter_that_waits_for_it(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)

    assert match.status == "draft"
    assert (match.letter.number, match.letter.status) == ("2026-001", "pending")
    framework, letter = _documents(clean, freelancer_id)
    assert (framework.kind, framework.status, framework.match_id, framework.number) == (
        "framework",
        "generated",
        None,
        None,
    )
    assert (framework.text_version, framework.draft_text) == (FRAMEWORK_VERSION, True)
    assert (letter.kind, letter.match_id) == ("letter", match.id)
    assert letter.data["data-contratto-quadro"] is None
    assert [document for document, _ in renderer.calls] == [
        "framework-agreement",
        "engagement-letter",
    ]
    assert [a.kind for a in AdminActionService(clean).timeline("match", match.id)] == [
        "match_created"
    ]


def test_the_framework_prints_the_freelancer_and_rebases_signer_and_leaves_the_signing_blanks(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer = FakeRenderer()
    _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    (framework_doc, framework), (letter_doc, letter) = renderer.calls
    assert framework["professionista-nome"] == "Ada Lovelace"
    assert framework["professionista-cf"] == "LVLDAA85T50H501Z"
    assert framework["professionista-email"] == "ada@studio.it"
    assert framework["professionista-pec"] == "non indicata"
    assert framework["rebase-rappresentante"] == "Nome Cognome"
    assert framework["luogo-firma"] == "firmato elettronicamente"
    assert framework["firma-rebase"] == "Documento emesso da rebase il 23 settembre 2026"
    assert framework["data-firma"] is None and framework["firma-professionista"] is None
    for document, data in ((framework_doc, framework), (letter_doc, letter)):
        asked = set(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        assert asked - {TERM} <= set(data), document
    assert letter["professionista-piva"] == "01234567890"
    assert letter["cliente-ragione-sociale"] == "ACME S.r.l."
    assert letter["compenso"] == 450


def test_with_an_active_framework_only_the_letter_is_written_and_it_cites_the_signature_date(
    clean: Session,
) -> None:
    """23:30 UTC on 30 September is 1 October in Rome."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC))
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.letter.status == "generated"
    assert [document for document, _ in renderer.calls] == ["engagement-letter"]
    assert renderer.calls[0][1]["data-contratto-quadro"] == "1° ottobre 2026"
    assert len(_documents(clean, freelancer_id, "framework")) == 1


@pytest.mark.parametrize("status", ["signed", "notice_served"])
def test_a_framework_with_a_notice_recorded_is_no_longer_active(
    clean: Session, status: str
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(
        clean, freelancer_id, admin_id, status=status, notice_at=datetime(2026, 11, 1, tzinfo=UTC)
    )
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert match.letter.status == "pending"
    assert [d.status for d in _documents(clean, freelancer_id, "framework")] == [
        status,
        "generated",
    ]


def test_an_unsent_framework_from_an_earlier_draft_is_replaced_not_duplicated(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    stale, current = _documents(clean, freelancer_id, "framework")
    assert [stale.status, current.status] == [
        "cancelled",
        "generated",
    ]
    entries = AdminActionService(clean).timeline("match", second.id)
    created = next(a for a in entries if a.kind == "match_created")
    assert created.payload["frameworks_cancelled"] == [str(stale.id)]


# ---- the idempotent id (REB-406) --------------------------------------------------------
def test_a_repeated_create_with_the_same_id_returns_the_match_already_written(
    clean: Session,
) -> None:
    """A retry after the server's response is lost must not write a second match with
    another letter number: the same client-generated id, sent again, returns the match
    already written rather than creating anything."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    given_id = uuid4()
    body = _body(company_id).model_copy(update={"id": given_id})

    first = service.create(freelancer_id, body, admin_id)
    second = service.create(freelancer_id, body, admin_id)

    assert first.id == second.id == given_id
    assert first.letter.number == second.letter.number
    assert len(_documents(clean, freelancer_id, "letter")) == 1
    assert [a.kind for a in AdminActionService(clean).timeline("match", first.id)] == [
        "match_created"
    ]


def test_a_repeated_id_already_used_by_another_freelancer_is_refused(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    given_id = uuid4()
    service.create(freelancer_id, _body(company_id).model_copy(update={"id": given_id}), admin_id)

    other_freelancer_id = _second_card(clean)
    _fiscal(clean, other_freelancer_id, admin_id)
    other_company_id = _request(clean, company_name="Bianchi Srl", requested_role="Designer")
    other_body = _body(other_company_id).model_copy(update={"id": given_id})

    with pytest.raises(InvalidState, match="un altro freelance"):
        service.create(other_freelancer_id, other_body, admin_id)
    assert len(_documents(clean, other_freelancer_id, "letter")) == 0


def test_two_freelancers_racing_the_same_match_id_get_a_409_not_a_500(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """REB-433: the check above (`existing is None`) reads before either session has
    committed, so two different freelancers' browsers sending the same client-generated
    id at the same moment can both read "nothing written yet" and both proceed to the
    insert; only the id's own primary key actually catches the second one. The gate
    technique of
    `test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_frameworks`:
    `next_letter_number`, the statement right after the Match itself is flushed, pauses
    the first session there, its insert still uncommitted, so the second genuinely races
    it at the database rather than merely following a thread schedule. Postgres blocks
    the second session's own insert of the same id until the first's is decided; once it
    commits, the second's raises `IntegrityError`, which `create` must turn into the same
    409 the check above gives, never an unhandled 500."""
    admin_id, freelancer_id, company_id = _setup(clean)
    other_freelancer_id = _second_card(clean)
    _fiscal(clean, other_freelancer_id, admin_id)
    other_company_id = _request(clean, company_name="Bianchi Srl", requested_role="Designer")
    given_id = uuid4()
    factory = session_factory(hub_engine)
    first, second = factory(), factory()
    paused = threading.Event()
    release = threading.Event()
    real_next_letter_number = matches_module.next_letter_number

    def paced_next_letter_number(session: Session, year: int) -> str:
        if session is first:
            paused.set()
            assert release.wait(timeout=5), "the test never released the first create()"
        return real_next_letter_number(session, year)

    monkeypatch.setattr(matches_module, "next_letter_number", paced_next_letter_number)
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []

    def run_first() -> None:
        try:
            MatchService(first, FakeRenderer(), SIGNER, today=lambda: TODAY).create(
                freelancer_id, _body(company_id).model_copy(update={"id": given_id}), admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            first_errors.append(exc)

    def run_second() -> None:
        try:
            MatchService(second, FakeRenderer(), SIGNER, today=lambda: TODAY).create(
                other_freelancer_id,
                _body(other_company_id).model_copy(update={"id": given_id}),
                admin_id,
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            second_errors.append(exc)

    try:
        first_worker = threading.Thread(target=run_first)
        first_worker.start()
        assert paused.wait(timeout=5), "the first create() never reached next_letter_number"

        second_worker = threading.Thread(target=run_second)
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), (
            "the second create() did not block on the first's uncommitted insert of the "
            "same match id"
        )

        release.set()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive()
        assert not second_worker.is_alive()
    finally:
        first.close()
        second.close()

    assert not first_errors, first_errors
    assert len(second_errors) == 1
    assert isinstance(second_errors[0], InvalidState)
    assert "un altro freelance" in second_errors[0].message
    # The second session's own id check never ran into the row at all (it read "nothing
    # written yet" too): this 409 came from the primary key's own IntegrityError, caught
    # at the flush, not from the ordinary cross-freelancer check above it.
    assert isinstance(second_errors[0].__cause__, IntegrityError)
    clean.expire_all()
    written = clean.get(Match, given_id)
    assert written is not None and written.freelancer_id == freelancer_id
    assert len(_documents(clean, other_freelancer_id, "letter")) == 0


def test_a_different_integrity_error_at_the_same_flush_still_propagates(
    monkeypatch: pytest.MonkeyPatch, clean: Session
) -> None:
    """REB-433: `create`'s catch is narrow on purpose -- only the match's own primary
    key (`matches_pkey`) is the freelancer race it exists for. An integrity error at
    the same flush over a different constraint (a foreign key gone missing
    mid-transaction, say) is not that race and must not be swallowed into "un altro
    freelance": it propagates unconverted, the admin's own failure to look into. The
    request carries an id (REB-406's idempotency key) so this actually exercises
    `_violates_match_id`'s own constraint-name check, not just the `data.id is None`
    guard ahead of it."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    real_flush = Session.flush

    def flush_raising_a_different_constraint(
        self: Session, *args: object, **kwargs: object
    ) -> None:
        if any(isinstance(obj, Match) for obj in self.new):
            raise IntegrityError(
                "INSERT INTO matches (id, ...)", {}, _FakeOrig("matches_company_id_fkey")
            )
        real_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", flush_raising_a_different_constraint)

    body = _body(company_id).model_copy(update={"id": uuid4()})
    with pytest.raises(IntegrityError):
        service.create(freelancer_id, body, admin_id)


def test_a_repeated_id_with_changed_data_is_refused_not_returned(clean: Session) -> None:
    """An admin who corrects the letter before retrying (a lost response, a second
    click) must not get the uncorrected match silently handed back: the request no
    longer matches the fingerprint stored on it, so this is a 409, and nothing changes
    on the row already written."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    given_id = uuid4()
    body = _body(company_id).model_copy(update={"id": given_id})
    first = service.create(freelancer_id, body, admin_id)

    changed = body.model_copy(
        update={"letter": body.letter.model_copy(update={"role": "Un altro ruolo"})}
    )
    with pytest.raises(InvalidState, match="dati diversi"):
        service.create(freelancer_id, changed, admin_id)

    assert len(_documents(clean, freelancer_id, "letter")) == 1
    assert service.reads.get(first.id).letter.number == first.letter.number


def test_a_repeated_id_with_no_fingerprint_stored_is_refused(clean: Session) -> None:
    """A match written before this fingerprint existed has `NULL` where the retry's
    digest would be compared against: nothing to match, so this is a 409 too, the same
    as a changed request -- never treated as a match by coincidence."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    given_id = uuid4()
    body = _body(company_id).model_copy(update={"id": given_id})
    service.create(freelancer_id, body, admin_id)
    written = clean.get(Match, given_id)
    assert written is not None
    written.request_fingerprint = None
    clean.commit()

    with pytest.raises(InvalidState, match="dati diversi"):
        service.create(freelancer_id, body, admin_id)


def test_an_ordinary_retry_across_the_release_still_finds_its_match(clean: Session) -> None:
    """A match written before REB-687 stored the digest of a request with no `regime`.
    An ordinary letter's request still hashes to that digest, so its retry across the
    release is the match returned, not a 409; an occasional one is another request."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    given_id = uuid4()
    body = _body(company_id).model_copy(update={"id": given_id})
    first = service.create(freelancer_id, body, admin_id)
    written = clean.get(Match, given_id)
    assert written is not None
    before_regime = json.dumps(
        body.model_dump(mode="json", exclude={"id", "regime"}), sort_keys=True
    )
    written.request_fingerprint = hashlib.sha256(before_regime.encode()).hexdigest()
    clean.commit()

    assert service.create(freelancer_id, body, admin_id).id == first.id
    occasional = body.model_copy(update={"regime": "occasional"})
    assert matches_module._request_fingerprint(occasional) != written.request_fingerprint


def test_create_with_no_id_still_writes_a_fresh_match_each_time(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    first = service.create(freelancer_id, _body(company_id), admin_id)
    second = service.create(freelancer_id, _body(company_id), admin_id)
    assert first.id != second.id


def test_create_stores_the_expected_days(clean: Session) -> None:
    """`expected_days` is an admin's estimate, optional (REB-497): given, it reads
    back on the match `create` returns; left out, it stays `None`. Outside 1-366 is a
    `MatchCreate` the wizard never manages to send at all."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)

    with_days = service.create(freelancer_id, _body(company_id, expected_days=40), admin_id)
    assert with_days.expected_days == 40

    without_days = service.create(freelancer_id, _body(company_id), admin_id)
    assert without_days.expected_days is None

    for invalid in (0, 367):
        with pytest.raises(PydanticValidationError):
            _body(company_id, expected_days=invalid)


def test_create_stores_the_letters_dates_and_fee(clean: Session) -> None:
    """The three `lettera_*` columns on `Match` are `create`'s own copy of what the
    letter was told (REB-497): a stable place for the report to read them from even if
    the letter itself is later regenerated. Read straight back on the `MatchRead`
    `create` returns."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    body = MatchCreate(
        company_id=company_id,
        customer=CustomerDate(
            customer_legal_name="ACME S.r.l.",
            customer_vat_number="01234567890",
            customer_registered_office="Milano",
        ),
        letter=LetterFields(
            role="Backend developer",
            activities="Le API del prodotto.",
            start_date=date(2026, 10, 1),
            end_date=date(2027, 3, 31),
            mode="to_work_unit",
            fee=Decimal("450"),
            payment_days=30,
            month_end=True,
        ),
    )

    match = service.create(freelancer_id, body, admin_id)

    assert match.letter_start_date == body.letter.start_date
    assert match.letter_end_date == body.letter.end_date
    assert match.letter_fee == body.letter.fee


def test_a_stale_draft_framework_that_never_left_stays_hidden_from_the_page(
    clean: Session,
) -> None:
    """The phase 2 case (spec § 6): the `generated` framework a second «Crea match»
    replaces never left for signature (`sent_at` stays `None`), so it stays hidden once
    `cancelled`; the page shows the current one instead."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id), admin_id)
    stale, current = _documents(clean, freelancer_id, "framework")
    assert (stale.status, stale.sent_at) == ("cancelled", None)

    framework = service.reads.for_freelancer(freelancer_id).framework

    assert framework is not None and framework.id == current.id


def test_a_framework_already_out_for_signature_is_waited_for_not_replaced(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, status="sent", signed_at=None)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(freelancer_id, _body(company_id), admin_id)
    assert match.letter.status == "pending"
    assert [document for document, _ in renderer.calls] == ["engagement-letter"]


def test_letters_are_numbered_per_year_in_the_order_they_are_written(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    numbers = [
        _service(clean, today=day).create(freelancer_id, _body(company_id), admin_id).letter.number
        for day in (date(2026, 12, 31), date(2026, 12, 31), date(2027, 1, 1))
    ]
    assert numbers == ["2026-001", "2026-002", "2027-001"]


def test_a_render_that_fails_takes_no_number_and_leaves_nothing_behind(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _service(clean).create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(ContractFailed):
        _service(clean, FailingRenderer(document="engagement-letter")).create(
            freelancer_id, _body(company_id), admin_id
        )
    assert clean.scalar(select(func.count()).select_from(Match)) == 1
    # The earlier framework was not replaced by a generation that failed.
    assert [d.status for d in _documents(clean, freelancer_id, "framework")] == ["generated"]
    assert (
        _service(clean).create(freelancer_id, _body(company_id), admin_id).letter.number
        == "2026-002"
    )
