import shutil
from pathlib import Path

import pytest
from orologio import OGGI_IN_ITALIA, congela
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.customers.models import Customer
from pigrocrm.core.documents import service as documents_service_module
from pigrocrm.core.documents.schemas import (
    DocumentCreate,
    DocumentFromTemplate,
    DocumentListQuery,
)
from pigrocrm.core.documents.service import OFFER_TRANSITIONS, DocumentService
from pigrocrm.core.emitter.schemas import AziendaUpsert
from pigrocrm.core.emitter.service import AziendaService
from pigrocrm.core.errors import Conflict, NotFound, ValidationFailed
from pigrocrm.core.storage import LocalFileStorage
from pigrocrm.core.templates.escaping import escape_markdown, escape_typst
from pigrocrm.core.templates.schemas import TemplateCreate, TemplateVariable
from pigrocrm.core.templates.service import TemplateService

ADMIN = Actor(id=None, type="system", role="admin")
needs_binaries = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("typst") is None,
    reason="pandoc e typst vivono nell'immagine dell'API",
)

BODY = """{{offerta.data}}

Spett.le **{{cliente.ragione_sociale}}**

Oggetto: {{offerta.oggetto}}

```{=typst}
#table(
  columns: (0.8fr, 0.2fr),
  [{{cliente.ragione_sociale}}],
  [{{offerta.oggetto}}],
)
```

{{emittente.ragione_sociale}}
"""


@pytest.fixture
def setup(db_session: Session, tmp_path: Path) -> tuple[DocumentService, Customer, object]:
    AziendaService(db_session).upsert_default(
        AziendaUpsert(ragione_sociale="Studio Rossi", partita_iva="01234567890"),
        ADMIN,
    )
    template = TemplateService(db_session).create(
        TemplateCreate(
            nome="Consulenza CTO",
            tipo="offerta",
            corpo_markdown=BODY,
            variabili_dichiarate=[
                TemplateVariable(
                    nome="offerta", etichetta="Offerta", tipo="text", obbligatoria=True
                ),
            ],
        ),
        ADMIN,
    )
    customer = Customer(ragione_sociale="ACME S.r.l.")
    db_session.add(customer)
    db_session.flush()
    return DocumentService(db_session, LocalFileStorage(tmp_path)), customer, template


def _payload(customer: Customer, template: object, **overrides: object) -> DocumentFromTemplate:
    values: dict[str, object] = {
        "template_id": template.id,  # type: ignore[attr-defined]
        "customer_id": customer.id,
        "titolo": "Offerta 2026-01",
        "variabili": {"offerta": {"data": "10/08/2026", "oggetto": "Advisory"}},
    }
    values.update(overrides)
    return DocumentFromTemplate(**values)  # type: ignore[arg-type]


@needs_binaries
def test_create_from_template_produces_a_pdf_version(setup: tuple) -> None:
    service, customer, template = setup
    document = service.create_from_template(_payload(customer, template), ADMIN)
    assert document.versione_corrente == 1
    data, content_type, _ = service.download(document.id, None, ADMIN)
    assert data.startswith(b"%PDF")
    assert content_type == "application/pdf"


def test_the_version_keeps_the_template_and_the_variables_for_regeneration(setup: tuple) -> None:
    service, customer, template = setup
    document = service.create_from_template(_payload(customer, template), ADMIN)
    version = service.versions(document.id, ADMIN)[0]
    assert version.template_id == template.id  # type: ignore[attr-defined]
    stored = service.repo.version(document.id, 1)
    assert stored is not None
    assert stored.variabili["offerta"]["oggetto"] == "Advisory"
    assert "Advisory" in stored.sorgente_markdown


def test_the_customer_is_injected_into_the_template_scope(setup: tuple) -> None:
    # `escape_markdown` backslash-escapes every ASCII punctuation character
    # unconditionally (escaping.py) -- so "ACME S.r.l." never reaches the compiled
    # markdown literally, only as its escaped form. Checking for the raw string
    # here would be checking for text this pipeline is specifically designed never
    # to produce.
    service, customer, template = setup
    document = service.create_from_template(_payload(customer, template), ADMIN)
    stored = service.repo.version(document.id, 1)
    assert stored is not None
    assert escape_markdown(customer.ragione_sociale) in stored.sorgente_markdown


def test_an_injecting_customer_name_is_literal_in_both_contexts(
    setup: tuple, db_session: Session
) -> None:
    """ "Literal" means inert, not unescaped: `escape_markdown` and `escape_typst`
    both backslash-protect every ASCII punctuation character (escaping.py), so the
    hostile value never reaches either context as the bytes '#import "/etc/passwd"'
    -- it reaches both as that same string with every punctuation character
    escaped, which is exactly what stops Pandoc's Typst writer (markdown context)
    or the raw fence's own Typst parser (typst context) from treating '#import' as
    a real directive."""
    service, customer, template = setup
    customer.ragione_sociale = '#import "/etc/passwd"'
    db_session.flush()
    document = service.create_from_template(_payload(customer, template), ADMIN)
    stored = service.repo.version(document.id, 1)
    assert stored is not None
    markdown_part, typst_part = stored.sorgente_markdown.split("```{=typst}", 1)
    assert customer.ragione_sociale not in markdown_part
    assert customer.ragione_sociale not in typst_part
    assert escape_markdown(customer.ragione_sociale) in markdown_part
    assert escape_typst(customer.ragione_sociale) in typst_part


