import re
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from pigrocrm.core.actor import Actor
from pigrocrm.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from pigrocrm.core.templates.renderer import DeclaredVariable, render_template
from pigrocrm.core.templates.repository import TemplateRepository
from pigrocrm.core.templates.schemas import (
    TemplateCreate,
    TemplateListQuery,
    TemplateUpdate,
    TemplateVariable,
)
from pigrocrm.core.templates.service import OFFER_TEMPLATE_NAME, TemplateService

ADMIN = Actor(id=None, type="system", role="admin")
READONLY = Actor(id=None, type="user", role="readonly")


def _create(**overrides: object) -> TemplateCreate:
    payload: dict[str, object] = {
        "nome": "Offerta Standard",
        "tipo": "offerta",
        "corpo_markdown": "Gentile {{cliente.ragione_sociale}}, offerta di {{importo}}.",
        "variabili_dichiarate": [
            {"nome": "importo", "etichetta": "Importo", "tipo": "currency", "obbligatoria": True}
        ],
    }
    payload.update(overrides)
    return TemplateCreate(**payload)  # type: ignore[arg-type]


def test_create_persists_a_template(db_session: Session) -> None:
    template = TemplateService(db_session).create(_create(), ADMIN)
    assert template.nome == "Offerta Standard"
    assert template.active is True
    assert template.declared_variables[0].nome == "amount"


def test_create_rejects_a_syntactically_invalid_body(db_session: Session) -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        TemplateService(db_session).create(_create(body_markdown="{{#if x}}senza chiusura"), ADMIN)
    assert excinfo.value.details["entity"] == "template"


def test_create_rejects_a_duplicate_nome(db_session: Session) -> None:
    service = TemplateService(db_session)
    service.create(_create(), ADMIN)
    with pytest.raises(Conflict):
        service.create(_create(), ADMIN)


def test_create_rejects_a_case_only_duplicate_nome(db_session: Session) -> None:
    service = TemplateService(db_session)
    service.create(_create(), ADMIN)
    with pytest.raises(Conflict):
        service.create(_create(nome="OFFERTA STANDARD"), ADMIN)


