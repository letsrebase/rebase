import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.templates.ast import EachNode, IfNode, TextNode, VariableNode
from pigrocrm.core.templates.parser import (
    TYPST_LINE_MARKER_PREFIX,
    declared_paths,
    parse_template,
    segment,
)

TYPST_BLOCK_TEMPLATE = """Gentile {{cliente.ragione_sociale}},

```{=typst}
#table(
  columns: (0.8fr, 0.2fr),
  [{{riga.servizio}}],
  [{{riga.totale}}],
)
```

Cordiali saluti.
"""


def test_segment_marks_a_fenced_typst_block_as_typst_context() -> None:
    segments = segment(TYPST_BLOCK_TEMPLATE)
    contexts = [s.context for s in segments]
    assert "typst" in contexts
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "#table(" in typst_segment.text
    assert "{{riga.servizio}}" in typst_segment.text


def test_segment_marks_everything_outside_a_fence_as_markdown() -> None:
    segments = segment(TYPST_BLOCK_TEMPLATE)
    markdown_text = "".join(s.text for s in segments if s.context == "markdown")
    assert "{{cliente.ragione_sociale}}" in markdown_text
    assert "#table(" not in markdown_text


def test_segment_marks_an_inline_typst_span_as_typst_context() -> None:
    segments = segment("prima `#emph[{{x}}]`{=typst} dopo")
    assert [(s.context, s.text) for s in segments] == [
        ("markdown", "prima "),
        ("typst", "`#emph[{{x}}]`{=typst}"),
        ("markdown", " dopo"),
    ]


def test_segment_marks_a_link_destination_as_url_context() -> None:
    segments = segment("vedi [il sito]({{cliente.sito_web}}) per i dettagli")
    assert [(s.context, s.text) for s in segments] == [
        ("markdown", "vedi [il sito"),
        ("url", "]({{cliente.sito_web}})"),
        ("markdown", " per i dettagli"),
    ]


def test_segment_records_the_line_each_segment_starts_on() -> None:
    segments = segment(TYPST_BLOCK_TEMPLATE)
    typst_segment = next(s for s in segments if s.context == "typst")
    # The fence opens on line 3 of TYPST_BLOCK_TEMPLATE.
    assert typst_segment.line == 3


def test_parse_gives_each_variable_the_context_of_its_segment() -> None:
    nodes = parse_template(TYPST_BLOCK_TEMPLATE)
    variables = {n.path: n.context for n in nodes if isinstance(n, VariableNode)}
    assert variables[("cliente", "ragione_sociale")] == "markdown"
    assert variables[("riga", "servizio")] == "typst"
    assert variables[("riga", "totale")] == "typst"


def test_the_same_placeholder_gets_two_different_contexts_in_two_places() -> None:
    # This is the single fact the whole engine exists for.
    source = "Nome: {{c.nome}}\n\n```{=typst}\n#text[{{c.nome}}]\n```\n"
    contexts = sorted(n.context for n in parse_template(source) if isinstance(n, VariableNode))
    assert contexts == ["markdown", "typst"]


def test_parse_records_the_template_line_of_every_variable() -> None:
    nodes = parse_template("riga1\nriga2 {{a}}\nriga3\n")
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.line == 2


def test_parse_builds_an_if_node_with_both_branches() -> None:
    nodes = parse_template("{{#if offerta.iva}}con IVA{{else}}senza IVA{{/if}}")
    assert len(nodes) == 1
    node = nodes[0]
    assert isinstance(node, IfNode)
    assert node.path == ("offerta", "iva")
    assert node.then == (TextNode("con IVA"),)
    assert node.otherwise == (TextNode("senza IVA"),)


def test_parse_builds_an_if_node_with_no_else_branch() -> None:
    nodes = parse_template("{{#if x}}solo{{/if}}")
    node = nodes[0]
    assert isinstance(node, IfNode)
    assert node.otherwise == ()


def test_parse_builds_an_each_node_and_resolves_this() -> None:
    nodes = parse_template("{{#each righe}}{{this}}-{{nome}};{{/each}}")
    node = nodes[0]
    assert isinstance(node, EachNode)
    assert node.path == ("righe",)
    paths = [n.path for n in node.body if isinstance(n, VariableNode)]
    assert paths == [("this",), ("nome",)]


def test_parse_nests_blocks() -> None:
    nodes = parse_template("{{#each r}}{{#if r.iva}}X{{/if}}{{/each}}")
    outer = nodes[0]
    assert isinstance(outer, EachNode)
    assert isinstance(outer.body[0], IfNode)


def test_parse_rejects_an_unclosed_block_naming_the_line() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("prima\n{{#if x}}mai chiuso\n")
    assert excinfo.value.details["field"] == "body_markdown"
    assert "riga 2" in excinfo.value.details["reason"]
    assert "#if" in excinfo.value.details["reason"]


def test_parse_rejects_a_stray_closing_tag_naming_the_line() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("a\nb\n{{/each}}")
    assert "riga 3" in excinfo.value.details["reason"]


def test_parse_rejects_a_mismatched_closing_tag() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("{{#if x}}{{/each}}")
    assert "non corrisponde" in excinfo.value.details["reason"]


