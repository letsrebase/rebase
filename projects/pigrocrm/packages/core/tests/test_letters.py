"""The letters' pure rules and the real render (REB-1018, collaborators design § 1): the payment
term written from the engagement's rule and refused past the law, the one-time key map over the
hub's texts, the front matter and drafts, and pandoc and Typst over a seeded text with the
signature blanks located.
"""

import datetime as dt
import re
import shutil
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from pigrocrm.core.collaborators import letters
from pigrocrm.core.collaborators.letter_pdf import compile_text, signature_blanks, typeset
from pigrocrm.core.collaborators.letter_templates import (
    SCOPE_ROOTS,
    SEEDS,
    declared_variables,
    seed_body,
)
from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.templates.parser import parse_template

needs_binaries = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("typst") is None,
    reason="pandoc e typst vivono nell'immagine dell'API",
)


# ---- the payment term -----------------------------------------------------------------------


def test_a_fixed_term_is_written_from_its_days_and_where_they_count_from() -> None:
    assert (
        letters.payment_term("fixed_term", 30, True, None, receipt=False)
        == "30 giorni data fattura fine mese"
    )
    assert (
        letters.payment_term("fixed_term", 60, False, None, receipt=False)
        == "60 giorni data fattura"
    )
    # An occasional letter is paid on a ricevuta (REB-687).
    assert (
        letters.payment_term("fixed_term", 30, False, None, receipt=True)
        == "30 giorni data ricevuta"
    )


@pytest.mark.parametrize(
    ("days", "month_end"),
    [(61, False), (90, False), (31, True), (45, True), (0, False)],
)
def test_a_term_over_the_limit_of_law_81_2017_is_refused(days: int, month_end: bool) -> None:
    with pytest.raises(ValidationFailed, match="81/2017|1\\.\\.|da 1 a"):
        letters.payment_term("fixed_term", days, month_end, None, receipt=False)


def test_the_clause_that_pays_after_the_client_keeps_its_sentence_and_its_limits() -> None:
    assert letters.payment_term("on_collection", None, False, 10, receipt=False) == (
        "10 giorni dopo che il Cliente ha saldato rebase per lo stesso lavoro, e in ogni caso"
        " entro 60 giorni dal ricevimento della fattura"
    )
    assert letters.payment_term("on_collection", None, False, 1, receipt=True).startswith(
        "1 giorno dopo che il Cliente"
    )
    assert letters.payment_term("on_collection", None, False, 60, receipt=True).endswith(
        "ricevimento della ricevuta"
    )
    for bad in (0, 61):
        with pytest.raises(ValidationFailed, match="81/2017"):
            letters.payment_term("on_collection", None, False, bad, receipt=False)
    # A letter that waits for the client names no floor, and a fixed term has no wait.
    with pytest.raises(ValidationFailed):
        letters.payment_term("on_collection", 30, False, 10, receipt=False)
    with pytest.raises(ValidationFailed):
        letters.payment_term("fixed_term", 30, False, 10, receipt=False)


def test_a_fee_and_a_date_are_printed_as_a_contract_says_them() -> None:
    assert letters.italian_money(Decimal("1234.5")) == "1.234,50 €"
    assert letters.italian_money(Decimal("450")) == "450,00 €"
    assert letters.italian_date(dt.date(2026, 10, 1)) == "1° ottobre 2026"
    assert letters.italian_date(dt.date(2026, 11, 2)) == "2 novembre 2026"
    assert letters.days_per_week_words(Decimal("3")) == "3 giorni a settimana"
    assert letters.days_per_week_words(Decimal("2.5")) == "2,5 giorni a settimana"
    assert letters.days_per_week_words(Decimal("1")) == "1 giorno a settimana"


# ---- the text's front matter ----------------------------------------------------------------


