# Positioning: who rebase is for

The source every public surface writes from. A page, a meta description, a wizard step
or an MCP tool description that says who we are for says it in these words; if the
words here are wrong, this file changes first and the surfaces follow.

Decided with Ivan on 2026-09-09 (REB-24). Since 2026-09-14 the name is **rebase**, written
lowercase everywhere a person reads it, including at the start of a sentence (REB-194);
The former name survives only in code identifiers and in history. The wording of what a visitor reads is his;
this page is what the copy is checked against.

## Who it is for

People who build software for a living, on their own account, at a seniority where a
company can hand them a project and trust them with it:

- **Software developers**: backend, frontend, mobile, data. People who have shipped
  things that are in production and have been paged for them.
- **AI engineers**: people who put models into products, not people who have done a
  course.
- **CTOs and fractional roles**: fractional CTO, fractional head of engineering,
  technical leads who take a company's engineering for two days a week.
- And the neighbours of those roles at the same level: architects, SREs, staff
  engineers who consult.

The common thread is not the title but the level: they scope work, say no, quote a day
rate without flinching, and can be the only technical person a client talks to. They
have a partita IVA, very often in forfettario, and they mostly work from Italy for
Italian and European companies.

## Who it is not for

Say it plainly, because the old copy did not:

- Not for freelancers in general. Not designers, copywriters, photographers,
  translators, marketers. Good people, different community; the tools we built do not
  fit their work and the projects we bring will not be theirs.
- Not for juniors looking for a first client, and not for people between jobs
  looking for a bridge. There is nobody here to mentor them, and a company that asks
  us for a person asks for someone who can be left alone with the problem.
- Not for agencies or studios placing their own people. rebase is one person, one
  CV, one day rate.
- Not for companies looking for the cheapest hourly rate. The projects we take are
  the ones where the client wants it done well.

## What we promise

Three things, always in this order, in these words:

1. **Projects from real companies.** Brought by people who know the company, not
   scraped from a board. No reverse auctions. You look, you ask, you decide.
2. **Tools to invoice and get paid.** Quote, contract, invoice, hours, the client's
   email on their card, with the fiscal details already right. PigroCRM is the tool,
   free for whoever is in the community, and it is the perk, never the headline.
3. **People who have been there.** Someone who has already had the difficult
   phone call, already priced the same kind of project, already said no to the same
   kind of client.

We do not promise volume, a marketplace, a rating, or a career. We are small and we say
so: «stiamo mettendo insieme», «ti scriviamo noi».

## The voice

- **Short.** The community page is under ninety words of body text and a test keeps
  it there. One idea per sentence.
- **Warm, first person plural.** «Noi» is the people running it, who do the same job.
  «Tu» is the reader, always singular, never «voi».
- **Italian on the product surface.** Everything a visitor reads is Italian. English
  words are allowed where the Italian tech world already uses them and the Italian
  translation would sound like a job ad from a bank: «developer», «AI engineer»,
  «CTO», «fractional», «backend», «remoto».
- **Concrete over aspirational.** «Fatturare e farti pagare», not «gestire la tua
  attività». «Aziende vere», not «opportunità».
- **No hype words.** Never «talent», «top», «elite», «network», «ecosistema»,
  «rivoluzione», «smart». Never an exclamation mark.

## The words

| We say | We do not say | Why |
|---|---|---|
| developer, sviluppatore when a sentence needs an Italian word | programmatore, coder, dev | «Developer» is what they call themselves on LinkedIn |
| AI engineer | esperto di intelligenza artificiale, data scientist as a catch-all | Names the job, not the field |
| CTO, fractional CTO, fractional | CTO a tempo, CTO part-time, consulente strategico | «Fractional» is the term of art and the search term |
| chi fa software in proprio, lavora in proprio | freelance as the subject of a headline | Says what they do and that they do it on their own account |
| freelance, partita IVA, forfettario | libero professionista, professionista autonomo | As the legal and fiscal category only: «developer freelance», «in forfettario». Never as who the reader is |

