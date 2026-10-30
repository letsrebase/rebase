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
`Vue.js` are skills.

In every string but a place (`luogo`) or a language (`lingue`): the identifiers that are
names, read as whole phrases of tokens: the handle of an email, the GitHub logins, the handle
in the path of each of the person's links (`linkedin.com/in/<slug>`), the whole host of each
and the labels of a personal one (not an employer's the CV names itself); and the surname,
written with a capital, whole and word by word (particles such as «de» aside), on the raw
text, so a skill called «Mario Messina» is still a leak. A place or a language is the CV's own
(Messina, Russo, Tedesco).

In the prose (headline, summary, roles, bullets, other sections): the first name, never
inside the CV's own skills, since Ada, Ruby and Julia are languages too.

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
    + ["la", "le", "lo", "dos", "das", "du", "d"]
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
    "orcid.org", "researchgate.net", "sourceforge.net",
)  # fmt: skip
_PROFILE_HOST = re.compile(
    r"\b(?:" + "|".join(re.escape(host) for host in _PROFILE_HOSTS) + r")(?!\w)",
    re.IGNORECASE,
)
# A slug is joined by `-` or `_` as often as it is dotted or pathed (`alex-fegatilli-fau50`).
_LINK_LIKE = re.compile(r"[/_\-]|\w\.\w")


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
        for word in re.split(r"[\s\-']+", name)
        if len(_compact(word)) >= 3 and _fold(word) not in _PARTICLES
    ]


@dataclass(frozen=True)
class Who:
    """What could name this person: the names as the account and the CV spell them, the
    emails (lower case) and phones (digits only) of both, the GitHub logins, the hostnames
    of the links and the handles in their paths."""

    nomi: list[str]
    cognomi: list[str]
    emails: list[str]
    phones: list[str]
    github_logins: list[str]
    hostnames: list[str]
    handles: list[str]

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
        )


def _path_handles(link: str) -> list[str]:
    """The first and the last segment of a link's path: the user of `github.com/<user>`,
    the slug of `linkedin.com/in/<slug>`, a personal site's page."""
    url = link if "://" in link else f"https://{link}"
    try:
        parsed = urlparse(url)
    except ValueError:
        return []
    segments = [segment for segment in parsed.path.split("/") if segment]
    return _unique(segments[:1], segments[-1:])


def who_of(
    *,
    nome: str | None,
    cognome: str | None,
    emails: Iterable[str | None],
    phones: Iterable[str | None],
    links: Iterable[str | None],
) -> Who:
    """A `Who` from raw values: blanks dropped, emails lower-cased, phones as digits."""
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
    )


def _in_a_link(value: str, candidates: list[str]) -> bool:
    """Whether a link-like token of `value` has a part that is one of `candidates`
    (compact, lower case), digits aside."""
    for token in value.split():
        if not _LINK_LIKE.search(token):
            continue
        for part in re.split(r"[\W_]+", _fold(token)):
            if part and any(re.fullmatch(rf"{candidate}\d*", part) for candidate in candidates):
                return True
    return False


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
        for phrase in _hostname_phrases(host.removeprefix("www."))
        if phrase not in employers
    ]
    # Identifiers that are names: read as phrases, wherever a name may be read.
    phrases = [
        phrase
        for phrase in (
            *(_phrase(email.partition("@")[0]) for email in emails),
            *(_phrase(login) for login in who.github_logins),
            *(_phrase(handle) for handle in who.handles),
            *(_phrase(re.sub(r"[\W_]*\d+$", "", handle)) for handle in who.handles),
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

    # A skill that holds the person's surname is not a technology to blank out.
    folded_surnames = [_fold(term) for term in surname_terms]
    skills = [
        value
        for path, value in every
        if _SKILL_PATH.match(path) and not any(name in _fold(value) for name in folded_surnames)
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
            or (
                not place
                and (
                    any(_contains(_tokens(value), phrase) for phrase in phrases)
                    or any(_names(value, surname) for surname in surname_terms)
                    or _in_a_link(value, link_names)
                )
            )
            or (prose and (any(_names(plain, g) for g in given) or _in_a_link(plain, link_firsts)))
        ):
            hit.append(path)
    return hit
