import shutil
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.templates.renderer import (
    TYPST_LINE_MARKER_PREFIX,
    DeclaredVariable,
    format_value,
    render_template,
)

TYPST_INJECTION = '#import "/etc/passwd"'
MARKDOWN_INJECTION = "**Grassetto** & <script>"

BOTH_CONTEXTS = """Spett.le **{{cliente.ragione_sociale}}**

```{=typst}
#table(
  columns: (0.8fr, 0.2fr),
  [{{cliente.ragione_sociale}}],
  [{{offerta.totale}}],
)
```
"""

# Declared up front, not next to its first use: several sections below need
# `requires_real_compiler` before the file reaches the "Real compiles" section that
# originally defined it, and a decorator has to exist at function-definition time,
# not merely somewhere later in the same module.
_MISSING_TOOLS = [tool for tool in ("pandoc", "typst", "pdftotext") if shutil.which(tool) is None]
requires_real_compiler = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"real Pandoc/Typst round-trip tests need these on PATH: {', '.join(_MISSING_TOOLS)}",
)


def test_a_plain_variable_is_substituted() -> None:
    assert render_template("Ciao {{nome}}!", {"nome": "Ivan"}) == "Ciao Ivan!"


def test_a_dotted_path_walks_nested_dicts() -> None:
    out = render_template("{{cliente.sede.comune}}", {"cliente": {"sede": {"comune": "Milano"}}})
    assert out == "Milano"


# --- Fix round 2: a placeholder that resolves to a dict or a list is a template
# author's mistake -- `{{cliente}}` where `{{cliente.nome}}` was meant -- not a
# value to best-effort stringify. Before this fix, `{{cliente}}` with
# `{"nome": "Rossi", "note_interne": "cattivo pagatore"}` printed the value's own
# Python repr, internal keys included, into a document sent *to* that customer,
# with no error at all -- confirmed live before writing the fix. `#each` over the
# same shape is the separate, legitimate case it already was and is untouched.


def test_a_dict_value_is_a_precise_error_not_a_repr_dump() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template(
            "Cliente: {{cliente}}",
            {"cliente": {"nome": "Rossi", "note_interne": "cattivo pagatore", "id": 42}},
        )
    assert excinfo.value.details["field"] == "cliente"
    assert "riga 1" in excinfo.value.details["reason"]
    # The whole point: the leaked internal note must never appear anywhere in
    # the exception either, the same as it must never reach the rendered text.
    assert "cattivo pagatore" not in str(excinfo.value.details)


def test_a_list_value_is_a_precise_error_not_a_repr_dump() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("riga1\nriga2 {{righe}}", {"righe": [1, 2, 3]})
    assert excinfo.value.details["field"] == "righe"
    assert "riga 2" in excinfo.value.details["reason"]


def test_a_dict_value_inside_an_each_body_is_also_a_precise_error() -> None:
    # Not just the root case: a row's own property that happens to be a nested
    # container is the identical mistake, one level in.
    with pytest.raises(ValidationFailed) as excinfo:
        render_template(
            "{{#each righe}}{{dettagli}}{{/each}}",
            {"righe": [{"dettagli": {"segreto": "no"}}]},
        )
    assert excinfo.value.details["field"] == "dettagli"


def test_each_over_a_list_is_unaffected_by_the_dict_and_list_value_guard() -> None:
    # The separate, legitimate case: #each is exactly the right construct for a
    # list, and must keep working exactly as before.
    out = render_template("{{#each righe}}{{nome}};{{/each}}", {"righe": [{"nome": "A"}]})
    assert out == "A;"


