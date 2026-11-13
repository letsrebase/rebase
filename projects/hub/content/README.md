# content

Source text for the perks a member downloads, kept
apart from the apps because it is prose reviewed on its own terms, not markup or code.

- `guide-first-steps-freelance.md`: the guide to the first steps as a freelancer, the
  community's second perk (ORB-69). Italian, because it is what a member reads, not what
  the repository says about itself. Approved by Lorenzo on 2026-09-10 (PR #8).

The contracts a freelancer signs are not here any more: the texts, their typesetting and their
signature moved to PigroCRM with the engagements (REB-1108).

## How a file here becomes something a member downloads

`../tools/build_guide_pdf.py` typesets this Markdown with pandoc and Typst into
`../packages/core/src/rebase_core/perks/`, from where `GET /api/hub/me/guide` hands it
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
