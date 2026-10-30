"""What keeps the anonymous CV anonymous (spec § Writing it, REB-862).

`identifying_fields` reads an `AnonymousCv` against `Who`: everything the CV and the account
say could name the person. It answers the **paths** of the offending strings, never the word
that matched, so a log line built from the answer never repeats an identifier. The readers are
the card's (`card_guard`), imported and not copied: a fix there is a fix here.

Three readings, from wide to narrow, because a CV is mostly names of other things.

In every string: an address (`https://`, `www.`, `@`); a profile or portfolio host written
bare (`linkedin.com/in/...`, `github.com/...`) from a fixed list, whoever's it is, since the
model writes links without a scheme; the person's name inside a link-like token
(`alex-fegatilli-fau50`, `fegatilli.dev`: a token with a `/`, `-`, `_` or a dot between two
word characters), case-insensitively because a slug is lower case; the person's email, the
handle of it and of the GitHub account, their own site and their phone. There is no generic
«domain with a path» rule on purpose: `Node.js/Express`, `ASP.NET` and `Vue.js` are skills.

The surname, written with a capital, is read everywhere but in a place (`luogo`) or a
language (Messina, Russo, Tedesco). The first name is read only where prose is: the headline,
the summary, the roles, the bullets and the other sections, since Ada, Ruby and Julia are
languages too, and never inside the CV's own skills.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from rebase_core.anonymous_cv.schema import AnonymousCv, strings
from rebase_core.card_draft import phone_digits
from rebase_core.card_guard import (
    _ADDRESS,
    PHONE_KEY_DIGITS,
    _contains,
    _has_phone,
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
# Where the surname is not read: a place or a language is the CV's own.
_PLACE_OR_LANGUAGE = re.compile(r"\.luogo$|^lingue\[")
# Profile and portfolio sites: a mention of one is a personal link, whoever it belongs to.
# Matched as a host (a word boundary before, no word character after), so «GitHub» or
# «LinkedIn» without a TLD are only words.
_PROFILE_HOST = re.compile(
    r"\b(?:"
    + "|".join(
        re.escape(host)
        for host in (
            "linkedin.com",
            "lnkd.in",
            "github.com",
            "github.io",
            "gitlab.com",
            "gitlab.io",
            "bitbucket.org",
            "stackoverflow.com",
            "medium.com",
            "dev.to",
            "behance.net",
            "dribbble.com",
            "about.me",
            "linktr.ee",
            "x.com",
            "twitter.com",
            "instagram.com",
            "facebook.com",
            "youtube.com",
            "kaggle.com",
            "huggingface.co",
            "notion.site",
            "vercel.app",
            "netlify.app",
        )
    )
    + r")(?!\w)",
    re.IGNORECASE,
)
# A slug is joined by `-` or `_` as often as it is dotted or pathed (`alex-fegatilli-fau50`).
_LINK_LIKE = re.compile(r"[/_\-]|\w\.\w")
_SKILL_PATH = re.compile(r"^competenze\[\d+\]\.voci\[\d+\]$")


def _unique(*groups: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for group in groups for item in group))


@dataclass(frozen=True)
class Who:
    """What could name this person: the names as the account and the CV spell them, the
    emails (lower case) and phones (digits only) of both, the GitHub login and the
    hostnames of the links."""

    nomi: list[str]
    cognomi: list[str]
    emails: list[str]
    phones: list[str]
    github_login: str | None
    hostnames: list[str]

    def merged(self, other: "Who") -> "Who":
        """Both, each identifier once: the account's `Who` and the one the CV gave."""
        return Who(
            nomi=_unique(self.nomi, other.nomi),
            cognomi=_unique(self.cognomi, other.cognomi),
            emails=_unique(self.emails, other.emails),
            phones=_unique(self.phones, other.phones),
            github_login=self.github_login or other.github_login,
            hostnames=_unique(self.hostnames, other.hostnames),
        )


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
        github_login=github_login_of(urls),
        hostnames=link_hostnames(urls),
    )


def identifying_fields(cv: AnonymousCv, who: Who) -> list[str]:
    """The paths of the strings of `cv` that identify the person, in document order, each
    once; empty when the CV is anonymous."""
    surnames = {name.strip() for name in who.cognomi if name.strip()}
    given: set[str] = set()
    for first in (name.strip() for name in who.nomi):
        given |= {first, *(word for word in re.split(r"[\s\-]+", first) if len(word) >= 3)}
    given.discard("")
    emails = [email for email in who.emails if email]
    phones = [digits[-PHONE_KEY_DIGITS:] for digits in who.phones if len(digits) >= 7]
    # An email's handle, the GitHub login and the whole host of each link; a host's labels
    # are not read, so an employer called like the person's site still passes.
    phrases = [
        phrase
        for phrase in (
            *(_phrase(email.partition("@")[0]) for email in emails),
            _phrase(who.github_login) if who.github_login else None,
            *(_phrase(host.removeprefix("www.")) for host in who.hostnames),
        )
        if phrase is not None
    ]

    # The person's names, lower case, for the link-like tokens (under three characters
    # are too weak to search inside a slug).
    slug_names = [name.lower() for name in {*surnames, *given} if len(name) >= 3]

    def in_a_link(value: str) -> bool:
        return any(
            _LINK_LIKE.search(token) and any(name in token.lower() for name in slug_names)
            for token in value.split()
        )

    every = strings(cv)
    skills = [value for path, value in every if _SKILL_PATH.match(path)]

    hit: list[str] = []
    for path, value in every:
        plain = _without_skills(value, skills)
        if (
            _ADDRESS.search(value)
            or _PROFILE_HOST.search(value)
            or in_a_link(value)
            or any(email in value.lower() for email in emails)
            or (bool(phones) and _has_phone(value, phones))
            or any(_contains(_tokens(value), phrase) for phrase in phrases)
            or (
                not _PLACE_OR_LANGUAGE.search(path)
                and any(_names(plain, surname) for surname in surnames)
            )
            or (_PROSE.match(path) is not None and any(_names(plain, g) for g in given))
        ):
            hit.append(path)
    return hit