def test_render_nodes_fails_loudly_on_a_node_type_it_does_not_recognise() -> None:
    # Pins the exhaustiveness guard added alongside the dict/list fix: a node
    # type _render_nodes's own match does not recognise must fail loudly, the
    # same class of bug as a future Node variant silently rendering as nothing
    # -- just later, and quieter. Reaches directly into the private
    # _render_nodes to prove the invariant, since parse_template can never
    # produce a fifth node type through the public API for this to test against
    # -- the same reasoning test_escape_for_verbatim_raises_since_it_should_
    # never_be_called (test_template_escaping.py) uses for its own
    # should-never-happen case.
    import pigrocrm.core.templates.renderer as renderer_module

    class _NotARealNode:
        pass

    with pytest.raises(AssertionError):
        renderer_module._render_nodes((_NotARealNode(),), [{}], [], None)  # type: ignore[arg-type]


def test_typst_injection_is_literal_text_in_the_markdown_context() -> None:
    # Overrides the brief's own expected value, which was written against the
    # pre-Task-1-fix design where "#" was escaped only at the start of a line. The
    # shipped `escape_markdown` (escaping.py) escapes the full ASCII punctuation
    # class unconditionally, "#" included -- see that module's own
    # test_markdown_escapes_hash_unconditionally_not_only_at_line_start and its
    # docstring: a curated, position-dependent rule is exactly the defect a
    # reviewer broke by compiling a real PDF, and this task is told to read that
    # module and use its escaper as given, not second-guess it with a narrower rule
    # of its own. The value still reads back as the identical literal text once
    # Pandoc consumes the escapes -- this assertion is about the intermediate
    # *compiled Markdown* string, proven end-to-end in
    # test_for_real_the_spec_3_3_acceptance_scenario_compiles_clean_with_both_injections
    # below.
    out = render_template("Spett.le {{c}}", {"c": TYPST_INJECTION})
    assert out == r"Spett.le \#import \"\/etc\/passwd\""


def test_typst_injection_is_neutralised_in_the_typst_context() -> None:
    out = render_template("```{=typst}\n#text[{{c}}]\n```\n", {"c": TYPST_INJECTION})
    assert r"\#import" in out
    assert "\n#import" not in out


def test_markdown_injection_is_literal_text_in_the_markdown_context() -> None:
    out = render_template("Spett.le {{c}}", {"c": MARKDOWN_INJECTION})
    assert out == r"Spett.le \*\*Grassetto\*\* \& \<script\>"


def test_markdown_injection_is_neutralised_in_the_typst_context() -> None:
    out = render_template("```{=typst}\n#text[{{c}}]\n```\n", {"c": MARKDOWN_INJECTION})
    assert r"\*\*Grassetto\*\*" in out
    assert r"\<script\>" in out


def test_the_same_value_is_escaped_the_same_way_in_both_contexts() -> None:
    # Overrides the brief's own name and first assertion ("...escaped differently
    # in the two contexts"), for the same reason as
    # test_typst_injection_is_literal_text_in_the_markdown_context above:
    # escape_markdown and escape_typst are, by Task 1's own deliberate design, the
    # identical function body (escaping.py's own docstring: "escape the entire
    # ASCII punctuation class, unconditionally, in both contexts... so there is no
    # character left for a list to omit"). The same value placed in a markdown
    # segment and a typst segment is escaped *identically* now, not "differently"
    # the way an earlier, two-independently-curated-lists design would have. What
    # the spec's own acceptance test actually needs -- the same value is safe
    # wherever it lands -- still holds, and holds more robustly for being uniform
    # rather than context-dependent; see
    # test_the_renderer_calls_escape_for_with_each_nodes_own_context_never_a_guess
    # below for a check that does not depend on the two escapers ever disagreeing.
    out = render_template(
        BOTH_CONTEXTS,
        {
            "cliente": {"ragione_sociale": TYPST_INJECTION},
            "offerta": {"totale": MARKDOWN_INJECTION},
        },
    )
    markdown_part, typst_part = out.split("```{=typst}", 1)
    assert r"\#import \"\/etc\/passwd\"" in markdown_part
    assert r"\#import" in typst_part
    assert r"\*\*Grassetto\*\*" in typst_part


