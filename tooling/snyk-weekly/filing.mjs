// Where the weekly Snyk report goes (REB-525): one Linear card and one GitHub issue,
// linked to each other, or a comment on last week's while either is still open.
// The two trackers arrive as small client objects (see snyk-weekly.mjs for the real
// ones), so filing.test.mjs can run every branch below against fakes.

export const GITHUB_MARKER = "<!-- snyk-weekly -->";
// Linear renders an HTML comment as visible text (checked on REB-525, 2026-09-26), so the
// card's marker is a plain sentence the next run searches for.
export const LINEAR_MARKER = "Filed by the snyk-weekly workflow";
const WORKFLOW = "`.github/workflows/snyk-weekly.yml`";

// GitHub caps an issue body and a comment at 65,536 characters; the report is cut to
// leave room for the few lines around it.
export const REPORT_MAX = 60000;

export function linearLinkMarker(identifier) {
  return `<!-- snyk-weekly-linear: ${identifier ?? "none"} -->`;
}

// undefined: the body has no link marker at all; null: it says no card was filed.
export function parseLinearLink(body) {
  const m = /<!-- snyk-weekly-linear: (\S+) -->/.exec(body ?? "");
  if (!m) return undefined;
  return m[1] === "none" ? null : m[1];
}

export function parseGithubLink(description, repo) {
  const escaped = repo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m = new RegExp(`https://github\\.com/${escaped}/issues/(\\d+)`).exec(description ?? "");
  return m ? Number(m[1]) : null;
}

// What to do this week, from what the scan found and what is still open from before.
// Nothing at medium or above files nothing; an open card or issue gets a comment
// instead of a new one; and the other half of the pair is only created when it never
// existed (a card Linear refused, an issue that failed), never because somebody closed
// it: closing one side is a person's answer, and the next week does not overrule it.
export function planFiling({ hasFindings, openGithub, openLinear }) {
  const plan = {
    createLinear: false,
    createGithub: false,
    commentLinear: Boolean(openLinear),
    commentGithub: Boolean(openGithub),
  };
  if (!hasFindings) return plan;
  if (!openGithub && !openLinear) return { ...plan, createLinear: true, createGithub: true };
  if (openGithub && !openLinear && !openGithub.linearIdentifier) plan.createLinear = true;
  if (openLinear && !openGithub && openLinear.githubNumber == null) plan.createGithub = true;
  return plan;
}

export function linearDescription({ report, githubUrl }) {
  const issue = githubUrl ? ` The same table is on the GitHub issue ${githubUrl}.` : "";
  return [
    `**Observed.** The weekly Snyk scan of \`main\` found what the table below lists.${issue}`,
    "**Needed.** Fix what it lists: a version bump lands here, anything larger gets a card of its own. A finding that does not apply is ignored in Snyk's UI with its reason, since the org's Consistent Ignores read no inline comment.",
    report,
    `${LINEAR_MARKER} (${WORKFLOW}): the next Monday run comments here while this card is open.`,
  ].join("\n\n");
}

export function githubBody({ report, linearCard, linearError }) {
  const link = linearCard
    ? `Linear: [${linearCard.identifier}](${linearCard.url}).`
    : `**No Linear card:** ${linearError}. The next run tries again while this issue is open.`;
  return [
    GITHUB_MARKER,
    linearLinkMarker(linearCard?.identifier ?? null),
    link,
    report,
    `Filed by ${WORKFLOW}, the way Dependabot files an alert: the next Monday run comments here while this issue is open. Close it when the table is fixed.`,
  ].join("\n\n");
}

// A clean week says so only when every scan ran; one that errored inside its JSON (a
// workspace project Snyk could not test) makes it a partial result, and it reads as one.
function comment({ report, clean, partial, extra = [] }) {
  let lead = [];
  if (clean) {
    lead = partial
      ? ["Not every scan ran this week (**Did not scan**, below); the ones that did found nothing at medium or above."]
      : ["Nothing at medium or above this week."];
  }
  return [...lead, ...extra, report].join("\n\n");
}

const PRIORITY_NAMES = { 1: "Urgent", 2: "High", 3: "Medium" };

// The issue body once its missing card exists: the marker names the card, and the
// "No Linear card" line becomes the link.
function recordCard(body, card) {
  const none = linearLinkMarker(null);
  const marked = body.includes(none)
    ? body.replace(none, linearLinkMarker(card.identifier))
    : `${body}\n\n${linearLinkMarker(card.identifier)}`;
  return marked.replace(/^\*\*No Linear card:\*\*.*$/m, `Linear: [${card.identifier}](${card.url}).`);
}

