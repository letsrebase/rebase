import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useLegalEntity } from '@/lib/legalEntity'
import { queryKeys } from '@/lib/query'
import { tenantPrefix } from '@/lib/tenant'
import type { Periodo } from './periodo'
import type { CashBase } from './search'

export type CommercialDashboard = components['schemas']['CommercialDashboard']
export type EconomicDashboard = components['schemas']['EconomicDashboard']
export type EconomicOverview = components['schemas']['EconomicOverview']
export type CashMonth = components['schemas']['CashMonth']
export type FiscalEstimate = components['schemas']['FiscalEstimate']
export type PipelineStageSummary = components['schemas']['PipelineStageSummary']
export type PendingOffer = components['schemas']['PendingOffer']
export type ReceivablesDashboard = components['schemas']['ReceivablesDashboard']
export type FasciaScadenza = components['schemas']['FasciaScadenza']
export type CeilingHeadroom = components['schemas']['CeilingHeadroom']
export type CeilingStatus = components['schemas']['CeilingStatusRead']
export type CeilingSimulation = components['schemas']['CeilingSimulation']
export type YearOverview = components['schemas']['YearOverview']
export type YearRegisterRow = components['schemas']['YearRegisterRow']
export type AutomationsDescription = components['schemas']['AutomationsDescription']
export type AutomationConfigUpdate = components['schemas']['AutomationConfigUpdate']

/**
 * §7.2: 60 seconds, a deliberate override of the 30 000 ms default in `lib/query.ts`.
 * A dashboard aggregates more than a list does, and its answer stays useful longer -- and
 * the response's age is shown on screen, so a stale figure is never a silent one.
 */
export const DASHBOARD_STALE_MS = 60_000

/**
 * The sidebar's azienda as the parameter every dashboard read takes (REB-632, spec
 * 2026-10-03 §5): `azienda_id` when one is selected, nothing in «tutte», where the
 * server adds every azienda up. It is spread into the request and into the key alike,
 * so a switch of the sidebar is a new cache entry and a fresh request, with no
 * invalidation to forget. Not in the URL: the selection is a session choice (§5).
 */
function useLegalEntityParam(): { azienda_id?: string } {
  const { selected } = useLegalEntity()
  return selected === null ? {} : { azienda_id: selected }
}

