import shutil
from pathlib import Path

import pytest

from pigrocrm.core.config import Settings
from pigrocrm.core.errors import ValidationFailed
from pigrocrm.core.render.pdf import (
    ASSETS_DIR,
    BLANK_PNG,
    MEDIA_LOGO_PNG,
    MEDIA_LOGO_SVG,
    build_header,
    render_pdf,
)
from pigrocrm.core.templates.renderer import render_template

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("typst") is None,
    reason="pandoc e typst vivono nell'immagine dell'API (Dockerfile.api)",
)

SETTINGS = Settings(jwt_secret="x" * 32)
PROFILE = {
    "ragione_sociale": "Studio Rossi",
    "partita_iva": "01234567890",
    "email": "mario@example.com",
    "telefono": "+39 02 1234567",
    "pec": "studiorossi@pec.it",
    "sito_web": "www.example.com",
    "indirizzo": "Via Roma 1",
    "cap": "20053",
    "comune": "Milano",
    "provincia": "MI",
}


def test_a_minimal_document_renders_to_a_pdf() -> None:
    pdf = render_pdf("# Titolo\n\nCorpo.\n", header_typst=build_header(PROFILE), settings=SETTINGS)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_the_emitter_profile_reaches_the_header() -> None:
    header = build_header(PROFILE)
    assert "Studio Rossi" in header
    assert "01234567890" in header
    # The `@` in an email is Typst syntax for a reference and must arrive escaped.
    # The brief's own sample asserted `r"mario\@example.com"` (dot bare) --
    # wrong: `build_header` escapes every ASCII punctuation character, "." included
    # (the same rule `escape_markdown`/`escape_typst` apply everywhere else in this
    # project), so the dot is escaped too. Fixed here rather than in the escaper,
    # since the escaper's behaviour is correct and consistent; the sample assertion
    # was not.
    assert r"mario\@example\.com" in header


def test_a_broken_raw_typst_block_names_the_template_line() -> None:
    markdown = "Testo\n\n```{=typst}\n// pigrocrm:line=7\n#nonesiste(\n```\n"
    with pytest.raises(ValidationFailed) as excinfo:
        render_pdf(markdown, header_typst=build_header(PROFILE), settings=SETTINGS)
    assert "riga 7" in excinfo.value.details["reason"]


def test_the_error_never_contains_the_temporary_directory() -> None:
    markdown = "```{=typst}\n// pigrocrm:line=3\n#nonesiste(\n```\n"
    with pytest.raises(ValidationFailed) as excinfo:
        render_pdf(markdown, header_typst=build_header(PROFILE), settings=SETTINGS)
    assert "/tmp/" not in excinfo.value.details["reason"]


def test_no_temporary_directory_survives_a_success_or_a_failure(tmp_path: Path) -> None:
    # Rendered under a directory only this test can see, not the system temp
    # directory: that one is shared by every xdist worker, and another worker's
    # in-flight `pigrocrm-render-*` there is indistinguishable from a directory this
    # render failed to remove. Observed on 2026-09-09 as
    # `assert {'pigrocrm-render-05zt78ya'} == set()`, 1 failed of 4083, with the file
    # passing alone (ORB-40).
    render_pdf("ok\n", header_typst=build_header(PROFILE), settings=SETTINGS, temp_root=tmp_path)
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ValidationFailed):
        render_pdf(
            "```{=typst}\n// pigrocrm:line=2\n#nonesiste(\n```\n",
            header_typst=build_header(PROFILE),
            settings=SETTINGS,
            temp_root=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_the_render_directory_is_created_under_the_root_it_is_given(tmp_path: Path) -> None:
    # The assertion above is only worth something if `temp_root` is honoured rather
    # than silently replaced by the system temp directory. `mkdtemp` refuses a parent
    # that does not exist, so a missing root failing loudly is the proof, and it fails
    # before anything is written that would then need cleaning up.
    with pytest.raises(FileNotFoundError):
        render_pdf(
            "ok\n",
            header_typst=build_header(PROFILE),
            settings=SETTINGS,
            temp_root=tmp_path / "absent",
        )
    assert list(tmp_path.iterdir()) == []


def test_the_bundle_ships_nobody_s_logo_and_a_seeded_signature_reference_still_compiles() -> None:
    """REB-48 called shipping one person's assets a defect; since REB-627 the bundle has
    no `media/` at all, and a job that is handed no signature gets a transparent pixel
    under the name the seeded offer template references."""
    assert not (ASSETS_DIR / "media").exists()
    pdf = render_pdf(
        "![](./media/sign_is.png){ width=90pt }\n",
        header_typst=build_header(PROFILE),
        settings=SETTINGS,
    )
    assert pdf.startswith(b"%PDF")


def test_the_header_draws_the_logo_the_job_carries_or_the_name_in_type() -> None:
    without = build_header(PROFILE)
    assert "#image(" not in without
    assert 'weight: "bold")[Studio Rossi]' in without
    with_png = build_header(PROFILE, logo=MEDIA_LOGO_PNG)
    assert '#image("./media/logo.png"' in with_png and "bold" not in with_png
    with_svg = build_header(PROFILE, logo=MEDIA_LOGO_SVG)
    assert '#image("./media/logo.svg"' in with_svg
    # And each of the three compiles, with the matching file in the job.
    assert render_pdf("ok\n", header_typst=without, settings=SETTINGS).startswith(b"%PDF")
    assert render_pdf(
        "ok\n", header_typst=with_png, settings=SETTINGS, media={MEDIA_LOGO_PNG: BLANK_PNG}
    ).startswith(b"%PDF")
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<rect width="10" height="10"/></svg>'
    )
    assert render_pdf(
        "ok\n", header_typst=with_svg, settings=SETTINGS, media={MEDIA_LOGO_SVG: svg}
    ).startswith(b"%PDF")


