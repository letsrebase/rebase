"""REB-392, spec § 4: the member area's «Contratti», a freelancer's own documents and
nobody else's. The admin side is `test_signing.py`, whose helpers this file borrows."""

from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_matches import _second_card
from test_signing import (
    SIGNED_AT,
    TABLES,
    _draft,
    _envelope_of,
    _framework_of,
    _setup,
    _signing,
    _webhook,
)

from rebase_core.contract_schemas import MemberContracts
from rebase_core.errors import NotFound
from rebase_core.mail import RecordingSender
from rebase_core.member_contracts import MemberContractService
from rebase_core.models import Freelancer

# The day after the signature: the next renewal is a year on.
AFTER = date(2026, 10, 2)


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _user_of(session: Session, freelancer_id: UUID) -> UUID:
    freelancer = session.get(Freelancer, freelancer_id)
    assert freelancer is not None
    return freelancer.user_id


def _service(session: Session) -> MemberContractService:
    return MemberContractService(session, today=lambda: AFTER)


def test_the_freelancer_sees_what_reached_them_and_the_link_to_sign(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    quadro = _framework_of(clean, freelancer_id)
    assert mine.quadro is not None
    assert (mine.quadro.id, mine.quadro.stato) == (quadro.id, "inviato")
    assert mine.quadro.signing_url == quadro.signing_url
    [lettera] = mine.lettere
    assert (lettera.numero, lettera.stato, lettera.cliente) == (
        match.lettera.numero,
        "in_attesa",
        "ACME S.r.l.",
    )
    assert lettera.inizio == "1° ottobre 2026"
    assert lettera.signing_url is None
    assert mine.quadri_precedenti == []


def test_a_draft_match_shows_nothing(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _draft(clean, FakeRenderer(draft=False), freelancer_id, company_id, admin_id)
    assert _service(clean).for_user(_user_of(clean, freelancer_id)) == MemberContracts(
        quadro=None, lettere=[]
    )


def test_a_signed_framework_shows_its_dates_its_copy_and_no_link(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.finish(signed)
    user_id = _user_of(clean, freelancer_id)

    mine = _service(clean).for_user(user_id)

    assert mine.quadro is not None
    assert (mine.quadro.stato, mine.quadro.attivo, mine.quadro.signing_url) == (
        "firmato",
        True,
        None,
    )
    assert mine.quadro.ha_pdf_firmato is True
    assert (mine.quadro.rinnovo, mine.quadro.ultimo_giorno_disdetta) == (
        date(2027, 10, 1),
        date(2027, 9, 1),
    )
    copy = _service(clean).signed_pdf(user_id, signed)
    assert copy.content == fake.signed_pdf(envelope)
    assert copy.filename == "contratto-quadro-v0.1-firmato.pdf"
    [lettera] = mine.lettere
    assert lettera.stato == "inviato" and lettera.signing_url is not None


def test_a_cancelled_document_says_so_and_offers_no_link(clean: Session) -> None:
    """Probe § 11.10: Documenso's page still opens a cancelled document and fails only at
    the click. The member area is where the person reads that it is not to be signed."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signing.send_match(match.id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "Il domicilio è sbagliato")
    signing.apply(_webhook(fake, envelope, "DOCUMENT_REJECTED"))

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.quadro is not None
    assert (mine.quadro.stato, mine.quadro.signing_url) == ("annullato", None)


def test_a_notice_moves_the_signed_framework_under_the_new_one_instead_of_hiding_it(
    clean: Session,
) -> None:
    """REB-392: a notice on the first framework agreement, then a second match writes
    and signs a new one. The person still sees the first, signed copy and all -- moved
    under the current one, not gone."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    first_match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing = _signing(clean, renderer, fake, sender)
    signing.send_match(first_match.id, admin_id)
    first_envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(first_envelope, SIGNED_AT)
    first_signed = signing.apply(_webhook(fake, first_envelope, "DOCUMENT_COMPLETED"))
    assert first_signed is not None
    signing.finish(first_signed)
    first_quadro = _framework_of(clean, freelancer_id)
    signing.record_notice(first_quadro.id, admin_id)

    second_match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(second_match.id, admin_id)
    second_envelope = _envelope_of(_framework_of(clean, freelancer_id))
    second_signed_at = SIGNED_AT.replace(month=11)
    fake.sign(second_envelope, second_signed_at)
    second_signed = signing.apply(_webhook(fake, second_envelope, "DOCUMENT_COMPLETED"))
    assert second_signed is not None
    signing.finish(second_signed)
    second_quadro = _framework_of(clean, freelancer_id)
    assert second_quadro.id != first_quadro.id

    mine = _service(clean).for_user(_user_of(clean, freelancer_id))

    assert mine.quadro is not None and mine.quadro.id == second_quadro.id
    [previous] = mine.quadri_precedenti
    assert previous.id == first_quadro.id
    assert previous.stato == "disdetto"
    assert previous.ha_pdf_firmato is True
    copy = _service(clean).signed_pdf(_user_of(clean, freelancer_id), first_quadro.id)
    assert copy.content == fake.signed_pdf(first_envelope)


def test_someone_elses_document_and_an_unsigned_copy_are_not_found(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    grace_id = _second_card(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, RecordingSender()).send_match(match.id, admin_id)
    quadro = _framework_of(clean, freelancer_id)
    grace, ada = _user_of(clean, grace_id), _user_of(clean, freelancer_id)

    with pytest.raises(NotFound):
        _service(clean).signed_pdf(grace, quadro.id)
    with pytest.raises(NotFound):
        _service(clean).signed_pdf(ada, quadro.id)
    assert _service(clean).for_user(grace) == MemberContracts(quadro=None, lettere=[])


def test_a_person_without_a_card_has_no_contracts(clean: Session) -> None:
    admin_id, _freelancer_id, _company_id = _setup(clean)
    with pytest.raises(NotFound, match="scheda"):
        _service(clean).for_user(admin_id)
