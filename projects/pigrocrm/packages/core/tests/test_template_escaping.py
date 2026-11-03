import re
import shutil
import string
import subprocess
from pathlib import Path
from typing import get_args
from urllib.parse import quote

import pytest

from pigrocrm.core.templates.escaping import (
    RenderContext,
    escape_for,
    escape_markdown,
    escape_plain,
    escape_typst,
    escape_typst_string,
    escape_url,
    escape_xml,
)

# The two adversarial values the spec names (§3.3, "Test di accettazione"). Every
# assertion below is about one of these two reaching the page as the literal text
# somebody typed, in whichever of the two contexts it lands in.
TYPST_INJECTION = '#import "/etc/passwd"'
MARKDOWN_INJECTION = "**Grassetto** & <script>"


def test_markdown_escapes_emphasis_so_it_is_not_bold() -> None:
    assert escape_markdown(MARKDOWN_INJECTION) == r"\*\*Grassetto\*\* \& \<script\>"


def test_markdown_escapes_the_backslash_first_and_only_once() -> None:
    # If "\" were escaped after the others, the backslash this function itself
    # introduced would be escaped again and the output would double for every pass.
    assert escape_markdown(r"a\*b") == r"a\\\*b"


def test_markdown_escapes_hash_unconditionally_not_only_at_line_start() -> None:
    # Fix round 1: the first version of this function escaped "#" only at the start
    # of a line (an ATX heading is only syntax there). A reviewer's own compiles
    # showed the curated-character-list design this depended on missed "/" and "@"
    # entirely (see below); the replacement escapes every ASCII punctuation
    # character unconditionally, "#" included. That is simpler and no less correct
    # for "#" specifically: a backslash-escaped "#" mid-sentence round-trips through
    # Pandoc to the identical bare "#" leaving it alone would have produced --
    # confirmed live in test_hash_at_paragraph_start_does_not_become_a_heading below.
    assert escape_markdown("C# e #hashtag") == r"C\# e \#hashtag"
    assert escape_markdown("# Titolo") == r"\# Titolo"


def test_markdown_escapes_brackets_so_a_name_is_not_a_link() -> None:
    assert escape_markdown("[NOME](http://x)") == r"\[NOME\]\(http\:\/\/x\)"


def test_typst_escapes_hash_so_an_import_is_not_executed() -> None:
    assert escape_typst(TYPST_INJECTION) == r"\#import \"\/etc\/passwd\""


def test_typst_escapes_at_dollar_and_angle_brackets() -> None:
    assert escape_typst("a@b $x$ <lab>") == r"a\@b \$x\$ \<lab\>"


def test_typst_escapes_brackets_that_would_close_a_content_block() -> None:
    # A raw {=typst} table cell is written as [ ... ]; an unbalanced "]" in a value
    # ends the cell early and the document stops compiling with a syntax error that
    # points at the template, not at the data.
    assert escape_typst("costo [IVA]") == r"costo \[IVA\]"


def test_typst_collapses_newlines_to_a_space() -> None:
    # A value lands inside a syntactic unit (a table cell, a header field). Keeping a
    # raw newline changes the layout of a document nobody proof-read; a space does not.
    assert escape_typst("riga1\nriga2") == "riga1 riga2"
    assert escape_typst("riga1\r\nriga2") == "riga1 riga2"


def test_typst_escapes_the_backslash_first_and_only_once() -> None:
    assert escape_typst(r"a\#b") == r"a\\\#b"


# --- Fix round 1, item 2: escape_typst_string, for Typst *string-literal* position
# (`#link("...")`, `#text("...")`), as opposed to escape_typst's markup position.
# Only `\` and `"` are a recognised escape inside a Typst string; escaping anything
# else (escape_typst's own rule) is not merely redundant there, it is visibly wrong
# -- confirmed live below and in the real-compile tests further down.


def test_typst_string_escapes_only_backslash_and_double_quote() -> None:
    # Everything else that escape_typst would have escaped -- "&", "#", "$", "[",
    # "]", "<", ">" and the rest of string.punctuation -- has no recognised escape
    # inside a Typst string literal and must stay bare here.
    assert escape_typst_string("Rossi & C.") == "Rossi & C."
    assert escape_typst_string("#import x $y$ [z] <w>") == "#import x $y$ [z] <w>"


