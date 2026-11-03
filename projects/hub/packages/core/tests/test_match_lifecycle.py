"""A match once it exists: the tax data it needs, a deleted card or request, `cancel` and `close`,
and the locks two admins race on (REB-406, REB-407). Helpers: `match_fixtures`."""

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import uuid4

import pytest
from fakes_contracts import FakeRenderer
from match_fixtures import (
    SIGNER,
    TODAY,
    _admin,
    _body,
    _card,
    _documents,
    _request,
    _service,
    _setup,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from rebase_core import matches as matches_module
from rebase_core.admin_schemas import StatusChange
from rebase_core.audit import AdminActionService
from rebase_core.companies import CompanyService
from rebase_core.contract_schemas import (
    FiscalData,
)
from rebase_core.contracts.fields import Value
from rebase_core.contracts.render import Rendered
from rebase_core.db import session_factory
from rebase_core.errors import InvalidState, NotFound, ValidationFailed
from rebase_core.fiscal import FiscalService
from rebase_core.freelancers import FreelancerService
from rebase_core.matches import MatchService, require_live_document, require_live_match
from rebase_core.models import Freelancer, Match


def test_a_match_needs_the_tax_data_saved_first(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    with pytest.raises(ValidationFailed) as refused:
        _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert refused.value.details["field"] == "fiscale"


def test_a_deleted_card_or_request_is_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    CompanyService(clean).soft_delete(company_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).drafts.prefill(freelancer_id, company_id)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    with pytest.raises(NotFound):
        _service(clean).reads.for_freelancer(freelancer_id)


def test_a_document_is_not_found_once_its_freelancer_is_deleted(clean: Session) -> None:
    """The guard the admin API and the MCP tools both run before a document's action:
    «documento ... non trovato» for a document that is gone and for one whose freelancer
    is soft-deleted, never the freelancer's own message (REB-417)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    letter = _service(clean).create(freelancer_id, _body(company_id), admin_id).lettera
    assert require_live_document(clean, letter.id).id == letter.id
    missing = uuid4()
    with pytest.raises(NotFound, match=f"documento {missing} non trovato"):
        require_live_document(clean, missing)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    with pytest.raises(NotFound, match=f"documento {letter.id} non trovato"):
        require_live_document(clean, letter.id)


def test_a_match_is_not_found_once_its_freelancer_is_deleted(clean: Session) -> None:
    """The guard the admin API and the MCP tools both run before a match's action or
    read: «match ... non trovato» for a match that is gone and for one whose freelancer
    is soft-deleted, never the freelancer's own message (REB-417)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _service(clean).create(freelancer_id, _body(company_id), admin_id)
    assert require_live_match(clean, match.id).id == match.id
    missing = uuid4()
    with pytest.raises(NotFound, match=f"match {missing} non trovato"):
        require_live_match(clean, missing)
    FreelancerService(clean).soft_delete(freelancer_id, admin_id)
    with pytest.raises(NotFound, match=f"match {match.id} non trovato"):
        require_live_match(clean, match.id)


def test_cancelling_a_draft_cancels_its_letter_and_leaves_the_framework(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    cancelled = service.cancel(match.id, admin_id)
    assert (cancelled.stato, cancelled.lettera.stato) == ("annullato", "annullato")
    assert cancelled.cancelled_at is not None
    assert [d.stato for d in _documents(clean, freelancer_id, "quadro")] == ["generato"]
    with pytest.raises(InvalidState):
        service.cancel(match.id, admin_id)
    kinds = [a.kind for a in AdminActionService(clean).timeline("match", match.id)]
    assert kinds == ["match_cancelled", "match_created"]


def test_only_an_active_match_can_be_closed(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    with pytest.raises(InvalidState):
        service.close(match.id, admin_id)
    row = clean.get(Match, match.id)
    assert row is not None
    row.stato = "attivo"  # only a signed letter gets a match here (phase 3)
    clean.commit()
    assert service.close(match.id, admin_id).stato == "concluso"


def test_two_admins_matching_the_same_freelancer_at_once_never_leave_two_open_frameworks(
    monkeypatch: pytest.MonkeyPatch, hub_engine: Engine, clean: Session
) -> None:
    """REB-399: two real `create()` calls, on two sessions, for the same
    freelancer -- the shape of `test_two_letters_taken_at_once_get_two_numbers`, but for
    the framework agreement rather than the letter counter.

    Postgres itself already serializes the two `Match` inserts a beat later (both name
    the freelancer as a foreign key), so pausing an *unrelated* session on a bare
    `SELECT ... FOR UPDATE` and then calling `create()` on a second session -- blocked or
    not -- proves nothing about *this* fix: it blocks at the `Match` insert either way, fix
    or no fix, with the framework's fate already decided by then. What must actually be
    tested is that `create()`'s own transaction takes its lock *before* it ever reads
    `active_framework`/`pending_framework`, not merely somewhere before it commits.

    So this test drives two genuine `create()` calls: `next_letter_number` -- the next
    real statement after the fix's own lock, and one the fix's own docstring names by
    name -- is patched to pause the first session there, after everything upstream of it
    has already run once. The second session's `create()` is then started for real:
    without the fix it races ahead of the paused first one, reads "no active, no
    pending" same as the first did, and writes its own `generato` framework -- so when
    the first is released and finishes writing *its* framework from the same stale
    read, both commit and two open frameworks exist at once (RED). With the fix, the
    second cannot get past its own first statement (the same row lock, already held,
    uncommitted, by the first) until the first commits; only then does it see the
    first's `generato` framework, cancel it, and write its own -- exactly one open
    framework survives (GREEN)."""
    admin_id, freelancer_id, company_id = _setup(clean)
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

    errors: list[BaseException] = []

    def run(session: Session) -> None:
        try:
            MatchService(session, FakeRenderer(), SIGNER, today=lambda: TODAY).create(
                freelancer_id, _body(company_id), admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    try:
        first_worker = threading.Thread(target=run, args=(first,))
        first_worker.start()
        assert paused.wait(timeout=5), "the first create() never reached next_letter_number"

        second_worker = threading.Thread(target=run, args=(second,))
        second_worker.start()
        second_worker.join(timeout=0.5)
        assert second_worker.is_alive(), (
            "the second create() read the freelancer while the first's transaction, "
            "past the same lock, was still open"
        )

        release.set()
        first_worker.join(timeout=5)
        second_worker.join(timeout=5)
        assert not first_worker.is_alive()
        assert not second_worker.is_alive()
        assert not errors, errors

        assert [q.stato for q in _documents(clean, freelancer_id, "quadro")] == [
            "annullato",
            "generato",
        ]
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("change", ["request_closed", "tax_data_corrected"])
def test_a_create_waiting_for_the_freelancers_lock_reads_the_request_and_tax_data_after_it(
    hub_engine: Engine, clean: Session, change: str
) -> None:
    """Greptile 4092036031: `create` used to read the tax data and check the request
    before taking the freelancer's row lock, so what another admin committed while it
    waited there -- a closed request, a corrected VAT number -- never reached it. Here
    another transaction holds that lock (a tax-data save or another match does), the
    creating session has already read both through a prefill, and the change commits
    while `create` waits: it must refuse the closed request, or print the new tax
    data."""
    admin_id, freelancer_id, company_id = _setup(clean)
    factory = session_factory(hub_engine)
    holder, creator = factory(), factory()
    outcome: list[object] = []
    service = MatchService(creator, FakeRenderer(), SIGNER, today=lambda: TODAY)
    service.drafts.prefill(freelancer_id, company_id)

    def run() -> None:
        try:
            outcome.append(service.create(freelancer_id, _body(company_id), admin_id))
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            outcome.append(exc)

    worker = threading.Thread(target=run)
    try:
        holder.execute(
            select(Freelancer.id).where(Freelancer.id == freelancer_id).with_for_update()
        )
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive(), "create() did not wait for the freelancer's row lock"

        # Each commits the holder's transaction, which releases the lock.
        if change == "request_closed":
            CompanyService(holder).set_status(
                company_id, StatusChange(stato="chiuso"), admin_id=admin_id, fonte="web"
            )
        else:
            FiscalService(holder).save(
                freelancer_id,
                FiscalData(
                    codice_fiscale="LVLDAA85T50H501Z",
                    partita_iva="09876543210",
                    domicilio="Corso Buenos Aires 2, Milano",
                ),
                admin_id,
            )
        worker.join(timeout=5)
        assert not worker.is_alive()

        [result] = outcome
        if change == "request_closed":
            assert isinstance(result, ValidationFailed), result
            assert result.details["field"] == "company_id"
            assert clean.scalar(select(func.count()).select_from(Match)) == 0
        else:
            assert not isinstance(result, BaseException), result
            quadro, lettera = _documents(clean, freelancer_id)
            assert quadro.data["professionista-piva"] == "09876543210"
            assert quadro.data["professionista-domicilio"] == "Corso Buenos Aires 2, Milano"
            assert lettera.data["professionista-piva"] == "09876543210"
    finally:
        holder.close()
        if worker.is_alive():
            worker.join(timeout=5)
        creator.close()


def test_closing_a_request_while_its_match_is_typeset_waits_for_the_match(
    hub_engine: Engine, clean: Session
) -> None:
    """Greptile 4092036031, the other half: the request is read under a shared lock
    that lasts to the commit, so an admin closing it while `create` typesets waits, and
    the draft is never saved for a request already closed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    factory = session_factory(hub_engine)
    creator, closer = factory(), factory()
    rendering, release = threading.Event(), threading.Event()

    @dataclass
    class PausedRenderer(FakeRenderer):
        def render(self, document: str, data: Mapping[str, Value]) -> Rendered:
            if not rendering.is_set():
                rendering.set()
                assert release.wait(timeout=5), "the test never released the render"
            return super().render(document, data)

    errors: list[BaseException] = []

    def create() -> None:
        try:
            MatchService(creator, PausedRenderer(), SIGNER, today=lambda: TODAY).create(
                freelancer_id, _body(company_id), admin_id
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    def close() -> None:
        try:
            CompanyService(closer).set_status(
                company_id, StatusChange(stato="chiuso"), admin_id=admin_id, fonte="web"
            )
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            errors.append(exc)

    creating, closing = threading.Thread(target=create), threading.Thread(target=close)
    try:
        creating.start()
        assert rendering.wait(timeout=5), "create() never reached the render"

        closing.start()
        closing.join(timeout=0.5)
        assert closing.is_alive(), "the request was closed while its match was being typeset"

        release.set()
        creating.join(timeout=5)
        closing.join(timeout=5)
        assert not creating.is_alive()
        assert not closing.is_alive()
        assert not errors, errors
        assert clean.scalar(select(func.count()).select_from(Match)) == 1
    finally:
        release.set()
        for worker in (creating, closing):
            if worker.is_alive():
                worker.join(timeout=5)
        creator.close()
        closer.close()
