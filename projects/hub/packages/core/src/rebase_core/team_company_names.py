"""Whether a proposal's summary names the company that asked (REB-512, spec § 3.5;
REB-892 split it out of `team_requests.py`).

The summary of a proposal is what the talents read in the availability mail, and a talent
must not learn which company is asking before rebase decides to tell them. So a summary
that names the company is logged when the request is filed, refused when an admin edits
it (`TeamRequestService.set_summary`), and refused again before the mail goes out
(`TeamAvailabilityService.prepare_contact`). `names_the_company` is the check.
"""

import re

NAMES_THE_COMPANY = "Il riassunto nomina l'azienda: correggilo prima di scrivere ai talenti."
# A word of the company's name, as `names_the_company` reads one: letters and digits.
_WORD = re.compile(r"[^\W_]+")
# Shorter words are articles and initials («di», «Ars», «3M»), too common to be the name.
_NAME_WORD_MIN_LENGTH = 4
# The legal forms, with or without their dots: «S.r.l.» names the kind of company, not
# which one, and «srls» is the one of four letters.
_LEGAL_FORMS = frozenset({"srl", "srls", "spa", "snc", "sas"})
# The words a company's name shares with the kind of company it is, which the prompt
# itself asks the summary to name instead («un'azienda di logistica»): «Logistica
# Veneta S.r.l.» is not named by it, and «Studio Legale Bianchi» not by «uno studio
# legale». Lower case, matched whole; only the other words of a name are distinctive.
_GENERIC_WORDS = frozenset(
    {
        "agency",
        "agenzia",
        "associati",
        "company",
        "consulenza",
        "consulting",
        "cooperativa",
        "design",
        "digital",
        "digitale",
        "engineering",
        "group",
        "gruppo",
        "holding",
        "impresa",
        "informatica",
        "ingegneria",
        "international",
        "italia",
        "italiana",
        "italy",
        "labs",
        "legale",
        "logistica",
        "marketing",
        "media",
        "partner",
        "partners",
        "servizi",
        "services",
        "sistemi",
        "società",
        "software",
        "solutions",
        "soluzioni",
        "studio",
        "systems",
        "tech",
        "technologies",
        "technology",
        "tecnologie",
        "trasporti",
    }
)


def names_the_company(summary: str, company: str) -> bool:
    """First, `company` as one phrase (legal-form tokens dropped, the rest rejoined)
    matched inside the summary case-insensitively, as a whole word on both ends,
    whatever its length, when one of its tokens is written all in capitals: an
    initialism such as «HP», «3M» or «IBM» is too short for the word rule below and
    would slip through it, and a company spelling its own name in capitals means
    exactly that spelling. «HP» is named by «HP lancia un progetto» and not by «un
    nuovo chip» (a word boundary, not shared letters), and «IBM Italia S.r.l.» is named
    by «IBM Italia lancia un progetto», its legal form being the kind of company, not
    which one, and rarely repeated in a summary. A name typed in ordinary case, such as
    «Di Più», is left to the word rule below, so everyday Italian is not mistaken for it.

    Below that, any word of four letters or more of `company` that is neither a legal
    form nor a generic word of a company's kind, case-insensitively, inside the summary
    as a whole word: «Acme S.r.l.» is named by «ACME rifà il gestionale», «Data Srl» is
    not by «un database», «Acme Srls» is not by «una srls di Torino», and «Logistica
    Veneta S.r.l.» is not by «un'azienda di logistica»."""
    kept = [
        token
        for token in company.split()
        if token.replace(".", "").strip(",;:").casefold() not in _LEGAL_FORMS
    ]
    if any(token.replace(".", "").strip(",;:").isupper() for token in kept):
        phrase = " ".join(kept)
        if phrase and re.search(
            rf"(?<![^\W_]){re.escape(phrase)}(?![^\W_])", summary, re.IGNORECASE
        ):
            return True
    for token in company.split():
        if token.replace(".", "").strip(",;:").casefold() in _LEGAL_FORMS:
            continue
        for word in _WORD.findall(token):
            if len(word) < _NAME_WORD_MIN_LENGTH:
                continue
            if word.casefold() in _LEGAL_FORMS or word.casefold() in _GENERIC_WORDS:
                continue
            if re.search(rf"(?<![^\W_]){re.escape(word)}(?![^\W_])", summary, re.IGNORECASE):
                return True
    return False