def test_typst_string_escapes_the_backslash_first_and_only_once() -> None:
    assert escape_typst_string(r"a\b") == r"a\\b"


def test_typst_string_escapes_a_literal_double_quote() -> None:
    # This is the one that matters most: an unescaped `"` closes the string early
    # and hands the rest of the value to Typst as real code, not data.
    assert escape_typst_string('Rossi "il migliore"') == r"Rossi \"il migliore\""


def test_typst_string_neutralises_an_attempt_to_break_out_and_inject_code() -> None:
    hostile = 'x") #import("/etc/passwd") #text("'
    escaped = escape_typst_string(hostile)
    # Every one of the 4 literal quotes in `hostile` is escaped -- none of them is
    # a bare `"` left for Typst's own string-literal lexer to read as the string
    # ending early.
    assert re.findall(r'(?<!\\)"', escaped) == []
    assert escaped == r"""x\") #import(\"/etc/passwd\") #text(\""""


def test_typst_string_collapses_newlines_to_a_space() -> None:
    assert escape_typst_string("riga1\nriga2") == "riga1 riga2"


def test_typst_string_rejects_a_nul_byte() -> None:
    with pytest.raises(ValueError, match="carattere nullo"):
        escape_typst_string("a\x00b")


def test_url_percent_encodes_everything_unsafe() -> None:
    assert escape_url("a b/c?d=e&f") == "a%20b%2Fc%3Fd%3De%26f"
    assert escape_url(TYPST_INJECTION) == "%23import%20%22%2Fetc%2Fpasswd%22"


# --- Fix round 1: escape_url used to quote(value, safe="") *every* value, including
# a genuine absolute URL -- "https://esempio.it/a?b=c" came out as
# "https%3A%2F%2Fesempio.it%2Fa%3Fb%3Dc", which Pandoc writes into the PDF's own link
# annotation byte-for-byte, so a customer's real website link pointed nowhere. See
# the real-compile tests further down for the annotation read back out of a PDF; the
# tests here are the string-level contract that fix rests on.


def test_url_wraps_a_trustworthy_absolute_url_in_angle_brackets_unencoded() -> None:
    # ":", "/", "?", "=" all stay literal -- the whole point of the angle-bracket
    # destination form is that none of them needs escaping there.
    assert escape_url("https://esempio.it/a?b=c") == "<https://esempio.it/a?b=c>"
    assert escape_url("http://esempio.it") == "<http://esempio.it>"


def test_url_escapes_backslash_and_angle_brackets_inside_a_trusted_url() -> None:
    # The only three characters that mean anything inside "<...>": a literal ">"
    # would close the destination early, "<" is disallowed unescaped by the same
    # grammar, and "\" has to be escaped first so it cannot be read as introducing
    # one of the other two escapes.
    assert escape_url("https://esempio.it/a>b") == "<https://esempio.it/a\\>b>"
    assert escape_url("https://esempio.it/a<b") == "<https://esempio.it/a\\<b>"
    assert escape_url(r"https://esempio.it/a\b") == "<https://esempio.it/a\\\\b>"


def test_url_does_not_trust_a_non_http_scheme() -> None:
    # A scheme this module has never vetted does not get the literal-link treatment
    # just because nothing here would technically break -- javascript:/data:/file:
    # all fall through to the same opaque, inert destination as any other value that
    # is not an absolute http(s) URL.
    assert escape_url("javascript:alert(1)") == quote("javascript:alert(1)", safe="")
    assert escape_url("data:text/html,<b>x</b>") == quote("data:text/html,<b>x</b>", safe="")


def test_url_does_not_trust_a_schemeless_value() -> None:
    # A bare domain with no "http(s)://" is common real-world data (a customer typed
    # "esempio.it" into the field) but this module will not guess a scheme for it.
    assert escape_url("esempio.it") == quote("esempio.it", safe="")


