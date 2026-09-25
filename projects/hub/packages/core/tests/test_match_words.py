"""REB-477: what a match and its documents are doing, and what comes next, in the words
the page, the «Match» list and the MCP tools all read. Pure functions: no database."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from rebase_core.contract_schemas import (
    ContractDocumentRead,
    LetteraFields,
    MatchRead,
    SendReport,
)
from rebase_core.documenso import REJECTED, Outcome
from rebase_core.match_words import (
    DOCUMENT_STATE_LABELS,
    MATCH_STATE_LABELS,
    DocumentFacts,
    check_sentences,
    document_words,
    match_words,
    send_report_sentence,
)
from rebase_core.models import MATCH_STATES
from rebase_core.signing import (
    CANCELLED_BY_REBASE,
    CANCELLED_ON_DOCUMENSO,
    CANCELLED_WITH_MATCH,
    REFUSED_ON_SITE,
    _cancel_reason,
)

SENT = date(2026, 9, 25)
SIGNED = date(2026, 9, 28)
RENEWAL = date(2027, 9, 28)
LAST_NOTICE = date(2027, 8, 29)
NOTICE = date(2027, 3, 2)
START, END = "1° ottobre 2026", "31 dicembre 2026"
# Every reason the hub stores for a cancelled document, each already a sentence; the
# sentence a page shows for it, the reason itself with one full stop; and whether it is
# the freelancer's refusal, the one reason a cancelled match's own sentence repeats.
STORED_REASONS = [
    (CANCELLED_BY_REBASE, "Annullato da rebase.", False),
    (CANCELLED_WITH_MATCH, "Annullato da rebase con il suo match.", False),
    (CANCELLED_ON_DOCUMENSO, "Annullato su Documenso.", False),
    (REFUSED_ON_SITE, "Rifiutato dal freelance sul sito di firma.", True),
    (
        _cancel_reason(Outcome("envelope", REJECTED, reason="il periodo non va")),
        "Rifiutato dal freelance: il periodo non va.",
        True,
    ),
    (
        _cancel_reason(Outcome("envelope", REJECTED, reason="il periodo non va.")),
        "Rifiutato dal freelance: il periodo non va.",
        True,
    ),
    (
        _cancel_reason(Outcome("envelope", REJECTED, reason="perché così poco?")),
        "Rifiutato dal freelance: perché così poco?",
        True,
    ),
    (
        _cancel_reason(Outcome("envelope", REJECTED, reason="no grazie!")),
        "Rifiutato dal freelance: no grazie!",
        True,
    ),
    (
        _cancel_reason(Outcome("envelope", REJECTED, reason="ci devo pensare…")),
        "Rifiutato dal freelance: ci devo pensare…",
        True,
    ),
]


def _quadro(stato: str, **facts: object) -> DocumentFacts:
    return DocumentFacts(kind="quadro", stato=stato, **facts)  # type: ignore[arg-type]


def _lettera(stato: str, **facts: object) -> DocumentFacts:
    return DocumentFacts(kind="lettera", stato=stato, numero="2026-003", **facts)  # type: ignore[arg-type]


# ---- the labels -----------------------------------------------------------------------


def test_every_match_state_has_its_admin_label() -> None:
    assert MATCH_STATE_LABELS == {
        "bozza": "Da inviare",
        "in_firma": "In attesa di firma",
        "attivo": "Attivo",
        "concluso": "Concluso",
        "annullato": "Annullato",
    }
    assert set(MATCH_STATE_LABELS) == set(MATCH_STATES)


def test_every_document_state_has_its_admin_label() -> None:
    assert DOCUMENT_STATE_LABELS == {
        "generato": "Pronto, non inviato",
        "in_attesa": "Parte dopo il contratto quadro",
        "inviato": "Da firmare",
        "firmato": "Firmato",
        "annullato": "Annullato",
        "disdetto": "Disdetto",
    }


# ---- a document -----------------------------------------------------------------------


def test_a_framework_generated_leaves_with_its_match() -> None:
    assert document_words(_quadro("generato")) == (
        "Pronto, non ancora inviato: parte con «Invia per la firma» sul match.",
        None,
        ["annulla"],
    )


def test_a_letter_generated_is_ready_and_has_no_action_of_its_own() -> None:
    assert document_words(_lettera("generato")) == ("Pronta, non ancora inviata.", None, [])


def test_a_waiting_letter_leaves_by_itself() -> None:
    assert document_words(_lettera("in_attesa")) == (
        "Parte da sola dopo la firma del contratto quadro.",
        None,
        [],
    )


def test_a_framework_out_for_signature_is_mailed_again_and_can_be_cancelled() -> None:
    assert document_words(_quadro("inviato", sent_on=SENT)) == (
        "Inviato il 25 settembre 2026: aspetta la firma del freelance.",
        "reinvia_email",
        ["aggiorna_stato", "annulla"],
    )


def test_a_letter_out_for_signature_is_mailed_again_and_goes_with_its_match() -> None:
    assert document_words(_lettera("inviato", sent_on=SENT)) == (
        "Inviata il 25 settembre 2026: aspetta la firma del freelance.",
        "reinvia_email",
        ["aggiorna_stato"],
    )


def test_a_signed_framework_without_its_copy_is_refreshed_first() -> None:
    assert document_words(
        _quadro(
            "firmato",
            signed_on=SIGNED,
            attivo=True,
            rinnovo=RENEWAL,
            ultimo_giorno_disdetta=LAST_NOTICE,
        )
    ) == (
        "Firmato il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "aggiorna_stato",
        ["registra_disdetta"],
    )


def test_a_signed_letter_without_its_copy_is_refreshed_first() -> None:
    assert document_words(_lettera("firmato", signed_on=SIGNED)) == (
        "Firmata il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "aggiorna_stato",
        [],
    )


def test_an_active_framework_says_when_it_renews_and_until_when_notice_is_given() -> None:
    assert document_words(
        _quadro(
            "firmato",
            signed_on=SIGNED,
            ha_pdf_firmato=True,
            attivo=True,
            rinnovo=RENEWAL,
            ultimo_giorno_disdetta=LAST_NOTICE,
        )
    ) == (
        "Firmato il 28 settembre 2026. Si rinnova da solo il 28 settembre 2027; disdetta "
        "entro il 29 agosto 2027.",
        None,
        ["aggiorna_stato", "registra_disdetta"],
    )


def test_a_signed_framework_no_longer_active_can_only_be_refreshed() -> None:
    assert document_words(_quadro("firmato", signed_on=SIGNED, ha_pdf_firmato=True)) == (
        "Firmato il 28 settembre 2026, non più attivo.",
        None,
        ["aggiorna_stato"],
    )


def test_a_signed_letter_with_its_copy_has_nothing_left_to_do() -> None:
    assert document_words(_lettera("firmato", signed_on=SIGNED, ha_pdf_firmato=True)) == (
        "Firmata il 28 settembre 2026.",
        None,
        [],
    )


@pytest.mark.parametrize(("reason", "sentence", "refusal"), STORED_REASONS)
@pytest.mark.parametrize("make", [_quadro, _lettera])
def test_a_cancelled_document_says_its_stored_reason_alone(
    make: Callable[..., DocumentFacts], reason: str, sentence: str, refusal: bool
) -> None:
    assert document_words(make("annullato", cancel_reason=reason)) == (sentence, None, [])


def test_a_cancelled_document_without_a_reason_says_so() -> None:
    assert document_words(_quadro("annullato")) == ("Annullato.", None, [])
    assert document_words(_lettera("annullato")) == ("Annullata.", None, [])


def test_a_framework_with_a_notice_says_when() -> None:
    assert document_words(_quadro("disdetto", signed_on=SIGNED, notice_on=NOTICE)) == (
        "Disdetto il 2 marzo 2027.",
        None,
        [],
    )


def test_a_framework_whose_text_is_a_draft_says_so_after_its_sentence() -> None:
    sentence, next_action, others = document_words(
        _quadro("inviato", sent_on=SENT, testo_bozza=True)
    )
    assert sentence == (
        "Inviato il 25 settembre 2026: aspetta la firma del freelance. Il testo è ancora in bozza."
    )
    assert (next_action, others) == ("reinvia_email", ["aggiorna_stato", "annulla"])
    # A letter's text in draft is not the admin's news: the framework's is.
    assert document_words(_lettera("generato", testo_bozza=True))[0] == (
        "Pronta, non ancora inviata."
    )


# ---- a match --------------------------------------------------------------------------


def test_a_draft_match_is_sent_next() -> None:
    assert match_words("bozza", _lettera("in_attesa"), None, START, None) == (
        "Da inviare: la lettera n. 2026-003 è pronta, il freelance non ha ancora ricevuto nulla.",
        "invia",
        ["annulla"],
    )


def test_waiting_letter_with_framework_out_has_no_next_step() -> None:
    assert match_words("in_firma", _lettera("in_attesa"), "inviato", START, None) == (
        "La lettera n. 2026-003 aspetta la firma del contratto quadro e parte da sola dopo.",
        None,
        ["annulla"],
    )


def test_waiting_letter_with_an_active_framework_is_ready_to_leave() -> None:
    """The framework agreement was signed and the letter's release was missed (a mail
    sender missing, Documenso down): «Invia per la firma» sends the letter itself."""
    assert match_words("in_firma", _lettera("in_attesa"), "firmato", START, None) == (
        "La lettera n. 2026-003 è pronta a partire: il contratto quadro è già firmato.",
        "invia",
        ["annulla"],
    )


@pytest.mark.parametrize(
    ("framework_stato", "sentence"),
    [
        # None active or pending: never written, or cancelled or refused since.
        (
            None,
            "La lettera n. 2026-003 aspetta un contratto quadro: «Invia per la firma» ne genera "
            "uno nuovo.",
        ),
        # Written for a later draft and never sent: the send takes that one.
        (
            "generato",
            "La lettera n. 2026-003 parte dopo il contratto quadro: «Invia per la firma» lo manda "
            "al freelance.",
        ),
    ],
)
def test_waiting_letter_without_framework_out_is_sent_again(
    framework_stato: str | None, sentence: str
) -> None:
    assert match_words("in_firma", _lettera("in_attesa"), framework_stato, START, None) == (
        sentence,
        "invia",
        ["annulla"],
    )


def test_a_match_whose_letter_is_out_for_signature_mails_it_again() -> None:
    assert match_words("in_firma", _lettera("inviato", sent_on=SENT), None, START, None) == (
        "Lettera n. 2026-003 inviata il 25 settembre 2026: aspetta la firma del freelance.",
        "reinvia_email",
        ["aggiorna_stato", "annulla"],
    )


@pytest.mark.parametrize(("stato", "other"), [("in_firma", "annulla"), ("attivo", "chiudi")])
def test_a_match_whose_signed_letter_has_no_copy_yet_is_refreshed(stato: str, other: str) -> None:
    assert match_words(stato, _lettera("firmato", signed_on=SIGNED), None, START, None) == (
        "Lettera n. 2026-003 firmata il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "aggiorna_stato",
        [other],
    )


@pytest.mark.parametrize(
    ("end", "period"),
    [(END, ", dal 1° ottobre 2026 al 31 dicembre 2026"), (None, ", dal 1° ottobre 2026")],
)
def test_an_active_match_says_when_its_letter_was_signed_and_its_period(
    end: str | None, period: str
) -> None:
    letter = _lettera("firmato", signed_on=SIGNED, ha_pdf_firmato=True)
    assert match_words("attivo", letter, None, START, end) == (
        f"Attivo: lettera n. 2026-003 firmata il 28 settembre 2026{period}.",
        None,
        ["chiudi"],
    )


def test_a_closed_match_says_its_period() -> None:
    letter = _lettera("firmato", signed_on=SIGNED, ha_pdf_firmato=True)
    assert match_words("concluso", letter, None, START, END) == (
        "Concluso: lettera n. 2026-003, dal 1° ottobre 2026 al 31 dicembre 2026.",
        None,
        [],
    )
    assert match_words("concluso", letter, None, None, None)[0] == "Concluso: lettera n. 2026-003."


@pytest.mark.parametrize(("reason", "sentence", "refusal"), [*STORED_REASONS, (None, "", False)])
def test_a_cancelled_match_says_nothing_is_to_be_signed_and_why_only_for_a_refusal(
    reason: str | None, sentence: str, refusal: bool
) -> None:
    words = match_words("annullato", _lettera("annullato", cancel_reason=reason), None, START, None)
    because = f" {sentence}" if refusal else ""
    assert words == (
        f"Annullato: la lettera n. 2026-003 non va più firmata.{because}",
        None,
        [],
    )
    assert "Annullato: Annullato" not in words[0]


@pytest.mark.parametrize(("reason", "sentence", "refusal"), [*STORED_REASONS, (None, "", False)])
def test_a_match_in_signature_whose_letter_was_refused_can_still_be_cancelled(
    reason: str | None, sentence: str, refusal: bool
) -> None:
    """Not a row of the design's table: a refusal on the signing site cancels the letter
    and leaves its match `in_firma`, and today's page still offers «Annulla» there."""
    words = match_words("in_firma", _lettera("annullato", cancel_reason=reason), None, START, None)
    because = f" {sentence}" if refusal else ""
    assert words == (f"Lettera n. 2026-003 annullata.{because}", None, ["annulla"])