def test_a_typst_block_carries_a_line_marker_naming_its_template_line() -> None:
    out = render_template(
        BOTH_CONTEXTS,
        {"cliente": {"ragione_sociale": "ACME"}, "offerta": {"totale": "100,00"}},
    )
    assert f"{TYPST_LINE_MARKER_PREFIX}4" in out
    # The marker is the first line inside the fence, so Pandoc passes it through and
    # the line arithmetic in render/diagnostics.py holds.
    body = out.split("```{=typst}\n", 1)[1]
    assert body.splitlines()[0] == f"{TYPST_LINE_MARKER_PREFIX}4"


def test_if_renders_the_then_branch_when_truthy() -> None:
    assert render_template("{{#if iva}}con{{else}}senza{{/if}}", {"iva": True}) == "con"


def test_if_renders_the_else_branch_when_falsy() -> None:
    assert render_template("{{#if iva}}con{{else}}senza{{/if}}", {"iva": False}) == "senza"


@pytest.mark.parametrize("falsy", [False, None, "", [], {}, 0])
def test_if_treats_every_empty_shape_as_false(falsy: object) -> None:
    assert render_template("{{#if x}}s{{else}}n{{/if}}", {"x": falsy}) == "n"


def test_if_with_a_missing_path_takes_the_else_branch_rather_than_failing() -> None:
    # A conditional's whole job is to ask whether something is there.
    assert render_template("{{#if x}}s{{else}}n{{/if}}", {}) == "n"


def test_each_iterates_and_exposes_item_properties() -> None:
    out = render_template(
        "{{#each righe}}{{nome}}={{totale}};{{/each}}",
        {"righe": [{"nome": "A", "totale": "1"}, {"nome": "B", "totale": "2"}]},
    )
    assert out == "A=1;B=2;"


def test_each_exposes_this_for_a_list_of_scalars() -> None:
    assert render_template("{{#each r}}[{{this}}]{{/each}}", {"r": ["x", "y"]}) == "[x][y]"


def test_each_over_a_missing_or_empty_path_renders_nothing() -> None:
    assert render_template("a{{#each r}}X{{/each}}b", {}) == "ab"
    assert render_template("a{{#each r}}X{{/each}}b", {"r": []}) == "ab"


def test_each_falls_back_to_the_outer_scope_for_a_path_the_item_lacks() -> None:
    out = render_template(
        "{{#each r}}{{nome}}@{{azienda}};{{/each}}",
        {"azienda": "ACME", "r": [{"nome": "A"}, {"nome": "B"}]},
    )
    assert out == "A@ACME;B@ACME;"


def test_nested_each_scopes_do_not_leak_into_each_other() -> None:
    # Beyond the brief: a nested #each pushes a second frame on top of the first,
    # and the push/pop discipline in _render_each has to keep both loops' own
    # items distinct while still letting the inner one fall back to the outer's
    # scope (not the root's) for a name the inner item lacks -- and to leave no
    # trace of the first outer item's frame behind for the second one to see.
    out = render_template(
        "{{#each ordini}}{{cliente}}:{{#each righe}}{{nome}}({{cliente}}),{{/each}};{{/each}}",
        {
            "ordini": [
                {"cliente": "A", "righe": [{"nome": "x"}, {"nome": "y"}]},
                {"cliente": "B", "righe": [{"nome": "z"}]},
            ]
        },
    )
    assert out == "A:x(A),y(A),;B:z(B),;"


def test_each_over_a_non_list_fails_naming_the_line() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("x\n{{#each r}}X{{/each}}", {"r": "non una lista"})
    assert "riga 2" in excinfo.value.details["reason"]
    assert "lista" in excinfo.value.details["reason"]


def test_an_unresolvable_variable_fails_naming_the_path_and_the_line() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("riga1\nriga2 {{cliente.inesistente}}", {"cliente": {}})
    assert "riga 2" in excinfo.value.details["reason"]
    assert "cliente.inesistente" in excinfo.value.details["reason"]


# --- Fix round 1, item 4: `field` names the specific variable or dotted path at
# fault -- the same structured detail across every error this module raises --
# rather than the generic "body_markdown" constant that used to appear here and
# in the #each "not a list" error, recoverable only by parsing free-text `reason`.


