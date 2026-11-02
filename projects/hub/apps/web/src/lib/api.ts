/**
 * The hub's HTTP client: `openapi-fetch` over the types generated from the API's own
 * OpenAPI document (`api-types.ts`, `pnpm generate:api`), same origin, cookies included.
 *
 * Every failure becomes an `ApiError` carrying the status and, for a 422, the field
 * names FastAPI put in `detail[].loc`, which is what a wizard needs to point at the
 * right question rather than blame the whole form.
 */

import createClient from 'openapi-fetch'
import type { components, paths } from './api-types'
import { linkedinProfile } from './linkedin'
import type { Utm } from './utm'

type Schemas = components['schemas']

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

/** The `detail` of an error body, whatever shape the body came in. */
function detailOf(body: unknown): unknown {
  return typeof body === 'object' && body !== null && 'detail' in body ? body.detail : null
}

function failWith(status: number, detail: unknown): never {
  if (Array.isArray(detail)) {
    const items = detail as ValidationItem[]
    const fields = items.map((item) => String(item.loc?.at(-1) ?? '')).filter(Boolean)
    const message = items.map((item) => item.msg).filter(Boolean).join(' · ') || 'Dati non validi.'
    throw new ApiError(status, message, fields)
  }
  if (status === 429) {
    throw new ApiError(429, 'Troppe richieste da qui. Riprova tra un minuto.')
  }
  throw new ApiError(
    status,
    typeof detail === 'string' ? detail : `Qualcosa è andato storto (${status}).`,
  )
}

async function fail(response: Response): Promise<never> {
  let body: unknown
  try {
    body = await response.json()
  } catch {
    body = null
  }
  return failWith(response.status, detailOf(body))
}

/** The page's own origin: `openapi-fetch` builds a `Request`, which wants an absolute URL. */
const client = createClient<paths>({
  baseUrl: globalThis.location.origin,
  credentials: 'same-origin',
  // Every request goes out through the global `fetch` as a path and an init, the way the
  // rest of the app (and every page test) reads it, not as the `Request` object
  // `openapi-fetch` would hand over.
  fetch: async (request) => {
    const { pathname, search } = new URL(request.url)
    return fetch(pathname + search, {
      method: request.method,
      headers: request.headers,
      credentials: request.credentials,
      body: request.body === null ? undefined : await request.text(),
    })
  },
})

/** The body a call through `client` answers on success: `openapi-fetch` types a result
 *  as either `{ data }` or `{ error }`, never both. */
type Success<R> = R extends { data: infer D; error?: never } ? D : never

/** `T` with some fields narrowed. The API declares closed vocabularies (a state, a work
 *  mode) as plain `str`, defaulted lists as optional and a few objects as free-form
 *  dicts; the pages read the narrower shape, so each type below that uses this says
 *  which fields it narrows. */
type Narrow<T, N> = Omit<T, keyof N> & N

/** Names the narrower type a response is read as, for `unwrap`: `reading<Match>()`. */
const reading = <T>(): { type?: T } => ({})

/** The success body of a call, or the `ApiError` its failure became. `reading` narrows
 *  it to a type the pages use (a `Narrow`), which must stay assignable to what the
 *  schema says: a field the API renamed or retyped fails to compile there. */
async function unwrap<R extends { data?: unknown; error?: unknown; response: Response }, T extends Success<R> = Success<R>>(
  call: Promise<R>,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- it only carries `T`
  _reading?: { type?: T },
): Promise<T> {
  const { data, error, response } = await call
  if (!response.ok || error !== undefined) return failWith(response.status, detailOf(error))
  // No error on an ok response: the success branch of the union, which a destructured
  // generic cannot show TypeScript.
  return data as T
}

/** A multipart upload, which the typed client has no way to express. */
async function sendForm<D>(method: 'POST' | 'PUT', path: string, form: FormData): Promise<D> {
  const response = await fetch(path, { credentials: 'same-origin', method, body: form })
  if (!response.ok) await fail(response)
  return response.json()
}

/** The name the API gives a download: `filename*=UTF-8''…` first, then `filename="…"`.
 *  Never a path or a control character, which a decoded `%2F` or `%0A` would otherwise
 *  hand the browser: they are dropped, and a name left empty is `fallback`. */
function dispositionFilename(header: string | null, fallback: string): string {
  if (!header) return fallback
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (star?.[1]) {
    try {
      return safeFilename(decodeURIComponent(star[1].trim())) || fallback
    } catch {
      // A malformed escape: fall through to the plain name.
    }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header)
  return safeFilename(plain?.[1] ?? '') || fallback
}

function safeFilename(name: string): string {
  return [...name]
    .filter((c) => c !== '/' && c !== '\\' && c.charCodeAt(0) >= 0x20 && c.charCodeAt(0) !== 0x7f)
    .join('')
    .trim()
}

/** A form value sent as the form holds it. The body type wants the narrower one (a work
 *  mode, a number), the wizards validate before they send, and the API names a bad field
 *  in its own 422, which is the message the page shows. */
function asSent<T>(value: unknown): T {
  return value as T
}

/** `params` without what is `undefined` or `""`: the list endpoints send exactly the
 *  filters an admin actually set. */
