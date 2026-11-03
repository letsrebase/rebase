import { ANY, boolToSelect, selectToBool } from '@/lib/adminList'
import { amountFilter } from '@/lib/amount'
import type { AudiencePreview, Campaign, CampaignAction, CampaignDraft, CampaignMeta, CampaignTemplate } from '@/lib/api'
import { CAMPAIGN_MAX_LENGTH, META_LABELS } from '@/lib/campaigns'

export type Source = 'status' | 'filters' | 'list'
export type List = 'talents' | 'companies'

// A `Select` cannot take an item with value `""` -- Radix reserves it for "nothing
// picked yet", which is exactly what an unset filter or an unset template is here.
export const NONE = ''

/** The Talenti list's own filters (spec § 2), field for field the server's
 *  `TalentsFilters` (`rebase_core/campaigns/schemas.py`) and the list page's
 *  `TalentsFilters`: every value a string as its input holds it, `ANY` for a `Select`
 *  left on «Tutti». `payloadOf` below turns it into the server's shape. */
export interface TalentsFiltersForm {
  status: string
  q: string
  posizione: string
  remote: string
  min_rate: string
  max_rate: string
  origin: string
  utm_source: string
  has_cv: string
  with_logins: string
  created_from: string
  created_to: string
}

/** The company list's own filters, the server's `CompaniesFilters`. */
export interface CompaniesFiltersForm {
  status: string
  q: string
  budget_min: string
  budget_max: string
  period_from: string
  origin: string
  created_from: string
  created_to: string
}

export const TalentsFiltersEmpty: TalentsFiltersForm = {
  status: ANY,
  q: '',
  posizione: '',
  remote: ANY,
  min_rate: '',
  max_rate: '',
  origin: '',
  utm_source: '',
  has_cv: ANY,
  with_logins: ANY,
  created_from: '',
  created_to: '',
}
export const CompaniesFiltersEmpty: CompaniesFiltersForm = {
  status: ANY,
  q: '',
  budget_min: '',
  budget_max: '',
  period_from: '',
  origin: '',
  created_from: '',
  created_to: '',
}

/** The filters that stay on screen; the rest sit behind «Altri filtri». */
export const TalentsMain: readonly (keyof TalentsFiltersForm)[] = ['status', 'q', 'has_cv', 'with_logins']
export const CompaniesMain: readonly (keyof CompaniesFiltersForm)[] = ['status', 'q']

/** Whether any filter outside the main ones holds a value: the edit route opens
 *  «Altri filtri» then, so a stored filter is never hidden. */
export function hasOtherFilters(form: Pick<CampaignForm, 'list' | 'talents' | 'companies'>): boolean {
  return form.list === 'talents'
    ? otherSet(form.talents, TalentsFiltersEmpty, TalentsMain)
    : otherSet(form.companies, CompaniesFiltersEmpty, CompaniesMain)
}

function otherSet<T extends object>(values: T, empty: T, main: readonly (keyof T)[]): boolean {
  return (Object.keys(values) as (keyof T)[]).some((key) => !main.includes(key) && values[key] !== empty[key])
}

/** Everything the page edits, in one value: the page's draft is a function of it, so
 *  "has anything changed since the last save" is one string comparison. */
export interface CampaignForm {
  nome: string
  source: Source
  journeyStatus: string | null
  list: List
  talents: TalentsFiltersForm
  companies: CompaniesFiltersForm
  subject: string
  text: string
  buttonText: string
  buttonMeta: CampaignMeta
  /** The address «Un link» leads to, as typed (REB-530); kept while another destination
   *  is picked, so going back to «Un link» finds it, and never sent with that one. */
  buttonUrl: string
  action: CampaignAction
  /** The campaign a `list` draft follows (`Campaign.segue_id`): `null` for a `status`
   *  or `filters` form, which picks its own audience instead. */
  segueId: string | null
}

export const EMPTY_FORM: CampaignForm = {
  nome: '',
  source: 'status',
  journeyStatus: null,
  list: 'talents',
  talents: TalentsFiltersEmpty,
  companies: CompaniesFiltersEmpty,
  subject: '',
  text: '',
  buttonText: '',
  buttonMeta: 'area',
  buttonUrl: '',
  action: 'signed_in',
  segueId: null,
}

