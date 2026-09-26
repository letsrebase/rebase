/**
 * The hub's HTTP client: plain `fetch`, same origin, cookies included. The API is a
 * handful of routes and three shapes, so a generated client would be more machinery
 * than code.
 *
 * Every failure becomes an `ApiError` carrying the status and, for a 422, the field
 * names FastAPI put in `detail[].loc`, which is what a wizard needs to point at the
 * right question rather than blame the whole form.
 */

import { linkedinProfile } from './linkedin'
import type { Utm } from './utm'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly fields: string[] = [],
  ) {
    super(message)
  }
}

interface ValidationItem {
  loc?: unknown[]
  msg?: string
}

async function fail(response: Response): Promise<never> {
  let detail: unknown = null
  try {
    detail = (await response.json())?.detail
  } catch {
    detail = null
  }
  if (Array.isArray(detail)) {
    const items = detail as ValidationItem[]
    const fields = items.map((item) => String(item.loc?.at(-1) ?? '')).filter(Boolean)
    const message = items.map((item) => item.msg).filter(Boolean).join(' · ') || 'Dati non validi.'
    throw new ApiError(response.status, message, fields)
  }
  if (response.status === 429) {
    throw new ApiError(429, 'Troppe richieste da qui. Riprova tra un minuto.')
  }
  throw new ApiError(
    response.status,
    typeof detail === 'string' ? detail : `Qualcosa è andato storto (${response.status}).`,
  )
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) await fail(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

/** A file the API answers, a preview PDF: the same error handling as `request`. */
async function requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) await fail(response)
  return response.blob()
}

const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ---- the two wizards ------------------------------------------------------------------

export type Remoto = 'remoto' | 'ibrido' | 'in_sede'

export interface FreelancerApplication {
  nome: string
  cognome: string
  email: string
  linkedin_url: string
  tariffa_giornaliera: string
  posizione: string
  remoto: Remoto | ''
  links: string[]
  cv: File | null
}

/** Multipart, because the CV is a file. Empty optional fields are left out rather than
 *  sent as `""`, which the API would try to validate as a value. */
export function applyAsFreelancer(
  data: FreelancerApplication,
  utm: Utm,
  distinctId: string | null = null,
): Promise<{ ok: true }> {
  const form = new FormData()
  // The browser's PostHog id, when the page is measured: the API sends the completion
  // event itself (REB-215), and this is what puts it on the same person as the steps.
  if (distinctId) form.set('distinct_id', distinctId)
  form.set('nome', data.nome)
  form.set('cognome', data.cognome)
  form.set('email', data.email)
  form.set('tariffa_giornaliera', data.tariffa_giornaliera)
  form.set('posizione', data.posizione)
  form.set('remoto', data.remoto)
  const linkedin = linkedinProfile(data.linkedin_url)
  if (linkedin) form.set('linkedin_url', linkedin)
  for (const link of data.links) if (link.trim()) form.append('links', link.trim())
  for (const [key, value] of Object.entries(utm)) if (value) form.set(key, value)
  if (data.cv) form.set('cv', data.cv, data.cv.name)
  return request('/api/hub/freelancers', { method: 'POST', body: form })
}

export interface CompanyRequest {
  nome_azienda: string
  figura_richiesta: string
  referente_nome: string
  referente_cognome: string
  email: string
  telefono: string
  progetto: string
  periodo_da: string
  durata: string
  budget_giornaliero: string
  remoto: Remoto | ''
  giorni_presenza: string
  numero_risorse: string
}

export function requestPeople(
  data: CompanyRequest,
  utm: Utm,
  distinctId: string | null = null,
): Promise<{ ok: true }> {
  return request(
    '/api/hub/companies',
    json({
      ...data,
      // `''` means "not ibrido, never asked": the API's own `giorni_presenza` is
      // `int | None`, which a blank string does not coerce to.
      giorni_presenza: data.giorni_presenza ? Number(data.giorni_presenza) : null,
      utm: Object.keys(utm).length ? utm : null,
      ...(distinctId ? { distinct_id: distinctId } : {}),
    }),
  )
}

// ---- the admin area -------------------------------------------------------------------

export type Role = 'member' | 'admin'

export interface Admin {
  id: string
  email: string
  nome: string
  attivo: boolean
  created_at: string
}

/** `GET /api/hub/admins`'s shape since REB-313: oldest first with no `q`, so the page
 *  still reads as a history (ORB-123), best-match first once `q` narrows it. */
export interface AdminList {
  items: Admin[]
  next_cursor: string | null
}

/** What `POST /admins/promote` sends: an email, and `nome`/`cognome` for an address
 *  with no `users` row yet -- ignored, harmlessly, when one already exists. */
