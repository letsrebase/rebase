"""The one rule that cannot be recovered later: core must not depend on adapters.

This is an allowlist, not a denylist. packages/core may import:
  - the standard library,
  - itself (the ``pigrocrm`` namespace),
  - whatever packages/core/pyproject.toml actually declares under
    ``[project].dependencies``.

Anything else - an adapter package such as pigrocrm_api or pigrocrm_mcp, a web
framework, a dependency only the adapters declare - fails the test. This means
adding a dependency to apps/api or apps/mcp never requires touching this file,
while importing something into core that core does not declare fails on its
own, without anyone having to remember to update a denylist by hand.

Known, accepted limitation: dynamic imports are only caught when the module
name is a string literal, e.g. ``importlib.import_module("fastapi")`` or
``__import__("fastapi")``. ``importlib.import_module(some_variable)`` cannot be
resolved statically and slips through - see
test_guard_known_limitation_cannot_catch_dynamic_import_via_variable below,
which demonstrates the gap with a real example instead of just asserting it
away in prose.
"""

import ast
import importlib
import inspect
import re
import sys
import tomllib
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = CORE_ROOT / "src" / "pigrocrm" / "core"

# Distribution name (as written in packages/core/pyproject.toml's
# [project].dependencies) -> importable module root, for the cases where they
# differ. Most distributions import as themselves ("-" becomes "_"); these
# do not and must be spelled out explicitly rather than guessed.
DIST_TO_MODULE_OVERRIDES: dict[str, str] = {
    "psycopg": "psycopg",  # extras such as "[binary]" are stripped before lookup
    "argon2-cffi": "argon2",
    "pyjwt": "jwt",
    "uuid-utils": "uuid_utils",
    "pydantic-settings": "pydantic_settings",
}

# PEP 508 requirement strings look like "name[extra]==1.2.3" or "name>=1.0";
# grab the leading name (letters, digits, ".", "_", "-") and stop at the
# first character that starts an extras marker, version specifier, or marker.
_DEP_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def _dependency_name(requirement: str) -> str:
    """Bare distribution name from a PEP 508 requirement string.

    "psycopg[binary]==3.3.4" -> "psycopg"; "sqlalchemy==2.0.51" -> "sqlalchemy".
    """
    match = _DEP_NAME_RE.match(requirement.strip())
    if not match:
        raise ValueError(f"cannot parse dependency requirement: {requirement!r}")
    return match.group(0)


def _module_root(requirement: str) -> str:
    dist = _dependency_name(requirement)
    return DIST_TO_MODULE_OVERRIDES.get(dist, dist.replace("-", "_"))


