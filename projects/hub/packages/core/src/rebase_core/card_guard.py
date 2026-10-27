"""What keeps a card anonymous, before it reaches a page (spec § 2.1, REB-838).

`identifying` reads a `Card` against everything the writer knows could name the person:
the surname and first name the CV carries, the GitHub login, the repositories they own,
the hostnames of their links and the employers their CV names. It answers the **names of
the offending fields**, never the word that matched, so a log line built from the answer
never repeats an identifier. A card that hits is asked for one rewrite, then fails
(`cards.py`).

The two readings differ on purpose. The person's own name is read the way it always was
(`_names`): a whole word written with a capital, because many surnames are words too
(Conti, Grande, Porta). Every other identifier is read case-insensitively, whole token,
punctuation-insensitive: `my-lib` is also `my_lib`, `My Lib` and `MyLib`. An identifier of
under three characters, or made only of ordinary English or Italian words (`STOPLIST`),
is ignored, so a repository called `app` or `api` does not fail every card.
"""

import re
from dataclasses import dataclass, field

from rebase_core.team_schemas import Card

# A link or an address in a card (spec § 2.1: no link). A scheme, not the bare word
# `http`, which is also a skill («HTTP/2», «REST/HTTP») a backend developer's card lists.
_ADDRESS = re.compile(r"https?://|www\.|@", re.IGNORECASE)

# Words a repository, a login or an employer may be called without being anyone's: a card
# that said «web» or «sito» would otherwise be refused for a repository named `web`. Only
# a whole identifier made of these is ignored; `web-shop` is still read.
STOPLIST = frozenset(
    [
        "app",
        "apps",
        "api",
        "web",
        "site",
        "sito",
        "blog",
        "docs",
        "test",
        "tests",
        "demo",
        "dev",
        "code",
        "config",
        "dotfiles",
        "backend",
        "frontend",
        "server",
        "client",
        "project",
        "projects",
        "progetto",
        "progetti",
        "example",
        "examples",
        "sample",
        "starter",
        "template",
        "playground",
        "sandbox",
        "scripts",
        "script",
        "cli",
        "bot",
        "data",
        "database",
        "db",
        "ui",
        "ux",
        "mobile",
        "android",
        "ios",
        "cloud",
        "infra",
        "platform",
        "portfolio",
        "personal",
        "private",
        "public",
        "misc",
        "the",
        "and",
        "for",
        "with",
        "online",
        "group",
        "company",
        "software",
        "tech",
        "technology",
        "solutions",
        "services",
        "service",
        "il",
        "lo",
        "la",
        "le",
        "gli",
        "un",
        "una",
        "per",
        "con",
        "di",
        "da",
        "del",
        "della",
        "dei",
        "delle",
        "prova",
        "codice",
        # What a repository or a page is often called, and the sites a person's page sits
        # on (`mario.github.io`: the platform is not the person).
        "website",
        "webapp",
        "homepage",
        "home",
        "main",
        "todo",
        "shop",
        "store",
        "notes",
        "note",
        "chat",
        "tool",
        "tools",
        "lib",
        "libs",
        "util",
        "utils",
        "core",
        "learning",
        "tutorial",
        "tutorials",
        "course",
        "resume",
        "page",
        "pages",
        "github",
        "gitlab",
        "bitbucket",
        "medium",
        "behance",
        "dribbble",
        "kaggle",
        "huggingface",
        "stackoverflow",
        "linkedin",
        "notion",
        "wordpress",
        "wixsite",
        "netlify",
        "vercel",
        "herokuapp",
        "com",
        "net",
        "org",
        "dev",
        "www",
    ]
)

# The legal form after an employer's name («Acme S.r.l.», «Acme Ltd»), which a card
# would leave out.
_LEGAL = frozenset(
    [
        "srl",
        "spa",
        "sas",
        "snc",
        "sapa",
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "llc",
        "llp",
        "plc",
        "gmbh",
        "ag",
        "bv",
        "nv",
        "sa",
        "sl",
        "co",
        "corp",
    ]
)

# Where an employer's name is read (`identifying`).
EMPLOYER_FIELDS = frozenset(["ruolo", "sintesi", "settori"])

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_DOTTED = re.compile(r"\b(?:\w\.){2,}")


def _names(value: str, name: str) -> bool:
    """Whether `name` is in `value` as a whole word written with a capital: in any case
    but all lower case, so «Conti» and «CONTI» are the person and «conti» is not; of a
    name of two words, one capital is enough («de Luca»)."""
    person = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
    return any(not found.group().islower() for found in person.finditer(value))


def _without_skills(value: str, skills: list[str]) -> str:
    """`value` with each of `skills` blanked where it is written whole, the longest
    first, so a name read afterwards is not one inside a technology («Ruby on Rails»)."""
    for skill in sorted((skill.strip() for skill in skills), key=len, reverse=True):
        if skill:
            value = re.sub(rf"(?<!\w){re.escape(skill)}(?!\w)", " ", value, flags=re.IGNORECASE)
    return value


def _tokens(value: str) -> tuple[str, ...]:
    """Lower-case alphanumeric runs: `my-lib`, `my_lib` and `My Lib` are all `(my, lib)`,
    and an abbreviation written with dots («s.r.l.») is one run."""
    return tuple(
        _TOKEN.findall(_DOTTED.sub(lambda hit: hit.group().replace(".", ""), value).lower())
    )