def test_parse_rejects_an_else_outside_an_if() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("{{else}}")
    assert "else" in excinfo.value.details["reason"]


@pytest.mark.parametrize(
    "bad",
    ["{{ }}", "{{1abc}}", "{{a..b}}", "{{a.}}", "{{a b}}", "{{a-b}}", "{{a\n}}"],
)
def test_parse_rejects_a_malformed_path(bad: str) -> None:
    with pytest.raises(ValidationFailed):
        parse_template(bad)


def test_parse_rejects_a_helper_call_because_the_engine_has_no_helpers() -> None:
    # Deliberate: "un template e' un documento, non un programma" (spec 3.2).
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("{{uppercase cliente.nome}}")
    assert "non ammessa" in excinfo.value.details["reason"]


# Overrides the brief: task-2-brief.md's own version of this test asserted
# `declared_paths(nodes) == (("a", "b"), ("nome",), ("c",))`, a single flat tuple
# that both omits the "#each r" block's own path entirely and puts the loop-
# relative "nome" in the same list as the root path "a.b". The coordinator
# overrode this: a template whose entire behaviour depends on an `#if`/`#each`
# path (the spec's own example is `offer.sconto`) used to declare that it
# needed nothing at all, which defeats what `describe_template` exists for. See
# `DeclaredPaths` and `declared_paths`'s own docstring in parser.py.


def test_declared_paths_collects_every_root_path_once_in_order() -> None:
    nodes = parse_template("{{a.b}}{{#each r}}{{nome}}{{/each}}{{a.b}}{{c}}")
    declared = declared_paths(nodes)
    assert declared.root == (("a", "b"), ("r",), ("c",))
    assert declared.loop_relative == (("nome",),)


def test_declared_paths_reports_an_if_blocks_own_path_as_a_root_requirement() -> None:
    # The spec's own motivating example: a template that only ever does
    # {{#if offerta.sconto}}...{{/if}} still needs offerta.sconto supplied, even
    # though no bare {{offerta.sconto}} ever appears as a VariableNode.
    nodes = parse_template("{{#if offerta.sconto}}scontato{{/if}}")
    assert declared_paths(nodes).root == (("offerta", "sconto"),)


def test_declared_paths_keeps_a_conditional_inside_a_loop_out_of_the_root_set() -> None:
    # this.iva is relative to the current element of "righe" -- a caller does not,
    # and cannot, supply a root-level "this".
    nodes = parse_template("{{#each righe}}{{#if this.iva}}IVA{{/if}}{{/each}}")
    declared = declared_paths(nodes)
    assert declared.root == (("righe",),)
    assert declared.loop_relative == (("this", "iva"),)


def test_declared_paths_a_nested_each_path_is_loop_relative_to_the_outer_one() -> None:
    nodes = parse_template("{{#each ordini}}{{#each righe}}{{nome}}{{/each}}{{/each}}")
    declared = declared_paths(nodes)
    assert declared.root == (("ordini",),)
    assert declared.loop_relative == (("righe",), ("nome",))


def test_a_template_with_no_placeholders_is_one_text_node() -> None:
    assert parse_template("solo testo") == (TextNode("solo testo"),)


def test_an_empty_template_parses_to_nothing() -> None:
    assert parse_template("") == ()


# --- Beyond the brief's own list: the segmentation boundary is the security-relevant
# part (a placeholder escaped for the wrong context loses everything Task 1 fixed),
# so every edge case named in the task -- an unterminated fence, a fence indented
# inside a list, extra backticks, a malformed attribute, a placeholder spanning a
# boundary, CRLF -- gets its own test below, and the ones that are actually claims
# about what *Pandoc* does are checked against a real Pandoc, not just against this
# module's own regex agreeing with itself.


def test_a_second_else_in_the_same_if_block_is_rejected_not_silently_overwritten() -> None:
    with pytest.raises(ValidationFailed) as excinfo:
        parse_template("{{#if x}}a{{else}}b{{else}}c{{/if}}")
    assert "duplicato" in excinfo.value.details["reason"]


def test_padding_spaces_inside_braces_are_ergonomic_but_an_embedded_newline_still_fails() -> None:
    # The `.strip()` in the brief's own sample code strips a trailing "\n" along with
    # the spaces -- which is the exact P.IVA-shaped mistake `_PATH_RE`'s `fullmatch`
    # comment warns about, just moved one call earlier: `{{a\n}}` would reach
    # `_parse_path` already reduced to the valid path "a", the newline discarded
    # before `fullmatch` ever ran. This test pins the fix's actual shape: horizontal
    # padding is still convenient, and a real newline is still rejected regardless.
    nodes = parse_template("{{ cliente.nome }}")
    assert nodes == (VariableNode(("cliente", "nome"), "markdown", 1),)
    with pytest.raises(ValidationFailed):
        parse_template("{{ cliente.nome\n }}")


