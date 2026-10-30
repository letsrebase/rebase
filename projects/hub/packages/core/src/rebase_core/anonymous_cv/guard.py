"""What keeps the anonymous CV anonymous (spec § Writing it, REB-862).

`identifying_fields` reads an `AnonymousCv` against `Who`: everything the CV and the account
say could name the person. It answers the **paths** of the offending strings, never the word
that matched, so a log line built from the answer never repeats an identifier. The readers are
the card's (`card_guard`), imported and not copied: a fix there is a fix here.

Four readings, from wide to narrow, because a CV is mostly names of other things.

In every string: an address (`https://`, `www.`, `@`); a profile or portfolio host written
bare (`linkedin.com/in/...`, `github.com/...`) from a fixed list, whoever's it is, since the
model writes links without a scheme; the person's whole email; their phone by its last digits.
There is no generic «domain with a path» rule on purpose: `Node.js/Express`, `ASP.NET` and
`Vue.js` are skills. And a tax identifier, which names one person as surely as a name: a
codice fiscale or an Italian IBAN by its shape, any IBAN after the word, eleven digits after
a label (`P.IVA`, `VAT`, `C.F.`, ...) and the person's own partita IVA or codice fiscale
however it is spaced. Eleven digits alone are not refused: an order number has them too.

In every string but a place (`luogo`) or a language (`lingue`): the identifiers that are
names, read as whole phrases of tokens: the handle of an email, the GitHub logins, the handle
in the path of each of the person's links (`linkedin.com/in/<slug>`), the whole host of each
and the labels of a personal one (not an employer's the CV names itself); and the surname,
written with a capital, whole and word by word (particles such as «de» aside), on the raw
text, so a skill called «Mario Messina» is still a leak; and the first name and surname run
together as one word (`mariorossi`, `rossimario`, digits aside), since a handle is written so
with no link around it. A place or a language is the CV's own
(Messina, Russo, Tedesco).

In the prose (headline, summary, roles, bullets, other sections): the first name, never
inside the CV's own skills, since Ada, Ruby and Julia are languages too. In a link-like
token (below) it is read in every string but a place, skills blanked; outside the prose only
in a token with a `/` or a dot, so «Max-Planck-Institut» is not Max.

In a link-like token (one with a `/`, `-`, `_` or a dot between two word characters): the
name as a slug writes it. The token is cut into parts on anything but letters and digits,
accents folded; a part that is (digits aside) a name word, the whole surname, first name and
surname joined either way or the initial and the surname is the person (`alex-fegatilli-fau50`,
`deluca.dev`, `mmessina`). Whole parts only, so `MySQL/MariaDB` is not Maria.
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from rebase_core.anonymous_cv.schema import AnonymousCv, strings
from rebase_core.card_draft import phone_digits
from rebase_core.card_guard import (
    _ADDRESS,
    PHONE_KEY_DIGITS,
    STOPLIST,
    _contains,
    _has_phone,
    _hostname_phrases,
    _names,
    _phrase,
    _tokens,
    _without_skills,
)
from rebase_core.sources import github_login_of, link_hostnames

# Where the first name is read: the prose of the CV, as paths from `strings`.
_PROSE = re.compile(
    r"^(titolo|sommario)$"
    r"|^esperienze\[\d+\]\.ruolo$"
    r"|^esperienze\[\d+\]\.punti\[\d+\]\.(titolo|testo)$"
    r"|^altre_sezioni\[\d+\]\.voci\[\d+\]$"
)
# Where a name is not read: a place or a language is the CV's own.
_PLACE_OR_LANGUAGE = re.compile(r"\.luogo$|^lingue\[")
_SKILL_PATH = re.compile(r"^competenze\[\d+\]\.voci\[\d+\]$")
# The small words of a compound surname (De Luca, van der Berg, D'Amico): not a name alone.
_PARTICLES = frozenset(
    ["de", "di", "da", "del", "della", "delle", "dei", "degli", "van", "von", "der", "den"]
    + ["la", "le", "lo", "dos", "das", "du", "d", "dal", "dalla", "dalle", "dallo", "dall"]
    + ["dell", "dello", "dagli", "dai"]
)
# Profile and portfolio sites: a mention of one is a personal link, whoever it belongs to.
# Matched as a host (a word boundary before, no word character after), so «GitHub» or
# «LinkedIn» without a TLD are only words.
_PROFILE_HOSTS = (
    "linkedin.com", "lnkd.in", "github.com", "github.io", "gitlab.com", "gitlab.io",
    "bitbucket.org", "stackoverflow.com", "medium.com", "dev.to", "behance.net",
    "dribbble.com", "about.me", "linktr.ee", "x.com", "twitter.com", "instagram.com",
    "facebook.com", "youtube.com", "kaggle.com", "huggingface.co", "notion.site",
    "vercel.app", "netlify.app", "t.me", "wa.me", "substack.com", "wordpress.com",
    "blogspot.com", "wixsite.com", "codepen.io", "leetcode.com", "hackerrank.com",
    "orcid.org", "researchgate.net", "sourceforge.net", "hub.docker.com", "webflow.io",
    "framer.website", "credly.com", "upwork.com", "fiverr.com", "toptal.com", "malt.it",
    "malt.fr",
)  # fmt: skip
_PROFILE_HOST = re.compile(
    r"\b(?:" + "|".join(re.escape(host) for host in _PROFILE_HOSTS) + r")(?!\w)",
    re.IGNORECASE,
)
# A slug is joined by `-` or `_` as often as it is dotted or pathed (`alex-fegatilli-fau50`).
_LINK_LIKE = re.compile(r"[/_\-]|\w\.\w")
_STRICT_LINK = re.compile(r"/|\w\.\w")

# A codice fiscale's shape: six letters, the year, the month's letter, the day, the place.
_CODICE_FISCALE = re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPRST]\d{2}[A-Z]\d{3}[A-Z]\b", re.IGNORECASE)
# An Italian IBAN, compact or in the bank's groups of four: the candidate is read with its
# spaces, then checked compact, so «IT20 anni di esperienza» is not one.
_IBAN_IT_SPACED = re.compile(r"\bIT\d{2}(?:\s?[0-9A-Z]){23}\b", re.IGNORECASE)
_IBAN_IT = re.compile(r"IT\d{2}[A-Z]\d{10}[0-9A-Z]{12}", re.IGNORECASE)
# Any country's, only after the word: «DE89 3704 0044» alone may be a code.
_IBAN_AFTER_LABEL = re.compile(
    r"\bIBAN\b\W{0,4}[A-Z]{2}\d{2}(?:\s?[0-9A-Z]{4}){3,7}\b", re.IGNORECASE
)
# Eleven digits (spaced, dotted or dashed) a few characters after a tax label: a partita IVA,
# or a company's codice fiscale. «Partita IVA attiva dal 2019» has no such number.
_TAX_LABEL = r"(?:p\.?\s?iva|partita\s+iva|vat|iva|c\.\s?f\.?|cf|codice\s+fiscale)"
_LABELLED_TAX_NUMBER = re.compile(
    rf"(?<!\w){_TAX_LABEL}(?!\w)[^\d\n]{{0,12}}?\d(?:[ .\-]?\d){{10}}(?![ .\-]?\d)",
    re.IGNORECASE,
)


# Labels in front of a platform's domain that are the site's own (`scholar.`, `sites.`), not
# a person's subdomain (`mariorossi.medium.com`).
_SITE_LABELS = frozenset(["www", "scholar", "sites", "m", "en", "it", "docs", "hub"])
# Where a profile site puts the user in the first segment of the path.
_FIRST_SEGMENT = (
    "github.com", "gitlab.com", "x.com", "twitter.com", "instagram.com", "facebook.com",
    "dev.to", "behance.net", "dribbble.com", "codepen.io", "medium.com", "bitbucket.org",
    "kaggle.com", "huggingface.co",
)  # fmt: skip
# Where it sits after a fixed word: (host, the segment before the user, how many to skip).
_AFTER = {
    "linkedin.com": ("in", 0),
    "stackoverflow.com": ("users", 1),
    "leetcode.com": ("u", 0),
    "hackerrank.com": ("profile", 0),
    "researchgate.net": ("profile", 0),
    "sourceforge.net": ("u", 0),
}
# Segments of a path that are the site's structure, not anyone.
_GENERIC_SEGMENTS = frozenset(
    ["users", "user", "u", "profile", "profiles", "channel", "c", "about", "work", "view"]
    + ["badges", "badge", "in", "pub", "citations", "portfolio", "projects", "blog", "posts"]
    + ["home", "me", "cv", "resume", "en", "it", "public_url", "edit", "people", "orgs"]
    + ["questions", "tags", "search", "explore", "watch", "playlist", "spaces", "models"]
    + ["datasets", "pages", "topics"]
)


def _unique(*groups: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for group in groups for item in group))


def _fold(value: str) -> str:
    """`value` lower case, accents dropped (Cantù is cantu)."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _compact(value: str) -> str:
    """Letters and digits only, folded: «D'Amico» is `damico`."""
    return re.sub(r"[\W_]+", "", _fold(value))