function filled<T extends object>(params: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== ''),
  ) as Partial<T>
}

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
  rif: string | null = null,
): Promise<Schemas['Ack']> {
  const form = new FormData()
  // The browser's PostHog id, when the page is measured: the API sends the completion
  // event itself (REB-215), and this is what puts it on the same person as the steps.
  if (distinctId) form.set('distinct_id', distinctId)
  if (rif) form.set('rif', rif)
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
  return sendForm('POST', '/api/hub/freelancers', form)
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

/** What «Nuova richiesta» collects: the wizard's answers, and the CRM deal when it has one. */
export interface AdminCompanyRequest extends CompanyRequest {
  deal_id?: string
}

export function requestPeople(
  data: CompanyRequest,
  utm: Utm,
  distinctId: string | null = null,
  rif: string | null = null,
): Promise<Schemas['Ack']> {
  return unwrap(
    client.POST('/api/hub/companies', {
      body: {
        ...data,
        remoto: asSent<Remoto>(data.remoto),
        // `''` means "not ibrido, never asked": the API's own `giorni_presenza` is
        // `int | None`, which a blank string does not coerce to.
        giorni_presenza: data.giorni_presenza ? Number(data.giorni_presenza) : null,
        numero_risorse: asSent<number>(data.numero_risorse),
        utm: Object.keys(utm).length ? utm : null,
        ...(distinctId ? { distinct_id: distinctId } : {}),
        ...(rif ? { rif } : {}),
      },
    }),
  )
}

// ---- the team builder, public (P-REB-43, spec § 3.2, § 3.3) --------------------------------

export type Band = Schemas['Band']
export type Scheda = Schemas['CardRead']

/** One person of a proposed team. The public read has no `freelancer_id` (a visitor
 *  cannot follow a talent across proposals), nor a name. */
export type TeamMember = Narrow<Schemas['TeamMemberRead'], { modalita: Remoto | null }>

/** The team's bands: the sum of its people's, per day and per month at `giorni_mese`
 *  (22) days; both `null` when somebody has no band, or nobody is proposed. */
export type TeamEconomia = {
  giorno: Band | null
  mese: Band | null
  giorni_mese: number
}

/** `luogo` is what the engine read in the visitor's own description about place. */
export type TeamProposal = Narrow<
  Schemas['TeamProposalRead'],
  { luogo: { locale: boolean; dove: string | null }; economia: TeamEconomia; team: TeamMember[] }
>

export type TeamProposalCreate = Schemas['TeamProposalCreate']
export type TeamRequestCreate = Schemas['TeamRequestCreate']
export type TeamAvailabilityAnswer = Schemas['TeamAvailabilityAnswer']

/** The answer recorded, or `invalid` for a link unknown, spent or expired alike. */
export type TeamAvailabilityOutcome = Schemas['TeamAvailabilityOutcome']['esito']

export const team = {
  /** 503 when the builder is off or too busy, 502 when Claude does not answer, 422 on
   *  the description or a `previous_id` that is not a live public proposal: each with
   *  the API's own sentence. */
  propose: (body: TeamProposalCreate) =>
    unwrap(client.POST('/api/hub/team/proposals', { body }), reading<TeamProposal>()),
  /** The public read of a proposal by its id (REB-675, `/hub/team?proposta=`): a 404
   *  for one older than a day, a cloud's, an attempt or an unknown id alike. */
  get: (id: string) =>
    unwrap(
      client.GET('/api/hub/team/proposals/{proposal_id}', { params: { path: { proposal_id: id } } }),
      reading<TeamProposal>(),
    ),
  /** 201; 409 when the proposal is already requested, 422 when it is gone. */
  request: (body: TeamRequestCreate) => unwrap(client.POST('/api/hub/team/requests', { body })),
  /** A talent's «Conferma»: 200 with the outcome, whatever the token; 429 past the
   *  wizards' speed bump. */
  answer: (body: TeamAvailabilityAnswer) =>
    unwrap(client.POST('/api/hub/team/availability', { body })),
}

// ---- the team builder, in the admin area (P-REB-43, spec § 2.1, § 3.5, § 5.1) --------------

export type TeamRequestStato = 'nuova' | 'contattata' | 'chiusa'
export type TeamRequestOrigine = 'pubblico' | 'cloud'
/** A talent's answer to the availability mail (D1): `null` until they give one. */
export type TalentAnswer = 'si' | 'no'

/** One person of a proposal as the admin reads it: the public shape, plus who it is. */
export type AdminTeamMember = Narrow<TeamMember, { freelancer_id: string }>
export type AdminTeamProposal = Narrow<TeamProposal, { team: AdminTeamMember[] }>
export type TeamRequestTalent = Narrow<Schemas['TeamRequestTalentRead'], { risposta: TalentAnswer | null }>
export type TeamRequest = Narrow<
  Schemas['TeamRequestRead'],
  {
    proposal: AdminTeamProposal | null
    origine: TeamRequestOrigine
    stato: TeamRequestStato
    talenti: TeamRequestTalent[]
  }
>
export type TeamRequestListItem = Narrow<
  Schemas['TeamRequestListItem'],
  { origine: TeamRequestOrigine; stato: TeamRequestStato }
>
export type TeamProposalListMember = Schemas['TeamProposalListMember']
/** The refusal an attempt row keeps: Claude did not answer, or every slot or the day's
 *  proposals were spent. */
export type TeamProposalErrore = 'llm_unavailable' | 'team_builder_busy'
export type TeamProposalListItem = Narrow<
  Schemas['TeamProposalListItem'],
  { origine: 'pubblico' | 'cloud' | 'admin'; errore: TeamProposalErrore | null }
>

/** What the list takes beside `cursor`/`limit`, and what `/admin/team` carries in its
 *  URL: a state of `TeamRequestStato`, which the API checks. */
export interface TeamRequestsFilters {
  stato?: string
}

/** What «Proposte» takes beside `cursor`/`limit`, and what `/admin/team/proposte`
 *  carries in its URL: `esito` is `ok` or `errore`, checked by the API. The API and
 *  the MCP tool filter by `origine` too; the page has no control for it yet, so the
 *  URL does not carry one. */
export interface TeamProposalsFilters {
  esito?: string
}

export type FreelancerCard = Narrow<
  Schemas['FreelancerCardAdminRead'],
  { modalita: Remoto | null; persona?: CardPersona | null; differenze?: PersonaDifference[] }
>
export type CardPersona = Narrow<Schemas['CardPersonaRead'], { altri_link: string[]; aziende: string[] }>
export type PersonaDifference = Schemas['PersonaDifference']
export type PersonaDifferenceField = PersonaDifference['campo']
export type CardConflict = Schemas['ConflictRead']
export type CardProvenance = Schemas['CardProvenance']

// ---- the admin area -------------------------------------------------------------------

export type Role = 'member' | 'admin'

export type Admin = Schemas['AdminRead']
export type Freelancer = Narrow<
  Schemas['FreelancerDetail'],
  {
    /** The detail carries these; the list and the mutations' answers leave them out. */
    iscrizione_utm: Schemas['SignupUtm'] | null
    ultimi_accessi: Schemas['LoginRead'][]
    ultimi_download_guida: Schemas['GuideDownloadRead'][]
    pigro_slug: string | null
    remoto: Remoto | null
    stato: 'nuovo' | 'contattato' | 'attivo' | 'scartato'
    compilata_da: 'persona' | 'admin'
    provenienza: 'form' | 'landing'
    note: Note[]
    origine: string | null
    utm_source: string | null
    utm_campaign: string | null
    ultimo_accesso: string | null
    deleted_at: string | null
    vetted_at: string | null
  }
>
export type TalentCloudGrant = Narrow<
  Schemas['TalentCloudGrantRead'],
  { revoked_by: string | null; revoked_by_nome: string | null; revoked_at: string | null }
>
export type Company = Narrow<
  Schemas['CompanyRead'],
  {
    remoto: Remoto
    stato: 'nuovo' | 'contattato' | 'in_corso' | 'chiuso'
    crm_stato: PigroStato | null
    crm_situazione: string | null
    crm_deal_url: string | null
    crm_attempted_at: string | null
    origine: string | null
    utm_source: string | null
    deleted_at: string | null
    note: Note[]
    talent_cloud_grant: TalentCloudGrant | null
  }
>

/** Where a note came from (REB-856). `null` is a note that predates the tracking. */
export type NoteSource = 'web' | 'mcp' | 'persona' | 'sistema'

/** The door a trail entry came through: a note's (`NoteSource`) or, for a regeneration of
 *  the cards, `cli` too (REB-861). */
export type ActionSource = NoteSource | 'cli'

export type Note = Narrow<Schemas['NoteRead'], { fonte: NoteSource | null }>

/** The two rows a thread can hang on, as the API paths name them. */
export type ThreadKind = 'freelancers' | 'companies'

export type AdminActionKind =
  | 'overridden'
  | 'cleared'
  | 'deleted'
  | 'restored'
  /** A framework agreement's own actions (REB-407), recorded on entity `freelancer`. */
  | 'mail_resent'
  | 'document_cancelled'
  | 'notice_recorded'
  /** «Segna come verificato» / «Togli la verifica» (REB-518): `payload.vetted` says which. */
  | 'vetted'
  /** «Riprova su Pigro» (REB-498), recorded on entity `match`. */
  | 'pigro_link'
  | 'crm_link'
  | 'company_created'
  /** Two cards of one person merged (REB-800): `merged` is on the absorbed card,
   *  `absorbed` on the one that stayed, `unmerged` on both once the shell put it back. */
  | 'merged'
  | 'absorbed'
  | 'unmerged'
  /** The notes' trail (REB-856), on the freelancer or the company the note is about,
   *  and a change of status: `payload.fonte` is the door (`web` or `mcp`). */
  | 'note_added'
  | 'note_edited'
  | 'note_deleted'
  | 'status_changed'
  | 'card_regenerated'
  /** «Scarica CV anonimo» (REB-862): `payload.riusato` is true when the PDF was already made. */
  | 'anon_cv_downloaded'

/** What an entry's `payload` carries, by `kind`: the API sends a free-form object. */
export type AdminActionPayload = {
  changed?: string[]
  /** An `overridden`/`cleared` entry's diff is an object per field; a `note_edited`
   *  (the text) and a `status_changed` (the state) carry plain strings. */
  before?: Record<string, unknown> | string
  after?: Record<string, unknown> | string
  vetted?: boolean
  /** The door the action came through: `web` or `mcp` (REB-856), `cli` for a
   *  regeneration started from the command line (REB-861). */
  fonte?: ActionSource
  /** A `note_added` / `note_deleted` entry's text. */
  testo?: string
  nota_id?: string
  /** A `pigro_link` entry's: the state the link reached, and the CRM's sentence. */
  esito?: PigroStato | 'scritta' | 'non_scritta'
  errore?: string | null
  /** A `merged` / `absorbed` entry's (REB-800). `moved` is a list of row ids per
   *  relation on `merged` (what an undo reads) and a count per relation on `absorbed`. */
  into_freelancer_id?: string
  from_freelancer_id?: string
  moved?: Record<string, number | string[]>
  cv?: MergeResult['cv']
  scheda_da_rigenerare?: boolean
  /** A `card_regenerated` entry's (REB-861): a regeneration of the card, one of a bulk
   *  start (`bulk`, `start_action_id`) or a single «Rigenera scheda», the prompt version
   *  and model that wrote it, the reason it was not written and what its calls cost. */
  bulk?: boolean
  start_action_id?: string | null
  prompt_version?: number
  model?: string | null
  reason?: string
  cost_eur?: string
  unpriced?: number
  /** An `anon_cv_downloaded` entry's: the stored CV was served, no model call. */
  riusato?: boolean
}

export type AdminAction = Narrow<Schemas['AdminActionRead'], { kind: AdminActionKind; payload: AdminActionPayload }>

export type FreelancerOverride = Schemas['FreelancerOverride']
export type CompanyOverride = Schemas['CompanyOverride']
export type LoginStats = Schemas['LoginStats']
/** `freelancer_id` is what the page links each download by; the API's `GuideDownloadRead`
 *  has no such field yet (HubBackend adds it, `null` for a company contact), so until the
 *  types are regenerated this is the page's own reading. */
export type GuideStats = Narrow<
  Schemas['GuideStats'],
  { recenti: (Schemas['GuideDownloadRead'] & { freelancer_id: string })[] }
>
export type Talento = Narrow<
  Schemas['TalentoRead'],
  {
    nome: string | null
    cognome: string | null
    linkedin_url: string | null
    origine: 'form' | 'wizard' | 'admin'
    tariffa_giornaliera: string | null
    remoto: string | null
    vetted_at: string | null
    utm_source: string | null
    possibile_duplicato_di: string[]
  }
>
export type TalentoList = Narrow<Schemas['TalentoList'], { items: Talento[] }>

/** Why two cards look like one person (REB-800): the same LinkedIn profile, the same name
 *  or the same telephone number. Only the first is a reason to merge. */
export type DuplicateSignal = 'linkedin' | 'nome' | 'telefono'

export type DuplicatePerson = Schemas['PersonRead']
export type DuplicatePair = Narrow<Schemas['DuplicatePairRead'], { segnali: DuplicateSignal[] }>
export type DuplicateList = Narrow<
  Schemas['DuplicateListRead'],
  { unibili: DuplicatePair[]; da_rivedere: DuplicatePair[] }
>
export type MergeResult = Narrow<Schemas['MergeRead'], { cv: 'invariato' | 'assorbita' | 'nessuno' }>
export type FreelancerDraft = Schemas['FreelancerDraft']
export type AdminToken = Schemas['AdminTokenRead']
export type CreatedToken = Schemas['CreatedToken']

/** What `GET /api/hub/talent` takes beside `limit` (REB-285/286): `q` searches
 *  name/surname/email/`posizione`, trigram-ordered when present; every other field
 *  narrows the merged list of cards and bare sign-ups the same way the state pills
 *  always have. This is also the shape `/admin/talent`'s own `validateSearch` carries in
 *  the URL (`router.tsx`). */
export interface TalentiFilters {
  stato?: string
  q?: string
  posizione?: string
  remoto?: Remoto
  tariffa_min?: string
  tariffa_max?: string
  /** The page of the site the person started from (`home`, `pigrocrm`); `origine`,
   *  the channel a row shows (`form`, `wizard`, `admin`), has no control on the page. */
  pagina_origine?: string
  utm_source?: string
  has_cv?: boolean
  con_accessi?: boolean
  creato_da?: string
  creato_a?: string
}

/** What `GET /api/hub/companies` takes beside `limit` (REB-285/286): the shape
 *  `/admin/companies`'s own `validateSearch` carries in the URL. */
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

/** REB-387: a match's state, and a contract document's. */
export type MatchStato = 'bozza' | 'in_firma' | 'attivo' | 'concluso' | 'annullato'
export type DocumentStato = 'generato' | 'in_attesa' | 'inviato' | 'firmato' | 'annullato' | 'disdetto'
/** Where an active match's link to its deal on Pigro stands (REB-497): `null` until the
 *  match turns `attivo`. */
export type PigroStato = 'da_collegare' | 'collegato' | 'errore' | 'rifiutato'
/** REB-687: a contract document's family, the letter type a match is made with. */
export type Regime = Schemas['MatchCheck']['regime']
/** What an admin can do next on a document or a match (REB-477): the page maps each to
 *  its button and its API call. */
export type Action = NonNullable<Schemas['ContractDocumentRead']['prossima_azione']>

export type FiscalData = Narrow<Schemas['FiscalData'], { partita_iva: string | null; pec: string | null }>
export type Fiscal = Schemas['FiscalRead']
export type ContractDocument = Narrow<
  Schemas['ContractDocumentRead'],
  { kind: 'quadro' | 'lettera'; stato: DocumentStato }
>
export type Match = Narrow<
  Schemas['MatchRead'],
  { stato: MatchStato; pigro_stato: PigroStato | null; lettera: ContractDocument }
>
/** `previsto` while the first letter is not signed (a projection), the reward's own
 *  state after, `gia_maturato` when the referral already paid on another match, and
 *  `da_verificare` for a company whose referente has not logged in yet (REB-658). */
export type MatchReferralStato = 'previsto' | 'gia_maturato' | 'da_verificare' | RewardStato
export type MatchReferral = Narrow<
  Schemas['MatchReferral'],
  { kind: 'freelancer' | 'company'; stato: MatchReferralStato }
>
export type MatchListItem = Narrow<
  Schemas['MatchListItem'],
  {
    stato: MatchStato
    lettera_stato: DocumentStato | null
    pigro_stato: PigroStato | null
    referrals: MatchReferral[]
  }
>

/** What `GET /api/hub/matches` takes beside `limit`/`offset` (REB-413): `stato` one of
 *  `MatchStato`, `q` matching the freelancer's name, surname or email and the
 *  company's name. */
export interface MatchesFilters {
  stato?: string
  q?: string
}

// ---- campaigns (P-REB-41) -------------------------------------------------------------

export type CampaignStato = 'bozza' | 'programmata' | 'in_invio' | 'inviata' | 'annullata'
export type CampaignAzione = Schemas['CampaignDraft']['azione']
export type CampaignMeta = Schemas['CampaignDraft']['bottone_meta']
export type RecipientStato = 'in_coda' | 'inviata' | 'saltata' | 'fallita'

export type Campaign = Narrow<
  Schemas['CampaignRead'],
  {
    fonte: 'stato' | 'filtri' | 'lista'
    bottone_meta: CampaignMeta
    bottone_url: string | null
    azione: CampaignAzione
    stato: CampaignStato
  }
>
export type CampaignCounts = Schemas['CampaignCounts']
export type CampaignListItem = Narrow<Schemas['CampaignListItem'], Pick<Campaign, 'fonte' | 'bottone_meta' | 'bottone_url' | 'azione' | 'stato'>>
export type CampaignRecipient = Narrow<Schemas['RecipientRead'], { stato: RecipientStato }>
export type AudienceRow = Schemas['AudienceRowRead']
export type AudiencePreview = Schemas['AudiencePreview']
export type CampaignTemplate = Narrow<Schemas['TemplateRead'], { bottone_meta: CampaignMeta; azione: CampaignAzione }>
/** The form builds `filtri` and the API validates it by `lista`, so the form's own
 *  reading is the looser one, narrowed back to the schema's where a body is sent. */
export type CampaignDraft = Narrow<Schemas['CampaignDraft'], { filtri?: Record<string, unknown> | null }>

export type MatchReport = Narrow<Schemas['MatchReport'], { pigro_stato: PigroStato | null }>
export type ReportDayInvoice = Schemas['ReportDayInvoice']
export type ReportDay = Schemas['ReportDay']
export type ReportWeek = Schemas['ReportWeek']
export type ReportMonth = Schemas['ReportMonth']
export type ReportInvoice = Schemas['ReportInvoice']
export type SendReport = Narrow<Schemas['SendReport'], { match: Match; inviato: 'quadro' | 'lettera' | null }>
export type MemberContract = Narrow<
  Schemas['MemberContract'],
  { kind: 'quadro' | 'lettera'; stato: DocumentStato }
>
export type MemberContracts = Narrow<
  Schemas['MemberContracts'],
  {
    quadro: MemberContract | null
    quadro_occasionale: MemberContract | null
    quadri_precedenti: MemberContract[]
    lettere: MemberContract[]
  }
>
export type FreelancerContracts = Narrow<
  Schemas['FreelancerContracts'],
  {
    quadro: ContractDocument | null
    quadro_occasionale: ContractDocument | null
    quadri: ContractDocument[]
    matches: Match[]
  }
>
export type MatchPrefill = Narrow<
  Schemas['MatchPrefill'],
  {
    fiscale: Fiscal | null
    cliente: ClienteDraft
    lettera: LetteraDraft
    quadro_attivo: ContractDocument | null
  }
>

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

/** What `LetteraFields` takes, as the wizard sends it: an empty text field is `null` and
 *  prints a blank line, and a missing required one is the API's 422 naming the field, so
 *  this is wider than the schema's own type (see `matchBody`). */
export type Lettera = Record<LetteraTextKey, string | null> & {
  data_inizio: string
  data_fine: string | null
  compenso: string
  giorni_pagamento: number
  fine_mese: boolean
  giorni_preavviso: number | null
}
export type LetteraDraft = Narrow<
  { [K in keyof Lettera]: Lettera[K] | null },
  { modalita: 'a giornata' | 'a corpo' | null }
>
export type Cliente = Schemas['ClienteData']
export type ClienteDraft = { [K in keyof Cliente]: string | null }

/** `id` is optional and client-generated (REB-406): one per wizard run, sent with both
 *  «Salva senza inviare» and «Invia per la firma», so a retry after the response is lost
 *  writes nothing new -- the server returns the match already written under it. */
export interface MatchCreate {
  id?: string
  company_id: string
  /** REB-687: the letter type, `partita_iva` or `occasionale`. */
  regime: Regime
  cliente: Cliente
  lettera: Lettera
  /** «Giorni previsti» (REB-497): beside the letter, never inside it, since `LetteraFields`
   *  refuses a key it does not know and the letter's text does not change with it. */
  giorni_previsti: number | null
}

/** The wizard's body for the schema's: the same fields, the letter's nulls left for the
 *  API to refuse by field name. */
const matchBody = (body: MatchCreate) => body as Schemas['MatchCreate']

export type MatchCheck = Schemas['MatchCheck']

export type CompanyList = Narrow<Schemas['CompanyList'], { items: Company[] }>
export type MatchList = Narrow<Schemas['MatchList'], { items: MatchListItem[] }>
export type TeamRequestList = Narrow<Schemas['TeamRequestList'], { items: TeamRequestListItem[] }>
export type TeamProposalList = Narrow<Schemas['TeamProposalList'], { items: TeamProposalListItem[] }>
export type CampaignList = Narrow<Schemas['CampaignList'], { items: CampaignListItem[] }>
export type CampaignDetail = Narrow<
  Schemas['CampaignDetail'],
  { campagna: Campaign; destinatari: CampaignRecipient[] }
>

/** A page of one of the four small admin lists REB-313 adds `q` and `cursor` to
 *  (Amministratori, Agenti, Accessi): a term, a cursor from the previous page's
 *  `next_cursor`, and a page size, all optional. */
export interface ListPageParams {
  q?: string
  cursor?: string
  limit?: number
}

export const admin = {
  campaigns: () => unwrap(client.GET('/api/hub/campaigns'), reading<CampaignList>()),
  campaignTemplates: () => unwrap(client.GET('/api/hub/campaigns/templates'), reading<CampaignTemplate[]>()),
  campaign: (id: string) =>
    unwrap(client.GET('/api/hub/campaigns/{campaign_id}', { params: { path: { campaign_id: id } } }), reading<CampaignDetail>()),
  createCampaign: (body: CampaignDraft) =>
    unwrap(client.POST('/api/hub/campaigns', { body: body as Schemas['CampaignDraft'] }), reading<Campaign>()),
  updateCampaign: (id: string, body: Partial<CampaignDraft>) =>
    unwrap(
      client.PATCH('/api/hub/campaigns/{campaign_id}', {
        params: { path: { campaign_id: id } },
        body: body as Schemas['CampaignPatch'],
      }),
      reading<Campaign>(),
    ),
  campaignAudience: (id: string) =>
    unwrap(
      client.GET('/api/hub/campaigns/{campaign_id}/audience', { params: { path: { campaign_id: id } } }),
    ),
  testCampaign: (id: string) =>
    unwrap(client.POST('/api/hub/campaigns/{campaign_id}/test', { params: { path: { campaign_id: id } } }), reading<Campaign>()),
  scheduleCampaign: (id: string, body: Schemas['ScheduleRequest']) =>
    unwrap(
      client.POST('/api/hub/campaigns/{campaign_id}/schedule', {
        params: { path: { campaign_id: id } },
        body,
      }),
      reading<Campaign>(),
    ),
  campaignToDraft: (id: string) =>
    unwrap(client.POST('/api/hub/campaigns/{campaign_id}/draft', { params: { path: { campaign_id: id } } }), reading<Campaign>()),
  cancelCampaign: (id: string) =>
    unwrap(client.POST('/api/hub/campaigns/{campaign_id}/cancel', { params: { path: { campaign_id: id } } }), reading<Campaign>()),
  deleteCampaign: (id: string) =>
    unwrap(client.DELETE('/api/hub/campaigns/{campaign_id}', { params: { path: { campaign_id: id } } })),
  followUpCampaign: (id: string) =>
    unwrap(
      client.POST('/api/hub/campaigns/{campaign_id}/follow-up', { params: { path: { campaign_id: id } } }),
      reading<Campaign>(),
    ),
  neverWrite: (email: string) => unwrap(client.POST('/api/hub/campaigns/never-write', { body: { email } })),
  freelancer: (id: string) =>
    unwrap(
      client.GET('/api/hub/freelancers/{freelancer_id}', { params: { path: { freelancer_id: id } } }),
      reading<Freelancer>(),
    ),
  cvUrl: (id: string) => `/api/hub/freelancers/${encodeURIComponent(id)}/cv`,
  moveFreelancer: (id: string, stato: string) =>
    unwrap(
      client.PATCH('/api/hub/freelancers/{freelancer_id}', {
        params: { path: { freelancer_id: id } },
        body: { stato },
      }),
      reading<Freelancer>(),
    ),
  /** Sets or clears any field beyond `stato` (REB-347/355): a key present and
   *  `null` clears it, a key left out of `data` leaves it alone. */
  overrideFreelancer: (id: string, body: FreelancerOverride) =>
    unwrap(
      client.PATCH('/api/hub/freelancers/{freelancer_id}/override', {
        params: { path: { freelancer_id: id } },
        body,
      }),
      reading<Freelancer>(),
    ),
  /** Drops the stored CV; the file itself never leaves the audit trail (REB-347/355). */
  clearFreelancerCv: (id: string) =>
    unwrap(
      client.DELETE('/api/hub/freelancers/{freelancer_id}/cv', { params: { path: { freelancer_id: id } } }),
      reading<Freelancer>(),
    ),
  /** Soft-deletes the card: it drops off `GET /freelancers` and `GET /talent`, and
   *  `restoreFreelancer` reverses it. Never a hard delete (REB-347/355). */
  deleteFreelancer: (id: string) =>
    unwrap(
      client.DELETE('/api/hub/freelancers/{freelancer_id}', { params: { path: { freelancer_id: id } } }),
      reading<Freelancer>(),
    ),
  restoreFreelancer: (id: string) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/restore', {
        params: { path: { freelancer_id: id } },
      }),
      reading<Freelancer>(),
    ),
  /** «Segna come verificato» (`true`) and «Togli la verifica» (`false`), REB-518. */
  setVetted: (id: string, vetted: boolean) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/vetted', {
        params: { path: { freelancer_id: id } },
        body: { vetted },
      }),
      reading<Freelancer>(),
    ),
  /** The anonymous card Claude wrote from the CV (REB-510), with the last failure. */
  freelancerCard: (id: string) =>
    unwrap(
      client.GET('/api/hub/freelancers/{freelancer_id}/card', { params: { path: { freelancer_id: id } } }),
      reading<FreelancerCard>(),
    ),
  /** «Rigenera scheda»: the card written again from the current CV, now. A failure is
   *  a 200 with `error`; 503 with the API's sentence when the builder is off. */
  regenerateFreelancerCard: (id: string) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/card', { params: { path: { freelancer_id: id } } }),
      reading<FreelancerCard>(),
    ),
  /** «Scarica CV anonimo» (REB-862): the CV rewritten without the person's name, contacts
   *  or links, as a PDF. The first press can take minutes (a model call of up to 150
   *  seconds, and one rewrite at most); the API's sentence comes back as an `ApiError` (no
   *  CV, a leak that survived, the task off). */
  downloadAnonymousCv: async (id: string): Promise<{ blob: Blob; filename: string }> => {
    const { data, error, response } = await client.POST('/api/hub/freelancers/{freelancer_id}/cv-anonimo', {
      params: { path: { freelancer_id: id } },
      parseAs: 'blob',
    })
    if (!response.ok) return failWith(response.status, detailOf(error))
    return {
      // An empty body comes back as no data at all: an empty file, as it always did.
      blob: data ?? new Blob([]),
      filename: dispositionFilename(response.headers.get('Content-Disposition'), 'cv-anonimo.pdf'),
    }
  },
  /** «Richieste team» (REB-512): newest first, a page at a time by cursor. */
  teamRequests: (filters: TeamRequestsFilters & { cursor?: string; limit?: number } = {}) =>
    unwrap(client.GET('/api/hub/team/requests', { params: { query: filled(filters) } }), reading<TeamRequestList>()),
  teamRequest: (id: string) =>
    unwrap(client.GET('/api/hub/team/requests/{request_id}', { params: { path: { request_id: id } } }), reading<TeamRequest>()),
  /** «Proposte» (0028): every «Proponi il team», newest first, a page at a time. */
  teamProposals: (filters: TeamProposalsFilters & { cursor?: string; limit?: number } = {}) =>
    unwrap(client.GET('/api/hub/team/proposals', { params: { query: filled(filters) } }), reading<TeamProposalList>()),
  /** «Segna come contattata», «Chiudi», «Riapri». */
  setTeamRequestStatus: (id: string, stato: TeamRequestStato) =>
    unwrap(
      client.POST('/api/hub/team/requests/{request_id}/status', {
        params: { path: { request_id: id } },
        body: { stato },
      }),
      reading<TeamRequest>(),
    ),
  /** «Salva la nota»: `null` clears it. */
  setTeamRequestNote: (id: string, note: string | null) =>
    unwrap(
      client.PATCH('/api/hub/team/requests/{request_id}/note', {
        params: { path: { request_id: id } },
        body: { note },
      }),
      reading<TeamRequest>(),
    ),
  /** «Salva il riassunto»: what the talents will read. */
  setTeamRequestSummary: (id: string, riassunto: string) =>
    unwrap(
      client.PATCH('/api/hub/team/requests/{request_id}/summary', {
        params: { path: { request_id: id } },
        body: { riassunto },
      }),
      reading<TeamRequest>(),
    ),
  /** «Contatta i talenti», or with `onlySilent` «Rimanda a chi non ha risposto» (D1):
   *  the request with its talents contacted, the mails leaving after the answer; 409
   *  with the API's sentence (a summary that names the company, among others), 503
   *  without a mail key. */
  contactTeamTalents: (id: string, onlySilent: boolean) =>
    unwrap(
      client.POST('/api/hub/team/requests/{request_id}/contact', {
        params: { path: { request_id: id }, query: { only_silent: onlySilent } },
      }),
      reading<TeamRequest>(),
    ),
  companies: (filters: CompaniesFilters & { cursor?: string; limit?: number } = {}) =>
    unwrap(client.GET('/api/hub/companies', { params: { query: filled(filters) } }), reading<CompanyList>()),
  company: (id: string) =>
    unwrap(client.GET('/api/hub/companies/{company_id}', { params: { path: { company_id: id } } }), reading<Company>()),
  /** «Nuova richiesta» (REB-870): opens a request for a company met directly. No mail goes
   *  out. With `deal_id` (a deal the CRM already has) the CRM links it and creates nothing,
   *  and the answer says so; without it the request is filed as a new deal. */
  createCompany: (data: AdminCompanyRequest) =>
    unwrap(
      client.POST('/api/hub/admin/companies', {
        body: {
          ...data,
          remoto: asSent<Remoto>(data.remoto),
          giorni_presenza: data.giorni_presenza ? Number(data.giorni_presenza) : null,
          numero_risorse: asSent<number>(data.numero_risorse),
          deal_id: data.deal_id ?? null,
        },
      }),
      reading<Company>(),
    ),
  moveCompany: (id: string, stato: string) =>
    unwrap(
      client.PATCH('/api/hub/companies/{company_id}', {
        params: { path: { company_id: id } },
        body: { stato },
      }),
      reading<Company>(),
    ),
  /** Sets or clears any field beyond `stato` (REB-347/355), same contract as
   *  `overrideFreelancer`. */
  overrideCompany: (id: string, body: CompanyOverride) =>
    unwrap(
      client.PATCH('/api/hub/companies/{company_id}/override', {
        params: { path: { company_id: id } },
        body,
      }),
      reading<Company>(),
    ),
  /** Soft-deletes the request: it drops off `GET /companies`, and `restoreCompany`
   *  reverses it. Never a hard delete (REB-347/355). */
  deleteCompany: (id: string) =>
    unwrap(client.DELETE('/api/hub/companies/{company_id}', { params: { path: { company_id: id } } }), reading<Company>()),
  /** «Riprova» (REB-868): the request is filed in rebase's CRM now and comes back as it
   *  stands. With `dealId`, «Collega a un altro deal»: the CRM links that deal and creates
   *  nothing; a refusal is a 409 with its sentence. A 503 says this environment has no
   *  link to the CRM. The CRM may take a while to answer: no timeout here cuts it short. */
  linkCompanyToCrm: (id: string, dealId?: string) =>
    unwrap(
      client.POST('/api/hub/companies/{company_id}/crm/link', {
        params: { path: { company_id: id } },
        body: dealId ? { deal_id: dealId } : {},
      }),
      reading<Company>(),
    ),
  restoreCompany: (id: string) =>
    unwrap(
      client.POST('/api/hub/companies/{company_id}/restore', { params: { path: { company_id: id } } }),
      reading<Company>(),
    ),
  /** «Apri il talent cloud» (REB-518): the live grant to the request's referente, new
   *  (201, with a mail to them) or already there (200, no mail). */
  openTalentCloud: (companyId: string) =>
    unwrap(
      client.POST('/api/hub/companies/{company_id}/cloud', { params: { path: { company_id: companyId } } }),
      reading<TalentCloudGrant>(),
    ),
  /** «Revoca il talent cloud»: 409 with the API's sentence when nothing is live. */
  revokeTalentCloud: (companyId: string) =>
    unwrap(
      client.DELETE('/api/hub/companies/{company_id}/cloud', {
        params: { path: { company_id: companyId } },
      }),
      reading<TalentCloudGrant>(),
    ),
  /** Every grant, live and closed, newest first (capped by the API, not paginated). */
  talentCloudGrants: () => unwrap(client.GET('/api/hub/cloud/grants'), reading<TalentCloudGrant[]>()),
  /** Every card and every bare sign-up as one list (REB-282/283), `stato` `lead` for
   *  the bare ones alone -- the read model «Talenti» replaced «Developer e CTO» and
   *  «Iscrizioni» with. `filters` beside `stato` and `cursor` are REB-285's search and
   *  its per-field narrowing, REB-286's own filter row sends straight through; `limit`
   *  is the one override `AdminTalentoLead` needs to see every lead at once rather
   *  than the server's own default page. */
  talent: (filters: TalentiFilters & { cursor?: string; limit?: number } = {}) =>
    unwrap(client.GET('/api/hub/talent', { params: { query: filled(filters) } }), reading<TalentoList>()),
  /** Who is on the list twice (REB-800): the pairs that share a LinkedIn profile
   *  (`unibili`) and the ones that only share a name or a number (`da_rivedere`). */
  duplicates: () => unwrap(client.GET('/api/hub/talent/duplicati'), reading<DuplicateList>()),
  /** «Unisci» (REB-800): the card `absorbedId` into the card `survivorId`. A refusal is a
   *  409 carrying the API's sentence; the merge is undone from the shell only. */
  mergeFreelancer: (survivorId: string, absorbedId: string) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/merge', {
        params: { path: { freelancer_id: survivorId } },
        body: { da: absorbedId },
      }),
      reading<MergeResult>(),
    ),
  /** One bare sign-up as the row the Talenti list shows for it: the lead's own page. */
  signup: (id: string) =>
    unwrap(
      client.GET('/api/hub/signups/{signup_id}', { params: { path: { signup_id: id } } }),
      reading<Talento>(),
    ),
  /** Drafts a card from a bare sign-up in place (ORB-155): the same `draft_from_signup`
   *  the MCP tool `create_freelancer_from_signup` calls, here behind the admin's
   *  cookie. 201 with the new (incomplete) card, or a 422 naming `email` when the
   *  person has already filled their own. */
  draftFromSignup: (signupId: string, body: FreelancerDraft) =>
    unwrap(
      client.POST('/api/hub/signups/{signup_id}/card', { params: { path: { signup_id: signupId } }, body }),
      reading<Freelancer>(),
    ),
  /** PigroCRM's spaces, read by the hub's API with the token it holds: the browser
   *  never talks to the CRM (ORB-142). A 503 carries the sentence the page shows.
   *  Newest first, `q` matched against the slug or the owner's address, paged with a
   *  cursor (REB-313): the registry itself takes no query parameters, but the hub's
   *  own route filters and slices what it already fetched before answering. */
  pigroSpaces: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/pigro/instances', { params: { query: filled(params) } })),
  /** How the guide is doing: downloads, the members behind them, the latest (ORB-156). */
  guideStats: () => unwrap(client.GET('/api/hub/perks/guide'), reading<GuideStats>()),
  /** Who comes back in: logins, the members behind them, the last week -- unchanged
   *  aggregate counters. `recenti` is searched and cursor-paginated since REB-313,
   *  no longer a hardcoded top 20 (ORB-158). */
  loginStats: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/logins', { params: { query: filled(params) } })),
  /** Who reads this area (ORB-123): oldest first with no `q`, so the page still reads
   *  as a history, best-match first once `q` narrows it (REB-313). */
  admins: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/admins', { params: { query: filled(params) } })),
  /** Promotes whatever `users` row already answers to this address, or creates a bare
   *  one from `nome`/`cognome` when none exists yet (REB-279, no password anywhere). */
  promote: (body: Schemas['PromoteRequest']) => unwrap(client.POST('/api/hub/admins/promote', { body })),
  /** Sets `role = 'member'`, fully reversible since nothing is deleted. */
  demote: (id: string) =>
    unwrap(client.POST('/api/hub/admins/{user_id}/demote', { params: { path: { user_id: id } } })),
  /** The admin's own tokens for agents (REB-213): newest first with no `q`, revoked
   *  ones included either way; best-match first by name once `q` narrows it, since
   *  REB-313. */
  tokens: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/tokens', { params: { query: filled(params) } })),
  createToken: (nome: string) => unwrap(client.POST('/api/hub/tokens', { body: { nome } })),
  revokeToken: (id: string) =>
    unwrap(client.DELETE('/api/hub/tokens/{token_id}', { params: { path: { token_id: id } } })),
  notes: (kind: ThreadKind, id: string) =>
    kind === 'freelancers'
      ? unwrap(
          client.GET('/api/hub/freelancers/{freelancer_id}/notes', { params: { path: { freelancer_id: id } } }),
      reading<Note[]>(),
        )
      : unwrap(client.GET('/api/hub/companies/{company_id}/notes', { params: { path: { company_id: id } } }), reading<Note[]>()),
  /** The author is the session's and the source is `web`, so the body is the text alone. */
  addNote: (kind: ThreadKind, id: string, testo: string) =>
    kind === 'freelancers'
      ? unwrap(
          client.POST('/api/hub/freelancers/{freelancer_id}/notes', {
            params: { path: { freelancer_id: id } },
            body: { testo },
          }),
      reading<Note>(),
        )
      : unwrap(
          client.POST('/api/hub/companies/{company_id}/notes', {
            params: { path: { company_id: id } },
            body: { testo },
          }),
      reading<Note>(),
        ),
  /** Replaces the text of a note an admin may edit; refused for the person's own events. */
  editNote: (noteId: string, testo: string) =>
    unwrap(
      client.PATCH('/api/hub/notes/{note_id}', { params: { path: { note_id: noteId } }, body: { testo } }),
      reading<Note>(),
    ),
  /** Soft-deletes a note an admin may delete; the trail keeps its text. */
  deleteNote: (noteId: string) =>
    unwrap(client.DELETE('/api/hub/notes/{note_id}', { params: { path: { note_id: noteId } } })),
  /** Who overrode, cleared, deleted, restored or reverted this card or request, and
   *  when (REB-355): the same trail shape for either `kind`, newest first. */
  auditTrail: (kind: ThreadKind, id: string) =>
    kind === 'freelancers'
      ? unwrap(
          client.GET('/api/hub/freelancers/{freelancer_id}/audit', { params: { path: { freelancer_id: id } } }),
      reading<AdminAction[]>(),
        )
      : unwrap(client.GET('/api/hub/companies/{company_id}/audit', { params: { path: { company_id: id } } }), reading<AdminAction[]>()),
  /** Puts a field back to the value a past `overridden` entry's own `before` names,
   *  itself recorded as a fresh override (REB-355): the response is the plain card or
   *  request, which the caller already reads through its own detail query. */
  revertAction: async (kind: ThreadKind, id: string, actionId: string): Promise<void> => {
    if (kind === 'freelancers') {
      await unwrap(
        client.POST('/api/hub/freelancers/{freelancer_id}/audit/{action_id}/revert', {
          params: { path: { freelancer_id: id, action_id: actionId } },
        }),
      )
    } else {
      await unwrap(
        client.POST('/api/hub/companies/{company_id}/audit/{action_id}/revert', {
          params: { path: { company_id: id, action_id: actionId } },
        }),
      )
    }
  },
  /** «Match» (REB-413): every match in the admin area, newest first. */
  matches: (filters: MatchesFilters & { limit?: number; offset?: number } = {}) =>
    unwrap(client.GET('/api/hub/matches', { params: { query: filled(filters) } }), reading<MatchList>()),
  /** A freelancer's matches and contracts (REB-387). */
  contracts: (freelancerId: string) =>
    unwrap(
      client.GET('/api/hub/freelancers/{freelancer_id}/matches', {
        params: { path: { freelancer_id: freelancerId } },
      }),
      reading<FreelancerContracts>(),
    ),
  fiscal: (freelancerId: string) =>
    unwrap(
      client.GET('/api/hub/freelancers/{freelancer_id}/fiscal', {
        params: { path: { freelancer_id: freelancerId } },
      }),
    ),
  saveFiscal: (freelancerId: string, body: FiscalData) =>
    unwrap(
      client.PUT('/api/hub/freelancers/{freelancer_id}/fiscal', {
        params: { path: { freelancer_id: freelancerId } },
        body,
      }),
    ),
  matchPrefill: (freelancerId: string, companyId: string) =>
    unwrap(
      client.GET('/api/hub/freelancers/{freelancer_id}/matches/prefill', {
        params: { path: { freelancer_id: freelancerId }, query: { company_id: companyId } },
      }),
      reading<MatchPrefill>(),
    ),
  /** «Controlla e invia»'s sentences (REB-476): a 422 names the field as `createMatch`
   *  would, and nothing is written. */
  matchCheck: (freelancerId: string, body: MatchCreate) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/matches/check', {
        params: { path: { freelancer_id: freelancerId } },
        body: matchBody(body),
      }),
    ),
  /** «Controlla e invia»'s previews: a PDF typeset now and saved nowhere. */
  matchPreview: (freelancerId: string, body: MatchCreate, documento: 'lettera' | 'quadro') =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/matches/preview', {
        params: { path: { freelancer_id: freelancerId }, query: { documento } },
        body: matchBody(body),
        parseAs: 'blob',
      }),
    ),
  /** «Salva senza inviare»: the draft match with its numbered letter. */
  createMatch: (freelancerId: string, body: MatchCreate) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/matches', {
        params: { path: { freelancer_id: freelancerId } },
        body: matchBody(body),
      }),
      reading<Match>(),
    ),
  cancelMatch: (matchId: string) =>
    unwrap(client.POST('/api/hub/matches/{match_id}/cancel', { params: { path: { match_id: matchId } } }), reading<Match>()),
  closeMatch: (matchId: string) =>
    unwrap(client.POST('/api/hub/matches/{match_id}/close', { params: { path: { match_id: matchId } } }), reading<Match>()),
  /** «Invia per la firma» (REB-390): the document that can leave now goes to Documenso. */
  sendMatch: (matchId: string) =>
    unwrap(client.POST('/api/hub/matches/{match_id}/send', { params: { path: { match_id: matchId } } }), reading<SendReport>()),
  /** «Aggiorna stato» (REB-407): what Documenso says, applied as the webhook would. */
  refreshDocument: (documentId: string) =>
    unwrap(
      client.POST('/api/hub/contract-documents/{document_id}/refresh', {
        params: { path: { document_id: documentId } },
      }),
      reading<ContractDocument>(),
    ),
  /** «Reinvia email»: the signing mail again, for a document still waiting. */
  resendDocument: (documentId: string) =>
    unwrap(
      client.POST('/api/hub/contract-documents/{document_id}/resend', {
        params: { path: { document_id: documentId } },
      }),
      reading<ContractDocument>(),
    ),
  /** «Annulla» on a framework agreement not signed yet. */
  cancelDocument: (documentId: string) =>
    unwrap(
      client.POST('/api/hub/contract-documents/{document_id}/cancel', {
        params: { path: { document_id: documentId } },
      }),
      reading<ContractDocument>(),
    ),
  /** «Registra disdetta» on an active framework agreement. */
  recordNotice: (documentId: string) =>
    unwrap(
      client.POST('/api/hub/contract-documents/{document_id}/notice', {
        params: { path: { document_id: documentId } },
      }),
      reading<ContractDocument>(),
    ),
  /** A plain href, like `cvUrl`: the route answers an attachment behind the cookie. */
  contractPdfUrl: (documentId: string, firmato = false) =>
    `/api/hub/contract-documents/${encodeURIComponent(documentId)}/pdf${firmato ? '?firmato=true' : ''}`,
  /** The referral ledger (P-REB-44): every reward, newest first. */
  referrals: (params: ReferralLedgerParams = {}) =>
    unwrap(client.GET('/api/hub/referrals', { params: { query: filled(params) } }), reading<ReferralLedgerList>()),
  /** `da_confermare` to `confermato` to `pagato`, one step at a time. */
  setReferralState: (id: string, stato: RewardStato) =>
    unwrap(
      client.POST('/api/hub/referrals/{reward_id}/state', {
        params: { path: { reward_id: id } },
        body: { stato },
      }),
      reading<ReferralLedgerItem>(),
    ),
  /** What an admin prices by hand for a reward left with no figure (an `a corpo`
   *  letter with no estimated days). */
  setReferralPrice: (id: string, baseAmount: string, rewardAmount: string) =>
    unwrap(
      client.POST('/api/hub/referrals/{reward_id}/price', {
        params: { path: { reward_id: id } },
        body: { base_amount: baseAmount, reward_amount: rewardAmount },
      }),
      reading<ReferralLedgerItem>(),
    ),
  referralSettings: () => unwrap(client.GET('/api/hub/referral-settings')),
  saveReferralSettings: (rateFreelancer: string, rateCompany: string) =>
    unwrap(
      client.PUT('/api/hub/referral-settings', {
        body: { rate_freelancer: rateFreelancer, rate_company: rateCompany },
      }),
    ),
  /** «Modelli AI» (REB-833): the providers' keys are write-only, the answer carries the hint. */
  aiConfig: () => unwrap(client.GET('/api/hub/admin/ai')),
  /** «Registro» (REB-861): the configuration's trail, newest first. */
  aiAudit: (limit = 50) => unwrap(client.GET('/api/hub/admin/ai/audit', { params: { query: { limit } } }), reading<AiAuditAction[]>()),
  aiSaveKey: (provider: string, key: string) =>
    unwrap(
      client.PUT('/api/hub/admin/ai/providers/{provider}/key', {
        params: { path: { provider } },
        body: { key },
      }),
    ),
  aiSetEnabled: (provider: string, enabled: boolean) =>
    unwrap(
      client.PUT('/api/hub/admin/ai/providers/{provider}/enabled', {
        params: { path: { provider } },
        body: { enabled },
      }),
    ),
  aiUpdateTask: (task: string, body: Schemas['TaskBody']) =>
    unwrap(client.PUT('/api/hub/admin/ai/tasks/{task}', { params: { path: { task } }, body })),
  aiUpsertPrice: (body: Schemas['PriceBody']) => unwrap(client.PUT('/api/hub/admin/ai/prices', { body })),
  aiSetSourceEnabled: (kind: Schemas['SourceSwitchRead']['kind'], enabled: boolean) =>
    unwrap(
      client.PUT('/api/hub/admin/ai/sources/{kind}/enabled', {
        params: { path: { kind } },
        body: { enabled },
      }),
    ),
  /** «Schede da aggiornare» (REB-839): the cards a newer prompt or changed sources left behind. */
  aiStaleCards: () => unwrap(client.GET('/api/hub/admin/ai/cards/stale'), reading<AiStaleCards>()),
  /** «Avvia rigenerazione»: queues up to `limit` of them; they are written afterwards. */
  aiRegenerateStale: (limit: number) =>
    unwrap(client.POST('/api/hub/admin/ai/cards/regenerate-stale', { body: { limit } })),
  aiProva: (task: string) =>
    unwrap(client.POST('/api/hub/admin/ai/tasks/{task}/prova', { params: { path: { task } } })),
}

