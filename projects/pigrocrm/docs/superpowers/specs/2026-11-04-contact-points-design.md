# Several contact points per person, customer and hub user

Date: 2026-11-04. Card: REB-939. Project: «Give people, customers and hub users several
contact points» (P-REB-79).

## 1. Why, and what is already decided

A CRM person has exactly one `email`, one `phone` and one `linkedin`
(`people/models.py`), and a customer one `email` and one `phone` (`customers/models.py:124`).
Real contacts do not fit: on 2026-11-04 Immobilsistem wrote to Lorenzo from two WhatsApp
numbers, Marco Crippa's own and the agency's shared one, and that second number belongs to the
company rather than to a person. The hub has the same limit on `users` (`email`, `phone`,
`linkedin_url`, `models/identity.py:143`), which is both the freelancer's and the company
contact's record, since `companies` keeps no contact columns of its own
(`models/profiles.py:176`).

Lorenzo decided on 2026-11-04:

- Several contact points for CRM people, CRM customers, hub freelancers and hub companies.
- A contact point carries a label from a fixed list plus a free note.
- When the CRM sends mail (contract mail, payment reminders), it uses the person's primary
  address.

## 2. The contact point

A contact point is one way to reach somebody: a kind, a value, a label and a note. Glossary:
recapito → `contact_point`.

| Column | Type | Meaning |
|---|---|---|
| `kind` | enum `email`, `phone`, `linkedin` | What the value is. |
| `value` | `String(320)` | The address, the number (stored as typed, compared on digits) or the profile URL (canonicalised the way the hub already does for `linkedin_url`). |
| `label` | enum `work`, `personal`, `billing`, `other` | UI: lavoro, personale, amministrazione, altro. |
| `note` | `String(200)`, nullable | Free text: «solo WhatsApp», «centralino, chiedere di Veronica». |
| `is_primary` | `Boolean` | At most one per owner and kind (partial unique index). The first point of a kind is primary. |
| `position` | `Integer` | Display order. |

Same owner, same kind, same normalised value: refused (unique index), the way
`deal_value_lines` refuses a malformed line.

What stays outside the list, on purpose:

- `customers.certified_email` (PEC): a fiscal field, read by invoicing and the FatturaPA
  export, one per customer.
- `customers.website`: one site, not a way to reach a person.
- The hub's login address, `users.email`: it stays the identity (§4).

## 3. PigroCRM

**Schema.** One owned child table, `contact_points`, with `person_id` and `customer_id`
nullable and a CHECK that exactly one is set, RLS through the parent's visibility as
`deal_value_lines` does through `deal_visible`. Clean cutover: the migration moves
`people.email`, `people.phone`, `people.linkedin`, `customers.email` and `customers.phone`
into rows (primary, label `work`) and drops the five columns, with the lowercase and trigram
indexes moving to `contact_points.value`.

**API, MCP and web.** A person and a customer read `contact_points` as a list, and still
answer `email` and `phone` as read-only fields derived from the primary point of each kind,
the way a deal answers `expected_value` from its lines. A write sends the whole list
(`changes.contact_points` replaces it, as `value_lines` does); `email` and `phone` are no
longer accepted on write. The web forms get a list editor (kind, value, label, note,
primary), and the detail pages show every point with its label and note.

**What changes in the lookups**, each listed by the survey of 2026-11-04:

| Lookup | Today | With contact points |
|---|---|---|
| Gmail roster, known addresses (`gmail/roster.py:50`) | one address per person and customer | every email point: mail from a personal address lands on the person too |
| Gmail `resolve_many` (`gmail/roster.py:125`) | by `people.email` | by any email point |
| Leads door reuse (`engagements/leads.py:295`) | same `people.email` | any email point of a live person; the incoming address becomes the primary point of a new person |
| People search (`people/repository.py:71`) | name and email, phone not searched | name and any point, phone compared on digits |
| Customer search (`customers/repository.py:112`) | VAT, email, tax code | VAT, tax code, any point |
| Contract mail, payment reminders, sync backfill (`contract_emails/service.py:258`, `gmail/payment_reminders.py:593`, `gmail/sync.py:680`) | each person's email | each person's primary email |

## 4. Hub

**The login address stays where it is.** `users.email` is unique, receives the magic link and
is what campaigns write to (`campaigns/service.py:391`). An extra address is never a login and
never a recipient.

**Schema.** `user_contact_points` on `users`, the same columns as §2. `users.phone` moves into
it (primary phone); `users.linkedin_url` stays on `users`, because it is an identity key
(`uq_users_linkedin_lower`) that duplicate detection and `find_person` already read. The login
address is shown as the primary email, read-only.

**Duplicates.** An extra email equal to another user's login address, or to another user's
extra email, is refused on save and named in the error. At signup, `find_person` also checks
extra emails, so somebody signing up again with their other address is recognised the way a
repeated LinkedIn profile is today (hub `AGENTS.md`, «a person is their address or their
LinkedIn profile»).

**Who edits.** The admin, from the freelancer and company pages and over the hub MCP
(`override_freelancer`, `override_company`); the person, from their own area.