def test_a_missing_required_variable_fails_before_anything_is_written(setup: tuple) -> None:
    service, customer, template = setup
    with pytest.raises(ValidationFailed) as excinfo:
        service.create_from_template(_payload(customer, template, variabili={}), ADMIN)
    assert excinfo.value.details["field"] == "offerta"
    assert service.list.__self__ is service  # sanity: the service is intact
    from pigrocrm.core.documents.schemas import DocumentListQuery

    assert service.list(DocumentListQuery(customer_id=customer.id), ADMIN).items == []


def test_an_unknown_template_raises_not_found(setup: tuple) -> None:
    from uuid import uuid4

    service, customer, _ = setup
    with pytest.raises(NotFound):
        service.create_from_template(
            DocumentFromTemplate(
                template_id=uuid4(), customer_id=customer.id, titolo="X", variabili={}
            ),
            ADMIN,
        )


@needs_binaries
def test_regenerate_reproduces_an_old_version_as_a_new_one(setup: tuple) -> None:
    service, customer, template = setup
    document = service.create_from_template(_payload(customer, template), ADMIN)
    first = service.repo.version(document.id, 1)
    regenerated = service.regenerate(document.id, 1, ADMIN)
    assert regenerated.numero == 2
    second = service.repo.version(document.id, 2)
    assert second is not None and first is not None
    # Identical source means identical bytes: the whole promise of storing both
    # template_id and variabili (spec 11 criterion 4).
    assert second.sorgente_markdown == first.sorgente_markdown
    assert second.hash_sha256 == first.hash_sha256


def test_regenerate_of_an_uploaded_version_is_refused(setup: tuple, tmp_path: Path) -> None:
    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="documento", titolo="Scansione"), ADMIN
    )
    service.add_version(document.id, b"%PDF-1.7\n", "application/pdf", ADMIN)
    with pytest.raises(ValidationFailed) as excinfo:
        service.regenerate(document.id, 1, ADMIN)
    assert "template" in excinfo.value.details["reason"]


def test_the_offer_state_machine_allows_only_the_declared_transitions(setup: tuple) -> None:
    service, customer, template = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="offerta", titolo="O"), ADMIN
    )
    assert service.get(document.id, ADMIN).stato == "bozza"
    assert service.set_offer_state(document.id, "inviata", ADMIN).stato == "inviata"
    assert service.set_offer_state(document.id, "accettata", ADMIN).stato == "accettata"


def test_an_undeclared_transition_is_refused(setup: tuple) -> None:
    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="offerta", titolo="O"), ADMIN
    )
    with pytest.raises(Conflict) as excinfo:
        service.set_offer_state(document.id, "accettata", ADMIN)
    assert "bozza" in excinfo.value.details["reason"]


def test_an_accepted_offer_is_terminal(setup: tuple) -> None:
    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="offerta", titolo="O"), ADMIN
    )
    service.set_offer_state(document.id, "inviata", ADMIN)
    service.set_offer_state(document.id, "accettata", ADMIN)
    assert OFFER_TRANSITIONS["accettata"] == frozenset()
    with pytest.raises(Conflict):
        service.set_offer_state(document.id, "bozza", ADMIN)


def test_a_non_offer_has_no_state_to_set(setup: tuple) -> None:
    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="verbale", titolo="V"), ADMIN
    )
    with pytest.raises(ValidationFailed) as excinfo:
        service.set_offer_state(document.id, "inviata", ADMIN)
    assert excinfo.value.details["field"] == "stato"


def test_a_state_change_leaves_a_timeline_entry(setup: tuple, db_session: Session) -> None:
    from pigrocrm.core.activities.service import ActivityService

    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="offerta", titolo="O"), ADMIN
    )
    service.set_offer_state(document.id, "inviata", ADMIN)
    kinds = [a.kind for a in ActivityService(db_session).timeline("document", document.id)]
    assert "state_changed" in kinds


def test_accepting_an_offer_that_has_no_deal_records_no_automation(
    setup: tuple, db_session: Session
) -> None:
    """The runner is called on **every** offer state change and decides for itself
    (slice 6 §9.3). This offer belongs to a customer, so there is no deal to move and
    nothing is recorded: a `non_eseguita` entry here would fill the automation log with
    non-events, and the commercial dashboard's "offerta accettata, deal non vinto" signal
    counts exactly those entries."""
    from pigrocrm.core.activities.repository import ActivityRepository
    from pigrocrm.core.automations.schemas import AUTOMATION_KINDS

    service, customer, _ = setup
    document = service.create(
        DocumentCreate(customer_id=customer.id, tipo="offerta", titolo="O senza deal"), ADMIN
    )
    service.set_offer_state(document.id, "inviata", ADMIN)
    service.set_offer_state(document.id, "accettata", ADMIN)

    assert ActivityRepository(db_session).by_kind(list(AUTOMATION_KINDS), limit=10) == []


