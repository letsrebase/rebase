"""The carried reminder copy, rendered through slice 2's template machinery.

The one thing worth knowing before reading these: a reminder body is *plain text* in a
mail client, not Markdown on its way to Pandoc and Typst. The renderer's default
"markdown" context escapes the entire ASCII punctuation class -- correct for a document
that a compiler reads next, catastrophic for a body a human reads next, where it turns
`2026/14` into `2026\\/14` in front of a paying client. That is why every assertion
below goes through the plain context, and why one test proves the difference rather
than trusting a comment about it.
"""

import re

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.gmail.solleciti_template import (
    SOLLECITO_DECLARED_VARIABLES,
    SOLLECITO_TEMPLATE_SOURCE,
    level_flags,
    render_sollecito_body,
)
from pigrocrm.core.templates.renderer import render_template

VALUES = {
    "cliente": "Acme S.r.l.",
    "numero_fattura": "2026/14",
    "data_fattura": "01/07/2026",
    "scadenza": "31/07/2026",
    "importo": "1.200,00 €",
    "iban": "IT60X0542811101000000123456",
    "emittente": {
        "ragione_sociale": "Studio Rossi",
        "telefono": "+39 333 1234567",
        "sito_web": "https://studiorossi.it",
    },
    "firma_email": "Mario Rossi\nConsulente",
}


def test_the_carried_copy_keeps_previous_system_running_order() -> None:
    """Portato: this text went to real clients for years, and its order is the useful
    part -- invoice number, date, due date, amount, IBAN."""
    rendered = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    assert "Gentile Acme S.r.l." in rendered
    for line in [
        "Fattura: 2026/14",
        "Data fattura: 01/07/2026",
        "Scadenza: 31/07/2026",
        "Importo: 1.200,00 €",
        "IBAN: IT60X0542811101000000123456",
    ]:
        assert line in rendered
    assert rendered.index("Fattura: ") < rendered.index("Data fattura: ")
    assert rendered.index("Scadenza: ") < rendered.index("Importo: ")
    assert rendered.index("Importo: ") < rendered.index("IBAN: ")


def test_the_courtesy_clauses_that_earned_their_place_are_still_there() -> None:
    rendered = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    assert (
        "Qualora avesse già provveduto al pagamento, La preghiamo di ignorare questo messaggio."
        in rendered
    )
    assert "Sistema di Interscambio (SdI)" in rendered


def test_a_body_with_nothing_attached_does_not_promise_a_courtesy_copy() -> None:
    """The one imperfection B2-9 left behind, closed.

    `GmailRepository.invoice_pdf_version_ids` legitimately answers `[]` -- a proforma
    converted before the PDF existed, an invoice whose document was removed -- and the
    reminder still has to go out, because a missing file is not a reason to stop chasing
    a real debt. What must not go out is the sentence: a letter to a paying client
    saying «in allegato trova copia di cortesia della fattura» with nothing attached
    sends them looking for a file that is not there, and a client who cannot find the
    attachment has a reason to distrust the figure printed next to it.

    The SdI sentence stays either way: the original *was* transmitted electronically,
    which is true whether or not this email carries a courtesy copy.
    """
    rendered = render_sollecito_body(VALUES, livello=1, con_allegato=False)

    assert "In allegato" not in rendered
    assert "copia di cortesia" not in rendered
    assert "Sistema di Interscambio (SdI)" in rendered
    # And the rest of the letter is untouched: the figures a reminder exists to state.
    assert "IBAN: IT60X0542811101000000123456" in rendered
    assert "Importo: 1.200,00 €" in rendered


def test_dropping_the_promise_leaves_no_double_space_or_orphan_line() -> None:
    """The failure mode of a conditional spliced mid-paragraph. The clause carries its
    own trailing space *inside* the `{{#if}}`, so removing it must leave the paragraph
    beginning at «L'originale» and not at a space -- which is the kind of thing only the
    recipient ever sees."""
    without = render_sollecito_body(VALUES, livello=1, con_allegato=False)

    assert "L'originale è stato trasmesso" in without
    assert "  " not in without
    paragraphs = [block for block in without.split("\n\n") if block.strip()]
    assert any(block.startswith("L'originale") for block in paragraphs), paragraphs


def test_no_freelancers_name_appears_in_the_source() -> None:
    """Rifatta la sostanza: a CRM for Italian freelancers cannot carry one freelancer's
    name in its source -- and the previous system carried it twice, verbatim, in two builders."""
    for forbidden in ["Mario Rossi", "CTO", "studiorossi"]:
        assert forbidden.lower() not in SOLLECITO_TEMPLATE_SOURCE.lower()
    # The phone number that used to be pinned here by literal is gone from the repository,
    # so the assertion is on the shape instead: no number of any form survives in the
    # template. That is strictly stronger, since it also catches a different one arriving.
    assert re.search(r"\+?\d[\d\s.]{8,}", SOLLECITO_TEMPLATE_SOURCE) is None


