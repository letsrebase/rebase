"""Cross-space identity: one proven email, several spaces (design 2026-09-23,
REB-345/376). Lives entirely in the registry database, beside
`pigrocrm.core.tenants` -- see that package's own docstring for why the registry is
the one module allowed to know more than one space exists.
"""

from pigrocrm.core.identity.models import Identity, IdentityLinkToken, IdentitySession
from pigrocrm.core.identity.schemas import IdentityRead
from pigrocrm.core.identity.service import IdentityService

__all__ = [
    "Identity",
    "IdentityLinkToken",
    "IdentityRead",
    "IdentitySession",
    "IdentityService",
]
