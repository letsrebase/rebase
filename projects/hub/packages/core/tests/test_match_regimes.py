"""The two letter types (REB-687): the regime a prefill proposes, the refusal of an ordinary letter
without a partita IVA, and the framework agreements kept apart per regime. Helpers:
`match_fixtures`."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fakes_contracts import FakeRenderer
from match_fixtures import (
    CUSTOMER_REST,
    REFUSED_WITHOUT_VAT_NUMBER,
    _admin,
    _body,
    _card,
    _documents,
    _fiscal,
    _framework,
    _request,
    _service,
    _setup,
    _setup_occasional,
    clean,  # noqa: F401  (fixture)
)
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from rebase_core import match_reads as match_reads_module
from rebase_core.contracts.fields import FIELD, TERM
from rebase_core.contracts.render import text_path
from rebase_core.errors import InvalidState, ValidationFailed
from rebase_core.framework import (
    active_framework,
    framework_state,
    framework_states,
    pending_framework,
)
from rebase_core.models import ContractDocument, Match
from rebase_core.regimes import Regime


def test_an_occasional_document_downloads_under_its_own_texts_name(clean: Session) -> None:
    """REB-687: `contratto-quadro-occasionale-v1.0.pdf`, never a second
    `contratto-quadro-v1.0.pdf`."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    framework = ContractDocument(
        kind="framework",
        regime="occasional",
        freelancer_id=freelancer_id,
        text_version="1.0",
        draft_text=False,
        data={},
        pdf=b"%PDF-quadro",
        status="generated",
        created_by=admin_id,
    )
    clean.add(framework)
    clean.commit()
    assert _service(clean).reads.document_pdf(framework.id).filename == (
        "contratto-quadro-occasionale-v1.0.pdf"
    )


def test_framework_lookups_and_states_are_per_regime(clean: Session) -> None:
    """REB-687: a freelancer may hold a framework agreement of each regime, and no lookup
    reads one for the other."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    ordinary = _framework(clean, freelancer_id, admin_id)
    occasional = _framework(
        clean, freelancer_id, admin_id, status="sent", signed_at=None, regime="occasional"
    )
    active = active_framework(clean, freelancer_id, "vat_registered")
    assert active is not None and active.id == ordinary.id
    assert active_framework(clean, freelancer_id, "occasional") is None
    pending = pending_framework(clean, freelancer_id, "occasional")
    assert pending is not None and pending.id == occasional.id
    assert pending_framework(clean, freelancer_id, "vat_registered") is None
    assert framework_states(clean, {freelancer_id}) == {
        (freelancer_id, "vat_registered"): "signed",
        (freelancer_id, "occasional"): "sent",
    }
    both = _documents(clean, freelancer_id, "framework")
    assert (framework_state(both, "vat_registered"), framework_state(both, "occasional")) == (
        "signed",
        "sent",
    )


def test_a_new_version_is_announced_against_the_frameworks_own_text(
    clean: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once one regime's text moves to a new version, only that regime's
    framework agreement says «Nuova versione disponibile»."""
    admin_id, freelancer_id, _company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id, text_version="1.0")
    _framework(clean, freelancer_id, admin_id, text_version="1.0", regime="occasional")
    versions = {"vat_registered": "1.1", "occasional": "1.0"}
    monkeypatch.setattr(match_reads_module, "current_framework_version", versions.__getitem__)
    frameworks = _service(clean).reads.for_freelancer(freelancer_id).frameworks
    assert {(framework.regime, framework.new_version) for framework in frameworks} == {
        ("vat_registered", True),
        ("occasional", False),
    }


def test_the_documents_of_an_ordinary_match_say_their_regime_and_type(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id), admin_id)
    assert (match.letter.regime, match.letter.label) == ("vat_registered", "Lettera di incarico")
    assert {document.regime for document in _documents(clean, freelancer_id)} == {"vat_registered"}
    page = service.reads.for_freelancer(freelancer_id)
    assert page.framework is not None
    assert (page.framework.regime, page.framework.label) == ("vat_registered", "Contratto quadro")


