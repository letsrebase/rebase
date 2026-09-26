import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from '@tanstack/react-router'
import type { CompaniesFilters, MatchesFilters, Remoto, TalentiFilters } from '@/lib/api'
import { Shell } from '@/components/Shell'
import { Chooser } from '@/pages/Chooser'
import { CompanyWizard } from '@/pages/CompanyWizard'
import { FreelancerWizard } from '@/pages/FreelancerWizard'
import { SignedInLayout } from '@/pages/SignedInLayout'
import { Team } from '@/pages/Team'
import { AdminAccessi } from '@/pages/admin/Accessi'
import { AdminAdmins } from '@/pages/admin/Admins'
import { AdminAgenti } from '@/pages/admin/Agenti'
import { AdminGuard } from '@/pages/admin/AdminGuard'
import { AdminGuida } from '@/pages/admin/Guida'
import { AdminPigro } from '@/pages/admin/Pigro'
import { AdminContratti } from '@/pages/admin/Contratti'
import { AdminCampagna } from '@/pages/admin/Campagna'
import { AdminCampagne } from '@/pages/admin/Campagne'
import { AdminCreaCampagna } from '@/pages/admin/CreaCampagna'
import { AdminCreaMatch } from '@/pages/admin/CreaMatch'
import { AdminMatches } from '@/pages/admin/Matches'
import { Disiscrizione } from '@/pages/Disiscrizione'
import { Thanks } from '@/pages/Thanks'
import {
  AdminCompanies,
  AdminCompanyDetail,
  AdminFreelancerDetail,
  AdminTalenti,
  AdminTalentoLead,
} from '@/pages/admin/lists'
import { Accedi } from '@/pages/member/Accedi'
import { Area } from '@/pages/member/Area'
import { Entra } from '@/pages/member/Entra'
import { Modifica } from '@/pages/member/Modifica'
import { ModificaAzienda } from '@/pages/member/ModificaAzienda'
import { NuovaRichiestaAzienda } from '@/pages/member/NuovaRichiestaAzienda'

/** A present, non-empty string out of `Record<string, unknown>`'s raw search params,
 *  or `undefined` -- the shape every optional filter on `/admin/talent` and
 *  `/admin/companies` shares (REB-286), the same narrowing `thanks`'s `chi` and
 *  `verify`'s `t` do below for their own single required param.
 *
 *  The router's default `parseSearch` runs `JSON.parse` on every raw query-string
 *  value before `validateSearch` sees it, so a purely numeric value in the URL
 *  (`?tariffa_min=50`) or a bare `true`/`false` arrives as that JS type, not a
 *  string -- on first load, a reload, a shared link, or back/forward, never on an
 *  in-app `navigate()`, which is why this only shows up outside the tab that set it.
 *  Coerced back to the string it was in the URL, the same treatment the `has_cv`/
 *  `con_accessi` booleans below already needed for the same reason. */