def test_an_unresolvable_variables_field_is_its_own_dotted_path_not_the_generic_body() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("{{cliente.inesistente}}", {"cliente": {}})
    assert excinfo.value.details["field"] == "cliente.inesistente"


def test_an_each_over_a_non_lists_field_is_its_own_path_not_the_generic_body() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("{{#each r}}X{{/each}}", {"r": "non una lista"})
    assert excinfo.value.details["field"] == "r"


def test_a_missing_required_declared_variable_fails_before_rendering() -> None:
    declared = (DeclaredVariable(name="oggetto", label="Oggetto", type="text", required=True),)
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("{{oggetto}}", {}, declared)
    assert excinfo.value.details["field"] == "oggetto"
    assert "obbligatoria" in excinfo.value.details["reason"]


# --- Fix round 1, item 4 continued: the missing-required-variable error also
# names the line of its first root-level reference in the template, when it has
# one -- this check used to run *before* parsing, so no tree existed yet to
# search, and no line could ever be reported at all.


def test_a_missing_required_declared_variable_names_the_line_of_its_first_use() -> None:
    declared = (DeclaredVariable(name="oggetto", label="Oggetto", type="text", required=True),)
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("riga1\nriga2 {{oggetto}}\n", {}, declared)
    assert "riga 2" in excinfo.value.details["reason"]


def test_a_missing_required_variable_referenced_only_by_an_if_names_that_line() -> None:
    # The spec's own motivating shape for declared_paths (parser.py): a
    # template's entire behaviour can depend on `{{#if x}}...{{/if}}` with no
    # bare `{{x}}` ever appearing. The line search must still find it.
    declared = (DeclaredVariable(name="sconto", label="Sconto", type="checkbox", required=True),)
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("riga1\nriga2\n{{#if sconto}}s{{/if}}\n", {}, declared)
    assert "riga 3" in excinfo.value.details["reason"]


def test_a_missing_required_variable_referenced_only_inside_each_is_not_a_false_match() -> None:
    # A `{{sconto}}` inside `{{#each righe}}` is relative to the loop's current
    # item, a different name-space from a root-level declared variable of the
    # same spelling -- it must not be mistaken for the declared variable's own
    # first use, even though the name matches textually.
    declared = (DeclaredVariable(name="sconto", label="Sconto", type="checkbox", required=True),)
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("riga1\n{{#each righe}}\n{{sconto}}\n{{/each}}\n", {"righe": []}, declared)
    # No root-level reference exists (the one inside #each does not count), so
    # no line is fabricated -- the reason names the missing variable, not a line.
    assert "riga" not in excinfo.value.details["reason"]


def test_a_missing_required_variable_never_referenced_anywhere_omits_the_line() -> None:
    # Declared, not deduced (spec 4.3): a required variable the template body
    # never actually mentions is a legitimate shape, not a bug -- and there is no
    # honest line to report for it, so none is fabricated.
    declared = (DeclaredVariable(name="oggetto", label="Oggetto", type="text", required=True),)
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("nessun placeholder qui\n", {}, declared)
    assert "riga" not in excinfo.value.details["reason"]
    assert "obbligatoria" in excinfo.value.details["reason"]


def test_a_missing_optional_declared_variable_renders_as_empty() -> None:
    declared = (DeclaredVariable(name="note", label="Note", type="text", required=False),)
    assert render_template("[{{note}}]", {}, declared) == "[]"


# --- Fix round 1, item 3: declaring a variable optional must never make a
# template *more* likely to fail than leaving it undeclared. `optional_defaults`
# used to seed `""` regardless of `type`, which broke exactly this for
# `multiselect`: `{{#each righe}}` resolved `lines` to the string `""`, not a
# list, and raised where the undeclared, unsupplied case renders nothing.