/** What a text, number or date input sends: nothing when it is empty. */
function typed(value: string): string | undefined {
  return value.trim() === '' ? undefined : value.trim()
}

/** What a `Select` sends: nothing while it reads «Tutti». */
function picked(value: string): string | undefined {
  return value === ANY ? undefined : value
}

/** A stored filter value back into its input: a decimal the server keeps as a string
 *  (or a number, from an older row), a day out of a stored `datetime`. */
function stored(value: unknown, { day = false }: { day?: boolean } = {}): string {
  const text = typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
  return day ? text.slice(0, 10) : text
}

/** A stored amount back into its input, with two decimals. The server writes a machine
 *  decimal, where a dot is always the decimal point, but the field reads what it holds
 *  the Italian way (REB-485): a draft that stored «1.500» (1.5, typed into the old
 *  number input) would come back as 1500. «1.50» cannot be misread, and two decimals
 *  are exact because the server keeps cents at most (`FilterAmount`). */
function storedAmount(value: unknown): string {
  const text = stored(value)
  const number = Number(text)
  return text.trim() !== '' && Number.isFinite(number) ? number.toFixed(2) : text
}

/** The name a draft is saved under while the title field is empty: `nome` is
 *  `min_length=1` server-side (fix 1, REB-472 round 1), and a blank one never leaves
 *  this page (REB-524 strips it server-side too). */
export function defaultName(source: Source): string {
  return source === 'filters' ? 'Campagna da filtri' : 'Nuova campagna'
}

/** The filters as the server takes them. An amount goes as the lists send it (REB-485),
 *  two decimals and a dot, and one that is not an amount narrows nothing, as there. */
function filtersOf(form: CampaignForm): Record<string, unknown> | null {
  if (form.source !== 'filters') return null
  if (form.list === 'talents') {
    const t = form.talents
    return {
      list: form.list,
      status: picked(t.status),
      q: typed(t.q),
      posizione: typed(t.posizione),
      remote: picked(t.remote),
      min_rate: amountFilter(typed(t.min_rate)),
      max_rate: amountFilter(typed(t.max_rate)),
      origin: typed(t.origin),
      utm_source: typed(t.utm_source),
      has_cv: selectToBool(t.has_cv),
      with_logins: selectToBool(t.with_logins),
      created_from: typed(t.created_from),
      created_to: typed(t.created_to),
    }
  }
  const a = form.companies
  return {
    list: form.list,
    status: picked(a.status),
    q: typed(a.q),
    budget_min: amountFilter(typed(a.budget_min)),
    budget_max: amountFilter(typed(a.budget_max)),
    period_from: typed(a.period_from),
    origin: typed(a.origin),
    created_from: typed(a.created_from),
    created_to: typed(a.created_to),
  }
}

/** What a `list` saves: the mail alone. Whom it reaches and what it measures come from
 *  the campaign it follows, and the API refuses a change to either (`LIST_IS_FIXED`). */
export type ListPatch = Pick<CampaignDraft, 'nome' | 'subject' | 'text' | 'button_text' | 'button_meta' | 'button_url'>

/** The address the button carries: «Un link»'s own, trimmed, and nothing at all for any
 *  other destination, so the request of a hub button stays what it always was and the
 *  server drops a stored address with the link (`CampaignService.update`). */
function linkOf(form: CampaignForm): string | undefined {
  return form.buttonMeta === 'link' ? form.buttonUrl.trim() : undefined
}

/** The draft the page saves, or `null` while there is nothing to save yet: a state
 *  source with no state picked has no list and no template behind it. A `list` is
 *  never new on this page (Task 3's `follow-up` creates it), so it always has a body:
 *  the mail, with no `source` -- the API's `CampaignPatch.source` does not accept
 *  `'list'`, and a change to `journey_status`/`filters`/`action` is refused either way. */
