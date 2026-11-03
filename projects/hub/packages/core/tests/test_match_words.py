"""REB-477: what a match and its documents are doing, and what comes next, in the words
the page, the «Match» list and the MCP tools all read. Pure functions: no database."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest

from rebase_core.contract_schemas import (
    ContractDocumentRead,
    LetterFields,
    MatchRead,
    SendReport,
)
from rebase_core.documenso import REJECTED, Outcome
from rebase_core.match_words import (
    DOCUMENT_STATE_LABELS,
    HAS_VAT_NUMBER,
    HTTPS_ONLY,
    MATCH_STATE_LABELS,
    ORDINARY_NEEDS_VAT_NUMBER,
    PIGRO_NOT_CONFIGURED,
    PROFILE_WITHOUT_NAME,
    WHAT_LEAVES_FIRST_OCCASIONAL,
    Action,
    DocumentFacts,
    check_sentences,
    document_words,
    match_words,
    pigro_state_sentence,
    send_report_sentence,
)
from rebase_core.models import MATCH_STATES
from rebase_core.signing_words import (
    CANCELLED_BY_REBASE,
    CANCELLED_ON_DOCUMENSO,
    CANCELLED_WITH_MATCH,
    REFUSED_ON_SITE,
    cancel_reason,
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
        cancel_reason(Outcome("envelope", REJECTED, reason="il periodo non va")),
        "Rifiutato dal freelance: il periodo non va.",
        True,
    ),
    (
        cancel_reason(Outcome("envelope", REJECTED, reason="il periodo non va.")),
        "Rifiutato dal freelance: il periodo non va.",
        True,
    ),
    (
        cancel_reason(Outcome("envelope", REJECTED, reason="perché così poco?")),
        "Rifiutato dal freelance: perché così poco?",
        True,
    ),
    (
        cancel_reason(Outcome("envelope", REJECTED, reason="no grazie!")),
        "Rifiutato dal freelance: no grazie!",
        True,
    ),
    (
        cancel_reason(Outcome("envelope", REJECTED, reason="ci devo pensare…")),
        "Rifiutato dal freelance: ci devo pensare…",
        True,
    ),
]


def _framework(status: str, **facts: object) -> DocumentFacts:
    return DocumentFacts(kind="framework", status=status, **facts)  # type: ignore[arg-type]


def _letter(status: str, **facts: object) -> DocumentFacts:
    return DocumentFacts(kind="letter", status=status, number="2026-003", **facts)  # type: ignore[arg-type]


# ---- the labels -----------------------------------------------------------------------


def test_every_match_state_has_its_admin_label() -> None:
    assert MATCH_STATE_LABELS == {
        "draft": "Da inviare",
        "awaiting_signature": "In attesa di firma",
        "active": "Attivo",
        "concluded": "Concluso",
        "cancelled": "Annullato",
    }
    assert set(MATCH_STATE_LABELS) == set(MATCH_STATES)


def test_every_document_state_has_its_admin_label() -> None:
    assert DOCUMENT_STATE_LABELS == {
        "generated": "Pronto, non inviato",
        "pending": "Parte dopo il contratto quadro",
        "sent": "Da firmare",
        "signed": "Firmato",
        "cancelled": "Annullato",
        "notice_served": "Disdetto",
    }


# ---- a document -----------------------------------------------------------------------


def test_a_framework_generated_leaves_with_its_match() -> None:
    assert document_words(_framework("generated")) == (
        "Parte con «Invia per la firma» sul suo match.",
        None,
        ["cancel"],
    )


def test_a_letter_generated_is_ready_and_has_no_action_of_its_own() -> None:
    assert document_words(_letter("generated")) == ("Pronta, non ancora inviata.", None, [])


def test_a_waiting_letter_leaves_by_itself() -> None:
    assert document_words(_letter("pending")) == (
        "Parte da sola dopo la firma del contratto quadro.",
        None,
        [],
    )


def test_a_framework_out_for_signature_is_mailed_again_and_can_be_cancelled() -> None:
    assert document_words(_framework("sent", sent_on=SENT)) == (
        "Inviato il 25 settembre 2026: aspetta la firma del freelance.",
        "resend_email",
        ["status_refresh", "cancel"],
    )


def test_a_letter_out_for_signature_is_mailed_again_and_goes_with_its_match() -> None:
    assert document_words(_letter("sent", sent_on=SENT)) == (
        "Inviata il 25 settembre 2026: aspetta la firma del freelance.",
        "resend_email",
        ["status_refresh"],
    )


def test_a_signed_framework_without_its_copy_is_refreshed_first() -> None:
    assert document_words(
        _framework(
            "signed",
            signed_on=SIGNED,
            active=True,
            renewal=RENEWAL,
            last_notice_day=LAST_NOTICE,
        )
    ) == (
        "Firmato il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "status_refresh",
        ["record_notice"],
    )


def test_a_signed_letter_without_its_copy_is_refreshed_first() -> None:
    assert document_words(_letter("signed", signed_on=SIGNED)) == (
        "Firmata il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "status_refresh",
        [],
    )


def test_an_active_framework_says_when_it_renews_and_until_when_notice_is_given() -> None:
    assert document_words(
        _framework(
            "signed",
            signed_on=SIGNED,
            has_pdf_signed=True,
            active=True,
            renewal=RENEWAL,
            last_notice_day=LAST_NOTICE,
        )
    ) == (
        "Firmato il 28 settembre 2026. Si rinnova da solo il 28 settembre 2027; disdetta "
        "entro il 29 agosto 2027.",
        None,
        ["status_refresh", "record_notice"],
    )


def test_a_signed_framework_no_longer_active_can_only_be_refreshed() -> None:
    assert document_words(_framework("signed", signed_on=SIGNED, has_pdf_signed=True)) == (
        "Firmato il 28 settembre 2026, non più attivo.",
        None,
        ["status_refresh"],
    )


def test_a_signed_letter_with_its_copy_has_nothing_left_to_do() -> None:
    assert document_words(_letter("signed", signed_on=SIGNED, has_pdf_signed=True)) == (
        "Firmata il 28 settembre 2026.",
        None,
        [],
    )


@pytest.mark.parametrize(("reason", "sentence", "refusal"), STORED_REASONS)
@pytest.mark.parametrize("make", [_framework, _letter])
def test_a_cancelled_document_says_its_stored_reason_alone(
    make: Callable[..., DocumentFacts], reason: str, sentence: str, refusal: bool
) -> None:
    assert document_words(make("cancelled", cancel_reason=reason)) == (sentence, None, [])


def test_a_cancelled_document_without_a_reason_says_so() -> None:
    assert document_words(_framework("cancelled")) == ("Annullato.", None, [])
    assert document_words(_letter("cancelled")) == ("Annullata.", None, [])


def test_a_framework_with_a_notice_says_when() -> None:
    assert document_words(_framework("notice_served", signed_on=SIGNED, notice_on=NOTICE)) == (
        "Disdetto il 2 marzo 2027.",
        None,
        [],
    )


def test_a_framework_whose_text_is_a_draft_says_so_after_its_sentence() -> None:
    sentence, next_action, others = document_words(
        _framework("sent", sent_on=SENT, draft_text=True)
    )
    assert sentence == (
        "Inviato il 25 settembre 2026: aspetta la firma del freelance. Il testo è ancora in bozza."
    )
    assert (next_action, others) == ("resend_email", ["status_refresh", "cancel"])
    # A letter's text in draft is not the admin's news: the framework's is.
    assert document_words(_letter("generated", draft_text=True))[0] == (
        "Pronta, non ancora inviata."
    )


# ---- a match --------------------------------------------------------------------------


def test_a_draft_match_is_sent_next() -> None:
    assert match_words("draft", _letter("pending"), None, START, None) == (
        "La lettera n. 2026-003 è pronta: il freelance non ha ancora ricevuto nulla.",
        "send",
        ["cancel"],
    )


def test_waiting_letter_with_framework_out_has_no_next_step() -> None:
    assert match_words("awaiting_signature", _letter("pending"), "sent", START, None) == (
        "La lettera n. 2026-003 aspetta la firma del contratto quadro e parte da sola dopo.",
        None,
        ["cancel"],
    )


def test_waiting_letter_with_an_active_framework_is_ready_to_leave() -> None:
    """The framework agreement was signed and the letter's release was missed (a mail
    sender missing, Documenso down): «Invia per la firma» sends the letter itself."""
    assert match_words("awaiting_signature", _letter("pending"), "signed", START, None) == (
        "La lettera n. 2026-003 è pronta a partire: il contratto quadro è già firmato.",
        "send",
        ["cancel"],
    )


@pytest.mark.parametrize(
    ("framework_status", "sentence"),
    [
        # None active or pending: never written, or cancelled or refused since.
        (
            None,
            "La lettera n. 2026-003 aspetta un contratto quadro: «Invia per la firma» ne genera "
            "uno nuovo.",
        ),
        # Written for a later draft and never sent: the send takes that one.
        (
            "generated",
            "La lettera n. 2026-003 parte dopo il contratto quadro: «Invia per la firma» lo manda "
            "al freelance.",
        ),
    ],
)
def test_waiting_letter_without_framework_out_is_sent_again(
    framework_status: str | None, sentence: str
) -> None:
    assert match_words("awaiting_signature", _letter("pending"), framework_status, START, None) == (
        sentence,
        "send",
        ["cancel"],
    )


def test_a_match_whose_letter_is_out_for_signature_mails_it_again() -> None:
    assert match_words("awaiting_signature", _letter("sent", sent_on=SENT), None, START, None) == (
        "Lettera n. 2026-003 inviata il 25 settembre 2026: aspetta la firma del freelance.",
        "resend_email",
        ["status_refresh", "cancel"],
    )


@pytest.mark.parametrize(
    ("status", "others"),
    [("awaiting_signature", ["cancel"]), ("active", ["close"]), ("concluded", [])],
)
def test_a_match_whose_signed_letter_has_no_copy_yet_is_refreshed(
    status: str, others: list[str]
) -> None:
    assert match_words(status, _letter("signed", signed_on=SIGNED), None, START, None) == (
        "Lettera n. 2026-003 firmata il 28 settembre 2026; la copia firmata non è ancora arrivata.",
        "status_refresh",
        others,
    )


@pytest.mark.parametrize(
    ("end", "period"),
    [(END, ", dal 1° ottobre 2026 al 31 dicembre 2026"), (None, ", dal 1° ottobre 2026")],
)
def test_an_active_match_says_when_its_letter_was_signed_and_its_period(
    end: str | None, period: str
) -> None:
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words("active", letter, None, START, end) == (
        f"Lettera n. 2026-003 firmata il 28 settembre 2026{period}.",
        None,
        ["close"],
    )


# ---- an active match's link to Pigro (REB-498, spec § 3.5) -----------------------------


def test_an_active_match_with_no_pigro_state_adds_nothing() -> None:
    """A match older than migration 0021, or read before the trigger ran: nothing to add
    yet."""
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words("active", letter, None, START, None, pigro_status=None) == (
        "Lettera n. 2026-003 firmata il 28 settembre 2026, dal 1° ottobre 2026.",
        None,
        ["close"],
    )


@pytest.mark.parametrize(
    ("pigro_status", "pigro_error", "note", "other_actions"),
    [
        ("linked", None, "Le ore si consuntivano su Pigro.", ["close"]),
        (
            "to_link",
            None,
            "Pigro non ha ancora il deal: riprova o aspetta lo sweep.",
            ["close", "retry_pigro"],
        ),
        (
            "error",
            "HTTP 503",
            "Pigro non ha risposto: HTTP 503.",
            ["close", "retry_pigro"],
        ),
        (
            "rejected",
            "Il deal di questa lettera è stato eliminato nello spazio.",
            "Pigro ha rifiutato il collegamento: Il deal di questa lettera è stato eliminato "
            "nello spazio.",
            ["close", "retry_pigro"],
        ),
    ],
)
def test_an_active_match_says_where_its_hours_stand_on_pigro(
    pigro_status: str, pigro_error: str | None, note: str, other_actions: list[Action]
) -> None:
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words(
        "active", letter, None, START, None, pigro_status=pigro_status, pigro_error=pigro_error
    ) == (
        f"Lettera n. 2026-003 firmata il 28 settembre 2026, dal 1° ottobre 2026. {note}",
        None,
        other_actions,
    )


@pytest.mark.parametrize("pigro_status", ["to_link", "error", "rejected", "linked"])
def test_without_the_token_an_active_match_says_the_report_is_not_configured(
    pigro_status: str,
) -> None:
    """Spec § 3.2: an environment without `REBASE_PIGRO_ENGAGEMENTS_TOKEN` has the
    feature off. The card says so, whatever the link's state, and offers no «Riprova»,
    which would only answer the same sentence with a 503."""
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words(
        "active",
        letter,
        None,
        START,
        None,
        pigro_status=pigro_status,
        pigro_error="HTTP 503",
        pigro_configured=False,
    ) == (
        "Lettera n. 2026-003 firmata il 28 settembre 2026, dal 1° ottobre 2026. "
        "Consuntivo non configurato su questo ambiente.",
        None,
        ["close"],
    )
    assert PIGRO_NOT_CONFIGURED == "Consuntivo non configurato su questo ambiente."


def test_without_the_token_a_match_with_no_pigro_state_still_adds_nothing() -> None:
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words(
        "active", letter, None, START, None, pigro_status=None, pigro_configured=False
    ) == (
        "Lettera n. 2026-003 firmata il 28 settembre 2026, dal 1° ottobre 2026.",
        None,
        ["close"],
    )


@pytest.mark.parametrize("sentence", [PROFILE_WITHOUT_NAME, HTTPS_ONLY])
def test_the_hubs_own_error_sentences_are_shown_alone(sentence: str) -> None:
    """A link the hub never sent: «Pigro non ha risposto» would say a call was made."""
    assert pigro_state_sentence("error", sentence) == sentence
    assert HTTPS_ONLY == "Pigro è raggiungibile solo su https."


def test_pigro_state_sentence_says_nothing_before_a_deal_or_once_one_is_linked() -> None:
    """`report`'s own `InvalidState` never needs a sentence for these two: a match not
    yet active has no report to refuse, and `linked` has one to answer instead."""
    assert pigro_state_sentence(None, None) == ""
    assert pigro_state_sentence("linked", None) == ""


def test_pigro_state_sentence_names_the_state_and_folds_in_the_crms_own_words() -> None:
    assert pigro_state_sentence("to_link", None) == (
        "Pigro non ha ancora il deal: riprova o aspetta lo sweep."
    )
    """The link stores the cause alone (`engagements.CAUSE_*`, or the CRM's own
    sentence), and this wraps it once: a bare cause gets its stop, a sentence keeps
    its own."""
    assert pigro_state_sentence("error", "timeout") == "Pigro non ha risposto: timeout."
    assert pigro_state_sentence("error", "HTTP 503") == "Pigro non ha risposto: HTTP 503."
    assert pigro_state_sentence("error", "Lo spazio non è raggiungibile.") == (
        "Pigro non ha risposto: Lo spazio non è raggiungibile."
    )
    assert pigro_state_sentence("rejected", "Il deal è stato eliminato nello spazio.") == (
        "Pigro ha rifiutato il collegamento: Il deal è stato eliminato nello spazio."
    )
    assert pigro_state_sentence("rejected", "HTTP 409") == (
        "Pigro ha rifiutato il collegamento: HTTP 409."
    )


def test_pigro_state_sentence_without_a_stored_error_still_says_pigro_did_not_answer() -> None:
    """`pigro_error` is typed `str | None`: an `error` state written with none stored
    (a race, or a row from before the column was backfilled) reads as a sentence, never
    «Pigro non ha risposto: None»."""
    assert pigro_state_sentence("error", None) == "Pigro non ha risposto."
    assert pigro_state_sentence("error", "  ") == "Pigro non ha risposto."
    assert pigro_state_sentence("rejected", None) == "Pigro ha rifiutato il collegamento."


def test_pigro_state_sentence_without_a_stored_error_still_says_pigro_refused() -> None:
    assert pigro_state_sentence("rejected", None) == "Pigro ha rifiutato il collegamento."


def test_a_closed_match_says_its_period() -> None:
    letter = _letter("signed", signed_on=SIGNED, has_pdf_signed=True)
    assert match_words("concluded", letter, None, START, END) == (
        "Lettera n. 2026-003, dal 1° ottobre 2026 al 31 dicembre 2026.",
        None,
        [],
    )
    assert match_words("concluded", letter, None, None, None)[0] == "Lettera n. 2026-003."


@pytest.mark.parametrize(("reason", "sentence", "refusal"), [*STORED_REASONS, (None, "", False)])
def test_a_cancelled_match_says_nothing_is_to_be_signed_and_why_only_for_a_refusal(
    reason: str | None, sentence: str, refusal: bool
) -> None:
    words = match_words("cancelled", _letter("cancelled", cancel_reason=reason), None, START, None)
    because = f" {sentence}" if refusal else ""
    assert words == (
        f"La lettera n. 2026-003 non va più firmata.{because}",
        None,
        [],
    )


@pytest.mark.parametrize(("reason", "sentence", "refusal"), [*STORED_REASONS, (None, "", False)])
def test_a_match_in_signature_whose_letter_was_refused_can_still_be_cancelled(
    reason: str | None, sentence: str, refusal: bool
) -> None:
    """Not a row of the design's table: a refusal on the signing site cancels the letter
    and leaves its match `awaiting_signature`, and today's page still offers «Annulla» there."""
    words = match_words(
        "awaiting_signature", _letter("cancelled", cancel_reason=reason), None, START, None
    )
    because = f" {sentence}" if refusal else ""
    assert words == (f"Lettera n. 2026-003 annullata.{because}", None, ["cancel"])