def test_an_unterminated_fence_is_ordinary_markdown_text_not_a_broken_inline_match() -> None:
    # A naive `` `[^`\n]*`\{=typst\} `` inline pattern matches *inside* this fence's
    # own opening line -- the 2nd and 3rd backticks plus the attribute -- because
    # nothing stops a single-backtick delimiter from sitting right next to another
    # backtick. Confirmed against real Pandoc below: with no closing fence anywhere in
    # the document, the whole line is ordinary paragraph text, not a raw block and not
    # a code span either.
    source = "prima\n\n```{=typst}\n#table([{{riga.totale}}])\n\ndopo, mai chiuso\n"
    segments = segment(source)
    assert all(s.context == "markdown" for s in segments)
    joined = "".join(s.text for s in segments)
    assert joined == source
    nodes = parse_template(source)
    variables = {n.path: n.context for n in nodes if isinstance(n, VariableNode)}
    assert variables[("riga", "totale")] == "markdown"


def test_a_placeholder_spanning_a_fence_boundary_becomes_two_inert_text_fragments() -> None:
    # `{{` opens in the markdown segment, the fence begins before `}}` closes it.
    # Tokenising happens per-segment, so this can never merge into one VariableNode
    # with an arbitrary context -- it becomes two literal, unescaped text fragments,
    # which is confusing for whoever wrote the template but not a security question:
    # no value is ever substituted for either half.
    source = "Nome: {{cliente.\n```{=typst}\nnome}}\n```\n"
    nodes = parse_template(source)
    assert not any(isinstance(n, VariableNode) for n in nodes)
    assert any(isinstance(n, TextNode) and "{{cliente." in n.text for n in nodes)
    assert any(isinstance(n, TextNode) and "nome}}" in n.text for n in nodes)


def test_segment_handles_crlf_line_endings_around_and_inside_a_fence() -> None:
    source = "prima\r\n\r\n```{=typst}\r\n#table([{{a}}])\r\n```\r\n\r\ndopo\r\n"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "#table([{{a}}])" in typst_segment.text
    assert "\r" not in typst_segment.text
    # Line 3 whichever line-ending convention it was written with: "prima", "", then
    # the fence opener.
    assert typst_segment.line == 3
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"
    assert variable.line == 4


def test_segment_recognises_a_fence_indented_inside_a_single_level_list() -> None:
    source = "- voce uno\n  ```{=typst}\n  #table([{{riga.totale}}])\n  ```\n- voce due\n"
    segments = segment(source)
    assert "typst" in [s.context for s in segments]
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{riga.totale}}" in typst_segment.text
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


def test_a_fence_indented_four_spaces_at_top_level_is_not_typst() -> None:
    # Four spaces (unindented by a list) is CommonMark's own threshold for "this is an
    # indented code block instead" -- and an indented code block cannot carry a
    # `{=typst}` attribute in the first place. Confirmed against real Pandoc below.
    source = "prima\n\n    ```{=typst}\n    #table([{{a}}])\n    ```\n\ndopo\n"
    segments = segment(source)
    assert all(s.context == "markdown" for s in segments)


def test_a_fence_nested_two_list_levels_deep_is_not_recognised_as_typst() -> None:
    # Documented, tested scope boundary, not a silent gap: a fence one list level deep
    # lines up with CommonMark's own "indented up to three spaces" allowance (see
    # test_segment_recognises_a_fence_indented_inside_a_single_level_list above), but
    # a *second* nested level pushes the fence's indentation past that, and this is a
    # flat regex, not a container-aware Markdown parser that tracks how many spaces
    # each enclosing list item strips. A template that needs this nested a raw block
    # should put the block at the top level instead.
    source = "- uno\n  - due\n    ```{=typst}\n    #table([{{a}}])\n    ```\n"
    segments = segment(source)
    assert all(s.context == "markdown" for s in segments)


def test_segment_treats_a_language_class_attribute_as_verbatim_not_typst() -> None:
    # `{.typst}` is a syntax-highlighting *class*, not the `=format` raw-block marker
    # -- a different Pandoc feature entirely (a real CodeBlock, confirmed live in
    # test_for_real_a_language_class_attribute_is_a_code_block_not_a_raw_block), and
    # its content is code to be displayed, not Typst source to be passed through
    # untouched. Fix round 1, item 2: "verbatim" now, not "markdown" -- Pandoc does
    # not markdown-escape-process a CodeBlock's content either, so this segment must
    # not be tokenised for placeholders any more than a genuine {=typst} one is
    # substituted with the wrong escaper.
    source = "prima\n\n```{.typst}\n#table([{{a}}])\n```\n\ndopo\n"
    segments = segment(source)
    assert [s.context for s in segments] == ["markdown", "verbatim", "markdown"]


def test_segment_treats_a_raw_format_combined_with_another_attribute_as_verbatim() -> None:
    # `{=format}` is special-cased by Pandoc to be the *entire* attribute; combined
    # with anything else it is not recognised as a raw block at all (confirmed live
    # below) -- Pandoc falls back to an ordinary, verbatim inline code span instead.
    # Being lenient about it here would mark a segment "typst" whose content
    # Pandoc's own `-t typst` writer never actually treats as raw; "markdown" would
    # be just as wrong, since Pandoc does not escape-process this content either.
    source = "prima\n\n```{=typst .foo}\nB\n```\n\ndopo\n"
    segments = segment(source)
    assert "typst" not in [s.context for s in segments]
    assert "verbatim" in [s.context for s in segments]