// ---- «Modelli AI» (REB-833) -------------------------------------------------------------

export type AiProvider = Schemas['ProviderRead']
export type AiTask = Schemas['TaskRead']
export type AiSourceSwitch = Schemas['SourceSwitchRead']
export type AiStaleCards = Narrow<Schemas['StaleCardsRead'], { by_reason: { prompt?: number; source?: number } }>
export type AiConfig = Schemas['AiConfigRead']
export type AiProva = Schemas['ProvaRead']

/** One entry of the «Registro» of «Modelli AI» (REB-861): what an admin changed in the
 *  configuration and every regeneration of the cards, its start and its end. The payloads
 *  carry counts, costs and the people's ids, never a name or a card's text. */
export type AiAuditKind =
  | 'ai_key'
  | 'ai_switch'
  | 'ai_task'
  | 'ai_price'
  | 'eval_add'
  | 'eval_remove'
  | 'eval_expected'
  | 'eval_accept'
  | 'eval_rate'
  | 'cards_regenerate'
  | 'cards_regenerated'

export type AiAuditPayload = {
  provider?: string
  model?: string
  /** An `ai_switch` entry's source (`github`, `links`) when it is not a provider's. */
  source?: string
  /** An `ai_key` entry's: `chiave sostituita`, or `seed` for the command line's import. */
  azione?: string
  task?: string
  changed?: string[]
  after?: Record<string, unknown>
  /** A `cards_regenerate` entry's: how many cards were asked for and queued, whether
   *  they went through the Batch API (and as which batch), and the door. */
  limit?: number
  queued?: number
  batch?: boolean
  batch_id?: string
  fonte?: ActionSource
  /** A `cards_regenerated` entry's: the start it ends and what became of the cards. */
  start_action_id?: string | null
  /** The counts, the cost and the failures are `null` when the start's entry was lost
   *  (`start_missing`) and the caller could not count them itself. */
  written?: number | null
  failed?: number | null
  superseded?: number | null
  skipped?: number | null
  errored?: number | null
  /** The calls' cost in euros, as a decimal string. */
  cost_eur?: string | null
  unpriced?: number
  failures?: { freelancer_id: string; reason: string }[] | null
  start_missing?: boolean
  stopped?: boolean
  /** Why it stopped, when known (`unavailable`, `cap_reached`, `not_configured`, `error`). */
  stop_reason?: string
  left?: number
  batch_status?: 'applied' | 'failed'
}

