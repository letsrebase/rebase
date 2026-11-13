import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from '@tanstack/react-router'
import type {
  CompaniesFilters,
  MatchesFilters,
  Remote,
  TalentsFilters,
  TeamProposalsFilters,
  TeamRequestsFilters,
} from '@/lib/api'
import { periodParam } from '@/lib/report'
import { parseSearch } from '@/lib/search'
import { Shell } from '@/components/Shell'
import { Chooser } from '@/pages/Chooser'
import { SignedInLayout } from '@/pages/SignedInLayout'
import { modernSearch, searchParam } from '@/lib/legacy'
import { Thanks } from '@/pages/Thanks'

// Every page but the chooser and the two frames is its own chunk: an anonymous visitor on
// the wizard does not download the admin area (REB-890).
const CompanyWizard = lazyRouteComponent(() => import('@/pages/CompanyWizard'), 'CompanyWizard')
const FreelancerWizard = lazyRouteComponent(() => import('@/pages/FreelancerWizard'), 'FreelancerWizard')
const Team = lazyRouteComponent(() => import('@/pages/Team'), 'Team')
const TeamAnswer = lazyRouteComponent(() => import('@/pages/TeamAnswer'), 'TeamAnswer')
const AdminLogins = lazyRouteComponent(() => import('@/pages/admin/Logins'), 'AdminLogins')
const AdminAdmins = lazyRouteComponent(() => import('@/pages/admin/Admins'), 'AdminAdmins')
const AdminAgents = lazyRouteComponent(() => import('@/pages/admin/Agents'), 'AdminAgents')
const AdminAiModels = lazyRouteComponent(() => import('@/pages/admin/AiModels'), 'AdminAiModels')
const AdminGuard = lazyRouteComponent(() => import('@/pages/admin/AdminGuard'), 'AdminGuard')
const AdminGuide = lazyRouteComponent(() => import('@/pages/admin/Guide'), 'AdminGuide')
const AdminPigro = lazyRouteComponent(() => import('@/pages/admin/Pigro'), 'AdminPigro')
const AdminMatchReport = lazyRouteComponent(() => import('@/pages/admin/MatchReport'), 'AdminMatchReport')
const AdminContracts = lazyRouteComponent(() => import('@/pages/admin/Contracts'), 'AdminContracts')
const AdminCampaign = lazyRouteComponent(() => import('@/pages/admin/Campaign'), 'AdminCampaign')
const AdminCampaigns = lazyRouteComponent(() => import('@/pages/admin/Campaigns'), 'AdminCampaigns')
const AdminCreateCampaign = lazyRouteComponent(() => import('@/pages/admin/CreateCampaign'), 'AdminCreateCampaign')
const AdminCreateCompany = lazyRouteComponent(() => import('@/pages/admin/CreateCompany'), 'AdminCreateCompany')
const AdminOpenEngagement = lazyRouteComponent(() => import('@/pages/admin/OpenEngagement'), 'AdminOpenEngagement')
const AdminDuplicates = lazyRouteComponent(() => import('@/pages/admin/Duplicates'), 'AdminDuplicates')
const AdminMatches = lazyRouteComponent(() => import('@/pages/admin/Matches'), 'AdminMatches')
const AdminReferrals = lazyRouteComponent(() => import('@/pages/admin/Referrals'), 'AdminReferrals')
const AdminTeamProposals = lazyRouteComponent(() => import('@/pages/admin/TeamProposals'), 'AdminTeamProposals')
const AdminTeamRequest = lazyRouteComponent(() => import('@/pages/admin/TeamRequest'), 'AdminTeamRequest')
const AdminTeamRequests = lazyRouteComponent(() => import('@/pages/admin/TeamRequests'), 'AdminTeamRequests')
const ConfirmAddress = lazyRouteComponent(() => import('@/pages/ConfirmAddress'), 'ConfirmAddress')
const Unsubscribe = lazyRouteComponent(() => import('@/pages/Unsubscribe'), 'Unsubscribe')
const AdminCompanies = lazyRouteComponent(() => import('@/pages/admin/companies/CompanyList'), 'AdminCompanies')
const AdminCompanyDetail = lazyRouteComponent(() => import('@/pages/admin/companies/CompanyDetail'), 'AdminCompanyDetail')
const AdminFreelancerDetail = lazyRouteComponent(() => import('@/pages/admin/freelancer/FreelancerDetail'), 'AdminFreelancerDetail')
const AdminTalents = lazyRouteComponent(() => import('@/pages/admin/talent/TalentList'), 'AdminTalents')
const AdminTalentLead = lazyRouteComponent(() => import('@/pages/admin/talent/LeadForm'), 'AdminTalentLead')
const Login = lazyRouteComponent(() => import('@/pages/member/Login'), 'Login')
const Area = lazyRouteComponent(() => import('@/pages/member/Area'), 'Area')
const Cloud = lazyRouteComponent(() => import('@/pages/member/Cloud'), 'Cloud')
const Verify = lazyRouteComponent(() => import('@/pages/member/Verify'), 'Verify')
const Edit = lazyRouteComponent(() => import('@/pages/member/Edit'), 'Edit')
const EditCompany = lazyRouteComponent(() => import('@/pages/member/EditCompany'), 'EditCompany')
const NewCompanyRequest = lazyRouteComponent(() => import('@/pages/member/NewCompanyRequest'), 'NewCompanyRequest')