def _words(name: str) -> list[str]:
    """The words of a name of 3+ letters that are not particles, as written."""
    return [
        word
        for word in re.split(r"[\s\-'\u2019]+", name)
        if len(_compact(word)) >= 3 and _fold(word) not in _PARTICLES
    ]


@dataclass(frozen=True)
class Who:
    """What could name this person: the names as the account and the CV spell them, the
    emails (lower case) and phones (digits only) of both, the GitHub logins, the hostnames
    of the links and the handles in their paths, and the tax identifiers the hub keeps for
    them (upper case, letters and digits only)."""

    nomi: list[str]
    cognomi: list[str]
    emails: list[str]
    phones: list[str]
    github_logins: list[str]
    hostnames: list[str]
    handles: list[str]
    tax_ids: list[str]

    def merged(self, other: "Who") -> "Who":
        """Both, each identifier once: the account's `Who` and the one the CV gave."""
        return Who(
            nomi=_unique(self.nomi, other.nomi),
            cognomi=_unique(self.cognomi, other.cognomi),
            emails=_unique(self.emails, other.emails),
            phones=_unique(self.phones, other.phones),
            github_logins=_unique(self.github_logins, other.github_logins),
            hostnames=_unique(self.hostnames, other.hostnames),
            handles=_unique(self.handles, other.handles),
            tax_ids=_unique(self.tax_ids, other.tax_ids),
        )


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _path_handles(link: str) -> list[str]:
    """The handle a link's path holds, only where its shape says it is a person: the user
    of `github.com/<user>` or `linkedin.com/in/<slug>` on a profile site, and on any other
    host the last segment when it is not a number or a word every site has (`/about`,
    `/work`, `/view`): «Remote work» must not be refused for a page called `/work`."""
    url = link if "://" in link else f"https://{link}"
    try:
        parsed = urlparse(url)
    except ValueError:
        return []
    host = (parsed.hostname or "").removeprefix("www.")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return []
    for domain in _FIRST_SEGMENT:
        if _is_host(host, domain):
            first = segments[0].lstrip("@")
            if first.lower() == "people":  # facebook.com/people/<name>/<id>
                return segments[1:2]
            return [] if first.lower() in _GENERIC_SEGMENTS else [first]
    if _is_host(host, "youtube.com"):
        first = segments[0]
        if first.startswith("@"):
            return [first[1:]]
        if first.lower() in ("c", "user"):
            return segments[1:2]
        return [] if first.lower() in _GENERIC_SEGMENTS or first.lower() == "channel" else [first]
    for domain, (before, skip) in _AFTER.items():
        if _is_host(host, domain):
            for i, segment in enumerate(segments[:-1]):
                if segment.lower() == before and i + 1 + skip < len(segments):
                    return [segments[i + 1 + skip]]
            # No marker (`leetcode.com/mariorossi`): the first segment, if it is a name.
            first = segments[0]
            ok = not first.isdigit() and first.lower() not in _GENERIC_SEGMENTS
            return [first] if ok else []
    last = segments[-1]
    if last.isdigit() or last.lower() in STOPLIST or last.lower() in _GENERIC_SEGMENTS:
        return []
    return [last]