Two exceptions Ivan decided on 2026-09-09: the `<title>` and the `og:title` of the landing keep «freelance» («rebase — freelance, ma non da soli», and in the `<title>` «Con PigroCRM gratis» after it), for continuity with what search engines and people already know; and «Freelance» is one of the words the h1 types, after the roles, so a visitor who only knows that word still finds themselves in it.
| aziende vere, un progetto vero | clienti, opportunità, lead | The company is the other party of a project, not a sales object |
| tariffa a giornata, quanto costa una tua giornata | rate, tariffa oraria | Seniors quote days |
| persone che ci sono passate | community, network, mentor | The value is the experience, not the group |
| PigroCRM è gratis per chi è dentro | piano, prova, trial, freemium | There is one price and it is being in the community |

The route `/hub/freelance`, the API vocabulary (`freelancers`), the database and the
code keep «freelance» as the category name. They are not what a person reads, and a
rename there is a separate issue once this statement has settled (REB-24 point 4).

## Where it already shows, and where it has to

Already aligned before this statement: the wizard asks «Quanto costa una tua giornata?»
and «Cosa fai?» with «Backend developer» as the placeholder; the landing's «Come
funziona» talks about CV, day rate and «chi ha già fatto cose simili»; the testimonial
placeholders name a backend developer and a CTO. The hub was built for this audience
and its copy said so; the headlines did not.

Has to follow this statement: every `<title>`, meta description and Open Graph tag of
`projects/website` (a LinkedIn share shows exactly those and nothing else), the two
`h1`s, the calls to action, the hub's chooser and thank-you page, and every MCP tool
description that names the audience.

## Identity verdict

Does the visual system (`shared/brand`: the four-tile mark, the palette, Outfit) read
as «senior tech», or as «freelance in general»?

**The mark carries it.** Four square tiles, Prussian Blue, Royal Gold, Watermelon,
Prussian Blue, on a 14px grid, with hard edges and a stepped shadow and no rounded
corner anywhere. That is a pixel, a terminal, a bitmap: it reads as made by engineers
for engineers more than any word on the page did. The website's square system
(`.glyph`, the grid, the `box-shadow` steps) is the strongest tech signal we have and
should stay exactly as it is.

**The palette is neutral to friendly, not senior.** Prussian Blue as ink is serious and
does the heavy lifting. Watermelon and Royal Gold together are warm and a little
playful, which is right for a community that says «noi» and «ti scriviamo noi», but on
their own they could dress a design studio as easily as an engineering community. This
is not a problem while the ink dominates and the two accents stay accents; it becomes
one if Royal Gold ever grows into a background.

**Outfit is fine, and the weight is the lever.** A geometric sans that half the
start-ups of 2023 chose, so it does not say anything specific about who we are. It is
clean and it renders well at the sizes used. What softens it is the body at weight 300:
light text reads as «gentle», and senior readers trust text that sits firmly on the
page. This is a website stylesheet decision (`landing.css`), not a brand
one.

**What I would change, in order, and none of it in this PR:**

1. Nothing in `shared/brand`. The copy was the gap, not the identity.
2. Body weight from 300 to 400 on both website stylesheets, keeping 500 for headings.
   Cheapest single change that makes the pages read more grounded. Website-only.
3. A monospace accent for the step numbers and the kicker on `/pigrocrm`, from a
   system stack (`ui-monospace, SFMono-Regular, Menlo, monospace`), no new font file:
   a nod to code without a second typeface to host. Website-only, worth a test render
   before deciding.
4. Only if 1 to 3 prove not enough: swap Outfit for a typeface with more character in
   the tech register (Geist, Inter Tight, IBM Plex Sans), which is a `shared/brand`
   change, a CRM change and a row in `DECISIONS.md`. I would not start here.

The mark, the palette and the typeface are Ivan's to change. This section is the
argument; the decision, when there is one, is a row in `docs/design/DECISIONS.md`.