/** A present, non-empty string out of `Record<string, unknown>`'s raw search params,
 *  or `undefined` -- the shape every optional filter on `/admin/talent` and
 *  `/admin/companies` shares (REB-286), the same narrowing `thanks`'s `who` and
 *  `verify`'s `t` do below for their own single required param.
 *
 *  The router's `parseSearch` (`lib/search.ts`) runs `JSON.parse` on every raw
 *  query-string value but the amount filters before `validateSearch` sees it, so a
 *  purely numeric value in the URL (`?posizione=50`) or a bare `true`/`false` arrives
 *  as that JS type, not a string -- on first load, a reload, a shared link, or
 *  back/forward, never on an in-app `navigate()`, which is why this only shows up
 *  outside the tab that set it. Coerced back to the string it was in the URL, the same
 *  treatment the `has_cv`/`with_logins` booleans below already needed for the same
 *  reason. */
export function strParam(value: unknown): string | undefined {
  if (typeof value === 'string') return value !== '' ? value : undefined
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return undefined
}

/** The search schema of `/admin/talent` (REB-286): every filter and the search box live in the
 *  URL, so a reload or a shared link reproduces the exact list -- `q` included even though the
 *  debounce that settles it lives in `AdminTalents` itself. Exported so the admin list tests'
 *  router (`list-test-kit.tsx`) reads the route's own schema instead of keeping a copy that
 *  every new filter would have to join (REB-983). */
export function validateTalentsSearch(search: Record<string, unknown>): TalentsFilters {
  return {
    status: strParam(search.status),
    q: strParam(search.q),
    job_title: strParam(search.job_title),
    remote:
      search.remote === 'remote' || search.remote === 'hybrid' || search.remote === 'on_site'
        ? (search.remote as Remote)
        : undefined,
    min_rate: strParam(search.min_rate),
    max_rate: strParam(search.max_rate),
    origin_page: strParam(search.origin_page),
    utm_source: strParam(search.utm_source),
    has_cv: search.has_cv === true || search.has_cv === 'true' ? true : search.has_cv === false || search.has_cv === 'false' ? false : undefined,
    with_logins:
      search.with_logins === true || search.with_logins === 'true'
        ? true
        : search.with_logins === false || search.with_logins === 'false'
          ? false
          : undefined,
    created_from: strParam(search.created_from),
    created_to: strParam(search.created_to),
    skills: strParam(search.skills),
    location: strParam(search.location),
    seniority: strParam(search.seniority),
    min_years: strParam(search.min_years),
    max_years: strParam(search.max_years),
    industries: strParam(search.industries),
    languages: strParam(search.languages),
    card: strParam(search.card),
    sort: strParam(search.sort),
  }
}

