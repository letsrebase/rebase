# Work profiles: data protection impact assessment (draft)

Status: draft, written 2026-11-18 for REB-1132. Controller: Rebase S.r.l. Not yet reviewed by a
lawyer; nothing here is legal advice, and the sign-off at the end is still open.

This assessment covers what the hub does with the work profiles freelancers send with
`rebase-verify` (the CLI called `work-profile` until REB-1140; spec
`docs/superpowers/specs/2026-11-17-work-profile-intake-and-review-design.md`), the
per-technology usage bands added in REB-1130, and two things we chose not to build: a
numeric score per technology, and a rating of higher-level skills. It is a living document:
change it in the same pull request as any change to what the processing does, and keep the
previous text in git history. Where it says "planned", the measure is a condition for shipping
that part, not something already in production.

## 1. Why we ran one

GDPR Art. 35 asks for an assessment before processing "likely to result in a high risk". The
EDPB's guidelines (WP248 rev.01) say processing that meets two of their nine criteria will in
most cases need one. The work profile meets at least three:

- **Evaluation or scoring** (criterion 1): it describes aspects of a person's work, and
  approval changes how they are presented to companies.
- **Innovative technology** (criterion 8): a tool that reads coding-agent logs and a model
  that writes prose from them.
- **Matching or combining datasets** (criterion 6): the approved profile can feed the
  anonymous card, beside the CV and GitHub.

Criterion 7 (vulnerable data subjects, with employees as the guidelines' example of a power
imbalance) applies in part: freelancers are not employees, but they depend on rebase for work
proposals. A per-person score would add criterion 2 (automated decision-making with a
significant effect) or 9 (preventing access to an opportunity) in substance, even with a
person in the loop. The Garante's list under Art. 35(4) also names large-scale evaluation and
scoring. The campaign that invites freelancers to send a profile (REB-1131) exists as a draft
and has not been sent; this assessment comes before it.

## 2. What the processing is

**Who.** Freelancers of the rebase community who choose to send a profile. Nobody is excluded
from the community or from proposals for not sending one. Vetted people are listed first in
the talent cloud, so a person without the badge is listed after those with it; the badge can
also be given by an admin without a profile («Segna come verificato», `set_vetted`).

**What arrives.** One JSON document per person, at most 512 KB, built on their own machine by
`rebase-verify` from their Claude Code, Codex, opencode and similar logs: counts (sessions,
products, months), the detected stack per project from a fixed vocabulary, abstract project
domains, prose written by the person's own model and reread by them, and (since REB-1129)
per-technology usage bands with their counts (a technology name outside the fixed vocabulary is
dropped before storage and only the count of dropped rows is kept, as data minimisation). The tool is meant to leave out session logs,
code, and client, product, person and repository names; the hub cannot verify the tool's
redaction, so it refuses file artifacts and a `repoLabel`, drops `contact`, and runs its own
checks (below). The person's identity comes from the hub sign-in, not from the profile.

**What the hub does.**

- At intake, deterministic gates refuse a profile that is malformed, whose structure does not
  add up, or whose prose is less than 60% supported by its own counts (groundedness). These
  are consistency checks on the document, with no effect beyond asking the person to
  regenerate it.
- An admin reads the profile, the groundedness score and the claims it could not support, and
  approves or rejects it. Approval sets «Verificato da rebase», which lists the person first in
  the talent cloud, shows companies the summary, the domains with their counts and the
  representative projects' sectors, and may feed the anonymous card. The card-source switch is
  off today; the privacy page already covers that use, so turning it on is Lorenzo's decision.
- The anonymity guard (`work_profiles/guard.py`) reads the fields companies see (summary,
  domain labels, project domains) at storage, at approval and at every cloud read, and approval
  waits while a hit stands unless an admin overrides it with a note. The card source runs its
  own guard. Both match identifiers the hub knows (the person's name and accounts, link hosts,
  the clients of their engagements), not third parties in general. The prose only admins read
  (narratives, trajectory, principles, URL artifacts) relies on the tool's redaction and the
  admin's reading.
- In place (REB-1130): bands shown to the freelancer and to admins, never to companies.

**Recipients.**

- rebase admins, in the hub and through their AI assistant over MCP (`get_work_profile`
  returns the rendered profile; each such read is logged as `work_profile_read`, a read in the
  web admin is not logged).
- Companies rebase works with: what an approved profile shows in the cloud (above).
- Anthropic as processor, when the card writer reads an approved profile once that switch is
  on (EU SCCs, disclosed on the privacy page).
- Resend, which carries the decision mails, including an admin's rejection note.
- Hetzner (Helsinki) for hosting, and Cloudflare R2 for the encrypted nightly database backups.

**Retention.**

- The profile: 12 months from submission, approved or rejected, or earlier when the person
  deletes it from the member area. A rejected profile is kept so the person can read the note
  and so a resubmission can be compared; it shows nowhere outside the admin.