@pytest.mark.parametrize(
    "letter",
    [_letter("generated"), _letter("signed", signed_on=SIGNED, has_pdf_signed=True)],
)
def test_a_match_in_signature_with_a_letter_no_row_names_still_reads_and_cancels(
    letter: DocumentFacts,
) -> None:
    """Neither combination is written by the hub today; the page must still read."""
    assert match_words("awaiting_signature", letter, None, START, None) == (
        "Lettera n. 2026-003.",
        None,
        ["cancel"],
    )


@pytest.mark.parametrize("status", MATCH_STATES)
@pytest.mark.parametrize("framework", [None, "generated", "sent", "signed"])
@pytest.mark.parametrize(
    "letter",
    [
        _letter("generated"),
        _letter("pending"),
        _letter("sent", sent_on=SIGNED),
        _letter("signed", signed_on=SIGNED),
        _letter("signed", signed_on=SIGNED, has_pdf_signed=True),
        _letter("cancelled", cancel_reason="Annullato da rebase."),
    ],
)
def test_no_match_sentence_opens_with_the_label_the_pages_show_beside_it(
    status: str, framework: str | None, letter: DocumentFacts
) -> None:
    sentence = match_words(status, letter, framework, START, END)[0]
    assert not sentence.startswith(MATCH_STATE_LABELS[status])


