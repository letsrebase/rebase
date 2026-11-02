"""The shape of the OpenAPI document the hub web client is generated from."""

from typing import Any

from rebase_api.main import create_app


def _refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                found.add(value.rsplit("/", 1)[1])
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for item in node:
            _refs(item, found)


def _response_schemas(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every schema a 2xx response of any route reaches, directly or through a field."""
    schemas = spec["components"]["schemas"]
    reached: set[str] = set()
    for operations in spec["paths"].values():
        for operation in operations.values():
            for code, response in operation.get("responses", {}).items():
                if code.startswith("2"):
                    _refs(response, reached)
    pending = list(reached)
    while pending:
        name = pending.pop()
        inner: set[str] = set()
        _refs(schemas[name], inner)
        pending.extend(inner - reached)
        reached |= inner
    return {name: schemas[name] for name in reached}


def test_every_property_of_a_response_schema_is_required() -> None:
    """The services build every response model whole, so a field with a default is still
    in the JSON. The generated client must not type it as possibly missing."""
    offenders = sorted(
        f"{name}.{prop}"
        for name, schema in _response_schemas(create_app().openapi()).items()
        for prop in set(schema.get("properties", {})) - set(schema.get("required", []))
    )

    assert offenders == [], f"optional in a response schema: {offenders}"