def test_url_does_not_trust_a_value_containing_whitespace_or_a_control_character() -> None:
    # Even with a valid http(s) scheme and host, a value carrying whitespace or a
    # control character never reaches the angle-bracket branch: nothing in this
    # module invents percent-encoding *inside* "<...>", so a raw space or newline
    # there would either break CommonMark's own angle-bracket grammar (no line
    # endings allowed) or -- for a space -- silently do something this function has
    # not verified is safe against every Pandoc version. Falling back to the
    # existing, already-proven-safe percent-encoding is the conservative choice.
    for hostile in (
        "https://esempio.it/a b",
        "https://esempio.it/a\nb",
        "https://esempio.it/a\tb",
        "https://esempio.it/a\x01b",
    ):
        assert escape_url(hostile) == quote(hostile, safe="")
        assert not escape_url(hostile).startswith("<")


def test_escape_for_dispatches_on_context() -> None:
    assert escape_for("markdown", MARKDOWN_INJECTION) == escape_markdown(MARKDOWN_INJECTION)
    assert escape_for("typst", TYPST_INJECTION) == escape_typst(TYPST_INJECTION)
    assert escape_for("url", "a b") == escape_url("a b")


def test_escape_for_rejects_an_unknown_context() -> None:
    with pytest.raises(ValueError, match="contesto di escaping sconosciuto"):
        escape_for("latex", "x")  # type: ignore[arg-type]


def test_escape_for_verbatim_raises_since_it_should_never_be_called() -> None:
    # No escaper exists for "verbatim" on purpose: the parser never tokenises a
    # verbatim segment, so no VariableNode should ever carry this context. This
    # pins the invariant loudly rather than silently -- if it is ever violated, this
    # is what should fail, not a backslash quietly showing up in a rendered PDF.
    with pytest.raises(ValueError, match="verbatim non ha una regola di escaping"):
        escape_for("verbatim", "x")


def test_a_nul_byte_is_rejected_not_stripped_in_every_context() -> None:
    # Same rule as pigrocrm.core.validation.SafeStr: silently deleting one invisible
    # byte from a user's text is a lost character nobody notices.
    for escaper in (escape_markdown, escape_typst, escape_url):
        with pytest.raises(ValueError, match="carattere nullo"):
            escaper("a\x00b")


def test_escaping_is_idempotent_in_meaning_not_in_text() -> None:
    # Escaping twice must not silently produce the same string as escaping once --
    # if it did, a double-escape bug would be invisible. This test exists to make
    # that failure loud if anyone "optimises" the escapers into being idempotent.
    once = escape_markdown("*x*")
    assert escape_markdown(once) != once


def test_every_ascii_punctuation_character_is_escaped_in_both_contexts() -> None:
    # Fix round 1: a reviewer compiled real PDFs against the first version and got
    # two Criticals through with ordinary data, not the two adversarial strings this
    # file names. escape_typst had "@" and escape_markdown did not; neither had "/",
    # and a bare "//" opens a Typst line comment -- a customer named "Rossi // nota"
    # deleted the rest of that line, including a VAT clause, from a real, cleanly
    # compiled PDF, no error. Two independently hand-curated lists drift apart from
    # each other for the same reason one list drifts from correct over time: nothing
    # forces either to be complete. This test asserts the property that replaces
    # both lists: every one of the 32 ASCII punctuation characters is escaped, in
    # both contexts, unconditionally -- not just the ones a previous bug report
    # happened to name.
    for ch in string.punctuation:
        assert escape_markdown(ch) == "\\" + ch, ch
        assert escape_typst(ch) == "\\" + ch, ch


def test_newline_equivalents_all_collapse_to_one_space_in_both_contexts() -> None:
    # Fix round 1: _ANY_NEWLINE (now _NEWLINE_EQUIVALENTS) caught \r\n, \r and \n but
    # missed five characters Typst's own lexer also treats as a line terminator --
    # U+000B, U+000C, U+0085, U+2028 and U+2029. Pandoc's Markdown reader does not
    # recognise any of the five as line-structuring whitespace, so it passed one
    # through a value untouched, straight into the compiled Typst source as a literal
    # code point -- where Typst's lexer does treat it as a real line break. Confirmed
    # live: "Rossi" + U+2028 + "= TITOLO INIETTATO" compiled to a genuine heading, in
    # both contexts, before this fix.
    exotic_newlines = ["\v", "\f", "\x85", chr(0x2028), chr(0x2029), "\r\n", "\r", "\n"]
    for newline in exotic_newlines:
        value = "a" + newline + "b"
        assert escape_markdown(value) == "a b", repr(newline)
        assert escape_typst(value) == "a b", repr(newline)


