import { TABS, type InboxTab } from './labels'

export type InboxSearch = { tab: InboxTab; item?: string }

/**
 * `?tab=pending|filed|set_aside&item=<id>`: the tab and the open item are in the URL, so a
 * link to an item reopens its panel (and on the phone its full-screen card). Anything else
 * lands on «Da gestire» with nothing open: a tab is a view, not a record.
 */
export function validateInboxSearch(search: Record<string, unknown>): InboxSearch {
  const tab = TABS.find((candidate) => candidate.id === search.tab)?.id ?? 'pending'
  const item = typeof search.item === 'string' && search.item !== '' ? search.item : undefined
  return item === undefined ? { tab } : { tab, item }
}
