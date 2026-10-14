"""The contract build's own logic, which is everything in it that is not pandoc or Typst.

Since REB-387 that logic is `rebase_core.contracts` and `tools/build_contract_pdf.py` is
the laptop's thin wrapper over it. What can go wrong without anybody noticing is small
and bad: a letter that prints a fee other than the one agreed, a field typed into the
Markdown that no data file knows about, a signed text that still carries a proposal
nobody decided, or the contracts' brand drifting away from the guide's. The real render
is `test_contract_render.py`.
"""

import json
import os
import re
import struct
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import build_guide_pdf
import pytest
from build_contract_pdf import CONTRACTS, load_data

from rebase_core.contracts import brand
from rebase_core.contracts.fields import (
    FIELD,
    ContractFailed,
    Value,
    checked,
    fill,
    front_matter,
    is_draft,
    italian,
    italian_date,
    mark_proposals,
    parse_italian_date,
    rendered,
    signer_data,
    survived,
)
from rebase_core.contracts.render import (
    ALL_TEXTS,
    COMPANY_DEFAULTS,
    DOCUMENTS,
    RECEIPT_TEXTS,
    REVIEW_TEXTS,
    TEXTS,
)
from rebase_core.regimes import TEXT_NAMES

EXAMPLE = CONTRACTS / "incarico.esempio.json"
PROPOSAL_EXAMPLE = CONTRACTS / "proposta.esempio.json"
# Signed on the signing site or on paper, never typed into a data file ahead of time.
SIGNATURES = {"firma-rebase", "firma-professionista", "firma-cliente"}
LETTERS = ("lettera-di-incarico", "lettera-di-incarico-occasionale")
OCCASIONAL_TEXTS = ("contratto-quadro-occasionale", "lettera-di-incarico-occasionale")


@pytest.mark.parametrize(
    ("fee", "complaint"),
    [
        # As text it would print whatever it says, unchecked.
        ("450", "JSON number"),
        # More precision than the page prints: 450.005 would print as 450,01.
        (450.005, "two decimals"),
        (-450, "above zero"),
        (0, "above zero"),
        # Past what Decimal holds at its default precision.
        (1e30, "not a number a contract can print"),
    ],
)
def test_a_fee_the_page_could_not_print_as_given_stops_the_build(
    fee: Value, complaint: str
) -> None:
    with pytest.raises(ContractFailed, match=complaint):
        checked({"compenso": fee})
    # A proposal's price (REB-689) is held to the same rules.
    with pytest.raises(ContractFailed, match=complaint):
        checked({"corrispettivo": fee})


def test_the_payment_term_is_written_from_the_days_and_the_month_end() -> None:
    assert checked({"giorni-pagamento": 30, "fine-mese": True})["termine-pagamento"] == (
        "30 giorni data fattura fine mese"
    )
    assert checked({"giorni-pagamento": 60})["termine-pagamento"] == "60 giorni data fattura"


@pytest.mark.parametrize(
    ("data", "complaint"),
    [
        # Counted from the end of the month, 45 days can reach 75 from the invoice.
        ({"giorni-pagamento": 45, "fine-mese": True}, "from 1 to 30"),
        ({"giorni-pagamento": 90}, "from 1 to 60"),
        ({"giorni-pagamento": "30"}, "whole number"),
        ({"giorni-pagamento": 30, "fine-mese": "sì"}, "true or false"),
        # Typed by hand it would skip the check above.
        ({"termine-pagamento": "90 giorni"}, "written from"),
    ],
)
def test_a_payment_term_past_the_law_stops_the_build(
    data: dict[str, Value], complaint: str
) -> None:
    with pytest.raises(ContractFailed, match=complaint):
        checked(data)


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", "1e400"])
def test_a_data_file_with_no_printable_number_is_refused(tmp_path: Path, raw: str) -> None:
    data = tmp_path / "job.json"
    data.write_text('{"compenso": ' + raw + "}", encoding="utf-8")
    with pytest.raises(ContractFailed, match="not a number a contract can print"):
        load_data(data)