# --- Real compiles: the test that matters is the property, not the two characters a
# bug report happened to name. Guarded rather than required: nothing in this project
# has ever shelled out to an external binary before this module, so there is no
# existing convention to follow, and this repository's own CI does not install Pandoc
# or Typst today. Skipping cleanly when either (or `pdftotext`, used to read the
# compiled PDF back as text) is missing keeps the suite honest on a machine that lacks
# them, rather than failing for a reason that has nothing to do with this module. On a
# machine that DOES have them -- this one, Pandoc 3.8.2.1 and Typst 0.14.2 confirmed --
# every `subprocess.run` below uses `check=True`: a real compile error is a test
# failure, never something the skip is allowed to quietly absorb instead.

_MISSING_TOOLS = [
    tool for tool in ("pandoc", "typst", "pdftotext", "strings") if shutil.which(tool) is None
]
requires_real_compiler = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason=f"real Pandoc/Typst round-trip tests need these on PATH: {', '.join(_MISSING_TOOLS)}",
)


def _compile_markdown_paragraph_to_pdf_text(body_markdown: str, tmp_path: Path) -> str:
    """One line of Markdown body text through the real two-stage pipeline this
    project uses -- Pandoc's own Typst *writer* (`-t typst`), not `--pdf-engine`,
    which needs a font this host does not always resolve for Pandoc's default
    standalone template; unrelated to escaping, noted in the task report -- then
    `typst compile`, then `pdftotext` to read the rendered PDF back as text.
    """
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(body_markdown + "\n", encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    extracted = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    )
    return extracted.stdout


def _compile_typst_markup_to_pdf_text(markup: str, tmp_path: Path) -> str:
    """`markup` -- already-escaped Typst source, exactly as it would appear inside a
    raw ```{=typst} block -- straight through `typst compile`, no Pandoc involved:
    `escape_typst`'s own contract is that its output IS the final Typst source.
    """
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    typst_path.write_text(markup + "\n", encoding="utf-8")
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    extracted = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    )
    return extracted.stdout


@requires_real_compiler
def test_every_ascii_punctuation_character_survives_markdown_context_for_real(
    tmp_path: Path,
) -> None:
    payload = string.punctuation
    escaped = escape_markdown(payload)
    text = _compile_markdown_paragraph_to_pdf_text(f"X{escaped}Y", tmp_path)
    assert f"X{payload}Y" in text


@requires_real_compiler
def test_every_ascii_punctuation_character_survives_typst_context_for_real(
    tmp_path: Path,
) -> None:
    payload = string.punctuation
    escaped = escape_typst(payload)
    text = _compile_typst_markup_to_pdf_text(f"X{escaped}Y", tmp_path)
    assert f"X{payload}Y" in text


# --- Fix round 1, item 2, settled by compiling: escape_typst is correct for markup
# position but wrong for a Typst string-literal argument, and #link("...") is an
# obvious thing for a template author to write. Confirmed live below, both ways --
# the bug the reviewer found, and the fix.


def _compile_typst_markup_to_pdf_uris(markup: str, tmp_path: Path) -> list[str]:
    """Every `/URI (...)` link-annotation target Typst produces from raw `markup`,
    no Pandoc involved -- the string-literal-position sibling of
    `_compile_markdown_to_pdf_uris` below, which goes through Pandoc instead."""
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    typst_path.write_text(markup + "\n", encoding="utf-8")
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    raw = subprocess.run(
        ["strings", str(pdf_path)], check=True, capture_output=True, text=True
    ).stdout
    return [
        m.group(1).replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
        for m in _URI_RE.finditer(raw)
    ]


