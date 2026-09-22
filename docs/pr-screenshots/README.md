# Before and after screenshots, and a video, on pull requests

Every pull request that changes something a person could see ends with a **before and
after** image in its body. One "after" shot is not enough: only the pair shows what
moved, and the point is that a reviewer sees the difference without checking out the
branch and without reading the diff to reconstruct what the page used to look like.

Every pull request that changes something a person could **do** also ends with a
**video of the feature in use**: the pairs show what moved, the video shows it working,
click by click, so a reviewer watches the flow instead of reconstructing it from two
still frames and the diff.

The `pr-creation` skill (`.claude/skills/pr-creation/SKILL.md`) asks for the section;
this file is how the picture and the video get made in this monorepo.

## What counts as a visible change

Broader than "frontend files changed". If a reviewer could spot it on screen, it needs a
pair:

- A copy change, including one word of a label, a toast or an empty state.
- A button that appears, disappears, becomes disabled or gains a tooltip.
- A new status pill, badge, column or pane.
- Anything that changes spacing, ordering, or what a menu or a wizard step offers.
- A page of the public site or of the hub, at the viewport it was designed for.

If a pair genuinely cannot be captured, the section stays and says why. Deleting it
reads as forgetting.

## What needs a video

Anything a person drives. If the change answers to a click, a keystroke or a scroll,
the PR shows it happening:

- A button, a menu item or a link that does something: the click and what follows.
- A wizard, a dialog, a form: from the first field to the state after submit.
- A state that follows an action: the timer that starts, the proforma that becomes an
  invoice, the row that moves, the toast that appears and goes.
- A page whose behaviour changed on scroll, hover, resize or keyboard.

One video per PR, of the whole flow the PR adds or changes, ten to forty seconds; a
second only when the PR carries two flows a reader would not follow in one take. A
change nobody interacts with (one label, a colour, a reordered column) gets its pairs
and a line saying why there is no video. The video is of the **after** only, your
branch running; there is no "before" video, the pairs already say what moved, and a
recording from the `origin/main` worktree is not asked for.

## Capturing the pair

Both frames: the same page, the same data, the same viewport. The only difference is
which code runs.

**Take the "before" from a second worktree on the base branch.** Never swap files in
place: `git checkout origin/main -- path` overwrites your index and working tree with no
recovery copy, and the restore brings back the last commit rather than what you had in
progress.

```bash
git fetch origin
git worktree add ../<repo>-before origin/main
cd ../<repo>-before && uv sync --frozen && pnpm install --frozen-lockfile --prefer-offline
```

Then run the app you changed from that worktree on a second port, beside your own:

| App | Your worktree | The "before" worktree | Notes |
|---|---|---|---|
| CRM web (`projects/pigrocrm/apps/web`) | `pnpm --filter web dev` on 5173 | `pnpm --filter web dev --port 5175` | Both proxy `/api` to the one API on `localhost:8000`, so the data is identical by construction. Run the API from your own worktree. |
| Hub web (`projects/hub/apps/web`) | `pnpm --filter hub dev` on 5180 | `pnpm --filter hub dev --port 5182` | Both proxy `/api` to the hub API on 8084. The page lives under `/hub/`. |
| Website (`projects/website`) | `pnpm --filter website preview` on 4173 | `pnpm --filter website build && pnpm --filter website preview --port 4175` | Static: build first, preview serves nginx's path map. |