def test_segment_accepts_trailing_whitespace_inside_the_attribute_braces() -> None:
    source = "prima\n\n```{=typst }\n#table([{{a}}])\n```\n\ndopo\n"
    segments = segment(source)
    assert "typst" in [s.context for s in segments]


def test_segment_rejects_a_leading_space_inside_the_attribute_braces_as_not_typst() -> None:
    # Not "typst": confirmed live that Pandoc does not read this as the raw-typst
    # attribute either. It ends up "verbatim" rather than "markdown" (fix round 1),
    # since falling all the way back to Pandoc's own multi-line inline-code-span
    # reading of "```{= typst}\nB\n```" is still a construct Pandoc does not
    # markdown-escape-process.
    source = "prima\n\n```{= typst}\nB\n```\n\ndopo\n"
    segments = segment(source)
    assert "typst" not in [s.context for s in segments]


def test_segment_recognises_a_fence_with_extra_backticks_around_inner_backticks() -> None:
    # Typst's own raw-code syntax uses backticks too, so a raw block that needs to
    # *contain* a literal ``` has to open with four or more. Confirmed against real
    # Pandoc below: it round-trips the inner ``` unchanged.
    source = "prima\n\n````{=typst}\n`raw code`\n#table([{{a}}])\n````\n\ndopo\n"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "`raw code`" in typst_segment.text
    assert "{{a}}" in typst_segment.text


# --- Fix round 1, items 2-3: verbatim regions. Pandoc does not markdown-escape-
# process the content of a plain code span or fenced code block at all, so a
# placeholder there must not be substituted-and-escaped the way one in ordinary
# markdown text is -- that produces visible backslashes in the rendered document
# (escape_markdown's own output, un-consumed). Decision made and pinned here: a
# verbatim segment is never tokenised, so `{{...}}` written inside one stays
# literal, unsubstituted text -- confusing for an author who meant to parametrise a
# code example, never a value landing where it can break the delimiter around it.


def test_segment_marks_a_plain_fenced_code_block_as_verbatim() -> None:
    source = "prima\n\n```python\nnome = '{{cliente.nome}}'\n```\n\ndopo\n"
    segments = segment(source)
    assert [s.context for s in segments] == ["markdown", "verbatim", "markdown"]
    verbatim_segment = next(s for s in segments if s.context == "verbatim")
    assert "{{cliente.nome}}" in verbatim_segment.text


def test_segment_marks_a_plain_inline_code_span_as_verbatim() -> None:
    source = "vedi `nome = {{cliente.nome}}` qui"
    segments = segment(source)
    assert [(s.context, s.text) for s in segments] == [
        ("markdown", "vedi "),
        ("verbatim", "`nome = {{cliente.nome}}`"),
        ("markdown", " qui"),
    ]


def test_parse_does_not_substitute_a_placeholder_inside_a_plain_fenced_code_block() -> None:
    source = "prima\n\n```python\nnome = '{{cliente.nome}}'\n```\n\ndopo\n"
    nodes = parse_template(source)
    assert not any(isinstance(n, VariableNode) for n in nodes)
    assert any(isinstance(n, TextNode) and "nome = '{{cliente.nome}}'" in n.text for n in nodes)


def test_parse_does_not_substitute_a_placeholder_inside_a_plain_inline_code_span() -> None:
    source = "vedi `nome = {{cliente.nome}}` qui"
    nodes = parse_template(source)
    assert not any(isinstance(n, VariableNode) for n in nodes)
    assert any(isinstance(n, TextNode) and "{{cliente.nome}}" in n.text for n in nodes)


def test_a_typst_fence_nested_inside_a_wider_plain_fence_is_verbatim_not_typst() -> None:
    # The mirror of items 2-3: a genuine ```{=typst} fence, verbatim to Pandoc
    # because a *wider*, non-typst fence encloses it. No special-case code exists
    # for this -- it falls out of the same leftmost-match scanning that already
    # keeps a placeholder from spanning a segment boundary: the outer fence is
    # recognised first, at its own opening line, and consumes everything up to its
    # own matching close, so the inner "```{=typst}" is just characters inside that
    # lazily-matched content and never gets a chance to be matched on its own.
    source = "prima\n\n````md\nqualcosa\n```{=typst}\n#emph[{{cliente.nome}}]\n```\n````\n\ndopo\n"
    segments = segment(source)
    assert "typst" not in [s.context for s in segments]
    verbatim_segment = next(s for s in segments if s.context == "verbatim")
    assert "```{=typst}" in verbatim_segment.text
    assert "{{cliente.nome}}" in verbatim_segment.text
    nodes = parse_template(source)
    assert not any(isinstance(n, VariableNode) for n in nodes)


# --- Fix round 1, item 4: three more fence spellings Pandoc accepts. Latent bugs
# until now only because escape_markdown and escape_typst happen to be byte-
# identical; also, the "not recognised at all" case had one live consequence
# already: inside an unrecognised fence, the `url` alternative was free to fire on
# any "](...)"-shaped substring in the raw Typst source and percent-encode a value
# in the middle of it. Pinned below as a regression test, not just a claim.