export type AiAuditAction = Narrow<Schemas['AdminActionRead'], { kind: AiAuditKind; payload: AiAuditPayload }>

/** One match's link to its deal on Pigro and the hours read from there (REB-498), the
 *  admin's: the browser never talks to the CRM, the hub's API does with its own token. */
export const matches = {
  /** One match as «Match e contratti» reads it: «Consuntivo» names it by its company,
   *  its role and its letter's number (REB-503). */
  get: (matchId: string) =>
    unwrap(client.GET('/api/hub/matches/{match_id}', { params: { path: { match_id: matchId } } }), reading<Match>()),
  /** «Riprova su Pigro»: the link runs now and the match comes back as it stands,
   *  `collegato` or with the CRM's sentence. The first link of a freelancer opens their
   *  space and can take the CRM up to 90 seconds: no timeout here cuts it short. A 503
   *  says this environment has no link to the CRM, a 502 that the CRM did not answer. */
  linkPigro: (matchId: string) =>
    unwrap(client.POST('/api/hub/matches/{match_id}/pigro/link', { params: { path: { match_id: matchId } } }), reading<Match>()),
  /** «Consuntivo»: the hours on the match's deal over the whole engagement. A 409 says,
   *  in the state's sentence, that the match is not linked yet. */
  report: (matchId: string) =>
    unwrap(client.GET('/api/hub/matches/{match_id}/report', { params: { path: { match_id: matchId } } }), reading<MatchReport>()),
}

