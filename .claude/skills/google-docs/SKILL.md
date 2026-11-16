---
name: google-docs
description: >-
  Use when writing a Google Doc for rebase: a proposal for a client or a partner, a brief, notes for a meeting, a summary to share, anything that lands in rebase's Drive. The template, the Markdown-to-Doc pipeline, the figures, the Drive folders, the preview before handing it over, and who it may be shared with. Triggers on «Google Doc», «documento», «proposta da mostrare», «prepara un doc», «template», and on a request to turn an analysis or a conversation into something to show.
---

# Google Docs for rebase

A rebase document is written as Markdown and rendered through the branded template in
`tooling/google-docs/` (its README says what each file does), then imported into
rebase's Drive. Nobody starts from a blank Doc and nobody restyles one by hand: the
title, the headings, the tables, the logo in the header and the footer all come from
`reference.docx`, which is built from `shared/brand`.

## Where it lives

- **Account:** `ciao@letsrebase.com`, through the `gws-rebase-ciao` MCP server. Never a
  personal Google account, never a client's.
- **Folders:** `Documenti/` (Drive id `1ycFZNWa9O0YMbsOC-ZokANgeIoziejZx`), shared as
  editors with Lorenzo and Ivan. Inside it, `Template/` holds only the template Doc
  («Template documento rebase»), `Interno/` holds what only the team reads, and every
  client or partner gets a folder of its own (`Documenti/<counterpart>/`) for what they
  may read. Create a counterpart's folder with its first document.
- **By hand:** a person opens the template, «File > Crea una copia» into the right
  folder, and writes with the template's own styles (Titolo, Sottotitolo, Intestazione
  1-3, Testo normale). They never paste over the header or the footer.

## Writing the Markdown

- **The language is the reader's.** A document a client or a partner reads is in their
  language, Italian by default; an internal one follows the team. The voice is the one
  of `docs/design/positioning.md`: plain, first person plural, no selling words.
- **Nothing the reader could not verify.** A capability, a delay, a number of people or
  a client's name goes in only if code, the CRM, the hub or Lorenzo confirmed it in
  this session. A promise («entro 48 ore») is written as what we propose, not as a fact
  about the past. Rates, margins and contract terms of another client never go in.
- **Internal and external are two documents in two places.** What only the team may read
  (what not to concede, the other side's weak points, our own numbers) is a separate Doc
  in `Documenti/Interno/`, titled «<counterpart> · Note interne (non condividere)»,
  never a section of the shared one and never in the counterpart's folder.
- **Shape:** the YAML block sets `title`, `subtitle` and `date`; `#` is a section, `##` a
  part of it, `###` only when needed. Open with «In breve» (three or four lines that are
  enough alone). Tables are pipe tables with a header row; the dashes of the separator
  row set the column widths (`|-|------------|----------|` makes the first column
  narrow), since `render.py` runs pandoc with `--columns=1`.
- **Figures:** an HTML fragment drawn with `var(--color-...)` and Outfit, rendered with
  `figure.py` at the width it will be read at (960 CSS pixels fills a page and keeps the
  text legible), then embedded as `![caption](figure.png){width=100%}`. Look at the PNG
  before using it. No palette hex values of your own, no stock pictures; a picture inside
  a fragment needs an absolute `file://` URI.

## Rendering and importing

```bash
python3 tooling/google-docs/figure.py flow.html flow.png --size 960x470
uv run tooling/google-docs/render.py doc.md          # writes doc.docx beside it
```

Work in a scratch directory on real disk (`~/.cache/tmp/<topic>/`), not in the
repository: a document is not committed. Then import with `import_to_google_doc` on
`gws-rebase-ciao`: `file_name` (the Doc's title), the file as `base64_content` with
its `base64_sha256`, `source_format: "docx"`, and the right folder as `folder_id`. The
server does not run on the devbox, so a `file_path` there reads its own disk and fails
with «Path does not exist» (observed 2026-11-16).

## Checking it before anyone sees it

1. `get_doc_content` on the new Doc and search it for every claim you were told to
   drop: what you fixed in the Markdown is not proof the Doc has it.
2. `export_doc_to_pdf` into the same folder, download it with
   `get_drive_file_download_url`, look at each page (`pdftoppm -r 70 -png`), then move
   the PDF to the bin (`update_drive_file` with `trashed: true`). `pdffonts` should list
   Outfit: anything else means a run lost its style.
3. A change means rendering again, importing the new Doc and moving the old one to the
   bin. `update_drive_file` could replace the content in place, but only from a path or
   a URL the server can reach, which a file on the devbox is not.

## Sharing

The folder's sharing covers Lorenzo and Ivan. Showing a document to a client or a
partner, sharing it with them or exporting it for them is theirs to decide: say the
Doc is ready and where, never share it outside the team yourself. Sharing outside the
team is one Doc at a time, never a folder, so an internal note cannot go along with
it. Test shares go to the team's own addresses only.

## When the brand changes

Run `uv run tooling/google-docs/build_reference.py`, render `template.md`, import it as
the new template, move the old one to the bin, and check a page as above. Documents
already written keep their styles; they are not re-rendered.
