import { STATE_LABELS, TABS, type EngagementBucket, type EngagementState } from './labels'

/**
 * The search parameters of «Collaboratori» (`/app/engagements`): the tab and the filters travel in the
 * URL, so a bookmark or a link from another page (the registry, a count on the sidebar) lands on
 * the same view. A value that is empty or not one of ours is dropped, so the URL never claims a
 * filter that is not applied; the tab is a view, not a record, and falls back to «Da fare».
 */
export interface EngagementsSearch {
  tab: EngagementBucket
  /** A freelancer's organization id. */
  freelancer?: string
  /** A client's organization id. */
  client?: string
  state?: EngagementState
  /** The hub's «Crea match» (REB-1105): the engagement id the hub minted. Its presence opens the form. */
  hub_match_id?: string
  /** A freelancer's organization, deal and client for the form «Crea match» opens; not filters. */
  freelancer_id?: string
  deal_id?: string
  client_id?: string
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function uuid(value: unknown): string | undefined {
  return typeof value === 'string' && UUID.test(value) ? value : undefined
}

const STATES = Object.keys(STATE_LABELS) as EngagementState[]

export function parseEngagementsSearch(search: Record<string, unknown>): EngagementsSearch {
  return {
    tab: TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'todo',
    freelancer: uuid(search.freelancer),
    client: uuid(search.client),
    state: STATES.find((state) => state === search.state),
    hub_match_id: uuid(search.hub_match_id),
    freelancer_id: uuid(search.freelancer_id),
    deal_id: uuid(search.deal_id),
    client_id: uuid(search.client_id),
  }
}

/** Whether any filter, besides the tab, narrows the list. */
export function isFiltered(search: EngagementsSearch): boolean {
  return search.freelancer !== undefined || search.client !== undefined || search.state !== undefined
}