export function payloadOf(form: CampaignForm): CampaignDraft | ListPatch | null {
  if (form.source === 'list') {
    return {
      nome: form.nome.trim() || defaultName(form.source),
      subject: form.subject,
      text: form.text,
      button_text: form.buttonText,
      button_meta: form.buttonMeta,
      button_url: linkOf(form),
    }
  }
  if (form.source === 'status' && form.journeyStatus === null) return null
  return {
    nome: form.nome.trim() || defaultName(form.source),
    source: form.source,
    journey_status: form.source === 'status' ? form.journeyStatus : null,
    filters: filtersOf(form),
    subject: form.subject,
    text: form.text,
    button_text: form.buttonText,
    button_meta: form.buttonMeta,
    button_url: linkOf(form),
    action: form.action,
  }
}

/** The key a save is compared by. `JSON.stringify` drops the `undefined` of an empty
 *  filter, as the request body does, so two forms that send the same body compare equal. */
export function keyOf(payload: CampaignDraft | ListPatch | null): string | null {
  return payload === null ? null : JSON.stringify(payload)
}

/** The form a stored campaign reads back as, on the edit route. The server always
 *  writes its own `list` into `filters` (`payloadOf` sends it on every save), so this
 *  reads that key directly rather than guessing the list from which of the
 *  Talenti-only fields happen to be present (fix 2, REB-472 round 1). */
export function formFromCampaign(c: Campaign): CampaignForm {
  const base: CampaignForm = {
    ...EMPTY_FORM,
    nome: c.nome,
    source: c.source,
    journeyStatus: c.journey_status,
    subject: c.subject,
    text: c.text,
    buttonText: c.button_text,
    buttonMeta: c.button_meta,
    buttonUrl: c.button_url ?? '',
    action: c.action,
    segueId: c.segue_id,
  }
  if (c.source !== 'filters' || !c.filters) return base
  const f = c.filters as Record<string, unknown>
  const status = stored(f.status) || ANY
  if (f.list !== 'companies') {
    return {
      ...base,
      list: 'talents',
      talents: {
        status,
        q: stored(f.q),
        posizione: stored(f.posizione),
        remote: stored(f.remote) || ANY,
        min_rate: storedAmount(f.min_rate),
        max_rate: storedAmount(f.max_rate),
        origin: stored(f.origin),
        utm_source: stored(f.utm_source),
        has_cv: boolToSelect(f.has_cv as boolean | undefined),
        with_logins: boolToSelect(f.with_logins as boolean | undefined),
        created_from: stored(f.created_from, { day: true }),
        created_to: stored(f.created_to, { day: true }),
      },
    }
  }
  return {
    ...base,
    list: 'companies',
    companies: {
      status,
      q: stored(f.q),
      budget_min: storedAmount(f.budget_min),
      budget_max: storedAmount(f.budget_max),
      period_from: stored(f.period_from, { day: true }),
      origin: stored(f.origin),
      created_from: stored(f.created_from, { day: true }),
      created_to: stored(f.created_to, { day: true }),
    },
  }
}

/** A state picked from «Stato del percorso». The action always follows the template,
 *  since it is not the admin's to set for a state (fix 4, REB-472 round 1); the mail
 *  follows it only until the admin has written into it (spec § 1), and the name only
 *  until the admin has typed one: the name is not the mail, so typing the title first
 *  must not stop the template from filling the mail. The destination is the same: a
 *  «Dove porta» picked first keeps the template out of it, and out of nothing else, so
 *  a link chosen before the state is never replaced and keeps measuring the click,
 *  while the subject, the text and the button's words still come from the template
 *  (REB-530, CodeRabbit on #476). */
export function withTemplate(
  form: CampaignForm,
  template: CampaignTemplate | undefined,
  value: string,
  touched: { mail: boolean; nome: boolean; meta?: boolean },
): CampaignForm {
  const next = { ...form, journeyStatus: value }
  if (!template) return next
  const keepMeta = touched.mail || touched.meta === true
  next.action = keepMeta && form.buttonMeta === 'link' ? 'click' : template.action
  if (!touched.nome) next.nome = template.label
  if (!keepMeta) next.buttonMeta = template.button_meta
  if (touched.mail) return next
  return {
    ...next,
    subject: template.subject,
    text: template.text,
    buttonText: template.button_text,
  }
}

