"""Walk the parsed tree, escaping each value for the context its node recorded.

The output is *compiled Markdown*, not a PDF: Pandoc and Typst are somebody else's
job (`pigrocrm.core.render.pdf`). Keeping this module free of I/O is
what lets the adversarial escaping cases be tested as pure string comparisons, with
no container, no subprocess and no PDF to parse.

Two ways to get this specific module wrong that no test of `escaping.py` or
`parser.py` alone can catch, because both of those modules are correct on their own
terms and stay that way even if this one misuses them:

1. Applying the wrong escaper, or applying one twice. `VariableNode.context` is
   decided once, by the parser, from which segment the placeholder was found in --
   this module never re-derives or second-guesses it. Escaping is applied exactly
   once, to the raw formatted value, at `_render_variable`; nothing downstream of
   that call ever touches the string again with an escaper.
2. Escaping at the wrong moment: the *value*, never the assembled output. `#each`
   renders its body once per item and lets `_render_variable` escape each value as
   it lands in its own node, rather than building a row's text first and escaping
   the concatenation afterwards -- the latter would escape literal template markup
   (the author's own `{{`-free prose around the placeholder) right along with the
   customer's data, which is a different, wrong program.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, assert_never

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.templates.ast import EachNode, IfNode, Node, TextNode, VariableNode
from pigrocrm.core.templates.escaping import RenderContext, escape_for
from pigrocrm.core.templates.parser import TYPST_LINE_MARKER_PREFIX, parse_template

__all__ = [
    "TYPST_LINE_MARKER_PREFIX",
    "DeclaredVariable",
    "format_value",
    "render_template",
]

# The generic "template body" field name, unused: every
# error this module raises now names the specific variable or dotted path at
# fault as `field` instead (`variable.nome`, or `".".join(path)`) -- naming
# "which variable" via the same structured detail every caller already knows to
# read, not only recoverable by parsing the free-text `reason`. Kept as ENTITY
# only; there is no more FIELD constant to keep alongside it.
ENTITY = "template"


@dataclass(frozen=True)
class DeclaredVariable:
    """One entry of a template's `declared_variables`.

    Declared, not deduced (spec 4.3): the compilation form shows label, type and
    whether it is required, and the render fails with a precise error if a required
    one is missing -- instead of producing a PDF with a hole in it.

    `type` is one of the nine `FieldType` values in fields/types.py, so the frontend
    can render it with the existing `DynamicFieldRenderer` and nothing new has to
    learn a tenth type.
    """

    name: str
    label: str
    type: str
    required: bool


def _is_blank(value: Any) -> bool:
    """Used only to decide whether a *declared* variable's value counts as
    "supplied" (`_check_declared` below) -- not to decide `#if` truthiness, which
    is plain Python truthiness a few lines down in `_render_nodes` and must stay
    that way: `False` and `0` are real answers to a required field (a checkbox the
    user actually unchecked, a discount actually set to zero), so they satisfy a
    required declaration, but they are exactly what an author writing
    `{{#if sconto}}` means by "nothing to show" and must still take the else
    branch. One boolean cannot serve both questions; this function only ever
    answers the first. `test_false_and_zero_satisfy_a_required_variable` pins both
    halves of that distinction at once.

    The container check differs from `fields.validator.is_blank` by one type: this
    also treats an empty *dict* as blank, which that function does not need to,
    since a custom field's coerced value is never a raw dict. `#if` needing `{}` to
    read as "nothing here" (`test_if_treats_every_empty_shape_as_false`) is a
    property of plain truthiness, not of this function, but a required declared
    variable whose value happens to be `{}` should not satisfy the requirement
    either, so the container check is widened here rather than narrowed to match.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


