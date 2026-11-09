"""The sentences a person reads when the FatturaPA checks refuse, and when a record is missing.

The machine names stay English in the error's details (`code`, `entity`, `field`); the message
names the field in the words of the screen where it is edited.
"""

import ast
import importlib
from pathlib import Path
from uuid import uuid4

import pytest

import pigrocrm.core as core_package
from pigrocrm.core.contracts.schemas import check_expense_policy
from pigrocrm.core.errors import NotFound, ValidationFailed
from pigrocrm.core.invoices.fatturapa.checks import (
    check_document_text_exportable,
    check_party_exportable,
    check_recipient_identity,
    check_recipient_routing,
)
from pigrocrm.core.invoices.schemas import PartySnapshot
from pigrocrm.core.labels import ENTITY_NOUN, FIELD_PATH, entity_not_found, field_path
from pigrocrm.core.reminders.catalogue import get_type

CORE_ROOT = Path(core_package.__file__).parent
REQUIRED = "campo obbligatorio per la fattura elettronica"


def _party(**overrides: object) -> PartySnapshot:
    base: dict[str, object] = {
        "legal_name": "Acme S.r.l.",
        "vat_number": "12345678901",
        "tax_code": None,
        "sdi_code": "ABCDEFG",
        "certified_email": None,
        "address": "Corso Italia 5",
        "postal_code": "00100",
        "city": "Roma",
        "province": "RM",
        "country": "IT",
        "email": None,
        "phone": None,
        "website": None,
    }
    base.update(overrides)
    return PartySnapshot(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("entity", "noun"), [("legal_entity", "azienda emittente"), ("customer", "cliente")]
)
@pytest.mark.parametrize(
    ("field", "word"),
    [
        ("address", "indirizzo"),
        ("postal_code", "CAP"),
        ("city", "comune"),
        ("province", "provincia"),
    ],
)
def test_a_missing_required_field_names_the_party_and_the_field_in_words(
    entity: str, noun: str, field: str, word: str
) -> None:
    with pytest.raises(ValidationFailed) as caught:
        check_party_exportable(_party(**{field: ""}), entity)

    assert caught.value.message == f"{noun}, {word}: {REQUIRED}"
    assert caught.value.code == "validation_failed"
    assert caught.value.details["entity"] == entity
    assert caught.value.details["field"] == field
    assert caught.value.details["reason"] == REQUIRED


def test_the_legal_name_and_the_country_are_named_in_words_too() -> None:
    with pytest.raises(ValidationFailed) as name:
        check_party_exportable(_party(legal_name=" "), "legal_entity")
    assert name.value.message == "azienda emittente, ragione sociale: campo obbligatorio"
    assert name.value.details["field"] == "legal_name"

    with pytest.raises(ValidationFailed) as country:
        check_party_exportable(_party(country="Italia"), "customer")
    assert country.value.message == "cliente, nazione: codice paese non valido"
    assert country.value.details["field"] == "country"


def test_a_malformed_cap_and_province_read_in_words() -> None:
    with pytest.raises(ValidationFailed) as postal_code:
        check_party_exportable(_party(postal_code="1234"), "customer")
    assert postal_code.value.message == "cliente, CAP: CAP non valido"

    with pytest.raises(ValidationFailed) as province:
        check_party_exportable(_party(province="Roma"), "legal_entity")
    assert province.value.message == "azienda emittente, provincia: sigla non valida"


def test_a_foreign_address_does_not_need_a_province_or_a_cap() -> None:
    check_party_exportable(_party(country="GB", postal_code="", province=""), "customer")


def test_the_recipient_routing_and_identity_read_in_words() -> None:
    with pytest.raises(ValidationFailed) as routing:
        check_recipient_routing(_party(sdi_code=None, certified_email=None))
    assert routing.value.message.startswith("cliente, codice destinatario (SDI): serve un codice")
    assert routing.value.details["field"] == "sdi_code"
    assert "sdi_code" not in str(routing.value.details["expected"])

    with pytest.raises(ValidationFailed) as identity:
        check_recipient_identity(_party(vat_number=None, tax_code=None))
    assert identity.value.message.startswith("cliente, partita IVA: serve un identificativo")
    assert identity.value.details["field"] == "vat_number"


def test_a_character_the_schema_refuses_is_named_by_field_in_words() -> None:
    with pytest.raises(ValidationFailed) as party:
        check_party_exportable(_party(city="Москва"), "customer")
    assert party.value.message.startswith("cliente, comune: ")
    assert party.value.details["field"] == "city"

    with pytest.raises(ValidationFailed) as memo:
        check_document_text_exportable("causale Москва", [])
    assert memo.value.message.startswith("fattura, causale: ")
    assert memo.value.details["entity"] == "invoice"
    assert memo.value.details["field"] == "memo"


def test_a_field_without_words_reads_as_its_machine_path() -> None:
    assert field_path("legal_entity", "address") == "azienda emittente, indirizzo"
    assert field_path("widget", "colour") == "widget.colour"
    assert ValidationFailed("customer", "vat_number", "x").message == "customer.vat_number: x"
    assert (
        ValidationFailed("customer", "vat_number", "x", in_words=True).message
        == "cliente, partita IVA: x"
    )


def _core_modules() -> list[tuple[str, ast.Module]]:
    found = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        source = path.read_text()
        if "ValidationFailed(" in source:
            name = ".".join(("pigrocrm.core", *path.relative_to(CORE_ROOT).with_suffix("").parts))
            found.append((name.removesuffix(".__init__"), ast.parse(source)))
    return found


