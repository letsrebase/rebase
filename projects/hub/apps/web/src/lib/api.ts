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
  // The API's own sentence says what ran out and when to come back (the CV applications'
  // hourly cap, the day's sign-in links); the generic one is for a limiter that names none.
  if (status === 429 && typeof detail !== 'string') {
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

export type Remote = 'remote' | 'hybrid' | 'on_site'

export interface FreelancerApplication {
  first_name: string
  last_name: string
  email: string
  linkedin_url: string
  daily_rate: string
  job_title: string
  remote: Remote | ''
  links: string[]
  cv: File | null
}

/** Multipart, because the CV is a file. Empty optional fields are left out rather than
 *  sent as `""`, which the API would try to validate as a value. */
export function applyAsFreelancer(
  data: FreelancerApplication,
  utm: Utm,
  distinctId: string | null = null,
  ref: string | null = null,
): Promise<Schemas['Ack']> {
  const form = new FormData()
  // The browser's PostHog id, when the page is measured: the API sends the completion
  // event itself (REB-215), and this is what puts it on the same person as the steps.
  if (distinctId) form.set('distinct_id', distinctId)
  if (ref) form.set('ref', ref)
  form.set('first_name', data.first_name)
  form.set('last_name', data.last_name)
  form.set('email', data.email)
  form.set('daily_rate', data.daily_rate)
  form.set('job_title', data.job_title)
  form.set('remote', data.remote)
  const linkedin = linkedinProfile(data.linkedin_url)
  if (linkedin) form.set('linkedin_url', linkedin)
  for (const link of data.links) if (link.trim()) form.append('links', link.trim())
  for (const [key, value] of Object.entries(utm)) if (value) form.set(key, value)
  if (data.cv) form.set('cv', data.cv, data.cv.name)
  return sendForm('POST', '/api/hub/freelancers', form)
}

export interface CompanyRequest {
  company_name: string
  requested_role: string
  contact_first_name: string
  contact_last_name: string
  email: string
  phone: string
  project: string
  period_from: string
  duration: string
  daily_budget: string
  remote: Remote | ''
  onsite_days: string
  headcount: string
}

/** What «Nuova richiesta» collects: the wizard's answers, and the CRM deal when it has one. */
export interface AdminCompanyRequest extends CompanyRequest {
  deal_id?: string
}

export function requestPeople(
  data: CompanyRequest,
  utm: Utm,
  distinctId: string | null = null,
  ref: string | null = null,
): Promise<Schemas['Ack']> {
  return unwrap(
    client.POST('/api/hub/companies', {
      body: {
        ...data,
        remote: asSent<Remote>(data.remote),
        // `''` means "not ibrido, never asked": the API's own `onsite_days` is
        // `int | None`, which a blank string does not coerce to.
        onsite_days: data.onsite_days ? Number(data.onsite_days) : null,
        headcount: asSent<number>(data.headcount),
        utm: Object.keys(utm).length ? utm : null,
        ...(distinctId ? { distinct_id: distinctId } : {}),
        ...(ref ? { ref } : {}),
      },
    }),
  )
}

// ---- the team builder, public (P-REB-43, spec § 3.2, § 3.3) --------------------------------

export type Band = Schemas['Band']
export type Card = Schemas['CardRead']

/** One person of a proposed team. The public read has no `freelancer_id` (a visitor
 *  cannot follow a talent across proposals), nor a name. */
export type TeamMember = Schemas['TeamMemberRead']

/** The team's bands: the sum of its people's, per day and per month at `days_month`
 *  (22) days; both `null` when somebody has no band, or nobody is proposed. */
export type TeamEconomics = {
  day: Band | null
  month: Band | null
  days_month: number
}

/** `location` is what the engine read in the visitor's own description about place. */
export type TeamProposal = Schemas['TeamProposalRead']

export type TeamProposalCreate = Schemas['TeamProposalCreate']
export type TeamRequestCreate = Schemas['TeamRequestCreate']
export type TeamAvailabilityAnswer = Schemas['TeamAvailabilityAnswer']

/** The answer recorded, or `invalid` for a link unknown, spent or expired alike. */
export type TeamAvailabilityOutcome = Schemas['TeamAvailabilityOutcome']['outcome']

export const team = {
  /** 503 when the builder is off or too busy, 502 when Claude does not answer, 422 on
   *  the description or a `previous_id` that is not a live public proposal: each with
   *  the API's own sentence. */
  propose: (body: TeamProposalCreate) =>
    unwrap(client.POST('/api/hub/team/proposals', { body }), reading<TeamProposal>()),
  /** The public read of a proposal by its id (REB-675, `/hub/team?proposal=`): a 404
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

export type TeamRequestStatus = 'new' | 'contacted' | 'closed'
export type TeamRequestOrigin = 'public' | 'cloud'
/** A talent's answer to the availability mail (D1): `null` until they give one. */
export type TalentAnswer = 'yes' | 'no'

export type TeamRequestTalent = Schemas['TeamRequestTalentRead']
export type TeamRequest = Schemas['TeamRequestRead']
export type TeamRequestListItem = Schemas['TeamRequestListItem']
export type TeamProposalListMember = Schemas['TeamProposalListMember']
/** The refusal an attempt row keeps: Claude did not answer, or every slot or the day's
 *  proposals were spent. */
export type TeamProposalError = 'llm_unavailable' | 'team_builder_busy'
export type TeamProposalListItem = Schemas['TeamProposalListItem']

/** What the list takes beside `cursor`/`limit`, and what `/admin/team` carries in its
 *  URL: a state of `TeamRequestStatus`, which the API checks. */
export interface TeamRequestsFilters {
  status?: string
}

/** What «Proposte» takes beside `cursor`/`limit`, and what `/admin/team/proposals`
 *  carries in its URL: `outcome` is `ok` or `error`, checked by the API. The API and
 *  the MCP tool filter by `origin` too; the page has no control for it yet, so the
 *  URL does not carry one. */
export interface TeamProposalsFilters {
  outcome?: string
}

export type FreelancerCard = Schemas['FreelancerCardAdminRead']
export type CardPerson = Schemas['CardPersonRead']
export type PersonDifference = Schemas['PersonDifference']
export type PersonDifferenceField = PersonDifference['field']
export type CardConflict = Schemas['ConflictRead']
export type CardProvenance = Schemas['CardProvenance']

// ---- the admin area -------------------------------------------------------------------

export type Role = 'member' | 'admin'

export type Admin = Schemas['AdminRead']
export type Freelancer = Schemas['FreelancerDetail']
export type TalentCloudGrant = Schemas['TalentCloudGrantRead']
export type Company = Schemas['CompanyRead']

/** Where a note came from (REB-856). `null` is a note that predates the tracking. */
export type NoteSource = 'web' | 'mcp' | 'person' | 'system'

/** The door a trail entry came through: a note's (`NoteSource`) or, for a regeneration of
 *  the cards, `cli` too (REB-861). */
export type ActionSource = NoteSource | 'cli'

export type Note = Schemas['NoteRead']

/** The two rows a thread can hang on, as the API paths name them. */
export type ThreadKind = 'freelancers' | 'companies'

export type AdminActionKind = Schemas['AdminActionRead']['kind']

export type AdminAction = Schemas['AdminActionRead']

export type FreelancerOverride = Schemas['FreelancerOverride']
export type CompanyOverride = Schemas['CompanyOverride']
/** A point of a person's contact list as a page reads it (REB-944): the sign-in address is
 *  the entry with no `id`. */
export type ContactPoint = Schemas['ContactPointRead']
/** A point as the list a write sends holds it. */
export type ContactPointWrite = Schemas['ContactPointInput']
export type LoginStats = Schemas['LoginStats']
/** `freelancer_id` is what the page links each download by; the API's `GuideDownloadRead`
 *  has no such field yet (HubBackend adds it, `null` for a company contact), so until the
 *  types are regenerated this is the page's own reading. */
export type GuideStats = Schemas['GuideStats']
export type Talent = Schemas['TalentRead']
export type TalentList = Schemas['TalentList']

/** Why two cards look like one person (REB-800): the same LinkedIn profile, the same name
 *  or the same telephone number. Only the first is a reason to merge. */
export type DuplicateSignal = 'linkedin' | 'first_name' | 'phone' | 'email'

export type DuplicatePerson = Schemas['PersonRead']
export type DuplicatePair = Schemas['DuplicatePairRead']
export type DuplicateList = Schemas['DuplicateListRead']
export type MergeResult = Schemas['MergeRead']
export type FreelancerDraft = Schemas['FreelancerDraft']
export type AdminToken = Schemas['AdminTokenRead']
export type CreatedToken = Schemas['CreatedToken']

/** What `GET /api/hub/talent` takes beside `limit` (REB-285/286): `q` searches
 *  name/surname/email/`posizione`, trigram-ordered when present; every other field
 *  narrows the merged list of cards and bare sign-ups the same way the state pills
 *  always have. This is also the shape `/admin/talent`'s own `validateSearch` carries in
 *  the URL (`router.tsx`). */
export interface TalentsFilters {
  status?: string
  q?: string
  job_title?: string
  remote?: Remote
  min_rate?: string
  max_rate?: string
  /** The page of the site the person started from (`home`, `pigrocrm`); `origin`,
   *  the channel a row shows (`form`, `wizard`, `admin`), has no control on the page. */
  origin_page?: string
  utm_source?: string
  has_cv?: boolean
  with_logins?: boolean
  created_from?: string
  created_to?: string
  /** REB-823, what the card says (the fields MCP's `list_talents` takes). `skills`,
   *  `industries` and `languages` are what an admin types, terms split by commas, and
   *  every term must be named; `seniority` is one level (`junior`, `mid`, `senior`,
   *  `lead`); the years are whole numbers; `card` is `present`, `failed` or `absent`;
   *  `sort` is `rate_asc`, `rate_desc`, `experience_years_asc` or `experience_years_desc`,
   *  newest first when left out. */
  skills?: string
  location?: string
  seniority?: string
  min_years?: string
  max_years?: string
  industries?: string
  languages?: string
  card?: string
  sort?: string
}

/** The comma-separated terms an admin typed, as the list the API takes: blanks dropped,
 *  `undefined` when nothing is left. */
export function terms(value: string | undefined): string[] | undefined {
  const list = (value ?? '').split(',').map((term) => term.trim()).filter(Boolean)
  return list.length > 0 ? list : undefined
}

/** A whole number of years as typed, or `undefined` for anything else: a filter the API
 *  cannot take narrows nothing. */
export function yearsFilter(value: string | undefined): number | undefined {
  return value !== undefined && /^[0-9]{1,2}$/.test(value.trim()) ? Number(value) : undefined
}

/** What `GET /api/hub/companies` takes beside `limit` (REB-285/286): the shape
 *  `/admin/companies`'s own `validateSearch` carries in the URL. */
export interface CompaniesFilters {
  status?: string
  q?: string
  budget_min?: string
  budget_max?: string
  period_from?: string
  origin?: string
  created_from?: string
  created_to?: string
}

/** A contract's state, as the member's «Contratti» page reads it. */
export type DocumentStatus = 'generated' | 'pending' | 'sent' | 'signed' | 'cancelled' | 'notice_served'
/** Where a company request's link to the CRM stands: `null` for a request from before the link. */
export type PigroStatus = 'to_link' | 'linked' | 'error' | 'rejected'
/** REB-687: a contract's family, the letter type it was made with. */
export type Regime = Schemas['MemberContract']['regime']
export type EngagementHandoff = Schemas['HandoffRead']
// ---- campaigns (P-REB-41) -------------------------------------------------------------

export type CampaignStatus = 'draft' | 'scheduled' | 'sending' | 'sent' | 'cancelled'
export type CampaignAction = Schemas['CampaignDraft']['action']
export type CampaignMeta = Schemas['CampaignDraft']['button_meta']
export type RecipientStatus = 'queued' | 'sent' | 'skipped' | 'failed'

export type Campaign = Schemas['CampaignRead']
export type CampaignCounts = Schemas['CampaignCounts']
export type CampaignListItem = Schemas['CampaignListItem']
export type CampaignRecipient = Schemas['RecipientRead']
export type AudienceRow = Schemas['AudienceRowRead']
export type AudiencePreview = Schemas['AudiencePreview']
export type CampaignTemplate = Schemas['TemplateRead']
/** The form builds `filters` and the API validates it by `list`, so the form's own
 *  reading is the looser one, narrowed back to the schema's where a body is sent. */
export type CampaignDraft = Narrow<Schemas['CampaignDraft'], { filters?: Record<string, unknown> | null }>

export type MemberContract = Schemas['MemberContract']
export type MemberContracts = Schemas['MemberContracts']
export type CompanyList = Schemas['CompanyList']
export type TeamRequestList = Schemas['TeamRequestList']
export type TeamProposalList = Schemas['TeamProposalList']
export type CampaignList = Schemas['CampaignList']
export type CampaignDetail = Schemas['CampaignDetail']

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
  moveFreelancer: (id: string, status: string) =>
    unwrap(
      client.PATCH('/api/hub/freelancers/{freelancer_id}', {
        params: { path: { freelancer_id: id } },
        body: { status },
      }),
      reading<Freelancer>(),
    ),
  /** Sets or clears any field beyond `status` (REB-347/355): a key present and
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
    const { data, error, response } = await client.POST('/api/hub/freelancers/{freelancer_id}/anonymous-cv', {
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
  setTeamRequestStatus: (id: string, status: TeamRequestStatus) =>
    unwrap(
      client.POST('/api/hub/team/requests/{request_id}/status', {
        params: { path: { request_id: id } },
        body: { status },
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
  setTeamRequestSummary: (id: string, summary: string) =>
    unwrap(
      client.PATCH('/api/hub/team/requests/{request_id}/summary', {
        params: { path: { request_id: id } },
        body: { summary },
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
          remote: asSent<Remote>(data.remote),
          onsite_days: data.onsite_days ? Number(data.onsite_days) : null,
          headcount: Number(data.headcount),
          deal_id: data.deal_id ?? null,
        },
      }),
      reading<Company>(),
    ),
  moveCompany: (id: string, status: string) =>
    unwrap(
      client.PATCH('/api/hub/companies/{company_id}', {
        params: { path: { company_id: id } },
        body: { status },
      }),
      reading<Company>(),
    ),
  /** Sets or clears any field beyond `status` (REB-347/355), same contract as
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
  /** «Crea match» (REB-1105): the CRM finds or writes the freelancer's organization, the hub keeps
   *  a `handoff` row under a new engagement id, and the answer carries the CRM's engagement form to
   *  open, filled in (`ready`), or says the request has no deal in the CRM yet (`no_deal`). A 503
   *  says this environment has no link to the CRM, a 409 is the CRM's refusal in its words, a 502
   *  that it did not answer. */
  handOffEngagement: (freelancerId: string, companyId: string) =>
    unwrap(
      client.POST('/api/hub/freelancers/{freelancer_id}/engagement-handoff', {
        params: { path: { freelancer_id: freelancerId } },
        body: { company_id: companyId },
      }),
      reading<EngagementHandoff>(),
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
  /** Every card and every bare sign-up as one list (REB-282/283), `status` `lead` for
   *  the bare ones alone -- the read model «Talenti» replaced «Developer e CTO» and
   *  «Iscrizioni» with. `filters` beside `status` and `cursor` are REB-285's search and
   *  its per-field narrowing, REB-286's own filter row sends straight through; `limit`
   *  is the one override `AdminTalentLead` needs to see every lead at once rather
   *  than the server's own default page. */
  talent: (filters: TalentsFilters & { cursor?: string; limit?: number } = {}) => {
    const { skills, seniority, min_years, max_years, industries, languages, ...rest } = filters
    const query = filled({
      ...rest,
      skills: terms(skills),
      industries: terms(industries),
      languages: terms(languages),
      seniority: seniority ? [seniority] : undefined,
      min_years: yearsFilter(min_years),
      max_years: yearsFilter(max_years),
    })
    return unwrap(client.GET('/api/hub/talent', { params: { query } }), reading<TalentList>())
  },
  /** Who is on the list twice (REB-800): the pairs that share a LinkedIn profile
   *  (`mergeable`) and the ones that only share a name or a number (`to_review`). */
  duplicates: () => unwrap(client.GET('/api/hub/talent/duplicates'), reading<DuplicateList>()),
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
      reading<Talent>(),
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
   *  aggregate counters. `recent` is searched and cursor-paginated since REB-313,
   *  no longer a hardcoded top 20 (ORB-158). */
  loginStats: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/logins', { params: { query: filled(params) } })),
  /** Who reads this area (ORB-123): oldest first with no `q`, so the page still reads
   *  as a history, best-match first once `q` narrows it (REB-313). */
  admins: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/admins', { params: { query: filled(params) } })),
  /** Promotes whatever `users` row already answers to this address, or creates a bare
   *  one from `first_name`/`last_name` when none exists yet (REB-279, no password anywhere). */
  promote: (body: Schemas['PromoteRequest']) => unwrap(client.POST('/api/hub/admins/promote', { body })),
  /** Sets `role = 'member'`, fully reversible since nothing is deleted. */
  demote: (id: string) =>
    unwrap(client.POST('/api/hub/admins/{user_id}/demote', { params: { path: { user_id: id } } })),
  /** The admin's own tokens for agents (REB-213): newest first with no `q`, revoked
   *  ones included either way; best-match first by name once `q` narrows it, since
   *  REB-313. */
  tokens: (params: ListPageParams = {}) =>
    unwrap(client.GET('/api/hub/tokens', { params: { query: filled(params) } })),
  createToken: (name: string) => unwrap(client.POST('/api/hub/tokens', { body: { name } })),
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
  addNote: (kind: ThreadKind, id: string, text: string) =>
    kind === 'freelancers'
      ? unwrap(
          client.POST('/api/hub/freelancers/{freelancer_id}/notes', {
            params: { path: { freelancer_id: id } },
            body: { text },
          }),
      reading<Note>(),
        )
      : unwrap(
          client.POST('/api/hub/companies/{company_id}/notes', {
            params: { path: { company_id: id } },
            body: { text },
          }),
      reading<Note>(),
        ),
  /** Replaces the text of a note an admin may edit; refused for the person's own events. */
  editNote: (noteId: string, text: string) =>
    unwrap(
      client.PATCH('/api/hub/notes/{note_id}', { params: { path: { note_id: noteId } }, body: { text } }),
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
  /** The referral ledger (P-REB-44): every reward, newest first. */
  referrals: (params: ReferralLedgerParams = {}) =>
    unwrap(client.GET('/api/hub/referrals', { params: { query: filled(params) } }), reading<ReferralLedgerList>()),
  /** `to_confirm` to `confirmed` to `paid`, one step at a time. */
  setReferralState: (id: string, status: RewardStatus) =>
    unwrap(
      client.POST('/api/hub/referrals/{reward_id}/state', {
        params: { path: { reward_id: id } },
        body: { status },
      }),
      reading<ReferralLedgerItem>(),
    ),
  /** What an admin prices by hand for a reward left with no figure (a `lump_sum`
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
  aiTest: (task: string) =>
    unwrap(client.POST('/api/hub/admin/ai/tasks/{task}/test', { params: { path: { task } } })),
}

// ---- «Modelli AI» (REB-833) -------------------------------------------------------------

export type AiProvider = Schemas['ProviderRead']
export type AiTask = Schemas['TaskRead']
export type AiSourceSwitch = Schemas['SourceSwitchRead']
export type AiStaleCards = Schemas['StaleCardsRead']
export type AiConfig = Schemas['AiConfigRead']
export type AiTest = Schemas['TestRead']

/** One entry of the «Registro» of «Modelli AI» (REB-861): what an admin changed in the
 *  configuration and every regeneration of the cards, its start and its end. The payloads
 *  carry counts, costs and the people's ids, never a name or a card's text. */
export type AiAuditKind = AdminActionKind

export type AiAuditAction = Schemas['AdminActionRead']

// ---- whoever is signed in --------------------------------------------------------------

export type MemberRequest = Schemas['MemberRequest']
export type Me = Schemas['MeRead']
export type MemberUpdate = Schemas['MemberUpdate']
export type CompanyUpdate = Schemas['CompanyUpdate']
export type MemberReferral = Schemas['MemberReferral']

// ---- referrals (P-REB-44) --------------------------------------------------------------

export type RewardStatus = 'to_confirm' | 'confirmed' | 'paid'
export type ReferralStatus = 'to_verify' | 'verified'
export type ReferralVerifiedVia = 'access' | 'letter' | 'history'
export type ReferralLedgerItem = Schemas['ReferralLedgerItem']
export type ReferralLedgerList = Schemas['ReferralLedgerList']
export type ReferralSettings = Schemas['ReferralSettingsRead']

export interface ReferralLedgerParams {
  status?: RewardStatus
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
  /** The caller's whole list of extra contact points, replaced (REB-944). The sign-in address
   *  is not in it. */
  updateContactPoints: (contactPoints: ContactPointWrite[]) =>
    unwrap(client.PUT('/api/hub/me/contact-points', { body: { contact_points: contactPoints } }), reading<Me>()),
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

export type CloudTalent = Schemas['CloudTalentRead']
export type CloudTalentList = Schemas['CloudTalentList']

/** The cloud's filters: a role of `roles`, a seniority, one skill, a work mode, and a
 *  band of the client's price per day in whole euro (its bottom and its top). */
export interface CloudFilters {
  role?: string
  seniority?: string
  skill?: string
  mode?: string
  min_band?: number
  max_band?: number
}

/** One person of the builder's proposal in the cloud: the admin's shape and the name,
 *  which the cloud shows (spec § 4.2) and the public read never carries. */
export type CloudTeamMember = Narrow<TeamMember, { first_name: string; last_name: string }>
export type CloudTeamProposal = Narrow<TeamProposal, { team: CloudTeamMember[] }>

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
    unwrap(client.POST('/api/hub/campaigns/unsubscribe', { params: { query: { t } } })),
  confirmAddress: (t: string) =>
    unwrap(client.POST('/api/hub/campaigns/confirm', { params: { query: { t } } })),
}
