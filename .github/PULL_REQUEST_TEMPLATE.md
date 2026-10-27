## What this changes

<!-- One paragraph, in the first person. The first sentence names the problem: what was
wrong, missing or impossible before. Then what is true now, in words a product person
understands. -->

## How I verified it

<!-- The command you ran and what it said, not "tests pass". If a UI changed, say what you
looked at. If you could not verify something, say which part and why. -->

## Anything a reviewer should look at twice

<!-- The bit you are least sure about, a decision that could have gone the other way, or a
path that is not covered by a test. Delete the section if there is genuinely nothing. -->

## Screenshots and video

<!-- For anything a person could see: one before-and-after pair per change, composed into a
single side-by-side image. For anything a person could do: one video of the feature in
use, recorded with docs/pr-screenshots/record.mjs. Reference each file by its local path
and attach them all when you open the PR: `gh pr create --body-file body.md --attach
./pair-1.png --attach ./demo-1.mp4` (docs/pr-screenshots/README.md). If nothing visible
changed, or a pair or the video cannot be captured, say so and why. Keep this section
either way.

Sections to add only when true, between «How I verified it» and this one: «What did not
change, on purpose» (a reviewer could assume a neighbouring thing moved); «Migrations»
(projects/*/packages/core/migrations/versions/ changed); «API changes» (a route, a
request or a response shape changed); «MCP surface» (an MCP tool was added, removed or
renamed). -->

Linear: REB-<number>.