@dataclass(frozen=True)
class KnownIdentifiers:
    """What could name this person: `cognome` and `nome` from the user row, `github_login`
    and `repo_names` (the repositories they own) from the GitHub source, `hostnames` of
    the links they gave, `employers` as the CV's card draft names them. Everything but the
    two names is empty when no source was read."""

    cognome: str
    nome: str = ""
    github_login: str | None = None
    repo_names: list[str] = field(default_factory=list)
    hostnames: list[str] = field(default_factory=list)
    employers: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls, cognome: str = "", nome: str = "") -> "KnownIdentifiers":
        """The no-source case: the person's name, and nothing from a source."""
        return cls(cognome=cognome, nome=nome)


def _phrase(identifier: str, *, legal: bool = False) -> tuple[str, ...] | None:
    """The tokens of `identifier` a card is searched for, or `None` for one too weak to
    search: under three characters, or only ordinary words."""
    tokens = _tokens(identifier)
    if legal:
        tokens = tuple(token for token in tokens if token not in _LEGAL)
    if not tokens or len("".join(tokens)) < 3 or all(token in STOPLIST for token in tokens):
        return None
    return tokens


# A second-level label that is a category and not a name (`co.uk`, `com.au`).
_GENERIC_SECOND_LEVEL = frozenset(["co", "com", "org", "net", "gov", "ac", "edu"])


def _hostname_phrases(hostname: str) -> list[tuple[str, ...]]:
    """What a card is searched for to keep a link's address out of it: the whole host
    (`acme-labs.io` written with its TLD), and each label of it but the TLD on its own
    (`acme-labs`, so «Acme Labs» is caught, and the `mario` of `mario.github.io`), every
    one under `_phrase`'s rules: a label that is an ordinary word or a well-known platform
    is ignored."""
    host = hostname.strip().lower().removeprefix("www.")
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 3 and labels[-2] in _GENERIC_SECOND_LEVEL:
        labels = labels[:-2]
    elif len(labels) >= 2:
        labels = labels[:-1]
    found = [_phrase(host), *(_phrase(label) for label in labels)]
    return [phrase for phrase in dict.fromkeys(found) if phrase is not None]


def _contains(text: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    """Whether `phrase` is a run of whole tokens of `text`, or `phrase` written without a
    separator is one token of it («MyLib» for `my-lib`)."""
    size = len(phrase)
    if any(text[i : i + size] == phrase for i in range(len(text) - size + 1)):
        return True
    joined = "".join(phrase)
    return size > 1 and joined in text


def identifying(card: Card, known: KnownIdentifiers) -> list[str]:
    """The names of the fields of `card` that identify the person, in the card's order,
    each once; empty when the card is anonymous.

    The surname is read, as a whole word written with a capital, in the role, the summary,
    the skills and the sectors; `luogo` and `lingue` are left out because a city or a
    language is the CV's own (Messina, Russo, Tedesco). The first name, and each word of
    one of two («Maria Grazia») of three letters or more, is read in the role and the
    summary only, and not inside one of the card's own skills (Ada, Ruby, Julia, Pascal
    are languages as well as people). A link or an email address is read everywhere.
    The GitHub login, the owned repositories, the links' hostnames and the employers are
    read everywhere, case-insensitively; a repository named exactly like one of the
    card's own skills is the technology (a fork of `django`), not a leak."""
    fields: list[tuple[str, list[str]]] = [
        ("ruolo", [card.ruolo]),
        ("sintesi", [card.sintesi]),
        ("competenze", card.competenze),
        ("settori", card.settori),
        ("lingue", card.lingue),
        ("luogo", [card.luogo] if card.luogo is not None else []),
    ]
    named = {"ruolo", "sintesi", "competenze", "settori"}
    prose = {"ruolo", "sintesi"}

    surname = known.cognome.strip()
    first = known.nome.strip()
    given = {first, *(word for word in re.split(r"[\s\-]+", first) if len(word) >= 3)}
    given.discard("")

    skills = {_tokens(skill) for skill in card.competenze}
    repos = [
        phrase
        for name in known.repo_names
        if (phrase := _phrase(name)) is not None and phrase not in skills
    ]
    # A login, or a link's label, that is exactly one of the card's own skills is the
    # technology (a login `flask`, a site `rust.example`), as a repository named so is.
    own = [
        phrase
        for phrase in (
            _phrase(known.github_login) if known.github_login else None,
            *(p for host in known.hostnames for p in _hostname_phrases(host)),
        )
        if phrase is not None and phrase not in skills
    ]
    searched = [
        *repos,
        *own,
    ]
    # An employer is also very often a technology (Google, Microsoft, Oracle, SAP, Amazon):
    # «Google Cloud» among the skills is no leak. So an employer is read only where the
    # card speaks of the person's work, the role, the summary and the sectors, and there
    # with the card's own skills blanked out first: «Lavora in Google» is caught, «esperto
    # di Google Cloud» for a person who lists that skill is not.
    employers = [
        phrase
        for phrase in (_phrase(employer, legal=True) for employer in known.employers)
        if phrase is not None
    ]

    hit: list[str] = []
    for name, values in fields:
        for value in values:
            if (
                _ADDRESS.search(value)
                or (name in named and surname and _names(value, surname))
                or (
                    name in prose
                    and any(_names(_without_skills(value, card.competenze), g) for g in given)
                )
                or any(_contains(_tokens(value), phrase) for phrase in searched)
                or (
                    name in EMPLOYER_FIELDS
                    and any(
                        _contains(_tokens(_without_skills(value, card.competenze)), phrase)
                        for phrase in employers
                    )
                )
            ):
                hit.append(name)
                break
    return hit
