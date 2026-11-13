import type { ThreadKind } from './api'

/** Every React Query key the admin and member pages use, in one place, so a fetch and
 *  the invalidation of what it read cannot drift apart by a typo. A factory called
 *  without its filters is the prefix that every key of that list starts with, which is
 *  what `invalidateQueries` wants. */
export const keys = {
  loginStats: (q?: string) => (q === undefined ? (['login-stats'] as const) : (['login-stats', q] as const)),
  admins: (q?: string) => (q === undefined ? (['admins'] as const) : (['admins', q] as const)),
  tokens: (q?: string) => (q === undefined ? (['tokens'] as const) : (['tokens', q] as const)),
  pigroSpaces: (q: string) => ['pigro-spaces', q] as const,
  guideStats: ['guide-stats'] as const,

  freelancer: (id: string) => ['freelancer', id] as const,
  /** Every freelancer detail: what a merge changes at once. */
  freelancerAll: ['freelancer'] as const,
  freelancerCard: (id: string) => ['freelancer-card', id] as const,
  talents: (filters?: object) => (filters ? (['talents', filters] as const) : (['talents'] as const)),
  signup: (id: string) => ['signup', id] as const,

  companies: (filters?: object) => (filters ? (['companies', filters] as const) : (['companies'] as const)),
  /** The company picker of «Crea match». */
  companiesForEngagement: (term: string) => ['companies', 'engagement', term] as const,
  company: (id: string) => ['company', id] as const,

  audit: (kind: ThreadKind, id: string) => ['audit', kind, id] as const,
  /** Every trail: what a merge writes into at once. */
  auditAll: ['audit'] as const,

  campaigns: ['campaigns'] as const,
  campaign: (id: string | null | undefined) => ['campaign', id] as const,
  campaignTemplates: ['campaignTemplates'] as const,
  campaignAudience: (id: string | undefined, source: unknown) => ['campaignAudience', id, source] as const,

  referrals: (status?: string) => (status === undefined ? (['referrals'] as const) : (['referrals', status] as const)),
  referralSettings: ['referral-settings'] as const,

  teamRequest: (id: string) => ['team-request', id] as const,
  teamRequests: (filters?: object) =>
    filters ? (['team-requests', filters] as const) : (['team-requests'] as const),
  teamProposals: (filters: object) => ['team-proposals', filters] as const,

  aiConfig: ['ai-config'] as const,
  /** A prefix of `aiConfig`, so a change of the configuration reads the trail again too. */
  aiAudit: ['ai-config', 'audit'] as const,
  aiStaleCards: ['ai-stale-cards'] as const,

  cloudTalents: (filters: object) => ['cloud', 'talents', filters] as const,
  memberContracts: ['me', 'contracts'] as const,
  memberReferral: ['me', 'referral'] as const,
}
