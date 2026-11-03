# 2026-11-02: the monorepo is renamed to English

On 2026-11-02 Lorenzo decided that every identifier of the monorepo is English: tables,
columns, enum values, REST paths, JSON fields, query parameters, MCP tool and parameter
names, CLI flags, environment variables, modules, files, tests and comments (REB-897 to
REB-904). `docs/glossary.md` is the mapping; `tooling/english-lint` holds the line. This
page is the runbook for the release: what breaks on purpose and what the host needs.

The cut is clean, with no compatibility aliases, so **the CRM and the hub are released
together**: the hub calls the CRM's engagements and leads doors and reads its registry, and
both sides changed their wire names (`company`, `contact`, `first_name`, `space_created`,
`created`, `member`, `spaces`, ...).

## What stays Italian, on purpose

What a person reads (UI copy, messages, OpenAPI and MCP descriptions, mails), document
template content and the template variables users type, users' custom-field keys,
FatturaPA element names, the dated records, every Alembic revision up to CRM 0067 and
hub 0048, and the names that live outside our systems below.

## Links that keep working

| Link | How |
|---|---|
| CRM browser routes in bookmarks and mails (`/app/clienti`, `/app/fatture`, `/app/oggi`, ...) | the redirect stubs of the SPA |
| `/app/entra`, `/<slug>/app/entra` | nginx 301 to `/app/verify`, token never logged (unchanged) |
| `/app/registrati` | nginx 301 to `/app/register` (unchanged) |
| `/termini` | 301 to `/terms` (unchanged) |
| hub `/hub/entra`, `/hub/accedi`, `/hub/disiscrizione`, `/hub/team/risposta`, `/hub/aziende` | the hub SPA's stubs |
| `POST/GET /api/hub/campagne/disiscrizione` (List-Unsubscribe), the team-answer and magic-link API paths | second routes in the hub API (`test_legacy_routes_api.py`) |
| `?rif=` | accepted next to the English name, for good |

No nginx rewrite was added for them: the applications answer the old URLs themselves.

## Breaks on purpose (release notes)

* **REST paths and JSON fields** of the CRM and the hub API renamed per the glossary
  (`/api/aziende` to `/api/legal-entities`, `/api/tenants/{slug}/disponibile` to
  `/available`, `/api/hub/freelancers/{id}/cv-anonimo` to `/anonymous-cv`, ...). Every
  script or client with a PAT must follow.
* **MCP tools and parameters** renamed (`list_talenti` to `list_talents`, `propose_azienda`
  to `suggest_legal_entity`, ...). Every connected assistant re-reads the tool list; saved
  prompts that name a tool change.
* **Hub to CRM wire**: `PUT /api/rebase/engagements/{match_id}` and
  `PUT /api/rebase/leads/{request_id}` bodies and responses, `POST /api/hub/members/lookup`,
  `TenantSignup`, `TenantAvailability`.
* **PostHog**: event names (`fattura_emessa` to `invoice_issued`, `wizard_passo` to
  `wizard_step`, `spazio_creato` to `space_created`, ...), properties (`tipo` to `kind`,
  `ruolo` to `role`, `settimana` to `week`) and the group type `spazio` to `space`
  (`docs/glossary.md`, section Analytics). Funnels, cohorts and dashboards keyed on the
  old names split history until they are edited or aliased in PostHog.
* **Browser storage**: consent and referral keys read the old key for 30 days.
* **CLI**: `createadmin --nome` to `--name`; `digest|reminders|automations --data` to
  `--date`; `digest --forza` to `--force`; hub `createtoken --nome` to `--name`;
  `setrole --nome/--cognome` to `--first-name/--last-name`.
* **Databases**: CRM revision 0068 (every space database and the root one) and hub 0049
  rename the schema in one transaction each; the downgrades restore the old names. An
  old backup restores onto the old schema: restore it, then run the migrations.

## Host steps

1. **Environment** (`/opt/pigrocrm/.env`, `/opt/pigrocrm-preview/.env`; the settings use
   `extra="ignore"`, so an old name is silently ignored and the default applies). Rename
   if set:

   | Old | New |
   |---|---|
   | `PIGROCRM_SOLLECITI_GRACE_DAYS` | `PIGROCRM_PAYMENT_REMINDERS_GRACE_DAYS` |
   | `PIGROCRM_SOLLECITI_MIN_INTERVAL_DAYS` | `PIGROCRM_PAYMENT_REMINDERS_MIN_INTERVAL_DAYS` |
   | `PIGROCRM_SOLLECITI_MAX_REMINDERS` | `PIGROCRM_PAYMENT_REMINDERS_MAX_REMINDERS` |
   | `PIGROCRM_CONCENTRAZIONE_SOGLIA_PREFERITA` | `PIGROCRM_PREFERRED_CONCENTRATION_THRESHOLD` |

   The hub's `REBASE_*` variables are unchanged (`REBASE_RESEND_DAILY_QUOTA` included);
   `REBASE_SIGNER_JSON` keeps its Italian keys, which are template variables of the
   contract texts. Check with
   `grep -E 'SOLLECITI|CONCENTRAZIONE' /opt/*/.env`.
2. **Crontab**: no line changes (`pigrocrm gmail-sync`, `digest`, `reminders`,
   `automations` carry no renamed flag). Only a by-hand run changes: `--data` is `--date`
   and `--forza` is `--force`.
3. **Host vhosts** (installed by hand, then `nginx -t && systemctl reload nginx`):
   * `pigro.letsrebase.conf` and `preview.pigro.letsrebase.conf`: the rate-limit location
     `^/api/tenants/[^/]+/disponibile$` becomes `^/api/tenants/[^/]+/available$`.
   * `letsrebase.conf` and `preview.letsrebase.conf` (website): the nested location
     `^/api/hub/freelancers/[^/]+/cv-anonimo$` becomes `.../anonymous-cv$` (330 s timeout;
     without it the anonymous CV download times out at 60 s).
4. **Deploy order**: stop the hub api, mcp and the loops before `alembic upgrade` (0049 is
   breaking for the old image), release the CRM and the hub in the same window, and watch
   the deploy health probe, which now asks for `/api/tenants/zzz-healthcheck/available`.
5. **PostHog**: update or alias the renamed events, properties and the `space` group type.
6. **Agents and tokens**: PATs keep working; tell whoever scripts the API about the new
   paths; reconnect MCP clients so they reload the tool list.
