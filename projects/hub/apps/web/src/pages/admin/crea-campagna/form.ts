import { amountFilter } from '@/lib/amount'
import type { AudiencePreview, Campaign, CampaignAzione, CampaignDraft, CampaignMeta, CampaignTemplate } from '@/lib/api'
import { CAMPAIGN_MAX_LENGTH, META_LABELS } from '@/lib/campaigns'

export type Fonte = 'stato' | 'filtri' | 'lista'
export type Lista = 'talenti' | 'aziende'

// A `Select` cannot take an item with value `""` -- Radix reserves it for "nothing
// picked yet", which is exactly what an unset filter or an unset template is here.
export const NONE = ''
export const ANY = 'tutti'

export function boolToSelect(value: boolean | undefined): string {
  return value === undefined ? ANY : value ? 'si' : 'no'
}

function selectToBool(value: string): boolean | undefined {
  return value === 'si' ? true : value === 'no' ? false : undefined
}

/** The Talenti list's own filters (spec § 2), field for field the server's
 *  `TalentiFiltri` (`rebase_core/campaigns/schemas.py`) and the list page's
 *  `TalentiFilters`: every value a string as its input holds it, `ANY` for a `Select`
 *  left on «Tutti». `payloadOf` below turns it into the server's shape. */
export interface TalentiFiltriForm {
  stato: string
  q: string
  posizione: string
  remoto: string
  tariffa_min: string
  tariffa_max: string
  origine: string
  utm_source: string
  has_cv: string
  con_accessi: string
  creato_da: string
  creato_a: string
}

/** The company list's own filters, the server's `AziendeFiltri`. */
export interface AziendeFiltriForm {
  stato: string
  q: string
  budget_min: string
  budget_max: string
  periodo_da: string
  origine: string
  creato_da: string
  creato_a: string
}

export const TALENTI_FILTRI_EMPTY: TalentiFiltriForm = {
  stato: ANY,
  q: '',
  posizione: '',
  remoto: ANY,
  tariffa_min: '',
  tariffa_max: '',
  origine: '',
  utm_source: '',
  has_cv: ANY,
  con_accessi: ANY,
  creato_da: '',
  creato_a: '',
}
export const AZIENDE_FILTRI_EMPTY: AziendeFiltriForm = {
  stato: ANY,
  q: '',
  budget_min: '',
  budget_max: '',
  periodo_da: '',
  origine: '',
  creato_da: '',
  creato_a: '',
}

/** The filters that stay on screen; the rest sit behind «Altri filtri». */
export const TALENTI_MAIN: readonly (keyof TalentiFiltriForm)[] = ['stato', 'q', 'has_cv', 'con_accessi']
export const AZIENDE_MAIN: readonly (keyof AziendeFiltriForm)[] = ['stato', 'q']

/** Whether any filter outside the main ones holds a value: the edit route opens
 *  «Altri filtri» then, so a stored filter is never hidden. */
export function hasOtherFilters(form: Pick<CampaignForm, 'lista' | 'talenti' | 'aziende'>): boolean {
  return form.lista === 'talenti'
    ? otherSet(form.talenti, TALENTI_FILTRI_EMPTY, TALENTI_MAIN)
    : otherSet(form.aziende, AZIENDE_FILTRI_EMPTY, AZIENDE_MAIN)
}

function otherSet<T extends object>(values: T, empty: T, main: readonly (keyof T)[]): boolean {
  return (Object.keys(values) as (keyof T)[]).some((key) => !main.includes(key) && values[key] !== empty[key])
}

/** Everything the page edits, in one value: the page's draft is a function of it, so
 *  "has anything changed since the last save" is one string comparison. */
export interface CampaignForm {
  nome: string
  fonte: Fonte
  statoPercorso: string | null
  lista: Lista
  talenti: TalentiFiltriForm
  aziende: AziendeFiltriForm
  oggetto: string
  testo: string
  bottoneTesto: string
  bottoneMeta: CampaignMeta
  /** The address «Un link» leads to, as typed (REB-530); kept while another destination
   *  is picked, so going back to «Un link» finds it, and never sent with that one. */
  bottoneUrl: string
  azione: CampaignAzione
  /** The campaign a `lista` draft follows (`Campaign.segue_id`): `null` for a `stato`
   *  or `filtri` form, which picks its own audience instead. */
  segueId: string | null
}