def test_a_render_takes_only_the_media_names_it_knows() -> None:
    with pytest.raises(ValueError):
        render_pdf(
            "ok\n", header_typst=build_header(PROFILE), settings=SETTINGS, media={"x.png": b""}
        )


def test_a_value_that_looks_like_a_shell_argument_is_just_text() -> None:
    # No user input ever reaches a command line: the value travels in the source file.
    pdf = render_pdf(
        "Cliente: `--output=/etc/passwd`\n",
        header_typst=build_header(PROFILE),
        settings=SETTINGS,
    )
    assert pdf.startswith(b"%PDF")


def test_the_compile_root_cannot_read_outside_the_render_directory() -> None:
    """Spec risk #2, verified for real rather than argued: a wide `--root` let
    `#read("/etc/passwd")` succeed and embed the real file's contents in the PDF
    (measured, see the task brief). `--root` here is the render's own throwaway
    directory, which contains nothing but this document's own source and `media/`,
    so the same expression must fail instead -- Typst reports "file not found",
    searched only under the render's own directory, never the real file's contents.

    Also pins a second finding from the same test, not named in the brief: Typst's
    own error message put the render's absolute temporary directory path directly in
    its *message* text ("file not found (searched at <workdir>/etc/passwd)"), not
    only in the "┌─ file:line:col" location line `translate_typst_failure` already
    strips -- confirmed live before `render_pdf` scrubbed it. `riga 1` still shows up
    because the marker mapping and the path scrubbing are independent fixes.
    """
    markdown = '```{=typst}\n// pigrocrm:line=1\n#read("/etc/passwd")\n```\n'
    with pytest.raises(ValidationFailed) as excinfo:
        render_pdf(markdown, header_typst=build_header(PROFILE), settings=SETTINGS)
    reason = excinfo.value.details["reason"]
    # "/etc/passwd" surviving here is not a leak: it is the *sandboxed* search path
    # the hostile expression itself asked for, relative to `--root`, echoed back
    # exactly as a real "file not found" would be for any other missing path -- not
    # the real `/etc/passwd`'s contents, and not proof the real file was ever opened.
    assert "file not found" in reason
    # What must never survive is the render's own absolute temp-directory path --
    # `pigrocrm-render-<random>` is that directory's own distinctive name prefix
    # (see `render_pdf`'s `tempfile.mkdtemp(prefix=...)`), so its absence here is
    # the actual assertion this test exists for.
    assert "pigrocrm-render-" not in reason
    assert "riga 1" in reason


def test_the_reported_line_survives_an_if_inside_an_each_inside_a_fence() -> None:
    """Spec risk #3, end to end and several constructs deep, not just the flat case
    the diagnostics unit tests already cover with a hand-built .typ fixture. The
    marker on the fence's opening line alone would put every error inside a loop
    body at the *same* template line regardless of where in the body it actually
    is; `templates/parser.py`'s re-anchoring after `#each`/`#if` (`_resume_marker`)
    is what keeps template line 9 -- and not line 6, the fence's own opening line,
    or some multiple of it once the loop runs -- attached to the real syntax error
    on that exact line.
    """
    source = (
        "Intro\n"  # 1
        "\n"  # 2
        "{{#each offerta.righe}}\n"  # 3
        "{{#if attivo}}\n"  # 4
        "```{=typst}\n"  # 5
        "#table(\n"  # 6
        "  [ok],\n"  # 7
        ")\n"  # 8
        "#nonesiste(\n"  # 9 <- the deliberate error
        "```\n"  # 10
        "{{/if}}\n"  # 11
        "{{/each}}\n"  # 12
    )
    compiled = render_template(source, {"offerta": {"righe": [{"attivo": True}]}})
    with pytest.raises(ValidationFailed) as excinfo:
        render_pdf(compiled, header_typst=build_header(PROFILE), settings=SETTINGS)
    assert "riga 9" in excinfo.value.details["reason"]