- Backups: a deleted or expired profile survives in the encrypted backups for up to 90 days.
- The vetted flag outlives the profile until the person asks us to remove it (spec § 7).
- Audit entries (`work_profile_decided` with its notes, `work_profile_read`) have no purge yet.
- A campaign recipient row keeps the fact that a profile was sent after the profile is gone.

**Legal basis.** Consent (Art. 6(1)(a)). `submit` first shows what will be sent and the
destination, and sends only when run again with `--yes`; `submit --dry-run` writes the exact
payload to a file. Withdrawal is deleting the profile from the member area, which stops every
use of it at once. The vetted flag and its position in the cloud do not go with it: removing
them takes an email to us (see § 4 and § 6).

## 3. Necessity and proportionality

**Purposes.** Let an admin decide whether to vouch for a freelancer; once approved, show
companies a short, checked description of how the person works; and, when the switch is on,
improve the anonymous card. A CV says what somebody did, not how they work with coding agents,
which is what companies ask us about most.

**Minimisation.** The profile is built and redacted on the person's machine, the hub receives
no session logs or code, and the counts let a reader check the prose. Alternatives considered:
a self-declared skills form (no evidence behind it), a technical interview for everyone (does
not scale, and stays available), reading public GitHub (already a card source, misses private
client work, which is most freelance work).

**Automated decisions (Art. 22).** No decision about the person is taken solely by automated
means. Approval and rejection are an admin's. The intake gates refuse a document, not a person,
and the person can regenerate and send again; the guard holds an approval until an admin reads
the hit and decides.

## 4. Risks to the people concerned

Likelihood and severity before the measures of § 5, then the residual risk after them.

| Risk | Likelihood | Severity | Residual |
|---|---|---|---|
| Usage read as competence: a company or an admin takes "continuous use of X" to mean "good at X". | High | Medium | Medium: the legend and the absence of scores help, the misreading stays possible. |
| Inaccurate or inflated prose from the person's own model. | Medium | Medium | Low: groundedness flags unsupported claims and the admin sees them. |
| Exclusion of people who do not use coding agents, or tools the CLI does not read. | Medium | Medium | Medium: the admin can vet without a profile, but nothing makes that route visible yet. |
| Consent under pressure: once verified people are listed first, sending a profile stops feeling optional. | High | Medium | Medium: the same mitigation as the row above, plus the profile's deletion at any time. |
| Withdrawal that leaves the effect in place: deleting the profile keeps the vetted flag and its position. | Medium | Low | Low, if the member area says how to remove the flag; today only the privacy page says so. |
| Re-identification of a client or project from a domain description. | Low | Medium | Low for what companies see (guard plus admin reading); the admin-only prose relies on the tool and the admin. |
| Third parties' data in the prose: a colleague's or a client's name, or special-category data, that the tool's redaction misses. | Low | Medium | Low: guard on company-facing fields, admin reading, rejection with a note. |
| Client confidentiality: a freelancer describes work under an NDA. | Low | Medium | Low: abstract domains only, no repository names; the member area tells the person what leaves. |
| Function creep into automated ranking or filtering on profile fields. | Medium | High | Low while the rule in DECISIONS.md (2026-11-18) holds; it is the main thing a reviewer must check in every change. |
| Storage: the payload sits as plain JSONB in Postgres; anyone with database access reads it, and web-admin reads are not logged. | Low | Medium | Low: the database port is bound to the server's loopback only, admin access is a short list of named people, backups are encrypted. |
| Forged or replayed submission. | Low | Medium | Low: single-use 15-minute token from a signed-in browser session, server-side recheck. |
| Transfer outside the EEA when the card writer or an admin's assistant reads the profile. | Low | Low | Low: Anthropic under SCCs; the card switch is off. |
| A minor sends a profile. | Very low | Medium | Very low: the community is working freelancers with a VAT number; there is no age check. |

No residual risk is high, so no prior consultation with the Garante (Art. 36) is needed as the
processing stands. Rebase S.r.l. has no DPO, and the people concerned were not consulted
(Art. 35(9)); a short survey of the first freelancers who send a profile would cover that.

## 5. Measures

In place:

- The person runs everything locally, previews the payload, and nothing leaves without
  `submit --yes`.
- A person decides; there is no approve or reject tool over MCP (spec § 12). The only ordering
  is by the vetted flag, which a person set.
- Companies see only an approved profile's summary, domains with their counts and project
  sectors. The privacy page names the summary, the domains and the sectors; it should also say
  that the domains carry counts (action for the privacy page, below).
- 12-month retention, deletion from the member area, the rejection note visible to the person.
- The card-source switch stays off until Lorenzo turns it on.
- The rule in DECISIONS.md (2026-11-18): no numeric score or ranking about a person.
- REB-1130: bands are derived by the intake from month counts alone, with the fork's own rule, and
  a profile whose claim does not match its counts is refused. They are shown with their counts; the
  badge fill shows the band and nothing finer; a legend says it measures how often and how
  steadily a technology appears in the work, not how well the person knows it. No sort or filter
  reads a band. Admins see an approved profile's bands on «Talenti» and on the talent's page, the
  reviewing admin a draft's in the review panel only, the person their own in the member area,
  and the talent cloud carries none.

