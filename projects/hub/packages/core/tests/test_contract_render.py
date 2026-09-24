"""The real render, the way the API runs it: pandoc and Typst over both contracts.

Needs both binaries and fontTools. Every machine that runs the hub's suite has them: the
Nix shell and the `contract-pdf`/`guide-pdf` preflight checks need the same pair, and the
hub's CI job installs it (`renderer: true` in ci.yml). This is the one file that proves
the markers, the template and the signature query against the real tools; every service
test hands `FakeRenderer` instead.
"""

import re
import unicodedata
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from rebase_core.cli import main
from rebase_core.contracts.fields import Value, read_layer
from rebase_core.contracts.render import (
    A4_HEIGHT_PT,
    A4_WIDTH_PT,
    DOCUMENTS,
    ContractRenderer,
    company_defaults,
    render,
    signature_blanks,
    text_is_draft,
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
    """A value is a Typst string, so markup inside it is inert, and it is
    inserted after the markers are counted, so a `[[` or a `{{...}}` in it is neither a
    proposal nor a field."""
    cliente = 'Rossi & "Figli" #1 $x$ *uno* _due_ <tre> @quattro \\ S.r.l.'
    attivita = "Correzioni [[in corso]] e {{non-un-campo}} al 10% // senza /* commenti */"
    data = {
        **_example(),
        "cliente-ragione-sociale": cliente,
        "attivita": attivita,
    }
    rendered = render("lettera-di-incarico", data)
    assert rendered.pdf.startswith(b"%PDF-")
    assert set(rendered.blank) == LETTER_BLANKS
    reader = PdfReader(BytesIO(rendered.pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert cliente in text
    assert attivita in text


def test_the_contracts_check_command_typesets_both_texts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["contracts-check"]) == 0
    out = capsys.readouterr().out
    for document in DOCUMENTS:
        assert f"{document}: " in out
    assert out.count("spazi da firmare") == len(DOCUMENTS)


def _words(pdf: bytes) -> str:
    """The PDF's text with every space removed and every ligature undone, so a label is
    found whatever pypdf does with the glyph runs of a small grey word."""
    reader = PdfReader(BytesIO(pdf))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def test_the_copy_for_signing_leaves_the_signing_blanks_labels_undrawn() -> None:
    """Probe § 11.9: the grey label under a blank the signing site fills shows through
    the signature and the date. The copy that goes out lays it out and does not draw it,
    so every blank stays exactly where the plain copy has it."""
    data = _example()
    plain = _words(render("lettera-di-incarico", data).pdf)
    signing = _words(render("lettera-di-incarico", data, signing=True).pdf)
    assert "firmaprofessionista" in plain and "datafirma" in plain
    assert "firmaprofessionista" not in signing and "datafirma" not in signing
    assert signature_blanks("lettera-di-incarico", data, signing=True) == signature_blanks(
        "lettera-di-incarico", data
    )


def test_the_renderer_says_whether_a_text_is_a_draft_as_its_render_does() -> None:
    for document in DOCUMENTS:
        assert text_is_draft(document) is render(document, _example()).draft
        assert ContractRenderer().is_draft(document) is text_is_draft(document)