def test_a_match_state_the_words_do_not_know_still_names_its_letter() -> None:
    assert match_words("sospeso", _letter("generated"), None, START, None) == (
        "Lettera n. 2026-003.",
        None,
        [],
    )


# ---- the check before saving ----------------------------------------------------------


def _letter(**change: object) -> LetterFields:
    fields: dict[str, object] = {
        "role": "Backend developer",
        "activities": "Le API del prodotto.",
        "start_date": date(2026, 10, 1),
        "fee": Decimal("450"),
        "mode": "to_work_unit",
        "unit": "to_work_unit",
        "payment_days": 30,
        "month_end": True,
    }
    fields.update(change)
    return LetterFields(**fields)  # type: ignore[arg-type]


def test_the_check_of_a_day_rate_names_the_person_the_client_the_period_and_the_fee() -> None:
    recap, what_happens = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(location="da remoto", data_fine=date(2026, 12, 31), commitment="3 mesi"),
        "to_send",
        missing_tax_data=False,
    )
    assert recap == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, da remoto, dal 1° "
        "ottobre 2026 al 31 dicembre 2026.",
        "Impegno: 3 mesi.",
        "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese.",
    ]
    assert what_happens == (
        "Prima parte il contratto quadro; la lettera di incarico parte da sola dopo la sua firma."
    )