@pytest.mark.parametrize(
    "letter",
    [_lettera("generato"), _lettera("firmato", signed_on=SIGNED, ha_pdf_firmato=True)],
)
def test_a_match_in_signature_with_a_letter_no_row_names_still_reads_and_cancels(
    letter: DocumentFacts,
) -> None:
    """Neither combination is written by the hub today; the page must still read."""
    assert match_words("in_firma", letter, None, START, None) == (
        "In attesa di firma: lettera n. 2026-003.",
        None,
        ["annulla"],
    )


def test_a_match_state_the_words_do_not_know_reads_as_itself() -> None:
    assert match_words("sospeso", _lettera("generato"), None, START, None) == (
        "sospeso: lettera n. 2026-003.",
        None,
        [],
    )


# ---- the check before saving ----------------------------------------------------------


def _letter(**change: object) -> LetteraFields:
    fields: dict[str, object] = {
        "ruolo": "Backend developer",
        "attivita": "Le API del prodotto.",
        "data_inizio": date(2026, 10, 1),
        "compenso": Decimal("450"),
        "modalita": "a giornata",
        "unita": "a giornata",
        "giorni_pagamento": 30,
        "fine_mese": True,
    }
    fields.update(change)
    return LetteraFields(**fields)  # type: ignore[arg-type]