/** The search schema of `/admin/companies` (REB-286), exported for the same reason as
 *  `validateTalentsSearch`. */
export function validateCompaniesSearch(search: Record<string, unknown>): CompaniesFilters {
  return {
    status: strParam(search.status),
    q: strParam(search.q),
    budget_min: strParam(search.budget_min),
    budget_max: strParam(search.budget_max),
    period_from: strParam(search.period_from),
    origin: strParam(search.origin),
    created_from: strParam(search.created_from),
    created_to: strParam(search.created_to),
  }
}

/**
 * The route tree, in code: this many screens is not enough to want a file-based router and
 * a generated tree beside it. The public pages sit in the `Shell`, the chooser alone in
 * a `Shell` without its panel (ORB-128); every signed-in route -- the member area and the
 * admin area alike -- shares one guard and one frame, `SignedInLayout` (REB-279, merging
 * the old `AdminLayout` and `MemberGuard`). `/admin/*` carries one more guard of its own,
 * `AdminGuard`, gating on role rather than on being signed in at all: a signed-in
 * non-admin at `/admin/*` lands on `/me` with a sentence, never on a blank frame
 * (REB-106) or a login form.
 *
 * REB-319 retrofits every Italian route segment to English. Each renamed browser route
 * keeps a `beforeLoad` redirect at its old path, the same shape `adminFreelanceRedirect`
 * already used for `/admin/freelance` -> `/admin/talent` (REB-283, REB-319): so a bookmark, a
 * mailed magic link or a shared URL at the old path never breaks. A route whose old path
 * carried a required or UTM-bearing search param forwards it with `search: true`, since
 * TanStack Router's redirect does not carry the query string over on its own.
 */
const root = createRootRoute({ component: () => <Outlet /> })

const publicLayout = createRoute({
  getParentRoute: () => root,
  id: 'public',
  component: () => (
    <Shell>
      <Outlet />
    </Shell>
  ),
})

const bareLayout = createRoute({
  getParentRoute: () => root,
  id: 'bare',
  component: () => (
    <Shell panel={false}>
      <Outlet />
    </Shell>
  ),
})