def who_of(
    *,
    nome: str | None,
    cognome: str | None,
    emails: Iterable[str | None],
    phones: Iterable[str | None],
    links: Iterable[str | None],
    tax_ids: Iterable[str | None] = (),
) -> Who:
    """A `Who` from raw values: blanks dropped, emails lower-cased, phones as digits, tax
    identifiers upper case with their spaces and dots dropped."""
    urls = [link.strip() for link in links if link and link.strip()]
    return Who(
        nomi=[nome.strip()] if nome and nome.strip() else [],
        cognomi=[cognome.strip()] if cognome and cognome.strip() else [],
        emails=_unique(email.strip().lower() for email in emails if email and email.strip()),
        phones=_unique(digits for phone in phones if (digits := phone_digits(phone))),
        github_logins=_unique(
            login for url in urls if (login := github_login_of([url])) is not None
        ),
        hostnames=link_hostnames(urls),
        handles=_unique(*(_path_handles(url) for url in urls)),
        tax_ids=_unique(compact for value in tax_ids if value and (compact := _tax_compact(value))),
    )


def _tax_compact(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).upper()


def _has_tax_id(value: str, known: list[str]) -> bool:
    """Whether `value` holds a tax identifier: one by its shape or its label, or one of the
    person's own (`known`, compact), however it is spaced, dotted or dashed. A known number
    is read between non-digits («IT01234567890» is the partita IVA), a known code between
    non-alphanumerics."""
    if (
        _CODICE_FISCALE.search(value)
        or _IBAN_AFTER_LABEL.search(value)
        or _LABELLED_TAX_NUMBER.search(value)
        or any(
            _IBAN_IT.fullmatch(re.sub(r"\s", "", m.group()))
            for m in _IBAN_IT_SPACED.finditer(value)
        )
    ):
        return True
    for tax_id in known:
        edge = r"\d" if tax_id.isdigit() else r"[0-9A-Za-z]"
        spaced = r"[ .\-]?".join(re.escape(character) for character in tax_id)
        if re.search(rf"(?<!{edge}){spaced}(?!{edge})", value, re.IGNORECASE):
            return True
    return False