export interface PromoteRequest {
  email: string
  nome?: string
  cognome?: string
}

export interface Freelancer {
  id: string
  nome: string
  cognome: string
  email: string
  linkedin_url: string | null
  /** Null on a card an admin wrote from a signup, until the person adds them (ORB-155). */
  cv_filename: string | null
  cv_size: number | null
  tariffa_giornaliera: string | null
  posizione: string | null
  remoto: Remoto | null
  links: string[]
  stato: 'nuovo' | 'contattato' | 'attivo' | 'scartato'
  note: string | null
  /** The page of the site the person started from, `home` or `pigrocrm` (ORB-167). */
  origine: string | null
  utm_source: string | null
  utm_campaign: string | null
  created_at: string
  /** Who wrote the answers last: the person, through the wizard or the member area, or
   *  an admin from research. */
  compilata_da: 'persona' | 'admin'
  /** CV, rate, position and remote preference all present. */
  completa: boolean
  /** The thread, newest first. The detail carries it; the list leaves it empty. */
  commenti: Comment[]
  /** How many times the person came in through the magic link (ORB-158). */
  accessi: number
  /** Where the lead came from (ORB-161): «form» when the address is also among the
   *  signups («Iscrizioni»), «landing» otherwise. */
  provenienza: 'form' | 'landing'
  /** When they last did, null if never. */
  ultimo_accesso: string | null
  /** `null` while the card is live; a moment once an admin soft-deletes it (REB-347),
   *  reversed by `restoreFreelancer` (REB-355). */
  deleted_at: string | null
}

export interface Company {
  id: string
  nome_azienda: string
  referente: string
  email: string
  telefono: string | null
  figura_richiesta: string
  progetto: string
  periodo_da: string
  durata: string
  budget_giornaliero: string
  remoto: Remoto
  giorni_presenza: number | null
  numero_risorse: number
  stato: 'nuovo' | 'contattato' | 'in_corso' | 'chiuso'
  note: string | null
  /** The page of the site the person started from, `home` or `pigrocrm` (ORB-167). */
  origine: string | null
  utm_source: string | null
  created_at: string
  commenti: Comment[]
  /** Same soft-delete as `Freelancer.deleted_at`. */
  deleted_at: string | null
}

/** One remark in a row's thread: appended, signed and dated, never edited. */
export interface Comment {
  id: string
  entity_type: 'freelancer' | 'company'
  entity_id: string
  testo: string
  autore: string
  created_at: string
}

/** The two rows a thread can hang on, as the API paths name them. */
export type CommentKind = 'freelancers' | 'companies'

/** One space of PigroCRM as the registry knows it, plus the hub member who opened it
 *  when the address is one the wizard has seen (ORB-142). */
export interface PigroSpace {
  slug: string
  owner_email: string
  created_at: string
  url: string
  membro: { id: string; nome: string; cognome: string } | null
}

/** One entry of the override/clear/delete/restore/revert trail (REB-347), as an admin
 *  reads it (REB-355): who, when, which kind, and the diff for an `overridden`/
 *  `cleared` entry -- `changed`/`before`/`after` are absent on a `deleted`/`restored`
 *  entry, whose kind is the whole story. */
export interface AdminAction {
  id: string
  entity_type: string
  entity_id: string
  kind:
    | 'overridden'
    | 'cleared'
    | 'deleted'
    | 'restored'
    /** A framework agreement's own actions (REB-407), recorded on entity `freelancer`. */
    | 'mail_resent'
    | 'document_cancelled'
    | 'notice_recorded'
  admin_id: string
  admin_nome: string
  payload: {
    changed?: string[]
    before?: Record<string, unknown>
    after?: Record<string, unknown>
  }
  created_at: string
}

/** What an admin may set or clear on a `Freelancer` beyond `stato`/`note` (REB-347),
 *  mirroring `FreelancerOverride` on the server one for one: a key present and `null`
 *  clears a nullable field, a key left out of the body leaves it alone. `links` and
 *  `nome`/`cognome` are never nullable columns, so those are only ever a value. */
export interface FreelancerOverride {
  nome?: string
  cognome?: string
  linkedin_url?: string | null
  tariffa_giornaliera?: string | null
  posizione?: string | null
  remoto?: Remoto | null
  links?: string[]
  compilata_da?: 'persona' | 'admin'
}

/** Same contract as `FreelancerOverride`, for a `Company` request's own fields beyond
 *  `stato`/`note`: the eight project answers (REB-314; REB-380 adds the last four),
 *  the company's own name, and the referente's identity on the linked `users` row --
 *  admin-only even for a self-edit. `giorni_presenza` is the one nullable addition,
 *  the same as `FreelancerOverride.remoto`; `remoto`/`numero_risorse`/
 *  `figura_richiesta` are never cleared, like `nome_azienda`. */