@requires_real_compiler
def test_for_real_escape_typst_in_string_literal_position_produces_a_dead_link(
    tmp_path: Path,
) -> None:
    # The bug exactly as the reviewer reported it: escape_typst (markup position's
    # escaper) applied to a #link(...) string argument turns a real URL into a
    # broken one -- confirmed by reading the compiled PDF's own link annotation,
    # not by asserting a string.
    real_url = "https://esempio.it"
    wrong = escape_typst(real_url)
    uris = _compile_typst_markup_to_pdf_uris(f'#link("{wrong}")[sito]', tmp_path)
    assert uris != [real_url]
    assert uris == [r"https\:\/\/esempio\.it"]


@requires_real_compiler
def test_for_real_escape_typst_string_makes_the_link_work(tmp_path: Path) -> None:
    real_url = "https://esempio.it"
    right = escape_typst_string(real_url)
    uris = _compile_typst_markup_to_pdf_uris(f'#link("{right}")[sito]', tmp_path)
    assert uris == [real_url]


@requires_real_compiler
def test_for_real_escape_typst_in_string_literal_position_leaves_visible_backslashes(
    tmp_path: Path,
) -> None:
    hostile = "Rossi & C."
    wrong = escape_typst(hostile)
    text = _compile_typst_markup_to_pdf_text(f'#text("{wrong}")', tmp_path)
    assert hostile not in text
    assert wrong in text  # the backslashes survive, visibly, exactly as reported


@requires_real_compiler
def test_for_real_escape_typst_string_renders_the_clean_value(tmp_path: Path) -> None:
    hostile = "Rossi & C."
    right = escape_typst_string(hostile)
    text = _compile_typst_markup_to_pdf_text(f'#text("{right}")', tmp_path)
    assert hostile in text


@requires_real_compiler
def test_for_real_escape_typst_string_prevents_breaking_out_of_the_string(
    tmp_path: Path,
) -> None:
    hostile = 'x") #import("/etc/passwd") #text("'
    escaped = escape_typst_string(hostile)
    text = _compile_typst_markup_to_pdf_text(f'#text("{escaped}")', tmp_path)
    # The whole hostile string reads back as inert literal text -- no second
    # function call, no attempted import, nothing executed.
    assert hostile in text


@requires_real_compiler
def test_c1_slash_no_longer_opens_a_typst_line_comment(tmp_path: Path) -> None:
    # The first Critical: escape_typst did not escape "/", and "//" is a Typst line
    # comment. A customer name of "Rossi // nota" used to delete the rest of the
    # line -- here, a VAT clause -- from a real, cleanly compiled PDF, no error.
    hostile = "Rossi // nota"
    escaped = escape_typst(hostile)
    markup = f"#block[\n  Cliente: {escaped}\n  Validita 15 giorni\n]\n"
    text = _compile_typst_markup_to_pdf_text(markup, tmp_path)
    assert hostile in text
    assert "Validita 15 giorni" in text


@requires_real_compiler
def test_c2_at_sign_no_longer_becomes_a_pandoc_citation(tmp_path: Path) -> None:
    # The mirror Critical: escape_markdown did not escape "@", and Pandoc reads an
    # unescaped "@word" as a citation -- the build used to die with "the document
    # does not contain a bibliography" instead of ever producing a PDF at all.
    hostile = "Contatto @rossi2024 urgente"
    escaped = escape_markdown(hostile)
    text = _compile_markdown_paragraph_to_pdf_text(escaped, tmp_path)
    assert hostile in text


@requires_real_compiler
def test_hash_at_paragraph_start_does_not_become_a_heading(tmp_path: Path) -> None:
    escaped = escape_markdown("# Titolo Iniettato")
    text = _compile_markdown_paragraph_to_pdf_text(escaped, tmp_path)
    assert "# Titolo Iniettato" in text


@requires_real_compiler
def test_u2028_no_longer_manufactures_a_heading_in_markdown_context(tmp_path: Path) -> None:
    hostile = "Rossi" + chr(0x2028) + "= TITOLO INIETTATO"
    escaped = escape_markdown(hostile)
    text = _compile_markdown_paragraph_to_pdf_text(escaped, tmp_path)
    assert "Rossi = TITOLO INIETTATO" in text


