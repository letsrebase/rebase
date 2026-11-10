"""The shape of `tools/`: one module per tool family, none of them forgotten or shadowed.

`tools/<family>.py` holds what a tool calls and `tools/families/<family>.py` registers the
tool itself, under the same name. Two ways that goes wrong quietly: a family file nobody
added to `FAMILIES` (its tools are never registered, and no test of another tool notices),
and a package attribute that points at the family instead of the call-through, so
`from pigrocrm_mcp.tools import timetracking` returns a module without `log_time`.
"""

import importlib
from pathlib import Path

import pigrocrm_mcp.tools as tools
from pigrocrm_mcp.tools.families import FAMILIES

TOOLS_DIR = Path(tools.__file__).parent
FAMILIES_DIR = TOOLS_DIR / "families"


def test_every_family_module_is_registered() -> None:
    on_disk = {path.stem for path in FAMILIES_DIR.glob("*.py") if path.stem != "__init__"}
    registered = {family.__name__.rsplit(".", 1)[-1] for family in FAMILIES}
    assert on_disk == registered, "a family module must be in `families/__init__.py::FAMILIES`"
    assert len(FAMILIES) == len(registered), "a family is registered twice"
    for family in FAMILIES:
        assert callable(family.register), family.__name__


def test_a_family_never_shadows_the_call_through_of_the_same_name() -> None:
    for family in FAMILIES:
        name = family.__name__.rsplit(".", 1)[-1]
        if (TOOLS_DIR / f"{name}.py").exists():
            call_through = importlib.import_module(f"pigrocrm_mcp.tools.{name}")
            assert getattr(tools, name) is call_through, name
