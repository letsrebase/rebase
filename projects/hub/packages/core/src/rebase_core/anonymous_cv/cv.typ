// The anonymous CV's page (REB-862, spec § The PDF): the order and density of the ATS-style
// CV Ivan gave as the model, in rebase's colours and typeface. A title where the name was,
// one line under it, then a section per part of the CV, each left out when it is empty.
//
// Everything comes from `cv.json`, which `render.py` writes beside this file: the CV as
// validated (`cv`), the head's line and the profile's «Rif.» (`head`), the headings in
// the CV's language (`headings`), the footer's words, the brand's colours read out of
// `@rebase/brand/palette.css` (`palette`) and the echo logo's file name (`echo`).
//
// Every value from the JSON is a string placed as text (`text(value)`, `strong(value)`,
// `#value` inside a content block): a string is never parsed as markup, so `*bold*`,
// `#set page(...)` or `$x$` in a CV print as those characters. Nothing here may change that:
// no `eval`, no `raw` with a language, no `show` rule that matches text.
//
// A blank string is an absent one (`present`): it prints no lone colon after a lead, no
// heading over nothing and no separator with nothing on one side.

#let data = json("cv.json")
#let cv = data.cv
#let headings = data.headings

#let ink = rgb(data.palette.ink)
#let inkquiet = rgb(data.palette.inkquiet)
#let cta = rgb(data.palette.cta)
#let gridline = rgb(data.palette.gridline)

// « · » between the parts of the head's line, of the footer and of the languages, a little
// wider than Outfit's narrow space so the dot reads as a separator. The spaces stay real
// characters, so the text layer still reads «Senior · 12 anni di esperienza».
#let sep = text(fill: inkquiet)[#h(0.15em) · #h(0.15em)]
#let spaced(s) = s.split(" · ").map(text).join(sep)

#set document(title: cv.title, author: "rebase")

#set page(
  paper: "a4",
  margin: (x: 18mm, top: 17mm, bottom: 21mm),
  fill: white,
  // The echo at the top right of the first page only: the footer names rebase on every
  // page, and a logo over each page's first line would only crowd it.
  header: context if counter(page).get().first() == 1 {
    align(right, image(data.echo, height: 7.5mm, alt: "rebase"))
  },
  header-ascent: 25%,
  footer: context {
    set text(size: 7.5pt, fill: inkquiet)
    let number = str(counter(page).get().first()) + " / " + str(counter(page).final().first())
    let where = text(data.head.ref) + sep + text(number)
    block(width: 100%, stroke: (top: 0.5pt + gridline.darken(20%)), inset: (top: 5pt))[
      #grid(columns: (1fr, auto), column-gutter: 12pt, spaced(data.footer), where)
    ]
  },
)

// Ragged right and no hyphenation: a CV is read by eye and by a company's ATS, and a word
// cut in two is a word neither of them finds. No kerning for the same reader: Outfit pulls
// «Te», «Ty», «Yo» together by so much that a PDF text reader such as pypdf takes the gap
// for a space and reads «T elco», while on the page the difference is a hair.
#set text(
  font: ("Outfit",),
  size: 10pt,
  weight: 300,
  fill: ink,
  lang: cv.language,
  hyphenate: false,
  kerning: false,
)
#set par(leading: 0.52em, spacing: 0.7em, justify: false)
#show strong: set text(weight: 500)

// Small capitals drawn by hand: Outfit has no `smcp`, so `smallcaps` would change nothing.
// A run of lower-case letters is set in capitals at 80%; a capital, a digit or a sign stays
// full size, and a space joins the run it is in, so the text layer reads one word per word.
#let small-caps(s) = {
  let runs = ()
  for c in s.clusters() {
    let small = lower(c) == c and upper(c) != c
    if runs.len() > 0 and (c == " " or runs.at(-1).small == small) {
      runs.at(-1).text += c
    } else {
      runs.push((small: small, text: c))
    }
  }
  for run in runs {
    if run.small { text(size: 0.8em, upper(run.text)) } else { text(run.text) }
  }
}

// A section's heading: small capitals over a thin rule in the brand's accent, kept with
// what follows it, since a heading alone at the foot of a page reads as a part left out.
#let section(title) = block(
  above: 1.45em,
  below: 0.65em,
  sticky: true,
  width: 100%,
  stroke: (bottom: 0.6pt + cta),
  inset: (bottom: 2.5pt),
  text(size: 10.5pt, weight: 500, tracking: 0.03em, small-caps(title)),
)

#let present(s) = s != none and s.trim() != ""
// A string as text, or nothing when it is blank.
#let opt(s) = if present(s) { text(s) }