def test_the_check_of_a_lump_sum_without_the_optional_lines_and_without_tax_data() -> None:
    recap, what_happens = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(
            fee=Decimal("12000.5"),
            mode="to_body",
            unit=None,
            payment_days=60,
            month_end=False,
        ),
        "awaiting_signature",
        missing_tax_data=True,
    )
    assert recap == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
        "Compenso: 12.000,50 € a corpo, IVA esclusa, pagato a 60 giorni.",
        "Mancano i dati fiscali del freelance: servono prima di salvare.",
    ]
    assert what_happens == (
        "Il contratto quadro è già in firma: la lettera di incarico parte da sola dopo la sua "
        "firma."
    )


def test_the_check_with_an_active_framework_sends_the_letter_at_once() -> None:
    recap, what_happens = check_sentences(
        "Ada Lovelace", "ACME S.r.l.", _letter(), "active", missing_tax_data=False
    )
    assert recap[-1] == "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese."
    assert what_happens == "Il contratto quadro è già attivo: parte subito la lettera di incarico."


def test_the_check_prints_the_period_once_even_if_the_commitment_ends_with_one() -> None:
    recap, _ = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(commitment="Tre giorni a settimana,\nper tre mesi."),
        "active",
        missing_tax_data=False,
    )
    assert recap[1] == "Impegno: Tre giorni a settimana, per tre mesi."