const chooser = createRoute({ getParentRoute: () => bareLayout, path: '/', component: Chooser })
const freelance = createRoute({
  getParentRoute: () => publicLayout,
  path: '/freelance',
  component: FreelancerWizard,
})
const companies = createRoute({
  getParentRoute: () => publicLayout,
  path: '/companies',
  component: CompanyWizard,
})
// The wizard reads UTM straight off the URL's own search string (`CompanyWizard.tsx`),
// so a campaign link at the old `/companies` path has to carry its query string across,
// not just resolve to the right page.
const companiesRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/aziende',
  beforeLoad: () => {
    throw redirect({ to: '/companies', search: true })
  },
})
// P-REB-43: the public team builder, with the wizards' chrome and no login.
// REB-675: `proposal` opens the page on a proposal made elsewhere, the landing's hero
// above all, and `description` fills the box alone, which is what the landing's form
// sends without JavaScript. Either left out is left out, never an empty string.
const team = createRoute({
  getParentRoute: () => publicLayout,
  path: '/team',
  validateSearch: (
    search: Record<string, unknown>,
  ): { proposal?: string; description?: string; people?: number } => {
    // The links made before the 2026-11 rename (`proposta`, `descrizione`, `persone`) are
    // accepted for good: the landing's form and the mails already sent carry them.
    const proposal = strParam(searchParam(search, 'proposal', 'proposta'))
    const description = strParam(searchParam(search, 'description', 'descrizione'))
    // The headcount the landing's form sends beside the description: 1 to 10, whole.
    const people = Number(strParam(searchParam(search, 'people', 'persone')))
    return {
      ...(proposal ? { proposal } : {}),
      ...(description ? { description } : {}),
      ...(Number.isInteger(people) && people >= 1 && people <= 10 ? { people } : {}),
    }
  },
  component: Team,
})
// REB-517: where a talent answers the availability mail, public like the page above; the
// page reads `t` and `r` itself and posts nothing until «Conferma».
const teamAnswer = createRoute({
  getParentRoute: () => publicLayout,
  path: '/team/answer',
  validateSearch: (search: Record<string, unknown>): { t: string; r: string } => ({
    t: strParam(search.t) ?? '',
    r: strParam(search.r) ?? '',
  }),
  component: TeamAnswer,
})
const thanks = createRoute({
  getParentRoute: () => publicLayout,
  path: '/thanks',
  validateSearch: (search: Record<string, unknown>): { who: 'freelance' | 'company' } => ({
    // `chi=azienda` is what the wizards and the mails said before the 2026-11 rename.
    who: searchParam(search, 'who', 'chi') === 'company' || searchParam(search, 'who', 'chi') === 'azienda' ? 'company' : 'freelance',
  }),
  component: Thanks,
})
const thanksRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/grazie',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/thanks', search: () => search as never })
  },
})
const login = createRoute({ getParentRoute: () => publicLayout, path: '/login', component: Login })
// Outreach mails link `/login?utm_...` (REB-426), and the login page sends that query
// string with the address so the login records which mail it came from: the redirect
// carries it across, the way `companiesRedirect` does for the wizard.
const loginRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/accedi',
  beforeLoad: () => {
    throw redirect({ to: '/login', search: true })
  },
})
const verify = createRoute({
  getParentRoute: () => publicLayout,
  path: '/verify',
  validateSearch: (search: Record<string, unknown>): { t: string } => ({
    t: typeof search.t === 'string' ? search.t : '',
  }),
  component: Verify,
})
// The token lives in the query string (`?t=`): every magic-link mail already sent
// points at the old path, so this redirect has to carry `t` across or the link inside
// it would stop working the moment this ships.
const verifyRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/entra',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/verify', search: () => search as never })
  },
})

const confirmAddress = createRoute({
  getParentRoute: () => publicLayout,
  path: '/confirm',
  validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  component: ConfirmAddress,
})

const unsubscribe = createRoute({
  getParentRoute: () => publicLayout,
  path: '/unsubscribe',
  validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  component: Unsubscribe,
})

// Old path support (the Italian page names retired in the English rename): a mail that was
// sent before still carries these two links, so they keep answering. `t` (and `r`) go along.
const unsubscribeRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/disiscrizione',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/unsubscribe', search: () => search as never })
  },
})
const teamAnswerRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/team/risposta',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/team/answer', search: () => search as never })
  },
})

const signedInLayout = createRoute({
  getParentRoute: () => root,
  id: 'signedIn',
  component: SignedInLayout,
})

