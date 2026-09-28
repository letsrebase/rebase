"""Schemas for the Drive credential (spec 9 §5.2) -- a second grant, kept apart from
Gmail's own `gmail/schemas.py` the same way `drive/models.py` keeps
`GoogleDriveAccount` apart from `GoogleAccount`: two independent OAuth grants, not one
wider one.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from pigrocrm.core.drive.query import OUTSIDE_ID_PATTERN
from pigrocrm.core.validation import SafeStr

# A Drive file id, never a query nor free text: Google mints these as URL-safe base64
# strings, in practice a good deal longer than ten characters, but there is no published
# minimum. Ten is a floor generous enough to admit every real id while still refusing the
# obviously-wrong single-character or few-character inputs a typo would produce, and it
# is what makes `DriveRootsUpdate` reject a Drive *query* -- `'x' in parents or name
# contains 'a'` -- outright rather than forwarding it to the Drive API as a folder id.
#
# The pattern is `query.py`'s own, imported and never re-typed. It was written out here
# once, identically, and that is exactly the shape of duplication `OUTSIDE_ID_PATTERN`'s
# comment there warns about: two spellings of one rule diverge silently in both
# directions -- a looser one here would let a query reach `checked_outside_id` and be
# refused from three levels down instead of by this schema, and a stricter one would
# refuse a root the titolare configured legitimately. Same rule, same source, whether it
# arrives on a PATCH body (here) or as an MCP tool argument (`drive_privileged.py`, which
# imports the same name).
_DriveId = Annotated[SafeStr, Field(pattern=OUTSIDE_ID_PATTERN)]

DRIVE_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_SCOPE_FILE = "https://www.googleapis.com/auth/drive.file"
# `openid` + `email` identify *which* Google identity was connected, the same reason
# `gmail/schemas.py`'s `REQUESTED_SCOPES` carries them: without the stable `sub` there is
# no way to refuse a reconnection that points at a different account by mistake.
# `drive.readonly` is what lets the CRM read the configured root folders;
# `drive.file` is the narrower grant that lets it write only what it itself created,
# which is what `storage_folder_id` writes into.
DRIVE_REQUESTED_SCOPES: tuple[str, ...] = (
    "openid",
    "email",
    DRIVE_SCOPE_READONLY,
    DRIVE_SCOPE_FILE,
)

# The same four values, and the same distinction between them, as `gmail/schemas.py`'s
# `GmailStatus` -- see `GoogleDriveAccount.status`'s docstring for why the four are kept
# apart from `GmailStatus` rather than shared with it.
DriveStatus = Literal["active", "expired", "revoked", "disconnected"]


class GoogleDriveAccountRead(BaseModel):
    """What the settings page shows for the Drive connection. No token, in either form:
    not the plaintext, not the ciphertext, not the nonce -- the same rule as
    `GoogleAccountRead`."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_address: str
    scopes_granted: list[str]
    status: DriveStatus
    consent_expires_at: datetime | None
    root_folder_ids: list[str]
    storage_folder_id: str | None
    # Exposed, not derived: whether the configured write folder was ever proven
    # reachable with this credential is a thing only the row knows (see
    # `GoogleDriveAccount.storage_folder_verified`), and the Drive panel is where a
    # «cartella di scrittura non ancora verificata» note belongs. It deliberately does
    # *not* raise a banner: `DriveBannerReason` is a closed enum about the health of the
    # credential, and an unproven folder is a configuration the next save will prove.
    storage_folder_verified: bool
    last_error: str | None
    last_error_at: datetime | None
    connected_at: datetime
    disconnected_at: datetime | None


# The four things that can be wrong, and `None` for "nothing is" -- the Drive twin of
# `gmail/schemas.py`'s `GmailBannerReason`. `disconnected` is absent for the same reason
# it is absent there: a Drive the user unhooked on purpose is not a fault to warn about.
DriveBannerReason = Literal["revoked", "expiring", "expired", "scope_missing"] | None


class GoogleDriveSpaceStorage(BaseModel):
    """Whose account holds the space's one write folder in effect -- what
    `DriveRepository.storage_holder` resolves for every document the space generates
    (REB-562), named for the admin reading the Drive page rather than left for them to
    ask a colleague or wait for a 409 `StorageNotConfigured` on the first generated
    document.

    `DriveHealth.space_storage` is this or `None`, and `None` there carries two
    meanings on purpose, never told apart on the wire: no admin's row currently names a
    write folder, or the viewer is not an admin. The write folder is chosen and read
    from an admin's row only (`set_roots`'s `require_admin`, `storage_holder`'s own
    join), so a non-admin has no decision to make about it and no name to learn from it
    either -- `GoogleDriveAccountService.health` computes this field only for an admin
    actor, the same gate `set_roots` already applies to *naming* this folder, and the
    Drive page renders the line only behind the same admin check that already hides the
    write-folder field itself (`DrivePanel`, REB-457)."""

    holder_name: str
    holder_email: str


class DriveHealth(BaseModel):
    """Everything the Drive banner needs, in one response -- the Drive twin of
    `gmail/schemas.py`'s `GmailHealth`, including its two reasons for existing:
    `banner_text` is a distinct field because the action behind each reason differs, and
    `configured` is what tells an installation with no Google client apart from an owner
    who simply has not connected Drive yet, which `account is None` alone cannot.

    `space_storage` is REB-562's addition: the space's *effective* write folder, as
    opposed to `account.storage_folder_id`, which is only the viewer's own row and may
    say nothing about what `DriveRepository.storage_account` actually resolves (an
    admin who has not connected Drive at all, or whose own folder lost the race to a
    more recently updated admin's). See `GoogleDriveSpaceStorage`'s own docstring for
    what its `None` means."""

    account: GoogleDriveAccountRead | None
    banner: DriveBannerReason
    banner_text: str | None
    missing_scopes: list[str]
    configured: bool
    space_storage: GoogleDriveSpaceStorage | None


class DriveRootsUpdate(BaseModel):
    """Which folders the CRM may read from, and which one it may write generated
    documents into.

    Both fields hold Drive file ids and nothing else -- `_DriveId`'s pattern (which is
    `query.py`'s `OUTSIDE_ID_PATTERN`, the same rule `checked_outside_id` applies) is what
    keeps a Drive *query* (`'x' in parents or name contains 'a'`, valid syntax for
    `files.list`'s `q` parameter) from ever reaching this table disguised as a folder id.
    `extra="forbid"` is not needed here the way it is on the wider Create/Update schemas
    elsewhere: there are only the two fields a Drive configuration has, and nothing else
    to guard against.

    The route is a `PATCH`, and `storage_folder_id`'s default is what makes it one:
    omitting the field leaves the configured write folder alone, sending `null` clears
    it. Those two are the same *value* on this model -- `None` either way -- so the
    difference lives only in `model_fields_set`, which is what
    `GoogleDriveAccountService.set_roots` reads rather than the attribute. Anything
    added to this schema later with a `None` default inherits that obligation.
    `root_folder_ids` has no default and is therefore always part of the change.
    """

    root_folder_ids: list[_DriveId] = Field(min_length=1, max_length=20)
    storage_folder_id: _DriveId | None = None