def test_the_check_of_an_occasional_lump_sum_says_gross_and_without_vat() -> None:
    """The fee of an occasional letter is gross and outside VAT."""
    recap, what_happens = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(fee=Decimal("5000"), mode="to_body", unit=None),
        "to_send",
        missing_tax_data=False,
        regime="occasional",
    )
    assert recap == [
        "Ada Lovelace lavorerà per ACME S.r.l. come Backend developer, dal 1° ottobre 2026.",
        "Compenso: 5.000,00 € a corpo, lordi, senza IVA, pagato a 30 giorni fine mese.",
    ]
    assert what_happens == WHAT_LEAVES_FIRST_OCCASIONAL["to_send"]


@pytest.mark.parametrize(
    ("step", "sentence"),
    [
        (
            "to_send",
            "Prima parte il contratto quadro per prestazioni occasionali; la lettera di "
            "incarico occasionale parte da sola dopo la sua firma.",
        ),
        (
            "awaiting_signature",
            "Il contratto quadro per prestazioni occasionali è già in firma: la lettera di "
            "incarico occasionale parte da sola dopo la sua firma.",
        ),
        (
            "active",
            "Il contratto quadro per prestazioni occasionali è già attivo: parte subito la "
            "lettera di incarico occasionale.",
        ),
    ],
)
def test_an_occasional_check_names_the_occasional_documents(step: str, sentence: str) -> None:
    _, what_happens = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(),
        step,  # type: ignore[arg-type]
        missing_tax_data=False,
        regime="occasional",
    )
    assert what_happens == sentence


