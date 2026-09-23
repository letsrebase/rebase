"""The contract build's own logic, which is everything in it that is not pandoc or Typst.

Since REB-387 that logic is `rebase_core.contracts` and `tools/build_contract_pdf.py` is
the laptop's thin wrapper over it. What can go wrong without anybody noticing is small
and bad: a letter that prints a fee other than the one agreed, a field typed into the
Markdown that no data file knows about, a signed text that still carries a proposal
nobody decided, or the contracts' brand drifting away from the guide's. The real render
is `test_contract_render.py`.
"""

import json
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
    rendered,
    survived,
)
from rebase_core.contracts.render import COMPANY_DEFAULTS, DOCUMENTS, TEXTS

EXAMPLE = CONTRACTS / "incarico.esempio.json"
# Signed on the signing site or on paper, never typed into a data file ahead of time.
SIGNATURES = {"firma-rebase", "firma-professionista"}


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
    assert rendered("numero", 2026001) == "2026001"
    assert rendered("giorni-preavviso", 30) == "30"
    assert rendered("risultati", "l'Incarico") == "l’Incarico"


def test_a_date_is_written_the_way_a_contract_writes_it() -> None:
    assert italian_date(date(2026, 10, 1)) == "1° ottobre 2026"
    assert italian_date(date(2026, 11, 2)) == "2 novembre 2026"
    assert italian_date(date(2027, 1, 31)) == "31 gennaio 2027"


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
    assert {path.stem for path in sources} == set(DOCUMENTS)
    for path in sources:
        markdown = path.read_text(encoding="utf-8")
        matter = front_matter(markdown, path.name)
        is_draft(markdown, path.name)
        for key in ("title", "version", "date", "status"):
            assert matter.get(key), f"{path.name} lacks {key}"
    letter = (TEXTS / "lettera-di-incarico.md").read_text(encoding="utf-8")
    assert "{{numero}}" in front_matter(letter, "lettera-di-incarico.md")["subtitle"]


def test_the_example_names_every_field_the_letter_asks_for() -> None:
    """A field added to the letter and not to the example is a field nobody knows to fill."""
    letter = (TEXTS / "lettera-di-incarico.md").read_text(encoding="utf-8")
    asked = set(FIELD.findall(letter))
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
    assert set(example["cliente-piva"]) == {"0"}


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
