"""Place an OOXML child where the schema wants it.

Word refuses, or «repairs», a property element out of its sequence (a `w:shd` after a
`w:tcMar`, a `w:pBdr` after a `w:spacing`). Google Docs is more forgiving, but a
`.docx` that only one reader accepts is a defect, so every property this directory
adds goes through `place`, which drops any earlier copy of the same element and
inserts the new one before the first sibling that must follow it.
"""

# The sequences of ECMA-376 for the property containers written here.
PPR = (
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl",
    "numPr", "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens",
    "kinsoku", "wordWrap", "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN",
    "bidi", "adjustRightInd", "snapToGrid", "spacing", "ind", "contextualSpacing",
    "mirrorIndents", "suppressOverlap", "jc", "textDirection", "textAlignment",
    "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr", "sectPr", "pPrChange",
)  # fmt: skip
TBLPR = (
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize",
    "tblStyleColBandSize", "tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd",
    "tblLayout", "tblCellMar", "tblLook", "tblCaption", "tblDescription",
)  # fmt: skip
TCPR = (
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap",
    "tcMar", "textDirection", "tcFitText", "vAlign", "hideMark",
)  # fmt: skip
TCMAR = ("top", "start", "left", "bottom", "end", "right")
RPR = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish",
    "webHidden", "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight",
    "u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang",
    "eastAsianLayout", "specVanish", "oMath",
)  # fmt: skip


def place(parent, element, order: tuple[str, ...]):
    """Insert `element` into `parent` in schema order, replacing a copy already there."""
    for old in parent.findall(element.tag):
        parent.remove(old)
    local = element.tag.split("}")[1]
    after = set(order[order.index(local) + 1 :])
    for index, child in enumerate(parent):
        if child.tag.split("}")[-1] in after:
            parent.insert(index, element)
            return element
    parent.append(element)
    return element