@requires_real_compiler
def test_u2028_no_longer_manufactures_a_heading_in_typst_context(tmp_path: Path) -> None:
    hostile = "Rossi" + chr(0x2028) + "= TITOLO INIETTATO"
    escaped = escape_typst(hostile)
    text = _compile_typst_markup_to_pdf_text(f"#block[{escaped}]", tmp_path)
    assert "Rossi = TITOLO INIETTATO" in text


@requires_real_compiler
def test_the_render_pipeline_closes_the_residual_dash_and_ellipsis_gap(
    tmp_path: Path,
) -> None:
    """Was `test_known_limitation_typst_own_ligature_still_alters_repeated_dash_in_
    markdown`, which pinned the *un*fixed behaviour: `escape_markdown`'s escaping
    never reaches past Pandoc's own Typst *writer*, which re-serialises parsed text
    on its own rules and does not defend a lone "-" or "." the way it defends "$" or
    "~". Two or three of them survive Pandoc's round trip as separate, individually
    harmless characters and only become adjacent again in Typst's own compiled
    input, where Typst's lexer -- independent of Pandoc -- merges a run of two-or-
    three hyphens into an en/em dash and a run of three dots into an ellipsis.
    `escape_typst` does not share this problem: see
    test_every_ascii_punctuation_character_survives_typst_context_for_real above,
    where an escaped "-" reaches Typst already protected because nothing
    re-serialises `escape_typst`'s output.

    This module still cannot close that gap -- the paragraph above, and the module
    docstring, both remain true: `escape_markdown`'s own output is exactly what it
    always was, and no escaper change is involved here. What changed is
    `pigrocrm.core.render.pdf`, which owns the intermediate .typ file Pandoc
    produces and re-escapes any bare hyphen/dot run still sitting in it before
    Typst ever compiles it (`_defeat_typst_autotypography`) -- the only point in the
    pipeline where a bare run and a `\\`-escaped one are still distinguishable byte
    sequences. Verified here through the real two-stage `render_pdf` pipeline, not
    the ad-hoc single-stage compile the other tests in this file use, because that
    is specifically the stage the fix lives in.
    """
    from pigrocrm.core.config import Settings
    from pigrocrm.core.render.pdf import build_header, render_pdf
    from pigrocrm.core.templates.scope import LEGAL_ENTITY_KEYS, italian_keys

    escaped = escape_markdown("Rossi -- Bianchi ... Fine --- capo")
    settings = Settings(jwt_secret="x" * 32)
    header = build_header(
        italian_keys(
            {
                "legal_name": "Test",
                "vat_number": "1",
                "email": "a@b.it",
                "phone": "1",
                "certified_email": "a@b.it",
                "website": "b.it",
                "address": "via Test 1",
                "cap": "00000",
                "city": "Roma",
                "province": "RM",
            },
            LEGAL_ENTITY_KEYS,
        )
    )
    pdf_bytes = render_pdf(f"{escaped}\n", header_typst=header, settings=settings)
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(pdf_bytes)
    extracted = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"], check=True, capture_output=True, text=True
    ).stdout
    # Literal, not an en dash / em dash / ellipsis: the residual is closed.
    assert "Rossi -- Bianchi ... Fine --- capo" in extracted
    assert "–" not in extracted
    assert "—" not in extracted
    assert "…" not in extracted


# --- Fix round 1, item 1: the url context. A unit test asserts a string; it cannot
# tell you what a PDF viewer would actually navigate to if you clicked the link.
# Typst's PDF writer does not compress the objects these fixtures produce, so the
# link annotation's own "/URI (...)" entry is readable straight out of the PDF's
# bytes with `strings` -- confirmed once by hand in the scratchpad before writing
# this helper, the same way every other claim in this file is settled by compiling
# something real rather than by trusting the grammar on paper.

_URI_RE = re.compile(r"/URI\s*\(((?:\\.|[^()\\])*)\)")


