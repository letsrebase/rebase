"""Every operation a service names has been classified for agent credentials, on purpose.

`Actor` refuses an agent any action that is in none of `AGENT_ALLOWED_ACTIONS`,
`FULL_ACCESS_ACTIONS` and `PERSON_ONLY_ACTIONS`, so a typo at a call site fails closed
for an agent. That is the safe direction and also a silent one: the operator of an
installation that opted in sees one operation refuse and nobody knows why. This sweep
reads every action key a service passes to the `require_*` methods (and the `action=` of
`drive_reader_for`, and the argument of the API's `person_only` route gate) and fails at
the moment somebody adds or misspells one.
"""

import ast
from pathlib import Path

from pigrocrm.core.agent_actions import (
    ACTION_LABELS,
    AGENT_ALLOWED_ACTIONS,
    FULL_ACCESS_ACTIONS,
    PERSON_ONLY_ACTIONS,
)

PROJECT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = (
    PROJECT / "packages" / "core" / "src",
    PROJECT / "apps" / "api" / "src",
    PROJECT / "apps" / "mcp" / "src",
)
REQUIRE_METHODS = {
    "require_write",
    "require_admin",
    "require_unscoped_admin",
    "require_agent_allowed",
    "require_person",
}
CLASSIFIED = AGENT_ALLOWED_ACTIONS | FULL_ACCESS_ACTIONS | PERSON_ONLY_ACTIONS


def _module_constants(tree: ast.Module) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            name, value = node.target.id, node.value
        else:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            found[name] = value.value
    return found


def _named_actions() -> dict[str, set[str]]:
    """Action key -> the files that pass it. A constant imported from another module is
    resolved through the one public constant of that name."""
    trees = {
        path: ast.parse(path.read_text()) for root in SOURCE_ROOTS for path in root.rglob("*.py")
    }
    constants = {path: _module_constants(tree) for path, tree in trees.items()}
    shared = {
        name: value
        for per_file in constants.values()
        for name, value in per_file.items()
        if not name.startswith("_")
    }
    found: dict[str, set[str]] = {}
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            candidates: list[ast.expr] = []
            method = node.func.attr if isinstance(node.func, ast.Attribute) else None
            function = node.func.id if isinstance(node.func, ast.Name) else None
            if method in REQUIRE_METHODS or function == "person_only":
                candidates = node.args[:1]
            elif function == "drive_reader_for":
                candidates = [kw.value for kw in node.keywords if kw.arg == "action"]
            for arg in candidates:
                value: str | None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                elif isinstance(arg, ast.Name):
                    value = constants[path].get(arg.id) or shared.get(arg.id)
                else:
                    continue  # a parameter: the caller's literal is swept at its own site
                if value is not None:
                    found.setdefault(value, set()).add(path.name)
    return found


def test_the_sweep_finds_the_call_sites() -> None:
    found = _named_actions()
    assert "issue_invoice" in found
    assert "send_email" in found, "a constant defined in the same module must resolve"
    assert "review_invoice_import" in found, "a constant imported from another module must resolve"


def test_every_action_a_service_names_is_classified() -> None:
    unclassified = {
        action: sorted(files)
        for action, files in _named_actions().items()
        if action not in CLASSIFIED
    }
    assert not unclassified, (
        "classify these in `core/agent_actions.py` (allowed, full access or person only); "
        f"until then no agent credential can perform them: {unclassified}"
    )


def test_no_classified_action_is_a_key_nobody_passes() -> None:
    """A stale key is a classification for an operation that no longer exists, which hides
    a typo on the key that replaced it."""
    stale = sorted(CLASSIFIED - set(_named_actions()))
    assert not stale, f"no service passes these any more: {stale}"


def test_a_label_belongs_to_a_classified_action() -> None:
    assert not set(ACTION_LABELS) - CLASSIFIED