def test_an_occasional_letter_for_someone_with_to_vat_number_carries_the_warning() -> None:
    """Allowed, since occasional work outside one's professional activity exists, and
    said; the ordinary letter never carries the warning."""
    recap, _ = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(),
        "active",
        missing_tax_data=False,
        regime="occasional",
        has_vat_number=True,
    )
    assert (
        recap[-1]
        == HAS_VAT_NUMBER
        == (
            "Ha la partita IVA: la prestazione occasionale vale solo per un lavoro fuori dalla "
            "sua attività professionale."
        )
    )
    ordinary, _ = check_sentences(
        "Ada Lovelace",
        "ACME S.r.l.",
        _letter(),
        "active",
        missing_tax_data=False,
        has_vat_number=True,
    )
    assert HAS_VAT_NUMBER not in ordinary
    assert ordinary[-1] == (
        "Compenso: 450,00 € a giornata, IVA esclusa, pagato a 30 giorni fine mese."
    )


def test_the_refusal_of_an_ordinary_letter_without_to_vat_number_is_the_specs_sentence() -> None:
    assert ORDINARY_NEEDS_VAT_NUMBER == (
        'Senza partita IVA si fa solo la lettera occasionale: scegli "Prestazione occasionale" '
        "o salva la partita IVA."
    )


# ---- what «Invia per la firma» did ------------------------------------------------------


def _report(sent: str | None, mail_sent: bool | None) -> SendReport:
    """Only the letter's number is read: the rest of the match does not matter here."""
    letter = ContractDocumentRead.model_construct(number="2026-001")
    match = MatchRead.model_construct(letter=letter)
    return SendReport.model_construct(match=match, sent=sent, mail_sent=mail_sent)


@pytest.mark.parametrize(
    ("sent", "mail_sent", "sentence"),
    [
        (
            "framework",
            True,
            "Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua "
            "firma.",
        ),
        ("letter", True, "Partita la lettera di incarico n. 2026-001."),
        (
            None,
            None,
            "La lettera n. 2026-001 aspetta il contratto quadro già in firma e partirà da sola "
            "dopo.",
        ),
        (
            "letter",
            False,
            "Partita la lettera di incarico n. 2026-001. La mail però non è partita: usa "
            "«Reinvia email».",
        ),
    ],
)
def test_the_send_report_says_which_document_left_and_whether_its_mail_did(
    sent: str | None, mail_sent: bool | None, sentence: str
) -> None:
    """The web's `sendReportMessage` word for word (REB-390): the pages and the MCP tool
    answer the same sentence after «Invia per la firma»."""
    assert send_report_sentence(_report(sent, mail_sent)) == sentence
