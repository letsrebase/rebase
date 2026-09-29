"""The refusals a document backend can produce that name no account.

Everything else `DocumentStorage` raises is about a *key* or about Google -- an unsafe
key is `ValidationFailed` from `storage/base.py`, a missing blob is `NotFound`, a Drive
outage is a `Conflict` from `drive/transport.py`. These two are about the installation
rather than the request: `StorageNotConfigured` is the backend chosen (`gdrive`), no
service account configured, and the titolare not yet having connected Drive and chosen
a folder -- nothing broken, nothing missing, a setup step not yet done.
`StorageUnreachable` (REB-562 fix round 3) is the same shape for the state one step
later: the folder *is* chosen, but the credential behind it just failed, and the caller
asking may not be the admin who owns it.

It is a module of its own rather than a line in `core/errors.py` for the reason
`drive/errors.py` gives for itself: the sentence has to survive the trip to the screen,
and the sentence is the whole value of each class. `core/errors.py` holds the five
shapes every domain shares; this holds what storage knows how to say about itself,
never about which Google account it happens to be backed by.
"""

from pigrocrm.core.errors import Conflict

# The entity every failure about a document's bytes is reported under -- the same one
# `drive/transport.py` uses (`_ENTITY`), because the API and the MCP adapter both
# render `Conflict.details["entity"]` and an adapter that routed on it would otherwise
# send the reader to a different place for two failures of the same feature.
STORAGE_ENTITY = "document_blob"

# The whole point of the class. An operator setting `PIGROCRM_STORAGE_BACKEND=gdrive`
# has two ways to finish the job, and this is the one that needs no Google Cloud
# console: connect Drive as yourself and say which folder to write into. The sentence
# names the screen, because this error is the only place the reader is looking.
NOT_CONFIGURED_REASON = (
    "Google Drive non è pronto a ricevere i documenti: collega Drive e scegli la "
    "cartella di scrittura in Impostazioni → Drive"
)


class StorageNotConfigured(Conflict):
    """Raised at the first operation, never at construction.

    A `Conflict` and not a `ValidationFailed`, deliberately: nothing about the caller's
    request is wrong, and a 422 with a field name would send somebody looking for a bad
    parameter. It is the state of the installation that conflicts with the operation
    asked for, which is what `Conflict` means everywhere else in this codebase -- and it
    is already mapped (409 by the API, guidance by the MCP adapter), so no adapter has
    to learn a new code for it.

    Deliberately built with no arguments: there is exactly one thing to say and one
    place to say it, and a `reason` parameter would let two call sites word it
    differently.
    """

    def __init__(self) -> None:
        super().__init__(STORAGE_ENTITY, NOT_CONFIGURED_REASON)


# The folder *is* configured -- an admin chose it, `DriveRepository.storage_account`
# finds the row -- but the credential behind it just failed, so "collega Drive e
# scegli la cartella" (`NOT_CONFIGURED_REASON`) would send the reader to redo a step
# that is already done. This names the actual next action instead, and names nobody:
# see `StorageUnreachable`'s own docstring for why.
UNREACHABLE_REASON = (
    "La cartella di scrittura dello spazio non è raggiungibile: un amministratore deve "
    "ricollegare Google Drive da Impostazioni → Drive"
)


class StorageUnreachable(Conflict):
    """The space's write folder is configured, but the admin's credential behind it
    just failed a token refresh (REB-562 fix round 3).

    Raised only by `storage/lazy_drive.py`'s `_run` -- the one path every actor's
    upload, download, regeneration and delete goes through, regardless of whose Drive
    backs the space's storage -- in place of re-raising the holder's own
    `DriveCredentialRevoked`/`DriveConsentExpired` unchanged. Those two name the
    account: `email_address` in the sentence and in `details`, which
    `domain_error_handler` (`pigrocrm_api/errors.py`) spreads into the JSON body
    verbatim. That is the right answer on the Drive settings page, where the viewer
    *is* the account owner (`GoogleDriveAccountService.usable`, `set_roots`) and
    reading their own email back is not a leak. It is the wrong answer here: a
    collaboratore downloading a document they have every right to read has no reason to
    learn which admin's Google account the space happens to write through, or that
    admin's address. `_run` logs the email once, server-side, before raising this --
    the fact is not lost, only kept off the wire.

    Deliberately built with no arguments, the same as `StorageNotConfigured`: one
    sentence, one place to say it.
    """

    def __init__(self) -> None:
        super().__init__(STORAGE_ENTITY, UNREACHABLE_REASON)