/** What decides who is on the list: the list is reloaded when this changes and never
 *  on an edit of the mail alone. The action is not part of it: whoever has already
 *  done it is skipped when the mail leaves (`campaigns/tick.py`), not left off the list.
 *  A `list` body (`ListPatch`) carries none of these -- its audience is fixed by the
 *  campaign it follows -- so `source` is optional and such a body always keys as
 *  `[null, null, null]`, a key that never changes and so never goes stale. */
export function audienceSource(c: { source?: string; journey_status?: string | null; filters?: Record<string, unknown> | null }): string {
  return JSON.stringify([c.source ?? null, c.journey_status ?? null, c.filters ?? null])
}

export interface AudienceCount {
  /** Who gets the mail: the list, less the rules' exclusions and the admin's unticks. */
  will_receive: number
  /** Left out by the rules: an admin, «non scrivere mai», an opt-out, a bounce, a
   *  recent campaign. */
  rules: number
  /** Unticked by the admin, among the rows the rules let through. */
  removed: number
  /** The unticked addresses still on the list, as «Invia» sends them. */
  excluded: string[]
}

export function countAudience(audience: AudiencePreview, excluded: readonly string[]): AudienceCount {
  const unticked = new Set(excluded)
  const open = audience.lines.filter((row) => row.excluded === null)
  const removed = open.filter((row) => unticked.has(row.email)).map((row) => row.email)
  return {
    will_receive: open.length - removed.length,
    rules: audience.lines.length - open.length,
    removed: removed.length,
    excluded: removed,
  }
}

// «Un link» (REB-530), the server's own sentences (`rebase_core/campaigns/links.py`),
// so the field says at once what a save would be refused for.
export const LINK_URL_MISSING = 'Scrivi il link a cui porta il bottone.'
export const LINK_URL_TOO_LONG = `Il link del bottone è troppo lungo: al massimo ${CAMPAIGN_MAX_LENGTH.button_url} caratteri.`
export const LINK_URL_NOT_HTTPS = 'Il link del bottone deve iniziare con https://.'
export const LINK_URL_INVALID = 'Il link del bottone non è un indirizzo valido.'

const OCTET = '(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)'
const IPV4 = new RegExp(`^${OCTET}(?:\\.${OCTET}){3}$`)

/** Why the address «Un link» carries cannot be saved, or `null`: always `null` for any
 *  other destination. The server's rules (`link_problem`), in the same order, so the
 *  field refuses exactly what a save would be refused for. */
