import { test } from "node:test";
import assert from "node:assert/strict";
import {
  fileReport,
  githubBody,
  linearDescription,
  parseGithubLink,
  parseLinearLink,
  planFiling,
  GITHUB_MARKER,
  LINEAR_MARKER,
} from "./filing.mjs";

const REPO = "letsrebase/rebase";
const REPORT = "**Snyk, week of 2026-09-28.** table";
const FINDINGS = { worst: "high" };
const CLEAN = { worst: null };

// In-memory stand-ins for the two trackers, with the same five or six calls the real
// clients in snyk-weekly.mjs make. Every write is recorded; `failOn` makes one throw.
function fakeGithub({ open = null, failOn = null } = {}) {
  const calls = [];
  const guard = (name) => {
    if (failOn === name) throw new Error(`github ${name} failed`);
  };
  return {
    calls,
    async findOpen() {
      return open;
    },
    async ensureLabel() {
      guard("ensureLabel");
      calls.push(["ensureLabel"]);
    },
    async create({ title, body }) {
      guard("create");
      calls.push(["create", title, body]);
      return { number: 501, url: `https://github.com/${REPO}/issues/501` };
    },
    async updateBody(number, body) {
      calls.push(["updateBody", number, body]);
    },
    async comment(number, body) {
      calls.push(["comment", number, body]);
    },
  };
}

function fakeLinear({ open = null, failOn = null } = {}) {
  const calls = [];
  const guard = (name) => {
    if (failOn === name) throw new Error(name === "create" ? "You've exceeded the free issue limit" : `linear ${name} failed`);
  };
  return {
    calls,
    lookups: [],
    async findOpen(query) {
      guard("findOpen");
      this.lookups.push(query);
      return open;
    },
    async create(input) {
      guard("create");
      calls.push(["create", input]);
      return { id: "uuid-600", identifier: "REB-600", url: "https://linear.app/letsrebase/issue/REB-600" };
    },
    async update(id, description) {
      calls.push(["update", id, description]);
    },
    async setPriority(id, priority) {
      calls.push(["setPriority", id, priority]);
    },
    async comment(id, body) {
      calls.push(["comment", id, body]);
    },
  };
}

const openIssue = (linearIdentifier) => ({
  number: 400,
  url: `https://github.com/${REPO}/issues/400`,
  body: githubBody({ report: "old", linearCard: linearIdentifier ? { identifier: linearIdentifier, url: "u" } : null, linearError: "x" }),
  linearIdentifier: linearIdentifier ?? null,
});
const openCard = (githubNumber) => ({
  id: "uuid-590",
  identifier: "REB-590",
  url: "https://linear.app/letsrebase/issue/REB-590",
  description: linearDescription({ report: "old", githubUrl: githubNumber ? `https://github.com/${REPO}/issues/${githubNumber}` : null }),
  githubNumber: githubNumber ?? null,
  priority: 2,
});

const run = (overrides) =>
  fileReport({ title: "Fix the Snyk findings of the week of 2026-09-28", report: REPORT, summary: FINDINGS, priority: 2, repo: REPO, dryRun: false, ...overrides });

test("the plan: new pair, comments, the missing half, and nothing on a clean week", () => {
  assert.deepEqual(planFiling({ hasFindings: true, openGithub: null, openLinear: null }), { createLinear: true, createGithub: true, commentLinear: false, commentGithub: false });
  assert.deepEqual(planFiling({ hasFindings: true, openGithub: openIssue("REB-590"), openLinear: openCard(400) }), { createLinear: false, createGithub: false, commentLinear: true, commentGithub: true });
  assert.deepEqual(planFiling({ hasFindings: false, openGithub: null, openLinear: null }), { createLinear: false, createGithub: false, commentLinear: false, commentGithub: false });
  assert.deepEqual(planFiling({ hasFindings: false, openGithub: openIssue("REB-590"), openLinear: null }), { createLinear: false, createGithub: false, commentLinear: false, commentGithub: true });
  assert.equal(planFiling({ hasFindings: true, openGithub: openIssue(null), openLinear: null }).createLinear, true, "a card that was never filed is filed");
  assert.equal(planFiling({ hasFindings: true, openGithub: openIssue("REB-590"), openLinear: null }).createLinear, false, "a card somebody closed is not refiled");
  assert.equal(planFiling({ hasFindings: true, openGithub: null, openLinear: openCard(null) }).createGithub, true);
  assert.equal(planFiling({ hasFindings: true, openGithub: null, openLinear: openCard(400) }).createGithub, false);
});