def _declared_dependency_roots() -> set[str]:
    data = tomllib.loads((CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies: list[str] = data["project"]["dependencies"]
    return {_module_root(dep) for dep in dependencies}


ALLOWED_ROOTS: set[str] = _declared_dependency_roots() | set(sys.stdlib_module_names) | {"pigrocrm"}


def _literal_str_arg(call: ast.Call) -> str | None:
    """The first positional argument of `call`, if it is a plain string literal."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """True for `importlib.import_module(...)` (any alias) or `__import__(...)`."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "import_module":
        return True
    return isinstance(func, ast.Name) and func.id == "__import__"


def _imported_roots(path: Path) -> set[str]:
    """Every top-level module root `path` imports, statically or dynamically-by-literal."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and _is_dynamic_import_call(node):
            literal = _literal_str_arg(node)
            if literal is not None:
                roots.add(literal.split(".")[0])
    return roots


def _offenders_in(source_dir: Path) -> list[str]:
    """One message per .py file under source_dir that imports a root outside ALLOWED_ROOTS."""
    offenders: list[str] = []
    for path in source_dir.rglob("*.py"):
        bad = _imported_roots(path) - ALLOWED_ROOTS
        if bad:
            offenders.append(
                f"{path.relative_to(source_dir)} imports {sorted(bad)} "
                "(not declared in packages/core/pyproject.toml)"
            )
    return offenders


def test_core_never_imports_from_adapters() -> None:
    offenders = _offenders_in(CORE_SRC)
    assert not offenders, (
        "packages/core may only import the stdlib, pigrocrm, and dependencies declared "
        "in packages/core/pyproject.toml:\n  " + "\n  ".join(offenders)
    )


def test_core_source_directory_exists() -> None:
    assert CORE_SRC.is_dir(), f"expected core sources at {CORE_SRC}"


# --- The guard proven to actually catch what it claims to, not just written. ---


def test_guard_catches_static_adapter_import(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("import pigrocrm_api\n", encoding="utf-8")
    offenders = _offenders_in(tmp_path)
    assert offenders and "pigrocrm_api" in offenders[0]


def test_guard_catches_dynamic_import_with_literal_string(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text(
        'import importlib\nimportlib.import_module("fastapi")\n', encoding="utf-8"
    )
    offenders = _offenders_in(tmp_path)
    assert offenders and "fastapi" in offenders[0]


def test_guard_catches_dunder_import_with_literal_string(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text('__import__("mcp")\n', encoding="utf-8")
    offenders = _offenders_in(tmp_path)
    assert offenders and "mcp" in offenders[0]


def test_guard_allows_stdlib_pigrocrm_and_declared_dependencies(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text(
        "import json\nimport sqlalchemy\nimport uuid_utils\nfrom pigrocrm.core import qualcosa\n",
        encoding="utf-8",
    )
    assert _offenders_in(tmp_path) == []


def test_guard_known_limitation_cannot_catch_dynamic_import_via_variable(
    tmp_path: Path,
) -> None:
    """Documents the accepted gap: a non-literal argument can't be resolved statically."""
    (tmp_path / "sneaky.py").write_text(
        'import importlib\n_name = "fastapi"\nimportlib.import_module(_name)\n',
        encoding="utf-8",
    )
    assert _offenders_in(tmp_path) == []


# --- slice 6's MCP ban, made mechanical --------------------------------------------
#
# Spec §11.1: for every public method of `DashboardService`, `SearchService` and
# `AutomationConfigService` either an MCP tool calls it, or it appears in a declared
# exclusion list -- and for these three services the list must be **exactly**
# `update_automation_config`. Adding a tool for it breaks the build; removing it from the
# list without adding a tool breaks it too.
#
# Two of the three services do not exist until sub-plan 6B. The list is asserted as a
# constant regardless -- it is the *declaration* the spec fixes -- while the coverage half
# inspects only the classes actually importable. Task B15 adds `DashboardService` and
# `AutomationConfigService` to `_audited_services_slice6()` and changes not one character
# of the list.
#
# Slice 4's own `MCP_EXCLUDED`/`_audited_services()` are not in this file yet: that clause
# belongs to task 4A-13, which is not merged. The four shared helpers below are the ones
# slice 4's plan specifies, verbatim, so that task will find them present and add only its
# own list and tests. Neither ordering duplicates a helper.
#
# This is deliberately *not* a second copy of `apps/mcp/tests/test_mcp_surface_coverage.py`,
# which resolves each call's receiver through an AST walk and is strictly stronger. That
# file answers "is every service method reachable"; this one answers the narrower question
# §11.1 asks by name, in the place slice 4 put it -- so a reader looking for the ban finds
# it where the spec says it lives, and so task B15 has the interface its brief names. Where
# the two ever disagree, the AST walk is right and this clause is the one to fix.

MCP_TOOLS_DIR = CORE_ROOT.parents[1] / "apps" / "mcp" / "src" / "pigrocrm_mcp"

MCP_EXCLUDED_SLICE6: tuple[str, ...] = ("update_automation_config",)


def _audited_services_slice6() -> list[type]:
    """`SearchService` and -- from sub-plan 6B -- `DashboardService` and
    `AutomationConfigService`, exactly the three classes §11.1 names. Resolved by import
    rather than by hard-coded objects, so this file does not fail to collect before 6B
    exists."""
    found: list[type] = []
    for module_path, class_name in (
        ("pigrocrm.core.search.service", "SearchService"),
        ("pigrocrm.core.dashboard.service", "DashboardService"),
        ("pigrocrm.core.automations.config_service", "AutomationConfigService"),
    ):
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            continue
        found.append(getattr(module, class_name))
    return found


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_") and member.__qualname__.startswith(cls.__name__ + ".")
    }


def _tool_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in (MCP_TOOLS_DIR / "tools").rglob("*.py")
    )