def test_a_text_says_its_version_and_whether_it_is_a_draft() -> None:
    final = '---\ntitle: T\nversion: "1.2"\nstatus: final\n---\n\nbody'
    draft = final.replace("final", "draft")
    assert letters.check_front_matter(final) == {"title": "T", "version": "1.2", "status": "final"}
    assert letters.is_draft(draft) and not letters.is_draft(final)
    letters.refuse_draft(final, "T")
    with pytest.raises(ValidationFailed, match="bozza"):
        letters.refuse_draft(draft, "T")
    for broken in (
        "no front matter",
        "---\ntitle: T\nstatus: final\n---\n",
        final.replace("final", "maybe"),
    ):
        with pytest.raises(ValidationFailed):
            letters.check_front_matter(broken)


# ---- the key map ----------------------------------------------------------------------------


def test_the_key_map_turns_flat_keys_into_the_engines_dotted_ones() -> None:
    hub = "{{professionista-nome}} {{rebase-ragione-sociale}} {{periodo-verifica}} {{compenso}}"
    assert letters.convert_hub_text(hub) == (
        "{{professionista.nome}} {{emittente.ragione_sociale}} {{periodo_verifica}}"
        " {{incarico.compenso}}"
    )
    assert letters.convert_hub_text("{{se giorni-incasso}}\nclause\n{{fine se}}") == (
        "{{#if incarico.giorni_incasso}}\nclause\n{{/if}}"
    )
    # Prose, front matter and proposals stay as they are.
    assert (
        letters.convert_hub_text("title: Lettera\n[[15%]] cosa") == "title: Lettera\n[[15%]] cosa"
    )


@pytest.mark.parametrize("seed", SEEDS, ids=lambda seed: seed.file)
def test_every_text_converts_parses_and_keeps_no_flat_key(seed) -> None:  # type: ignore[no-untyped-def]
    body = seed_body(seed)
    assert not re.search(r"\{\{[a-z0-9]+-[a-z0-9-]+\}\}", body), "a flat key was left behind"
    assert "{{se " not in body and "{{fine se}}" not in body
    parse_template(body)
    front = letters.front_matter(body)
    assert front["version"] and front["status"] in ("draft", "final")
    for variable in declared_variables(body):
        assert variable.name not in SCOPE_ROOTS and variable.required is False


def test_the_texts_carry_the_versions_and_the_collection_clause_unchanged() -> None:
    by_file = {seed.file: seed_body(seed) for seed in SEEDS}
    versions = {name: letters.front_matter(body) for name, body in by_file.items()}
    # Framework 1.1 and letter 1.2 carry the pay-after-the-client clause of REB-979.
    assert versions["framework-agreement"]["version"] == "1.1"
    assert versions["engagement-letter"]["version"] == "1.2"
    assert versions["engagement-letter"]["status"] == "final"
    assert versions["freelancer-nda"]["status"] == "draft"
    letter = by_file["engagement-letter"]
    assert "{{#if incarico.giorni_incasso}}" in letter
    assert "**Pagamento a incasso.**" in letter
    assert "rebase paga entro 60 giorni dal ricevimento della fattura" in letter.replace("\n", " ")
    # The regime picks the identifier the letter prints: a VAT number, or the tax code.
    assert "{{professionista.partita_iva}}" in letter
    assert "{{professionista.partita_iva}}" not in by_file["engagement-letter-occasional"]
    assert "{{professionista.codice_fiscale}}" in by_file["engagement-letter-occasional"]


# ---- the real render ------------------------------------------------------------------------