export const EMPTY_FORM: CampaignForm = {
  nome: '',
  fonte: 'stato',
  statoPercorso: null,
  lista: 'talenti',
  talenti: TALENTI_FILTRI_EMPTY,
  aziende: AZIENDE_FILTRI_EMPTY,
  oggetto: '',
  testo: '',
  bottoneTesto: '',
  bottoneMeta: 'area',
  bottoneUrl: '',
  azione: 'entrato',
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
export function defaultNome(fonte: Fonte): string {
  return fonte === 'filtri' ? 'Campagna da filtri' : 'Nuova campagna'
}

/** The filters as the server takes them. An amount goes as the lists send it (REB-485),
 *  two decimals and a dot, and one that is not an amount narrows nothing, as there. */
function filtriOf(form: CampaignForm): Record<string, unknown> | null {
  if (form.fonte !== 'filtri') return null
  if (form.lista === 'talenti') {
    const t = form.talenti
    return {
      lista: form.lista,
      stato: picked(t.stato),
      q: typed(t.q),
      posizione: typed(t.posizione),
      remoto: picked(t.remoto),
      tariffa_min: amountFilter(typed(t.tariffa_min)),
      tariffa_max: amountFilter(typed(t.tariffa_max)),
      origine: typed(t.origine),
      utm_source: typed(t.utm_source),
      has_cv: selectToBool(t.has_cv),
      con_accessi: selectToBool(t.con_accessi),
      creato_da: typed(t.creato_da),
      creato_a: typed(t.creato_a),
    }
  }
  const a = form.aziende
  return {
    lista: form.lista,
    stato: picked(a.stato),
    q: typed(a.q),
    budget_min: amountFilter(typed(a.budget_min)),
    budget_max: amountFilter(typed(a.budget_max)),
    periodo_da: typed(a.periodo_da),
    origine: typed(a.origine),
    creato_da: typed(a.creato_da),
    creato_a: typed(a.creato_a),
  }
}

/** What a `lista` saves: the mail alone. Whom it reaches and what it measures come from
 *  the campaign it follows, and the API refuses a change to either (`LIST_IS_FIXED`). */
export type ListaPatch = Pick<CampaignDraft, 'nome' | 'oggetto' | 'testo' | 'bottone_testo' | 'bottone_meta' | 'bottone_url'>

/** The address the button carries: «Un link»'s own, trimmed, and nothing at all for any
 *  other destination, so the request of a hub button stays what it always was and the
 *  server drops a stored address with the link (`CampaignService.update`). */
function linkOf(form: CampaignForm): string | undefined {
  return form.bottoneMeta === 'link' ? form.bottoneUrl.trim() : undefined
}

/** The draft the page saves, or `null` while there is nothing to save yet: a state
 *  source with no state picked has no list and no template behind it. A `lista` is
 *  never new on this page (Task 3's `follow-up` creates it), so it always has a body:
 *  the mail, with no `fonte` -- the API's `CampaignPatch.fonte` does not accept
 *  `'lista'`, and a change to `stato_percorso`/`filtri`/`azione` is refused either way. */
export function payloadOf(form: CampaignForm): CampaignDraft | ListaPatch | null {
  if (form.fonte === 'lista') {
    return {
      nome: form.nome.trim() || defaultNome(form.fonte),
      oggetto: form.oggetto,
      testo: form.testo,
      bottone_testo: form.bottoneTesto,
      bottone_meta: form.bottoneMeta,
      bottone_url: linkOf(form),
    }
  }
  if (form.fonte === 'stato' && form.statoPercorso === null) return null
  return {
    nome: form.nome.trim() || defaultNome(form.fonte),
    fonte: form.fonte,
    stato_percorso: form.fonte === 'stato' ? form.statoPercorso : null,
    filtri: filtriOf(form),
    oggetto: form.oggetto,
    testo: form.testo,
    bottone_testo: form.bottoneTesto,
    bottone_meta: form.bottoneMeta,
    bottone_url: linkOf(form),
    azione: form.azione,
  }
}

/** The key a save is compared by. `JSON.stringify` drops the `undefined` of an empty
 *  filter, as the request body does, so two forms that send the same body compare equal. */
export function keyOf(payload: CampaignDraft | ListaPatch | null): string | null {
  return payload === null ? null : JSON.stringify(payload)
}

/** The form a stored campaign reads back as, on the edit route. The server always
 *  writes its own `lista` into `filtri` (`payloadOf` sends it on every save), so this
 *  reads that key directly rather than guessing the list from which of the
 *  Talenti-only fields happen to be present (fix 2, REB-472 round 1). */
export function formFromCampaign(c: Campaign): CampaignForm {
  const base: CampaignForm = {
    ...EMPTY_FORM,
    nome: c.nome,
    fonte: c.fonte,
    statoPercorso: c.stato_percorso,
    oggetto: c.oggetto,
    testo: c.testo,
    bottoneTesto: c.bottone_testo,
    bottoneMeta: c.bottone_meta,
    bottoneUrl: c.bottone_url ?? '',
    azione: c.azione,
    segueId: c.segue_id,
  }
  if (c.fonte !== 'filtri' || !c.filtri) return base
  const f = c.filtri as Record<string, unknown>
  const stato = stored(f.stato) || ANY
  if (f.lista !== 'aziende') {
    return {
      ...base,
      lista: 'talenti',
      talenti: {
        stato,
        q: stored(f.q),
        posizione: stored(f.posizione),
        remoto: stored(f.remoto) || ANY,
        tariffa_min: storedAmount(f.tariffa_min),
        tariffa_max: storedAmount(f.tariffa_max),
        origine: stored(f.origine),
        utm_source: stored(f.utm_source),
        has_cv: boolToSelect(f.has_cv as boolean | undefined),
        con_accessi: boolToSelect(f.con_accessi as boolean | undefined),
        creato_da: stored(f.creato_da, { day: true }),
        creato_a: stored(f.creato_a, { day: true }),
      },
    }
  }
  return {
    ...base,
    lista: 'aziende',
    aziende: {
      stato,
      q: stored(f.q),
      budget_min: storedAmount(f.budget_min),
      budget_max: storedAmount(f.budget_max),
      periodo_da: stored(f.periodo_da, { day: true }),
      origine: stored(f.origine),
      creato_da: stored(f.creato_da, { day: true }),
      creato_a: stored(f.creato_a, { day: true }),
    },
  }
}

/** A state picked from «Stato del percorso». The action always follows the template,
 *  since it is not the admin's to set for a state (fix 4, REB-472 round 1); the mail
 *  follows it only until the admin has written into it (spec § 1), and the name only
 *  until the admin has typed one: the name is not the mail, so typing the title first
 *  must not stop the template from filling the mail. */
export function withTemplate(
  form: CampaignForm,
  template: CampaignTemplate | undefined,
  value: string,
  touched: { mail: boolean; nome: boolean },
): CampaignForm {
  const next = { ...form, statoPercorso: value }
  if (!template) return next
  // A button the admin sent to «Un link» keeps measuring the click (REB-530).
  next.azione = touched.mail && form.bottoneMeta === 'link' ? 'clic' : template.azione
  if (!touched.nome) next.nome = template.etichetta
  if (touched.mail) return next
  return {
    ...next,
    oggetto: template.oggetto,
    testo: template.testo,
    bottoneTesto: template.bottone_testo,
    bottoneMeta: template.bottone_meta,
  }
}

/** What decides who is on the list: the list is reloaded when this changes and never
 *  on an edit of the mail alone. The action is not part of it: whoever has already
 *  done it is skipped when the mail leaves (`campaigns/tick.py`), not left off the list.
 *  A `lista` body (`ListaPatch`) carries none of these -- its audience is fixed by the
 *  campaign it follows -- so `fonte` is optional and such a body always keys as
 *  `[null, null, null]`, a key that never changes and so never goes stale. */
export function audienceSource(c: { fonte?: string; stato_percorso?: string | null; filtri?: Record<string, unknown> | null }): string {
  return JSON.stringify([c.fonte ?? null, c.stato_percorso ?? null, c.filtri ?? null])
}

export interface AudienceCount {
  /** Who gets the mail: the list, less the rules' exclusions and the admin's unticks. */
  riceveranno: number
  /** Left out by the rules: an admin, «non scrivere mai», an opt-out, a bounce, a
   *  recent campaign. */
  regole: number
  /** Unticked by the admin, among the rows the rules let through. */
  tolte: number
  /** The unticked addresses still on the list, as «Invia» sends them. */
  esclusi: string[]
}

export function countAudience(audience: AudiencePreview, esclusi: readonly string[]): AudienceCount {
  const unticked = new Set(esclusi)
  const open = audience.righe.filter((row) => row.escluso === null)
  const tolte = open.filter((row) => unticked.has(row.email)).map((row) => row.email)
  return {
    riceveranno: open.length - tolte.length,
    regole: audience.righe.length - open.length,
    tolte: tolte.length,
    esclusi: tolte,
  }
}

// «Un link» (REB-530), the server's own sentences (`rebase_core/campaigns/links.py`),
// so the field says at once what a save would be refused for.
export const LINK_URL_MISSING = 'Scrivi il link a cui porta il bottone.'
export const LINK_URL_TOO_LONG = `Il link del bottone è troppo lungo: al massimo ${CAMPAIGN_MAX_LENGTH.bottone_url} caratteri.`
export const LINK_URL_NOT_HTTPS = 'Il link del bottone deve iniziare con https://.'
export const LINK_URL_INVALID = 'Il link del bottone non è un indirizzo valido.'

const OCTET = '(?:25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)'
const IPV4 = new RegExp(`^${OCTET}(?:\\.${OCTET}){3}$`)

/** Why the address «Un link» carries cannot be saved, or `null`: always `null` for any
 *  other destination. The server's rules (`link_problem`), in the same order, so the
 *  field refuses exactly what a save would be refused for. */
export function linkProblem(form: Pick<CampaignForm, 'bottoneMeta' | 'bottoneUrl'>): string | null {
  if (form.bottoneMeta !== 'link') return null
  const url = form.bottoneUrl.trim()
  if (url === '') return LINK_URL_MISSING
  if (url.length > CAMPAIGN_MAX_LENGTH.bottone_url) return LINK_URL_TOO_LONG
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
  }
  return null
}