// ---- whoever is signed in --------------------------------------------------------------

export type MemberRequest = Narrow<Schemas['MemberRequest'], { remoto: Remoto }>
export type Me = Narrow<
  Schemas['MeRead'],
  {
    role: Role
    remoto: Remoto | null
    cv_filename: string | null
    cv_size: number | null
    tariffa_giornaliera: string | null
    posizione: string | null
    links: string[]
    richieste: MemberRequest[]
  }
>
export type MemberUpdate = Schemas['MemberUpdate']
export type CompanyUpdate = Schemas['CompanyUpdate']
export type MemberReferral = Narrow<Schemas['MemberReferral'], { referred: { kind: 'freelancer' | 'company'; nome: string; created_at: string; stato: string | null }[] }>

// ---- referrals (P-REB-44) --------------------------------------------------------------

export type RewardStato = 'da_confermare' | 'confermato' | 'pagato'
export type ReferralStato = 'da_verificare' | 'verificato'
export type ReferralVerifiedVia = 'accesso' | 'lettera' | 'storico'
export type ReferralLedgerItem = Narrow<
  Schemas['ReferralLedgerItem'],
  {
    kind: 'freelancer' | 'company'
    stato: RewardStato | null
    referral_stato: ReferralStato
    verified_via: ReferralVerifiedVia | null
  }
