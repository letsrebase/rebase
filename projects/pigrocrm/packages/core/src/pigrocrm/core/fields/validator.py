import math
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from urllib.parse import urlparse

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.fields.types import FieldSpec

TRUE_STRINGS = frozenset({"1", "true", "True", "si", "sì", "yes"})
FALSE_STRINGS = frozenset({"0", "false", "False", "no"})
ALLOWED_URL_SCHEMES = {"http", "https"}
# text/textarea/url all funnel through _clean_text: only these become text, so a
# dict or a list can never be silently stringified into "{'a': 1}"-shaped garbage.
TEXT_SCALAR_TYPES = (str, int, float, bool, Decimal)
# Formatting a container's contents is exactly what can exhaust the stack on a
# deeply nested input. Naming its type instead never touches that structure, so
# _brief/_safe_text cannot recurse into it no matter how deep it goes.
CONTAINER_TYPES = (list, dict, tuple)
_MAX_VALUE_REPR = 60


def _fail(entity: str, key: str, reason: str, expected: str | None = None) -> NoReturn:
    """`NoReturn` is load-bearing: it tells mypy every call ends the function, so no
    caller needs an unreachable `raise` after it just to satisfy the return type."""
    raise ValidationFailed(entity, key, reason, expected=expected)


def _brief(value: Any) -> str:
    """A bounded representation of the value, for error messages.

    Interpolating the raw value has two defects: a deeply nested input exhausts the
    stack during formatting, before the exception even exists, and a very large value
    produces a huge message that ends up in an HTTP problem document and in an agent's
    context.
    """
    if isinstance(value, CONTAINER_TYPES):
        # Never attempt str() on a container at all: checking the type is O(1) and
        # cannot recurse, whereas formatting its contents is the actual recursion.
        return f"<{type(value).__name__}>"
    try:
        text = str(value)
    except RecursionError:
        # Defence in depth for shapes CONTAINER_TYPES does not name - e.g. a custom
        # object whose __repr__ walks into a deeply nested attribute.
        return f"<{type(value).__name__} troppo annidato>"
    if len(text) > _MAX_VALUE_REPR:
        return text[:_MAX_VALUE_REPR] + "…"
    return text


def _safe_text(value: Any) -> str | None:
    """str(value).strip(), or None if value is a container type whose contents we
    refuse to format - printing them is exactly what could exhaust the stack.
    Used for the actual coerced value, never truncated like `_brief`."""
    if isinstance(value, CONTAINER_TYPES):
        return None
    return str(value).strip()


