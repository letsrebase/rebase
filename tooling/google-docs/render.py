# /// script
# requires-python = ">=3.12"
# dependencies = ["python-docx==1.2.0"]
# ///
"""Render a Markdown file into a .docx in rebase's styles, ready for Google Docs.

    uv run tooling/google-docs/render.py doc.md            # writes doc.docx beside it
    uv run tooling/google-docs/render.py doc.md -o out.docx

pandoc does the conversion with `reference.docx` as its style source, so headings,
body, captions and the header with the logo come from there. Tables are styled here,
after pandoc, because Google Docs keeps a cell's own shading and borders on import but
not a Word table style (observed 2026-11-16): the header row in Prussian Blue with white
text, a hairline under every other row, the full width of the text block. Pictures are
whatever the Markdown embeds; a figure drawn in the brand's face is `figure.py`'s job.

The YAML block at the top of the Markdown sets `title`, `subtitle` and `date`, which
pandoc writes in the Title, Subtitle and Date styles. The dashes of a pipe table's
separator row always set its column widths (pandoc runs with `--columns=1`).
"""

import argparse
import subprocess
import sys
from pathlib import Path

from brand_palette import palette
from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from ooxml import RPR, TBLPR, TCMAR, TCPR, place

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "reference.docx"
WHITE = "FFFFFF"  # the header row's text; white is not a palette token
TABLE_TEXT = Pt(9.5)


def _borders(tbl_pr, rule: str) -> None:
    """No outer frame and no verticals: a hairline between rows only."""
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideV"):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    inside = OxmlElement("w:insideH")
    inside.set(qn("w:val"), "single")
    inside.set(qn("w:sz"), "4")
    inside.set(qn("w:color"), rule)
    borders.append(inside)
    place(tbl_pr, borders, TBLPR)


def _shade(tc_pr, fill: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    place(tc_pr, shd, TCPR)


def _margins(tc_pr) -> None:
    mar = OxmlElement("w:tcMar")
    for side, twips in (("top", 70), ("left", 110), ("bottom", 70), ("right", 110)):
        el = OxmlElement(f"w:{side}")
        el.set(qn("w:w"), str(twips))
        el.set(qn("w:type"), "dxa")
        place(mar, el, TCMAR)
    place(tc_pr, mar, TCPR)


def _width(parent, order, twips: int, tag: str) -> None:
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:type"), "dxa")
    el.set(qn("w:w"), str(twips))
    place(parent, el, order)


def _full_width(table, usable: int) -> None:
    """The text block's width in twips, split as pandoc split the columns.

    Google Docs ignores a percentage width on import (observed 2026-11-16), so the table
    and every column get an absolute one, scaled from pandoc's own grid.
    """
    tbl = table._tbl
    _width(tbl.tblPr, TBLPR, usable, "tblW")
    columns = tbl.tblGrid.findall(qn("w:gridCol"))
    given = [int(col.get(qn("w:w")) or 0) for col in columns]
    total = sum(given) or len(columns)
    widths = [round(usable * (w or 1) / total) for w in given]
    for col, w in zip(columns, widths, strict=True):
        col.set(qn("w:w"), str(w))
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            twips = widths[min(index, len(widths) - 1)]
            _width(cell._tc.get_or_add_tcPr(), TCPR, twips, "tcW")


def _is_header(row) -> bool:
    """pandoc marks a pipe table's header row as repeating; a headerless table has none."""
    tr_pr = row._tr.trPr
    return tr_pr is not None and tr_pr.find(qn("w:tblHeader")) is not None


def _runs(paragraph):
    """Every run of a paragraph, the ones inside a hyperlink included."""
    return paragraph._p.iter(qn("w:r"))


def _run_style(run, *, bold: bool, colour: str | None) -> None:
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        run.insert(0, rpr)
    if bold:
        place(rpr, OxmlElement("w:b"), RPR)
    if colour:
        el = OxmlElement("w:color")
        el.set(qn("w:val"), colour)
        place(rpr, el, RPR)
    size = OxmlElement("w:sz")
    size.set(qn("w:val"), str(int(TABLE_TEXT.pt * 2)))
    place(rpr, size, RPR)


def style_tables(doc: DocumentObject, colours: dict[str, str]) -> None:
    ink = colours["prussian-blue"]
    rule = colours["charcoal-blue"]
    section = doc.sections[0]
    usable = int((section.page_width - section.left_margin - section.right_margin) / 635)
    for table in doc.tables:
        _full_width(table, usable)
        _borders(table._tbl.tblPr, rule)
        for row in table.rows:
            header = _is_header(row)
            for cell in row.cells:
                tc_pr = cell._tc.get_or_add_tcPr()
                if header:
                    _shade(tc_pr, ink)
                _margins(tc_pr)
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
                    for run in _runs(paragraph):
                        _run_style(run, bold=header, colour=WHITE if header else None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    output = (args.output or source.with_suffix(".docx")).resolve()
    if not REFERENCE.exists():
        sys.exit("reference.docx is missing: run build_reference.py first")
    subprocess.run(
        [
            "pandoc",
            str(source),
            "--from=markdown+pipe_tables+yaml_metadata_block+implicit_figures",
            "--to=docx",
            "--columns=1",
            f"--reference-doc={REFERENCE}",
            f"--resource-path={source.parent}",
            "-o",
            str(output),
        ],
        check=True,
    )
    doc = Document(str(output))
    style_tables(doc, palette())
    doc.save(str(output))
    print(output)


if __name__ == "__main__":
    main()