def test_the_slice6_exclusion_list_is_exactly_one_name() -> None:
    assert MCP_EXCLUDED_SLICE6 == ("update_automation_config",)


def test_no_mcp_tool_reaches_update_automation_config() -> None:
    """Matched on the call site, not on the tool's own name: a tool called `tidy_settings`
    that happened to call `.update_automation_config(` is exactly how this ban would
    otherwise be lost.

    The ban is imposed by not registering a tool rather than by an authorisation check,
    because residuo R10 leaves a PAT inheriting its owner's full role -- an admin token
    would pass any check we wrote.
    """
    offenders = [name for name in MCP_EXCLUDED_SLICE6 if f".{name}(" in _tool_source()]
    assert not offenders, f"these methods must not be reachable from any MCP tool: {offenders}"


def test_the_ban_would_catch_the_call_it_bans() -> None:
    """Until sub-plan 6B writes `AutomationConfigService`, the test above passes because the
    method does not exist -- which is indistinguishable from passing because the ban works.

    So the matcher is run once against a source that *does* contain the call. A ban whose
    instrument has never been seen to fire is a ban nobody has tested.
    """
    sneaky = "def tidy_settings():\n    return service.update_automation_config(data, actor)\n"
    offenders = [name for name in MCP_EXCLUDED_SLICE6 if f".{name}(" in sneaky]
    assert offenders == ["update_automation_config"]


def test_every_other_public_method_of_a_slice6_service_has_a_tool() -> None:
    source = _tool_source()
    audited = _audited_services_slice6()
    assert audited, "expected at least SearchService to be importable"
    missing: list[str] = []
    for cls in audited:
        for name in sorted(_public_methods(cls)):
            if name in MCP_EXCLUDED_SLICE6:
                continue
            if f".{name}(" not in source:
                missing.append(f"{cls.__name__}.{name}")
    assert not missing, (
        "every public method of an audited slice-6 service must either be reachable from "
        f"an MCP tool or be the one declared exclusion: {missing}"
    )


def test_the_slice6_audit_actually_inspects_something() -> None:
    """The coverage test above is green over an empty method set, and an empty method set is
    what a mistyped module path produces: `importlib` raises `ModuleNotFoundError`, the loop
    skips it, and nothing says so. `SearchService.search_everything` is named here because
    it is the one method of the three services that exists today."""
    methods = {
        (cls.__name__, name) for cls in _audited_services_slice6() for name in _public_methods(cls)
    }
    assert ("SearchService", "search_everything") in methods, methods


def test_the_dashboard_arithmetic_ban_has_its_own_guard() -> None:
    """Spec §3's two AST clauses live in `test_dashboard_no_arithmetic.py`. Named here
    because this file is where somebody looks for the project's architectural rules, and a
    rule enforced in a file nobody opens is a rule deleted in the next refactor.

    The exemption list is asserted from here as well as from there, and deliberately so: the
    file that holds a rule is the file a contributor edits when the rule is in their way, so
    the assertion that keeps it empty is worth having somewhere they are not editing.
    """
    guard = CORE_ROOT / "tests" / "test_dashboard_no_arithmetic.py"
    assert guard.exists(), "the dashboard arithmetic guard is missing"
    source = guard.read_text(encoding="utf-8")
    assert "BINOP_EXEMPT: frozenset[str] = frozenset()" in source, (
        "the BinOp exemption list must stay empty (spec §3)"
    )


