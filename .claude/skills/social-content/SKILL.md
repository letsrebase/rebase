---
name: social-content
description: Use when asked for anything rebase publishes on a social network or shares as a picture: a LinkedIn post or its text, a carousel or document post, a single image, a cover or banner for a profile or page, an announcement, a «build in public» update with numbers, a redraw of an old one. Triggers on «post», «carosello», «carousel», «LinkedIn», «copertina», «banner», «immagine da condividere», «annuncio», «scrivi il testo del post», and on a request to turn a report, a release or an analysis into something to share.
---

# Social content for rebase

Everything rebase shows in public is one system: the words in `docs/design/positioning.md`
(who we are for, the voice, the words we say and do not say), the shapes in
`shared/brand` (the four-tile mark, the palette, Outfit, the echo logo) and the rows of
`docs/design/DECISIONS.md` dated 2026-09-14 and 2026-09-15 on the name and the logo. Read
`positioning.md` before writing a word; where this skill and those documents disagree,
the document is right and the skill has a bug. What this directory adds is the picture
system as files (`carousel-template.html`, `banner-template.html`, `render.mjs`), so a
carousel or a cover is edited and rendered, never redrawn.

## The name and the voice, in short

- The product is **rebase**, lowercase everywhere a person reads it, sentence start
  included. Never «Rebase», never «REBASE», never «Orbiters» (history and code only).
- Italian. Short: one idea per sentence, one idea per slide. «Noi» is the people running
  it, who do the same job; «tu» is the reader, singular, never «voi». Concrete over
  aspirational: «aziende vere», «fatturare e farti pagare», «quanto costa una tua
  giornata». No exclamation marks, no hype words (never «talent», «top», «elite»,
  «network», «ecosistema», «rivoluzione», «smart»), no emoji, no hashtags.
- English where the Italian tech world already uses it («developer», «AI engineer»,
  «fractional CTO», «backend», «remoto») and nowhere else. «Freelance» is the fiscal
  category, not who the reader is: «chi fa software in proprio».
- Small and honest: «stiamo mettendo insieme», «ti scriviamo noi». Numbers are quoted
  with their date and their source, small numbers included («nove persone in due
  giorni»), and a number a member could be recognised by is not quoted at all. Names
  of members, applicants or companies never appear; what travels is a count and a
  pattern.
- The claim is «Freelance, ma non da soli.»; the closing question is «Pronto a fare
  rebase?»; the door is «Entra in rebase» and the address is `letsrebase.com`, once.
- A number's slide carries its source and date once, in a `.source` line under the
  element that shows it («PostHog, 14 e 15 settembre 2026, persone vere»). «Persone
  vere» is the whole bot sentence on a slide: bots and internal visits are filtered
  before counting, and the README says how.

## The public addresses

| Page | Address |
|---|---|
| The landing | `letsrebase.com` |
| PigroCRM for whoever is in | `letsrebase.com/pigrocrm` |
| The community form | `letsrebase.com/community` |
| The freelance wizard («Entra come talento») | `letsrebase.com/hub/freelance` |
| The company wizard («Cerchi persone?») | `letsrebase.com/hub/aziende` |
| A member's area | `letsrebase.com/hub/accedi` |
| The deck | `letsrebase.com/pitch` |

The guide has no public address: it is downloaded from the member's area, so a post
about it points at the wizard.

## What gets made

| Deliverable | Size | File |
|---|---|---|
| Carousel (LinkedIn document post) | 1080×1080 per slide, 6 to 8 slides, one PDF | `carousel-template.html` |
| Single image post | 1080×1080, one slide of the same file | `carousel-template.html` |
| Profile cover | 1584×396 (3168×792 for a 2× export) | `banner-template.html --size 1584x396` |
| Company page cover | 1128×191 | `banner-template.html --size 1128x191` |
| Link preview of the site | drawn in the repository, not here | `projects/website/scripts/share-card.js` |

A carousel is one HTML file, one `<section class="slide">` per page, rendered by
`render.mjs` into `png/<name>-NN.png` and `<name>.pdf`. The PDF is what a LinkedIn
document post takes; the PNGs are for an image carousel or for reading the result.

## The picture system

The template carries it; these are the rules the template cannot enforce:

- **Paper ground with the 24px grid, the field of tiles, a white box with a 3px ink
  border and the stepped shadow, no rounded corner anywhere.** The four colours and
  nothing else: Prussian Blue for ink, Watermelon for the accent on paper, Royal Gold for
  the accent on ink, Charcoal Blue for quiet text. Values from `shared/brand/palette.css`,
  never a hex of your own.
- **Rhythm**: a boxed cover, then content slides that alternate light and dark, then a
  boxed close. The footer carries `letsrebase.com` and «n / N»; the cover carries «scorri».
