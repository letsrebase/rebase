"""Every `/app/...` path core builds into a mail, digest, reminder or push payload is a
route the web app serves (the to-do list is `/app/activities`, never `/app/today`)."""

import re
from pathlib import Path

import pytest

import pigrocrm.core.mail.digest as digest_module
import pigrocrm.core.reminders.links as links_module
from pigrocrm.core.dashboard import service as dashboard_service

ROUTES = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "routes" / "app"
PATH = re.compile(r"/app/([a-z-]+)(?:/([a-z-]+))?")


def _served(first: str, second: str | None) -> bool:
    return (ROUTES / f"{first}.tsx").exists() or (ROUTES / first).is_dir()


@pytest.mark.parametrize("module", [digest_module, links_module, dashboard_service])
def test_every_built_path_is_a_served_route(module: object) -> None:
    source = Path(module.__file__).read_text()  # type: ignore[attr-defined]
    code_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    found = {m.groups() for line in code_lines for m in PATH.finditer(line)}
    assert found, "the module builds no /app path"
    missing = sorted(f"/app/{a}" for a, b in found if not _served(a, b))
    assert not missing, f"not served by the web app: {missing}"


def test_the_digest_todo_link_is_the_activities_route() -> None:
    source = Path(digest_module.__file__).read_text()
    assert "/app/activities?view=today" in source
    assert "/app/tasks" not in source and "/app/today" not in source