export interface CompanyOverride {
  nome?: string
  cognome?: string
  linkedin_url?: string | null
  nome_azienda?: string
  figura_richiesta?: string
  progetto?: string
  periodo_da?: string
  durata?: string
  budget_giornaliero?: string
  remoto?: Remoto
  giorni_presenza?: number | null
  numero_risorse?: number
}

/** The guide's numbers for the admin area (ORB-156), as `GET /api/hub/perks/guide` answers. */
export interface GuideStats {
  totale: number
  membri: number
  membri_totali: number
  ultimi_7_giorni: number
  recenti: GuideDownload[]
}

export interface GuideDownload {
  id: string
  freelancer_id: string
  nome: string
  cognome: string
  email: string
  downloaded_at: string
}

/** The logins for the admin area (ORB-158), as `GET /api/hub/logins` answers. The four
 *  counters read the whole table, unaffected by `q`; `recenti` is the searched,
 *  cursor-paginated part, no longer capped at 20 (REB-313). */
export interface LoginStats {
  totale: number
  membri: number
  membri_totali: number
  ultimi_7_giorni: number
  recenti: LoginRead[]
  next_cursor: string | null
}

export interface LoginRead {
  id: string
  user_id: string
  nome: string
  cognome: string
  email: string
  logged_at: string
  /** The campaign the login page was opened from (REB-426); `null` when it had none. */
  origine: string | null
  utm_source: string | null
  utm_medium: string | null
  utm_campaign: string | null
  utm_content: string | null
  utm_term: string | null
  utm_id: string | null
}

/** One row of `talenti` (REB-282/283): every freelancer card and every bare sign-up
 *  as one row, `stato` `lead` for the bare ones and the freelancer's own state
 *  otherwise, as `GET /api/hub/talenti` answers -- the single list that replaced
 *  «Developer e CTO» and «Iscrizioni». `origine` names how the row came to be:
 *  `form` for a bare sign-up, `wizard` for a card the person filled in themselves,
 *  `admin` for one an admin drafted from research (ORB-155). */
export interface Talento {
  id: string
  nome: string | null
  cognome: string | null
  email: string
  linkedin_url: string | null
  stato: string
  origine: 'form' | 'wizard' | 'admin'
  utm_source: string | null
  created_at: string
}

/** What an admin found about a signup on the public web (ORB-155): a name, maybe a
 *  LinkedIn profile, a position, some links, and at least one source, since a card
 *  written from research with no source is a card nobody can check. The body
 *  `POST /api/hub/signups/{id}/card` expects -- the same `draft_from_signup` the
 *  MCP tool `create_freelancer_from_signup` calls. */
export interface FreelancerDraft {
  nome: string
  cognome: string
  linkedin_url?: string
  posizione?: string
  tariffa_giornaliera?: string
  remoto?: Remoto
  links?: string[]
  fonti: string[]
}