- **The header is the glyph and the word**: `.brand` (the four tiles, then «rebase» in
  Outfit 500). The echo logo (`shared/brand/echo/echo-ink-watermelon-outlines.png` on
  paper, `echo-white.png` on ink) is for a cover or a close at large size; the outlined
  wordmark SVGs in `shared/brand` are the site's and are not mixed into a slide with the
  echo, since they are two faces.
- **One idea per slide, filled**: a kicker, a statement of two or three short lines,
  then one element (three rows, three tiles, one bar chart, one card, one redrawn screen)
  or one lead. Empty ground under a third of the pad is fine; more than that, grow the
  gaps between kicker, element and lead before adding content, and a slide with three
  paragraphs of 30px text and the lower half empty is a slide to split or to cut. Type
  sizes are the template's classes; shrink a statement before shrinking a lead.
- **A screen of the product is the real screen**: a screenshot from the running app, or
  the template's `.mock`, which is drawn in the shape of the wizard's engine
  (`projects/hub/apps/web/src/wizard/Wizard.tsx`: the breadcrumb with «n di N», the
  progress bar, the question as a heading, the controls, «Invio ↵ per continuare» beside
  «Avanti») with the exact copy of the page (`pages/FreelancerWizard.tsx` and its
  siblings). A control the product does not have, a progress bar it does not draw, a
  placeholder it does not say, is a false picture of the product.
- **The field stays off the copy**: `data-band` puts it in the corner the text does not
  use (`diagonal` behind a box, `topright` or `bottomleft` beside a column of text) and
  `data-seed` changes the drawing per slide. A band under a paragraph is the first thing
  to change when a slide reads badly.
- **Charts** follow the `dataviz` skill in spirit: one hue for the measure, the accent
  only on a called-out peak, a value at the end of every bar, square ends.

## The post text

The caption is not a summary of the carousel; it is the reason to open it. Its shape:

1. One line that is true and specific, no preamble: a number with its date, or a
   thing that happened («Nove persone hanno aperto il wizard. Cinque sono arrivate in
   fondo.»).
2. Two to four short paragraphs, one idea each, in the voice above. Under 120 words,
   counting the paragraphs and not the address; the carousel carries the detail.
3. The address, once, at the end (`letsrebase.com` or the page from the table above),
   as the last line. No hashtags, no «link nei commenti», no emoji, no exclamation mark.

Delivered as `post.md` beside the carousel, with the slide list under it so whoever
posts can check the two agree.

## The order of work

1. **Copy first.** Write the slide plan as a numbered list (title and the one element
   of each slide) and the caption, and show them to whoever asked before touching the
   HTML; when nobody can answer (a subagent, a scheduled run), write the plan into the
   README and go on. Numbers come from Ivan or from a query with its date
   (`posthog-analytics` skill); an unknown number is asked for, not invented, and the
   plan says which ones are missing.
2. **The folder.** A directory of its own for the deliverable, one per topic
   (`rebase-<topic>-carousel`): copy the template there as `carosello-<topic>.html`, copy
   `shared/brand/fonts/outfit-variable-latin.woff2` beside it, and write a `README.md`
   that lists the slides, the sources of every number, and what was left out on purpose.
3. **Edit the slides** in that copy, and its `<title>`: the template's three slides are
   a cover, a dark content slide and a close; add the middle ones from the classes in the
   file (`rows`, `tiles`, `bars`, `cards`, `mock`, `source`, `cta`).
4. **Render** with `node .claude/skills/social-content/render.mjs <file>` from any
   directory: the script finds Playwright through `projects/website`, so the only
   requirement is that `pnpm install` has run in the checkout. Read `overflow […]`:
   anything but `[]` is a slide LinkedIn crops.
5. **Look at every PNG** before calling it done: a band under a paragraph, a lead that
   collides with the footer, a statement that wrapped to four lines, a colour that is not
   one of the four, an exclamation mark or a capital «Rebase» in the copy (grep the text
   nodes, not the file: the doctype and the comments carry both). Fix, re-render, look
   again. Ship the PDF and the PNGs together.
6. **Never post.** Publishing is Ivan's: the deliverable is the folder and the caption.

## Red flags

| Thought | Reality |
|---|---|
| «I'll add a few hashtags, everyone does» | rebase never has. The caption ends with the address. |
| «Rebase» at the start of a sentence | Lowercase, always. Sentence start included. |
| «I'll draw a plausible screen of the wizard» | The real copy or a real screenshot. A control the product lacks is a lie about the product. |
| «Three paragraphs fit at 30px» | One idea per slide. Split it or cut it. |
| «A quick post text summarising the slides» | The caption is the reason to open, under 120 words, one number up front. |
| «I'll pick a nicer red / a darker blue» | Four colours, from `palette.css`. |
| «Let me quote what she said on the call» | Counts and patterns travel; names and quotes of members do not. |
| «I'll write the HTML from scratch, it's faster» | The template is the system. Copy it, edit the slides. |
