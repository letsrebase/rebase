"""The contract build's own logic, which is everything in it that is not pandoc or Typst.

`tools/build_contract_pdf.py` turns `content/contratti/` into PDFs nobody commits, so
there is no artefact to hash the way `test_guide_pdf.py` does. What can go wrong without
anybody noticing is smaller and worse: a letter that prints a fee other than the one
agreed, a field typed into the Markdown that no data file knows about, or a signed text
that still carries a proposal nobody decided. The `contract-pdf` preflight check runs
the real build on a machine with both binaries.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from build_contract_pdf import (
    COMPANY,
    CONTRACTS,
    FIELD,
    Failed,
    Value,
    checked,
    fill,
    is_draft,
    italian,
    load_data,
    mark_proposals,
    rendered,
    survived,
)

EXAMPLE = CONTRACTS / "incarico.esempio.json"
# Signed in the member area or on paper, never typed into a data file ahead of time.
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
    with pytest.raises(Failed, match=complaint):
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
    with pytest.raises(Failed, match=complaint):
        checked(data)


@pytest.mark.parametrize("raw", ["Infinity", "-Infinity", "NaN", "1e400"])
def test_a_data_file_with_no_printable_number_is_refused(tmp_path: Path, raw: str) -> None:
    data = tmp_path / "job.json"
    data.write_text('{"compenso": ' + raw + "}", encoding="utf-8")
    with pytest.raises(Failed, match="not a number a contract can print"):
        load_data(data)


def test_the_fee_is_written_the_italian_way_and_identifiers_as_given() -> None:
    assert checked({"compenso": 450})["compenso"] == 450
    assert rendered("compenso", 450) == "450,00 €"
    assert italian(Decimal("1234.5"), 2) == "1.234,50"
    assert rendered("compenso", 12000) == "12.000,00 €"
    assert rendered("numero", 2026001) == "2026001"
    assert rendered("giorni-preavviso", 30) == "30"
    assert rendered("risultati", "l'Incarico") == "l\u2019Incarico"


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
    with pytest.raises(Failed, match="no longer a draft"):
        mark_proposals(escaped, "doc", draft=False)
    with pytest.raises(Failed, match="against"):
        mark_proposals("\\[\\[15%", "doc", draft=True)


def test_a_marker_pandoc_stopped_passing_through_stops_the_build() -> None:
    markdown = "il {{cliente-sede}} e il [[15%]]"
    survived(markdown, "il {{cliente-sede}} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(Failed, match="1 fields"):
        survived(markdown, "il \\{\\{cliente-sede\\}\\} e il \\[\\[15%\\]\\]", "doc")
    with pytest.raises(Failed, match="1 proposals"):
        survived(markdown, "il {{cliente-sede}} e il [[15%]]", "doc")


def test_every_contract_has_the_front_matter_the_page_reads() -> None:
    sources = sorted(CONTRACTS.glob("*.md"))
    assert {path.stem for path in sources} == {"contratto-quadro", "lettera-di-incarico"}
    for path in sources:
        markdown = path.read_text(encoding="utf-8")
        is_draft(markdown, path.name)
        for key in ("title", "version", "date"):
            assert f"\n{key}:" in markdown.split("\n---\n", 1)[0], f"{path.name} lacks {key}"


def test_the_example_names_every_field_the_letter_asks_for() -> None:
    """A field added to the letter and not to the example is a field nobody knows to fill."""
    letter = (CONTRACTS / "lettera-di-incarico.md").read_text(encoding="utf-8")
    asked = set(FIELD.findall(letter))
    company = json.loads(COMPANY.read_text(encoding="utf-8"))
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
