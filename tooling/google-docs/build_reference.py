# /// script
# requires-python = ">=3.12"
# dependencies = ["python-docx==1.2.0"]
# ///
"""Build `reference.docx`, the styles every rebase Google Doc is rendered with.

Run by hand when the brand changes: `uv run tooling/google-docs/build_reference.py`.
It starts from pandoc's own reference document, so every style pandoc writes exists,
and restyles it from `shared/brand`: the colours are read from `palette.css`, the face
is Outfit (a Google Font, so Google Docs shows it after the import), and the header
carries the echo logo at document size. No palette colour is restated here.
"""

import io
import subprocess
from pathlib import Path

from brand_palette import BRAND, palette
from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from ooxml import PPR, place

HERE = Path(__file__).resolve().parent
OUT = HERE / "reference.docx"
LOGO = BRAND / "echo" / "echo-ink-watermelon-outlines-260.png"
FONT = "Outfit"
MONO = "JetBrains Mono"
MONO_STYLES = ("Source Code", "Verbatim Char")
FOOTER_TEXT = "rebase · letsrebase.com"


def set_font(style, name: str) -> None:
    """Every script slot, so Word and Docs agree on the face, and no theme font."""
    rpr = style.element.get_or_add_rPr()
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for key in list(fonts.attrib):
        del fonts.attrib[key]
    for slot in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(slot), name)


def set_colour(style, colour: RGBColor) -> None:
    """A literal colour, dropping the theme colour pandoc's styles carry."""
    style.font.color.rgb = colour
    found = style.element.get_or_add_rPr().find(qn("w:color"))
    for attr in ("w:themeColor", "w:themeShade", "w:themeTint"):
        if found is not None and found.get(qn(attr)) is not None:
            del found.attrib[qn(attr)]


def restyle(doc: DocumentObject, colours: dict[str, RGBColor]) -> None:
    ink = colours["prussian-blue"]
    muted = colours["charcoal-blue"]
    accent = colours["watermelon-deep"]
    styles = {style.name: style for style in doc.styles}

    # Outfit everywhere first, the character styles linked to headings included.
    for name, style in styles.items():
        if style.type in (1, 2) and name not in MONO_STYLES:
            set_font(style, FONT)

    # (size pt, bold, colour, space before pt, space after pt)
    text_styles = {
        "Normal": (10.5, False, ink, 0, 6),
        "Body Text": (10.5, False, ink, 0, 6),
        "First Paragraph": (10.5, False, ink, 0, 6),
        "Compact": (10.5, False, ink, 0, 2),
        "Block Text": (10.5, False, muted, 6, 6),
        "Title": (26, True, ink, 0, 4),
        "Subtitle": (13, False, muted, 0, 14),
        "Author": (10, False, muted, 0, 0),
        "Date": (10, False, muted, 0, 12),
        "Abstract": (11, False, muted, 6, 12),
        "Heading 1": (17, True, ink, 20, 6),
        "Heading 2": (13, True, accent, 14, 4),
        "Heading 3": (11.5, True, ink, 10, 2),
        "Caption": (9, False, muted, 2, 10),
        "Image Caption": (9, False, muted, 2, 10),
        "Table Caption": (9, False, muted, 8, 4),
        "Footnote Text": (8.5, False, muted, 0, 2),
    }
    for level in range(4, 10):
        text_styles[f"Heading {level}"] = (10.5, True, muted, 8, 2)
    for name, (size, bold, colour, before, after) in text_styles.items():
        style = styles.get(name)
        if style is None:
            continue
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.italic = False
        set_colour(style, colour)
        fmt = style.paragraph_format
        fmt.space_before = Pt(before)
        fmt.space_after = Pt(after)
        fmt.line_spacing = 1.15
        # Square and editorial, like the site: nothing is centred.
        fmt.alignment = WD_ALIGN_PARAGRAPH.LEFT
        linked = styles.get(f"{name} Char")
        if linked is not None:
            set_colour(linked, colour)

    # A quotation stands on a Watermelon rule, the way the site marks a quote.
    if "Block Text" in styles:
        border = OxmlElement("w:pBdr")
        left = OxmlElement("w:left")
        left.set(qn("w:val"), "single")
        left.set(qn("w:sz"), "18")
        left.set(qn("w:space"), "8")
        left.set(qn("w:color"), str(colours["watermelon"]))
        border.append(left)
        place(styles["Block Text"].element.get_or_add_pPr(), border, PPR)

    if "Hyperlink" in styles:
        set_colour(styles["Hyperlink"], accent)

    for name in MONO_STYLES:
        if name in styles:
            set_font(styles[name], MONO)
            styles[name].font.size = Pt(9)

    # The defaults, so a run with no style still reads as Outfit.
    defaults = doc.styles.element.find(qn("w:docDefaults"))
    rpr = defaults.find(qn("w:rPrDefault")).find(qn("w:rPr"))
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is None:
        fonts = OxmlElement("w:rFonts")
        rpr.insert(0, fonts)
    for key in list(fonts.attrib):
        del fonts.attrib[key]
    for slot in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        fonts.set(qn(slot), FONT)


def page_number(paragraph) -> None:
    """A PAGE field, which Docs turns into its own page number."""
    run = paragraph.add_run()
    for kind, text in (("begin", None), (None, "PAGE"), ("separate", None), ("end", None)):
        if kind:
            char = OxmlElement("w:fldChar")
            char.set(qn("w:fldCharType"), kind)
            run._r.append(char)
        else:
            instr = OxmlElement("w:instrText")
            instr.set(qn("xml:space"), "preserve")
            instr.text = text
            run._r.append(instr)


def frame(doc: DocumentObject, colours: dict[str, RGBColor]) -> None:
    section = doc.sections[0]
    section.start_type = WD_SECTION.NEW_PAGE
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin, section.bottom_margin = Cm(2.4), Cm(2.0)
    section.header_distance, section.footer_distance = Cm(1.0), Cm(1.0)

    header = section.header
    header.is_linked_to_previous = False
    para = header.paragraphs[0]
    para.text = ""
    para.add_run().add_picture(str(LOGO), height=Cm(0.75))

    footer = section.footer
    footer.is_linked_to_previous = False
    para = footer.paragraphs[0]
    para.text = ""
    usable = section.page_width - section.left_margin - section.right_margin
    para.paragraph_format.tab_stops.add_tab_stop(usable, WD_TAB_ALIGNMENT.RIGHT)
    para.add_run(FOOTER_TEXT + "\t")
    page_number(para)
    for run in para.runs:
        run.font.name = FONT
        run.font.size = Pt(8.5)
        run.font.color.rgb = colours["charcoal-blue"]


def main() -> None:
    base = subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        capture_output=True,
    ).stdout
    doc = Document(io.BytesIO(base))
    colours = {name: RGBColor.from_string(value) for name, value in palette().items()}
    restyle(doc, colours)
    frame(doc, colours)
    doc.core_properties.author = "rebase"
    doc.core_properties.title = "rebase"
    doc.save(str(OUT))
    print(f"Wrote {OUT.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    main()