def format_value(value: Any) -> str:
    """A value as text, before escaping. Meant for a *scalar*: a `dict`, `list` or
    `tuple` still falls through to the final `str(value)` here (this function's
    own contract does not promise otherwise), but `_render_variable` -- this
    function's only caller -- refuses one with a precise error before ever
    reaching this point, precisely to stop a container's own
    `repr` (internal keys included) from reaching a rendered document.

    `Decimal` is formatted with `str`, never `float`: a binary float cannot represent
    1234.56 exactly, and the drift is a bug the moment it reaches an offer. The same
    rule the `Numeric(12, 2)` columns exist for. Checked before the general
    fallback, and before any numeric-type check that does not exist here: `bool` is
    a subtype of `int` in Python, so it must be checked first or `True`/`False`
    would fall through to `str(value)` and print as "True"/"False" instead of the
    Italian a template author actually wrote the document for.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Si" if value else "No"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _resolve(path: tuple[str, ...], scopes: list[dict[str, Any]]) -> tuple[bool, Any]:
    """Walk `path` against the innermost scope that has its first segment.

    Returns `(found, value)` rather than raising, because `#if` and `#each` treat a
    missing path as "nothing here" while a bare variable treats it as an error, and
    only the caller knows which of the two it is. Once a scope is chosen because it
    has `path[0]`, the walk commits to that scope for the rest of `path` -- it does
    not fall further outward if a later segment is missing there, so `{{a.b}}` with
    an inner scope's `a` lacking `b` reports "not found", it does not go looking for
    a *different* `a` in an outer scope.
    """
    for scope in reversed(scopes):
        if path[0] not in scope:
            continue
        current: Any = scope[path[0]]
        for part in path[1:]:
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
        return True, current
    return False, None


def _first_root_line_referencing(nodes: tuple[Node, ...], name: str) -> int | None:
    """The template line of the first node -- a bare `{{nome...}}`, or the path an
    `#if`/`#each` itself names -- whose top-level path segment is `nome`, at the
    *root* level only. Mirrors `parser.declared_paths`'s own root/loop_relative
    split exactly (same recursion, same `inside_each` gate): a `{{nome}}` inside
    an `#each` body is relative to that loop's current item, a different
    name-space from a root-level declared variable, even when the two happen to
    share a spelling, and must not be mistaken for a match.

    Used only to enrich a missing-required-variable error with a line, on a
    genuinely best-effort basis: `None` when `nome` is declared but never
    referenced anywhere at the root level, which is a legitimate shape --
    declared, not deduced (spec 4.3) -- not a bug to paper over with a fabricated
    line number.
    """

    def walk(items: tuple[Node, ...], *, inside_each: bool) -> int | None:
        for node in items:
            match node:
                case VariableNode(path=path, line=line):
                    if not inside_each and path[0] == name:
                        return line
                case IfNode(path=path, line=line, then=then, otherwise=otherwise):
                    if not inside_each and path[0] == name:
                        return line
                    found = walk(then, inside_each=inside_each)
                    if found is not None:
                        return found
                    found = walk(otherwise, inside_each=inside_each)
                    if found is not None:
                        return found
                case EachNode(path=path, line=line, body=body):
                    if not inside_each and path[0] == name:
                        return line
                    found = walk(body, inside_each=True)
                    if found is not None:
                        return found
                case TextNode():
                    pass
        return None

    return walk(nodes, inside_each=False)


def _check_declared(
    nodes: tuple[Node, ...], declared: tuple[DeclaredVariable, ...], values: dict[str, Any]
) -> None:
    """`field` is the variable's own name and `reason` names
    the line of its first root-level reference, when it has one -- both were
    previously missing (`field` was the constant "corpo_markdown", the generic
    template-body field; `reason` had no line at all, because this check used to
    run *before* `parse_template`, when no tree existed yet to search). `entity`
    stays the constant "template": this function has no notion of *which*
    template it is -- `render_template` takes raw source text, not a name or an
    id -- so there is genuinely nothing more specific to put there at this layer;
    a caller that knows which template it is dealing with is the one positioned
    to enrich a caught `ValidationFailed` with that fact, not this pure function.
    """
    for variable in declared:
        if variable.required and _is_blank(values.get(variable.name)):
            reason = f"variabile obbligatoria mancante: {variable.label}"
            line = _first_root_line_referencing(nodes, variable.name)
            if line is not None:
                reason = f"riga {line}: {reason}"
            raise ValidationFailed(
                ENTITY,
                variable.name,
                reason,
                expected="un valore non vuoto",
            )


def render_template(
    source: str,
    values: dict[str, Any],
    declared: tuple[DeclaredVariable, ...] = (),
    *,
    context: RenderContext | None = None,
) -> str:
    """Compile `source` with `values`. Raises `ValidationFailed` naming the template
    line for anything the template needs that `values` does not actually supply --
    a required declared variable, an unresolved `{{path}}`, an `#each` pointed at
    something that is not a list -- rather than rendering a PDF with a hole in it.

    `context` overrides the escaping context the *parser* derived for every
    placeholder, and exists for one reason: not every template compiles to Markdown
    any more. Slice 5's reminder body is a `text/plain` MIME part read by a person,
    where the segment-derived "markdown" context's backslashes are not protection but
    damage -- see `escaping.escape_plain`. It is a whole-document decision (which
    language is this output written in?), never a per-placeholder one (where inside
    that language does this value land?), which is why it belongs to the caller here
    and the per-node context still belongs to the parser: `_render_variable` is still
    forbidden to re-derive or second-guess a node's own context, and with `context`
    left `None` -- every existing caller -- nothing about this module changes.
    """
    nodes = parse_template(source)
    _check_declared(nodes, declared, values)
    # An optional declared variable defaults to `None`, so `{{note}}` for a note
    # nobody filled in renders as empty text rather than as an unresolved-variable
    # error -- the error is reserved for a path the template needs that no
    # supplied *or* declared-optional value covers. `values` is layered on top and
    # always wins, so an actual (even falsy) value is never masked by this default.
    #
    # This used to seed `""` unconditionally, regardless of
    # `type`. For a scalar type that is merely cosmetic -- `format_value("")` and
    # `format_value(None)` are both `""`, and `bool("")`/`bool(None)` are both
    # falsy, so `{{note}}` and `{{#if note}}` behave identically either way -- but
    # for `multiselect` it was a real bug: `{{#each righe}}` resolved `lines` to
    # the string `""`, which is not a list, and raised "'righe' non e' una lista"
    # -- on the exact template that renders as nothing at all if `lines` is never
    # declared. Declaring an optional variable must never make a template *more*
    # likely to fail than leaving it undeclared.
    #
    # `None` is the fix, for every type, not a per-type table of empty values
    # (`[]` for multiselect, `False` for checkbox, ...): a hardcoded table is
    # exactly one more list this project has already paid once for trusting to
    # stay complete (see escaping.py's own docstring) -- and it is unnecessary
    # here, because every consumer of a resolved value already interprets `None`
    # correctly for whatever shape it is actually used as. `_render_each` already
    # treats `value is None` as "no rows" (the same branch a genuinely absent
    # path takes); plain Python truthiness already treats `None` as falsy for
    # `#if`, the same as `False`, `0` or `[]`; and `format_value(None)` is `""`
    # for a bare substitution. `type` therefore still is not consulted here --
    # matching it correctly does not require branching on it.
    optional_defaults: dict[str, Any] = {v.name: None for v in declared if not v.required}
    scopes: list[dict[str, Any]] = [{**optional_defaults, **values}]
    out: list[str] = []
    _render_nodes(nodes, scopes, out, context)
    return "".join(out)


def _render_nodes(
    nodes: tuple[Node, ...],
    scopes: list[dict[str, Any]],
    out: list[str],
    override: RenderContext | None,
) -> None:
    for node in nodes:
        match node:
            case TextNode(text=text):
                # Never escaped, and never touched here beyond appending it: it is
                # the template author's own markup, already carrying the parser's
                # `// pigrocrm:line=` marker wherever one applies (see parser.py).
                out.append(text)
            case VariableNode(path=path, context=context, line=line):
                out.append(_render_variable(path, override or context, line, scopes))
            case IfNode(path=path, then=then, otherwise=otherwise):
                found, value = _resolve(path, scopes)
                # Plain Python truthiness, not `_is_blank`: `False` and `0` must
                # take the else branch here even though `_is_blank` (correctly, for
                # its own job) does not consider either one blank -- see
                # `_is_blank`'s own docstring. A missing path (`found` is `False`)
                # also takes the else branch, never an error: a conditional's whole
                # job is to ask whether something is there.
                _render_nodes(then if found and value else otherwise, scopes, out, override)
            case EachNode(path=path, line=line, body=body):
                _render_each(path, line, body, scopes, out, override)
            case _:
                # A future fifth `Node` variant silently rendering as
                # nothing here is the same class of failure as any of the errors
                # above -- just later, and quieter. `assert_never` makes it
                # impossible to add one to the `ast.py` union without this match
                # failing mypy first (`node`'s narrowed type here is only ever
                # `Never` while the four cases above are exhaustive), and, should
                # that check ever be bypassed, raises loudly at run time instead
                # of dropping the node's content with no trace.
                assert_never(node)


def _render_variable(
    path: tuple[str, ...], context: RenderContext, line: int, scopes: list[dict[str, Any]]
) -> str:
    # `field` is the dotted path itself, not the constant
    # "corpo_markdown" -- naming *which* variable failed via the same structured
    # detail `_check_declared`'s error uses (`variable.nome` there), rather than
    # leaving that fact recoverable only by parsing the free-text `reason`.
    dotted = ".".join(path)
    found, value = _resolve(path, scopes)
    if not found:
        raise ValidationFailed(
            ENTITY,
            dotted,
            f"riga {line}: variabile '{dotted}' non risolta",
            expected="una variabile dichiarata dal template",
        )
    if isinstance(value, (dict, list, tuple)):
        # A dict or a list in *value* position -- a bare `{{cliente}}`
        # where the author meant `{{cliente.nome}}` -- used to fall through to
        # `format_value`'s final `str(value)` and print the value's own Python
        # `repr` into the document, internal keys included: confirmed live,
        # `{{cliente}}` with `{"nome": "Rossi", "note_interne": "cattivo
        # pagatore"}` put the customer's own internal note into a document sent
        # *to* that customer, with no error at all. This is a template author's
        # mistake -- the fix is the same shape as every other one in this
        # module: a precise error naming the variable and the line, never a
        # best-effort stringification of something that was never meant to be
        # displayed whole. `#each` over the same shape is unaffected: it is
        # `_render_each`'s job, a separate, legitimate case for exactly this
        # type, checked before any of this ever runs.
        raise ValidationFailed(
            ENTITY,
            dotted,
            f"riga {line}: '{dotted}' e' un oggetto o una lista, non un valore singolo",
            expected=f"un percorso piu' specifico (es. {dotted}.campo) o un blocco #each",
        )
    # `context` came from the node the parser built for this exact placeholder --
    # never re-derived from the path, the value, or anything else this function
    # could get wrong. Escaping the *value* here, once, before it is ever
    # concatenated with any surrounding text, is what keeps a `#each` body's
    # repeated rendering from double-escaping or escaping the author's own markup.
    try:
        return escape_for(context, format_value(value))
    except ValueError as exc:
        raise ValidationFailed(
            ENTITY, dotted, f"riga {line}: {exc}", expected="testo senza caratteri di controllo"
        ) from exc


def _render_each(
    path: tuple[str, ...],
    line: int,
    body: tuple[Node, ...],
    scopes: list[dict[str, Any]],
    out: list[str],
    override: RenderContext | None,
) -> None:
    found, value = _resolve(path, scopes)
    if not found or value is None:
        # Missing entirely, or explicitly null: both read as "no rows yet", a
        # common shape for an optional relation that has not been filled in, and
        # render as nothing rather than an error.
        return
    if not isinstance(value, (list, tuple)):
        # Anything else -- a string, a number, a dict -- is not "no rows", it is
        # the wrong shape entirely, and worth surfacing rather than silently
        # iterating zero (or, for a string, character-by-character) times.
        # `field` is the dotted path, not the generic "corpo_markdown" constant --
        # same as `_render_variable`'s two errors above.
        dotted = ".".join(path)
        raise ValidationFailed(
            ENTITY,
            dotted,
            f"riga {line}: '{dotted}' non e' una lista",
            expected="una lista di elementi",
        )
    for item in value:
        # `this` is the item itself; a dict item's own keys also become a scope, so
        # `{{nome}}` inside `{{#each righe}}` reads the row's `nome` and falls back
        # to the outer scope when the row has none (`_resolve` walks scopes
        # outward). Pushed and popped per item, not left on the stack, so a
        # `{{this}}` two loops removed cannot see an inner loop's own item.
        frame: dict[str, Any] = {"this": item}
        if isinstance(item, dict):
            frame.update(item)
        scopes.append(frame)
        try:
            _render_nodes(body, scopes, out, override)
        finally:
            scopes.pop()
