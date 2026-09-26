import { amountFilter } from '@/lib/amount'
import type { AudiencePreview, Campaign, CampaignAzione, CampaignDraft, CampaignMeta, CampaignTemplate } from '@/lib/api'

export type Fonte = 'stato' | 'filtri'
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
  azione: CampaignAzione
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
  azione: 'entrato',
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

/** The draft the page saves, or `null` while there is nothing to save yet: a state
 *  source with no state picked has no list and no template behind it. */
export function payloadOf(form: CampaignForm): CampaignDraft | null {
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
    azione: form.azione,
  }
}

/** The key a save is compared by. `JSON.stringify` drops the `undefined` of an empty
 *  filter, as the request body does, so two forms that send the same body compare equal. */
export function keyOf(payload: CampaignDraft | null): string | null {
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
    fonte: c.fonte === 'filtri' ? 'filtri' : 'stato',
    statoPercorso: c.stato_percorso,
    oggetto: c.oggetto,
    testo: c.testo,
    bottoneTesto: c.bottone_testo,
    bottoneMeta: c.bottone_meta,
    azione: c.azione,
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
        tariffa_min: stored(f.tariffa_min),
        tariffa_max: stored(f.tariffa_max),
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
      budget_min: stored(f.budget_min),
      budget_max: stored(f.budget_max),
      periodo_da: stored(f.periodo_da, { day: true }),
      origine: stored(f.origine),
      creato_da: stored(f.creato_da, { day: true }),
      creato_a: stored(f.creato_a, { day: true }),
    },
  }
}

/** A state picked from «Stato del percorso». The action always follows the template,
 *  since it is not the admin's to set for a state (fix 4, REB-472 round 1); the mail
 *  follows it only until the admin has written into it (spec § 1). */
export function withTemplate(form: CampaignForm, template: CampaignTemplate | undefined, value: string, touched: boolean): CampaignForm {
  const next = { ...form, statoPercorso: value }
  if (!template) return next
  next.azione = template.azione
  if (touched) return next
  return {
    ...next,
    nome: template.etichetta,
    oggetto: template.oggetto,
    testo: template.testo,
    bottoneTesto: template.bottone_testo,
    bottoneMeta: template.bottone_meta,
  }
}

/** What decides who is on the list: the list is reloaded when this changes and never
 *  on an edit of the mail alone. The action is not part of it: whoever has already
 *  done it is skipped when the mail leaves (`campaigns/tick.py`), not left off the list. */
export function audienceSource(c: { fonte: string; stato_percorso?: string | null; filtri?: Record<string, unknown> | null }): string {
  return JSON.stringify([c.fonte, c.stato_percorso ?? null, c.filtri ?? null])
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

export function contentReady(form: CampaignForm): boolean {
  return form.oggetto.trim() !== '' && form.testo.trim() !== '' && form.bottoneTesto.trim() !== ''
}