def _safe_decimal(value: Any) -> Decimal | None:
    text = _safe_text(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _coerce_number(entity: str, spec: FieldSpec, value: Any) -> float:
    if isinstance(value, bool):
        # `float(True)` is 1.0: a checkbox's answer is not a quantity.
        _fail(entity, spec.key, f"'{_brief(value)}' non è un numero", "un numero")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError: JSON has no size limit on integers, so json.loads can hand
        # us an int float() cannot represent - e.g. an integer with hundreds of
        # digits. That is exactly as invalid as a non-numeric string. float() never
        # formats a container's contents to raise this, so value is safe to brief.
        _fail(entity, spec.key, f"'{_brief(value)}' non è un numero", "un numero")
    if not math.isfinite(result):
        # float("1e1000") becomes inf without float() raising anything, so
        # finiteness has to be checked after conversion, not inferred from it.
        # Postgres rejects a bare NaN/Infinity token in JSONB outright.
        _fail(entity, spec.key, f"'{_brief(value)}' non è un numero finito", "un numero finito")
    return result


def _coerce_currency(entity: str, spec: FieldSpec, value: Any) -> str:
    """Money is stored as a fixed-scale string: JSON has no decimal type, and float
    rounding on money is a bug that only shows up on an invoice."""
    amount = _safe_decimal(value)
    if amount is None:
        _fail(entity, spec.key, f"'{_brief(value)}' non è un importo", "un importo numerico")
    if not amount.is_finite():
        # Decimal("NaN").quantize(...) does not raise - it silently propagates NaN -
        # while Decimal("Infinity").quantize(...) does. Checking is_finite() first
        # means both fail the same way instead of NaN slipping through as "NaN".
        _fail(entity, spec.key, f"'{_brief(value)}' non è un importo finito", "un importo finito")
    try:
        return str(amount.quantize(Decimal("0.01")))
    except InvalidOperation:
        # A value with more significant digits than the decimal context allows
        # (e.g. a 400-digit integer) is finite but still cannot be quantized.
        _fail(entity, spec.key, f"'{_brief(value)}' non è un importo", "un importo numerico")


def _coerce_date(entity: str, spec: FieldSpec, value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = _safe_text(value)
    if text is not None:
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
    _fail(entity, spec.key, f"'{_brief(value)}' non è una data valida", "una data ISO (YYYY-MM-DD)")


def _coerce_select(entity: str, spec: FieldSpec, value: Any) -> str:
    text = _safe_text(value)
    if text is not None and text in spec.options:
        return text
    _fail(
        entity,
        spec.key,
        f"'{_brief(value)}' non è tra le opzioni ammesse",
        f"uno tra: {', '.join(spec.options)}",
    )


def _coerce_multiselect(entity: str, spec: FieldSpec, value: Any) -> list[str]:
    if not isinstance(value, list):
        _fail(entity, spec.key, "il valore deve essere una lista", "una lista di valori")
    seen: list[str] = []
    for item in value:
        text = _safe_text(item)
        if text is None or text not in spec.options:
            _fail(
                entity,
                spec.key,
                f"'{_brief(item)}' non è tra le opzioni ammesse",
                f"valori tra: {', '.join(spec.options)}",
            )
        if text not in seen:
            seen.append(text)
    return seen


def _coerce_checkbox(entity: str, spec: FieldSpec, value: Any) -> bool:
    """A boolean, the integers 0 and 1, or one of the words clients are known to send. Not
    any other number: set membership would have taken `1.0` for `True` because they hash
    alike, which is an accident of Python and not a rule of the field."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, str):
        if value in TRUE_STRINGS:
            return True
        if value in FALSE_STRINGS:
            return False
    _fail(entity, spec.key, f"'{_brief(value)}' non è un booleano", "true oppure false")


def _clean_text(entity: str, spec: FieldSpec, value: Any) -> str:
    """Shared by text, textarea and url. Only scalars become text - str() on a dict
    or a list produces silent garbage like "{'a': 1}" with no signal to whoever sent
    it - and a NUL byte is rejected outright rather than stripped, because stripping
    would change the user's data without telling them, and Postgres refuses \\x00 in
    a text column regardless."""
    if not isinstance(value, TEXT_SCALAR_TYPES):
        # value is by definition not one of TEXT_SCALAR_TYPES here - it could be
        # the very container we must not format - so the message uses _brief, not
        # value directly, even though the main path never calls str() on it.
        _fail(entity, spec.key, f"'{_brief(value)}' non è un valore testuale", "un valore testuale")
    text = str(value).strip()
    if "\x00" in text:
        _fail(
            entity,
            spec.key,
            "il testo contiene un carattere nullo",
            "testo senza caratteri di controllo",
        )
    return text


def _coerce_url(entity: str, spec: FieldSpec, value: Any) -> str:
    text = _clean_text(entity, spec, value)
    parsed = urlparse(text)
    # Rejecting javascript: here is what stops a stored-XSS the moment the UI
    # renders a custom field as a link.
    if parsed.scheme not in ALLOWED_URL_SCHEMES or not parsed.netloc:
        _fail(
            entity, spec.key, f"'{_brief(text)}' non è un URL valido", "un URL http:// o https://"
        )
    return text


def coerce_value(entity: str, spec: FieldSpec, value: Any) -> Any:
    match spec.field_type:
        case "text" | "textarea":
            return _clean_text(entity, spec, value)
        case "number":
            return _coerce_number(entity, spec, value)
        case "currency":
            return _coerce_currency(entity, spec, value)
        case "date":
            return _coerce_date(entity, spec, value)
        case "select":
            return _coerce_select(entity, spec, value)
        case "multiselect":
            return _coerce_multiselect(entity, spec, value)
        case "checkbox":
            return _coerce_checkbox(entity, spec, value)
        case "url":
            return _coerce_url(entity, spec, value)
    _fail(entity, spec.key, f"tipo di campo sconosciuto: {spec.field_type}")


def is_blank(value: Any) -> bool:
    """None, a whitespace-only string, or an empty sequence all mean "nothing was
    provided". False and 0 do not: they are legitimate values, not missing ones.

    Public and re-exported: this is the same domain rule the runtime model factory
    (`pigrocrm.core.fields.dynamic`) must apply, in the same order relative to
    `coerce_value`, for the model to agree with `validate_custom_fields` on every
    input -- not a private detail of this module."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple)):
        return len(value) == 0
    return False


def validate_custom_fields(
    entity: str, specs: list[FieldSpec], values: dict[str, Any]
) -> dict[str, Any]:
    """The single authority on custom-field validity.

    Routers, MCP tools and the UI all defer to this. Duplicating any part of it
    elsewhere is how the three interfaces start disagreeing about what is valid.
    """
    by_key = {spec.key: spec for spec in specs}

    for key in values:
        if key not in by_key:
            known = ", ".join(sorted(by_key)) or "nessuno"
            # key comes straight from the caller's payload and becomes this
            # exception's `field`; ValidationFailed.__init__ formats it into
            # `message` in errors.py, a different file a local grep cannot see.
            # _brief bounds it here, before it ever leaves this function.
            _fail(entity, _brief(key), f"campo non definito (campi disponibili: {known})")

    result: dict[str, Any] = {}
    for spec in specs:
        raw = values.get(spec.key)
        if is_blank(raw):
            if spec.required:
                _fail(entity, spec.key, "campo obbligatorio", "un valore non vuoto")
            continue
        result[spec.key] = coerce_value(entity, spec, raw)
    return result