/** The destinations «Dove porta» offers. A `lista` keeps what its campaign measures
 *  (`LIST_IS_FIXED`), and «Un link» measures the click (REB-530), so a «Riscrivi» of a
 *  link stays a link and any other stays off it. */
export function metaOptions(form: Pick<CampaignForm, 'fonte' | 'azione'>): [CampaignMeta, string][] {
  const all = Object.entries(META_LABELS) as [CampaignMeta, string][]
  if (form.fonte !== 'lista') return all
  return all.filter(([value]) => (value === 'link') === (form.azione === 'clic'))
}

/** «Dove porta» picked. «Un link» measures the click (REB-530), so the action follows
 *  it there, and back from it to the state's own action (`fallback`, the template's)
 *  or to «È entrato nell’area» for a filtered list, whose menu comes back with it. A
 *  `lista` keeps its action whatever the button does (`metaOptions` keeps it valid). */
export function withMeta(form: CampaignForm, meta: CampaignMeta, fallback: CampaignAzione | undefined): CampaignForm {
  if (form.fonte === 'lista') return { ...form, bottoneMeta: meta }
  if (meta === 'link') return { ...form, bottoneMeta: meta, azione: 'clic' }
  const azione = form.azione === 'clic' ? (form.fonte === 'stato' ? (fallback ?? 'entrato') : 'entrato') : form.azione
  return { ...form, bottoneMeta: meta, azione }
}

export function contentReady(form: CampaignForm): boolean {
  return form.oggetto.trim() !== '' && form.testo.trim() !== '' && form.bottoneTesto.trim() !== ''
}
