"""What only the MCP's HTTP transport reads from the environment (`REBASE_MCP_*`).

Kept beside the transport rather than in `rebase_core.config`: no other process cares
which Host names this one answers to. `REBASE_MCP_TOKEN` (stdio's credential) shares the
prefix and is read in `__main__`, which is why unknown names are ignored here.

The defaults are the names the deployment really uses: the public hosts nginx forwards
(`proxy_set_header Host $host`, so the header the service sees is the public name, with no
port), the loopback and the compose service's own name for a health check or a shell on
the host. A stack served under another name sets `REBASE_MCP_ALLOWED_HOSTS` (and the
compose file has to pass it through to the `mcp` service); every entry is `host` or
`host:*` for any port.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_HOSTS = (
    "letsrebase.com,preview.letsrebase.com,"
    "localhost,localhost:*,127.0.0.1,127.0.0.1:*,[::1],[::1]:*,mcp,mcp:*"
)
DEFAULT_ORIGINS = (
    "https://letsrebase.com,https://preview.letsrebase.com,"
    "http://localhost:*,http://127.0.0.1:*,http://[::1]:*"
)


def _names(raw: str) -> list[str]:
    return [name.strip() for name in raw.split(",") if name.strip()]


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REBASE_MCP_", env_file=".env", extra="ignore")

    # Comma-separated lists, because an environment variable carries no list.
    allowed_hosts: str = DEFAULT_HOSTS
    allowed_origins: str = DEFAULT_ORIGINS
    # Failed bearers a client may present per minute before it is told to wait, without
    # the database being asked (REB-889): the token is 32 random bytes, so this is about
    # the cost of a flood, not the odds of a guess.
    bad_bearers_per_minute: int = 10

    @property
    def hosts(self) -> list[str]:
        return _names(self.allowed_hosts)

    @property
    def origins(self) -> list[str]:
        return _names(self.allowed_origins)