test("the markers survive the round trip through the bodies", () => {
  const body = githubBody({ report: REPORT, linearCard: { identifier: "REB-600", url: "u" } });
  assert.ok(body.startsWith(GITHUB_MARKER));
  assert.equal(parseLinearLink(body), "REB-600");
  assert.equal(parseLinearLink(githubBody({ report: REPORT, linearCard: null, linearError: "refused" })), null);
  assert.equal(parseLinearLink("no marker here"), undefined);
  const description = linearDescription({ report: REPORT, githubUrl: `https://github.com/${REPO}/issues/77` });
  assert.ok(description.includes(LINEAR_MARKER));
  assert.equal(parseGithubLink(description, REPO), 77);
  assert.equal(parseGithubLink(description, "someone/else"), null);
  assert.doesNotMatch(description, /<!--/, "Linear shows an HTML comment as text, so the card carries none");
});

test("first week: one card and one issue, linked both ways", async () => {
  const github = fakeGithub();
  const linear = fakeLinear();
  const result = await run({ github, linear });
  assert.deepEqual(result.failures, []);

  const [create] = linear.calls;
  assert.equal(create[0], "create");
  assert.equal(create[1].priority, 2);
  assert.match(create[1].title, /^Fix the Snyk findings of the week of 2026-09-28$/);
  assert.ok(create[1].description.includes(REPORT));

  assert.deepEqual(github.calls.map((c) => c[0]), ["ensureLabel", "create"]);
  const issueBody = github.calls[1][2];
  assert.equal(parseLinearLink(issueBody), "REB-600");
  assert.match(issueBody, /Linear: \[REB-600\]\(https:\/\/linear\.app\/letsrebase\/issue\/REB-600\)/);

  const update = linear.calls.find((c) => c[0] === "update");
  assert.equal(update[1], "uuid-600");
  assert.equal(parseGithubLink(update[2], REPO), 501);
});

test("second week: comments on both, creates nothing", async () => {
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear({ open: openCard(400) });
  const result = await run({ github, linear });
  assert.deepEqual(result.failures, []);
  assert.deepEqual(linear.lookups, [{ identifier: "REB-590" }], "the card the issue names is asked for first");
  assert.deepEqual(github.calls.map((c) => [c[0], c[1]]), [["comment", 400]]);
  assert.deepEqual(linear.calls.map((c) => [c[0], c[1]]), [["comment", "uuid-590"]]);
  assert.ok(github.calls[0][2].includes(REPORT));
});

test("a pair linked one way only is linked both ways the next week", async () => {
  // Last week the card's link back failed: the issue names REB-590, the card no issue.
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear({ open: openCard(null) });
  const result = await run({ github, linear });
  assert.deepEqual(result.failures, []);
  const update = linear.calls.find((c) => c[0] === "update");
  assert.equal(update[1], "uuid-590");
  assert.equal(parseGithubLink(update[2], REPO), 400);
  assert.ok(!github.calls.some((c) => c[0] === "create" || c[0] === "updateBody"));

  // The other way round: the card links the issue, the issue names no card.
  const github2 = fakeGithub({ open: openIssue(null) });
  const linear2 = fakeLinear({ open: openCard(400) });
  await run({ github: github2, linear: linear2 });
  const body = github2.calls.find((c) => c[0] === "updateBody");
  assert.equal(parseLinearLink(body[2]), "REB-590");
  assert.ok(!linear2.calls.some((c) => c[0] === "create" || c[0] === "update"));
});

