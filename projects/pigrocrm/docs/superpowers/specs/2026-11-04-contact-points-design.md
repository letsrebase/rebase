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

## 7. Deviations found while building (CRM, REB-940 to REB-942)

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
