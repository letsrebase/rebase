import re
import unicodedata
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from pigrocrm.core.validation import SafeStr

# Lowercase, 3 to 32 characters, letters, digits and hyphens, never a hyphen at either
# end. Doubles as a Postgres identifier after `-` -> `_`, and as a URL segment as is.
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$")
SLUG_MIN = 3
SLUG_MAX = 32

# Already paths of the site (deploy/nginx/spa.conf, the landing, the SPA), so a space
# with one of these names would shadow them or be shadowed by them. `root` is not a path:
# it is what `pigrocrm digest` calls the root installation, in its log and in `--slug`
# (REB-263), so a space by that name would read as the root in both.
RESERVED_SLUGS = frozenset(
    {
        "root",
        "app",
        "api",
        "health",
        "assets",
        "privacy",
        "termini",
        "orbiters",
        "pigrocrm",
        "pigro",
        "login",
        "www",
        "admin",
        "static",
        "registrati",
        "mcp",
    }
)


def slugify(nome: str) -> str:
    """What the page proposes from the name a person types: accents stripped, anything
    that is not a letter or digit turned into one hyphen, trimmed, lowercased."""
    ascii_only = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    return hyphenated[:SLUG_MAX].rstrip("-")


def validate_slug(slug: str) -> str | None:
    """The reason a slug is refused, in the page's own words, or None when it is fine.
    A function rather than a validator so the availability check can say why."""
    if len(slug) < SLUG_MIN:
        return f"il nome deve avere almeno {SLUG_MIN} caratteri"
    if len(slug) > SLUG_MAX:
        return f"il nome può avere al massimo {SLUG_MAX} caratteri"
    if not SLUG_PATTERN.match(slug):
        return "solo lettere minuscole, cifre e trattini, senza trattini all'inizio o alla fine"
    if slug in RESERVED_SLUGS:
        return "questo nome è riservato"
    return None


class TenantSignup(BaseModel):
    slug: str = Field(min_length=SLUG_MIN, max_length=SLUG_MAX)
    nome: SafeStr = Field(min_length=1, max_length=200)
    email: EmailStr
    # No password (spec 2026-09-12 §6.4): a space's first admin enters with a link by
    # mail, and a password chosen by whoever typed somebody else's address would outlive
    # the revocation that protects the real owner. `extra="forbid"` so a client that
    # still sends one hears about it.
    # What the wizard learned from the hub about this address: decides whether the
    # welcome mail tells the person about Orbiters. Never trusted for anything else.
    membro: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("slug")
    @classmethod
    def _slug_is_well_formed(cls, value: str) -> str:
        reason = validate_slug(value)
        if reason is not None:
            raise ValueError(reason)
        return value


class TenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    owner_email: str
    created_at: datetime


class TenantAvailability(BaseModel):
    slug: str
    disponibile: bool
    # Why not, when not: a reserved word, a taken name, a malformed one.
    motivo: str | None = None
