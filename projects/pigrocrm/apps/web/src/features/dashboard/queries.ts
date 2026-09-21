import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from '@rebase/ui/sonner'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { queryKeys } from '@/lib/query'
import type { Periodo } from './periodo'
import type { CashBase } from './search'

export type CommercialDashboard = components['schemas']['CommercialDashboard']
export type EconomicDashboard = components['schemas']['EconomicDashboard']
export type EconomicOverview = components['schemas']['EconomicOverview']
export type CashMonth = components['schemas']['CashMonth']
export type FiscalEstimate = components['schemas']['FiscalEstimate']
export type PipelineStageSummary = components['schemas']['PipelineStageSummary']
export type PendingOffer = components['schemas']['PendingOffer']
export type AutomationsDescription = components['schemas']['AutomationsDescription']
export type AutomationConfigUpdate = components['schemas']['AutomationConfigUpdate']

/**
 * §7.2: 60 seconds, a deliberate override of the 30 000 ms default in `lib/query.ts`.
 * A dashboard aggregates more than a list does, and its answer stays useful longer -- and
 * the response's age is shown on screen, so a stale figure is never a silent one.
 */
export const DASHBOARD_STALE_MS = 60_000

export function useCommercialDashboard(periodo: Periodo) {
  return useQuery({
    queryKey: queryKeys.dashboard('commerciale', periodo),
    queryFn: () => unwrap(api.GET('/api/dashboard/sales', { params: { query: periodo } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

export function useEconomicDashboard(periodo: Periodo) {
  return useQuery({
    queryKey: queryKeys.dashboard('economica', periodo),
    queryFn: () => unwrap(api.GET('/api/dashboard/economic', { params: { query: periodo } })),
    staleTime: DASHBOARD_STALE_MS,
  })
}

/** The economic tab reads the year, not the period: cash is an annual story (the
 *  fiscal estimate only exists per year), so the picker's `da` names the year. `base`
 *  is which month each document falls in (ORB-133), part of the key because the same
 *  year answers differently under the two readings. */
export function useEconomicOverview(anno: number, base: CashBase) {
  return useQuery({
    queryKey: queryKeys.dashboard('panoramica', { anno: String(anno), base }),
    queryFn: () =>
      unwrap(api.GET('/api/analytics/overview', { params: { query: { anno, base } } })),
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
 */
export function useFiscalEstimate(anno: number) {
  return useQuery({
    queryKey: queryKeys.fiscalEstimate(anno),
    queryFn: () => unwrap(api.GET('/api/analytics/fiscal', { params: { query: { anno } } })),
  })
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