**CRM link.** The leads door payload grows `contact.contact_points`, so a request reaches the
CRM with every point of its contact. That changes the payload's shape, so the `hub-v*` and
`pigrocrm-v*` tags ship back to back, as with the value lines.

## 5. Order of work

1. This design.
2. CRM: table, migration, core and lookups, API and MCP, web.
3. Hub: table, migration, duplicate rule, admin and self-service UI, hub MCP.
4. Leads door carries the points, hub sends them; one release of both projects.

## 6. Open points

- Whether a person may hide a point from the agents' MCP reads (a personal number, say).
  Not asked; nothing in this design hides anything.

## 7. Deviations found while building

### Hub (REB-943, REB-944)

- **The login address is shown as the primary email, and only that.** The design says the login
  address is «shown as the primary email, read-only». Reads answer it as the first entry of
  `contact_points`, without an `id`; a write leaves it out (and accepts the same list sent back
  as it was read). `users.linkedin_url` is not presented as a point: a `linkedin` point is an
  ordinary extra one, first of its kind primary, and nothing about it takes part in `find_person`.
- **An extra address is never primary.** The design says «the first point of a kind is primary»;
  for `email` the login address is the primary one, so a stored address is always non-primary and
  a write that flags one primary is refused.
- **A generated `value_key` column holds «same kind, same normalised value».** Alembic cannot
  compare an expression index with a `CASE` in it against the model, so the comparison key (digits
  of a phone, lower case of anything else) is a stored generated column and the unique index is on
  `(user_id, kind, value_key)`. A second unique index, on `lower(value)` where `kind = 'email'`,
  keeps an extra address to one person; that it is not another person's *login* address is the
  service's check (`users.email` is in another table).
- **Duplicates compare the primary phone only.** `find_duplicates` and `duplicates_of` flag two
  people by their primary phone point, as they flagged `users.phone`; a second number, an agency's
  shared one, never makes two people the same.
- **A merge carries the points.** The absorbed person's points go to the survivor (their
  primary phone arrives as a secondary one when the survivor has a primary) together with their
  login address as an extra email; the undo puts back what the survivor has not changed since.
- **Extra addresses never decide who somebody is (decided after the review of 2026-11-04).** §4 has
  `find_person` read the extra addresses, so a sign-up with one is recognised as the holder. An
  extra address is unverified, though: anybody could type it into a form to be taken for its
  holder. So `find_person`, `get_or_create`, the freelancer and company wizards, the admin's draft
  and `MemberService.lookup` read login addresses only, as before. A sign-up or request under
  somebody's extra address makes its own person, and the pair is flagged: `find_duplicates` and
  `duplicates_of` carry a new signal, `email` (one person's login is another's extra address), which
  only flags (`to_review`, never `mergeable`, so `rebase talent-duplicates --apply` leaves it
  alone) and shows as `duplicate_possible_of`; the admin merges with the usual flow. The card
  lists and the lead anti-join (`card_emails`) are unchanged. The flag covers people with a
  freelancer card; a company contact with no card is not in that list.
- **An address typed is the bare address.** An email point is validated as exactly an address
  (no display name, no angle brackets) and stored as typed, trimmed, so what is compared is what
  is stored.
- **A phone is anything with six digits.** A value stored before the list existed
  (`+39 345 1234567 (ufficio)`) must still save: a phone is trimmed, at most 40 characters, at
  least 6 digits, anything else allowed; `value_key` is its digits. The admin dialogs send the
  contacts only when the list changed.
- **A role or a login never goes to an extra address.** `UserService.promote` and `set_role` refuse
  to create a user under somebody's extra address, naming it. An address a person already keeps is
  not asked again when they save their list, so a sign-up that took it as its login does not stop
  them.

### CRM (REB-940 to REB-942)

- **`normalized` column.** §2 lists no such column, but the unique index and every lookup
  need the compared form (lowercased address, digits of a phone with a leading `+` kept,
  canonical LinkedIn profile) as data, not as a per-index expression. `contact_points` has
  `normalized`, written only by `contact_points.service`; the roster, the leads door and the
  searches read it, never `value`. Its indexes are `(kind, normalized)` and one trigram index
  each on `value` and `normalized`; the two unique indexes and the one-primary-per-kind
  indexes are partial on the owner column.
- **Stored as typed.** A person's email used to be lowercased on write; the point keeps what
  was typed (trimmed) and compares on `normalized`. A LinkedIn value is stored canonical, as
  the hub stores it (`contact_points/linkedin.py` is a port of `rebase_core.linkedin`).
- **Reading mail takes every address, sending takes the primary.** §3 says the sync backfill
  uses each person's primary email. The backfill and the payment reminders' «did anybody
  reply» search read mail, and a person may answer from their second address, so they look at
  every email point of the customer and its people; contract mail and the reminders'
  recipients (what is sent) use the primary alone.
- **A customer's email is now format-checked** like a person's: it was free text before.
- **The leads door** keeps accepting today's payload. The address is the identity and is
  validated as before; a phone or a LinkedIn profile that the point rules refuse is left
  out of the new person instead of refusing the request, as a blank one is.