export function strParam(value: unknown): string | undefined {
  if (typeof value === 'string') return value !== '' ? value : undefined
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return undefined
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
// so a campaign link at the old `/aziende` path has to carry its query string across,
// not just resolve to the right page.
const companiesRedirect = createRoute({
  getParentRoute: () => publicLayout,
  path: '/aziende',
  beforeLoad: () => {
    throw redirect({ to: '/companies', search: true })
  },
})
// P-REB-43: the public team builder, with the wizards' chrome and no login.
const team = createRoute({ getParentRoute: () => publicLayout, path: '/team', component: Team })
const thanks = createRoute({
  getParentRoute: () => publicLayout,
  path: '/thanks',
  validateSearch: (search: Record<string, unknown>): { chi: 'freelance' | 'azienda' } => ({
    chi: search.chi === 'azienda' ? 'azienda' : 'freelance',
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
const login = createRoute({ getParentRoute: () => publicLayout, path: '/login', component: Accedi })
// Outreach mails link `/accedi?utm_...` (REB-426), and the login page sends that query
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
  component: Entra,
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

const unsubscribe = createRoute({
  getParentRoute: () => publicLayout,
  path: '/disiscrizione',
  validateSearch: (search: Record<string, unknown>): { t: string } => ({ t: typeof search.t === 'string' ? search.t : '' }),
  component: Disiscrizione,
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
  validateSearch: (search: Record<string, unknown>): { negato?: true } => ({
    negato: search.negato === true || search.negato === 'true' ? true : undefined,
  }),
})
const meEdit = createRoute({ getParentRoute: () => me, path: '/edit', component: Modifica })
const meEditCompany = createRoute({
  getParentRoute: () => me,
  path: '/edit-company',
  component: ModificaAzienda,
})
const meNewCompany = createRoute({
  getParentRoute: () => me,
  path: '/new-company',
  component: NuovaRichiestaAzienda,
})
// `/io`, `/io/modifica` and `/io/modifica-azienda` each renamed their own segment, not
// just the shared `/io` prefix, so a deep link to any of the three needs its own
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
  component: AdminTalenti,
  // REB-286: every filter and the search box live here too, so a reload or a shared
  // link reproduces the exact list -- `q` included even though the debounce that
  // settles it lives in `AdminTalenti` itself, not here.
  validateSearch: (search: Record<string, unknown>): TalentiFilters => ({
    stato: strParam(search.stato),
    q: strParam(search.q),
    posizione: strParam(search.posizione),
    remoto:
      search.remoto === 'remoto' || search.remoto === 'ibrido' || search.remoto === 'in_sede'
        ? (search.remoto as Remoto)
        : undefined,
    tariffa_min: strParam(search.tariffa_min),
    tariffa_max: strParam(search.tariffa_max),
    origine: strParam(search.origine),
    utm_source: strParam(search.utm_source),
    has_cv: search.has_cv === true || search.has_cv === 'true' ? true : search.has_cv === false || search.has_cv === 'false' ? false : undefined,
    con_accessi:
      search.con_accessi === true || search.con_accessi === 'true'
        ? true
        : search.con_accessi === false || search.con_accessi === 'false'
          ? false
          : undefined,
    creato_da: strParam(search.creato_da),
    creato_a: strParam(search.creato_a),
  }),
})
// Every filter on the old list has to survive the redirect too, or a saved/shared
// filtered view at `/admin/talenti?...` would silently reset once it lands.
const adminTalentRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/talenti',
  beforeLoad: () => {
    throw redirect({ to: '/admin/talent', search: true })
  },
})
const adminTalentLead = createRoute({
  getParentRoute: () => adminArea,
  path: '/talent/$id',
  component: AdminTalentoLead,
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
  component: AdminContratti,
})
// REB-387, REB-476: the three-step «Crea match», from the talent row's menu and the contracts page.
const adminFreelanceMatchNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/freelance/$id/match/new',
  component: AdminCreaMatch,
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
    stato: strParam(search.stato),
    q: strParam(search.q),
  }),
})
// P-REB-41: the list, a stub for the new/edit form (Task 20) and a stub for the
// detail (Task 21). `adminCampaignNew` sits before `adminCampaign` in the tree below --
// TanStack ranks a static segment over `$id` either way, but the list reads in the
// order a person follows.
const adminCampaigns = createRoute({ getParentRoute: () => adminArea, path: '/campaigns', component: AdminCampagne })
const adminCampaignNew = createRoute({
  getParentRoute: () => adminArea,
  path: '/campaigns/new',
  component: AdminCreaCampagna,
})
const adminCampaign = createRoute({ getParentRoute: () => adminArea, path: '/campaigns/$id', component: AdminCampagna })
const adminCampaignEdit = createRoute({
  getParentRoute: () => adminArea,
  path: '/campaigns/$id/edit',
  component: AdminCreaCampagna,
})
const adminCompanies = createRoute({
  getParentRoute: () => adminArea,
  path: '/companies',
  component: AdminCompanies,
  validateSearch: (search: Record<string, unknown>): CompaniesFilters => ({
    stato: strParam(search.stato),
    q: strParam(search.q),
    budget_min: strParam(search.budget_min),
    budget_max: strParam(search.budget_max),
    periodo_da: strParam(search.periodo_da),
    origine: strParam(search.origine),
    creato_da: strParam(search.creato_da),
    creato_a: strParam(search.creato_a),
  }),
})
const adminCompaniesRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/aziende',
  beforeLoad: () => {
    throw redirect({ to: '/admin/companies', search: true })
  },
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
const adminPigro = createRoute({ getParentRoute: () => adminArea, path: '/pigro', component: AdminPigro })
const adminGuide = createRoute({ getParentRoute: () => adminArea, path: '/guide', component: AdminGuida })
const adminGuideRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/guida',
  beforeLoad: () => {
    throw redirect({ to: '/admin/guide' })
  },
})
const adminAccess = createRoute({ getParentRoute: () => adminArea, path: '/access', component: AdminAccessi })
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
const adminAgents = createRoute({ getParentRoute: () => adminArea, path: '/agents', component: AdminAgenti })
const adminAgentsRedirect = createRoute({
  getParentRoute: () => adminArea,
  path: '/agenti',
  beforeLoad: () => {
    throw redirect({ to: '/admin/agents' })
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
    thanks,
    thanksRedirect,
    login,
    loginRedirect,
    verify,
    verifyRedirect,
    unsubscribe,
  ]),
  signedInLayout.addChildren([
    me.addChildren([meIndex, meEdit, meEditCompany, meNewCompany]),
    meRedirect,
    meEditRedirect,
    meEditCompanyRedirect,
    adminArea.addChildren([
      adminIndexRedirect,
      adminTalent,
      adminTalentRedirect,
      adminTalentLead,
      adminTalentLeadRedirect,
      adminFreelanceDetail,
      adminFreelanceContracts,
      adminFreelanceMatchNew,
      adminFreelanceRedirect,
      adminMatches,
      adminCampaigns,
      adminCampaignNew,
      adminCampaign,
      adminCampaignEdit,
      adminCompanies,
      adminCompaniesRedirect,
      adminCompaniesDetail,
      adminCompaniesDetailRedirect,
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

export const router = createRouter({ routeTree, basepath: '/hub' })

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