def test_segment_recognises_a_tilde_fence_as_typst() -> None:
    source = "prima\n\n~~~{=typst}\n#table([{{a}}])\n~~~\n\ndopo\n"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{a}}" in typst_segment.text


def test_segment_recognises_a_wider_tilde_fence() -> None:
    source = "prima\n\n~~~~{=typst}\n#table([{{a}}])\n~~~~\n\ndopo\n"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{a}}" in typst_segment.text


def test_segment_recognises_a_typst_fence_inside_a_blockquote() -> None:
    source = "> testo\n> ```{=typst}\n> #table([{{a}}])\n> ```\n"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{a}}" in typst_segment.text


def test_segment_recognises_an_inline_typst_span_with_a_trailing_space_before_the_brace() -> None:
    source = "prima `#emph[{{x}}]`{=typst } dopo"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{x}}" in typst_segment.text


def test_segment_recognises_an_inline_typst_span_with_double_backticks() -> None:
    source = "prima ``#emph[{{x}}]``{=typst} dopo"
    segments = segment(source)
    typst_segment = next(s for s in segments if s.context == "typst")
    assert "{{x}}" in typst_segment.text


def test_a_placeholder_inside_an_unrecognised_tilde_fence_no_longer_gets_url_escaping() -> None:
    # Before this fix, a bare "~~~{=typst}" (with no support for tildes at all) fell
    # through entirely to "markdown", and inside that fallback text, the `url`
    # alternative was free to match any "](...)"-shaped substring of the raw Typst
    # source -- Typst's own square-bracket-heavy syntax makes that a real risk, not
    # a hypothetical one. Now the whole tilde fence is recognised as typst first, so
    # nothing inside it is ever independently scanned for "url" at all.
    source = "prima\n\n~~~{=typst}\n#place([{{a}}](nope))\n~~~\n\ndopo\n"
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


# --- Real compiles: an oracle for what Pandoc actually does with a given byte
# sequence, not a restatement of what this module's own regex believes. Skipped
# cleanly when Pandoc is missing, exactly like test_template_escaping.py; on a machine
# that has it (this one, 3.8.2.1), every subprocess call uses `check=True`.

_MISSING_TOOLS = [tool for tool in ("pandoc", "typst") if shutil.which(tool) is None]
requires_real_compiler = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"real Pandoc round-trip tests need these on PATH: {', '.join(_MISSING_TOOLS)}",
)


def _pandoc_blocks(markdown_source: str, tmp_path: Path) -> list[dict[str, Any]]:
    """Every top-level-or-nested block Pandoc's own reader produces for
    `markdown_source`, flattened one level into list items and block quotes --
    enough to find a `RawBlock` wherever this module's tests put one, without
    writing a full recursive Pandoc-AST walker for a handful of fixtures."""
    md_path = tmp_path / "doc.md"
    md_path.write_text(markdown_source, encoding="utf-8")
    result = subprocess.run(
        ["pandoc", str(md_path), "-t", "json"], check=True, capture_output=True, text=True
    )
    doc = json.loads(result.stdout)
    blocks: list[dict[str, Any]] = []
    for block in doc["blocks"]:
        blocks.append(block)
        if block["t"] in ("BulletList", "OrderedList"):
            items = block["c"] if block["t"] == "BulletList" else block["c"][1]
            for item in items:
                blocks.extend(item)
        elif block["t"] == "BlockQuote":
            blocks.extend(block["c"])
    return blocks


def _is_typst_raw_block(block: dict[str, Any]) -> bool:
    return block["t"] == "RawBlock" and block["c"][0] == "typst"


