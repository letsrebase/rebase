# The weekly Snyk scan

Every Monday `.github/workflows/snyk-weekly.yml` scans `main` with Snyk and files what
it finds as one Linear card and one GitHub issue, the way Dependabot files an alert. It
fixes nothing and ignores nothing: whoever picks the card up does that (REB-525).

## What runs on Monday

At 06:00 UTC, which is after Dependabot's 06:00 Europe/Rome run in both summer and
winter time:

1. **`snyk test --all-projects`** on the pnpm workspace, read from `pnpm-lock.yaml` with
   nothing installed; each workspace package is its own project.
2. **`snyk test`** on the Python dependencies. Snyk cannot read `uv.lock`, so they are
   exported (`uv export --frozen --no-dev --all-packages --no-emit-workspace
   --no-hashes`), installed into a throwaway venv with their environment markers
   honoured, and given to Snyk as that venv's `pip freeze`. Stripping the markers
   instead fails: `pywin32` and `colorama` cannot install on Linux, and Snyk stops on
   SNYK-OS-PYTHON-0013.
3. **`snyk code test`**, which reads `.snyk` and the ignores made in Snyk's UI.
4. **`snyk container test`** on the five images the repository builds, built as
   `ci.yml` builds them: `pigrocrm-api`, `pigrocrm-web`, `rebase-api`, `rebase-web`,
   `website-web`.

Every scan's JSON is uploaded as the run's `snyk-json` artifact (kept 30 days). Then
`snyk-weekly.mjs` turns it into one table and files it. A scan that errors (anything
but "nothing found" or "something found") fails the job before anything is filed, so a
report never reads clean because a scan did not run; an error Snyk reports only inside
its JSON is listed under **Did not scan**, keeps a week from reading clean, and turns
the run red after filing. `snyk monitor` is never run: it would add CLI projects to the
org next to the ones the GitHub import made. `SNYK_TOKEN` is only in the environment of
the steps that run Snyk, and the checkout keeps no token for the builds.

## What gets filed

The report is one table per severity (Critical, High, Medium) with the source, the
package and version or the file and line, the title, the Snyk id with its CVEs, and the
fixed-in version; lows are a count per source. An advisory found in several places (two
workspace packages, the two API images that share a base) is one row that names them
all. The run's job summary always has the whole table; what is filed is cut to fit
GitHub's 65,536 characters, dropping the least severe rows first and saying so.

- **Nothing at medium or above:** nothing is filed. An issue or a card still open from
  an earlier week gets a comment that says the week was clean, above the counts.
- **Findings, and nothing open from an earlier week:** a Linear card in team `rebase`
  titled `Fix the Snyk findings of the week of <Monday>`, labelled `security`,
  `parallel` and `area:ci`, with no assignee, in `Todo`, its priority from the worst
  severity (critical Urgent, high High, medium Medium); and a GitHub issue with the same
  title and table, labelled `security` (created if missing). Each links to the other.
- **Findings, and last week's card or issue still open:** the new report goes on it as a
  comment, and nothing new is filed; a worse week raises the card's priority, a better
  one leaves it. The other half of the pair is only created when it never existed; a
  card or an issue somebody closed is not refiled.
- **An advisory with no fix yet counts like any other.** Most rows today are Debian
  packages in the API images waiting for a Debian update, and the report's second line
  says how many rows can be fixed now. Waiting is written on the open card, which stays
  open: a card closed while rows are left is filed again the next Monday.
- **Linear refuses the card** (the workspace is on the Free plan, which refuses a new
  issue past its cap; `docs/tracker.md` § Where things are): the GitHub issue is still
  filed and says why, and the job fails so the red run is seen. The next Monday tries
  the card again.

The next run finds last week's pair by a marker: an HTML comment in the GitHub issue's
body (`<!-- snyk-weekly -->`, with the card's id beside it), and the card that id names.
Without one it searches the team for an open card labelled `security`, titled as above,
that ends with the sentence `Filed by the snyk-weekly workflow`; Linear shows an HTML
comment as text, so the card carries no hidden marker.

## The two secrets

Both are repository secrets of `letsrebase/rebase`, and neither is read by anything but
this workflow.

- **`SNYK_TOKEN`**: a Snyk token that may test in the org `ciao` (app.snyk.io): a service
  account token when the plan offers one, otherwise the personal API token from
  Account settings.
- **`LINEAR_API_KEY`**: a Linear personal API key (Settings, Account, Security & access)
  of an account on team `rebase`, with read and write access: the run creates cards,
  updates their description and priority, and comments. The cards show that account
  as their creator.

To set one, or to rotate it: create the new token in Snyk or Linear, store it, check it
with a dry run, then revoke the old one where it was created.

```bash
gh secret set SNYK_TOKEN -R letsrebase/rebase        # paste the value when asked
gh secret set LINEAR_API_KEY -R letsrebase/rebase
gh workflow run snyk-weekly.yml -R letsrebase/rebase -f dry_run=true
gh run watch -R letsrebase/rebase                     # then read the job summary
```

## A dry run

`gh workflow run snyk-weekly.yml -f dry_run=true` runs every scan, uploads the JSON,
prints the report and what it would create or comment on, and files nothing. It still
looks up last week's card and issue, so it also proves both secrets work. Without
`dry_run` the same command files for real, which is how to run it outside Monday. A run
started on any branch but `main` (`--ref <branch>`) is always a dry run.

Locally, with the Snyk CLI logged in (`snyk auth`), from the repository root:

```bash
mkdir -p /tmp/snyk-json
snyk test --all-projects --json-file-output=/tmp/snyk-json/pnpm.json
snyk code test --json-file-output=/tmp/snyk-json/code.json
node tooling/snyk-weekly/snyk-weekly.mjs --dir /tmp/snyk-json --dry-run
```

The file names are the contract: `pnpm.json`, `python.json`, `code.json` and
`image-<name>.json`. Without `GITHUB_TOKEN` and `LINEAR_API_KEY` in the environment the
dry run skips the lookups and says so.

## Changing it

- The tests are `node --test tooling/snyk-weekly/*.test.mjs`, run by the `tooling`
  job of `ci.yml`. Their fixtures are trimmed from real Snyk output; regenerate one with
  `--json-file-output` and trim it again rather than writing JSON by hand.
- The CLI is a pinned binary checked against its SHA-256, and Dependabot does not see
  it: bump `SNYK_VERSION` and `SNYK_SHA256` in the workflow together, taking the
  checksum from `https://downloads.snyk.io/cli/v<version>/snyk-linux.sha256`.
- A new image the repository ships gets a build-and-scan step next to the others, named
  `image-<name>.json`.