def test_an_optional_declared_multiselect_left_unsupplied_renders_like_undeclared() -> None:
    declared = (DeclaredVariable(name="righe", label="Righe", type="multiselect", required=False),)
    with_declaration = render_template("a{{#each righe}}X{{/each}}b", {}, declared)
    without_declaration = render_template("a{{#each righe}}X{{/each}}b", {})
    assert with_declaration == without_declaration == "ab"


def test_an_optional_declared_checkbox_left_unsupplied_takes_the_if_else_branch() -> None:
    declared = (DeclaredVariable(name="attivo", label="Attivo", type="checkbox", required=False),)
    out = render_template("{{#if attivo}}si{{else}}no{{/if}}", {}, declared)
    assert out == "no"


def test_an_optional_declared_variable_of_any_type_still_renders_blank_when_bare() -> None:
    # format_value(None) == "" regardless of which of the nine field types
    # declared the variable -- the fix does not need a per-type table of empty
    # values to get every type right, only the one it actually breaks.
    for type in ("text", "textarea", "number", "currency", "date", "select", "url"):
        declared = (DeclaredVariable(name="x", label="X", type=type, required=False),)
        assert render_template("[{{x}}]", {}, declared) == "[]"


def test_a_blank_string_does_not_satisfy_a_required_variable() -> None:
    declared = (DeclaredVariable(name="oggetto", label="Oggetto", type="text", required=True),)
    with pytest.raises(ValidationFailed):
        render_template("{{oggetto}}", {"oggetto": "   "}, declared)


def test_false_and_zero_satisfy_a_required_variable() -> None:
    # Mirrors `is_blank` in fields/validator.py: False and 0 are values, not blanks.
    declared = (DeclaredVariable(name="x", label="X", type="checkbox", required=True),)
    assert render_template("{{#if x}}s{{else}}n{{/if}}", {"x": False}, declared) == "n"
    assert render_template("{{x}}", {"x": 0}, declared) == "0"


def test_format_value_renders_money_as_a_decimal_string_never_a_float() -> None:
    assert format_value(Decimal("1234.56")) == "1234.56"
    assert format_value(Decimal("0.10")) == "0.10"


def test_format_value_renders_a_date_as_iso() -> None:
    assert format_value(date(2026, 8, 10)) == "2026-08-10"


def test_format_value_renders_booleans_in_italian() -> None:
    assert format_value(True) == "Si"
    assert format_value(False) == "No"


def test_format_value_renders_none_as_empty() -> None:
    assert format_value(None) == ""


def test_a_nul_byte_in_a_value_is_rejected() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        render_template("{{x}}", {"x": "a\x00b"})
    assert "carattere nullo" in excinfo.value.details["reason"]


# --- Beyond the brief's own list. This module's whole risk, named explicitly in the
# task brief, is that applying the wrong escaper is silent: escape_markdown and
# escape_typst currently produce byte-identical output for every input (see
# escaping.py's own docstring), so a mix-up between "markdown" and "typst" cannot be
# caught by comparing rendered strings -- both tests above already pass whichever of
# the two gets used for a given node. The tests below target the actual risk instead
# of the part a string comparison happens to be able to see.


def test_the_renderer_calls_escape_for_with_each_nodes_own_context_never_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Proven at the one point that actually matters: what `escape_for` is called
    # with, for a template that places the *same* value once in a markdown segment
    # and once in a typst segment. A renderer that ever re-derived context from the
    # path, the value, or anything other than the node the parser built would still
    # pass this file's string-comparison tests above (the two escapers agree today)
    # but would fail this one the moment it called escape_for with the wrong label.
    import pigrocrm.core.templates.renderer as renderer_module

    calls: list[tuple[str, str]] = []
    original = renderer_module.escape_for

    def spy(context: str, value: str) -> str:
        calls.append((context, value))
        return original(context, value)  # type: ignore[arg-type]

    monkeypatch.setattr(renderer_module, "escape_for", spy)
    render_template(
        BOTH_CONTEXTS,
        {"cliente": {"ragione_sociale": "ACME"}, "offerta": {"totale": "100"}},
    )
    assert ("markdown", "ACME") in calls
    assert ("typst", "ACME") in calls
    assert ("typst", "100") in calls
    assert ("markdown", "100") not in calls