@requires_real_compiler
def test_for_real_an_unterminated_fence_is_not_a_raw_block_in_pandoc_either(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{=typst}\n#table([x])\n\ndopo, mai chiuso\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)
    assert not any(b["t"] == "CodeBlock" for b in blocks)


@requires_real_compiler
def test_for_real_a_fence_indented_inside_a_single_level_list_is_a_raw_block(
    tmp_path: Path,
) -> None:
    source = "- voce uno\n  ```{=typst}\n  #table([x])\n  ```\n- voce due\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert any(_is_typst_raw_block(b) for b in blocks)


@requires_real_compiler
def test_for_real_a_fence_indented_four_spaces_at_top_level_is_a_plain_code_block(
    tmp_path: Path,
) -> None:
    source = "prima\n\n    ```{=typst}\n    #table([x])\n    ```\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)
    assert any(b["t"] == "CodeBlock" for b in blocks)


@requires_real_compiler
def test_for_real_a_language_class_attribute_is_a_code_block_not_a_raw_block(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{.typst}\n#table([x])\n```\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)
    assert any(b["t"] == "CodeBlock" for b in blocks)


@requires_real_compiler
def test_for_real_trailing_whitespace_inside_the_attribute_braces_is_still_raw(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{=typst }\nB\n```\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert any(_is_typst_raw_block(b) for b in blocks)
    md_path = tmp_path / "doc.md"
    md_path.write_text(source, encoding="utf-8")
    written = subprocess.run(
        ["pandoc", str(md_path), "-t", "typst"], check=True, capture_output=True, text=True
    ).stdout
    assert "B" in written


@requires_real_compiler
def test_for_real_a_leading_space_inside_the_attribute_braces_is_not_raw(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{= typst}\nB\n```\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)


@requires_real_compiler
def test_for_real_a_raw_format_combined_with_another_attribute_is_not_raw(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{=typst .foo}\nB\n```\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)


@requires_real_compiler
def test_for_real_a_mixed_case_attribute_is_dropped_by_the_typst_writer_entirely(
    tmp_path: Path,
) -> None:
    # Not this module's bug to fix -- recorded because it changes what "safe default"
    # means. Pandoc's *reader* accepts `{=Typst}` as a raw block (format "Typst",
    # case preserved), but its `-t typst` *writer* only passes through a block whose
    # format is exactly "typst": a case mismatch does not fall back to visible text,
    # it makes the whole block vanish with no error. This module still only ever
    # recognises the exact lowercase literal, which -- given the content disappears
    # either way once rendered -- is the same outcome as trying to be lenient here,
    # not a gap this module leaves open.
    source = "prima\n\n```{=Typst}\nB\n```\n\ndopo\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(source, encoding="utf-8")
    written = subprocess.run(
        ["pandoc", str(md_path), "-t", "typst"], check=True, capture_output=True, text=True
    ).stdout
    assert "B" not in written


@requires_real_compiler
def test_for_real_extra_backticks_let_a_raw_block_contain_a_literal_triple_backtick(
    tmp_path: Path,
) -> None:
    source = "prima\n\n````{=typst}\n`raw code`\n#table([x])\n````\n\ndopo\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(source, encoding="utf-8")
    written = subprocess.run(
        ["pandoc", str(md_path), "-t", "typst"], check=True, capture_output=True, text=True
    ).stdout
    assert "`raw code`" in written


# --- Fix round 1, items 2-3: proof that "do not substitute inside verbatim" is the
# safe choice, against the two alternatives the review named -- not just an assertion
# that this module now returns "verbatim" for the right spans.


def _compile_markdown_to_pdf_text(markdown_source: str, tmp_path: Path) -> str:
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
def test_for_real_leaving_a_placeholder_unsubstituted_in_a_plain_fence_is_clean(
    tmp_path: Path,
) -> None:
    # This module's actual choice: the literal template syntax, never replaced,
    # round-trips through the real pipeline with no corruption at all -- confusing
    # for an author who meant to parametrise the example, never unsafe.
    source = "prima\n\n```python\nnome = '{{cliente.nome}}'\n```\n\ndopo\n"
    text = _compile_markdown_to_pdf_text(source, tmp_path)
    assert "nome = '{{cliente.nome}}'" in text


@requires_real_compiler
def test_for_real_substituting_with_markdown_escaping_would_leave_visible_backslashes(
    tmp_path: Path,
) -> None:
    # What the review actually found broken, reproduced deliberately: Pandoc's
    # reader does not run escape processing inside a fenced code block, so
    # escape_markdown's backslashes -- meant to be *consumed* by that processing --
    # come out the other end still there. This is the empirical case against
    # "substitute with escaping" as the alternative this module did not choose.
    from pigrocrm.core.templates.escaping import escape_markdown

    hostile = "Rossi // nota & C."
    escaped = escape_markdown(hostile)
    source = f"prima\n\n```python\nnome = '{escaped}'\n```\n\ndopo\n"
    text = _compile_markdown_to_pdf_text(source, tmp_path)
    assert hostile not in text
    assert escaped in text  # the backslashes survive, visibly, in the rendered PDF


@requires_real_compiler
def test_for_real_substituting_unescaped_would_let_a_backtick_break_out_of_the_span(
    tmp_path: Path,
) -> None:
    # The other alternative the review named -- "substitute with no escaping" --
    # reproduced deliberately too, for an *inline* span (a fenced block is delimited
    # by a whole matching line, so a single embedded backtick does not close one
    # early the same way; an inline span is delimited by a matching backtick RUN of
    # equal length wherever it occurs, so one does). A raw customer value containing
    # the same single backtick that delimits the span ends the span early right
    # there, handing the rest of the value back to Pandoc as ordinary text no
    # longer inside any code formatting at all -- confirmed live: the leading "#"
    # loses its code-span protection and Pandoc's writer treats it as if a template
    # author had typed a real heading marker into ordinary prose. This is the
    # empirical case against "substitute with no escaping"; "do not substitute at
    # all" is the only one of the two options named that has no such failure mode,
    # because no customer-controlled byte ever reaches this region.
    hostile = 'Rossi`#import("/etc/passwd")'
    source = f"vedi `nome = {hostile}` qui\n"
    text = _compile_markdown_to_pdf_text(source, tmp_path)
    # The span closed at the embedded backtick: "nome = Rossi" is what stayed
    # monospace code, not the full value, and the rest reads as ordinary text with
    # a heading-shaped "#" -- not the clean, still-inside-one-code-span value this
    # module's actual choice (no substitution at all) would have left untouched.
    assert "nome = Rossi" in text
    assert hostile not in text


@requires_real_compiler
def test_for_real_a_typst_fence_nested_inside_a_wider_fence_is_one_codeblock(
    tmp_path: Path,
) -> None:
    source = "prima\n\n````md\nqualcosa\n```{=typst}\n#emph[x]\n```\n````\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert not any(b["t"] == "RawBlock" for b in blocks)
    code_blocks = [b for b in blocks if b["t"] == "CodeBlock"]
    assert len(code_blocks) == 1
    assert "```{=typst}" in code_blocks[0]["c"][1]


@requires_real_compiler
def test_for_real_a_tilde_fence_is_a_raw_block(tmp_path: Path) -> None:
    source = "prima\n\n~~~{=typst}\n#table([x])\n~~~\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert any(_is_typst_raw_block(b) for b in blocks)


@requires_real_compiler
def test_for_real_a_wider_tilde_fence_is_a_raw_block(tmp_path: Path) -> None:
    source = "prima\n\n~~~~{=typst}\n#table([x])\n~~~~\n\ndopo\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert any(_is_typst_raw_block(b) for b in blocks)


@requires_real_compiler
def test_for_real_a_typst_fence_inside_a_blockquote_is_a_raw_block(tmp_path: Path) -> None:
    source = "> testo\n> ```{=typst}\n> #table([x])\n> ```\n"
    blocks = _pandoc_blocks(source, tmp_path)
    assert any(_is_typst_raw_block(b) for b in blocks)


@requires_real_compiler
def test_for_real_an_inline_span_with_a_trailing_space_is_raw_inline(tmp_path: Path) -> None:
    source = "prima `#emph[x]`{=typst } dopo\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(source, encoding="utf-8")
    written = subprocess.run(
        ["pandoc", str(md_path), "-t", "typst"], check=True, capture_output=True, text=True
    ).stdout
    assert "#emph[x]" in written
    assert "`" not in written  # not left as a literal, un-rendered code span


@requires_real_compiler
def test_for_real_an_inline_span_with_double_backticks_is_raw_inline(tmp_path: Path) -> None:
    source = "prima ``#emph[x]``{=typst} dopo\n"
    md_path = tmp_path / "doc.md"
    md_path.write_text(source, encoding="utf-8")
    written = subprocess.run(
        ["pandoc", str(md_path), "-t", "typst"], check=True, capture_output=True, text=True
    ).stdout
    assert "#emph[x]" in written
    assert "`" not in written


# --- Task 3 (the renderer): the parser stamps every raw typst block fence with a
# `// pigrocrm:line=<N>` comment naming its own template line, so a later stage can
# map a Typst compiler error back to "riga N del template" by arithmetic. Tested here
# rather than only in the renderer's own suite because computing the line is entirely
# this module's responsibility -- `segment()` already carries the true line through
# CRLF, list indentation and blockquote nesting; a marker computed by counting
# newlines within a single TextNode's own string, with no visibility into how many
# lines came before it in the wider document, would be wrong for any fence that is
# not the first thing in the template. That specific wrong-but-plausible shortcut is
# exactly what the first test below pins.


def test_a_typst_fence_far_from_the_document_start_gets_the_correct_line_marker() -> None:
    # A naive "count the newlines inside this fence's own TextNode" approach cannot
    # see "una", "due", "tre" or "quattro" at all and would report line 3, not 7.
    source = "una\ndue\ntre\nquattro\n\n```{=typst}\n#table([x])\n```\n"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert f"{TYPST_LINE_MARKER_PREFIX}7" in text


def test_two_typst_fences_each_get_their_own_correct_line_marker() -> None:
    # Guards against an implementation that carries state between segments (e.g.
    # counting cumulatively) instead of computing each fence's marker independently
    # from that segment's own `seg.line`.
    source = "intro\n\n```{=typst}\n#table([x])\n```\n\nmezzo\n\n```{=typst}\n#table([y])\n```\n"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert f"{TYPST_LINE_MARKER_PREFIX}4" in text
    assert f"{TYPST_LINE_MARKER_PREFIX}10" in text


def test_a_placeholders_line_is_unaffected_by_its_own_fences_inserted_marker() -> None:
    # The marker inserts a real extra line before the fence's body; the offset that
    # compensates for it must land every placeholder's reported line back on the
    # template's own numbering, not the marker-shifted one.
    source = "una\ndue\n\n```{=typst}\n#table([{{a}}])\n```\n"
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.line == 5
    assert variable.context == "typst"


def test_a_tilde_fenced_typst_block_also_gets_a_line_marker() -> None:
    # Generalised beyond the one exact spelling (three backticks, lowercase, no
    # indentation) the task brief's own sample code checks for with a literal
    # `.startswith(...)` -- this segmenter recognises tilde fences too (see
    # test_segment_recognises_a_tilde_fence_as_typst above), and a marker that only
    # fires for one fence spelling would silently not work for the others.
    source = "prima\n\n~~~{=typst}\n#table([x])\n~~~\n\ndopo\n"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert f"{TYPST_LINE_MARKER_PREFIX}4" in text


def test_a_typst_fence_indented_inside_a_list_still_gets_a_correct_line_marker() -> None:
    # Confirmed live (pandoc -t typst, then a real typst compile) before writing this:
    # inserting a bare, unindented marker line does not stop Pandoc from reading the
    # rest of the list-indented fence as the same raw block, and the compiled PDF
    # shows no trace of the marker -- see the task report for the full transcript.
    source = "- voce uno\n  ```{=typst}\n  #table([{{riga.totale}}])\n  ```\n- voce due\n"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert f"{TYPST_LINE_MARKER_PREFIX}3" in text
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.line == 3
    assert variable.context == "typst"


def test_a_typst_fence_inside_a_blockquote_still_gets_a_correct_line_marker() -> None:
    # Same live confirmation as the list case above, for blockquote nesting instead.
    source = "> testo\n> ```{=typst}\n> #table([{{a}}])\n> ```\n"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert f"{TYPST_LINE_MARKER_PREFIX}3" in text


def test_an_inline_typst_span_never_gets_a_line_marker() -> None:
    # An inline raw span is one line with no independent "body" to prepend a line
    # to; this segmenter's own grammar guarantees its matched text never contains a
    # newline, which is the exact signal `parse_template` uses to skip it.
    source = "prima `#emph[{{x}}]`{=typst} dopo"
    nodes = parse_template(source)
    text = "".join(n.text for n in nodes if isinstance(n, TextNode))
    assert TYPST_LINE_MARKER_PREFIX not in text


@requires_real_compiler
def test_for_real_the_line_marker_survives_pandoc_and_produces_no_visible_text(
    tmp_path: Path,
) -> None:
    source = "prima\n\n```{=typst}\n#table([x])\n```\n\ndopo\n"
    nodes = parse_template(source)
    rendered = "".join(n.text for n in nodes if isinstance(n, TextNode))
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(rendered + "\n", encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    written = typst_path.read_text(encoding="utf-8")
    assert f"{TYPST_LINE_MARKER_PREFIX}4" in written
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    ).stdout
    assert "pigrocrm:line" not in text
    assert "prima" in text and "dopo" in text


# --- Fix round 1, item 2: a placeholder that is the entire content of a Typst
# string-literal function argument (`#link("{{u}}")`) needs escape_typst_string,
# not escape_typst -- see parser.py's own comment on _TYPST_STRING_ARG_OPEN_RE for
# why this is narrow by design (a confidently-safe subset, not a general Typst
# string-literal parser) and why "#identifier(" immediately before the quote is
# what makes it safe to detect at all: "#" unambiguously introduces Typst code
# mode, unlike a bare quote on its own, which markup mode also uses for an
# ordinary quotation in prose.


def test_a_placeholder_wrapping_a_single_string_argument_gets_the_typst_string_context() -> None:
    source = '```{=typst}\n#link("{{cliente.sito_web}}")[sito]\n```\n'
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst_string"


def test_a_placeholder_as_a_second_string_argument_also_gets_the_typst_string_context() -> None:
    source = '```{=typst}\n#link("{{cliente.sito_web}}")[{{cliente.nome}}]\n```\n'
    nodes = parse_template(source)
    contexts = {n.path: n.context for n in nodes if isinstance(n, VariableNode)}
    assert contexts[("cliente", "sito_web")] == "typst_string"
    # The second placeholder is markup content (a link's label), not a string
    # argument -- it must keep the ordinary typst context, not be swept along.
    assert contexts[("cliente", "nome")] == "typst"


def test_a_placeholder_in_ordinary_markup_position_keeps_the_typst_context() -> None:
    # No "#identifier(" precedes this quote at all -- it is not code position.
    source = "```{=typst}\n#text[{{cliente.nome}}]\n```\n"
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


def test_a_placeholder_quoted_in_ordinary_prose_keeps_the_typst_context() -> None:
    # The false-positive risk this pattern is deliberately narrow enough to avoid:
    # a bare quote around a placeholder in plain markup text (a prose quotation,
    # not a function argument) must not be mistaken for string-literal position --
    # doing so would apply the *weaker* escaper to a value that actually needs
    # the full markup one, which is a real regression, not a cosmetic one.
    source = '```{=typst}\nIl cliente ha detto "{{testo}}"\n```\n'
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


def test_a_placeholder_split_across_a_concatenation_keeps_the_typst_context() -> None:
    # Not the whole string-argument content -- outside this pattern's narrow
    # reach on purpose (documented residual, not silently wrong: escape_typst is
    # a safe, if cosmetically imperfect, superset for this position).
    source = '```{=typst}\n#link("https://" + "{{cliente.sito_web}}")[sito]\n```\n'
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


def test_a_placeholder_in_a_multi_argument_call_keeps_the_typst_context() -> None:
    source = '```{=typst}\n#link("{{cliente.sito_web}}", "extra")[sito]\n```\n'
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst"


def test_an_inline_typst_span_can_also_get_the_typst_string_context() -> None:
    source = 'prima `#link("{{u}}")[sito]`{=typst} dopo'
    nodes = parse_template(source)
    variable = next(n for n in nodes if isinstance(n, VariableNode))
    assert variable.context == "typst_string"