def test_the_prefill_proposes_the_letter_type_from_the_tax_data(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    service = _service(clean)
    assert (
        service.drafts.prefill(freelancer_id, company_id).regime == "vat_registered"
    )  # none saved
    _fiscal(clean, freelancer_id, admin_id)
    assert service.drafts.prefill(freelancer_id, company_id).regime == "vat_registered"
    _fiscal(clean, freelancer_id, admin_id, vat_number=None)
    prefill = service.drafts.prefill(freelancer_id, company_id)
    assert (prefill.regime, prefill.required_framework) == ("occasional", True)
    proposed = service.drafts.proposal(freelancer_id, company_id, customer=CUSTOMER_REST)
    assert proposed.regime == "occasional"
    chosen = service.drafts.proposal(
        freelancer_id, company_id, customer=CUSTOMER_REST, regime="vat_registered"
    )
    assert chosen.regime == "vat_registered"


@pytest.mark.parametrize("action", ["create", "check", "preview"])
def test_an_ordinary_letter_without_to_vat_number_is_refused_with_the_sentence(
    clean: Session, action: str
) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    service = _service(clean)
    attempts = {
        "create": lambda: service.create(freelancer_id, _body(company_id), admin_id),
        "check": lambda: service.drafts.check(freelancer_id, _body(company_id)),
        "preview": lambda: service.drafts.preview(freelancer_id, _body(company_id), "letter"),
    }
    with pytest.raises(ValidationFailed) as refused:
        attempts[action]()
    assert refused.value.details == {
        "entity": "match",
        "field": "regime",
        "reason": REFUSED_WITHOUT_VAT_NUMBER,
    }
    assert clean.scalar(select(func.count()).select_from(Match)) == 0


def test_an_occasional_letter_for_to_vat_number_holder_is_allowed_and_warned(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    checked = service.drafts.check(freelancer_id, _body(company_id, regime="occasional"))
    assert checked.regime == "occasional"
    assert checked.recap[-2:] == [
        "Compenso: 450,00 € a giornata, lordi, senza IVA, pagato a 30 giorni fine mese.",
        "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla "
        "sua attività professionale.",
    ]
    assert checked.what_happens == (
        "Prima parte il contratto quadro per prestazioni occasionali; la lettera di incarico "
        "occasionale parte da sola dopo la sua firma."
    )
    match = service.create(freelancer_id, _body(company_id, regime="occasional"), admin_id)
    assert match.letter.label == "Lettera di incarico occasionale"


def test_an_occasional_match_writes_the_occasional_texts_and_prints_the_tax_code(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasional"), admin_id
    )
    assert [document for document, _ in renderer.calls] == [
        "framework-agreement-occasional",
        "engagement-letter-occasional",
    ]
    framework, letter = _documents(clean, freelancer_id)
    assert (framework.regime, framework.status, letter.regime, letter.status) == (
        "occasional",
        "generated",
        "occasional",
        "pending",
    )
    (_, framework_date), (_, letter_data) = renderer.calls
    assert "professionista-piva" not in framework_date
    assert "professionista-piva" not in letter_data
    assert (
        letter_data["professionista-cf"]
        == framework_date["professionista-cf"]
        == ("LVLDAA85T50H501Z")
    )
    for document, data in renderer.calls:
        asked = set(FIELD.findall(text_path(document).read_text(encoding="utf-8")))
        assert asked - {TERM} <= set(data), document
    assert (match.letter.regime, match.letter.label) == (
        "occasional",
        "Lettera di incarico occasionale",
    )


def test_an_occasional_match_reuses_an_active_occasional_framework(clean: Session) -> None:
    """23:30 UTC on 30 September is 1 October in Rome."""
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    _framework(
        clean,
        freelancer_id,
        admin_id,
        signed_at=datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
        regime="occasional",
    )
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasional"), admin_id
    )
    assert [document for document, _ in renderer.calls] == ["engagement-letter-occasional"]
    assert match.letter.status == "generated"
    assert renderer.calls[0][1]["data-contratto-quadro"] == "1° ottobre 2026"


@pytest.mark.parametrize(
    ("status", "signed_at"),
    [("signed", datetime(2026, 10, 1, 9, 0, tzinfo=UTC)), ("sent", None)],
)
def test_an_occasional_match_ignores_an_ordinary_framework_active_or_out_for_signature(
    clean: Session, status: str, signed_at: datetime | None
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    ordinary = _framework(clean, freelancer_id, admin_id, status=status, signed_at=signed_at)
    renderer = FakeRenderer()
    match = _service(clean, renderer).create(
        freelancer_id, _body(company_id, regime="occasional"), admin_id
    )
    assert [document for document, _ in renderer.calls] == [
        "framework-agreement-occasional",
        "engagement-letter-occasional",
    ]
    assert match.letter.status == "pending"
    clean.refresh(ordinary)
    assert ordinary.status == status


def test_the_stale_framework_cleanup_leaves_the_other_regimes_frameworks_alone(
    clean: Session,
) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasional"), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasional"), admin_id)
    assert [(d.regime, d.status) for d in _documents(clean, freelancer_id, "framework")] == [
        ("vat_registered", "generated"),
        ("occasional", "cancelled"),
        ("occasional", "generated"),
    ]


