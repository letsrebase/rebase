# The AI Socratic talk deck (REB-1172)

**Intent.** Ivan Sala and Lorenzo Fiore give a 15-minute talk at AI Socratic's AI Aperitivo
(Milan, 2026-11-25): *Technical interviews are useless in the AI era: an AI native approach to
evaluate a candidate*. The deck carries a bold, opinionated claim (the classic tech interview
measures what the machine now does) and proposes the alternative rebase built: read how a person
actually works with AI, from their own agent logs, with `rebase-verify`.

**Decided by Ivan (2026-11-25).** Slides in English, spoken in Italian. 15 minutes. The site's deck
style (`/company`, `/pitch`). Ivan opens (rebase, who we are, a show of hands, the claim, three
ages: yesterday LeetCode and kata, today an AI-assisted loop with Wonderful's May–June 2026 loop
as the example, tomorrow the AI-native evaluation); Lorenzo goes deep on `rebase-verify` and runs
the demo.

**Goals.** Readable from the back of a room; one idea per slide; an interactive moment (the show
of hands); every claim about `rebase-verify` true to its README and to the hub's rules (no score
about a person, a person decides, logs stay on the machine).

**Live.** Ivan asked for it on the site the same day, so it is registered like `/company`: nginx,
the flake module and its VM test, the link, share-card and footer suites, e2e.

**Anti-goals.** Repo jargon on a slide; claiming the tool rates people; naming Ivan's or rebase's
clients.

**Approaches.** (1) A new page in `projects/website` on the shared `pitch.css` and `deck.js`,
rendered to PNG and PDF locally (chosen: identical look, one source, ready to publish later).
(2) A copy of the Desktop pitch folder (drifts from the site's tokens). (3) A slides artifact
type (loses the site's look).

**Plan.**
1. `projects/website/src/talks/technical-interviews.html`, 19 slides, registered like `/company`
   at `/talks/technical-interviews` (Ivan chose the path on 2026-11-25).
2. Render every slide with `?static=1` through Playwright into PNGs and a PDF; check that no
   slide overflows its 1920×1080 frame.
3. Hand the PDF and the local URL to Ivan; the site question stays with him.

**Second draft (Ivan, 2026-11-25).** Bolder: full-colour slides (watermelon, gold) for the loud beats, no member count, Ivan's bio from his LinkedIn headline with Owlise as a private AI platform, no client named, the show of hands only on code written by hand, a new claim (AI writes code better than us), stages left-aligned. No work
profile had been sent or approved yet that day.
