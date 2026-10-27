"""What `tools/list` costs, and the one place it is paid down.

An agent reads the whole tool list before its first call. Pydantic's JSON schema spends a
fifth of it on two things nobody reads:

  * `title`, which repeats the property name in Title Case (`"ragione_sociale"` becomes
    `"Ragione Sociale"`) and the tool name again on the argument and output wrappers
    (`"get_customerArguments"`, `"get_customerDictOutput"`);
  * `"default": null` on every optional property, which `required` already says: a property
    that is not required may be left out;
  * an `outputSchema` of `{"type": "object", "additionalProperties": true}`, which 120 tools
    advertise and which says only that the answer is an object. A tool's result still carries
    its `structuredContent`; what is dropped is the empty promise about its shape, and the
    schemas that do say something (a map of strings, a `result` array) stay.

`compact_schema` removes the first two from the schemas the SDK derived and from nothing else: it
follows only the keywords that hold a schema, so a *property called* `title` or `default`, or
an `enum` value that happens to be a dict, are left alone. `trim_tool_list` runs it once,
after every tool is registered, on what the listing advertises; a tool is still validated
against the argument model the SDK built from its signature, which this does not touch.
"""

from typing import Any

from mcp.server import MCPServer

# Keywords whose value is one schema, a list of schemas, or a mapping name -> schema.
_SCHEMA = ("items", "additionalProperties", "not", "contains", "if", "then", "else")
_SCHEMAS = ("anyOf", "oneOf", "allOf", "prefixItems")
_NAMED = ("properties", "patternProperties", "$defs", "definitions")


def compact_schema(schema: Any) -> Any:
    """`schema` without its `title` keywords and its `"default": null`, wherever a schema
    can carry them."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if (key == "title" and isinstance(value, str)) or (key == "default" and value is None):
            continue
        if key in _SCHEMA:
            out[key] = compact_schema(value)
        elif key in _SCHEMAS and isinstance(value, list):
            out[key] = [compact_schema(sub) for sub in value]
        elif key in _NAMED and isinstance(value, dict):
            out[key] = {name: compact_schema(sub) for name, sub in value.items()}
        else:
            out[key] = value
    return out


ANY_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": True}


def trim_tool_list(mcp: MCPServer) -> None:
    """Compact what the listing advertises for every registered tool.

    Only the advertised schemas change. The argument model each tool is validated against
    and the output model its result is built from (`tool.fn_metadata`) are the SDK's own.
    """
    for tool in mcp._tool_manager.list_tools():
        tool.parameters = compact_schema(tool.parameters)
        output = compact_schema(tool.fn_metadata.output_schema)
        # `output_schema` is a cached property of the tool and what `list_tools` reads:
        # setting it replaces whatever it computed before.
        tool.__dict__["output_schema"] = None if output == ANY_OBJECT else output
