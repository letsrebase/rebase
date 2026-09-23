# content

Source text for the perks a member downloads and the contracts a member signs, kept
apart from the apps because it is prose reviewed on its own terms, not markup or code.

- `guida-primi-passi-freelance.md`: the guide to the first steps as a freelancer, the
  community's second perk (ORB-69). Italian, because it is what a member reads, not what
  the repository says about itself. Approved by Lorenzo on 2026-09-10 (PR #8).
- `contratti/`: the contract between rebase and a freelancer (roadmap #285, REB-386), in
  two documents. `contratto-quadro.md` is signed once and holds every rule;
  `lettera-di-incarico.md` is signed per engagement and holds the client, the work, the
  dates and the numbers. Italian for the same reason. A draft, see
  [The contracts](#the-contracts) below.

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
The version is what the member area will record against a signature (REB-339), so a
change to the text after the first signature is a new version, not an edit.

Two markers, both handled by `../tools/build_contract_pdf.py`:

- `{{key}}`: a field, lowercase words joined by hyphens. The data fill it; without a
  value it prints as a labelled blank line, so the same file is the template and the
  printable form. The keys are Italian because the blank prints its key as the label.
- `[[text]]`: a proposal still to be decided, highlighted in the draft. The build
  refuses a file whose `status` is not `draft` while one is left.

```
uv run python projects/hub/tools/build_contract_pdf.py                       # both, blank
uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico \
  --data projects/hub/content/contratti/incarico.esempio.json               # one, filled
```

The PDFs land in `contratti/dist/`, which git ignores: nothing serves them, so unlike the
guide nothing is committed or locked. `contratti/rebase.json` holds rebase's company data
and the defaults every letter starts from (the payment term: 30 days from the end of the
invoice's month); its `null`s wait for the SRL (roadmap #284). Until then the data of
whoever signs for rebase goes in `contratti/rebase.local.json`, which git ignores and the
build reads over `rebase.json` when it is there: this repository is public. `compenso` is
a JSON number, and the build refuses one given as text, not above zero, or with more than
two decimals.
`incarico.esempio.json` is fiction and names every field the letter asks for, which
`packages/core/tests/test_contract_pdf.py` checks. Real data names a real person and a
real client: keep it outside the repository or in a `*.local.json`, which git ignores.

### Open before the first signature

The highlighted proposals, and what the text cannot settle on its own. Decided by Ivan on
2026-09-23: the payment default (30 days from the end of the invoice's month, 7.1), the
freelancer is paid whether or not the client has paid and rebase aligns the client's
terms to that (7.2), the social security surcharge is inside the fee (5.5), and the 15%
of 12.2.

- **Non-circumvention** (12): it covers every company rebase introduces, not only the
  ones an engagement came from, for twelve months from the introduction or the last
  engagement. A company the freelancer declares within seven days as a client of the
  previous 24 months is exempt; going direct early means paying rebase 15% of what the
  freelancer invoices that company until the twelve months end (decided). Hidden, it
  costs the 15% plus a penalty; the €2,000 of the first draft was too low (Ivan), and the
  proposal is three times the last engagement's average monthly fee, at least €5,000,
  which scales with the engagement and gives a judge less reason to cut it under article
  1384 of the civil code.
- **Notice**: 30 days per engagement. When the client stops, rebase may close sooner
  only by paying the fee for the notice days that are missing, so the company contract
  has to give rebase at least that much notice (10.2). Two days during the opening check,
  up to two paid handover days (10.3, 10.4).
- **Confidentiality** lasts three years after the last engagement (13.3). **Court**: Milan,
  to match the SRL's registered seat (18.3).
- **Autonomy, as the text and in practice** (4.2): the freelancer works alone and in
  person for a client who coordinates the work, which is the pattern article 2 of
  legislative decree 81/2015 reclassifies as employment when the client sets the hours
  and the place. The letter says the coordination is agreed «di comune accordo», the
  wording of article 409 of the civil procedure code; a labour lawyer should read this
  before the SRL signs, and if a relationship ever counted as a coordinated collaboration
  the exclusive court of 18.3 would not hold (article 413 of the same code).
- **Signing in the member area**: a click is a simple electronic signature. It records
  consent, but a court may not accept it as the separate written approval that articles
  1341 and 1342 of the civil code ask for the onerous clauses. REB-339 should ask for that
  approval as its own step, and an advanced signature (a one-time code) for the framework
  agreement is worth weighing.
- **The company contract** (roadmap #286) has to give rebase what this one promises:
  client payment terms that let rebase pay the freelancer at 30 days from the end of the
  month, rights on the work passing on payment, a notice from the client at least as long
  as the freelancer's, and the client's authorisation to appoint the freelancer as a
  sub-processor.

No lawyer has read these texts (#285: our own draft).
