"""Turn a Typst compiler error into a template line number, shown to the user as
"riga N del template".

Spec 6: when Typst fails, the error that goes back to the user contains the line of
the template that caused it, not the compiler's raw stderr.

The mechanism is arithmetic, not guesswork. `templates/parser.py` inserts
`// pigrocrm:line=<N>` as the first line inside every raw ```{=typst} block. Pandoc
copies a raw block through **verbatim, line for line**, so that comment survives into
the intermediate .typ at a known offset. Given an error at .typ line L, the nearest
preceding marker at .typ line M naming template line N puts the offending template
line at exactly `N + (L - M - 1)`.
"""

import re

from pigrocrm.core.templates.renderer import TYPST_LINE_MARKER_PREFIX

# Typst 0.11 renders a diagnostic as:
#     error: unexpected keyword `import`
#       ┌─ /path/to/intermediate.typ:6:4
_ERROR_RE = re.compile(r"^error: (?P<message>.+)$", re.MULTILINE)
_LOCATION_RE = re.compile(r"┌─\s*\S+?:(?P<line>\d+):(?P<column>\d+)")
_MARKER_RE = re.compile(rf"^\s*{re.escape(TYPST_LINE_MARKER_PREFIX)}(?P<line>\d+)\s*$")


def template_line_for(typst_source: str, typst_line: int) -> int | None:
    """The template line that produced line `typst_line` of the intermediate .typ."""
    lines = typst_source.splitlines()
    for index in range(min(typst_line, len(lines)) - 1, -1, -1):
        match = _MARKER_RE.match(lines[index])
        if match:
            marker_typst_line = index + 1
            return int(match.group("line")) + (typst_line - marker_typst_line - 1)
    return None


def translate_typst_failure(stderr: str, typst_source: str) -> str:
    """A message for a human, never the compiler's raw output.

    The temporary path in Typst's own location line is dropped: it names a directory
    on the server, is different on every render, and tells the user nothing. The raw
    stderr belongs in the server log, which is where the caller of `render_pdf` puts
    it.
    """
    error = _ERROR_RE.search(stderr)
    message = error.group("message").strip() if error else ""
    location = _LOCATION_RE.search(stderr)
    if location:
        line = template_line_for(typst_source, int(location.group("line")))
        if line is not None:
            detail = message or "sintassi Typst non valida"
            return f"errore nella riga {line} del template: {detail}"
    if message:
        return f"errore di composizione del PDF: {message}"
    return "errore di composizione del PDF: la compilazione Typst e' fallita senza dettagli"