def test_the_fee_is_written_the_italian_way_and_identifiers_as_given() -> None:
    assert checked({"compenso": 450})["compenso"] == 450
    assert rendered("compenso", 450) == "450,00 €"
    assert italian(Decimal("1234.5"), 2) == "1.234,50"
    assert rendered("compenso", 12000) == "12.000,00 €"
    assert rendered("corrispettivo", 600) == "600,00 €"
    assert rendered("numero", 2026001) == "2026001"
    assert rendered("giorni-preavviso", 30) == "30"
    assert rendered("risultati", "l'Incarico") == "l’Incarico"


def test_a_date_is_written_the_way_a_contract_writes_it() -> None:
    assert italian_date(date(2026, 10, 1)) == "1° ottobre 2026"
    assert italian_date(date(2026, 11, 2)) == "2 novembre 2026"
    assert italian_date(date(2027, 1, 31)) == "31 gennaio 2027"


def test_parse_italian_date_round_trips() -> None:
    """A letter printed before its match kept the dates as dates (REB-498) is read back
    from what it printed: every month, the first with its ordinal, the plain number on
    every other day, and nothing `italian_date` would not have written."""
    for month in range(1, 13):
        for day in (1, 2, 12, 28):
            printed = date(2026, month, day)
            assert parse_italian_date(italian_date(printed)) == printed
    assert parse_italian_date("1° ottobre 2026") == date(2026, 10, 1)
    assert parse_italian_date("12 ottobre 2026") == date(2026, 10, 12)
    assert parse_italian_date("31 dicembre 2026") == date(2026, 12, 31)
    for text in (
        "1 ottobre 2026",
        "12° ottobre 2026",
        "01 ottobre 2026",
        "31 febbraio 2026",
        "12 october 2026",
        "12 Ottobre 2026",
        "2026-10-12",
        "12 ottobre",
        "",
    ):
        with pytest.raises(ContractFailed):
            parse_italian_date(text)


def test_a_field_is_the_value_when_known_and_a_labelled_blank_otherwise() -> None:
    typst, blank = fill(
        'Tra {{professionista-nome}} e {{cliente-sede}} "x"',
        {
            "professionista-nome": 'Anna "Nina" Rossi',
            "cliente-sede": None,
        },
    )
    assert '#value("Anna \\"Nina\\" Rossi");' in typst
    assert '#field("cliente sede");' in typst
    assert blank == ["cliente-sede"]


def test_a_proposal_is_highlighted_in_a_draft_and_refused_in_a_final_text() -> None:
    escaped = "il \\[\\[15%\\]\\] degli importi"
    marked = mark_proposals(escaped, "doc", draft=True)
    assert marked == "il #proposal[15%]; degli importi"
    with pytest.raises(ContractFailed, match="no longer a draft"):
        mark_proposals(escaped, "doc", draft=False)
    with pytest.raises(ContractFailed, match="against"):
        mark_proposals("\\[\\[15%", "doc", draft=True)


