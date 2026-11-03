"""Every `{name}` in a route's path is a declared path parameter of each operation.

A handler that drops its path argument (an unused `contract_id`, say) still serves the
route, but the generated client loses the parameter and every caller stops compiling.
"""

import re

from pigrocrm_api.main import create_app


def test_every_path_variable_is_declared_by_each_operation() -> None:
    undeclared = []
    for path, operations in create_app().openapi()["paths"].items():
        wanted = set(re.findall(r"{(\w+)}", path))
        for method, operation in operations.items():
            declared = {p["name"] for p in operation.get("parameters", []) if p["in"] == "path"}
            if wanted - declared:
                undeclared.append((method.upper(), path, sorted(wanted - declared)))
    assert not undeclared, undeclared