def test_the_automations_entry_precedes_the_documents_own(
    setup: tuple, db_session: Session, tmp_path: Path
) -> None:
    """Slice 6 §9.3 fixes the order inside the trigger, and it is not cosmetic:
    `ActivityService.record` must be the last thing that touches the session before the
    caller's commit, so the runner has to run *before* the document's own entry. Asserted
    on the timeline rather than trusted to a comment -- `by_kind` returns newest first, so
    the document's `state_changed` comes back ahead of the automation's entry.
    """
    from pigrocrm.core.activities.repository import ActivityRepository
    from pigrocrm.core.automations.schemas import KIND_STAGE_MOVED
    from pigrocrm.core.deals.models import Deal
    from pigrocrm.core.pipeline.service import PipelineService

    service, customer, _ = setup
    PipelineService(db_session).seed_defaults(ADMIN)
    stages = {s.code: s for s in PipelineService(db_session).list() if s.code is not None}
    deal = Deal(
        nome="Impianto",
        customer_id=customer.id,
        pipeline_stage_id=stages["lead"].id,
        probabilita=10,
        custom_fields={},
    )
    db_session.add(deal)
    db_session.flush()

    document = service.create(
        DocumentCreate(deal_id=deal.id, tipo="offerta", titolo="O con deal"), ADMIN
    )
    service.set_offer_state(document.id, "inviata", ADMIN)
    service.set_offer_state(document.id, "accettata", ADMIN)

    assert deal.pipeline_stage_id == stages["vinto"].id
    ordered = ActivityRepository(db_session).by_kind([KIND_STAGE_MOVED, "state_changed"], limit=10)
    assert [entry.kind for entry in ordered][:2] == ["state_changed", KIND_STAGE_MOVED]


def test_oggi_defaults_to_italys_own_day_not_the_processs(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`{{oggi}}` is the date a template prints on the document itself. It is a date in
    the issuer's calendar, so it comes off Italy's clock and not the host's -- see
    `clock.py`.

    Frozen at 00:30 on 1 January in Rome, which a process running in UTC (the API image
    does) still reads as 31 December: same hour, different day, different *year*, on a
    document whose rendered markdown and PDF are then frozen into an immutable version
    that `regenerate` faithfully reproduces. Nothing downstream corrects it, which is
    why the assertion is on the compiled source rather than on which function was
    called.
    """
    AziendaService(db_session).upsert_default(
        AziendaUpsert(ragione_sociale="Studio Rossi", partita_iva="01234567890"),
        ADMIN,
    )
    template = TemplateService(db_session).create(
        TemplateCreate(
            nome="Offerta datata",
            tipo="offerta",
            corpo_markdown="Milano, {{oggi}}\n",
            variabili_dichiarate=[],
        ),
        ADMIN,
    )
    customer = Customer(ragione_sociale="ACME S.r.l.")
    db_session.add(customer)
    db_session.flush()
    service = DocumentService(db_session, LocalFileStorage(tmp_path))

    congela(monkeypatch, documents_service_module)
    document = service.create_from_template(
        DocumentFromTemplate(
            template_id=template.id, customer_id=customer.id, titolo="Offerta", variabili={}
        ),
        ADMIN,
    )

    stored = service.repo.version(document.id, 1)
    assert stored is not None
    # `escape_markdown` backslash-escapes every ASCII punctuation character, so the ISO
    # date never reaches the compiled markdown literally -- only in its escaped form.
    assert escape_markdown(OGGI_IN_ITALIA.isoformat()) in stored.sorgente_markdown


def test_a_sollecito_template_cannot_become_a_document(db_session: Session, setup: tuple) -> None:
    """`TemplateTipo` and `DocumentTipo` used to be one Literal, so copying
    `template.tipo` onto `Document.tipo` could not go wrong. Slice 5 widened the
    template side with `email` and `sollecito`; `DocumentRead.tipo` is a plain `str`,
    so without an explicit check the copy succeeds and a "sollecito" document appears
    in the customer's document list, with a body that is an email."""
    service, customer, _ = setup
    sollecito = TemplateService(db_session).create(
        TemplateCreate(nome="Sollecito", tipo="sollecito", corpo_markdown="Gentile cliente,"),
        ADMIN,
    )
    with pytest.raises(ValidationFailed) as excinfo:
        service.create_from_template(_payload(customer, sollecito), ADMIN)
    assert excinfo.value.details["field"] == "template_id"
    assert "sollecito" in excinfo.value.details["reason"]
    # Nothing was left behind by the refusal.
    assert service.list(DocumentListQuery(customer_id=customer.id), ADMIN).items == []
