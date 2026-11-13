"""`SafeStr`: a string that refuses a NUL byte before it reaches Postgres.

`"\\x00"` is valid JSON and an ordinary Python `str`, so it sails through pydantic and
reaches `flush()`, where Postgres refuses a NUL byte in `text`/`varchar` and the driver
raises a raw, uncaught exception. Rejected rather than stripped: the caller is choosing
a value for their own record and can be told to fix it, and silently deleting one
invisible byte is how a character disappears with nobody noticing. The same type, with
the same reasoning, that PigroCRM's `validation.py` applies to every user-supplied string.
"""

from typing import Annotated, Any, get_args

from pydantic import BeforeValidator, ValidationInfo
from pydantic_core import ErrorDetails
from pydantic_core.core_schema import ErrorType


def _reject_nul(value: Any, info: ValidationInfo) -> Any:
    if isinstance(value, str) and "\x00" in value:
        field = info.field_name or "valore"
        raise ValueError(f"{field}: il testo contiene un carattere nullo (\\x00), non ammesso")
    return value


SafeStr = Annotated[str, BeforeValidator(_reject_nul)]


# Pydantic's own error types, whose messages are English; any other type is one the hub
# raised itself (`PydanticCustomError`), with an Italian message.
PYDANTIC_ERRORS = frozenset(get_args(ErrorType))
# The word after a field's name for a value Pydantic's own checks refused (`field_reason`).
NOT_VALID = "non valido"


def field_reason(error: ErrorDetails) -> str:
    """What an admin reads after a field's name for one of a model's errors: «manca» for
    an empty field; the hub's own sentence when one of its validators refused the value,
    already Italian, without the field's name when the sentence starts with it
    (`SafeStr`'s refusal of a NUL byte does), so the name is not said twice; «non
    valido» for Pydantic's own checks, whose words are English and repeat the value.
    The MCP server's tools name a refusal this way (`rebase_mcp.tools_common.field_refusal`)."""
    if error["type"] == "missing" or error["input"] is None:
        return "manca"
    if error["type"] == "value_error":
        reason = str(error.get("ctx", {}).get("error", NOT_VALID))
        return reason.removeprefix(f"{error['loc'][-1]}: ") if error["loc"] else reason
    return error["msg"] if error["type"] not in PYDANTIC_ERRORS else NOT_VALID