No `--` before `--port`: pnpm 11 hands it to Vite literally, Vite reads it as the end of
its options, and the port is ignored (the dev servers then bump to the next free port
without a word; the website's preview has `strictPort` and dies). Read the `Local:` line
Vite prints and open that URL, not the one you asked for.

If the PR changes the API as well as the web, the before frame is "old web, new API"
and can show a state `main` never had: say so under the pair.

A page behind the CRM's login needs a session on both origins: log in once per origin
in the same browser, or set the cookie with whatever session tool you use.

Capture with the Playwright MCP: `browser_resize` to the same viewport for both frames
(1440×900 for the CRM and the hub, 390×844 for the website when the change is about a
phone), then `browser_take_screenshot` with `scale: "css"`, so the pixels match the
CSS-pixel rectangles `browser_snapshot` reports and a box measured there lands where it
should (`"device"` doubles everything on a Retina display), and a file name that says
which frame it is: `before-invoice-detail.png`, `after-invoice-detail.png`. Set up the
fixture you need (an issued invoice, a draft, a signup) **while you build the change**,
not after the PR is open; the state is cheap to arrange while it is in your head.

Afterwards:

```bash
git worktree remove ../<repo>-before
```

## One image per pair

Compose the two frames side by side, labelled, in one file. `compose.py` beside this
file does it with Pillow, which the repository does not depend on and `uv` fetches on
the spot:

```bash
uv run --no-project --with pillow docs/pr-screenshots/compose.py \
  before-invoice-detail.png after-invoice-detail.png pair-1-invoice-detail.png \
  --box 760,180,640,720
```

`--box x,y,w,h` draws an outline on the after frame around what the change adds, in
source pixels of that frame, in a colour the product's palette does not use, so it reads
as an annotation and not as UI. Measure the box from the after screenshot (Playwright's
`browser_snapshot` with `boxes: true` gives element rectangles); never estimate it. A
guessed box that clips the very text the pair exists to show looks plausible and is
wrong. Crop chrome that is identical in both frames (the sidebar above all) with
`--crop-left <px>` so the content fills the image.

**Open the composite and read it before uploading.**

## Recording the video

The Playwright MCP takes the screenshots but does not record here: video is a launch
option of the MCP server (`--save-video`), the session's instance is not started with
it, and its output would land in the server's own directory anyway. `record.mjs` beside
this file drives its own Chromium, the one `@playwright/test` already installed for the
e2e tests, with Playwright's `recordVideo` on, draws a cursor so each click is visible
where it lands, and hands the `.webm` to `ffmpeg` (`brew install ffmpeg`) for an H.264
`.mp4`, the container a PR body plays inline in every browser. No GIF: an `.mp4` is
smaller and sharper, and `gh` uploads it.

The recording is the **after**: from **your worktree**, the app running as for the
after frame, on the same data and the same viewport, and with the browser's default
locale, as the MCP's screenshots are. Write the steps as a small module that does what
a person would do, and keep it out of the repository (the scratchpad, `/tmp`):

```js
// steps.mjs: issue the proforma and land on the invoice
export default async function (page, { pause }) {
  await page.getByRole('link', { name: 'PF 2026/3' }).click()
  await pause()
  await page.getByRole('button', { name: 'Emetti' }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Conferma' }).click()
  await page.waitForURL(/\/app\/fatture\/\d+/)
  await pause(1500)
}
```

```bash
node docs/pr-screenshots/record.mjs steps.mjs demo-1-issue-proforma.mp4 \
  --url http://localhost:5173/app/fatture --viewport 1440x900
```

`--url` is the page the flow starts from and the script waits for it to be idle before
the first step. `pause()` waits the default `--pause` (800 ms), `pause(ms)` what you say:
a reader needs a beat after each action to see what changed, and Playwright on its own
clicks faster than an eye follows. The recording runs headless at 25 frames per second
in the viewport you give (1440×900 for the CRM and the hub, 390×844 for the website
on a phone, the same as the after frame), and the file name says which flow it is. A
page behind the CRM's login takes `--storage-state state.json`, a file saved from a
logged-in Playwright context (`context.storageState({ path })`), or the flow starts at
the login page and logs in as its first step, which is the honest video of a feature a
new user meets after logging in. `--keep-webm` keeps the raw recording beside the
`.mp4` when ffmpeg's result needs checking.

**The run is confined.** On macOS the script re-executes itself under Seatbelt
(`sandbox-exec`, the mechanism Claude Code's own command sandbox uses on a Mac) with a
profile written for the run: Chromium, the steps module and ffmpeg may write only to the
output file's directory, the temp directories (`$TMPDIR` and the user's own under
`/var/folders`), the user's cache directory and `/dev`, and may open network connections
only to `localhost`, where the app under test runs. So a steps
module cannot write a file elsewhere, and a page under test cannot call home while it is
recorded (measured: a write to `$HOME` fails with `EPERM`, a `fetch` to example.com
fails from the page and from node, `localhost:4173` answers). Reads are not confined:
the browser and the module still read whatever the user can read. The first line of the
run says what the profile allows; `--no-sandbox` opts out and says so, which is what to
try when a recording fails for a reason that smells of permission (a store the browser
wants to write outside its temp directory, a proxy on another host). Linux has no
`sandbox-exec`, the run says so and records unconfined; a bubblewrap profile is not
written yet, and Chromium inside `bwrap` needs its own sandbox off, so it is not a
one-liner.

The script prints the length and the size at the end. A step that fails (a selector
that never resolves, a page that never goes idle) stops the run with the error and the
path of the partial `.webm`, which is where you see how far the flow got. **Open the
file and watch it before uploading** (`open demo-1-issue-proforma.mp4`): a flow that
stalled on a selector records as a still page, and a still page is not a video of the
feature.

## Uploading with `gh --attach`

`gh` 2.99.0 or newer (`gh --version`), which is what makes the CLI able to upload a
`user-attachments` image or video: the only kind a PR body on a private repository
renders. Files committed to the repository, raw links and signed URLs all render broken.

**Attach in the command that opens the PR**, not in an edit after it. Write the
reference in the body first; `gh` rewrites the reference to the uploaded URL and keeps
your alt text. Without a reference the file is appended at the end, which is not where a
numbered pair belongs. A video has no alt text (GitHub renders it as a player), so its
reference is written as an embed with an empty one, `![](./demo.mp4)`, **in a paragraph
of its own**: a blank line above and below. The embed form is the contract: `gh`
rewrites an embedded video reference into the bare URL GitHub plays (measured on gh
2.100.0, 2026-09-22; the same rewrite is in the 2.99.0 source), while a plain link
`[demo.mp4](./demo.mp4)` degrades to a link. The older note here asked for a second
`sed` edit to turn that link into a bare URL; with the embed form the step is gone.

```bash
# body.md holds, in the Screenshots and video section:
#   **1. Invoice detail, the PDF pane on the right**
#   ![Invoice detail, before and after](./pair-1-invoice-detail.png)
#
#   **Video: issuing the proforma, from the list to the invoice**
#
#   ![](./demo-1-issue-proforma.mp4)
gh pr create --body-file body.md \
  --attach ./pair-1-invoice-detail.png --attach ./demo-1-issue-proforma.mp4
```

`--attach` repeats for several files and works on `gh pr create`, `gh pr edit` and
`gh pr comment`. On a partial failure the files that uploaded stay attached, the URL is
still printed and the exit code is non-zero: read the exit code, not the URL.

## Verify before calling it done

```bash
body=$(gh pr view <n> --json body --jq .body)
grep -o user-attachments <<<"$body" | wc -l     # pairs + videos, + any mention in prose
if grep -o '](\./[^)]*)' <<<"$body"; then
  echo "ERROR: the paths above never got rewritten." >&2; false
else
  echo "OK: no local path remains."
fi
```
Then open the PR in a browser and look: a broken attachment still passes a text check,
and the video is a player with a first frame, not a link (a link means the body wrote
it as one, `[demo.mp4](./demo.mp4)` instead of the embed `![](./demo.mp4)` above). The
images and the video live **in the PR body**. A Linear comment
may carry them too, and a list of local paths handed to the reviewer is never the
substitute.
