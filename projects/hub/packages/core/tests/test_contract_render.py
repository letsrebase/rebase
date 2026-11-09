"""The real render, the way the API runs it: pandoc and Typst over every contract.

Needs both binaries and fontTools. Every machine that runs the hub's suite has them: the
Nix shell and the `contract-pdf`/`guide-pdf` preflight checks need the same pair, and the
hub's CI job installs it (`renderer: true` in ci.yml). This is the one file that proves
the markers, the template and the signature query against the real tools; every service
test hands `FakeRenderer` instead.
"""

import re
import struct
import unicodedata
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from rebase_core.cli import main
from rebase_core.contracts import brand
from rebase_core.contracts.fields import ContractFailed, Value, read_layer
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

EXAMPLE = Path(__file__).resolve().parents[3] / "content" / "contracts" / "engagement.example.json"
# 55 mm, the room a pen needs, in points.
SIGNATURE_WIDTH_PT = 155.0
LETTER_BLANKS = {
    "data-firma",
    "firma-rebase",
    "firma-professionista",
    "rebase-rappresentante",
}


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
        assert rendered.draft is False, document


def test_a_filled_letter_leaves_blank_only_what_nobody_typed_ahead() -> None:
    assert set(render("engagement-letter", _example()).blank) == LETTER_BLANKS


def test_the_framework_marks_two_signatures_and_one_date_for_the_freelancer() -> None:
    blanks = signature_blanks("framework-agreement", _example())
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
    names = [blank.name for blank in signature_blanks("engagement-letter", _example())]
    assert names.count("firma professionista") == 1
    assert names.count("data firma") == 1


def test_a_blank_the_data_fill_is_not_marked() -> None:
    data = {
        **_example(),
        "firma-rebase": "Documento emesso da rebase il 1° ottobre 2026",
        "rebase-rappresentante": "Nome Cognome",
    }
    names = [blank.name for blank in signature_blanks("engagement-letter", data)]
    assert "firma rebase" not in names
    assert names.count("firma professionista") == 1


def test_a_value_full_of_typst_syntax_prints_as_text() -> None:
    """A value is a Typst string, so markup inside it is inert, and it is
    inserted after the markers are counted, so a `[[` or a `{{...}}` in it is neither a
    proposal nor a field."""
    customer = 'Rossi & "Figli" #1 $x$ *uno* _due_ <tre> @quattro \\ S.r.l.'
    activities = "Correzioni [[in corso]] e {{non-un-campo}} al 10% // senza /* commenti */"
    data = {
        **_example(),
        "cliente-ragione-sociale": customer,
        "attivita": activities,
    }
    rendered = render("engagement-letter", data)
    assert rendered.pdf.startswith(b"%PDF-")
    assert set(rendered.blank) == LETTER_BLANKS
    reader = PdfReader(BytesIO(rendered.pdf))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert customer in text
    assert activities in text


def test_the_contracts_check_command_typesets_both_texts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["contracts-check"]) == 0
    out = capsys.readouterr().out
    for document in DOCUMENTS:
        assert f"{document}: " in out
    assert out.count("signing blanks") == len(DOCUMENTS)


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
    plain = _words(render("engagement-letter", data).pdf)
    signing = _words(render("engagement-letter", data, signing=True).pdf)
    assert "firmaprofessionista" in plain and "datafirma" in plain
    assert "firmaprofessionista" not in signing and "datafirma" not in signing
    assert signature_blanks("engagement-letter", data, signing=True) == signature_blanks(
        "engagement-letter", data
    )


def test_the_renderer_says_whether_a_text_is_a_draft_as_its_render_does() -> None:
    for document in DOCUMENTS:
        assert text_is_draft(document) is render(document, _example()).draft
        assert ContractRenderer().is_draft(document) is text_is_draft(document)


def _png_size(png: bytes) -> tuple[int, int]:
    """A PNG's width and height in pixels, from its header chunk."""
    width, height = struct.unpack(">II", png[16:24])
    return width, height


def _pictures(pdf: bytes) -> list[list[tuple[int, int]]]:
    """Every page's pictures, as width and height in pixels. Read from the page's own
    resources, where Typst puts an image, because pypdf's `page.images` decodes each one
    and needs Pillow to do it."""
    pages = []
    for page in PdfReader(BytesIO(pdf)).pages:
        xobjects = page["/Resources"].get_object().get("/XObject") or {}
        found = []
        for ref in xobjects.values():
            xobject = ref.get_object()
            if xobject["/Subtype"] == "/Image":
                found.append((int(xobject["/Width"]), int(xobject["/Height"])))
        pages.append(found)
    return pages


