import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // A 401 means the session expired; retrying just delays the redirect. A 403
      // means the actor's role will not change mid-request either. Both are terminal
      // for this request -- everything else gets the library's usual couple of
      // retries for a transient blip.
      retry: (failureCount, error) => {
        const status = (error as { status?: number }).status
        if (status === 401 || status === 403) return false
        return failureCount < 2
      },
    },
  },
})

/**
 * For a read whose «not found» is an answer (a legal entity with no fiscal profile): a 404 is
 * terminal and the page says what to do, instead of showing «loading» through the library's two
 * retries; anything else, except 401 and 403 as above, gets them.
 */
export function retryUnlessMissing(failureCount: number, error: unknown): boolean {
  const status = (error as { status?: number }).status
  if (status === 401 || status === 403 || status === 404) return false
  return failureCount < 2
}

/** The id the history of a space-wide setting is read and invalidated under: `slack` and
 *  `reminder_defaults` have one history each and no row to name (REB-828). */
export const SETTINGS_TIMELINE_ID = 'space'

export const queryKeys = {
  me: ['me'] as const,
  // The other spaces this identity may reach, and the installation's own root name
  // (`tenants/root`): one read each, shared by the shell's space switcher and the login page.
  identitySpaces: ['identity', 'spaces'] as const,
  rootTenant: ['tenants', 'root'] as const,
  organizations: (params?: unknown) => ['organizations', params ?? {}] as const,
  customer: (id: string) => ['organization', id] as const,
  people: (params?: unknown) => ['people', params ?? {}] as const,
  person: (id: string) => ['person', id] as const,
  deals: (params?: unknown) => ['deals', params ?? {}] as const,
  deal: (id: string) => ['deal', id] as const,
  // What converting a won deal into a contract would start from; under the deal's prefix, so
  // any write to the deal refreshes it.
  dealContractProposal: (id: string, start?: string) =>
    ['deal', id, 'contract-proposal', start ?? null] as const,
  timeline: (entity: string, id: string) => ['timeline', entity, id] as const,
  // The notes thread of a deal, a customer or a person (REB-879), keyed like the timeline
  // by entity type and id so one write can refresh both.
  notes: (entity: string, id: string) => ['notes', entity, id] as const,
  fields: (entityType: string) => ['field-definitions', entityType] as const,
  schema: (entityType: string) => ['schema', entityType] as const,
  stages: ['pipeline-stages'] as const,
  // The space's profile and module switches (REB-953): one read for the whole shell, kept
  // as fresh as the server's own settings cache (`lib/modules.ts`).
  modules: ['space-modules'] as const,
  mailboxes: ['mailboxes'] as const,
  aiModel: ['ai-model-settings'] as const,
  users: ['users'] as const,
  // The pending invitations «Inviti in attesa» lists (REB-291). Same reasoning as
  // `users` above: one installation, one space per page load (the router's basepath
  // is fixed at boot), so no id needs to scope it.
  invites: ['invites'] as const,
  // Scoped by user id, not a bare `['tokens']`: `GET /api/tokens` already
  // scopes the *response* to the caller (`PatService.list` filters on
  // `actor.id`), and `logout()`'s `queryClient.clear()` already wipes this
  // cache before another user's session could read it either way -- but a
  // per-user key is what makes a stale cross-user read impossible by
  // construction, rather than merely unreproduced today.
  tokens: (userId: string) => ['tokens', userId] as const,
  // The colleagues' tokens an admin sees (REB-810), and what the space lets a token be
  // given. Both under `tokens` so one `invalidateQueries({ queryKey: ['tokens'] })` after a
  // change refreshes every table of the panel.
  teamTokens: (userId: string) => ['tokens', userId, 'team'] as const,
  tokenOptions: (userId: string) => ['tokens', userId, 'options'] as const,
  // `owner` is the discriminated `{customerId} | {dealId}` object, so a customer's
  // documents and a deal's documents can never share a cache entry, and
  // `invalidateQueries({queryKey: ['documents']})` still matches both.
  documents: (owner?: unknown) => ['documents', owner ?? {}] as const,
  document: (id: string) => ['document', id] as const,
  documentVersions: (id: string) => ['document-versions', id] as const,
  templates: () => ['templates'] as const,
  templateDescription: (id: string) => ['template-description', id] as const,
  // The agent proposals the Proposte queue reviews (REB-750). `proposals` is the prefix a
  // decision invalidates; the contract behind a day proposal has a key of its own because
  // the page only needs its title, not the contracts feature's cache.
  proposals: (status?: string) => ['proposals', status ?? 'all'] as const,
  // «In arrivo» (REB-984): `inbox` is the prefix every write to an item invalidates, under it the
  // lists by state and kind, one item, the waiting count the sidebar draws, and the two
  // lists a panel reads (the copies of an item, and the files one may be a copy of).
  inbox: ['inbox'] as const,
  inboxList: (state: string, kind: string | null) => ['inbox', 'list', state, kind] as const,
  inboxItem: (id: string) => ['inbox', 'item', id] as const,
  inboxCount: ['inbox', 'count'] as const,
  inboxUnread: ['inbox', 'unread'] as const,
  inboxCopies: ['inbox', 'copies'] as const,
  inboxOriginals: ['inbox', 'originals'] as const,
  // «Collaboratori» (REB-1035): `engagements` is the prefix every write to an engagement or one of
  // its months invalidates; under it the lists by tab and filters, the counts the entry draws,
  // one engagement with its report, its payments and its availability.
  engagements: ['engagements'] as const,
  engagementList: (params: unknown) => ['engagements', 'list', params] as const,
  engagementCounts: ['engagements', 'counts'] as const,
  engagement: (id: string) => ['engagements', 'one', id] as const,
  engagementReport: (id: string) => ['engagements', 'one', id, 'report'] as const,
  engagementPayments: (id: string) => ['engagements', 'one', id, 'payments'] as const,
  freelancerAvailability: (params: unknown) => ['engagements', 'availability', params] as const,
  // The freelancer's own card on a contract: rebase's data in another space, so it is not under
  // `engagements`, which belongs to rebase's own space.
  ownEngagement: (contractId: string) => ['own-engagement', contractId] as const,
  proposal: (id: string) => ['proposal', id] as const,
  proposalTimeline: (id: string) => ['proposal-timeline', id] as const,
  proposalContract: (id: string) => ['proposal-contract', id] as const,
  // The legal entities of the space (REB-617): the list every selector and Settings →
  // Legal entities read; a single legal entity is read from the list, so there is no per-id key.
  legal_entities: ['legal_entities'] as const,
  // One azienda's logo or signature bytes (REB-629), under the `legal_entities` prefix so the
  // save's invalidation refreshes the preview with the row.
  legalEntityImage: (legalEntityId: string, slot: 'logo' | 'signature') =>
    ['legal_entities', 'image', legalEntityId, slot] as const,
  // The azienda a nation proposes for a new customer (REB-626). Under the `legal_entities`
  // prefix on purpose: a rename or a deactivation invalidates the proposal with the list.
  legalEntityProposal: (country: string) => ['legal_entities', 'proposal', country] as const,
  // The municipalities of one province, ISTAT's list (REB-1069): static for the life of a
  // release, so it is not under `legal_entities` and a save does not refetch it.
  municipalities: (province: string) => ['municipalities', province] as const,
  // The places of business of one legal entity beside its registered office (REB-1092), with the
  // staff count each carries. Its own key and not under `legal_entities`: a save of the legal
  // entity's identity does not change it, a staff write does, and both invalidate it by name.
  legalEntitySites: (legalEntityId: string) => ['legal-entity-sites', legalEntityId] as const,
  // The regions a site can be in: the IRAP rate table's keys, fixed for the life of a release.
  irapRegions: ['irap-regions'] as const,
  invoices: (params?: unknown) => ['invoices', params ?? {}] as const,
  // Distinct from `invoices` above: that key holds one `InvoicePage`, this one holds
  // an `useInfiniteQuery`'s own `{pages, pageParams}` shape (`useInvoicesPaged`,
  // REB-231). A caller reading one cache entry under the other's key would misread
  // `data.items` as `data.pages` or vice versa with no type error to catch it, so the
  // two never share a key even where their filters happen to coincide.
  invoicesList: (params?: unknown) => ['invoices-list', params ?? {}] as const,
  invoice: (id: string) => ['invoice', id] as const,
  invoiceLines: (id: string) => ['invoice-lines', id] as const,
  // The document id is in the key for the null -> id transition (a proforma's first
  // render); a regeneration is a new *version* under the same id, so it is reached by
  // invalidating the prefix `invoicePdfs(id)` instead (`useInvoiceInvalidation`).
  invoicePdfs: (id: string) => ['invoice-pdf', id] as const,
  invoicePdf: (id: string, documentId: string) => ['invoice-pdf', id, documentId] as const,
  // Keyed by azienda since REB-615: a fiscal profile belongs to one.
  fiscalProfile: (legalEntityId: string) => ['fiscal-profile', legalEntityId] as const,
  // The space's own settings (`GET /api/settings/space`): the panel in Impostazioni reads
  // and writes it, and the get-started invoice door reads the same entry.
  spaceSettings: ['settings', 'space'] as const,
  timeEntries: (params?: unknown) => ['time-entries', params ?? {}] as const,
  // One key per month, and the prefix `['calendar']` on purpose: a commitment whose
  // date moved is stale in two months at once, so every write invalidates the prefix
  // rather than the month the page happens to be showing.
  // The sidebar's azienda is part of the key since REB-625: the hours and the invoices
  // of a month differ by it, and a switch is a new key rather than an invalidation.
  calendarMonth: (month: string, legalEntityId: string | null = null) =>
    ['calendar', month, legalEntityId] as const,
  /** The prefix of every month: a write that moves a date invalidates all of them. */
  calendarAll: ['calendar'] as const,
  activities: (params?: unknown) => ['activities', params ?? {}] as const,
  /** The prefix of every activities read (lists, pages, today, one). */
  activitiesAll: ['activities'] as const,
  /** The caller's own running timer -- one row or null, so one key with no argument. */
  timer: ['timer'] as const,
  timeEntry: (id: string) => ['time-entry', id] as const,
  dealTimeSummary: (dealId: string) => ['deal-time-summary', dealId] as const,
  // Keyed by both ids: the answer depends on the deal's rate *and* the user's default,
  // so a single-id key would serve one user's rate for another's.
  dealRates: (dealId: string, userId: string) => ['deal-rates', dealId, userId] as const,
  purchases: (params?: unknown) => ['purchases', params ?? {}] as const,
  purchase: (id: string) => ['purchase', id] as const,
  costCategories: (includeArchived: boolean) => ['cost-categories', includeArchived] as const,
  periodLocks: (year?: number) => ['period-locks', year ?? null] as const,
  dealPnl: (dealId: string) => ['deal-pnl', dealId] as const,
  // Keyed on the whole query object, window included: the same customer read over two
  // different periods is two different answers, and a key that dropped the dates would
  // serve January's report for December's.
  periodPnl: (params?: unknown) => ['period-pnl', params ?? {}] as const,
  // The legal entity is part of the key since REB-632: an estimate is one legal entity's, and in
  // «tutte» the economic tab holds one entry per legal entity at once.
  fiscalEstimate: (year: number, legalEntityId: string | null = null) =>
    ['fiscal-estimate', year, legalEntityId] as const,
  // The IVA cycle of one legal entity (REB-1024): the entities that have it, a year's periods and
  // a period's register. `entityId` is the legal entity the page reads, never a sum.
  ivaEntities: () => ['iva-entities'] as const,
  ivaPeriods: (year: number, entityId: string | null) => ['iva-periods', year, entityId] as const,
  // Every year's periods of every legal entity, for the writes that change them (closing a period).
  ivaPeriodsAll: () => ['iva-periods'] as const,
  ivaRegister: (kind: 'sales' | 'purchases', year: number, number: number, entityId: string | null) =>
    ['iva-register', kind, year, number, entityId] as const,
  // The tax calendar is one legal entity's and one calendar year's (REB-1026).
  taxCalendar: (year: number, legalEntityId: string | null = null) =>
    ['tax-calendar', year, legalEntityId] as const,
  // «Tasse»'s due items of one legal entity (a calendar year's, or the plans' own kinds) and its
  // plans (REB-1079).
  taxDueItems: (scope: string, legalEntityId: string | null = null) =>
    ['tax-due-items', scope, legalEntityId] as const,
  taxDueItemsAll: ['tax-due-items'] as const,
  taxF24Groups: (year: number, legalEntityId: string | null = null) =>
    ['f24-groups', year, legalEntityId] as const,
  taxPlans: (legalEntityId: string | null = null) => ['tax-plans', legalEntityId] as const,
  // The ceilings and the year register are one azienda's as well (REB-755, REB-757), and
  // the simulation is keyed on everything typed: the same deal against another date is
  // another answer.
  ceilings: (year: number, legalEntityId: string | null = null) =>
    ['ceilings', year, legalEntityId] as const,
  ceilingSimulation: (params: Record<string, string>) => ['ceiling-simulation', params] as const,
  yearOverview: (year: number, legalEntityId: string | null = null) =>
    ['year-overview', year, legalEntityId] as const,
  yearOverviewAll: ['year-overview'] as const,
  // The term is part of the key so an in-flight response for "ross" cannot overwrite the
  // rendering of "rossi": TanStack Query discards the stale entry rather than the
  // component having to compare what came back with what was typed.
  search: (term: string, legalEntityId: string | null = null) => ['search', term, legalEntityId] as const,
  // The period is part of the key, so switching period is a different cache entry rather
  // than a refetch that briefly shows March's numbers under April's heading. Every
  // dashboard key starts with the literal 'dashboard' so a mutation that cannot know which
  // period is on screen can invalidate all of them by prefix.
  /** Every dashboard read, whatever the period: for a write that cannot know which is on screen. */
  dashboardAll: ['dashboard'] as const,
  dashboard: (
    kind: 'cash' | 'commercial' | 'economic' | 'panoramica' | 'receivables' | 'work_units',
    params: Record<string, string>,
  ) =>
    ['dashboard', kind, params] as const,
  automations: () => ['automations'] as const,
  // What the cron commands last did in this space (REB-764).
  scheduledJobs: ['scheduled-jobs'] as const,
  // A contract and everything the page hangs on it (REB-741..744). All under the
  // 'contract' prefix except the customer's list, so one invalidation of
  // `contract(id)` refreshes the row, its rate cards, expenses, assumption and figures.
  contracts: (params?: unknown) => ['contracts', params ?? {}] as const,
  contract: (id: string) => ['contract', id] as const,
  contractRateCards: (id: string) => ['contract', id, 'rate-cards'] as const,
  contractExpenses: (id: string) => ['contract', id, 'expenses'] as const,
  contractAssumption: (id: string) => ['contract', id, 'assumption'] as const,
  contractClauseNotes: (id: string) => ['contract', id, 'clause-notes'] as const,
  contractEmailTemplates: (id: string) => ['contract', id, 'email-templates'] as const,
  contractEmailVariables: (id: string) => ['contract', id, 'email-template-variables'] as const,
  /** The prefix of every cached preview of a contract's mail. */
  contractEmailPreviews: (id: string) => ['contract', id, 'email-preview'] as const,
  contractEmailPreview: (id: string, templateId?: string, invoiceId?: string) =>
    ['contract', id, 'email-preview', templateId, invoiceId] as const,
  contractProjection: (id: string, params?: unknown) =>
    ['contract', id, 'projection', params ?? {}] as const,
  contractConcentration: (id: string) => ['contract', id, 'concentration'] as const,
  // The contract days (REB-745): `workUnits` is the prefix every day write invalidates, and
  // the calendar's month is under its own `['calendar']` prefix because it carries the
  // days' states as well.
  workUnits: (params?: unknown) => ['work-units', params ?? {}] as const,
  workUnit: (id: string) => ['work-unit', id] as const,
  /** Every day read: the list prefix and the single-day prefix. */
  workUnitsAll: ['work-units'] as const,
  workUnitAll: ['work-unit'] as const,
  // The caller's own reminders (REB-734): the Avvisi page and the sidebar's unread count
  // read this one entry, so a snooze updates both at once. It is the caller's list, and
  // `logout()` clears the whole cache, so no user id is needed to keep two people apart.
  reminders: ['reminders'] as const,
  // Both layers of reminder settings and what they resolve to for the caller (REB-735): the
  // space's editor and the personal one read this one entry, so a write in either shows in
  // both. A key of its own, not a child of `reminders`: a write invalidates the Avvisi list
  // too, and the two are different reads.
  reminderPreferences: ['reminder-preferences'] as const,
  contractBillableDays: (id: string, params?: unknown) =>
    ['contract', id, 'billable-days', params ?? {}] as const,
  // The occurrences of a contract's recurring fees (REB-1032), under the contract's own key so
  // anything that invalidates the contract reads them again.
  contractFeeOccurrences: (id: string, params?: unknown) =>
    ['contract', id, 'fee-occurrences', params ?? {}] as const,
  contractRegister: (id: string, params?: unknown) =>
    ['contract', id, 'register', params ?? {}] as const,
  // Web push (REB-738): whether this installation can send it and the key a browser
  // subscribes with, and the caller's own browsers.
  pushConfig: ['push-config'] as const,
  pushSubscriptions: ['push-subscriptions'] as const,
}