test("an open card that belongs to another issue is not taken for this issue's pair", async () => {
  const github = fakeGithub({ open: openIssue("REB-580") });
  const linear = fakeLinear({ open: openCard(399) });
  await run({ github, linear });
  assert.deepEqual(linear.calls, []);
  assert.deepEqual(github.calls.map((c) => c[0]), ["comment"]);
});

test("a clean week says so on what is open and files nothing new", async () => {
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear({ open: openCard(400) });
  await run({ github, linear, summary: CLEAN, priority: null });
  assert.deepEqual(github.calls.map((c) => c[0]), ["comment"]);
  assert.match(github.calls[0][2], /^Nothing at medium or above this week\./);
  assert.deepEqual(linear.calls.map((c) => c[0]), ["comment"]);
});

test("a clean week with a scan missing does not read as clean", async () => {
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear({ open: openCard(400) });
  await run({ github, linear, summary: CLEAN, priority: null, scanErrors: 1 });
  assert.match(github.calls[0][2], /^Not every scan ran this week/);
  assert.doesNotMatch(github.calls[0][2], /^Nothing at medium or above/);
  assert.match(linear.calls[0][2], /^Not every scan ran this week/);
});

test("a worse week raises the open card's priority, a better one leaves it", async () => {
  const worse = fakeLinear({ open: openCard(400) });
  await run({ github: fakeGithub({ open: openIssue("REB-590") }), linear: worse, priority: 1, summary: { worst: "critical" } });
  assert.deepEqual(worse.calls.find((c) => c[0] === "setPriority"), ["setPriority", "uuid-590", 1]);
  const better = fakeLinear({ open: openCard(400) });
  await run({ github: fakeGithub({ open: openIssue("REB-590") }), linear: better, priority: 3, summary: { worst: "medium" } });
  assert.ok(!better.calls.some((c) => c[0] === "setPriority"));
});

test("a clean week with nothing open touches nothing", async () => {
  const github = fakeGithub();
  const linear = fakeLinear();
  const result = await run({ github, linear, summary: CLEAN, priority: null });
  assert.deepEqual(github.calls, []);
  assert.deepEqual(linear.calls, []);
  assert.deepEqual(result.failures, []);
  assert.match(result.actions[0], /file nothing/);
});