// Runs the plan. Returns what it did (or, in a dry run, would do) and the failures that
// must turn the job red. A GitHub error is thrown: the job fails and nothing after it
// runs. A Linear error is caught and recorded instead, so the GitHub issue is still
// filed and says why the card is missing: the workspace is on Linear's Free plan, which
// refuses a new issue past its cap (docs/tracker.md, § Where things are).
export async function fileReport({ title, report, summary, priority, scanErrors = 0, repo, linear, github, dryRun, log = () => {} }) {
  const actions = [];
  const failures = [];
  const act = (line) => {
    actions.push(line);
    log(`${dryRun ? "[dry run] would " : ""}${line}`);
  };
  const hasFindings = Boolean(summary.worst);

  if (!github) log("No GitHub token: the open issue was not looked up.");
  const openGithub = github ? await github.findOpen() : null;

  let openLinear = null;
  let linearError = null;
  if (!linear) {
    linearError = "LINEAR_API_KEY is not set";
  } else {
    try {
      // The card the open issue names comes first; the marker search is the fallback.
      openLinear = await linear.findOpen({ identifier: openGithub?.linearIdentifier ?? null });
    } catch (e) {
      linearError = `the Linear lookup failed (${e.message})`;
    }
  }
  if (linearError) {
    log(`Linear unavailable: ${linearError}`);
    // A dry run on a machine without the key is a preview, not a failure.
    if (!(dryRun && !linear)) failures.push(`Linear: ${linearError}`);
  }
  if (openGithub) log(`Open GitHub issue from a previous run: #${openGithub.number} (${openGithub.url})`);
  if (openLinear) log(`Open Linear card from a previous run: ${openLinear.identifier} (${openLinear.url})`);

  const plan = planFiling({ hasFindings, openGithub, openLinear });
  // Unknown is not the same as absent: with Linear unreachable the card may well be
  // open, so nothing is created or commented there, and the issue's `none` marker has
  // the next run try again.
  // A dry run with no key still shows the card it would file.
  if (linearError && !(dryRun && !linear)) plan.createLinear = plan.commentLinear = false;

  // 1. The Linear card, when one is due. Its failure is recorded, not thrown.
  let card = null;
  let cardError = linearError;
  if (plan.createLinear) {
    act(`create the Linear card "${title}" (priority ${PRIORITY_NAMES[priority]}, labels security, parallel and area:ci, state Todo, no assignee)`);
    if (!dryRun) {
      try {
        card = await linear.create({ title, description: linearDescription({ report, githubUrl: openGithub?.url }), priority });
        log(`Created ${card.identifier}: ${card.url}`);
      } catch (e) {
        cardError = `Linear refused the card (${e.message})`;
        failures.push(`Linear: ${cardError}`);
        log(cardError);
      }
    }
  }
  const cardFiled = Boolean(card) || (dryRun && plan.createLinear);

  // 2. The GitHub issue, linked to the new card or to last week's open one.
  let issue = null;
  if (plan.createGithub) {
    act(`create the GitHub issue "${title}" in ${repo} with the label security${cardFiled || openLinear ? "" : ", saying why there is no Linear card"}`);
    if (!dryRun) {
      await github.ensureLabel();
      const linearCard = card ?? (openLinear ? { identifier: openLinear.identifier, url: openLinear.url } : null);
      issue = await github.create({ title, body: githubBody({ report, linearCard, linearError: cardError ?? "no card was due" }) });
      log(`Created #${issue.number}: ${issue.url}`);
    }
  }

  // 3. The links back, so neither side is filed twice next week.
  if (plan.createLinear && openGithub && cardFiled) {
    act(`record the new card on GitHub issue #${openGithub.number}`);
    if (!dryRun) await github.updateBody(openGithub.number, recordCard(openGithub.body, card));
  }
  if (plan.createGithub && (cardFiled || openLinear)) {
    const which = openLinear && !plan.createLinear ? openLinear.identifier : "the new card";
    act(`link ${which} to the new GitHub issue`);
    if (!dryRun && issue) {
      const target = card ?? openLinear;
      const description = card
        ? linearDescription({ report, githubUrl: issue.url })
        : `${openLinear.description}\n\nGitHub issue: ${issue.url}`;
      try {
        await linear.update(target.id, description);
      } catch (e) {
        failures.push(`Linear: linking ${target.identifier} to #${issue.number} failed (${e.message})`);
      }
    }
  }

  // 4. The comments on what was already open.
  const clean = !hasFindings;
  const partial = scanErrors > 0;
  if (plan.commentGithub) {
    const extra = [];
    if (card) extra.push(`Linear: [${card.identifier}](${card.url}).`);
    else if (plan.createLinear && cardError && !dryRun) extra.push(`**Still no Linear card:** ${cardError}.`);
    if (linearError && hasFindings) extra.push(`**No Linear update this week:** ${linearError}.`);
    act(`comment the ${clean ? "clean result" : "report"} on GitHub issue #${openGithub.number}`);
    if (!dryRun) await github.comment(openGithub.number, comment({ report, clean, partial, extra }));
  }
  if (plan.commentLinear) {
    act(`comment the ${clean ? "clean result" : "report"} on Linear card ${openLinear.identifier}`);
    if (!dryRun) {
      try {
        await linear.comment(openLinear.id, comment({ report, clean, partial, extra: issue ? [`GitHub issue: ${issue.url}`] : [] }));
      } catch (e) {
        failures.push(`Linear: the comment on ${openLinear.identifier} failed (${e.message})`);
      }
    }
    // A worse week raises the open card's priority; a better one leaves it, since
    // lowering it is the call of whoever works the card. 0 is Linear's "no priority".
    const current = openLinear.priority ?? 0;
    if (priority && (current === 0 || priority < current)) {
      act(`raise ${openLinear.identifier} to priority ${PRIORITY_NAMES[priority]}`);
      if (!dryRun) {
        try {
          await linear.setPriority(openLinear.id, priority);
        } catch (e) {
          failures.push(`Linear: raising the priority of ${openLinear.identifier} failed (${e.message})`);
        }
      }
    }
  }
  if (!actions.length) {
    act(hasFindings ? "file nothing: Linear is unavailable and the GitHub side needs nothing" : "file nothing: nothing at medium or above, and nothing open from a previous run");
  }
  return { plan, actions, failures, card, issue };
}