export function linkProblem(form: Pick<CampaignForm, 'buttonMeta' | 'buttonUrl'>): string | null {
  if (form.buttonMeta !== 'link') return null
  const url = form.buttonUrl.trim()
  if (url === '') return LINK_URL_MISSING
  if (url.length > CAMPAIGN_MAX_LENGTH.button_url) return LINK_URL_TOO_LONG
  // A backslash (a browser reads it as `/`), a space, a control or format character:
  // Python's `isspace() or not isprintable()` on the server.
  if (/[\\\p{C}\p{Z}]/u.test(url)) return LINK_URL_INVALID
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(url)?.[1]
  if (scheme?.toLowerCase() !== 'https') return LINK_URL_NOT_HTTPS
  // `new URL('https:lu.ma')` would find a host there; `urlsplit` on the server does not.
  if (!/^https:\/\//i.test(url)) return LINK_URL_INVALID
  const authority = url.slice('https://'.length).split(/[/?#]/, 1)[0] ?? ''
  // No host right after `https://` (`new URL` skips extra slashes, `urlsplit` does
  // not), credentials in front of the host, and a percent-encoded host, which `new
  // URL` decodes and `urlsplit` does not: none belongs in a campaign link.
  if (authority === '' || /[@%]/.test(authority)) return LINK_URL_INVALID
  let parsed: URL
  try {
    parsed = new URL(url) // throws on a broken host or a port out of range
  } catch {
    return LINK_URL_INVALID
  }
  if (!parsed.hostname) return LINK_URL_INVALID
  if (!authority.startsWith('[')) {
    // A last label that is a number makes the host an IPv4 address: only four numbers
    // up to 255 pass, on both sides (`new URL` alone would turn `127.1` into one).
    const name = authority.replace(/:[^:]*$/, '').replace(/\.$/, '').toLowerCase()
    const last = name.split('.').at(-1) ?? ''
    if (/^(?:\d+|0x[0-9a-f]*)$/.test(last) && !IPV4.test(name)) return LINK_URL_INVALID
    if (!name.split('.').every(punycodeOk)) return LINK_URL_INVALID
  }
  return null
}

/** The server's `_punycode_ok`: an `xn--` label must decode to printable text, as a
 *  browser requires, whatever this browser's own `new URL` lets through. */
function punycodeOk(label: string): boolean {
  if (!label.startsWith('xn--')) return true
  const decoded = punycodeDecode(label.slice(4))
  return decoded !== null && decoded !== '' && ![...decoded].some((ch) => ch !== ' ' && /[\p{C}\p{Z}]/u.test(ch))
}

/** RFC 3492's decoding, step for step as Python's `punycode` codec runs it in strict
 *  mode (`encodings/punycode.py`), so both sides refuse the same labels: `null` where
 *  Python raises. */
export function punycodeDecode(text: string): string | null {
  // ASCII only, as Python's `text.encode("ascii")` before it decodes.
  if ([...text].some((ch) => ch.charCodeAt(0) > 0x7f)) return null
  const pos = text.lastIndexOf('-')
  const basic = pos === -1 ? '' : text.slice(0, pos)
  const extended = (pos === -1 ? text : text.slice(pos + 1)).toUpperCase()
  const output = [...basic].map((ch) => ch.codePointAt(0)!)
  let char = 0x80
  let index = -1
  let bias = 72
  let at = 0
  while (at < extended.length) {
    // One generalized variable-length integer.
    let delta = 0
    let weight = 1
    let j = 0
    let next = at
    for (;;) {
      if (next >= extended.length) return null
      const code = extended.charCodeAt(next)
      next += 1
      let digit: number
      if (code >= 0x41 && code <= 0x5a) digit = code - 0x41
      else if (code >= 0x30 && code <= 0x39) digit = code - 22
      else return null
      const threshold = Math.min(26, Math.max(1, 36 * (j + 1) - bias))
      delta += digit * weight
      if (digit < threshold) break
      weight *= 36 - threshold
      j += 1
    }
    index += delta + 1
    char += Math.floor(index / (output.length + 1))
    if (char > 0x10ffff) return null
    index %= output.length + 1
    output.splice(index, 0, char)
    bias = adapt(delta, at === 0, output.length)
    at = next
  }
  return String.fromCodePoint(...output)
}

function adapt(delta: number, first: boolean, count: number): number {
  let d = first ? Math.floor(delta / 700) : Math.floor(delta / 2)
  d += Math.floor(d / count)
  let divisions = 0
  while (d > 455) {
    d = Math.floor(d / 35)
    divisions += 36
  }
  return divisions + Math.floor((36 * d) / (d + 38))
}

/** The destinations «Dove porta» offers. A `list` keeps what its campaign measures
 *  (`LIST_IS_FIXED`), and «Un link» measures the click (REB-530), so a «Riscrivi» of a
 *  link stays a link and any other stays off it. */
export function metaOptions(form: Pick<CampaignForm, 'source' | 'action'>): [CampaignMeta, string][] {
  const all = Object.entries(META_LABELS) as [CampaignMeta, string][]
  if (form.source !== 'list') return all
  return all.filter(([value]) => (value === 'link') === (form.action === 'click'))
}

/** «Dove porta» picked. «Un link» measures the click (REB-530), so the action follows
 *  it there, and back from it to the state's own action (`fallback`, the template's)
 *  or to «È entrato nell’area» for a filtered list, whose menu comes back with it. A
 *  `list` keeps its action whatever the button does (`metaOptions` keeps it valid). */
export function withMeta(form: CampaignForm, meta: CampaignMeta, fallback: CampaignAction | undefined): CampaignForm {
  if (form.source === 'list') return { ...form, buttonMeta: meta }
  if (meta === 'link') return { ...form, buttonMeta: meta, action: 'click' }
  const action = form.action === 'click' ? (form.source === 'status' ? (fallback ?? 'signed_in') : 'signed_in') : form.action
  return { ...form, buttonMeta: meta, action }
}

export function contentReady(form: CampaignForm): boolean {
  return form.subject.trim() !== '' && form.text.trim() !== '' && form.buttonText.trim() !== ''
}