/** A personal token of the admin, as `GET /api/hub/tokens` lists it (REB-213): never the value. */
export interface AdminToken {
  id: string
  nome: string
  prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

/** The `POST` answer: the one place the value appears, shown once. */
export interface CreatedToken extends AdminToken {
  token: string
}

/** What `GET /api/hub/talent` takes beside `limit` (REB-285/286): `q` searches
 *  name/surname/email/`posizione`, trigram-ordered when present; every other field
 *  narrows the merged list of cards and bare sign-ups the same way the state pills
 *  always have. Mirrors `list_talenti`'s own parameters in
 *  `apps/api/src/rebase_api/routers/admin.py` one for one -- this is also the shape
 *  `/admin/talent`'s own `validateSearch` carries in the URL (`router.tsx`). */
export interface TalentiFilters {
  stato?: string
  q?: string
  posizione?: string
  remoto?: Remoto
  tariffa_min?: string
  tariffa_max?: string
  origine?: string
  utm_source?: string
  has_cv?: boolean
  con_accessi?: boolean
  creato_da?: string
  creato_a?: string
}

export interface TalentoList {
  totale: number
  items: Talento[]
  per_stato: Record<string, number>
  next_cursor: string | null
}

/** What `GET /api/hub/companies` takes beside `limit` (REB-285/286), mirroring
 *  `list_companies`'s own parameters -- the shape `/admin/companies`'s own
 *  `validateSearch` carries in the URL. */
export interface CompaniesFilters {
  stato?: string
  q?: string
  budget_min?: string
  budget_max?: string
  periodo_da?: string
  origine?: string
  creato_da?: string
  creato_a?: string
}

export interface CompanyList {
  totale: number
  items: Company[]
  per_stato: Record<string, number>
  next_cursor: string | null
}

/** REB-387: a match's state, and a contract document's. */
export type MatchStato = 'bozza' | 'in_firma' | 'attivo' | 'concluso' | 'annullato'
export type DocumentStato = 'generato' | 'in_attesa' | 'inviato' | 'firmato' | 'annullato' | 'disdetto'

/** What an admin can do next on a document or a match (REB-477), as the core's
 *  `match_words` names it: the page maps each to its button and its API call. */
export type Action =
  | 'invia'
  | 'reinvia_email'
  | 'aggiorna_stato'
  | 'annulla'
  | 'chiudi'
  | 'registra_disdetta'
  | 'riprova_pigro'

/** A freelancer's tax data, as the two contracts print them. */
export interface FiscalData {
  codice_fiscale: string
  partita_iva: string
  domicilio: string
  pec: string | null
}

export interface Fiscal extends FiscalData {
  freelancer_id: string
  updated_by: string
  updated_at: string
}

/** A generated contract as the pages read it: never its bytes, which are a link. */
export interface ContractDocument {
  id: string
  kind: 'quadro' | 'lettera'
  freelancer_id: string
  match_id: string | null
  numero: string | null
  text_version: string
  testo_bozza: boolean
  stato: DocumentStato
  created_at: string
  created_by: string
  sent_at: string | null
  signed_at: string | null
  notice_at: string | null
  /** Why it was cancelled: the freelancer's own reason when they refused it, or who
   *  cancelled it (REB-407). */
  cancel_reason: string | null
  ha_pdf_firmato: boolean
  attivo: boolean
  rinnovo: string | null
  ultimo_giorno_disdetta: string | null
  nuova_versione: boolean
  /** What happened and what comes next, in a sentence (REB-477). */
  situazione: string
  prossima_azione: Action | null
  altre_azioni: Action[]
}

export interface Match {
  id: string
  freelancer_id: string
  company_id: string
  nome_azienda: string
  figura_richiesta: string
  cliente_ragione_sociale: string
  cliente_piva: string
  cliente_sede: string
  stato: MatchStato
  created_at: string
  created_by: string
  cancelled_at: string | null
  updated_at: string
  lettera: ContractDocument
  /** Same as the document's: the match's sentence and its actions (REB-477). */
  situazione: string
  prossima_azione: Action | null
  altre_azioni: Action[]
}

/** One row of the admin's «Match» list (REB-413): never a tax field and never
 *  `budget_giornaliero`, the same rule `Match` and `ContractDocument` already keep.
 *  `lettera_*` is `null` together, only were a match somehow to have no letter. */
export interface MatchListItem {
  id: string
  freelancer_id: string
  freelancer_nome: string
  freelancer_cognome: string
  freelancer_email: string
  nome_azienda: string
  figura_richiesta: string
  stato: MatchStato
  lettera_numero: string | null
  lettera_stato: DocumentStato | null
  lettera_data_inizio: string | null
  lettera_data_fine: string | null
  created_at: string
  created_by_nome: string
  created_by_email: string
  /** The match's sentence, the one `Match` carries (REB-477). */
  situazione: string
}

/** `GET /api/hub/matches`'s shape (REB-413): newest first, `totale` counting every
 *  row the filters select, not just the page returned. */
export interface MatchList {
  totale: number
  items: MatchListItem[]
}

/** What `GET /api/hub/matches` takes beside `limit`/`offset` (REB-413): `stato` one of
 *  `MatchStato`, `q` matching the freelancer's name, surname or email and the
 *  company's name. */
export interface MatchesFilters {
  stato?: string
  q?: string
}

/** «Match e contratti»: the framework agreement at the top, every one of them, the
 *  matches newest first, and the tax data the page edits. */
export interface FreelancerContracts {
  freelancer_id: string
  quadro: ContractDocument | null
  quadri: ContractDocument[]
  matches: Match[]
  fiscale: Fiscal | null
}

/** What «Invia per la firma» did (REB-390): the document that left now, none when the
 *  letter waits for a framework agreement already out for signature, and whether its
 *  mail left too. */
export interface SendReport {
  match: Match
  inviato: 'quadro' | 'lettera' | null
  mail_inviata: boolean | null
}

/** A contract as its freelancer reads it in «Contratti» (REB-392): the signing link only
 *  while the document waits for the signature. */
export interface MemberContract {
  id: string
  kind: 'quadro' | 'lettera'
  numero: string | null
  stato: DocumentStato
  cliente: string | null
  inizio: string | null
  fine: string | null
  sent_at: string | null
  signed_at: string | null
  signing_url: string | null
  ha_pdf_firmato: boolean
  attivo: boolean
  rinnovo: string | null
  ultimo_giorno_disdetta: string | null
}

/** `quadri_precedenti` (REB-392): the freelancer's other framework agreements that were
 *  signed, newest first, excluding the one in `quadro` -- a notice, or a newer one
 *  replacing it, moves the older one here rather than off the page. */
export interface MemberContracts {
  quadro: MemberContract | null
  quadri_precedenti: MemberContract[]
  lettere: MemberContract[]
}

/** The letter's text fields, in the order `lettera-di-incarico.md` asks for them and the
 *  server's `LETTERA_TEXT_FIELDS` lists them. */
export const LETTERA_TEXT_KEYS = [
  'ruolo',
  'attivita',
  'risultati',
  'accettazione',
  'impegno',
  'periodo_verifica',
  'luogo',
  'coordinamento',
  'referente_cliente',
  'referente_rebase',
  'modalita',
  'unita',
  'lavoro_extra',
  'spese',
  'scadenze_fatturazione',
  'dati_personali',
  'dati_finalita',
  'dati_categorie',
  'dati_interessati',
  'dati_autorizzazione',
  'esclusiva',
  'portfolio',
  'assicurazione',
  'altre_condizioni',
  'rapporti_precedenti',
] as const
export type LetteraTextKey = (typeof LETTERA_TEXT_KEYS)[number]

/** What `LetteraFields` takes: an empty text field is `null` and prints a blank line. */
export type Lettera = Record<LetteraTextKey, string | null> & {
  data_inizio: string
  data_fine: string | null
  compenso: string
  giorni_pagamento: number
  fine_mese: boolean
  giorni_preavviso: number | null
}
export type LetteraDraft = { [K in keyof Lettera]: Lettera[K] | null }

export interface Cliente {
  cliente_ragione_sociale: string
  cliente_piva: string
  cliente_sede: string
}
export type ClienteDraft = { [K in keyof Cliente]: string | null }

/** `id` is optional and client-generated (REB-406): one per wizard run, sent with both
 *  «Salva senza inviare» and «Invia per la firma», so a retry after the response is lost
 *  writes nothing new -- the server returns the match already written under it. */
export interface MatchCreate {
  id?: string
  company_id: string
  cliente: Cliente
  lettera: Lettera
}

/** What saving a match would do, in sentences, with nothing written (REB-476):
 *  «Controlla e invia» shows `riepilogo` and `cosa_succede` as they come. */
export interface MatchCheck {
  riepilogo: string[]
  cosa_succede: string
  quadro_necessario: boolean
  dati_fiscali_mancanti: boolean
}

export interface MatchPrefill {
  fiscale: Fiscal | null
  cliente: ClienteDraft
  lettera: LetteraDraft
  quadro_attivo: ContractDocument | null
  quadro_necessario: boolean
  lettera_in_attesa: boolean
}

/** Every value in `params` that is not `undefined` or `""`, as a query string: the two
 *  list endpoints below send exactly the filters an admin actually set, rather than
 *  the fixed `limit=500` that fetched everything in one page before REB-285/286 gave
 *  both a cursor. */
function filterQuery(params: object): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params) as [string, string | number | boolean | undefined][]) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  return search.toString()
}