Planned, and conditions for shipping the part they belong to:

- The member area says how to have the vetted flag removed.

Open, for Lorenzo:

- The privacy page does not say that an admin's AI assistant can read a work profile over MCP,
  nor that the domains shown to companies carry counts. Both are public text.
- A purge rule for the audit entries of work profile decisions and reads.
- Rights (Art. 15 to 20): access today means the member area, which shows the state, the dates
  and the note but not the groundedness flags or the audit entries; a full copy is on request
  to ciao@letsrebase.com, as for every other right on the privacy page. There is no edit path:
  rectification is deleting and sending again. Portability is the person's own `candidate.json`.

## 6. Per-technology scores (REB-1132)

Lorenzo asked on 2026-11-17 whether we could score each technology, approved by a person and
clear to the freelancer. What a score would have to measure, and why we do not ship one:

- **What the logs can support.** How often, how long and alongside what a technology appears;
  whether tests and checks ran; how often work was reverted. None of these measures skill: a
  person can use a library daily and badly, or master one they touched for a month. A number
  on top of the same counts adds precision the data does not have, and makes the "usage read
  as competence" risk above worse.
- **Regulation.** Annex III point 4 of the AI Act (Regulation (EU) 2024/1689) lists, under
  "employment, workers' management and access to self-employment", systems for the selection
  of natural persons and for evaluating candidates (4(a)), and systems that allocate tasks on
  the basis of individual behaviour or personal traits or that evaluate performance in
  work-related relationships (4(b)). The obligations apply from 2 December 2027, after
  Regulation (EU) 2026/1744. A score used to choose whom to propose would likely fall under
  4(a), with risk management, data governance, logging, human oversight, accuracy testing and
  registration. We assess the current flow (a person reads a report and decides) as outside
  Annex III, subject to the lawyer's review below, since it already vets people and lists them
  first.
- **Upstream.** Play New's ADR-001 ("AI may score the artifact, never the person") is part of
  why people trust the tool. A fork that scores people would have to say so, and would weaken
  every change we propose upstream.

**Decision, Lorenzo, 2026-11-18:** no numeric scores. Bands only, shown as a badge filled to
one third, two thirds or full. Reopening it needs this assessment signed off, a lawyer's
opinion on Annex III, a written definition of what the score measures and how it is validated
against outcomes, and a person who approves every score before anyone else sees it.

## 7. Higher-level skills: architecture, organisation, soft skills

Lorenzo asked on 2026-11-18 whether the tool could also read skills that are not strictly
implementation: designing a software architecture, organising work, soft skills.

**What the logs can show, as practices with counts.** The tool already reads some of it: the
DESIGNS axis of agentic literacy counts planning moves (plan mode, todo lists, questions back
to the person), the AI-relationship axis places prompts between directing and co-thinking from
their length and structure, and the trajectory shows changes in how the person works and the
principles they wrote into their own instructions. More could be counted the same way,
without a model: sessions that produce design documents, ADRs or specs before code; how work
is decomposed and delegated (sub-agents, parallel sessions); how often a plan is written,
followed and verified; refactors that span modules.

These stay on the right side of the line because each is a count of something the person did,
which the person can check in their own preview and which says nothing about who they are.
The AI-relationship axis is the closest to the line: it reads the shape of prompts (numbered
steps, file paths, questions), never their tone or sentiment, and reports a position on one
axis with the counts behind it.

**What the logs cannot show.** Whether an architecture was right needs the outcome and the
context, which the logs do not hold. Organisation beyond one person's sessions (estimates kept,
coordination with a team or a client) is mostly outside them. Soft skills are interpersonal,
and the logs are a person talking to a model, so anything inferred from tone or wording would
be a guess about character.

**Where the line is.** Inferring emotions in a work context is prohibited by the AI Act
(Art. 5(1)(f), applicable since 2 February 2025), and evaluating people or allocating work on
the basis of personal traits is in Annex III point 4. So: describing practices with counts, as
the tool already does, is in scope and could grow, under this assessment; rating architecture,
organisation or soft skills from the logs is out. Those belong to interviews, references and
the projects themselves.

## 8. Sign-off

- [ ] Reviewed by Lorenzo Fiore for Rebase S.r.l.
- [ ] Reviewed by a lawyer: Annex III scope of the current flow, the consent-under-pressure
      risk, the vetted flag outliving withdrawal
- [ ] Questions for Play New (REB-1125): would they accept band and practice counts upstream,
      and do they read ADR-001 as ruling out per-person scores in their own intake too?

Review this assessment before the campaign is sent, when the card-source switch is turned on,
and whenever the profile gains a field or a recipient.