>
export type ReferralLedgerList = Narrow<Schemas['ReferralLedgerList'], { items: ReferralLedgerItem[] }>
export type ReferralSettings = Schemas['ReferralSettingsRead']

export interface ReferralLedgerParams {
  stato?: RewardStato
  cursor?: string
  limit?: number
}

export const member = {
  /** 202 whether the address is known or not; the page says one thing in both cases.
   *  `utm` is the campaign the login page was opened from (REB-426), left out when empty. */
  requestLink: (email: string, utm: Utm = {}) =>
    unwrap(
      client.POST('/api/hub/auth/link', {
        body: Object.keys(utm).length ? { email, utm } : { email },
      }),
    ),
  enter: (token: string) => unwrap(client.POST('/api/hub/auth/enter', { body: { token } }), reading<Me>()),
  me: () => unwrap(client.GET('/api/hub/me'), reading<Me>()),
  update: (body: MemberUpdate) => unwrap(client.PATCH('/api/hub/me', { body }), reading<Me>()),
  /** One of the caller's own requests by its id (REB-602): the edit page always names the
   *  request it shows, so a new request filed in another tab never changes which one it
   *  saves. The id-less `PATCH /me/company` stays on the API for older links and is not
   *  called from here. */
  updateCompany: (id: string, body: CompanyUpdate) =>
    unwrap(
      client.PATCH('/api/hub/me/company/{company_id}', { params: { path: { company_id: id } }, body }),
      reading<Me>(),
    ),
  /** A brand-new request (REB-381), not an edit: the same eight-field shape as
   *  `updateCompany`, since `CompanyFields` is what both accept. */
  createCompanyRequest: (body: CompanyUpdate) => unwrap(client.POST('/api/hub/me/company', { body }), reading<Me>()),
  replaceCv: (file: File) => {
    const form = new FormData()
    form.set('cv', file, file.name)
    return sendForm<Me>('PUT', '/api/hub/me/cv', form)
  },
  cvUrl: '/api/hub/me/cv',
  /** The guide, the community's second perk. A plain href rather than a fetch: the
   *  route answers with an attachment, and a session cookie travels with a navigation
   *  the same way it travels with a request. */
  guideUrl: '/api/hub/me/guide',
  /** «Contratti» (REB-392): the caller's own, from the session. */
  contracts: () => unwrap(client.GET('/api/hub/me/contracts'), reading<MemberContracts>()),
  /** A signed copy, a plain href like `cvUrl`: the route answers an attachment. */
  contractPdfUrl: (documentId: string) => `/api/hub/me/contracts/${encodeURIComponent(documentId)}/pdf`,
  /** The member's own link, issued the first time it is asked for (P-REB-44). */
  referral: () => unwrap(client.GET('/api/hub/me/referral'), reading<MemberReferral>()),
  logout: () => unwrap(client.POST('/api/hub/me/logout')),
}

