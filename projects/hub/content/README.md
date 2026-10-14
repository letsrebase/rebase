# content

Source text for the perks a member downloads and the contracts a member signs, kept
apart from the apps because it is prose reviewed on its own terms, not markup or code.

- `guida-primi-passi-freelance.md`: the guide to the first steps as a freelancer, the
  community's second perk (ORB-69). Italian, because it is what a member reads, not what
  the repository says about itself. Approved by Lorenzo on 2026-09-10 (PR #8).
- `contratti/`: the example data and the ignored local files for the contract between
  rebase and a freelancer (roadmap #285, REB-386). The two texts moved to
  `../packages/core/src/rebase_core/contracts/texts/` in REB-387, because the hub
  typesets them at request time and the API image carries only `packages/`:
  `contratto-quadro.md` is signed once and holds every rule; `lettera-di-incarico.md` is
  signed per engagement and holds the client, the work, the dates and the numbers.
  Italian for the same reason. Final since 2026-09-24, see [The contracts](#the-contracts)
  below.

## How a file here becomes something a member downloads

`../tools/build_guide_pdf.py` typesets this Markdown with pandoc and Typst into
`../packages/core/src/rebase_core/perks/`, from where `GET /api/hub/me/guida` hands it
to a resolved member session and 401s everybody else. It is a perk, so there is no public
URL for it: the landing on `letsrebase.com` announces it and links to the wizard.

Read that script's docstring before changing anything here: the PDF is a **committed**
artefact, so editing a word in this directory and stopping there leaves a download that
says something the repository no longer does.

```
uv run python projects/hub/tools/build_guide_pdf.py   # rewrite the PDF and its lock
git add projects/hub/packages/core/src/rebase_core/perks projects/hub/tools/guide-pdf.lock.json
```

Three checks hold that together, deliberately in different tiers.
`../packages/core/tests/test_guide_pdf.py` compares the SHA-256 of every source with
`../tools/guide-pdf.lock.json` and needs nothing installed, so it runs in the hub's gate
on every pull request. `../apps/web/src/lib/perks.test.ts` compares the page count and
size the member area shows with that same lock. And the `guide-pdf` preflight check
rebuilds the PDF with pandoc, Typst and fontTools and compares the bytes, which only a
machine carrying those three can do.

Structure this file is read for, rather than free-form prose: the `# ` title and the
paragraph under it become the cover, everything from the first `## ` on is the body, and
a root-relative link is made absolute against `https://letsrebase.com`, because a PDF
has no origin to resolve one against.

## The contracts

The framework agreement carries every rule once, so a letter stays one or two pages: who
the client is, what the work is, when, where, and the fee. The fee is agreed with the
freelancer, starting from the day rate they ask for (article 5); what rebase agrees with
the client is a separate contract (roadmap #286) and appears in neither document.

Each file opens with a front matter (`title`, `subtitle`, `version`, `date`, `status`).
The version is what the member area will record against a signature (REB-454), so a
change to the text after the first signature is a new version, not an edit.

Two markers, both handled by `rebase_core.contracts.fields` (`../tools/build_contract_pdf.py` is the laptop's thin wrapper over it):

- `{{key}}`: a field, lowercase words joined by hyphens. The data fill it; without a
  value it prints as a labelled blank line, so the same file is the template and the
  printable form. The keys are Italian because the blank prints its key as the label.
- `[[text]]`: a proposal still to be decided, highlighted in the draft. The build
  refuses a file whose `status` is not `draft` while one is left.

```
uv run python projects/hub/tools/build_contract_pdf.py                       # every text, blank
uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico \
  --data projects/hub/content/contratti/incarico.esempio.json               # one, filled
```

The laptop's PDFs land in `contratti/dist/`, which git ignores; the hub stores its own per
document, in its database (REB-387). Unlike the guide, nothing is committed or locked.
`texts/rebase.json`, package data beside the texts, holds rebase's company data
and the defaults every letter starts from (the payment term: 30 days from the end of the
invoice's month); its `null`s wait for the SRL (roadmap #284). Until then the data of
whoever signs for rebase goes in `contratti/rebase.local.json`, which git ignores and the
build reads over `rebase.json` when it is there: this repository is public. `compenso` is
a JSON number, and the build refuses one given as text, not above zero, or with more than
two decimals. The payment term is written by the build from `giorni-pagamento` and
`fine-mese`, at most 30 days from the end of the month or 60 from the invoice, so no letter
can print a term that article 7.1 and law 81/2017 forbid.
`incarico.esempio.json` is fiction and names every field either letter asks for, which
`packages/core/tests/test_contract_pdf.py` checks. Real data names a real person and a
real client: keep it outside the repository or in a `*.local.json`, which git ignores.

Since REB-687 the package holds four texts: the two above and their occasional twins for
a member who works without a partita IVA. `contratto-quadro-occasionale.md` is the
ordinary framework agreement with the Collaboratore in place of the Professionista, the
ricevuta in place of the fattura and the new article 5.7 on the 5.000 euro threshold;
`lettera-di-incarico-occasionale.md` names its collaborator by the codice fiscale, and the
build counts its payment term from the receipt. Both are `status: final` at version 1.0.
The points left for the accountant before the first occasional letter leaves are in the
design record's «Outside the code».

### Settled on 2026-09-24, and what stays open

The highlighted proposals are gone: both texts are `status: final` at version 1.0.
Decided by Ivan on 2026-09-23: the payment default (30 days from the end of the invoice's
month, 7.1), the freelancer is paid whether or not the client has paid and rebase aligns
the client's terms to that (7.2), the social security surcharge is inside the fee (5.5),
the 15% of 12.2, and the framework agreement lasting 12 months and renewing itself unless
either side gives notice 30 days before the end (9.1, approved specifically as a tacit
renewal). On 2026-09-24 Ivan accepted the framework agreement's remaining seven proposals
as written, unchanged (REB-430): seven working days to answer a contested Rendiconto
(6.2), ten working days after delivery when no contestation arrives (6.3), 30 days'
notice per engagement (10.1), two working days' notice during the opening check (10.3),
up to two paid handover days (10.4), three years of confidentiality after the last
engagement (13.3), and Milan as the exclusive court (18.3).

- **Non-circumvention** (12): it covers every company rebase introduces, not only the
  ones an engagement came from, for twelve months from the introduction or the last
  engagement. A company the freelancer declares within seven days as a client of the
  previous 24 months is exempt; going direct early means paying rebase 15% of what the
  freelancer invoices that company until the twelve months end. Hidden, it costs the 15%
  plus a penalty of three times the last engagement's average monthly fee, at least
  €5,000, which scales with the engagement and gives a judge less reason to cut it under
  article 1384 of the civil code (decided by Ivan on 2026-09-24, after he found the first
  draft's €2,000 too low).
- **Notice**: 30 days per engagement. When the client stops, rebase may close sooner
  only by paying the fee for the notice days that are missing, so the company contract
  has to give rebase at least that much notice (10.2). Two days during the opening check,
  up to two paid handover days (10.3, 10.4).
- **Confidentiality** lasts three years after the last engagement (13.3). **Court**: Milan,
  to match the SRL's registered seat (18.3).

Still open, before the first real signature (spec § 11):

- **The separate written approval**: a click in the member area is a simple electronic
  signature. It records consent, but a court may not accept it as the separate written
  approval that articles 1341 and 1342 of the civil code ask for the onerous clauses.
  REB-454 should ask for that approval as its own step, and an advanced signature (a
  one-time code) for the framework agreement is worth weighing.
- **Autonomy, as the text and in practice** (4.2): the freelancer works alone and in
  person for a client who coordinates the work, which is the pattern article 2 of
  legislative decree 81/2015 reclassifies as employment when the client sets the hours
  and the place. The letter says the coordination is agreed «di comune accordo», the
  wording of article 409 of the civil procedure code; a labour lawyer should read this
  before the SRL signs, and if a relationship ever counted as a coordinated collaboration
  the exclusive court of 18.3 would not hold (article 413 of the same code).
- **The company contract** (roadmap #286) has to give rebase what this one promises:
  client payment terms that let rebase pay the freelancer at 30 days from the end of the
  month, rights on the work passing on payment, a notice from the client at least as long
  as the freelancer's, and the client's authorisation to appoint the freelancer as a
  sub-processor. Its first draft is below (REB-689).

No lawyer has read these texts (#285: our own draft).

### The company's contract and the confidentiality agreement (REB-689)

Three drafts at version 0.1, `status: draft`, written for a lawyer's review. They sit in
`REVIEW_TEXTS` (`rebase_core.contracts.render`), apart from `DOCUMENTS`: the build and
the renderer typeset them, the registry and the signing flow do not know them, and
wiring one in is a card of its own after the review.

- `condizioni-generali-azienda.md`: the general terms attached to every proposal. They
  are what a company accepts with each proposal, the way Ivan's own consulting
  proposals already work: a proposal with the general terms attached.
- `proposta-azienda.md`: the proposal for one engagement. It follows the layout of
  those consulting proposals (addressee and subject, scope and goals, activities, exclusions,
  economic terms, invoicing and payment, privacy, a summary of the general terms,
  validity, acceptance) and carries the company's specific approval of the onerous
  clauses (articles 1341 and 1342). One profile per proposal for now: the professionals'
  table has one row of fields, and a team needs a list field the renderer does not have.
  `corrispettivo` is held to the rules of `compenso` and printed the same way.
  `proposta.esempio.json` is fiction and names every field it asks for, which
  `test_contract_pdf.py` checks.
- `accordo-di-riservatezza.md`: the confidentiality agreement the freelancer signs on
  top of the framework agreement and the letter. It covers what a member sees before any
  engagement too (open requests, company names, a selection's material), lasts three
  years after the last information or engagement, and yields to whichever rule protects
  more when the framework agreement (13) or a company's own NDA covers the same
  information.

How the general terms give rebase what the framework agreement promises the freelancer:

| Framework agreement (freelancer) | General terms (company) |
|---|---|
| 7.1, paid at 30 days from the end of the invoice's month | 7.1, the same default term |
| 7.2, paid whether or not the client has paid | 7.2 to 7.4, late interest and suspension |
| 6.2, seven working days to answer a Rendiconto | 6.1 and 6.2, forwarded the next working day, five working days for the client to contest: six in all |
| 6.3, ten working days after delivery | 6.4, the same ten working days |
| 10.1 and 10.2, 30 days' notice, or the missing notice paid | 8.3, the same notice, the same payment to close sooner |
| 10.3, two working days during the opening check | 3.5, the same two days |
| 10.4, up to two paid handover days | 8.5, the same two days, paid by the client |
| 12, twelve months, 24-month declaration, penalty | 12, the same twelve months and declaration, the client's own penalty |
| 14.1, rights to rebase on the freelancer's payment | 10.1, rights to the client on the client's payment, or later when rebase pays the freelancer |
| 16.2 and Allegato A, sub-processor with the client's authorisation | 14.3 and Allegato A.5, the authorisation |

Open in these drafts, for the lawyer and for Ivan (the highlighted proposals): whether
the service shape of article 3 keeps rebase clear of labour supply (legislative decree
276/2003) when the client coordinates a freelancer day to day; whether a fee on hiring a
presented professional as an employee (12.2) needs an employment-agency authorisation,
or the penalty of 12.4 should stand alone, and its amount; the liability cap (11.3) and
whether data and confidentiality breaches sit outside it; the 48 hours for a data breach
(A.7); the NDA's reach back to information received before its signature (8.1) and the
companies as third-party beneficiaries (5.3, article 1411 of the civil code).