def test_the_same_path_is_escaped_per_its_own_segment_not_a_global_guess() -> None:
    # Unlike markdown vs typst, the "url" escaper *does* produce visibly different
    # output from "markdown" for the same input -- which makes this a case a plain
    # string comparison actually can catch, using the same value in two segments the
    # way the brief's own BOTH_CONTEXTS fixture does for markdown vs typst.
    url = "https://esempio.it/a?b=c"
    out = render_template(
        "Sito: {{cliente.sito_web}} - [link]({{cliente.sito_web}})",
        {"cliente": {"sito_web": url}},
    )
    assert r"Sito: https\:\/\/esempio\.it\/a\?b\=c" in out
    assert "[link](<https://esempio.it/a?b=c>)" in out


def test_a_typst_fence_inside_an_each_body_repeats_the_same_template_line_marker() -> None:
    # The marker names the *template* line the fence is written on, which does not
    # change across iterations -- three rows of the same `{{#each}}` body should
    # show the same "// pigrocrm:line=N" three times, not three different numbers
    # and not a marker that got lost, duplicated wrongly, or escaped by mistake
    # after the first iteration. The fence opener must start its own line (the
    # segmenter's fence pattern requires it), so it sits on the line after
    # `{{#each righe}}`, not the same one.
    source = "{{#each righe}}\n```{=typst}\n#text[{{nome}}]\n```\n{{/each}}\n"
    out = render_template(source, {"righe": [{"nome": "A"}, {"nome": "B"}, {"nome": "C"}]})
    assert out.count(f"{TYPST_LINE_MARKER_PREFIX}3") == 3
    assert "A" in out and "B" in out and "C" in out


# --- Fix round 1, item 1: line mapping must survive control flow *inside* a fence.
# The single fence-open marker plus "count newlines from here" is exactly wrong the
# moment a branch is skipped (its template lines vanish from the render, but the
# template still had them) or a loop body repeats (its lines multiply). This helper
# plays the part a later diagnostics module will actually play: given a rendered
# typst fence, find the nearest preceding marker and add the line distance -- the
# same arithmetic the module docstring on `_resume_marker` describes. A test that
# only checked "a marker with the right value appears somewhere in the output"
# would have passed against the original, unfixed code too (it does emit *a*
# marker); checking what this arithmetic actually *resolves to* for a line that
# comes after the control flow is what catches the bug.


def _predicted_template_line(rendered_typst_body: str, target: str) -> int:
    lines = rendered_typst_body.splitlines()
    target_idx = next(i for i, line in enumerate(lines) if target in line)
    for i in range(target_idx, -1, -1):
        if lines[i].startswith(TYPST_LINE_MARKER_PREFIX):
            marker_value = int(lines[i][len(TYPST_LINE_MARKER_PREFIX) :])
            return marker_value + (target_idx - i - 1)
    raise AssertionError(f"no line marker precedes {target!r}")


# The coordinator's own reproduction: a skipped #if branch inside a fence.
_IF_FENCE = (
    "```{=typst}\n"
    "{{#if mai}}\n"
    "#text[nascosto]\n"
    "{{/if}}\n"
    "#text[ok]\n"
    "#nonesistente[boom]\n"  # template line 6
    "```\n"
)


def test_line_after_a_skipped_if_branch_is_still_mapped_to_its_true_template_line() -> None:
    out = render_template(_IF_FENCE, {"mai": False})
    assert _predicted_template_line(out, "#nonesistente[boom]") == 6


def test_line_after_a_taken_if_branch_is_still_mapped_to_its_true_template_line() -> None:
    out = render_template(_IF_FENCE, {"mai": True})
    assert _predicted_template_line(out, "#text[nascosto]") == 3
    assert _predicted_template_line(out, "#nonesistente[boom]") == 6