def test_the_check_of_a_day_rate_names_the_person_the_client_the_period_and_the_fee() -> None:
    riepilogo, cosa_succede = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(luogo="da remoto", data_fine=date(2026, 12, 31), impegno="3 mesi"),
        "da_inviare",
        dati_fiscali_mancanti=False,
    )
    assert riepilogo == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, da remoto, dal 1° "
        "ottobre 2026 al 31 dicembre 2026.",
        "Impegno: 3 mesi.",
        "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese.",
    ]
    assert cosa_succede == (
        "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma."
    )


def test_the_check_of_a_lump_sum_without_the_optional_lines_and_without_tax_data() -> None:
    riepilogo, cosa_succede = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(
            compenso=Decimal("12000.5"),
            modalita="a corpo",
            unita=None,
            giorni_pagamento=60,
            fine_mese=False,
        ),
        "in_firma",
        dati_fiscali_mancanti=True,
    )
    assert riepilogo == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
        "Compenso: 12.000,50 € a corpo, IVA esclusa, pagato a 60 giorni.",
        "Mancano i dati fiscali del freelance: servono prima di salvare.",
    ]
    assert cosa_succede == (
        "Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua "
        "firma."
    )


def test_the_check_with_an_active_framework_sends_the_letter_at_once() -> None:
    riepilogo, cosa_succede = check_sentences(
        "Ada Lovelace", "ACME S.r.l.", _letter(), "attivo", dati_fiscali_mancanti=False
    )
    assert (
        riepilogo[-1] == "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese."
    )
    assert cosa_succede == "Il contratto quadro è già attivo: parte subito la lettera di incarico."


