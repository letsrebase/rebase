"""One builder for both doors, and the two families of documents (REB-478, REB-687). Helpers:
`signing_fixtures`."""

import logging

import pytest
from fakes_contracts import FakeRenderer
from fakes_documenso import FakeDocumenso
from fakes_pigro import RecordedPigro, linked_body
from match_fixtures import (
    _setup,
    _setup_occasional,
)
from signing_fixtures import (
    CONTRACTS_MAIL,
    NOW,
    OCCASIONAL_QUADRO_VERSION,
    SIGNED_AT,
    _active_framework,
    _draft,
    _envelope_of,
    _framework_of,
    _letter_of,
    _occasional_draft,
    _quadro_of,
    _sent,
    _sign,
    _signing,
    _webhook,
    _without_partita_iva,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy.orm import Session

from rebase_core.config import Settings
from rebase_core.contracts.render import ContractRenderer
from rebase_core.documenso import (
    DocumensoClient,
)
from rebase_core.engagements import EngagementService
from rebase_core.errors import (
    ValidationFailed,
)
from rebase_core.http import urllib_engagements_call
from rebase_core.mail import RecordingSender, ResendSender
from rebase_core.models import Match
from rebase_core.signing import signing_from_settings


# ---- one builder for both doors -----------------------------------------------------------
def test_signing_from_settings_builds_the_service_this_environment_configures(
    clean: Session,
) -> None:
    """The admin API's dependency and the MCP server build `SigningService` here, so a
    setting reaches both doors or neither."""
    configured = Settings(  # type: ignore[call-arg]
        _env_file=None,
        documenso_url="http://documenso.test",
        documenso_api_token="api_fake",
        resend_api_key="re_fake",
        signer_json='{"rebase-sede": "Milano"}',
        contracts_mail=CONTRACTS_MAIL,
        contracts_allow_draft=True,
    )
    renderer = FakeRenderer()
    service = signing_from_settings(configured, renderer)(clean)
    assert service.renderer is renderer
    assert isinstance(service.documenso, DocumensoClient)
    assert service.documenso.api.startswith("http://documenso.test")
    assert isinstance(service.sender, ResendSender)
    assert (service.contracts_mail, service.allow_draft) == (CONTRACTS_MAIL, True)
    assert service.require_signer() == {"rebase-sede": "Milano"}
    # The link to Pigro (REB-499): the same session and mail sender, and the seam with
    # time for a new space to be provisioned.
    engagements = service.engagements
    assert isinstance(engagements, EngagementService)
    assert (engagements.session, engagements.settings) == (clean, configured)
    assert (engagements.door.http, engagements.sender) == (urllib_engagements_call, service.sender)

    # What the API injects wins, `None` included: signing off stays off.
    sender = RecordingSender()
    http = RecordedPigro([(201, linked_body())])
    overridden = signing_from_settings(
        configured, renderer, documenso=None, sender=sender, http=http
    )(clean)
    assert (overridden.documenso, overridden.sender) == (None, sender)
    assert overridden.engagements is not None
    assert (overridden.engagements.door.http, overridden.engagements.sender) == (http, sender)

    bare = signing_from_settings(Settings(_env_file=None))(clean)  # type: ignore[call-arg]
    assert isinstance(bare.renderer, ContractRenderer)
    assert (bare.documenso, bare.sender, bare.allow_draft) == (None, None, False)


@pytest.mark.parametrize(
    ("first", "other"), [("occasionale", "partita_iva"), ("partita_iva", "occasionale")]
)
def test_a_framework_signature_releases_only_the_letters_of_its_own_regime(
    clean: Session, first: str, other: str
) -> None:
    """A partita IVA holder may hold both families: each framework agreement's signature
    releases its own regime's waiting letter and leaves the other one waiting."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    matches = {
        "partita_iva": _draft(clean, renderer, freelancer_id, company_id, admin_id),
        "occasionale": _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id),
    }
    for match in matches.values():
        assert signing.send_match(match.id, admin_id).inviato == "quadro"

    _sign(signing, fake, _quadro_of(clean, freelancer_id, first))

    assert _letter_of(clean, matches[first].id).stato == "inviato"
    assert _letter_of(clean, matches[other].id).stato == "in_attesa"


def test_an_occasional_letter_waits_while_only_the_ordinary_framework_is_active(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    ordinary = _active_framework(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "in_attesa"

    report = signing.send_match(match.id, admin_id)

    assert (report.inviato, report.match.lettera.stato) == ("quadro", "in_attesa")
    assert _quadro_of(clean, freelancer_id, "occasionale").stato == "inviato"
    # The ordinary agreement's own copy is stored and mailed already, so only a waiting
    # letter could still bring it into the sweep.
    ordinary.signed_pdf = b"%PDF-firmato"
    ordinary.signed_copy_to_freelancer_at = NOW
    ordinary.signed_copy_to_rebase_at = NOW
    clean.commit()
    # The sweep never takes the ordinary agreement for this letter's own.
    assert ordinary.id not in signing.recovery._to_finish()
    signing.recovery.finish(ordinary.id)
    assert _letter_of(clean, match.id).stato == "in_attesa"


def test_an_occasional_match_turns_active_on_its_letters_signature(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _active_framework(clean, freelancer_id, admin_id, regime="occasionale")
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    assert match.lettera.stato == "generato"
    assert signing.send_match(match.id, admin_id).inviato == "lettera"

    _sign(signing, fake, _letter_of(clean, match.id))

    clean.expire_all()
    assert clean.get(Match, match.id).stato == "attivo"  # type: ignore[union-attr]
    assert _letter_of(clean, match.id).stato == "firmato"


def test_an_occasional_documents_names_are_its_own_on_documenso_and_in_the_mail(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    _without_partita_iva(clean, freelancer_id, admin_id)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)
    signing.send_match(match.id, admin_id)

    [quadro_envelope] = fake.envelopes.values()
    assert quadro_envelope.payload["title"] == "Contratto quadro rebase per prestazioni occasionali"
    assert quadro_envelope.filename == (
        f"contratto-quadro-occasionale-v{OCCASIONAL_QUADRO_VERSION}.pdf"
    )
    assert sender.sent[-1].subject == (
        "Da firmare: contratto quadro rebase per prestazioni occasionali"
    )

    _sign(signing, fake, _quadro_of(clean, freelancer_id, "occasionale"))

    letter = _letter_of(clean, match.id)
    letter_envelope = list(fake.envelopes.values())[-1]
    assert letter_envelope.payload["title"] == f"Lettera di incarico occasionale n. {letter.numero}"
    assert letter_envelope.filename == f"lettera-di-incarico-occasionale-{letter.numero}.pdf"
    assert sender.sent[-1].subject == (
        f"Da firmare: lettera di incarico occasionale n. {letter.numero}"
    )
    document, data = renderer.calls[-1]
    assert document == "lettera-di-incarico-occasionale"
    assert data["professionista-cf"] == "LVLDAA85T50H501Z"
    assert "professionista-piva" not in data


def test_an_occasional_match_goes_out_and_comes_back_on_the_occasional_texts(
    clean: Session,
) -> None:
    """A freelancer with no partita IVA, sent an occasional match from start to finish:
    the framework agreement leaves first, then its signature releases the letter, each
    typeset from its occasional text and printing the codice fiscale, never a partita
    IVA."""
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    signing = _signing(clean, renderer, fake, sender)
    match = _occasional_draft(clean, renderer, freelancer_id, company_id, admin_id)

    assert signing.send_match(match.id, admin_id).inviato == "quadro"

    document, data = renderer.calls[-1]
    assert (document, renderer.signing[-1]) == ("contratto-quadro-occasionale", True)
    assert data["professionista-cf"] == "LVLDAA85T50H501Z"
    assert "professionista-piva" not in data

    _sign(signing, fake, _quadro_of(clean, freelancer_id, "occasionale"))

    assert _letter_of(clean, match.id).stato == "inviato"
    document, data = renderer.calls[-1]
    assert (document, renderer.signing[-1]) == ("lettera-di-incarico-occasionale", True)
    assert data["professionista-cf"] == "LVLDAA85T50H501Z"
    assert "professionista-piva" not in data
    # Neither ordinary text was typeset at any point, for the draft or for a send.
    assert {name for name, _ in renderer.calls} == {
        "contratto-quadro-occasionale",
        "lettera-di-incarico-occasionale",
    }


def test_an_ordinary_draft_whose_partita_iva_was_removed_does_not_leave(clean: Session) -> None:
    """The tax data changed after the draft, so the ordinary framework
    agreement would leave with a blank partita IVA: refused with the sentence «Crea
    match» shows on `regime`, and nothing reaches Documenso or the mailbox."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _draft(clean, renderer, freelancer_id, company_id, admin_id)
    _without_partita_iva(clean, freelancer_id, admin_id)

    with pytest.raises(ValidationFailed) as refused:
        _signing(clean, renderer, fake, sender).send_match(match.id, admin_id)

    assert refused.value.details["field"] == "regime"
    assert refused.value.details["reason"] == (
        'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
        "o salva la partita IVA."
    )
    assert len(fake.envelopes) == 0 and sender.sent == []
    assert _framework_of(clean, freelancer_id).stato == "generato"


def test_a_waiting_ordinary_letter_is_not_released_once_its_partita_iva_is_gone(
    clean: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """The ordinary framework agreement left while the tax data had a partita IVA, and it
    was removed before the signature: the letter waiting on it is refused at its release
    as `send_match` refuses it (CodeRabbit, PR #579), so it keeps waiting, no envelope
    and no mail for it leaves (the framework's own signed copy still does), and the log
    says why in the rule's own words."""
    admin_id, freelancer_id, company_id = _setup(clean)
    renderer, fake, sender = FakeRenderer(draft=False), FakeDocumenso(), RecordingSender()
    match = _sent(clean, renderer, fake, sender, freelancer_id, company_id, admin_id)
    envelopes, mails = len(fake.envelopes), len(sender.sent)
    _without_partita_iva(clean, freelancer_id, admin_id)
    envelope = _envelope_of(_framework_of(clean, freelancer_id))
    fake.sign(envelope, SIGNED_AT)
    signing = _signing(clean, renderer, fake, sender)
    signed = signing.apply(_webhook(fake, envelope, "DOCUMENT_COMPLETED"))
    assert signed is not None

    with caplog.at_level(logging.WARNING, logger="rebase_core.signing"):
        signing.recovery.finish(signed)

    assert _letter_of(clean, match.id).stato == "in_attesa"
    assert len(fake.envelopes) == envelopes
    assert not [m for m in sender.sent[mails:] if "lettera" in m.subject.lower()]
    assert "Senza partita IVA si fa solo la lettera occasionale" in caplog.text
