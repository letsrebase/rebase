# Google Docs in rebase's styles

Every document rebase writes in Google Docs (a proposal for a partner, a brief for a
client, a summary after a meeting) starts from one template, so they all look like the
site: Outfit, the palette's ink and Watermelon, the echo logo in the header and
`rebase · letsrebase.com` with the page number in the footer. An agent writes Markdown
and these scripts turn it into a Doc; a person copies the template in Drive. The
procedure, the Drive folders and the sharing rules are the `google-docs` skill
(`.agents/skills/google-docs/SKILL.md`).

| File | What it is |
|---|---|
| `build_reference.py` | Builds `reference.docx` from pandoc's own reference document and `shared/brand` (colours read from `palette.css`, the logo from `echo/`). Run it when the brand changes |
| `reference.docx` | The styles: Title, Subtitle, Date, Heading 1 to 4, body, captions, quotes, the header and the footer. Committed, so rendering needs no build step |
| `render.py` | Markdown to `.docx`: pandoc with `reference.docx`, then every table restyled with the cell's own shading, borders and absolute widths, which Google Docs keeps on import where it drops a Word table style and a percentage width (observed 2026-11-16) |
| `figure.py` | An HTML fragment to a PNG in Outfit and the palette, photographed by headless Chrome at twice the size, for a diagram or a chart inside a document |
| `brand_palette.py`, `ooxml.py` | What the three scripts share: the palette read from `palette.css`, and the schema order every property element is inserted in, so Word opens the file as cleanly as Docs does |
| `template.md` | The content of the template Doc in Drive: every style once, in Italian, the language of the documents it is used for |

```bash
uv run tooling/google-docs/build_reference.py           # after a brand change only
python3 tooling/google-docs/figure.py flow.html flow.png --size 960x470
uv run tooling/google-docs/render.py doc.md              # writes doc.docx
```

`build_reference.py` and `render.py` declare their one dependency (`python-docx`)
inline, so `uv run` needs no workspace member; `figure.py` needs only the standard
library and a Chrome or Chromium. `pandoc` (3.x) and Google Chrome are on the devbox.

**Why Markdown through `.docx` rather than writing the Doc through the API.** The Docs
API can apply a named style, but tables, pictures and cell shading cost dozens of
index-counted requests each, and a mistake leaves a half-written document. A `.docx`
imported by Drive arrives whole, in one call, and carries the header, the footer and
the logo with it. The price is that a Doc is replaced rather than edited in place: a
change re-imports and the old Doc goes to the bin.