- **The command palette** (`search/repository.py`) matches the contact points of a person or
  customer through one correlated aggregate of their values, a filter rather than an index
  seek: its tables are small, and the field is still reported as `email`.
- **A change to the list bumps the owner's `updated_at`**, since the owner row itself does
  not change and the lists sorted by «recently touched» would miss it.
- **The people and customer list searches left the trigram BitmapOr too.** `owners_matching`
  answers them with a `value`/`normalized` `ILIKE` over `contact_points` joined back as an
  `IN`, so the four-way OR of one table's trigram indexes is now a semi-join; acceptable at a
  space's size, and both `contact_points` trigram indexes serve it.
- **Legacy values never block a save (review of 2026-11-04).** A point that is the same value
  (same kind, same compared key) as one the owner already has is accepted as it is, even where
  the rules would refuse it as a new value, so a list read back and sent unchanged always
  saves (`prepare(..., stored)`); the web forms also send and check `contact_points` only
  when the list changed. The rules are what the old columns held: a phone is trimmed, at most
  40 characters, at least 6 digits, any other characters, compared on its digits (a leading `+`
  kept); an email is a bare address (none of `<>,;"()` or whitespace); a bare LinkedIn handle
  becomes `https://www.linkedin.com/in/<handle>`.
- **Migration 0073 and a phone with no digit.** It is not dropped in silence: it becomes the
  note (`Telefono: <text>`) of the owner's primary email point when the owner has one, and
  otherwise is left out and named in the migration's log, one line per owner id.

### Leads door (REB-945)

- **`contact.contact_points` is optional and additive.** `LeadContactPerson` gains the list in
  the shape of `ContactPointIn` (kind, value, label, note, is_primary; `id` and `position` are
  refused like any unknown field), at most `MAX_CONTACT_POINTS`. `email`, `phone` and
  `linkedin_url` stay as they were, and `email` is still the identity the door matches on and
  the lock is taken on. An absent list is the payload of before.
- **Flat fields stay primary.** A new person is born with the address, phone and profile as
  today, then every extra point in the order sent. An extra point is never primary whatever it
  says (`is_primary` is accepted and ignored): the primary of a kind is the flat value, and when
  there is none (or it was left out) the list's own rule makes the first point of the kind
  primary. An extra point that is the same value as a flat one (the hub sends its primary phone
  both ways) is not written twice: the flat point takes its label and note, the last such point
  winning. A point the point rules refuse, one that repeats a kind and compared key already in
  the list, and any past the list's bound are left out, as the door already leaves out an
  unreadable phone; the request is never refused for them.
- **A known person gets only what they lack.** The person is found through `contact.email`
  alone, never through the extra points. Since REB-942 that address is compared with every
  email point a person has; after the review of 2026-11-04 the door prefers a live person whose
  *primary* email it is and falls back to one who holds it as another point only when nobody has
  it as primary (oldest first within each tier). The same order picks the person a retry names.
  Their missing extra points are appended after their own, non-primary (a point is primary only
  as the first of a kind they have none of), by `contact_points.service.add_points`: it locks the
  person's row, reloads the list under the lock (so a point written since the call loaded the
  person is not lost) and inserts only the new rows. The existing rows are not rewritten, so a
  legacy value is not canonicalised as a side effect and every id stays. An `updated` entry
  (`changed: ["contact_points"]`) goes on their timeline; nothing is changed or removed, and the
  flat phone and profile are still not added to a known person. This applies to the person the door reuses with their customer and to the one
  with no live customer that is attached to a new one. A call that links an explicit `deal_id`,
  and a retry that finds the deal by its marker, write no point, as they write no person.
- **Hub side.** `CrmLeadService.payload` sends every stored point of the contact (extra
  emails, every phone including the primary one, LinkedIn points), with label, note and
  `is_primary` as stored and the login address never among them; a point whose value or note is
  past the CRM's bound or holds a NUL byte is left out, never cut. The hub's own rules are the
  stricter ones, so in practice nothing is left out here.
- **Release order.** The deployed CRM's door refuses unknown fields, so it answers 422 to the
  hub's new payload; the hub files a 422 as `rejected`, which the sweep never asks again and only
  an admin's «Riprova» does, so a request made in between waits for a person. `pigrocrm-v*` ships first, `hub-v*` right after; the new CRM accepts the
  old payload.
- **Residual exposure of extra emails (review of 2026-11-04).** An extra email that came from
  the hub is unverified (the hub's own rule: it never decides who somebody is there), yet in the
  CRM it persists as an ordinary email point. The primary-first rule keeps it from taking over a
  request whose address somebody holds as primary, but with no primary holder a later request, or
  Gmail mail from that address (the roster reads every email point), still matches the person who
  has it. Not closed here; closing it would need a flag on the point.
- **Every point of a hub user goes to the CRM (review of 2026-11-04).** When somebody files a
  company request, all their stored points go with it, personal labels and notes included: the
  hub has no private flag (§6), so nothing is held back.