const me = createRoute({ getParentRoute: () => signedInLayout, path: '/me', component: () => <Outlet /> })
const meIndex = createRoute({
  getParentRoute: () => me,
  path: '/',
  component: Area,
  // Set by `AdminGuard` when a signed-in non-admin is bounced off `/admin/*`
  // (REB-279's own access rule): `Area` reads it to say why, rather than a blank
  // screen or a raw 403.
  validateSearch: (search: Record<string, unknown>): { denied?: true } => ({
    denied: search.denied === true || search.denied === 'true' ? true : undefined,
  }),
})
const meEdit = createRoute({ getParentRoute: () => me, path: '/edit', component: Edit })
const meEditCompany = createRoute({
  getParentRoute: () => me,
  path: '/edit-company',
  component: EditCompany,
})
// One request by its id (REB-602): every request the member area lists has its own
// «Modifica». `/edit-company` with no id stays, on the same page, for the newest one.
const meEditCompanyById = createRoute({
  getParentRoute: () => me,
  path: '/edit-company/$id',
  component: EditCompany,
})
const meNewCompany = createRoute({
  getParentRoute: () => me,
  path: '/new-company',
  component: NewCompanyRequest,
})
// The talent cloud (REB-519, spec § 4.2): REB-518 opens its door from «Aziende», and the
// nav's «Talent cloud» leads here while a grant of the person is live.
const meCloud = createRoute({ getParentRoute: () => me, path: '/cloud', component: Cloud })
// `/me`, `/me/edit` and `/me/edit-company` each renamed their own segment, not
// just the shared `/me` prefix, so a deep link to any of the three needs its own
// redirect: TanStack Router does not cascade a parent's rename onto a child route that
// renamed its own segment too.
const meRedirect = createRoute({
  getParentRoute: () => signedInLayout,
  path: '/io',
  beforeLoad: () => {
    throw redirect({ to: '/me', search: true })
  },
})
const meEditRedirect = createRoute({
  getParentRoute: () => signedInLayout,
  path: '/io/modifica',
  beforeLoad: () => {
    throw redirect({ to: '/me/edit' })
  },
})
const meEditCompanyRedirect = createRoute({
  getParentRoute: () => signedInLayout,
  path: '/io/modifica-azienda',
  beforeLoad: () => {
    throw redirect({ to: '/me/edit-company' })
  },
})

