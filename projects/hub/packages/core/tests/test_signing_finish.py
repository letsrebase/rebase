"""What a signature leaves to do (REB-391): the sealed copy, the two mails each recorded on its own,
the waiting letters a framework agreement releases, and a rejection or a cancellation. Helpers:
`signing_fixtures`."""

import threading
from datetime import date

from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from match_fixtures import (
    QUADRO_VERSION,
    _setup,
)
from signing_fixtures import (
    CONTRACTS_MAIL,
    NOW,
    SIGNED_AT,
    RefusingRecipientSender,
    RefusingSender,
    _active_framework,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _sent,
    _signing,
    _webhook,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from rebase_core.audit import AdminActionService
from rebase_core.db import session_factory
from rebase_core.documenso import (
    Outcome,
)
from rebase_core.mail import RecordingSender
from rebase_core.models import ContractDocument, Match


def test_finish_downloads_and_mails_the_signed_copy_once(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    before = len(sender.sent)

    signing.recovery.finish(signed)
    signing.recovery.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    copies = [mail for mail in sender.sent[before:] if mail.attachments]
    assert [(mail.to, mail.subject) for mail in copies] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]
    assert all(
        mail.attachments[0].filename == f"contratto-quadro-v{QUADRO_VERSION}-firmato.pdf"
        and mail.attachments[0].content == quadro.signed_pdf
        for mail in copies
    )


def test_a_download_that_fails_is_done_by_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("download", 500, "Internal server error")

    signing.recovery.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf is None

    signing.recovery.finish(signed)
    assert _framework_of(clean, freelancer_id).signed_pdf == fake.signed_pdf(envelope)


def test_a_framework_signed_late_at_night_releases_its_letter_with_the_rome_date(
    clean: Session,
) -> None:
    """REB-391: signed at 23:30 UTC on 30 September, which is 1 October in Rome. The
    letter that waited leaves on its own, citing that date, mailed as its own, and its
    own trail entry, as `send_match` leaves for a letter it sends itself."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender, today=date(2026, 10, 1))
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.recovery.finish(signed)

    letter = _letter_of(clean, match.id)
    assert letter.stato == "inviato"
    assert letter.data["data-contratto-quadro"] == "1° ottobre 2026"
    assert letter.data["firma-rebase"] == "Documento emesso da rebase il 1° ottobre 2026"
    assert letter.sent_by == admin_id
    assert sender.sent[-1].subject == f"Da firmare: lettera di incarico n. {letter.numero}"
    assert len(fake.envelopes) == 2
    trail = AdminActionService(clean).timeline("match", match.id)
    assert trail[0].kind == "documents_sent"
    assert trail[0].payload == {"documento": str(letter.id), "kind": "lettera", "mail": True}


def test_a_released_letters_refused_mail_still_names_it_in_the_trail(clean: Session) -> None:
    """REB-391: the mail provider refusing a released letter's link is not only logged
    (as `send_match`'s own refusal is not): the trail entry says `mail: false`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent(clean, renderer, fake, RecordingSender(), freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, RefusingSender())
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.recovery.finish(signed)

    letter = _letter_of(clean, match.id)
    assert letter.stato == "inviato"
    trail = AdminActionService(clean).timeline("match", match.id)
    assert trail[0].payload == {"documento": str(letter.id), "kind": "lettera", "mail": False}


def test_a_release_documenso_refuses_waits_for_the_next_finish(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    fake.fail("create", 500, "Internal server error")

    signing.recovery.finish(signed)
    assert _letter_of(clean, match.id).stato == "in_attesa"

    signing.recovery.finish(signed)
    assert _letter_of(clean, match.id).stato == "inviato"


def test_a_draft_matchs_letter_is_not_released(clean: Session) -> None:
    """Only a match an admin sent is released; a draft's letter leaves with its match."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    sent = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    draft = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.recovery.finish(signed)

    assert _letter_of(clean, sent.id).stato == "inviato"
    assert _letter_of(clean, draft.id).stato == "in_attesa"


def test_a_signed_letter_turns_its_match_active(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_letter_of(clean, match.id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.recovery.finish(signed)

    clean.expire_all()
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]
    assert _letter_of(clean, match.id).stato == "firmato"


def test_a_refused_document_is_cancelled_with_the_freelancers_reason(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.reject(envelope, "La PEC indicata non è la mia")

    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_REJECTED")
    )

    quadro = _framework_of(clean, freelancer_id)
    assert signed is None
    assert (quadro.stato, quadro.cancel_reason) == (
        "annullato",
        "Rifiutato dal freelance: La PEC indicata non è la mia",
    )
    # The letter keeps waiting: «Invia per la firma» on its match writes a new framework.
    assert _letter_of(clean, match.id).stato == "in_attesa"


def test_a_cancellation_on_documenso_cancels_and_a_late_event_is_ignored(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.client().cancel(envelope, "Annullato a mano.")
    signing = _signing(clean, renderer, fake, sender)

    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_CANCELLED")) is None
    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.stato, quadro.cancel_reason) == ("annullato", "Annullato su Documenso.")
    # An envelope already cancelled, or one the hub never recorded, moves nothing.
    fake.sign(envelope, SIGNED_AT)
    assert signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED")) is None
    assert _framework_of(clean, freelancer_id).stato == "annullato"
    assert signing.apply(Outcome("envelope_sconosciuto", "completed", SIGNED_AT)) is None


def test_a_release_with_no_mail_sender_leaves_the_letters_waiting(clean: Session) -> None:
    """REB-391: `send_match` refuses up front without a mail sender, so a release
    must not dispatch a letter to Documenso that nobody could then be told about. The
    sender is checked before Documenso is touched, not after."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    match = _sent(clean, renderer, fake, RecordingSender(), freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, RecordingSender())
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    envelopes_before = len(fake.envelopes)

    _signing(clean, renderer, fake, None).recovery.finish(signed)

    assert _letter_of(clean, match.id).stato == "in_attesa"
    assert len(fake.envelopes) == envelopes_before


def test_a_refused_signed_copy_mail_is_retried_by_the_next_finish_and_then_not_again(
    clean: Session,
) -> None:
    """REB-391: the download succeeded, both signed-copy mails were refused. Neither
    column is set, so the next `finish` -- with a sender that now works -- sends the
    pair again; a third `finish` after that sends nothing more."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    refusing = RefusingSender()
    _sent(clean, renderer, fake, refusing, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    refused_signing = _signing(clean, renderer, fake, refusing)
    signed = refused_signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    refused_signing.recovery.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_pdf == fake.signed_pdf(envelope)
    assert (quadro.signed_copy_to_freelancer_at, quadro.signed_copy_to_rebase_at) == (None, None)
    first_attempt = [mail for mail in refusing.sent if mail.attachments]
    assert len(first_attempt) == 2

    recording = RecordingSender()
    working_signing = _signing(clean, renderer, fake, recording)
    working_signing.recovery.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert (quadro.signed_copy_to_freelancer_at, quadro.signed_copy_to_rebase_at) == (NOW, NOW)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    retried = [mail for mail in recording.sent if mail.attachments]
    assert [(mail.to, mail.subject) for mail in retried] == [
        ("ada@studio.it", "Firmato: contratto quadro rebase"),
        (CONTRACTS_MAIL, "Firmato da Ada Lovelace: contratto quadro rebase"),
    ]

    working_signing.recovery.finish(signed)

    assert [mail for mail in recording.sent if mail.attachments] == retried


def test_one_recipient_refusing_forever_does_not_stop_the_others_mail(clean: Session) -> None:
    """REB-391: the freelancer's provider keeps rejecting their mail; rebase's own
    address is still mailed, exactly once, on the first `finish` that succeeds for it,
    and never mailed again however many times a later sweep retries the freelancer's."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    sender = RefusingRecipientSender("ada@studio.it")
    _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    signing.recovery.finish(signed)
    signing.recovery.finish(signed)
    signing.recovery.finish(signed)

    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_copy_to_freelancer_at is None
    assert quadro.signed_copy_to_rebase_at == NOW
    to_rebase = [mail for mail in sender.sent if mail.to == CONTRACTS_MAIL and mail.attachments]
    assert len(to_rebase) == 1
    to_freelancer = [
        mail for mail in sender.sent if mail.to == "ada@studio.it" and mail.attachments
    ]
    assert len(to_freelancer) == 3


def test_the_retry_mails_only_the_recipient_that_had_failed(clean: Session) -> None:
    """REB-391: once the freelancer's provider starts accepting mail again, the next
    `finish` mails them alone -- rebase already got theirs, and must not get a second
    copy just because the freelancer's kept failing."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake = FakeRenderer(draft=False), FakeDocumenso()
    refusing = RefusingRecipientSender("ada@studio.it")
    _sent(clean, renderer, fake, refusing, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    refused_signing = _signing(clean, renderer, fake, refusing)
    signed = refused_signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    refused_signing.recovery.finish(signed)
    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_copy_to_freelancer_at is None
    assert quadro.signed_copy_to_rebase_at == NOW

    recording = RecordingSender()
    working_signing = _signing(clean, renderer, fake, recording)
    working_signing.recovery.finish(signed)

    retried = [mail for mail in recording.sent if mail.attachments]
    assert [mail.to for mail in retried] == ["ada@studio.it"]
    quadro = _framework_of(clean, freelancer_id)
    assert quadro.signed_copy_to_freelancer_at == NOW
    assert quadro.signed_copy_to_rebase_at == NOW


def test_two_concurrent_finishes_download_and_mail_the_signed_copy_once(
    hub_engine: Engine, clean: Session
) -> None:
    """REB-391: the webhook's own `finish` and an admin's «Aggiorna stato»
    refresh can overlap on the same freshly signed document. `_store_signed_copy`'s row
    lock serialises them: exactly one download, and exactly one pair of signed-copy
    mails (to the freelancer and to rebase), however many callers race for it.

    Committing `firmato` directly, rather than through `apply` then `finish`'s own
    confirmation (REB-431), keeps this test about `_store_signed_copy`'s own lock: a
    document still `inviato` would instead serialise the two calls inside
    `_confirm_completion`, at the freelancer's row, before either ever reaches
    `_store_signed_copy`."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)
    letter = _letter_of(clean, match.id)
    envelope = _envelope_of(letter)
    fake.sign(envelope, SIGNED_AT)
    signed = _signing(clean, renderer, fake, sender).apply(
        _webhook(fake, envelope, "DOCUMENT_COMPLETED")
    )
    assert signed is not None
    letter.stato, letter.signed_at = "firmato", SIGNED_AT
    clean.commit()
    factory = session_factory(hub_engine)
    gate, first_session, second_session = factory(), factory(), factory()
    before = len(sender.sent)

    def call_finish(session: Session) -> None:
        _signing(session, renderer, fake, sender).recovery.finish(signed)

    try:
        gate.execute(
            select(ContractDocument.id).where(ContractDocument.id == signed).with_for_update()
        )
        first = threading.Thread(target=call_finish, args=(first_session,))
        second = threading.Thread(target=call_finish, args=(second_session,))
        first.start()
        first.join(timeout=0.5)
        assert first.is_alive(), "the first finish did not wait on the document row"
        second.start()
        second.join(timeout=0.5)
        assert second.is_alive(), "the second finish did not wait on the document row"
        gate.commit()
        first.join(timeout=5)
        second.join(timeout=5)
        assert not first.is_alive() and not second.is_alive()
    finally:
        gate.close()
        first_session.close()
        second_session.close()
    clean.expire_all()
    letter = _letter_of(clean, match.id)
    assert letter.signed_pdf == fake.signed_pdf(envelope)
    downloads = [call for call in fake.calls if call[1].endswith("/download?version=signed")]
    assert len(downloads) == 1
    copies = [mail for mail in sender.sent[before:] if mail.attachments]
    assert len(copies) == 2
    assert (letter.signed_copy_to_freelancer_at, letter.signed_copy_to_rebase_at) == (NOW, NOW)