// The example's italic, slanted by hand: Outfit has no italic either.
#let oblique(s) = if present(s) { skew(ax: -11deg, text(s)) }

// Rows of two cells, the left one wide and the right one flush right: role and period,
// company and place, school and place, degree and period. A row with neither is left out.
#let rows(..pairs) = {
  let cells = ()
  for (l, r) in pairs.pos() {
    if l != none or r != none {
      cells.push(l)
      cells.push(align(right, r))
    }
  }
  grid(columns: (1fr, auto), column-gutter: 14pt, row-gutter: 0.45em, ..cells)
}

// A bold lead, a skills area: bold, closed by a colon the page adds, so a lead the CV
// already closed with one does not print two.
#let lead(s) = strong(s.trim().trim(":", at: end, repeat: true).trim() + ":")

#let bullets(items) = if items.len() > 0 {
  list(
    marker: text(fill: inkquiet, "•"),
    indent: 3pt,
    body-indent: 6pt,
    spacing: 0.58em,
    ..items,
  )
}

// The head: the CV's title large and centred where the name was, the line under it.
#align(center)[
  #if present(cv.title) {
    block(below: 0.8em, text(size: 19pt, weight: 500, cv.title))
  }
  #if present(data.head.line) {
    text(size: 10pt, fill: inkquiet, spaced(data.head.line))
  }
]
#v(0.2em)

#if present(cv.summary) {
  section(headings.summary)
  text(cv.summary)
}

// A bullet: the bold lead and its text, the lead alone (no colon to close) or the text
// alone; one with neither is not a bullet.
#let point(p) = {
  if present(p.title) and present(p.text) {
    [#lead(p.title) #p.text]
  } else if present(p.title) {
    strong(p.title)
  } else {
    text(p.text)
  }
}

#let roles = {
  cv
    .experience
    .map(r => (..r, points: r.points.filter(p => present(p.title) or present(p.text))))
    .filter(r => (r.role, r.company, r.location, r.period).any(present) or r.points.len() > 0)
}
#if roles.len() > 0 {
  section(headings.experience)
  for (i, role) in roles.enumerate() {
    // The two rows stay with the first bullet: a role's name at the foot of a page with
    // its work on the next reads as two roles. Without a company the place takes its
    // slot, rather than sitting alone at the right of an empty line.
    let second = if present(role.company) {
      (oblique(role.company), oblique(role.location))
    } else {
      (oblique(role.location), none)
    }
    block(above: if i == 0 { 0.7em } else { 1.15em }, below: 0.5em, sticky: true, rows(
      (if present(role.role) { strong(role.role) }, opt(role.period)),
      second,
    ))
    bullets(role.points.map(point))
  }
}

#let groups = {
  cv.skills.map(g => (area: g.area, entries: g.entries.filter(present))).filter(g => g.entries.len() > 0)
}
#if groups.len() > 0 {
  section(headings.skills)
  // One paragraph per area, a long one hanging under its own name. The first stays with
  // the heading, so a page never ends on the heading and a single area.
  for (i, group) in groups.enumerate() {
    let words = group.entries.join(", ")
    let line = if present(group.area) {
      par(hanging-indent: 1em)[#lead(group.area) #words]
    } else {
      par(hanging-indent: 1em, text(words))
    }
    block(above: 0.5em, below: 0.5em, sticky: i == 0, line)
  }
}

#let degrees = cv.education.filter(t => present(t.title) or present(t.institution))
#if degrees.len() > 0 {
  section(headings.education)
  for (i, title) in degrees.enumerate() {
    let pairs = if present(title.institution) {
      ((strong(title.institution), opt(title.location)), (oblique(title.title), opt(title.period)))
    } else {
      let when = (title.location, title.period).filter(present).map(text).join(sep)
      ((strong(title.title), when),)
    }
    block(above: if i == 0 { 0.7em } else { 0.9em }, breakable: false, rows(..pairs))
  }
}

#let certificates = cv.certifications.filter(present)
#if certificates.len() > 0 {
  section(headings.certifications)
  bullets(certificates.map(text))
}

#let languages = cv.languages.filter(l => present(l.language))
#if languages.len() > 0 {
  section(headings.languages)
  languages
    .map(l => if present(l.level) [#lead(l.language) #l.level] else { strong(l.language) })
    .join(sep)
}

// A section the CV gives no title still prints, under the page's own word for it.
#for other in cv.other_sections.map(s => (title: s.title, entries: s.entries.filter(present))) {
  if other.entries.len() > 0 {
    section(if present(other.title) { other.title } else { headings.other })
    bullets(other.entries.map(text))
  }
}