const adminArea = createRoute({ getParentRoute: () => signedInLayout, path: '/admin', component: AdminGuard })
// A signed-in admin lands here bare: `landingRoute()` in the website's `session.js`
// sends the marketing site's "Accedi" link straight to `/hub/admin` for anyone whose
// session already says `role === 'admin'`, and this router had no page for `/admin`
// alone -- AdminGuard's own `<Outlet />` renders nothing for it (the "frame with an
// empty panel" ORB-106 already named, until now only patched for the old
// `/admin/freelance` address below). Talent is the same sensible default
// `adminFreelanceRedirect` already uses.
const adminIndexRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/admin/talent' })
  },
})
const adminTalent = createRoute({
  getParentRoute: () => adminArea,
  path: '/talent',
  component: AdminTalents,
  // REB-286: every filter and the search box live here too, so a reload or a shared
  // link reproduces the exact list -- `q` included even though the debounce that
  // settles it lives in `AdminTalents` itself, not here.
  validateSearch: validateTalentsSearch,
})
// Every filter on the old list has to survive the redirect too, or a saved/shared
// filtered view at `/admin/talent?...` would silently reset once it lands.
const adminTalentRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/talenti',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/admin/talent', search: () => modernSearch(search) as never })
  },
})
// REB-800: the people who are on the list twice, from the link on Talenti. A static
// segment, so it wins over `/talent/$id` below.
const adminTalentDuplicates = createRoute({
  getParentRoute: () => adminArea,
  path: '/talent/duplicates',
  component: AdminDuplicates,
})
const adminTalentLead = createRoute({
  getParentRoute: () => adminArea,
  path: '/talent/$id',
  component: AdminTalentLead,
})
const adminTalentLeadRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/talenti/$id',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/admin/talent/$id', params })
  },
})
const adminFreelanceDetail = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id',
  component: AdminFreelancerDetail,
})
// REB-387: a card's matches and contracts, from the talent row's menu and the card's header.
const adminFreelanceContracts = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id/contracts',
  component: AdminContracts,
})
// REB-1105: «Crea match», from the talent row's menu and the contracts page, hands the pair over to the CRM's engagement form.
const adminFreelanceMatchNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id/match/new',
  component: AdminOpenEngagement,
})
// The website's footer links to /hub/admin/freelance (ORB-106: before
// `adminIndexRedirect` above existed, the hub router had no index route under /admin
// at all, so even a signed-in admin sent to a bare /admin saw the frame with an empty
// panel). Talent replaced the list this used to be (REB-283, then REB-319 for the
// English path); the redirect keeps that one documented door open rather than 404ing it.
const adminFreelanceRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance',
  beforeLoad: () => {
    throw redirect({ to: '/admin/talent' })
  },
})
// REB-413: the last addition to the milestone, listing every match the other admin
// pages create rather than any one card's or request's own.
const adminMatches = createRoute({
  getParentRoute: () => adminArea,
  path: '/matches',
  component: AdminMatches,
  validateSearch: (search: Record<string, unknown>): MatchesFilters => ({
    status: strParam(search.status),
    q: strParam(search.q),
  }),
})
// REB-514: «Richieste team», the requests the team builder files, and one request's
// page. The state pill lives in the URL, as «Match»'s does.
const adminTeamRequests = createRoute({
  getParentRoute: () => adminArea,
  path: '/team',
  component: AdminTeamRequests,
  validateSearch: (search: Record<string, unknown>): TeamRequestsFilters => ({
    status: strParam(search.status),
  }),
})
const adminTeamRequest = createRoute({
  getParentRoute: () => adminArea,
  path: '/team/$id',
  component: AdminTeamRequest,
})
// 0028: «Proposte», every «Proponi il team» asked, filed or not; a static segment
// beside `$id`, which the router ranks first. The outcome pill lives in the URL.
const adminTeamProposals = createRoute({
  getParentRoute: () => adminArea,
  path: '/team/proposals',
  component: AdminTeamProposals,
  validateSearch: (search: Record<string, unknown>): TeamProposalsFilters => ({
    outcome: strParam(search.outcome),
  }),
})
// REB-503: «Consuntivo», a match's hours on its deal on Pigro, from the match card and
// the «Pigro» column of «Match». `month` is the period shown, a month as `YYYY-MM` or
// `all` for the whole engagement; absent, or anything else, is the current month.
const adminMatchReport = createRoute({
  getParentRoute: () => adminArea,
  path: '/matches/$id/report',
  component: AdminMatchReport,
  validateSearch: (search: Record<string, unknown>): { month?: string } => ({ month: periodParam(modernSearch(search).month) }),
})
// P-REB-41: the list, a stub for the new/edit form (Task 20) and a stub for the
// detail (Task 21). `adminCampaignNew` sits before `adminCampaign` in the tree below --
// TanStack ranks a static segment over `$id` either way, but the list reads in the
// order a person follows.
const adminCampaigns = createRoute({ getParentRoute: () => adminArea, path: '/campaigns', component: AdminCampaigns })
const adminCampaignNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/campaigns/new',
  component: AdminCreateCampaign,
})
const adminCampaign = createRoute({ getParentRoute: () => adminArea, path: '/campaigns/$id', component: AdminCampaign })
const adminCampaignEdit = createRoute({
  getParentRoute: () => adminArea,
  path: '/campaigns/$id/edit',
  component: AdminCreateCampaign,
})
const adminCompanies = createRoute({
  getParentRoute: () => adminArea,
  path: '/companies',
  component: AdminCompanies,
  validateSearch: validateCompaniesSearch,
})
const adminCompaniesRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/aziende',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/admin/companies', search: () => modernSearch(search) as never })
  },
})
// REB-870: «Nuova richiesta». Static, so it ranks above `/companies/$id`.
const adminCompaniesNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/companies/new',
  component: AdminCreateCompany,
})
const adminCompaniesDetail = createRoute({
  getParentRoute: () => adminArea,
  path: '/companies/$id',
  component: AdminCompanyDetail,
})
const adminCompaniesDetailRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/aziende/$id',
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/admin/companies/$id', params })
  },
})
const adminReferrals = createRoute({
  getParentRoute: () => adminArea,
  path: '/referrals',
  component: AdminReferrals,
})
const adminPigro = createRoute({ getParentRoute: () => adminArea, path: '/pigro', component: AdminPigro })
const adminGuide = createRoute({ getParentRoute: () => adminArea, path: '/guide', component: AdminGuide })
const adminGuideRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/guida',
  beforeLoad: () => {
    throw redirect({ to: '/admin/guide' })
  },
})
const adminAccess = createRoute({ getParentRoute: () => adminArea, path: '/access', component: AdminLogins })
const adminAccessRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/accessi',
  beforeLoad: () => {
    throw redirect({ to: '/admin/access' })
  },
})
const adminAdmins = createRoute({
  getParentRoute: () => adminArea,
  path: '/admins',
  component: AdminAdmins,
})
const adminAdminsRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/amministratori',
  beforeLoad: () => {
    throw redirect({ to: '/admin/admins' })
  },
})
const adminAgents = createRoute({ getParentRoute: () => adminArea, path: '/agents', component: AdminAgents })
const adminModelsAi = createRoute({
  getParentRoute: () => adminArea,
  path: '/ai-models',
  component: AdminAiModels,
})
const adminAgentsRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/agenti',
  beforeLoad: () => {
    throw redirect({ to: '/admin/agents' })
  },
})