// ---- the talent cloud (REB-519, spec § 4.2) -------------------------------------------------

export type CloudTalent = Narrow<Schemas['CloudTalentRead'], { modalita: Remoto | null }>
export type CloudTalentList = Narrow<Schemas['CloudTalentList'], { items: CloudTalent[] }>

/** The cloud's filters: a role of `ruoli`, a seniority, one skill, a work mode, and a
 *  band of the client's price per day in whole euro (its bottom and its top). */
export interface CloudFilters {
  ruolo?: string
  seniority?: string
  competenza?: string
  modalita?: string
  fascia_min?: number
  fascia_max?: number
}

/** One person of the builder's proposal in the cloud: the admin's shape and the name,
 *  which the cloud shows (spec § 4.2) and the public read never carries. */
export type CloudTeamMember = Narrow<AdminTeamMember, { nome: string; cognome: string }>
export type CloudTeamProposal = Narrow<AdminTeamProposal, { team: CloudTeamMember[] }>

export const cloud = {
  /** 403 with the API's sentence when no grant of the caller is live. */
  talents: (filters: CloudFilters = {}) =>
    unwrap(client.GET('/api/hub/me/cloud/talents', { params: { query: filled(filters) } }), reading<CloudTalentList>()),
  /** «Apri il CV»: a plain href like `member.cvUrl`, the route answers an attachment. */
  cvUrl: (freelancerId: string) => `/api/hub/me/cloud/talents/${encodeURIComponent(freelancerId)}/cv`,
  /** The builder in the cloud: the public route's answers, with the caller's name on it. */
  propose: (body: TeamProposalCreate) => unwrap(client.POST('/api/hub/me/cloud/proposals', { body }), reading<CloudTeamProposal>()),
  /** «Assumi team»: the proposal filed at once, no form; 409 when already requested. */
  hire: (proposalId: string) =>
    unwrap(client.POST('/api/hub/me/cloud/requests', { body: { proposal_id: proposalId } })),
  /** «Richiedi» on one card: 404 with the API's sentence for a talent no longer shown. */
  ask: (freelancerId: string) =>
    unwrap(client.POST('/api/hub/me/cloud/requests', { body: { freelancer_id: freelancerId } })),
}

// ---- campaigns, the public part -----------------------------------------------------------

export const campaigns = {
  unsubscribe: (t: string) =>
    unwrap(client.POST('/api/hub/campagne/disiscrizione', { params: { query: { t } } })),
}
