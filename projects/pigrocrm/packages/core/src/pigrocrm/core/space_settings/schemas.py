from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The keys a space may decide for itself. Deliberately not every field of `Settings`:
# the database URL, the JWT secret and the cookie flag are the process's; the timezone
# is read by the clock through `get_settings()` and would lie if it differed per space.
OVERRIDABLE_KEYS: tuple[str, ...] = (
    "google_client_id",
    "google_client_secret",
    "google_token_key",
    "google_app_unverified",
    "storage_backend",
    "mcp_full_access",
    "payment_reminders_grace_days",
    "payment_reminders_min_interval_days",
    "payment_reminders_max_reminders",
    "gmail_backfill_days",
    "preferred_concentration_threshold",
)
# Never sent back to a browser; the page learns only whether they are set.
SECRET_KEYS: frozenset[str] = frozenset({"google_client_secret", "google_token_key"})


class SpaceSettingsUpdate(BaseModel):
    """Every field optional: absent means "leave as it is", an empty string clears the
    override so the environment's value shows through again."""

    model_config = ConfigDict(extra="forbid")

    google_client_id: str | None = Field(default=None, max_length=200)
    google_client_secret: str | None = Field(default=None, max_length=200)
    google_app_unverified: bool | None = None
    storage_backend: Literal["local", "gdrive"] | None = None
    mcp_full_access: bool | None = None
    payment_reminders_grace_days: int | None = Field(default=None, ge=1, le=365)
    payment_reminders_min_interval_days: int | None = Field(default=None, ge=1, le=365)
    payment_reminders_max_reminders: int | None = Field(default=None, ge=1, le=3)
    gmail_backfill_days: int | None = Field(default=None, ge=1, le=3650)
    preferred_concentration_threshold: float | None = Field(default=None, gt=0.0, le=1.0)
    # The version check of REB-622 (spec 2026-10-03 §11): the `updated_at` read off
    # `SpaceSettingsRead`, which is `null` on a database nothing was ever written to.
    # As on the two upsert bodies, a key that was sent is checked whatever its value, a
    # key left out checks nothing.
    updated_at: datetime | None = Field(
        default=None,
        description=(
            "The `updated_at` read on the settings this change starts from (`null` included): if "
            "someone else saved in the meantime, the request is rejected with 409 `stale_row`. "
            "Omitted, no check."
        ),
    )


class SpaceSettingsRead(BaseModel):
    """What the page shows: the effective values, which of them come from this
    database rather than the environment, and the two URLs Google has to know."""

    space: str | None
    public_url: str
    google_client_id: str
    google_client_secret_set: bool
    google_token_key_set: bool
    google_app_unverified: bool
    gmail_configured: bool
    # True while the space borrows the root's Google client (REB-394): nothing to set
    # or register. The redirect URIs below stay this space's own, for a client of its own.
    google_client_shared: bool
    redirect_uri_gmail: str
    redirect_uri_drive: str
    storage_backend: str
    mcp_full_access: bool
    payment_reminders_grace_days: int
    payment_reminders_min_interval_days: int
    payment_reminders_max_reminders: int
    gmail_backfill_days: int
    preferred_concentration_threshold: float
    overwritten: list[str]
    # The version a save hands back (REB-622): the `updated_at` of the newest row in
    # `space_settings`, `None` while there is none. Every write that changes anything
    # moves it, a cleared override included, because the service stamps a reserved row
    # (`SpaceSettingsService.VERSION_KEY`) on each; `max` over the table alone would
    # stand still when an older override is cleared under a newer one.
    updated_at: datetime | None