export function useCommercialDashboard(periodo: Periodo) {
  const params = { ...periodo, ...useLegalEntityParam() }
  return useQuery({
    queryKey: queryKeys.dashboard('commerciale', params),
    queryFn: () => unwrap(api.GET('/api/dashboard/sales', { params: { query: params } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

export function useEconomicDashboard(periodo: Periodo) {
  const params = { ...periodo, ...useLegalEntityParam() }
  return useQuery({
    queryKey: queryKeys.dashboard('economica', params),
    queryFn: () => unwrap(api.GET('/api/dashboard/economic', { params: { query: params } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

/** Slice 8 part A (REB-329): no period, so the key carries none. What is owed is owed
 *  today, and the response's `oggi` says which day the buckets were measured from. */
export function useReceivablesDashboard() {
  const params = useLegalEntityParam()
  return useQuery({
    queryKey: queryKeys.dashboard('scadenziario', params),
    queryFn: () => unwrap(api.GET('/api/dashboard/receivables', { params: { query: params } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

/** The economic tab reads the year, not the period: cash is an annual story (the
 *  fiscal estimate only exists per year), so the picker's `da` names the year. `base`
 *  is which month each document falls in (ORB-133), part of the key because the same
 *  year answers differently under the two readings. */
export function useEconomicOverview(anno: number, base: CashBase) {
  const scope = useLegalEntityParam()
  return useQuery({
    queryKey: queryKeys.dashboard('panoramica', { anno: String(anno), base, ...scope }),
    queryFn: () =>
      unwrap(
        api.GET('/api/analytics/overview', { params: { query: { anno, base, ...scope } } }),
      ),
    staleTime: DASHBOARD_STALE_MS,
  })
}

/**
 * The annual fiscal estimate the «Stima fiscale» card is built from, per year.
 *
 * It sits beside the dashboard's own queries since the estimate moved into Home: its one
 * caller is `FiscalPanel.tsx` next door, and a hook left behind in `features/analytics`
 * would be the only thing that screen still borrowed from a section the interface no
 * longer has.
 *
 * No `staleTime` override: unlike the aggregates above, this one is read once per visit
 * under the cards and the default in `lib/query.ts` is the right answer for it.
 *
 * One azienda's (REB-632): the one `aziendaId` names, or the sidebar's when the caller
 * names none. In «tutte» on a space with several aziende the economic tab asks once
 * per azienda, by id, because the server has no space-wide estimate to give (spec
 * §1.9); with one azienda nothing is sent and the server resolves it alone.
 */
export function useFiscalEstimate(anno: number, aziendaId?: string | null) {
  const { selected } = useLegalEntity()
  const azienda = aziendaId === undefined ? selected : aziendaId
  const query = azienda === null ? { anno } : { anno, azienda_id: azienda }
  return useQuery({
    queryKey: queryKeys.fiscalEstimate(anno, azienda),
    queryFn: () => unwrap(api.GET('/api/analytics/fiscal', { params: { query } })),
  })
}

/**
 * How much room is left under each of an azienda's ceilings in `anno` (REB-755). One
 * azienda's, like the estimate: the one `aziendaId` names, or the sidebar's when the
 * caller names none. `soglie` comes back empty for an azienda the forfettario's
 * thresholds do not apply to, and the panel then draws nothing.
 */
export function useCeilingHeadroom(anno: number, aziendaId?: string | null) {
  const { selected } = useLegalEntity()
  const azienda = aziendaId === undefined ? selected : aziendaId
  const query = azienda === null ? { anno } : { anno, azienda_id: azienda }
  return useQuery({
    queryKey: queryKeys.ceilings(anno, azienda),
    queryFn: () => unwrap(api.GET('/api/analytics/ceilings', { params: { query } })),
  })
}

/** What a person typed into the «ci sta?» simulator, already in the API's own spelling. */
export type CeilingSimulationInput = {
  valore_preventivato?: string
  ore_preventivate?: string
  tariffa_oraria?: string
}

/**
 * The «ci sta?» answer for one estimate, asked only once the person presses «Simula»
 * (`input` is `null` until then): a request per keystroke would answer questions nobody
 * finished asking. Nothing is saved on the server, and the answer is as fresh as the
 * revenue it adds to, so it is kept no longer than the screen shows it.
 */
export function useCeilingSimulation(
  anno: number,
  input: CeilingSimulationInput | null,
  aziendaId?: string | null,
  /** Which press of «Simula» this is: part of the key, so pressing it again with the same
   *  inputs asks the server again instead of showing the answer already held. */
  attempt = 0,
) {
  const { selected } = useLegalEntity()
  const azienda = aziendaId === undefined ? selected : aziendaId
  const params: Record<string, string> = {
    anno: String(anno),
    attempt: String(attempt),
    ...(azienda === null ? {} : { azienda_id: azienda }),
    ...input,
  }
  return useQuery({
    enabled: input !== null,
    queryKey: queryKeys.ceilingSimulation(params),
    queryFn: () =>
      unwrap(
        api.GET('/api/analytics/ceilings/simulate', {
          params: { query: { ...input, anno, ...(azienda === null ? {} : { azienda_id: azienda }) } },
        }),
      ),
    gcTime: 0,
  })
}

/** One azienda's register for `anno` (REB-757): revenue by regime and tax treatment and
 *  the issued invoices, with the stamp duty and the rivalsa apart. */
export function useYearOverview(anno: number, aziendaId?: string | null) {
  const { selected } = useLegalEntity()
  const azienda = aziendaId === undefined ? selected : aziendaId
  const query = azienda === null ? { anno } : { anno, azienda_id: azienda }
  return useQuery({
    queryKey: queryKeys.yearOverview(anno, azienda),
    queryFn: () => unwrap(api.GET('/api/analytics/year', { params: { query } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

/**
 * Where the year's CSV is downloaded from. A plain URL, not a fetch, like the timesheet's
 * XLSX: the browser saves the file itself and the bytes never pass through JavaScript.
 * Same-origin, so the session cookie travels with it, and the space's prefix is part of
 * the path.
 */
export function yearCsvUrl(anno: number, aziendaId: string | null): string {
  const query = new URLSearchParams({ anno: String(anno) })
  if (aziendaId !== null) query.set('azienda_id', aziendaId)
  return `${tenantPrefix}/api/analytics/year/csv?${query.toString()}`
}

export function useAutomations() {
  return useQuery({
    queryKey: queryKeys.automations(),
    queryFn: () => unwrap(api.GET('/api/automations')),
  })
}

export function useUpdateAutomationConfig() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: AutomationConfigUpdate) => unwrap(api.PUT('/api/automation-config', { body })),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.automations() })
      // The commercial dashboard's signal count reflects whether A1 has been firing, so a
      // configuration change is a reason to re-read it. By the `['dashboard']` prefix
      // rather than an exact key: a mutation cannot know which period the user is looking
      // at, and the prefix covers every cached period and every tab.
      void client.invalidateQueries({ queryKey: ['dashboard'] })
      toast.success('Configurazione aggiornata')
    },
    // No `onError` that copies the failure into component state: the panel renders it from
    // this mutation's own `error`, so a later success clears it by construction. An error
    // set into state and never cleared is a defect this codebase fixed twice
    // (CostCategoriesPanel, RatesPanel).
  })
}