_IF_ELSE_FENCE = (
    "```{=typst}\n"
    "{{#if mai}}\n"
    "#text[A]\n"
    "{{else}}\n"
    "#text[B]\n"  # template line 5
    "{{/if}}\n"
    "#text[ok]\n"  # template line 7
    "```\n"
)


def test_line_inside_and_after_a_taken_else_branch_is_mapped_correctly() -> None:
    out = render_template(_IF_ELSE_FENCE, {"mai": False})
    assert _predicted_template_line(out, "#text[B]") == 5
    assert _predicted_template_line(out, "#text[ok]") == 7


# The coordinator's own reproduction: an #each loop, three rows, inside a fence.
_EACH_FENCE = (
    "```{=typst}\n"
    "{{#each righe}}\n"
    "#text[{{nome}}]\n"
    "{{/each}}\n"
    "#nonesistente[boom]\n"  # template line 5
    "```\n"
)


def test_line_after_an_each_loop_with_three_rows_is_still_mapped_to_its_true_line() -> None:
    out = render_template(_EACH_FENCE, {"righe": [{"nome": "A"}, {"nome": "B"}, {"nome": "C"}]})
    assert _predicted_template_line(out, "#nonesistente[boom]") == 5


def test_line_after_an_each_loop_with_zero_rows_is_still_mapped_to_its_true_line() -> None:
    out = render_template(_EACH_FENCE, {"righe": []})
    assert _predicted_template_line(out, "#nonesistente[boom]") == 5


def test_line_mapping_survives_a_nested_if_inside_an_each_inside_a_fence() -> None:
    source = (
        "```{=typst}\n"
        "{{#each righe}}\n"
        "{{#if this.iva}}\n"
        "#text[IVA]\n"
        "{{/if}}\n"
        "#text[{{nome}}]\n"  # template line 6
        "{{/each}}\n"
        "#dopo[x]\n"  # template line 8
        "```\n"
    )
    out = render_template(
        source, {"righe": [{"nome": "A", "iva": True}, {"nome": "B", "iva": False}]}
    )
    assert _predicted_template_line(out, "#text[A]") == 6
    assert _predicted_template_line(out, "#text[B]") == 6
    assert _predicted_template_line(out, "#dopo") == 8