def test_the_calendar_day_ban_has_its_own_guard() -> None:
    """The other rule this slice mechanised, and the one whose home moved.

    Task B1's brief said the ban on `date.today()`/`datetime.now(...).date()` would be
    enforced by "Task B9's AST test"; B9's brief turned out to be about the dashboard's
    arithmetic instead, so B1 put the scan in `test_clock.py` -- over all three source
    trees, with `db/clock.py` as its only exemption. It is named here so that the pointer
    survives the briefs, and asserted to exist in exactly one place: two scans for one rule
    is how the two come to disagree.
    """
    clock_guard = CORE_ROOT / "tests" / "test_clock.py"
    assert clock_guard.exists()
    assert "def test_no_module_in_src_reads_a_calendar_day_from_the_process_clock" in (
        clock_guard.read_text(encoding="utf-8")
    )
    # The scanner's *definition*, not a mention of it: this test names it in its own
    # assertion, and a file that points at a rule is not a second copy of the rule. The
    # needle is assembled rather than written out for the same reason `tools/automations.py`
    # cannot spell the call it bans -- a literal here would match this very line.
    needle = "def " + "_is_process_clock_day"
    others = [
        path
        for path in sorted((CORE_ROOT / "tests").glob("test_*.py"))
        if path != clock_guard and needle in path.read_text(encoding="utf-8")
    ]
    assert not others, f"the calendar-day scan must live in test_clock.py alone: {others}"


def test_the_in_transaction_convention_has_its_own_guard() -> None:
    """Slice 6 §9.3's rule is enforced in `test_in_transaction_callers.py`, which walks
    the AST of every call site in three packages. Named here because this file is where
    somebody looks for the project's architectural rules, and a rule enforced in a file
    nobody opens is a rule that gets deleted in a refactor."""
    guard = CORE_ROOT / "tests" / "test_in_transaction_callers.py"
    assert guard.exists(), "the *_in_transaction caller guard is missing"
    assert "core/automations/" in guard.read_text(encoding="utf-8")


def _interpolated_sql(source: str) -> list[int]:
    """The line of every `text(...)` or `.execute(...)` whose first argument is an f-string
    with a substitution, a `%` format or a `+` concatenation: SQL built from a value."""
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in {"text", "execute", "exec_driver_sql"}:
            continue
        first = node.args[0]
        built = (
            isinstance(first, ast.JoinedStr)
            and any(isinstance(part, ast.FormattedValue) for part in first.values)
        ) or (isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add | ast.Mod))
        if built:
            found.append(node.lineno)
    return found


def test_no_sql_is_built_from_a_value_outside_the_sidecar_module() -> None:
    """The row-level policies read a setting any session may write (`set_config`), so the
    scope holds only while no statement is assembled from input (B-10). Today's exceptions
    are the identifiers `db/sidecar.py` quotes for `CREATE/DROP DATABASE`, and the
    constant-named sequence of the proforma numbers (`invoices/repository.py`); a new one
    is a decision to make here, not a line to add quietly."""
    allowed = {"db/sidecar.py", "invoices/repository.py"}
    offenders = []
    for path in CORE_SRC.rglob("*.py"):
        relative = path.relative_to(CORE_SRC).as_posix()
        if relative in allowed:
            continue
        lines = _interpolated_sql(path.read_text(encoding="utf-8"))
        offenders.extend(f"{relative}:{line}" for line in lines)
    assert not offenders, "SQL built from a value:\n  " + "\n  ".join(offenders)


def test_the_scan_for_built_sql_catches_the_shapes_it_bans() -> None:
    assert _interpolated_sql('conn.execute(text(f"select {x}"))') == [1]
    assert _interpolated_sql('session.execute("select " + x)') == [1]
    assert _interpolated_sql('session.execute(text("select 1"))') == []
    assert _interpolated_sql('session.execute(text(f"select 1"))') == []