test("Linear refusing the card still files the issue, says why, and fails the job", async () => {
  const github = fakeGithub();
  const linear = fakeLinear({ failOn: "create" });
  const result = await run({ github, linear });
  assert.deepEqual(github.calls.map((c) => c[0]), ["ensureLabel", "create"]);
  const body = github.calls[1][2];
  assert.match(body, /\*\*No Linear card:\*\* Linear refused the card \(You've exceeded the free issue limit\)/);
  assert.equal(parseLinearLink(body), null);
  assert.equal(result.failures.length, 1);
  assert.match(result.failures[0], /free issue limit/);
});

test("the week after a refused card, the card is filed and recorded on the open issue", async () => {
  const github = fakeGithub({ open: openIssue(null) });
  const linear = fakeLinear();
  const result = await run({ github, linear });
  assert.deepEqual(result.failures, []);
  assert.equal(linear.calls[0][0], "create");
  assert.equal(parseGithubLink(linear.calls[0][1].description, REPO), 400);
  const update = github.calls.find((c) => c[0] === "updateBody");
  assert.equal(parseLinearLink(update[2]), "REB-600");
  assert.doesNotMatch(update[2], /No Linear card/);
  assert.match(update[2], /^Linear: \[REB-600\]\(https:\/\/linear\.app\/letsrebase\/issue\/REB-600\)\.$/m);
  const comment = github.calls.find((c) => c[0] === "comment");
  assert.match(comment[2], /Linear: \[REB-600\]/);
  assert.ok(!github.calls.some((c) => c[0] === "create"));
});

test("Linear refusing the card again says so on the open issue", async () => {
  const github = fakeGithub({ open: openIssue(null) });
  const result = await run({ github, linear: fakeLinear({ failOn: "create" }) });
  assert.deepEqual(github.calls.map((c) => c[0]), ["comment"]);
  assert.match(github.calls[0][2], /\*\*Still no Linear card:\*\* Linear refused the card/);
  assert.equal(result.failures.length, 1);
});

test("a card somebody closed is not refiled while its issue stays open", async () => {
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear();
  await run({ github, linear });
  assert.deepEqual(linear.calls, []);
  assert.deepEqual(github.calls.map((c) => c[0]), ["comment"]);
});

test("an open card with no issue gets one, linked back, and the report as a comment", async () => {
  const github = fakeGithub();
  const linear = fakeLinear({ open: openCard(null) });
  await run({ github, linear });
  const create = github.calls.find((c) => c[0] === "create");
  assert.equal(parseLinearLink(create[2]), "REB-590");
  const update = linear.calls.find((c) => c[0] === "update");
  assert.equal(update[1], "uuid-590");
  assert.equal(parseGithubLink(update[2], REPO), 501);
  const comment = linear.calls.find((c) => c[0] === "comment");
  assert.match(comment[2], /GitHub issue: https:\/\/github\.com\/letsrebase\/rebase\/issues\/501/);
});

test("an issue somebody closed is not refiled while its card stays open", async () => {
  const github = fakeGithub();
  const linear = fakeLinear({ open: openCard(400) });
  await run({ github, linear });
  assert.deepEqual(github.calls, []);
  assert.deepEqual(linear.calls.map((c) => c[0]), ["comment"]);
});

test("a dry run looks things up and writes nothing", async () => {
  const github = fakeGithub({ failOn: "create" });
  const linear = fakeLinear({ failOn: "create" });
  const lines = [];
  const result = await run({ github, linear, dryRun: true, log: (l) => lines.push(l) });
  assert.deepEqual(github.calls, []);
  assert.deepEqual(linear.calls, []);
  assert.deepEqual(result.failures, []);
  assert.ok(result.actions.some((a) => a.startsWith("create the Linear card")));
  assert.ok(result.actions.some((a) => a.startsWith("create the GitHub issue")));
  assert.ok(result.actions.some((a) => a.startsWith("link the new card")));
  assert.ok(lines.every((l) => !l.startsWith("[dry run]") || l.startsWith("[dry run] would ")));
});

test("a dry run with no Linear key is a preview, not a failure", async () => {
  const result = await run({ github: fakeGithub(), linear: null, dryRun: true });
  assert.deepEqual(result.failures, []);
});

test("no Linear key on a real run files the issue and fails the job", async () => {
  const github = fakeGithub();
  const result = await run({ github, linear: null });
  assert.match(github.calls[1][2], /\*\*No Linear card:\*\* LINEAR_API_KEY is not set/);
  assert.deepEqual(result.failures, ["Linear: LINEAR_API_KEY is not set"]);
});

test("a Linear outage comments on the open issue, touches no card, and fails the job", async () => {
  const github = fakeGithub({ open: openIssue("REB-590") });
  const linear = fakeLinear({ failOn: "findOpen" });
  const result = await run({ github, linear });
  assert.deepEqual(linear.calls, []);
  assert.deepEqual(github.calls.map((c) => c[0]), ["comment"]);
  assert.match(github.calls[0][2], /\*\*No Linear update this week:\*\* the Linear lookup failed/);
  assert.equal(result.failures.length, 1);
});

test("a GitHub error stops the run", async () => {
  await assert.rejects(run({ github: fakeGithub({ failOn: "create" }), linear: fakeLinear() }), /github create failed/);
});