def test_a_marker_pandoc_stopped_passing_through_stops_the_build() -> None:
    markdown = "il {{cliente-sede}} e il [[15%]]"
    survived(markdown, "il {{cliente-sede}} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(ContractFailed, match="1 fields"):
        survived(markdown, "il \\{\\{cliente-sede\\}\\} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(ContractFailed, match="1 proposals"):
        survived(markdown, "il {{cliente-sede}} e il [[15%]]", "doc")


def test_every_contract_has_the_front_matter_the_page_reads() -> None:
    sources = sorted(TEXTS.glob("*.md"))
    assert {path.stem for path in sources} == set(ALL_TEXTS)
    for path in sources:
        markdown = path.read_text(encoding="utf-8")
        matter = front_matter(markdown, path.name)
        is_draft(markdown, path.name)
        for key in ("title", "version", "date", "status"):
            assert matter.get(key), f"{path.name} lacks {key}"
    for letter in LETTERS:
        markdown = (TEXTS / f"{letter}.md").read_text(encoding="utf-8")
        assert "{{numero}}" in front_matter(markdown, letter)["subtitle"]


def test_the_texts_are_one_per_kind_and_regime() -> None:
    """REB-687: the four texts the registry names are the four in the package."""
    assert sorted(DOCUMENTS) == sorted(TEXT_NAMES.values())


def test_the_texts_for_review_are_drafts_no_flow_signs() -> None:
    """REB-689: the company's terms and proposal and the freelancer's confidentiality
    agreement typeset for the lawyer, and the registry, which drives the signing flow,
    names none of them. Wiring one in is a card of its own, after the review."""
    assert set(REVIEW_TEXTS).isdisjoint(DOCUMENTS)
    assert set(REVIEW_TEXTS).isdisjoint(TEXT_NAMES.values())
    for document in REVIEW_TEXTS:
        source = TEXTS / f"{document}.md"
        assert is_draft(source.read_text(encoding="utf-8"), source.name), document


def test_the_proposal_example_names_every_field_the_proposal_asks_for() -> None:
    asked = set(FIELD.findall((TEXTS / "proposta-azienda.md").read_text(encoding="utf-8")))
    company = json.loads(COMPANY_DEFAULTS.read_text(encoding="utf-8"))
    example = json.loads(PROPOSAL_EXAMPLE.read_text(encoding="utf-8"))
    known = set(checked({**company, **example}))
    assert asked - known - SIGNATURES == set()


def test_the_proposal_example_is_fiction() -> None:
    """The repository is public: the example names no real company and no real person."""
    example = json.loads(PROPOSAL_EXAMPLE.read_text(encoding="utf-8"))
    assert example["cliente-ragione-sociale"] == "Azienda Esempio S.r.l."
    assert example["professionista-nome"] == "Nome Cognome"
    assert set(example["cliente-piva"]) == {"0"}
    assert set(example["cliente-codice-destinatario"]) == {"0"}
    assert example["cliente-pec"].endswith("@pec.example.com")


@pytest.mark.parametrize("letter", LETTERS)
def test_the_example_names_every_field_the_letter_asks_for(letter: str) -> None:
    """A field added to a letter and not to the example is a field nobody knows to fill."""
    asked = set(FIELD.findall((TEXTS / f"{letter}.md").read_text(encoding="utf-8")))
    company = json.loads(COMPANY_DEFAULTS.read_text(encoding="utf-8"))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    # `termine-pagamento` is in no data file on purpose: the build writes it.
    known = set(checked({**company, **example}))
    assert asked - known - SIGNATURES == set()


def test_the_example_is_fiction() -> None:
    """The repository is public, so the example may name nobody real."""
    example = json.loads(Path(EXAMPLE).read_text(encoding="utf-8"))
    assert example["professionista-nome"] == "Nome Cognome"
    assert set(example["professionista-piva"]) == {"0"}
    assert set(example["professionista-cf"]) == {"X", "0"}
    assert set(example["cliente-piva"]) == {"0"}


def test_the_payment_term_of_an_occasional_letter_counts_from_the_receipt() -> None:
    """REB-687: a prestazione occasionale is paid on a ricevuta, not an invoice."""
    assert {"lettera-di-incarico-occasionale"} == RECEIPT_TEXTS
    assert checked({"giorni-pagamento": 30, "fine-mese": True}, receipt=True)[
        "termine-pagamento"
    ] == ("30 giorni data ricevuta fine mese")
    assert checked({"giorni-pagamento": 60}, receipt=True)["termine-pagamento"] == (
        "60 giorni data ricevuta"
    )


@pytest.mark.parametrize("document", OCCASIONAL_TEXTS)
def test_an_occasional_text_prints_no_partita_iva_no_vat_excluded_and_no_invoice(
    document: str,
) -> None:
    """Spec § 5: the occasional texts never print the collaborator's partita IVA, never
    price a fee «IVA esclusa» and never speak of a «fattura», outside the field names."""
    markdown = (TEXTS / f"{document}.md").read_text(encoding="utf-8")
    prose = FIELD.sub("", markdown)
    assert "professionista-piva" not in markdown
    assert "IVA esclusa" not in prose
    assert "fattur" not in prose.lower()
    assert "Professionista" not in prose
    matter = front_matter(markdown, document)
    assert (matter["version"], matter["date"], matter["status"]) == (
        "1.0",
        "12 ottobre 2026",
        "final",
    )


def test_the_occasional_quadro_keeps_the_ordinary_articles_and_adds_only_5_7() -> None:
    """Spec § 5.1: a copy of the ordinary text with the listed changes and no others, so
    the letter cites the same article numbers; the one new clause is 5.7, and rebase's
    paragraph loses the codice destinatario."""
    ordinary = (TEXTS / "contratto-quadro.md").read_text(encoding="utf-8")
    occasional = (TEXTS / "contratto-quadro-occasionale.md").read_text(encoding="utf-8")

    def headings(markdown: str) -> list[str]:
        return [line for line in markdown.splitlines() if line.startswith("## ")]

    def clauses(markdown: str) -> list[str]:
        return re.findall(r"^(\d+\.\d+|[A-D]\.|A\.\d) ", markdown, re.MULTILINE)

    assert [line.replace("fattura", "ricevuta") for line in headings(ordinary)] == headings(
        occasional
    )
    ordinary_clauses = clauses(ordinary)
    after = ordinary_clauses.index("5.6") + 1
    assert clauses(occasional) == [*ordinary_clauses[:after], "5.7", *ordinary_clauses[after:]]
    assert "rebase-codice-destinatario" not in occasional
    assert set(FIELD.findall(occasional)) == set(FIELD.findall(ordinary)) - {
        "professionista-piva",
        "rebase-codice-destinatario",
    }


def test_the_contracts_read_the_brand_exactly_as_the_guide_does() -> None:
    """`brand` is a copy of the guide script's reading, because that script's bytes are
    locked and a package cannot import a script: this keeps the two documents of one
    brand from drifting apart one hand-edited hex at a time."""
    assert brand.TOKENS == build_guide_pdf.TOKENS
    assert brand.WEIGHTS == build_guide_pdf.WEIGHTS
    assert brand.GRID_INK_SHARE == build_guide_pdf.GRID_INK_SHARE
    assert brand.PALETTE == build_guide_pdf.PALETTE
    assert brand.FONT == build_guide_pdf.FONT
    assert brand.palette() == build_guide_pdf.palette()


def test_the_echo_is_read_from_the_checkout_or_from_the_directory_nix_names(
    tmp_path: Path,
) -> None:
    """The echo is `shared/brand`'s own file in a checkout and in the image, which mirrors
    it; the Nix `hub-api` package names another brand directory with
    `REBASE_CONTRACTS_BRAND_DIR` (REB-403). `brand` reads the variable once, when it is
    imported, so a fresh interpreter is what shows the second case."""
    name = Path("echo") / "echo-ink-watermelon-outlines-260.png"
    assert brand.REPO / "shared" / "brand" / name == brand.ECHO
    assert brand.ECHO.is_file()
    printed = subprocess.run(
        [sys.executable, "-c", "from rebase_core.contracts import brand; print(brand.ECHO)"],
        env={**os.environ, "REBASE_CONTRACTS_BRAND_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert Path(printed) == tmp_path / name


def _png_size(path: Path) -> tuple[int, int]:
    """A PNG's width and height in pixels, from its header chunk."""
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def test_the_echo_a_contract_prints_is_the_brands_own_drawing_at_document_size() -> None:
    """Typst embeds a picture's own pixels, so the contracts take the copy
    `shared/brand/tools/build-echo.mjs` draws 260 pixels tall, 600 per inch at the 11 mm
    the title block prints it at, rather than the 2572x1222 file (REB-479). It is the
    same drawing: the full file's proportions, to within a pixel."""
    full = brand.REPO / "shared" / "brand" / "echo" / "echo-ink-watermelon-outlines.png"
    width, height = _png_size(brand.ECHO)
    full_width, full_height = _png_size(full)
    assert height == 260
    assert abs(width - full_width * height / full_height) < 1


def test_the_signer_setting_fills_only_rebases_own_fields() -> None:
    assert signer_data("") == {}
    assert signer_data("  ") == {}
    assert signer_data('{"rebase-rappresentante": "Nome Cognome"}') == {
        "rebase-rappresentante": "Nome Cognome"
    }
    with pytest.raises(ContractFailed, match="only the rebase-"):
        signer_data('{"professionista-nome": "Qualcuno"}')
    with pytest.raises(ContractFailed, match="cannot read REBASE_SIGNER_JSON"):
        signer_data("{non json")
