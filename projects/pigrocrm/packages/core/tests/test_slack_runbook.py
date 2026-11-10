"""The Slack runbook's manifest says what the code asks for (REB-739).

The manifest in `docs/superpowers/notes/2026-10-27-slack-app-runbook.md` is what Lorenzo pastes
into Slack, by hand, once. If the code asks for a scope the manifest does not list, the install
succeeds with fewer permissions and `complete` refuses it by name; if the manifest lists one the
code does not use, the app asks the workspace's admin for a permission nothing needs. So the two
lists are held equal, and the redirect URL the code builds is held to the manifest's path.
"""

import re
from pathlib import Path

from fakes.slack_fixtures import slack_settings
from sqlalchemy.orm import Session

from pigrocrm.core.slack.oauth import SlackOAuthService
from pigrocrm.core.slack.schemas import SLACK_BOT_SCOPES

RUNBOOK = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "superpowers"
    / "notes"
    / "2026-10-27-slack-app-runbook.md"
)


def _manifest() -> str:
    match = re.search(r"```yaml\n(.*?)```", RUNBOOK.read_text(), re.DOTALL)
    assert match is not None, "the runbook has no manifest"
    return match.group(1)


def test_the_manifests_bot_scopes_are_the_codes() -> None:
    manifest = _manifest()
    block = re.search(r"    bot:\n((?:      - .+\n)+)", manifest)
    assert block is not None
    listed = tuple(re.findall(r"      - (\S+)", block.group(1)))
    assert listed == SLACK_BOT_SCOPES
    assert not re.search(r"^\s+user:\s*$", manifest, re.MULTILINE), (
        "no user scopes: nothing reads or writes as a person"
    )


def test_the_manifests_redirect_urls_end_in_the_callback_the_code_builds(
    db_session: Session,
) -> None:
    urls = re.findall(r"    - (https://\S+)", _manifest())
    assert urls and all(url.endswith("/api/slack/oauth/callback") for url in urls)
    service = SlackOAuthService(db_session, settings=slack_settings(), client=None)  # type: ignore[arg-type]
    assert service.redirect_uri.endswith("/api/slack/oauth/callback")


def test_the_manifest_leaves_pkce_and_token_rotation_off() -> None:
    manifest = _manifest()
    assert "token_rotation_enabled: false" in manifest
    assert "pkce_enabled" not in manifest
    assert "request_url" not in manifest, "nothing in Slack calls PigroCRM"