def _fatturapa_refusals() -> list[tuple[str, ast.Call]]:
    """Every `ValidationFailed(...)` of the FatturaPA checks, the writer, and the XML file name."""
    wanted = {
        "pigrocrm.core.invoices.fatturapa.checks",
        "pigrocrm.core.invoices.fatturapa.exporter",
        "pigrocrm.core.invoices.artifacts",
    }
    calls = []
    for name, tree in _core_modules():
        if name not in wanted:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ValidationFailed"
            ):
                calls.append((name, node))
    return calls


def test_every_refusal_of_the_fatturapa_checks_names_its_field_in_words() -> None:
    calls = _fatturapa_refusals()
    # checks.py alone raises a dozen; a scan that finds none would pass for the wrong reason.
    assert len(calls) >= 15
    for name, call in calls:
        flags = {keyword.arg: keyword.value for keyword in call.keywords}
        in_words = flags.get("in_words")
        assert isinstance(in_words, ast.Constant) and in_words.value is True, (
            f"{name}:{call.lineno} refuses without naming the field in words"
        )


def test_every_literal_entity_and_field_the_fatturapa_checks_refuse_has_its_words() -> None:
    for name, call in _fatturapa_refusals():
        entity, field = call.args[0], call.args[1]
        if isinstance(entity, ast.Constant) and isinstance(field, ast.Constant):
            assert f"{entity.value!s}.{field.value!s}" in FIELD_PATH, f"{name}:{call.lineno}"


def test_the_fields_the_checks_pass_through_check_text_have_their_words() -> None:
    for path in (
        "invoice.memo",
        "invoice.purchase_order_number",
        "invoice.purchase_order_project_code",
        "invoice.purchase_order_tender_code",
        "invoice_line.description",
        "invoice_line.unit_of_measure",
        "fiscal_profile.legal_reference",
        "legal_entity.email",
        "customer.certified_email",
        "legal_entity.tax_code",
        "legal_entity.vat_number",
        "fiscal_profile.regime_code",
    ):
        assert path in FIELD_PATH, path


def test_no_words_are_the_machine_path_itself() -> None:
    for path, words in FIELD_PATH.items():
        assert words != path
        assert "_" not in words, path


def test_a_missing_record_reads_in_italian_and_keeps_its_code_and_details() -> None:
    identifier = uuid4()
    err = NotFound("organization", identifier)
    assert err.message == f"cliente {identifier} non trovato"
    assert err.code == "not_found"
    assert err.details == {"entity": "organization", "identifier": str(identifier)}

    assert NotFound("invoice", "x").message == "fattura x non trovata"
    assert NotFound("fiscal_profile", "x").message == "profilo fiscale x non trovato"
    assert entity_not_found("unheard_of", "x") == "unheard_of x non trovato"


def test_a_missing_record_can_say_what_to_do_about_it() -> None:
    err = NotFound("fiscal_profile", "x", message="compilalo in Impostazioni")
    assert err.message == "compilalo in Impostazioni"
    assert err.code == "not_found"
    assert err.details["entity"] == "fiscal_profile"


def _not_found_entities() -> set[str]:
    """The entity every `NotFound(...)` of the core names, read from the source: a string
    literal as it is, a module constant (`ENTITY`) by importing the module that holds it."""
    entities: set[str] = set()
    for path in sorted(CORE_ROOT.rglob("*.py")):
        source = path.read_text()
        if "NotFound(" not in source:
            continue
        module = importlib.import_module(
            ".".join(
                ("pigrocrm.core", *path.relative_to(CORE_ROOT).with_suffix("").parts)
            ).removesuffix(".__init__")
        )
        for node in ast.walk(ast.parse(source)):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "NotFound"
                and node.args
            ):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                entities.add(first.value)
            elif isinstance(first, ast.Name) and first.id.isupper():
                entities.add(getattr(module, first.id))
    return entities


def test_every_entity_a_not_found_names_has_its_noun() -> None:
    entities = _not_found_entities()
    assert len(entities) > 30
    # The records a note, a mail draft and a task reference by a variable: the parents.
    entities |= {"organization", "person", "deal", "contract"}
    assert sorted(entity for entity in entities if entity not in ENTITY_NOUN) == []


def test_the_nouns_agree_with_the_participle() -> None:
    for entity, (noun, ending) in ENTITY_NOUN.items():
        assert ending in ("o", "a"), entity
        assert "_" not in noun, entity


def test_a_list_of_accepted_codes_is_worded_in_italian() -> None:
    """The refusals that list the values a field accepts name them as a person reads them: the
    codes 0068 renamed (reminder types, expense policy kinds) never print as English."""
    with pytest.raises(ValidationFailed) as reminder:
        get_type("nope")
    listed = str(reminder.value.details["expected"]).split(", ")
    assert "attivita_due" in listed and "attivita_overdue" in listed
    assert "task_due" not in listed and "task_overdue" not in listed

    with pytest.raises(ValueError, match=r"non_rimborsabile, rimborsabile, rimborsabile_con_tetto"):
        check_expense_policy({"kind": "nope"})
    with pytest.raises(ValueError, match=r"vale solo per rimborsabile_con_tetto"):
        check_expense_policy({"kind": "reimbursable", "amount_cap": "10"})
