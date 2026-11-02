"""What the signing tests share (REB-391, REB-892, HTEST-10): the fakes and refusing senders, the
`clean` fixture, and the builders of a sent, signed or cancelled document."""

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fakes_pigro import PIGRO, TOKEN, RecordedPigro
from match_fixtures import (
    SIGNER,
    TABLES,
    TODAY,
    _body,
    _documents,
    _setup,
)
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.contract_schemas import FiscalData, MatchRead
from rebase_core.contracts.fields import ContractFailed, Value
from rebase_core.contracts.render import Renderer
from rebase_core.contracts.render import text_version as current_text_version
from rebase_core.documenso import (
    Outcome,
    WebhookBody,
    outcome_from_webhook,
)
from rebase_core.engagements import EngagementService
from rebase_core.fiscal import FiscalService
from rebase_core.mail import EmailSender, Mail, RecordingSender
from rebase_core.matches import MatchService
from rebase_core.models import ContractDocument, Match
from rebase_core.regimes import text_name
from rebase_core.signing import SigningService

# 23:30 UTC on 30 September is already 1 October in Rome.
SIGNED_AT = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)


NOW = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


CONTRACTS_MAIL = "contratti@rebase.test"


class RefusingSender:
    """A provider that turns every mail away, and keeps what it refused."""

    def __init__(self) -> None:
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return False


class RefusingRecipientSender:
    """A provider that turns away mail to one address forever and accepts every other,
    and keeps what it was asked to send."""

    def __init__(self, refuses: str) -> None:
        self.refuses = refuses
        self.sent: list[Mail] = []

    def send(self, mail: Mail) -> bool:
        self.sent.append(mail)
        return mail.to != self.refuses


def _fiscal(
    session: Session, freelancer_id: UUID, admin_id: UUID, domicilio: str = "Via Roma 1, Milano"
) -> None:
    """Beyond `match_fixtures._fiscal`: a `domicilio` a test can change, to prove a send
    prints the tax data as they are today rather than as they were on the draft."""
    FiscalService(session).save(
        freelancer_id,
        FiscalData(
            codice_fiscale="LVLDAA85T50H501Z", partita_iva="01234567890", domicilio=domicilio
        ),
        admin_id,
    )


@pytest.fixture
def clean(hub_session: Session) -> Iterator[Session]:
    yield hub_session
    hub_session.rollback()
    for table in TABLES:
        hub_session.execute(text(f"DELETE FROM {table}"))
    hub_session.commit()


def _matches(session: Session, renderer: Renderer) -> MatchService:
    return MatchService(session, renderer, SIGNER, today=lambda: TODAY)


def _signing(
    session: Session,
    renderer: Renderer,
    fake: FakeDocumenso | None,
    sender: EmailSender | None,
    *,
    today: date = TODAY,
    signer: dict[str, Value] | None = None,
    allow_draft: bool = False,
    engagements: EngagementService | None = None,
) -> SigningService:
    return SigningService(
        session,
        renderer=renderer,
        documenso=fake.client() if fake is not None else None,
        sender=sender,
        signer=SIGNER if signer is None else signer,
        contracts_mail=CONTRACTS_MAIL,
        allow_draft=allow_draft,
        today=lambda: today,
        now=lambda: NOW,
        engagements=engagements,
    )


def _framework_of(session: Session, freelancer_id: UUID) -> ContractDocument:
    return _documents(session, freelancer_id, "quadro")[-1]


def _letter_of(session: Session, match_id: UUID) -> ContractDocument:
    return session.scalars(
        select(ContractDocument).where(ContractDocument.match_id == match_id)
    ).one()


def _draft(
    session: Session, renderer: Renderer, freelancer_id: UUID, company_id: UUID, admin_id: UUID
) -> MatchRead:
    return _matches(session, renderer).create(freelancer_id, _body(company_id), admin_id)


def _active_framework(
    session: Session,
    freelancer_id: UUID,
    admin_id: UUID,
    signed_at: datetime = SIGNED_AT,
    regime: str = "partita_iva",
) -> ContractDocument:
    """A framework agreement as a signature leaves one."""
    document = ContractDocument(
        kind="quadro",
        regime=regime,
        freelancer_id=freelancer_id,
        text_version=current_text_version(text_name("quadro", regime)),
        testo_bozza=False,
        data={},
        pdf=b"%PDF-quadro",
        stato="firmato",
        signed_at=signed_at,
        created_by=admin_id,
    )
    session.add(document)
    session.commit()
    return document


# ---- the webhook (REB-391) --------------------------------------------------------------
def _sent(
    session: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    sender: RecordingSender,
    freelancer_id: UUID,
    company_id: UUID,
    admin_id: UUID,
) -> MatchRead:
    """A draft match, sent: its framework agreement `inviato`, its letter waiting."""
    match = _draft(session, renderer, freelancer_id, company_id, admin_id)
    _signing(session, renderer, fake, sender).send_match(match.id, admin_id)
    return match