def _compile_markdown_to_pdf_uris(body_markdown: str, tmp_path: Path) -> list[str]:
    """Every `/URI (...)` link-annotation target in the PDF Pandoc+Typst produce
    from `body_markdown`, in the order `strings` finds them in the file, with the
    PDF's own backslash-escapes for `(`, `)` and `\\` undone."""
    md_path = tmp_path / "doc.md"
    typst_path = tmp_path / "doc.typst"
    pdf_path = tmp_path / "doc.pdf"
    md_path.write_text(body_markdown + "\n", encoding="utf-8")
    subprocess.run(["pandoc", str(md_path), "-t", "typst", "-o", str(typst_path)], check=True)
    subprocess.run(["typst", "compile", str(typst_path), str(pdf_path)], check=True)
    raw = subprocess.run(
        ["strings", str(pdf_path)], check=True, capture_output=True, text=True
    ).stdout
    return [
        m.group(1).replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
        for m in _URI_RE.finditer(raw)
    ]


@requires_real_compiler
def test_url_context_makes_the_customers_own_site_a_working_link_for_real(
    tmp_path: Path,
) -> None:
    # The brief's own flagship example, and the bug this round's fix closes: before
    # it, this produced a PDF whose link annotation was the literal string
    # "https%3A%2F%2Fesempio.it%2Fa%3Fb%3Dc" -- a broken, relative-looking path, not
    # the customer's site.
    real_url = "https://esempio.it/a?b=c"
    escaped = escape_url(real_url)
    body = f"vedi [il sito]({escaped}) fine"
    uris = _compile_markdown_to_pdf_uris(body, tmp_path)
    assert uris == [real_url]
    text = _compile_markdown_paragraph_to_pdf_text(body, tmp_path)
    assert "fine" in text


@requires_real_compiler
def test_url_context_a_non_absolute_value_is_a_safe_but_inert_destination_for_real(
    tmp_path: Path,
) -> None:
    # A schemeless value ("esempio.it", plausible real customer data for a field
    # named "sito web") is not guessed into a link -- it becomes the same opaque,
    # percent-encoded destination as before this fix, which still compiles and still
    # cannot be mistaken for the customer's real, unentered URL.
    non_url = "esempio.it"
    escaped = escape_url(non_url)
    body = f"vedi [il sito]({escaped}) fine"
    uris = _compile_markdown_to_pdf_uris(body, tmp_path)
    assert uris == [quote(non_url, safe="")]
    text = _compile_markdown_paragraph_to_pdf_text(body, tmp_path)
    assert "fine" in text


@requires_real_compiler
def test_url_context_a_trusted_url_with_parens_and_brackets_does_not_break_the_link_for_real(
    tmp_path: Path,
) -> None:
    # No whitespace, so this reaches the angle-bracket branch -- and a raw ")" and a
    # "](" are both completely inert there, unlike in the bare destination form: one
    # link annotation, "fine" still its own separate text, not a destination that
    # ended early or a second, unintended link.
    #
    # "]" itself comes back as "%5D" in the annotation -- confirmed by hand in the
    # scratchpad to happen identically for a hand-written destination with no
    # escaping involved at all, e.g. "[x](<https://e.it/a]b>)". That is Pandoc's own
    # writer normalising a character URIs treat as reserved (used for IPv6 literal
    # hosts, "[::1]"), on every link it emits regardless of source -- not something
    # escape_url does or could turn off, and not a gap this fix needs to close: the
    # value still ends up exactly as inert-or-real as escape_url decided, just with
    # Pandoc's own unrelated normalisation applied on top, same as it would be for a
    # template author's own hand-typed link.
    hostile_but_trusted = "https://esempio.it/a)b](c"
    escaped = escape_url(hostile_but_trusted)
    body = f"vedi [il sito]({escaped}) fine"
    uris = _compile_markdown_to_pdf_uris(body, tmp_path)
    assert uris == ["https://esempio.it/a)b%5D(c"]
    text = _compile_markdown_paragraph_to_pdf_text(body, tmp_path)
    assert "fine" in text