def test_the_check_prints_the_period_once_even_if_the_commitment_ends_with_one() -> None:
    riepilogo, _ = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(impegno="Tre giorni a settimana,\nper tre mesi."),
        "attivo",
        dati_fiscali_mancanti=False,
    )
    assert riepilogo[1] == "Impegno: Tre giorni a settimana, per tre mesi."


# ---- what «Invia per la firma» did ------------------------------------------------------


def _report(inviato: str | None, mail_inviata: bool | None) -> SendReport:
    """Only the letter's number is read: the rest of the match does not matter here."""
    letter = ContractDocumentRead.model_construct(numero="2026-001")
    match = MatchRead.model_construct(lettera=letter)
    return SendReport.model_construct(match=match, inviato=inviato, mail_inviata=mail_inviata)


@pytest.mark.parametrize(
    ("inviato", "mail_inviata", "sentence"),
    [
        (
            "quadro",
            True,
            "Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua "
            "firma.",
        ),
        ("lettera", True, "Partita la lettera di incarico n. 2026-001."),
        (
            None,
            None,
            "La lettera n. 2026-001 aspetta il contratto quadro già in firma e partirà da sola "
            "dopo.",
        ),
        (
            "lettera",
            False,
            "Partita la lettera di incarico n. 2026-001. La mail però non è partita: usa "
            "«Reinvia email».",
        ),
    ],
)
def test_the_send_report_says_which_document_left_and_whether_its_mail_did(
    inviato: str | None, mail_inviata: bool | None, sentence: str
) -> None:
    """The web's `sendReportMessage` word for word (REB-390): the pages and the MCP tool
    answer the same sentence after «Invia per la firma»."""
    assert send_report_sentence(_report(inviato, mail_inviata)) == sentence