def _scope(**incarico: object) -> dict[str, object]:
    return {
        "emittente": {
            "ragione_sociale": "rebase S.r.l.",
            "sede": "Via Roma 1, 20100 Milano (MI)",
            "codice_fiscale": "01234567890",
            "partita_iva": "01234567890",
            "codice_sdi": "ABC1234",
            "pec": "rebase@pec.it",
            "rappresentante": "Mario Rossi",
        },
        "professionista": {
            "nome": "Ada Lovelace",
            "partita_iva": "12345678901",
            "codice_fiscale": "LVLDAA15B10Z114X",
            "email": "ada@example.com",
            "pec": "non indicata",
            "domicilio": "Via dei Calcoli 2, 00100 Roma (RM)",
        },
        "cliente": {
            "ragione_sociale": "Acme S.r.l.",
            "partita_iva": "09876543210",
            "sede": "Via Milano 3, 10100 Torino (TO)",
        },
        "incarico": {
            "numero": "2026-007",
            "ruolo": "Backend developer",
            "data_inizio": "2 novembre 2026",
            "data_fine": "29 novembre 2026",
            "modalita": "a giornata",
            "compenso": "450,00 €",
            "unita": "al giorno",
            "termine_pagamento": "30 giorni data fattura fine mese",
            "giorni_incasso": None,
            **incarico,
        },
        "firma": {
            "luogo": "firmato elettronicamente",
            "data": None,
            "rebase": "Documento emesso da rebase S.r.l. il 9 novembre 2026",
            "professionista": None,
        },
        "attivita": "Sviluppo del backend",
    }


def _letter_text() -> str:
    (seed,) = [seed for seed in SEEDS if seed.file == "engagement-letter"]
    return seed_body(seed)


def _words(pdf: bytes) -> str:
    return " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)


@needs_binaries
def test_a_letter_typesets_with_the_fee_the_term_and_blank_lines_for_what_is_missing() -> None:
    compiled = compile_text(_letter_text(), _scope())
    rendered = typeset(compiled, "rebase S.r.l.", Settings())
    assert rendered.pdf.startswith(b"%PDF-")
    assert rendered.version == "1.2" and rendered.draft is False
    words = " ".join(_words(rendered.pdf).split())
    assert "450,00 €" in words and "30 giorni data fattura fine mese" in words
    assert "Ada Lovelace" in words and "Mario Rossi" in words and "Acme S.r.l." in words
    # Nothing that is not filled is printed as a placeholder: it is a labelled blank line.
    assert "{{" not in words and "PGBLANK" not in words
    assert "firma professionista" in rendered.blank and "data firma" in rendered.blank
    assert "data contratto quadro" in rendered.blank
    assert "periodo verifica" in rendered.blank
    # The clause that pays after the client is not printed when the engagement does not ask.
    assert "Pagamento a incasso" not in words


@needs_binaries
def test_the_clause_that_pays_after_the_client_is_printed_only_when_the_letter_asks() -> None:
    term = letters.payment_term("on_collection", None, False, 15, receipt=False)
    scope = _scope(**{"termine_pagamento": term, "giorni_incasso": 15})
    words = " ".join(
        _words(typeset(compile_text(_letter_text(), scope), "rebase", Settings()).pdf).split()
    )
    assert "Pagamento a incasso" in words
    assert "15 giorni dopo che il Cliente ha saldato rebase" in words


@needs_binaries
def test_the_signature_blanks_are_found_on_the_page_in_points() -> None:
    compiled = compile_text(_letter_text(), _scope())
    blanks = signature_blanks(compiled, "rebase S.r.l.", Settings())
    assert {blank.name for blank in blanks} == {"firma professionista", "data firma"}
    for blank in blanks:
        assert blank.page >= 1 and 0 <= blank.x < 596 and 0 <= blank.y < 842
        assert blank.width > 0 and blank.height > 0
    signature = next(blank for blank in blanks if blank.name == "firma professionista")
    assert signature.width == pytest.approx(155.9, abs=1.0)


@needs_binaries
def test_a_draft_text_carries_the_watermark_and_a_final_one_refuses_proposals() -> None:
    draft_body = (
        '---\ntitle: Bozza\nversion: "0.1"\ndate: 1 novembre 2026\nstatus: draft\n---\n\n'
        "Un [[15%]] di prova.\n"
    )
    rendered = typeset(compile_text(draft_body, {}), "rebase", Settings())
    assert rendered.draft is True
    assert "bozza" in " ".join(_words(rendered.pdf).split()).lower()
    final_body = draft_body.replace("draft", "final")
    with pytest.raises(ValidationFailed, match="proposte"):
        typeset(compile_text(final_body, {}), "rebase", Settings())
