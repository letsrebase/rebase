"""The hub's settings, read once from the environment (`REBASE_*`) and `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REBASE_", env_file=".env", extra="ignore")

    # The hub's own database. Nothing here derives a name from another product's URL:
    # the old `PIGROCRM_ORBITERS_DATABASE_URL` fallback ("the CRM's server with the
    # database renamed") is exactly the coupling this project was split to remove. That
    # variable kept the old brand in its name and is history, so it is written here as
    # it actually was.
    database_url: str = "postgresql+psycopg://rebase:rebase@localhost:5433/rebase"

    # --- ChatGPT Ads: the signup conversion --------------------------------------------
    # The pixel measures the signup from the browser; these values are the server half
    # (`conversions.py`), which exists because the browser event is the one that gets
    # lost: an ad blocker, a network that drops the SDK, a tab closed before the ping
    # leaves. `openai_pixel_id` is public (it sits in the website's markup) and is
    # repeated here because the conversions endpoint wants it in the query string;
    # `openai_conversions_api_key` is a secret with write access to the conversion data
    # source and lives in the server's `.env` only. With either empty no server event is
    # sent, and that is not an error: it is a site that runs no campaigns.
    openai_pixel_id: str = ""
    openai_conversions_api_key: str = ""
    # The page where the conversion happens, required by the API for a `web` event and
    # taken from here rather than from the request: a `source_url` that arrives from the
    # client is a string the caller chose, forwarded to a third party as it came.
    signup_url: str = "https://letsrebase.com/"
    # Whether to also send OpenAI the SHA-256 of the address. It improves attribution,
    # and a hash of an email is still that person's identifier: whoever runs the site
    # decides, and the default is no.
    openai_conversions_send_hashed_email: bool = False

    # --- PostHog: the completion event, from the server -------------------------------
    # The browsers report the wizard's steps (`shared/analytics`); the API reports the
    # completion itself (`analytics.py`, REB-215), because the browser's event is the one
    # an ad blocker eats: on 2026-09-14/15 three profiles out of seven arrived with no
    # event at all. The key is the public project key, the same one the browsers carry,
    # read from here rather than imported because this process never sees `shared/`.
    # Empty means no client and no event, which is what the tests and a self-hosted
    # stack want.
    posthog_key: str = ""
    posthog_host: str = "https://eu.i.posthog.com"

    # --- the one cookie, for a member and an admin alike (REB-281) -------------------
    # `Secure` by default, like PigroCRM: on plain HTTP the browser drops the cookie and
    # the login looks like it worked. Local development sets it to false in its `.env`;
    # compose never forwards it, so a deploy cannot inherit that.
    cookie_secure: bool = True

    # --- the member area -------------------------------------------------------------
    # Resend sends the magic link. An empty key means no sender, and the API answers the
    # link request with a 503 sentence rather than pretending a mail went out: the key
    # lives in the server's `.env` only (`.env.example`).
    resend_api_key: str = ""
    mail_from: str = "Rebase <ciao@letsrebase.com>"
    # Where the SPA answers, for the link in the mail: `{hub_url}/entra?t=...`. Local
    # development points it at the Vite dev server.
    hub_url: str = "https://letsrebase.com/hub"
    magic_link_minutes: int = 15
    # Sliding: every authenticated request pushes the expiry this far ahead, so anyone
    # signed in, member or admin alike, sees the same window before being asked again.
    # `Settings.admin_session_days` was dropped in the same migration that dropped the
    # cookie it timed (REB-281).
    member_session_days: int = 30

    # --- PigroCRM's spaces, read-only -------------------------------------------------
    # The admin area lists which spaces of PigroCRM exist and whose they are (ORB-142) by
    # asking the CRM's API, never its database: the two products share nothing but this
    # token, which PigroCRM reads as `PIGROCRM_REGISTRY_TOKEN`. Empty means the page
    # answers 503 with a sentence, like the member area without a mail key. The URL is
    # also where a space is linked: `{pigro_api_url}/<slug>/app/`.
    pigro_api_url: str = "https://pigro.letsrebase.com"
    pigro_registry_token: str = ""

    # --- the contracts (REB-387) -----------------------------------------------------
    # Who signs for rebase, as the `rebase-*` fields of the framework agreement and the
    # letter: one JSON object, e.g. {"rebase-rappresentante": "Nome Cognome"}, read over
    # the package's own `rebase.json`. The server's `.env` only: this repository is
    # public. Empty means those fields print as blank lines.
    signer_json: str = ""

    # --- the signing site: Documenso (REB-387) ---------------------------------------
    # The instance's base URL as this process reaches it (production: the compose
    # service, `http://documenso:3000`; preview: `https://firma.letsrebase.com`), and the
    # API token of this environment's own Documenso user and team: one user per
    # environment, because a token reads and cancels every envelope of its user's teams
    # (probe § 8). Either empty: signing is off and «Invia per la firma» answers 503.
    documenso_url: str = ""
    documenso_api_token: str = ""
    # The secret typed into this environment's Documenso webhook, which Documenso sends
    # verbatim as `X-Documenso-Secret`. Empty: the webhook answers 503.
    documenso_webhook_secret: str = ""
    # Where rebase's own copy of every signed contract is mailed.
    contracts_mail: str = "ciao@letsrebase.com"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