def test_the_echo_heads_the_first_page_and_no_other() -> None:
    """The title block prints the echo's document-size copy (REB-479), embedded as it is;
    the running header on the pages after the first keeps the words and draws no
    picture, since the echo has no compact variant."""
    expected = _png_size(brand.ECHO.read_bytes())
    for document in DOCUMENTS:
        pages = _pictures(render(document, _example()).pdf)
        assert len(pages) >= 2, document
        assert pages[0] == [expected], document
        assert all(page == [] for page in pages[1:]), document


def test_the_occasional_framework_marks_two_signatures_and_one_date() -> None:
    names = [blank.name for blank in signature_blanks("framework-agreement-occasional", _example())]
    assert names.count("firma professionista") == 2
    assert names.count("data firma") == 1


def test_an_occasional_letter_marks_one_signature_and_prints_its_term_from_the_receipt() -> None:
    """REB-687: the real render writes «data ricevuta», and a filled occasional letter
    leaves blank what the filled ordinary one does, the `codice fiscale` included in the
    example."""
    data = {**_example(), "giorni-incasso": None}
    names = [blank.name for blank in signature_blanks("engagement-letter-occasional", data)]
    assert names.count("firma professionista") == 1
    assert names.count("data firma") == 1
    rendered = render("engagement-letter-occasional", data)
    assert set(rendered.blank) == LETTER_BLANKS
    assert "30giornidataricevutafinemese" in _words(rendered.pdf)


def _flat(text: str) -> str:
    """`_words` of a sentence, and without the hyphens Typst breaks a long word at."""
    return re.sub(r"[\s-]+", "", unicodedata.normalize("NFKC", text))


def _printed(pdf: bytes) -> str:
    reader = PdfReader(BytesIO(pdf))
    return _flat("".join(page.extract_text() or "" for page in reader.pages))


@pytest.mark.parametrize(
    ("document", "paper"),
    [("engagement-letter", "fattura"), ("engagement-letter-occasional", "ricevuta")],
)
def test_a_letter_that_waits_for_the_client_prints_the_term_and_the_clause(
    document: str, paper: str
) -> None:
    """REB-979: with `giorni-incasso` the term and the paragraph after the table are
    printed, and the fixed term is not; without it the letter prints the fixed term and
    none of the clause."""
    fixed = _printed(render(document, {**_example(), "giorni-incasso": None}).pdf)
    rendered = render(document, {**_example(), "giorni-incasso": 15})
    waits = _printed(rendered.pdf)
    assert set(rendered.blank) == LETTER_BLANKS
    sentences = (
        "15 giorni dopo che il Cliente ha saldato rebase per lo stesso lavoro",
        f"e in ogni caso entro 60 giorni dal ricevimento della {paper}",
        "Pagamento a incasso.",
        "Quel pagamento decide solo quando rebase paga, non se paga",
        "il rischio resta di rebase",
        "vale come eccezione all\u2019articolo 7.2 del Contratto quadro",
    )
    for sentence in sentences:
        assert _flat(sentence) in waits, sentence
        assert _flat(sentence) not in fixed, sentence
    assert _flat(f"30 giorni data {paper} fine mese") in fixed
    assert _flat(f"30 giorni data {paper} fine mese") not in waits


@pytest.mark.parametrize("days", [0, 61])
def test_a_letter_with_collection_days_out_of_range_is_not_typeset(days: int) -> None:
    with pytest.raises(ContractFailed, match="from 1 to 60"):
        render("engagement-letter", {**_example(), "giorni-incasso": days})


@pytest.mark.parametrize(
    ("document", "paper"),
    [("framework-agreement", "fattura"), ("framework-agreement-occasional", "ricevuta")],
)
def test_both_frameworks_say_a_letter_may_tie_the_term_to_the_clients_payment(
    document: str, paper: str
) -> None:
    rendered = render(document, _example())
    assert rendered.version == text_version(document) == "1.1"
    printed = _printed(rendered.pdf)
    for sentence in (
        "il rischio è di rebase. La Lettera di incarico può però legare il termine di pagamento"
        " a quello del Cliente",
        "dopo che il Cliente ha saldato rebase per lo stesso lavoro",
        f"rebase paga entro 60 giorni dal ricevimento della {paper} (articolo 7.1) e il rischio"
        " resta di rebase",
    ):
        assert _flat(sentence) in printed, sentence