def test_the_signature_comes_from_the_emitter_profile() -> None:
    rendered = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    assert "Mario Rossi" in rendered
    assert "Studio Rossi" in rendered
    # The phone and the website are read from emitter_profile rather than retyped into
    # firma_email, so the number cannot diverge between two places.
    assert "+39 333 1234567" in rendered
    assert "https://studiorossi.it" in rendered


def test_a_signature_keeps_its_own_line_breaks() -> None:
    """`firma_email` is a *block* -- name on one line, role on the next. The markdown
    and typst escapers both collapse every line terminator to a space, because a value
    landing in a table cell must not manufacture a row; a plain-text body has no such
    unit to protect, and collapsing here would run the whole signature together."""
    rendered = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    assert "Mario Rossi\nConsulente" in rendered


def test_an_emitter_without_a_phone_or_a_site_leaves_no_empty_label() -> None:
    """Both are nullable on `emitter_profile`. `tel. ` with nothing after it is the
    kind of detail a client notices and the sender never sees."""
    rendered = render_sollecito_body(
        {**VALUES, "emittente": {"ragione_sociale": "Studio Rossi"}, "firma_email": None},
        livello=1,
        con_allegato=True,
    )
    assert "tel." not in rendered
    assert "Studio Rossi" in rendered


def test_the_reminder_level_is_a_variable_and_not_three_templates() -> None:
    """Spec 7.3: the tone of the sequence is a template variable. Three templates that
    resemble each other diverge -- the same decision slice 2 made about the offer."""
    first = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    third = render_sollecito_body(VALUES, livello=3, con_allegato=True)
    assert first != third
    assert "sollecito" in first.lower()
    # By the third, the tone is firmer and says so, without inventing a legal threat.
    assert "terzo" in third.lower() or "ulteriore" in third.lower()


def test_exactly_one_level_branch_ever_renders() -> None:
    """The three flags are mutually exclusive by construction. If they were not, two
    opening sentences would arrive stacked in the same paragraph, which is the failure
    a `{{#if}}`-per-level template invites and no single-level test would catch."""
    for livello in (0, 1, 2, 3, 4, 99):
        flags = level_flags(livello)
        assert sum(flags.values()) == 1, flags


def test_a_level_beyond_the_third_reuses_the_third_wording() -> None:
    """`max_reminders` defaults to 3; inventing a fourth register would be inventing a
    legal threat this project has no standing to make."""
    assert level_flags(4) == level_flags(3)
    assert render_sollecito_body(VALUES, livello=4, con_allegato=True) == render_sollecito_body(
        VALUES, livello=3, con_allegato=True
    )


def test_a_missing_required_variable_names_the_template_line() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_sollecito_body(
            {key: value for key, value in VALUES.items() if key != "iban"},
            livello=1,
            con_allegato=True,
        )
    assert excinfo.value.details["field"] == "iban"
    assert "riga " in excinfo.value.details["reason"]


def test_the_body_is_rendered_plain_and_not_markdown_escaped() -> None:
    """The regression guard for the whole module: rendering this source through the
    renderer's *default* context escapes every ASCII punctuation character, so an
    invoice number, an amount and a website all reach the client wearing backslashes.
    Asserting that the default really does mangle it is what keeps this from being a
    comment somebody deletes.
    """
    plain = render_sollecito_body(VALUES, livello=1, con_allegato=True)
    as_markdown = render_template(
        SOLLECITO_TEMPLATE_SOURCE, {**VALUES, **level_flags(1)}, SOLLECITO_DECLARED_VARIABLES
    )
    assert "2026\\/14" in as_markdown
    assert "2026\\/14" not in plain
    assert "\\" not in plain


def test_the_declared_variables_match_the_paths_the_body_actually_reads() -> None:
    """Declared, not deduced (spec 4.3) -- but a declaration nobody references is a
    compilation-form field whose value never reaches the message."""
    from pigrocrm.core.templates.parser import declared_paths, parse_template

    root = {path[0] for path in declared_paths(parse_template(SOLLECITO_TEMPLATE_SOURCE)).root}
    declared = {variable.nome for variable in SOLLECITO_DECLARED_VARIABLES}
    assert declared <= root, f"declared but never used: {sorted(declared - root)}"
    # `emittente` is supplied whole by `AziendaService.as_template_values`, never
    # typed into a form, so it is deliberately not declared -- the same decision
    # `TIME_REPORT_TEMPLATE_VARIABLES` made about `voci`.
    assert root - declared == {"emittente"}
