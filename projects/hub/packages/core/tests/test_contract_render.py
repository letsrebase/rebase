"""The real render, the way the API runs it: pandoc and Typst over both contracts.

Needs both binaries and fontTools. Every machine that runs the hub's suite has them: the
Nix shell and the `contract-pdf`/`guide-pdf` preflight checks need the same pair, and the
hub's CI job installs it (`renderer: true` in ci.yml). This is the one file that proves
the markers, the template and the signature query against the real tools; every service
test hands `FakeRenderer` instead.
"""

from pathlib import Path

import pytest

from rebase_core.cli import main
from rebase_core.contracts.fields import Value, read_layer
from rebase_core.contracts.render import (
    A4_HEIGHT_PT,
    A4_WIDTH_PT,
    DOCUMENTS,
    company_defaults,
    render,
    signature_blanks,
    text_version,
)

EXAMPLE = Path(__file__).resolve().parents[3] / "content" / "contratti" / "incarico.esempio.json"
# 55 mm, the room a pen needs, in points.
SIGNATURE_WIDTH_PT = 155.0
LETTER_BLANKS = {"data-firma", "firma-rebase", "firma-professionista", "rebase-rappresentante"}


def _example() -> dict[str, Value]:
    """rebase's defaults and the public example, with the date of signing left blank as
    the hub leaves it for the signing site."""
    data = {**company_defaults(), **read_layer(EXAMPLE.read_text(encoding="utf-8"), EXAMPLE.name)}
    data["data-firma"] = None
    return data


def test_both_contracts_typeset_into_a_pdf_that_carries_its_version() -> None:
    for document in DOCUMENTS:
        rendered = render(document, _example())
        assert rendered.pdf.startswith(b"%PDF-"), document
        assert rendered.version == text_version(document), document
        assert rendered.draft is True, document


def test_a_filled_letter_leaves_blank_only_what_nobody_typed_ahead() -> None:
    assert set(render("lettera-di-incarico", _example()).blank) == LETTER_BLANKS


def test_the_framework_marks_two_signatures_and_one_date_for_the_freelancer() -> None:
    blanks = signature_blanks("contratto-quadro", _example())
    names = [blank.name for blank in blanks]
    # The contract's own signature and the specific approval of the onerous clauses.
    assert names.count("firma professionista") == 2
    assert names.count("data firma") == 1
    for blank in blanks:
        assert blank.page >= 1
        assert 0 <= blank.x < A4_WIDTH_PT and 0 <= blank.y < A4_HEIGHT_PT
        assert blank.width > 0 and blank.height > 0
    signatures = [blank for blank in blanks if blank.name == "firma professionista"]
    assert all(blank.width >= SIGNATURE_WIDTH_PT for blank in signatures)
    first, approval = signatures
    assert (approval.page, approval.y) > (first.page, first.y)


def test_a_letter_marks_one_signature_and_one_date() -> None:
    names = [blank.name for blank in signature_blanks("lettera-di-incarico", _example())]
    assert names.count("firma professionista") == 1
    assert names.count("data firma") == 1


def test_a_blank_the_data_fill_is_not_marked() -> None:
    data = {
        **_example(),
        "firma-rebase": "Documento emesso da rebase il 1° ottobre 2026",
        "rebase-rappresentante": "Nome Cognome",
    }
    names = [blank.name for blank in signature_blanks("lettera-di-incarico", data)]
    assert "firma rebase" not in names
    assert names.count("firma professionista") == 1


def test_a_value_full_of_typst_syntax_prints_as_text() -> None:
    """Review Focus 5: a value is a Typst string, so markup inside it is inert, and it is
    inserted after the markers are counted, so a `[[` or a `{{...}}` in it is neither a
    proposal nor a field."""
    data = {
        **_example(),
        "cliente-ragione-sociale": 'Rossi & "Figli" #1 $x$ *uno* _due_ <tre> @quattro \\ S.r.l.',
        "attivita": "Correzioni [[in corso]] e {{non-un-campo}} al 10% // senza /* commenti */",
    }
    rendered = render("lettera-di-incarico", data)
    assert rendered.pdf.startswith(b"%PDF-")
    assert set(rendered.blank) == LETTER_BLANKS


def test_the_contracts_check_command_typesets_both_texts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["contracts-check"]) == 0
    out = capsys.readouterr().out
    for document in DOCUMENTS:
        assert f"{document}: " in out
    assert out.count("spazi da firmare") == len(DOCUMENTS)