@requires_real_compiler
def test_for_real_line_mapping_survives_a_skipped_if_branch_through_pandoc(tmp_path: Path) -> None:
    # Settled by compiling, not just by string arithmetic: writes the rendered
    # markdown out, runs the real Pandoc -t typst step, and reads the *actual*
    # intermediate .typ file back to confirm the marker Pandoc actually preserved
    # is still adjacent to the real content the way the arithmetic assumes -- the
    # same standard this slice has used for every other line-number claim.
    rendered = render_template(_IF_FENCE, {"mai": False})
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    md_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    written = typst_path.read_text(encoding="utf-8")
    assert _predicted_template_line(written, "#nonesistente[boom]") == 6
    pdf_path = tmp_path / "doc.pdf"
    # And the marker still produces no visible artefact once actually compiled --
    # Typst rejects #nonesistente as an undefined function, which is the expected,
    # honest failure for a real error in the template's own typst content; this
    # test is about the *line mapping*, not about making that error disappear.
    result = subprocess.run(
        ["typst", "compile", str(typst_path), str(pdf_path)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "pigrocrm:line" not in result.stderr


@requires_real_compiler
def test_for_real_line_mapping_survives_a_three_row_each_loop_through_pandoc(
    tmp_path: Path,
) -> None:
    rendered = render_template(
        _EACH_FENCE, {"righe": [{"nome": "A"}, {"nome": "B"}, {"nome": "C"}]}
    )
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    md_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    written = typst_path.read_text(encoding="utf-8")
    assert _predicted_template_line(written, "#nonesistente[boom]") == 5


# --- Fix round 1, item 2: a placeholder that is the whole content of a Typst
# string-literal argument (`#link("{{u}}")`) must be escaped with
# escape_typst_string, not escape_typst -- see parser.py's detection and
# escaping.py's two escapers. The two reviewer examples, through the full
# render_template path this time, not just the escaper called directly.


def test_a_url_inside_link_survives_the_full_render_as_a_working_link_string() -> None:
    out = render_template(
        '```{=typst}\n#link("{{cliente.sito_web}}")[il sito]\n```\n',
        {"cliente": {"sito_web": "https://esempio.it"}},
    )
    assert '#link("https://esempio.it")' in out


def test_a_value_inside_text_survives_the_full_render_with_no_visible_backslashes() -> None:
    out = render_template('```{=typst}\n#text("{{nome}}")\n```\n', {"nome": "Rossi & C."})
    assert '#text("Rossi & C.")' in out
    assert "\\&" not in out


@requires_real_compiler
def test_for_real_a_customers_url_inside_link_is_a_working_link_end_to_end(
    tmp_path: Path,
) -> None:
    real_url = "https://esempio.it"
    rendered = render_template(
        '```{=typst}\n#link("{{cliente.sito_web}}")[il sito]\n```\n',
        {"cliente": {"sito_web": real_url}},
    )
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    raw = subprocess.run(
        ["strings", str(pdf_path)], check=True, capture_output=True, text=True
    ).stdout
    assert f"/URI ({real_url})" in raw


@requires_real_compiler
def test_for_real_an_injection_attempt_inside_a_string_argument_stays_inert(
    tmp_path: Path,
) -> None:
    hostile = 'x") #import("/etc/passwd") #text("'
    rendered = render_template('```{=typst}\n#text("{{n}}")\n```\n', {"n": hostile})
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(rendered, encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    ).stdout
    assert hostile in text


# --- Real compiles: settling the module's own flagship claim -- "the same value,
# escaped differently in the two contexts, both compile clean" -- against a real PDF
# rather than a string, the same way Task 1 and Task 2 settled theirs. Skipped
# cleanly when Pandoc/Typst/pdftotext are missing; on a machine that has them (this
# one), every subprocess call uses `check=True`, so a real compile error is a test
# failure, never something the skip is allowed to absorb.


def _compile_to_pdf_text(markdown_source: str, tmp_path: Path) -> str:
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(markdown_source, encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    return subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    ).stdout


@requires_real_compiler
def test_for_real_the_spec_3_3_acceptance_scenario_compiles_clean_with_both_injections(
    tmp_path: Path,
) -> None:
    rendered = render_template(
        BOTH_CONTEXTS,
        {
            "cliente": {"ragione_sociale": TYPST_INJECTION},
            "offerta": {"totale": MARKDOWN_INJECTION},
        },
    )
    text = _compile_to_pdf_text(rendered, tmp_path)
    # Neither injected string executed or altered the document's structure: both
    # come out the other end as the literal characters a person typed them as.
    # Checked as separate substrings, not one contiguous phrase: `pdftotext
    # -layout` wraps a long table cell's content across lines by column width,
    # which is a text-extraction layout artefact, not an escaping defect.
    assert '#import "/etc/passwd"' in text
    assert "**Grassetto**" in text
    assert "<script>" in text
    # And the line marker that made the round trip produced no visible artefact.
    assert "pigrocrm:line" not in text


@requires_real_compiler
def test_for_real_an_each_body_with_a_typst_fence_compiles_clean_for_every_row(
    tmp_path: Path,
) -> None:
    source = "intro\n\n{{#each righe}}\n```{=typst}\n#text[{{nome}}: {{nota}}]\n```\n{{/each}}\n"
    rendered = render_template(
        source,
        {
            "righe": [
                {"nome": "Rossi", "nota": "// annullato"},
                {"nome": "Bianchi", "nota": "normale"},
            ]
        },
    )
    text = _compile_to_pdf_text(rendered, tmp_path)
    assert "Rossi: // annullato" in text
    assert "Bianchi: normale" in text
    assert "pigrocrm:line" not in text