@requires_real_compiler
def test_url_context_a_hostile_non_url_value_does_not_break_the_document_for_real(
    tmp_path: Path,
) -> None:
    # Space, closing paren, newline and "](" all at once, in a value with no http(s)
    # scheme at all -- the percent-encoding fallback path. The document must still
    # compile as ONE link followed by "fine", not have "fine" swallowed into the
    # destination or turned into a second, unintended link.
    hostile = "non e' un url) ]( con spazi\ne newline"
    escaped = escape_url(hostile)
    assert not escaped.startswith("<")
    body = f"vedi [il sito]({escaped}) fine"
    uris = _compile_markdown_to_pdf_uris(body, tmp_path)
    assert uris == [quote(hostile, safe="")]
    text = _compile_markdown_paragraph_to_pdf_text(body, tmp_path)
    assert "fine" in text


@requires_real_compiler
def test_url_context_two_spellings_of_the_same_intent_now_agree_for_real(
    tmp_path: Path,
) -> None:
    # The brief's own two spellings of "link to the customer's site" -- inline
    # destination vs. a reference-style link, which never goes through escape_url at
    # all, only escape_markdown -- used to disagree (one worked, one produced a
    # broken relative path). They now both produce the same working link.
    real_url = "https://esempio.it/a?b=c"
    inline = f"vedi [il sito]({escape_url(real_url)}) fine"
    reference = f"vedi [il sito][r] fine\n\n[r]: {escape_markdown(real_url)}\n"
    assert _compile_markdown_to_pdf_uris(inline, tmp_path) == [real_url]
    assert _compile_markdown_to_pdf_uris(reference, tmp_path) == [real_url]


# --- the xml context (slice 3) ---------------------------------------------


def test_xml_context_returns_the_domain_value_untouched() -> None:
    """The tree serialiser is the one and only escaping pass. The previous system put a literal
    backslash into an Agenzia delle Entrate record by running a value through
    escapeTypstText and then escapeXml; there is no code path here that can stack
    two escapers, because this one substitutes nothing."""
    hostile = 'Rossi & C. <IdCodice>999</IdCodice> "#$@\\ ]]>'
    assert escape_xml(hostile) == hostile
    assert escape_for("xml", hostile) == hostile


@pytest.mark.parametrize(
    "forbidden",
    [
        "\x00",  # NUL, also refused by SafeStr upstream
        "\x01",
        "\x08",
        "\x0b",  # vertical tab
        "\x0c",  # form feed
        "\x1f",
        "￾",  # non-character
        "￿",  # non-character
        "\ud800",  # a lone surrogate, reachable in a Python str
    ],
)
def test_xml_context_refuses_a_code_point_xml_cannot_represent(forbidden: str) -> None:
    with pytest.raises(ValueError, match="non rappresentabile in XML"):
        escape_xml(f"Rossi{forbidden}C.")


@pytest.mark.parametrize("allowed", ["\t", "\n", "\r", "à", "€", "𝄞"])
def test_xml_context_allows_every_code_point_xml_1_0_permits(allowed: str) -> None:
    assert escape_xml(f"a{allowed}b") == f"a{allowed}b"


def test_xml_is_a_declared_render_context() -> None:
    assert "xml" in get_args(RenderContext)


def test_plain_context_leaves_punctuation_alone() -> None:
    """The reminder body is a `text/plain` MIME part read by a person: nothing
    downstream re-parses it, so there is no syntax to escape out of, and every
    backslash the markdown escaper adds is damage a paying client can see. The
    contrast with `escape_markdown` on the same value is the whole point."""
    invoice_line = "Fattura 2026/14 - 1.200,00 €"
    assert escape_plain(invoice_line) == invoice_line
    assert escape_markdown(invoice_line) != invoice_line
    assert escape_for("plain", invoice_line) == invoice_line


def test_plain_context_keeps_the_line_breaks_a_signature_block_is_made_of() -> None:
    """Every other escaper here collapses line terminators, because its value lands
    inside one syntactic unit -- a table cell, a header field -- that a stray break
    would restructure. A plain-text body *is* lines."""
    signature = "Mario Rossi\nConsulente"
    assert escape_plain(signature) == signature
    assert "\n" not in escape_markdown(signature)


def test_plain_context_still_refuses_a_nul_byte() -> None:
    with pytest.raises(ValueError, match="carattere nullo"):
        escape_plain("Mario\x00Rossi")


def test_plain_is_a_declared_render_context() -> None:
    assert "plain" in get_args(RenderContext)
