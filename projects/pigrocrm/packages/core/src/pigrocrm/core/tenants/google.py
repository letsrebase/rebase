"""The root's Google client, lent to the spaces (spec 2026-09-16 §5, REB-394).

Google compares the redirect URI of a consent with the list registered on the client,
character by character, so a callback per space (`/<slug>/api/gmail/oauth/callback`)
would be one more address to register for every signup. With
`PIGROCRM_GOOGLE_SHARED_CLIENT` on, every space borrowing the root's client sends
Google the root's own callback instead, and writes its slug ahead of the jti in the
`state`: `<slug>.<jti>`. The root's callback reads the slug back (`relay_slug`) and
hands the browser, with the same query, to that space's callback, which is where the
consent is actually completed.

Nothing is signed, and nothing needs to be. The slug only decides which space's
callback the browser is sent to. What makes a consent count is the jti, redeemed there
against the space's own `google_oauth_states` row: 192 random bits, single use, five
minutes, bound to the user who started the flow and holding the PKCE verifier that the
code exchange needs. A state whose slug was swapped for another space's lands on a
database where that jti was never issued, and the space's callback refuses a state
that does not carry its own prefix before looking anything up.

The token key is the other half. Each space's refresh tokens are encrypted with a key
derived from the root's and the space's slug (`space_token_key`), never with the root's
own key and never with a key stored in the space's database. See the DECISIONS row of
2026-09-23.
"""

import base64
import binascii
import re

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from pigrocrm.core.config import GOOGLE_TOKEN_KEY_BYTES
from pigrocrm.core.tenants.schemas import RESERVED_SLUGS, SLUG_PATTERN

# Neither a slug (`[a-z0-9-]`) nor a jti (base64url, `[A-Za-z0-9_-]`) can contain it,
# so the first one in a state is the boundary and nothing needs escaping.
STATE_SEPARATOR = "."
# HKDF's `info`: binds the derived key to its purpose and to one space. Versioned so a
# second derivation, if one is ever needed, cannot collide with this one.
_KEY_INFO = b"pigrocrm/google-token-key/v1/space:"
# What `gmail/oauth.py` mints: base64url, 32 characters today. Checked so that a state
# with anything else after the slug is not relayed at all.
_JTI = re.compile(r"[A-Za-z0-9_-]{1,128}")


def oauth_state_prefix(slug: str) -> str:
    """What a space borrowing the root's client writes ahead of its jti."""
    return f"{slug}{STATE_SEPARATOR}"


def relay_slug(state: str | None) -> str | None:
    """The space a consent belongs to, read off its `state`, or None when the state
    names no space: the root's own consent (a bare jti), a missing state, a prefix that
    is not a well-formed, unreserved slug, or anything but a jti after it. Only the
    shape is checked here; which spaces exist is the registry's answer, and an unknown
    one is a 404 at the space's own callback."""
    if not state:
        return None
    slug, separator, jti = state.partition(STATE_SEPARATOR)
    if not separator or not _JTI.fullmatch(jti):
        return None
    if slug in RESERVED_SLUGS or not SLUG_PATTERN.fullmatch(slug):
        return None
    return slug


def space_token_key(root_key: str, slug: str) -> str:
    """The key a space's refresh tokens are sealed with, derived from the root's.

    HKDF-SHA256 over the root key, with the slug in `info`: 32 bytes, base64, the same
    shape `decode_google_token_key` expects of `PIGROCRM_GOOGLE_TOKEN_KEY`. The key
    stays outside every database, like the root's (`gmail/crypto.py` says why that is
    the point), and a ciphertext copied into another space's database does not open
    there. Empty when the root has no valid key: the space then reads as Gmail not
    configured, rather than failing every request it serves.
    """
    try:
        root = base64.b64decode(root_key, validate=True)
    except (binascii.Error, ValueError):
        return ""
    if len(root) != GOOGLE_TOKEN_KEY_BYTES:
        return ""
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=GOOGLE_TOKEN_KEY_BYTES,
        salt=None,
        info=_KEY_INFO + slug.encode("ascii"),
    ).derive(root)
    return base64.b64encode(derived).decode("ascii")