def _bare(handle: str) -> str:
    """`handle` without its trailing number («afegatilli-99»), when 6 letters or more are
    left: «fau50» would otherwise search «fau», and «java8» «java»."""
    stripped = re.sub(r"[\W_]*\d+$", "", handle)
    return stripped if len(re.sub(r"[\W\d_]", "", stripped)) >= 6 else ""


def _in_a_link(value: str, candidates: list[str], *, strict: bool = False) -> bool:
    """Whether a link-like token of `value` has a part that is one of `candidates`
    (compact, lower case), digits aside. `strict` leaves out a bare hyphen: «Max-Planck»."""
    like = _STRICT_LINK if strict else _LINK_LIKE
    for token in value.split():
        if not like.search(token):
            continue
        for part in re.split(r"[\W_]+", _fold(token)):
            if part and any(re.fullmatch(rf"{candidate}\d*", part) for candidate in candidates):
                return True
    return False


def _is_run(value: str, runs: list[str]) -> bool:
    """Whether `value` has a word that is one of `runs` (digits aside)."""
    parts = re.split(r"[\W_]+", _fold(value))
    return any(re.fullmatch(rf"{run}\d*", part) for part in parts for run in runs)


def _host_phrases(host: str) -> list[tuple[str, ...]]:
    """What a link's host is searched for: a personal domain whole and by its labels. A
    platform (`youtube.com`, `dev.to`, `scholar.google.it`) names no one, and is caught as
    a host anyway; what comes before it (`mariorossi.medium.com`) is the person."""
    if "google" in host.split(".")[:-1]:
        return []
    for domain in _PROFILE_HOSTS:
        if _is_host(host, domain):
            labels = host.removesuffix(domain).strip(".").split(".")
            return [
                phrase
                for label in labels
                if label and label not in _SITE_LABELS and (phrase := _phrase(label)) is not None
            ]
    return _hostname_phrases(host)