// Old path support, same reason as above: the admin pages whose Italian names were retired.
const adminTeamProposalsRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/team/proposte',
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/admin/team/proposals', search: () => modernSearch(search) as never })
  },
})
const adminCompaniesNewRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/companies/nuova',
  beforeLoad: () => {
    throw redirect({ to: '/admin/companies/new' })
  },
})
const adminModelsAiRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/modelli-ai',
  beforeLoad: () => {
    throw redirect({ to: '/admin/ai-models' })
  },
})

// Exported for the tests that drive the real tree (`router.test.tsx`), never for the app.
export const routeTree = root.addChildren([
  bareLayout.addChildren([chooser]),
  publicLayout.addChildren([
    freelance,
    companies,
    companiesRedirect,
    team,
    teamAnswer,
    thanks,
    thanksRedirect,
    login,
    loginRedirect,
    verify,
    verifyRedirect,
    confirmAddress,
    unsubscribe,
    unsubscribeRedirect,
    teamAnswerRedirect,
  ]),
  signedInLayout.addChildren([
    me.addChildren([meIndex, meEdit, meEditCompany, meEditCompanyById, meNewCompany, meCloud]),
    meRedirect,
    meEditRedirect,
    meEditCompanyRedirect,
    adminArea.addChildren([
      adminIndexRedirect,
      adminTalent,
      adminTalentRedirect,
      adminTalentDuplicates,
      adminTalentLead,
      adminTalentLeadRedirect,
      adminFreelanceDetail,
      adminFreelanceContracts,
      adminFreelanceMatchNew,
      adminFreelanceRedirect,
      adminMatches,
      adminMatchReport,
      adminTeamRequests,
      adminTeamRequest,
      adminTeamProposals,
      adminTeamProposalsRedirect,
      adminCampaigns,
      adminCampaignNew,
      adminCampaign,
      adminCampaignEdit,
      adminCompanies,
      adminCompaniesRedirect,
      adminCompaniesNew,
      adminCompaniesNewRedirect,
      adminCompaniesDetail,
      adminCompaniesDetailRedirect,
      adminReferrals,
      adminModelsAi,
      adminModelsAiRedirect,
      adminPigro,
      adminGuide,
      adminGuideRedirect,
      adminAccess,
      adminAccessRedirect,
      adminAdmins,
      adminAdminsRedirect,
      adminAgents,
      adminAgentsRedirect,
    ]),
  ]),
])

export const router = createRouter({ routeTree, basepath: '/hub', parseSearch })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
  interface HistoryState {
    /** A sentence the page navigated to shows on arrival: «Crea match» leaves the send
     *  report's, or that the draft is saved, for «Match e contratti» (REB-476). */
    notice?: string
  }
}