def test_create_converts_a_true_insert_race_into_a_clean_conflict(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors test_emitter.py's identical race test: forces the pre-check
    (`get_by_name`) to report "no such template" for two successive creates, so only
    the database's own `uq_templates_nome` functional unique index stops the second
    -- proving the whole mutation, not just the pre-check, converts a real
    constraint violation into a clean `Conflict`."""
    service = TemplateService(db_session)
    monkeypatch.setattr(TemplateRepository, "get_by_name", lambda self, nome: None)
    service.create(_create(), ADMIN)
    with pytest.raises(Conflict):
        service.create(_create(), ADMIN)

    monkeypatch.undo()
    page = TemplateService(db_session).list(_list_query(), ADMIN)
    assert len(page.items) == 1


def test_create_requires_admin(db_session: Session) -> None:
    with pytest.raises(PermissionDenied):
        TemplateService(db_session).create(_create(), READONLY)


def test_get_missing_template_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        TemplateService(db_session).get(uuid4(), ADMIN)


def test_update_renames_a_template(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    updated = service.update(created.id, TemplateUpdate(nome="Offerta Rivista"), ADMIN)
    assert updated.nome == "Offerta Rivista"
    assert updated.body_markdown == created.body_markdown


def test_update_to_a_case_only_variant_of_its_own_name_is_not_a_conflict(
    db_session: Session,
) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    updated = service.update(created.id, TemplateUpdate(nome="OFFERTA STANDARD"), ADMIN)
    assert updated.nome == "OFFERTA STANDARD"


def test_update_to_another_templates_name_is_a_conflict(db_session: Session) -> None:
    service = TemplateService(db_session)
    service.create(_create(), ADMIN)
    other = service.create(_create(nome="Contratto Base"), ADMIN)
    with pytest.raises(Conflict):
        service.update(other.id, TemplateUpdate(nome="Offerta Standard"), ADMIN)


def test_update_rejects_a_syntactically_invalid_body(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    with pytest.raises(ValidationFailed):
        service.update(created.id, TemplateUpdate(body_markdown="{{#each x}}senza chiusura"), ADMIN)


def test_update_missing_template_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        TemplateService(db_session).update(uuid4(), TemplateUpdate(nome="X"), ADMIN)


def test_update_requires_admin(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    with pytest.raises(PermissionDenied):
        service.update(created.id, TemplateUpdate(nome="X"), READONLY)


def test_deactivate_then_activate_round_trips(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    deactivated = service.deactivate(created.id, ADMIN)
    assert deactivated.active is False
    activated = service.activate(created.id, ADMIN)
    assert activated.active is True


def test_deactivate_requires_admin(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)
    with pytest.raises(PermissionDenied):
        service.deactivate(created.id, READONLY)


def _list_query(**overrides: object) -> TemplateListQuery:
    return TemplateListQuery(**overrides)  # type: ignore[arg-type]


def test_list_hides_inactive_templates_by_default(db_session: Session) -> None:
    service = TemplateService(db_session)
    active = service.create(_create(), ADMIN)
    inactive = service.create(_create(nome="Contratto Base"), ADMIN)
    service.deactivate(inactive.id, ADMIN)

    page = service.list(_list_query(), ADMIN)
    assert [item.id for item in page.items] == [active.id]

    page_all = service.list(_list_query(include_inactive=True), ADMIN)
    assert {item.id for item in page_all.items} == {active.id, inactive.id}


def test_list_search_matches_nome_case_insensitively(db_session: Session) -> None:
    service = TemplateService(db_session)
    service.create(_create(), ADMIN)
    service.create(_create(nome="Contratto Base"), ADMIN)

    page = service.list(_list_query(search="offer"), ADMIN)
    assert [item.nome for item in page.items] == ["Offerta Standard"]


def test_describe_reports_the_compilation_form_and_the_root_paths_needed(
    db_session: Session,
) -> None:
    # `lines`/`nome`/`prezzo` are loop-relative (inside the `#each` body): describe
    # answers "what must the caller supply at the root", so only `sconto`, `lines`
    # itself (the array a caller supplies) and `customer.nome` -- all root-level --
    # should appear in `used_journeys`; `nome`/`prezzo` describe the shape of each
    # element of `lines`, never something a caller provides directly.
    body = (
        "{{#if sconto}}Sconto applicato{{/if}}\n"
        "{{#each righe}}- {{nome}}: {{prezzo}}\n{{/each}}\n"
        "Cliente: {{cliente.nome}}"
    )
    service = TemplateService(db_session)
    created = service.create(
        _create(
            body_markdown=body,
            declared_variables=[
                {"nome": "sconto", "etichetta": "Sconto", "tipo": "checkbox", "obbligatoria": False}
            ],
        ),
        ADMIN,
    )

    description = service.describe(created.id, ADMIN)

    assert description.variables[0].nome == "sconto"
    assert description.used_journeys == [["sconto"], ["righe"], ["cliente", "nome"]]
    assert description.variables_not_used == []


def test_describe_flags_a_declared_variable_the_body_never_uses(db_session: Session) -> None:
    service = TemplateService(db_session)
    # Default BODY references `customer.legal_name` and `amount` only, so a
    # second declared variable the body never mentions must be flagged.
    created = service.create(
        _create(
            declared_variables=[
                {
                    "nome": "amount",
                    "label": "Importo",
                    "type": "currency",
                    "required": True,
                },
                {"nome": "inutile", "label": "Inutile", "type": "text", "required": False},
            ]
        ),
        ADMIN,
    )

    description = service.describe(created.id, ADMIN)

    assert description.variables_not_used == ["inutile"]


def test_describe_missing_template_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        TemplateService(db_session).describe(uuid4(), ADMIN)


def test_preview_renders_compiled_markdown(db_session: Session) -> None:
    # No ASCII punctuation in either value: escaping.py's markdown escaper
    # deliberately escapes the entire punctuation class unconditionally (see its own
    # module docstring), so a value containing "." or "," would round-trip through
    # this assertion as "\." / "\," -- a fact about escaping, not about preview,
    # that a plain rendering test has no business depending on.
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)

    markdown = service.preview(
        created.id,
        {"customer": {"legal_name": "ACME Srl"}, "amount": "1500"},
        ADMIN,
    )

    assert markdown == "Gentile ACME Srl, offerta di 1500."


def test_preview_fails_precisely_when_a_required_declared_variable_is_missing(
    db_session: Session,
) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        service.preview(created.id, {"customer": {"legal_name": "ACME Srl"}}, ADMIN)

    assert excinfo.value.details["field"] == "amount"


def test_preview_fails_precisely_on_an_unresolved_path(db_session: Session) -> None:
    service = TemplateService(db_session)
    created = service.create(_create(), ADMIN)

    with pytest.raises(ValidationFailed) as excinfo:
        service.preview(created.id, {"amount": "1.500,00"}, ADMIN)

    assert excinfo.value.details["field"] == "cliente.ragione_sociale"


def test_preview_missing_template_raises_not_found(db_session: Session) -> None:
    with pytest.raises(NotFound):
        TemplateService(db_session).preview(uuid4(), {}, ADMIN)


def test_a_select_variable_without_options_is_rejected() -> None:
    with pytest.raises(ValueError):
        TemplateVariable(nome="colore", label="Colore", type="select", required=False)


def test_declared_variables_is_a_public_method_that_drops_options(db_session: Session) -> None:
    """Task 11's `create_document_from_template` calls
    `self.templates.declared_variables(template)` directly (plan lines 5110, 5158),
    against a `Template` ORM row it already has in hand -- so this must be a real
    method on `TemplateService`, not a module-private helper, and it must build the
    `DeclaredVariable` the renderer expects: `options` (schemas.py's own addition for
    the compilation form) is not one of that dataclass's fields, so it is dropped
    here, not carried through."""
    service = TemplateService(db_session)
    created = service.create(
        _create(
            declared_variables=[
                {
                    "nome": "colore",
                    "etichetta": "Colore",
                    "tipo": "select",
                    "obbligatoria": True,
                    "options": ["rosso", "blu"],
                }
            ]
        ),
        ADMIN,
    )
    template = TemplateRepository(db_session).get(created.id)
    assert template is not None

    declared = service.declared_variables(template)

    assert declared == (
        DeclaredVariable(nome="colore", label="Colore", type="select", required=True),
    )


# --- the default offer (spec 2026-09-12 §6.5) --------------------------------------

# What the body may read without the form asking for it: `customer` and `emittente`
# come from `DocumentTemplating.template_scope`, and so does `today`.
_SCOPE_PREFIXES = ("cliente.", "emittente.")
_SCOPE_KEYS = {"today"}


def test_seed_creates_a_default_offer_a_space_can_render_without_editing(
    db_session: Session,
) -> None:
    service = TemplateService(db_session)
    created = service.seed_defaults(ADMIN)
    offer = next(t for t in created if t.nome == OFFER_TEMPLATE_NAME)
    assert offer.type == "offerta"
    assert offer.active is True
    declared = {v.nome for v in offer.declared_variables}
    assert declared == {"oggetto", "ambito", "attivita", "compenso", "pagamento"}
    # Every placeholder in the body is either declared or supplied by the document
    # scope, so the first render of a fresh space cannot fail on a missing variable.
    placeholders = set(re.findall(r"\{\{([a-z_.]+)\}\}", offer.body_markdown))
    for name in placeholders:
        assert name in declared or name in _SCOPE_KEYS or name.startswith(_SCOPE_PREFIXES), name
    assert "{{#each" not in offer.body_markdown


def test_seed_skips_the_offer_when_a_template_with_that_name_exists(
    db_session: Session,
) -> None:
    service = TemplateService(db_session)
    service.create(
        TemplateCreate(nome=OFFER_TEMPLATE_NAME, type="offer", body_markdown="mia"),
        ADMIN,
    )
    created = service.seed_defaults(ADMIN)
    assert OFFER_TEMPLATE_NAME not in {t.nome for t in created}
    kept = TemplateRepository(db_session).get_by_name(OFFER_TEMPLATE_NAME)
    assert kept is not None and kept.body_markdown == "mia"


def test_the_default_offer_renders_with_the_five_answers_and_the_document_scope(
    db_session: Session,
) -> None:
    """What `DocumentService.create_from_template` does with it: the declared answers
    plus `customer`, `emittente` and `today` from the record. No hole, no error."""
    service = TemplateService(db_session)
    created = service.seed_defaults(ADMIN)
    offer = next(t for t in created if t.nome == OFFER_TEMPLATE_NAME)
    row = TemplateRepository(db_session).get(offer.id)
    assert row is not None
    scope = {
        "oggetto": "Consulenza CTO",
        "ambito": "Tre mesi di guida tecnica.",
        "attivita": "Revisione dell'architettura e del team.",
        "compenso": "1.000 euro al giorno, due giorni a settimana.",
        "pagamento": "Fattura mensile, 30 giorni.",
        "cliente": {"ragione_sociale": "ACME S.r.l."},
        "emittente": {"ragione_sociale": "Ada Lovelace"},
        "oggi": "2026-09-12",
    }
    markdown = render_template(row.body_markdown, scope, service.declared_variables(row))
    # The renderer escapes Markdown in values ("S\\.r\\.l\\."), so match the plain words.
    assert "ACME" in markdown and "Consulenza CTO" in markdown and "Ada Lovelace" in markdown
    assert "{{" not in markdown
