from typing import Literal

from pydantic import BaseModel, Field

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
    "solleciti_grace_days",
    "solleciti_min_interval_days",
    "solleciti_max_reminders",
    "gmail_backfill_days",
    "concentrazione_soglia_preferita",
)
# Never sent back to a browser; the page learns only whether they are set.
SECRET_KEYS: frozenset[str] = frozenset({"google_client_secret", "google_token_key"})


class SpaceSettingsUpdate(BaseModel):
    """Every field optional: absent means "leave as it is", an empty string clears the
    override so the environment's value shows through again."""

    google_client_id: str | None = Field(default=None, max_length=200)
    google_client_secret: str | None = Field(default=None, max_length=200)
    google_app_unverified: bool | None = None
    storage_backend: Literal["local", "gdrive"] | None = None
    mcp_full_access: bool | None = None
    solleciti_grace_days: int | None = Field(default=None, ge=1, le=365)
    solleciti_min_interval_days: int | None = Field(default=None, ge=1, le=365)
    solleciti_max_reminders: int | None = Field(default=None, ge=1, le=3)
    gmail_backfill_days: int | None = Field(default=None, ge=1, le=3650)
    concentrazione_soglia_preferita: float | None = Field(default=None, gt=0.0, le=1.0)


class SpaceSettingsRead(BaseModel):
    """What the page shows: the effective values, which of them come from this
    database rather than the environment, and the two URLs Google has to know."""

    spazio: str | None
    public_url: str
    google_client_id: str
    google_client_secret_impostato: bool
    google_token_key_impostata: bool
    google_app_unverified: bool
    gmail_configurato: bool
    redirect_uri_gmail: str
    redirect_uri_drive: str
    storage_backend: str
    mcp_full_access: bool
    solleciti_grace_days: int
    solleciti_min_interval_days: int
    solleciti_max_reminders: int
    gmail_backfill_days: int
    concentrazione_soglia_preferita: float
    sovrascritte: list[str]
