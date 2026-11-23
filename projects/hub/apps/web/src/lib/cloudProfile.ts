import type { CloudTalent, Freelancer, FreelancerCard, WorkProfileAdmin } from './api'

/** `view.SUMMARY_CLOUD_MAX` in core: the summary a company reads is cut here. */
export const SUMMARY_CLOUD_MAX = 600

type CloudProfile = NonNullable<CloudTalent['work_profile']>

/** Core's `view._cut`: the text cut at a word boundary within `limit` characters, with an ellipsis. */
export function cutSummary(text: string, limit: number = SUMMARY_CLOUD_MAX): string {
  if (text.length <= limit) return text
  const head = text.slice(0, limit)
  const space = head.lastIndexOf(' ')
  return `${(space === -1 ? head : head.slice(0, space)).replace(/[ ,;:.]+$/, '')}…`
}

/**
 * What a company reads of an approved profile, derived from the admin's read exactly as
 * core's `cloud_work_profile` does: the summary (cut), the domains' labels with their counts
 * and each project's abstract domain once, and nothing a guard hit named. The admin page
 * draws the cloud's own card from this, so what it shows is what the cloud would send.
 */
export function cloudWorkProfile(read: WorkProfileAdmin): CloudProfile {
  const flagged = new Set(read.guard_hits.map((hit) => hit.field))
  const view = read.profile
  const domains = view.domains
    .filter((domain) => domain.label && !flagged.has(domain.where.replace('.note', '.label')))
    .map((domain) => ({ label: domain.label, products: domain.products, sessions: domain.sessions }))
  const seen = new Set<string>()
  for (const project of view.projects) {
    if (project.domain && !flagged.has(`${project.id}.domain`)) seen.add(project.domain.trim())
  }
  return {
    summary: view.summary && !flagged.has('summary') ? cutSummary(view.summary) : null,
    domains,
    project_domains: [...seen],
  }
}

/** The talent as the cloud read would return them, for the admin's preview: the card, the
 *  mode and the band the card read carries, and the three profile fields above. `null` while
 *  no card is written, since the cloud lists nobody without one. */
export function cloudTalent(freelancer: Freelancer, read: FreelancerCard, profile: WorkProfileAdmin): CloudTalent | null {
  if (!read.card) return null
  return {
    freelancer_id: freelancer.id,
    first_name: freelancer.first_name,
    last_name: freelancer.last_name,
    linkedin_url: freelancer.linkedin_url ?? null,
    links: freelancer.links,
    vetted: freelancer.vetted_at !== null,
    card: read.card,
    mode: read.mode,
    band: read.band,
    has_cv: freelancer.cv_filename !== null,
    work_profile: cloudWorkProfile(profile),
  }
}
