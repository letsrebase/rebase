"""The per-process `GoogleTokenClient` cache, shared by every Google surface of the API
(the Gmail routes, the Drive credential, the mail drafts).

One per process, keyed by the OAuth client it authenticates as.

Not one per request, which is what an adapter naturally writes and what would quietly undo
the module this cache lives in: `GoogleTokenClient` says "one per process, shared across
requests: the cache is the whole point", and a fresh instance per request throws every
cached access token away -- an extra OAuth round trip on every sync. It also makes
`forget()` mean something: `disconnect` calls it, and on a throwaway client that call would
clear a cache nobody was ever going to read. One Google OAuth client authenticates Gmail
and Drive both, so the access-token cache it keeps is one cache: a second, Drive-only dict
would cold-start against tokens a Gmail sync had already warmed, and `forget()` on one
instance would leave the other's copy of the same token live.

Keyed on the client id rather than on the `Settings` object: `get_settings` is `lru_cache`d
in production so the object is a singleton there, but the test suite overrides the
dependency per test and an identity key would leak one test's client into the next. The
secret is not part of the key -- one client id has one secret, and a dict key is one more
place a credential would sit.

The space's state prefix is part of it (REB-394). Spaces borrowing the root's client share
its client id, and the access tokens in a client's cache are keyed by the account's id
alone: a row copied into another space's database would otherwise pick up the first
space's live token without unsealing anything, and `forget()` in one space would evict
another's. The prefix is empty for the root and for a space with a client of its own, and
it is not a secret.
"""

import threading

from pigrocrm.core.config import Settings
from pigrocrm.core.gmail.tokens import GoogleTokenClient
from pigrocrm.core.gmail.transport import GmailTransport

_token_clients: dict[tuple[str, str], GoogleTokenClient] = {}
# FastAPI runs sync endpoints in a thread pool, so two requests can reach a cold cache at
# once. Same reasoning, and the same shape, as `deps.py`'s `_registry_lock`.
_token_clients_lock = threading.Lock()


def token_client_key(settings: Settings) -> tuple[str, str]:
    """Which cached `GoogleTokenClient` a request's settings get: one per client id and per
    space borrowing it."""
    return (settings.google_oauth_state_prefix, settings.google_client_id)


def token_client(settings: Settings) -> GoogleTokenClient:
    key = token_client_key(settings)
    client = _token_clients.get(key)
    if client is None:
        with _token_clients_lock:
            client = _token_clients.get(key)
            if client is None:
                client = GoogleTokenClient(
                    client_id=settings.google_client_id,
                    client_secret=settings.google_client_secret,
                    transport=GmailTransport(),
                )
                _token_clients[key] = client
    return client