def test_the_contracts_page_shows_one_framework_per_regime(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    assert service.reads.for_freelancer(freelancer_id).occasional_framework is None
    service.create(freelancer_id, _body(company_id), admin_id)
    service.create(freelancer_id, _body(company_id, regime="occasional"), admin_id)
    page = service.reads.for_freelancer(freelancer_id)
    assert page.framework is not None and page.occasional_framework is not None
    assert (page.framework.regime, page.framework.label) == ("vat_registered", "Contratto quadro")
    assert (page.occasional_framework.regime, page.occasional_framework.label) == (
        "occasional",
        "Contratto quadro per prestazioni occasionali",
    )
    assert len(page.frameworks) == 2


def test_an_occasional_matchs_sentence_reads_its_own_framework_not_the_ordinary_one(
    clean: Session,
) -> None:
    """With only the ordinary framework active, an occasional letter
    waiting in signature is not «pronta a partire». The card, the list row and `get`
    agree."""
    admin_id, freelancer_id, company_id = _setup(clean)
    _framework(clean, freelancer_id, admin_id)  # the ordinary one, active
    service = _service(clean)
    match = service.create(freelancer_id, _body(company_id, regime="occasional"), admin_id)
    clean.execute(update(Match).where(Match.id == match.id).values(status="awaiting_signature"))
    clean.commit()
    expected = (
        f"La lettera n. {match.letter.number} parte dopo il contratto quadro: «Invia per la "
        "firma» lo manda al freelance."
    )
    assert service.reads.get(match.id).situation == expected
    [row] = service.reads.list_all(status=None, q=None).items
    assert row.situation == expected
    [card] = service.reads.for_freelancer(freelancer_id).matches
    assert card.situation == expected


def test_a_repeated_id_with_the_regime_changed_is_refused(clean: Session) -> None:
    """The same client-generated id with the other letter type is another
    request, a 409, never the stale match of the other type handed back."""
    admin_id, freelancer_id, company_id = _setup(clean)
    service = _service(clean)
    body = _body(company_id).model_copy(update={"id": uuid4()})
    service.create(freelancer_id, body, admin_id)
    with pytest.raises(InvalidState, match="dati diversi"):
        service.create(freelancer_id, body.model_copy(update={"regime": "occasional"}), admin_id)
    assert clean.scalar(select(func.count()).select_from(Match)) == 1


def test_an_occasional_preview_renders_the_occasional_texts(clean: Session) -> None:
    admin_id, freelancer_id, company_id = _setup_occasional(clean)
    renderer = FakeRenderer()
    service = _service(clean, renderer)
    letter = service.drafts.preview(freelancer_id, _body(company_id, regime="occasional"), "letter")
    framework = service.drafts.preview(
        freelancer_id, _body(company_id, regime="occasional"), "framework"
    )
    assert (letter.filename, framework.filename) == (
        "anteprima-lettera-di-incarico-occasionale.pdf",
        "anteprima-contratto-quadro-occasionale.pdf",
    )
    assert [document for document, _ in renderer.calls] == [
        "engagement-letter-occasional",
        "framework-agreement-occasional",
    ]


@pytest.mark.parametrize(
    ("framework_regime", "vat_number", "letter_regime"),
    [("vat_registered", None, "occasional"), ("occasional", "01234567890", "vat_registered")],
)
def test_prefill_check_and_letter_preview_read_only_their_own_regimes_framework(
    clean: Session, framework_regime: Regime, vat_number: str | None, letter_regime: Regime
) -> None:
    """An active framework agreement of the other regime is not this letter's: the
    prefill, the check and the letter's preview all see none, and say one must leave."""
    admin_id, freelancer_id, company_id = _admin(clean), _card(clean), _request(clean)
    _fiscal(clean, freelancer_id, admin_id, vat_number=vat_number)
    _framework(clean, freelancer_id, admin_id, regime=framework_regime)
    renderer = FakeRenderer()
    service = _service(clean, renderer)

    prefill = service.drafts.prefill(freelancer_id, company_id)
    assert (prefill.regime, prefill.active_framework, prefill.required_framework) == (
        letter_regime,
        None,
        True,
    )
    body = _body(company_id, regime=letter_regime)
    assert service.drafts.check(freelancer_id, body).required_framework is True
    service.drafts.preview(freelancer_id, body, "letter")
    [(_text, fields)] = renderer.calls
    assert fields["data-contratto-quadro"] is None