def identifying_fields(cv: AnonymousCv, who: Who) -> list[str]:
    """The paths of the strings of `cv` that identify the person, in document order, each
    once; empty when the CV is anonymous."""
    every = strings(cv)
    surnames = _unique(name.strip() for name in who.cognomi if name.strip())
    # The whole surname and, for a compound one, each of its words (a particle aside).
    surname_terms = _unique(surnames, *(_words(name) for name in surnames))
    firsts = _unique(name.strip() for name in who.nomi if name.strip())
    given = _unique(firsts, *(_words(name) for name in firsts))
    emails = [email for email in who.emails if email]
    phones = [digits[-PHONE_KEY_DIGITS:] for digits in who.phones if len(digits) >= 7]
    # A partita IVA has 11 digits and a codice fiscale 16 characters (11 for a company).
    tax_ids = [tax_id for tax_id in who.tax_ids if len(tax_id) >= 11]

    # An employer the CV names itself is not the person's site, even when the person's
    # site is called like it: the company's own page is theirs.
    employers = {
        phrase
        for path, value in every
        if path.endswith(".azienda") and (phrase := _phrase(value, legal=True)) is not None
    }
    host_phrases = [
        phrase
        for host in who.hostnames
        for phrase in _host_phrases(host.removeprefix("www."))
        if phrase not in employers
    ]
    # Identifiers that are names: read as phrases, wherever a name may be read.
    phrases = [
        phrase
        for phrase in (
            *(_phrase(email.partition("@")[0]) for email in emails),
            *(_phrase(login) for login in who.github_logins),
            *(_phrase(handle) for handle in who.handles),
            *(_phrase(_bare(handle)) for handle in who.handles if _bare(handle)),
            *host_phrases,
        )
        if phrase is not None
    ]

    # A slug writes a name as one run: «mariomessina», «messinamario», «mmessina».
    surname_runs = [run for name in surnames if len(run := _compact(name)) >= 3]
    link_names = _unique(
        surname_runs,
        (_compact(word) for word in surname_terms),
        (_compact(first) + run for first in firsts for run in surname_runs),
        (run + _compact(first) for first in firsts for run in surname_runs),
        (_compact(first)[:1] + run for first in firsts if _compact(first) for run in surname_runs),
    )
    link_names = [name for name in link_names if len(name) >= 3]
    link_firsts = [name for word in given if len(name := _compact(word)) >= 3]
    # The name as one run («mariorossi», «rossimario»), a whole word of its own.
    joined = [
        run
        for first in firsts
        for last in surname_runs
        for run in (_compact(first) + last, last + _compact(first))
    ]

    # A skill that holds the person's surname is not a technology to blank out.
    folded_surnames = [_fold(term) for term in surname_terms]
    skills = [
        value
        for path, value in every
        if _SKILL_PATH.match(path)
        and not any(
            re.search(rf"(?<!\w){re.escape(n)}(?!\w)", _fold(value)) for n in folded_surnames
        )
    ]

    hit: list[str] = []
    for path, value in every:
        place = _PLACE_OR_LANGUAGE.search(path) is not None
        prose = _PROSE.match(path) is not None
        plain = _without_skills(value, skills)
        if (
            _ADDRESS.search(value)
            or _PROFILE_HOST.search(value)
            or any(email in value.lower() for email in emails)
            or (bool(phones) and _has_phone(value, phones))
            or _has_tax_id(value, tax_ids)
            or (
                not place
                and (
                    any(_contains(_tokens(value), phrase) for phrase in phrases)
                    or any(_names(value, surname) for surname in surname_terms)
                    or _in_a_link(value, link_names)
                    or _is_run(value, joined)
                )
            )
            or (prose and any(_names(plain, g) for g in given))
            or (not place and _in_a_link(plain, link_firsts, strict=not prose))
        ):
            hit.append(path)
    return hit