def _webhook(fake: FakeDocumenso, envelope_id: str, event: str) -> Outcome:
    outcome = outcome_from_webhook(WebhookBody.model_validate(fake.webhook(envelope_id, event)))
    assert outcome is not None
    return outcome


def _envelope_of(document: ContractDocument) -> str:
    assert document.documenso_id is not None
    return document.documenso_id


def _try_lock_nowait(session: Session, document_id: UUID) -> bool:
    """`True` when `document_id` could be locked immediately, `False` when another
    transaction already holds it (`FOR UPDATE NOWAIT`): the proof that a caller has, or
    has not, taken this row yet (REB-391)."""
    try:
        session.execute(
            select(ContractDocument.id)
            .where(ContractDocument.id == document_id)
            .with_for_update(nowait=True)
        )
        return True
    except OperationalError:
        session.rollback()
        return False


# ---- the link to Pigro, once a letter is signed (REB-499) ----------------------------------
class SpyEngagements(EngagementService):
    """The real engagement service, keeping which matches `link` was asked for, so a
    test can tell «never asked» from «asked and refused»."""

    def __init__(self, session: Session, http: RecordedPigro) -> None:
        super().__init__(
            session,
            Settings(  # type: ignore[call-arg]
                _env_file=None,
                pigro_api_url=PIGRO,
                pigro_engagements_token=TOKEN,
                signer_json=json.dumps(SIGNER),
            ),
            http,
            now=lambda: NOW,
            today=lambda: TODAY,
        )
        self.asked: list[UUID] = []

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        self.asked.append(match_id)
        return super().link(match_id, admin_id)


class FailingEngagements(SpyEngagements):
    """A link that cannot even be built, the way a letter that printed no start fails."""

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        self.asked.append(match_id)
        raise ContractFailed("letter 2026-001 printed no data-inizio")


def _letter_out(
    session: Session, renderer: FakeRenderer, fake: FakeDocumenso, sender: RecordingSender
) -> tuple[UUID, ContractDocument, str]:
    """A match whose framework agreement is already active, sent: its letter `inviato`
    on Documenso, waiting for the freelancer. (match id, letter, envelope)."""
    admin_id, freelancer_id, company_id = _setup(session)
    _active_framework(session, freelancer_id, admin_id)
    match = _sent(session, renderer, fake, sender, freelancer_id, company_id, admin_id)
    letter = _letter_of(session, match.id)
    return match.id, letter, _envelope_of(letter)


def _match_row(session: Session, match_id: UUID) -> Match:
    session.expire_all()
    match = session.get(Match, match_id)
    assert match is not None
    return match


class RaisingEngagements(SpyEngagements):
    """A link that fails outside the domain's errors: the database gone mid-call."""

    def link(self, match_id: UUID, admin_id: UUID | None = None) -> MatchRead:
        self.asked.append(match_id)
        raise OperationalError("UPDATE matches", {}, Exception("server closed the connection"))


# ---- the recovery actions (REB-407) ------------------------------------------------------
def _signed_framework(
    clean: Session,
    renderer: FakeRenderer,
    fake: FakeDocumenso,
    sender: RecordingSender,
) -> tuple[UUID, UUID, MatchRead, str]:
    """A match sent and its framework agreement signed on Documenso, the webhook never
    heard: (admin, freelancer, match, envelope)."""
    admin_id, freelancer_id, company_id = _setup(clean)
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    return admin_id, freelancer_id, match, envelope


# ---- two families of documents (REB-687) -------------------------------------------------
OCCASIONAL_QUADRO_VERSION = current_text_version("contratto-quadro-occasionale")


def _without_partita_iva(session: Session, freelancer_id: UUID, admin_id: UUID) -> None:
    FiscalService(session).save(
        freelancer_id,
        FiscalData(codice_fiscale="LVLDAA85T50H501Z", domicilio="Via Roma 1, Milano"),
        admin_id,
    )


def _occasional_draft(
    session: Session, renderer: Renderer, freelancer_id: UUID, company_id: UUID, admin_id: UUID
) -> MatchRead:
    return _matches(session, renderer).create(
        freelancer_id, _body(company_id, regime="occasionale"), admin_id
    )


def _quadro_of(session: Session, freelancer_id: UUID, regime: str) -> ContractDocument:
    return [d for d in _documents(session, freelancer_id, "quadro") if d.regime == regime][-1]


def _sign(signing: SigningService, fake: FakeDocumenso, document: ContractDocument) -> None:
    """`document` signed the way a real signature reaches the hub: its envelope signed on
    the fake Documenso, the completion delivered to the webhook (`apply`), then `finish`
    run for it."""
    envelope = _envelope_of(document)
    fake.sign(envelope, SIGNED_AT)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None
    signing.recovery.finish(signed)
