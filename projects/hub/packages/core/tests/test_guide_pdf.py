"""The guide a member downloads, and the three things that can quietly stop being true.

It is a built artefact that is committed: `../../../tools/build_guide_pdf.py` typesets
the Markdown in `../../../content/` with pandoc and Typst, and its docstring says why
neither the API image nor CI does that for itself. The cost of committing it is that the
bytes can fall behind their sources, so `tools/guide-pdf.lock.json` records the SHA-256
of every input and of the output, this file fails when any of them moved, and the
script's own `--check` in preflight rebuilds the PDF and compares it byte for byte on a
machine that has the two binaries. Nothing here needs either of them, which is why it
runs in the hub's own gate on every pull request.
"""

import hashlib
import json
import re
from pathlib import Path

from rebase_core.perks import GUIDE_PATH, guide_bytes

REPO = Path(__file__).resolve().parents[5]
LOCK = json.loads((REPO / "projects/hub/tools/guide-pdf.lock.json").read_text(encoding="utf-8"))
PDF = guide_bytes()

# ORB-70's budget. The measured file is two orders of magnitude under it, so this
# catches a typeface that stopped being subset, never a paragraph.
SIZE_BUDGET = 2 * 1024 * 1024


def test_the_file_is_the_one_its_lock_describes() -> None:
    assert str(GUIDE_PATH.relative_to(REPO)) == LOCK["output"]
    assert GUIDE_PATH.name == Path(LOCK["output"]).name
    assert len(PDF) == LOCK["bytes"]
    assert hashlib.sha256(PDF).hexdigest() == LOCK["sha256"]


def test_it_was_built_from_the_sources_in_the_tree_right_now() -> None:
    """The assertion that earns this file's existence.

    Editing the Markdown, the Typst template, the palette or the typeface without
    regenerating leaves a download that says something the repository no longer does,
    and nothing else would notice.
    """
    assert len(LOCK["sources"]) >= 5
    for name, digest in LOCK["sources"].items():
        path = REPO / name
        assert path.is_file(), f"{name} is in the lock and not in the tree"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
            f"{name} changed without a regeneration. Run "
            "`uv run python projects/hub/tools/build_guide_pdf.py` and commit the result."
        )


def test_it_is_a_small_pdf_of_the_pages_the_member_area_promises() -> None:
    assert PDF.startswith(b"%PDF-")
    assert len(PDF) < SIZE_BUDGET
    # The member area shows this number beside the link, reading it from the lock, so a
    # guide that grows by a page changes what the card says rather than lying.
    assert LOCK["pages"] == len(re.findall(rb"/Type\s*/Page[^s]", PDF))
    assert LOCK["pages"] > 1


def test_every_link_in_it_is_absolute() -> None:
    """A PDF has no origin, so a root-relative href in the Markdown would resolve to
    nothing in a reader. The generator rewrites them; this is the half that fails if it
    stops."""
    uris = re.findall(rb"/URI\s*\((.*?)\)", PDF)
    assert uris
    for uri in uris:
        assert uri.startswith(b"https://letsrebase.com"), uri