/** `GET /api/hub/tokens`'s shape since REB-313: the same search-and-cursor page beside
 *  the newest-first order this list already had. */
export interface AdminTokenList {
  items: AdminToken[]
  next_cursor: string | null
}

/** A page of one of the four small admin lists REB-313 adds `q` and `cursor` to
 *  (Amministratori, Agenti, Accessi): a term, a cursor from the previous page's
 *  `next_cursor`, and a page size, all optional. */
export interface ListPageParams {
  q?: string
  cursor?: string
  limit?: number
}

function listQuery({ q, cursor, limit }: ListPageParams): string {
  const query = new URLSearchParams()
  if (q) query.set('q', q)
  if (cursor) query.set('cursor', cursor)
  if (limit !== undefined) query.set('limit', String(limit))
  const qs = query.toString()
  return qs ? `?${qs}` : ''
}

export const admin = {
  freelancer: (id: string) => request<Freelancer>(`/api/hub/freelancers/${id}`),
  cvUrl: (id: string) => `/api/hub/freelancers/${id}/cv`,
  moveFreelancer: (id: string, stato: string, note: string | null) =>
    request<Freelancer>(`/api/hub/freelancers/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stato, note }),
    }),
  /** Sets or clears any field beyond `stato`/`note` (REB-347/355): a key present and
   *  `null` clears it, a key left out of `data` leaves it alone. */
  overrideFreelancer: (id: string, data: FreelancerOverride) =>
    request<Freelancer>(`/api/hub/freelancers/${id}/override`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  /** Drops the stored CV; the file itself never leaves the audit trail (REB-347/355). */
  clearFreelancerCv: (id: string) => request<Freelancer>(`/api/hub/freelancers/${id}/cv`, { method: 'DELETE' }),
  /** Soft-deletes the card: it drops off `GET /freelancers` and `GET /talent`, and
   *  `restoreFreelancer` reverses it. Never a hard delete (REB-347/355). */
  deleteFreelancer: (id: string) => request<Freelancer>(`/api/hub/freelancers/${id}`, { method: 'DELETE' }),
  restoreFreelancer: (id: string) =>
    request<Freelancer>(`/api/hub/freelancers/${id}/restore`, { method: 'POST' }),
  companies: (filters: CompaniesFilters & { cursor?: string; limit?: number } = {}) => {
    const qs = filterQuery(filters)
    return request<CompanyList>(`/api/hub/companies${qs ? `?${qs}` : ''}`)
  },
  company: (id: string) => request<Company>(`/api/hub/companies/${id}`),
  moveCompany: (id: string, stato: string, note: string | null) =>
    request<Company>(`/api/hub/companies/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stato, note }),
    }),
  /** Sets or clears any field beyond `stato`/`note` (REB-347/355), same contract as
   *  `overrideFreelancer`. */
  overrideCompany: (id: string, data: CompanyOverride) =>
    request<Company>(`/api/hub/companies/${id}/override`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  /** Soft-deletes the request: it drops off `GET /companies`, and `restoreCompany`
   *  reverses it. Never a hard delete (REB-347/355). */
  deleteCompany: (id: string) => request<Company>(`/api/hub/companies/${id}`, { method: 'DELETE' }),
  restoreCompany: (id: string) => request<Company>(`/api/hub/companies/${id}/restore`, { method: 'POST' }),
  /** Every card and every bare sign-up as one list (REB-282/283), `stato` `lead` for
   *  the bare ones alone -- the read model «Talenti» replaced «Developer e CTO» and
   *  «Iscrizioni» with. `filters` beside `stato` and `cursor` are REB-285's search and
   *  its per-field narrowing, REB-286's own filter row sends straight through; `limit`
   *  is the one override `AdminTalentoLead` needs to see every lead at once rather
   *  than the server's own default page. */
  talent: (filters: TalentiFilters & { cursor?: string; limit?: number } = {}) => {
    const qs = filterQuery(filters)
    return request<TalentoList>(`/api/hub/talent${qs ? `?${qs}` : ''}`)
  },
  /** Drafts a card from a bare sign-up in place (ORB-155): the same `draft_from_signup`
   *  the MCP tool `create_freelancer_from_signup` calls, here behind the admin's
   *  cookie. 201 with the new (incomplete) card, or a 422 naming `email` when the
   *  person has already filled their own. */
  draftFromSignup: (signupId: string, data: FreelancerDraft) =>
    request<Freelancer>(`/api/hub/signups/${signupId}/card`, json(data)),
  /** PigroCRM's spaces, read by the hub's API with the token it holds: the browser
   *  never talks to the CRM (ORB-142). A 503 carries the sentence the page shows.
   *  Newest first, `q` matched against the slug or the owner's address, paged with a
   *  cursor (REB-313): the registry itself takes no query parameters, but the hub's
   *  own route filters and slices what it already fetched before answering. */
  pigroSpaces: (params: ListPageParams = {}) =>
    request<{ totale: number; items: PigroSpace[]; next_cursor: string | null }>(
      `/api/hub/pigro/instances${listQuery(params)}`,
    ),
  /** How the guide is doing: downloads, the members behind them, the latest (ORB-156). */
  guideStats: () => request<GuideStats>('/api/hub/perks/guide'),
  /** Who comes back in: logins, the members behind them, the last week -- unchanged
   *  aggregate counters. `recenti` is searched and cursor-paginated since REB-313,
   *  no longer a hardcoded top 20 (ORB-158). */
  loginStats: (params: ListPageParams = {}) =>
    request<LoginStats>(`/api/hub/logins${listQuery(params)}`),
  /** Who reads this area (ORB-123): oldest first with no `q`, so the page still reads
   *  as a history, best-match first once `q` narrows it (REB-313). */
  admins: (params: ListPageParams = {}) =>
    request<AdminList>(`/api/hub/admins${listQuery(params)}`),
  /** Promotes whatever `users` row already answers to this address, or creates a bare
   *  one from `nome`/`cognome` when none exists yet (REB-279, no password anywhere). */
  promote: (data: PromoteRequest) => request<Admin>('/api/hub/admins/promote', json(data)),
  /** Sets `role = 'member'`, fully reversible since nothing is deleted. */
  demote: (id: string) => request<Admin>(`/api/hub/admins/${id}/demote`, { method: 'POST' }),
  /** The admin's own tokens for agents (REB-213): newest first with no `q`, revoked
   *  ones included either way; best-match first by name once `q` narrows it, since
   *  REB-313. */
  tokens: (params: ListPageParams = {}) =>
    request<AdminTokenList>(`/api/hub/tokens${listQuery(params)}`),
  createToken: (nome: string) => request<CreatedToken>('/api/hub/tokens', json({ nome })),
  revokeToken: (id: string) => request<void>(`/api/hub/tokens/${id}`, { method: 'DELETE' }),
  comments: (kind: CommentKind, id: string) =>
    request<Comment[]>(`/api/hub/${kind}/${id}/comments`),
  /** The author is the session's, so the body is the text alone. */
  addComment: (kind: CommentKind, id: string, testo: string) =>
    request<Comment>(`/api/hub/${kind}/${id}/comments`, json({ testo })),
  /** Who overrode, cleared, deleted, restored or reverted this card or request, and
   *  when (REB-355): the same trail shape for either `kind`, newest first. */
  auditTrail: (kind: CommentKind, id: string) => request<AdminAction[]>(`/api/hub/${kind}/${id}/audit`),
  /** Puts a field back to the value a past `overridden` entry's own `before` names,
   *  itself recorded as a fresh override (REB-355): the response is the plain card or
   *  request, which the caller already reads through its own detail query. */
  revertAction: (kind: CommentKind, id: string, actionId: string) =>
    request<unknown>(`/api/hub/${kind}/${id}/audit/${actionId}/revert`, { method: 'POST' }),
  /** «Match» (REB-413): every match in the admin area, newest first. */
  matches: (filters: MatchesFilters & { limit?: number; offset?: number } = {}) => {
    const qs = filterQuery(filters)
    return request<MatchList>(`/api/hub/matches${qs ? `?${qs}` : ''}`)
  },
  /** A freelancer's matches and contracts (REB-387). */
  contracts: (freelancerId: string) =>
    request<FreelancerContracts>(`/api/hub/freelancers/${freelancerId}/matches`),
  fiscal: (freelancerId: string) => request<Fiscal | null>(`/api/hub/freelancers/${freelancerId}/fiscal`),
  saveFiscal: (freelancerId: string, data: FiscalData) =>
    request<Fiscal>(`/api/hub/freelancers/${freelancerId}/fiscal`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  matchPrefill: (freelancerId: string, companyId: string) =>
    request<MatchPrefill>(
      `/api/hub/freelancers/${freelancerId}/matches/prefill?company_id=${encodeURIComponent(companyId)}`,
    ),
  /** «Controlla e invia»'s sentences (REB-476): a 422 names the field as `createMatch`
   *  would, and nothing is written. */
  matchCheck: (freelancerId: string, payload: MatchCreate) =>
    request<MatchCheck>(`/api/hub/freelancers/${freelancerId}/matches/check`, json(payload)),
  /** «Controlla e invia»'s previews: a PDF typeset now and saved nowhere. */
  matchPreview: (freelancerId: string, payload: MatchCreate, documento: 'lettera' | 'quadro') =>
    requestBlob(`/api/hub/freelancers/${freelancerId}/matches/preview?documento=${documento}`, json(payload)),
  /** «Salva senza inviare»: the draft match with its numbered letter. */
  createMatch: (freelancerId: string, payload: MatchCreate) =>
    request<Match>(`/api/hub/freelancers/${freelancerId}/matches`, json(payload)),
  cancelMatch: (matchId: string) => request<Match>(`/api/hub/matches/${matchId}/cancel`, { method: 'POST' }),
  closeMatch: (matchId: string) => request<Match>(`/api/hub/matches/${matchId}/close`, { method: 'POST' }),
  /** «Invia per la firma» (REB-390): the document that can leave now goes to Documenso. */
  sendMatch: (matchId: string) => request<SendReport>(`/api/hub/matches/${matchId}/send`, { method: 'POST' }),
  /** «Aggiorna stato» (REB-407): what Documenso says, applied as the webhook would. */
  refreshDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/refresh`, { method: 'POST' }),
  /** «Reinvia email»: the signing mail again, for a document still waiting. */
  resendDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/resend`, { method: 'POST' }),
  /** «Annulla» on a framework agreement not signed yet. */
  cancelDocument: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/cancel`, { method: 'POST' }),
  /** «Registra disdetta» on an active framework agreement. */
  recordNotice: (documentId: string) =>
    request<ContractDocument>(`/api/hub/contract-documents/${documentId}/notice`, { method: 'POST' }),
  /** A plain href, like `cvUrl`: the route answers an attachment behind the cookie. */
  contractPdfUrl: (documentId: string, firmato = false) =>
    `/api/hub/contract-documents/${documentId}/pdf${firmato ? '?firmato=true' : ''}`,
}

// ---- whoever is signed in --------------------------------------------------------------

/** Whoever `orbiters_user` resolves to, member or admin (REB-278/279, replacing
 *  `MemberProfile`): a `users` row is not necessarily an applicant with a card any
 *  more, so `ha_scheda` says whether one exists, and the seven card fields answer
 *  blank -- `null`, `false`, `[]` -- when it does not, the shape a signed-in admin
 *  with no card gets. `ha_azienda` and the eight request fields mirror `ha_scheda`'s
 *  own shape for a company contact's most recent request (REB-314; REB-380 adds the
 *  last four, `azienda_`-prefixed since `Company` and `Freelancer` both have a
 *  `remoto` and this shape flattens both onto one row): a person can carry both,
 *  one, or neither. `telefono` (REB-380) is a top-level identity field, never blank
 *  because of `ha_scheda`/`ha_azienda`. */
export interface Me {
  id: string
  nome: string
  cognome: string
  email: string
  linkedin_url: string | null
  telefono: string | null
  role: Role
  created_at: string
  updated_at: string
  ha_scheda: boolean
  cv_filename: string | null
  cv_size: number | null
  tariffa_giornaliera: string | null
  posizione: string | null
  remoto: Remoto | null
  links: string[]
  ha_azienda: boolean
  progetto: string | null
  periodo_da: string | null
  durata: string | null
  budget_giornaliero: string | null
  azienda_remoto: Remoto | null
  azienda_giorni_presenza: number | null
  azienda_numero_risorse: number | null
  azienda_figura_richiesta: string | null
  /** CV, rate, position and remote preference all present. Always `false` without a
   *  card (`ha_scheda`). */
  completa: boolean
}

/** The seven answers a member may change. The email is not among them. */
export interface MemberUpdate {
  nome: string
  cognome: string
  linkedin_url: string | null
  tariffa_giornaliera: string
  posizione: string
  remoto: Remoto
  links: string[]
}

/** The eight answers a company contact may change about their most recent request
 *  (REB-314; REB-380 adds the last four): never `stato`, `note`, `telefono` or the
 *  company's own identity. */
export interface CompanyUpdate {
  progetto: string
  periodo_da: string
  durata: string
  budget_giornaliero: string
  remoto: Remoto
  giorni_presenza: number | null
  numero_risorse: number
  figura_richiesta: string
}

export const member = {
  /** 202 whether the address is known or not; the page says one thing in both cases.
   *  `utm` is the campaign the login page was opened from (REB-426), left out when empty. */
  requestLink: (email: string, utm: Utm = {}) =>
    request<{ ok: true }>(
      '/api/hub/auth/link',
      json(Object.keys(utm).length ? { email, utm } : { email }),
    ),
  enter: (token: string) => request<Me>('/api/hub/auth/enter', json({ token })),
  me: () => request<Me>('/api/hub/me'),
  update: (data: MemberUpdate) =>
    request<Me>('/api/hub/me', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  updateCompany: (data: CompanyUpdate) =>
    request<Me>('/api/hub/me/company', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    }),
  /** A brand-new request (REB-381), not an edit: the same eight-field shape as
   *  `updateCompany`, since `CompanyFields` is what both accept. */
  createCompanyRequest: (data: CompanyUpdate) => request<Me>('/api/hub/me/company', json(data)),
  replaceCv: (file: File) => {
    const form = new FormData()
    form.set('cv', file, file.name)
    return request<Me>('/api/hub/me/cv', { method: 'PUT', body: form })
  },
  cvUrl: '/api/hub/me/cv',
  /** The guide, the community's second perk. A plain href rather than a fetch: the
   *  route answers with an attachment, and a session cookie travels with a navigation
   *  the same way it travels with a request. */
  guideUrl: '/api/hub/me/guide',
  /** «Contratti» (REB-392): the caller's own, from the session. */
  contracts: () => request<MemberContracts>('/api/hub/me/contracts'),
  /** A signed copy, a plain href like `cvUrl`: the route answers an attachment. */
  contractPdfUrl: (documentId: string) => `/api/hub/me/contracts/${documentId}/pdf`,
  logout: () => request<void>('/api/hub/me/logout', { method: 'POST' }),
}
