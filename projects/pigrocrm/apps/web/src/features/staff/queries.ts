import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/lib/api'
import type { components } from '@/lib/api-types'
import { useCursorPages } from '@/lib/useCursorPages'

export type StaffMember = components['schemas']['StaffMemberRead']
export type PayrollMonth = components['schemas']['PayrollMonthRead']
export type MemberMonth = components['schemas']['MemberMonthRead']
export type CashItem = components['schemas']['PayrollCashItemRead']
export type HourlyCostProposal = components['schemas']['HourlyCostProposalRead']
export type PayDay = components['schemas']['PayDayRead']
export type EmployeeBody = components['schemas']['EmployeeCreate']
export type DirectorBody = components['schemas']['DirectorCreate']
export type StaffUpdateBody = components['schemas']['StaffMemberUpdate']
export type PayslipProposal = components['schemas']['PayslipProposalRead']
export type PayrollClosure = components['schemas']['PayrollClosureRead']
export type PayslipBody = components['schemas']['PayslipFigures']
export type PayslipCorrectionBody = components['schemas']['PayslipCorrection']

/** A screen reads fifty people at a time (the brief: «Mostra altri»). */
export const STAFF_PAGE_SIZE = 50

const key = {
  all: ['staff'] as const,
  list: (entity: string, ended: boolean) => ['staff', 'list', entity, ended] as const,
  month: (entity: string | undefined, month: string) => ['staff', 'month', entity ?? null, month] as const,
  one: (id: string) => ['staff', 'one', id] as const,
  hourly: (id: string) => ['staff', 'hourly-cost', id] as const,
  payDay: (entity: string | undefined) => ['staff', 'pay-day', entity ?? null] as const,
}

/** The people of one legal entity, a page at a time; `ended` also lists who has left. */
export function useStaffList(entity: string, ended: boolean) {
  return useCursorPages<StaffMember>({
    queryKey: key.list(entity, ended),
    fetchPage: (cursor) =>
      unwrap(
        api.GET('/api/staff', {
          params: {
            query: { legal_entity_id: entity, include_ended: ended, limit: STAFF_PAGE_SIZE, cursor },
          },
        }),
      ),
  })
}

/** What the legal entity's staff cost in `month` (`YYYY-MM`) and when it leaves, from the
 *  server's estimate; nothing here is computed in the browser. */
export function usePayrollMonth(entity: string | undefined, month: string) {
  return useQuery({
    enabled: entity !== undefined,
    queryKey: key.month(entity, month),
    queryFn: () =>
      unwrap(
        api.GET('/api/staff/payroll/{legal_entity_id}', {
          params: { path: { legal_entity_id: entity ?? '' }, query: { month: `${month}-01` } },
        }),
      ),
  })
}

export function usePayDay(entity: string | undefined) {
  return useQuery({
    enabled: entity !== undefined,
    queryKey: key.payDay(entity),
    queryFn: () =>
      unwrap(
        api.GET('/api/staff/pay-days/{legal_entity_id}', {
          params: { path: { legal_entity_id: entity ?? '' } },
        }),
      ),
  })
}

/** The hour a staff member's pay gives (design § 5). Asked once per opening and never retried:
 *  a refusal («manca il collegamento a un utente») is the answer, in the server's words. */
export function useHourlyCost(id: string) {
  return useQuery({
    queryKey: key.hourly(id),
    retry: false,
    queryFn: () =>
      unwrap(
        api.GET('/api/staff/{staff_member_id}/hourly-cost', {
          params: { path: { staff_member_id: id } },
        }),
      ),
  })
}

function useRefresh() {
  const client = useQueryClient()
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: key.all }),
      // A person's place of work moves the count of each site and the IRAP split (REB-1092).
      client.invalidateQueries({ queryKey: ['legal-entity-sites'] }),
      client.invalidateQueries({ queryKey: ['fiscal-estimate'] }),
    ])
}

export function useCreateStaff() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: EmployeeBody | DirectorBody) => unwrap(api.POST('/api/staff', { body })),
    onSuccess: refresh,
  })
}

export function useUpdateStaff(id: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: StaffUpdateBody) =>
      unwrap(
        api.PATCH('/api/staff/{staff_member_id}', { params: { path: { staff_member_id: id } }, body }),
      ),
    onSuccess: refresh,
  })
}

export function useRecordPayslip(id: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: PayslipBody) =>
      unwrap(
        api.POST('/api/staff/{staff_member_id}/payslips', {
          params: { path: { staff_member_id: id } },
          body,
        }),
      ),
    onSuccess: refresh,
  })
}

/** Corrects a recorded payslip: the month and the forecasts it feeds are read again. */
export function useCorrectPayslip(id: string, payslipId: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: PayslipCorrectionBody) =>
      unwrap(
        api.PATCH('/api/staff/{staff_member_id}/payslips/{payslip_id}', {
          params: { path: { staff_member_id: id, payslip_id: payslipId } },
          body,
        }),
      ),
    onSuccess: refresh,
  })
}

/** Closes a payroll month of the legal entity: locks its payslips and its estimates. The server
 *  refuses a month that has not ended (422) and one already closed (409), in its own words. */
export function useCloseMonth(entity: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (month: string) =>
      unwrap(
        api.POST('/api/staff/payroll/{legal_entity_id}/closures', {
          params: { path: { legal_entity_id: entity } },
          body: { month: `${month}-01` },
        }),
      ),
    onSuccess: refresh,
  })
}

/** Adopts the figure the person saw as the linked user's default hourly cost. */
export function useAdoptHourlyCost(id: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (hourly_cost: string) =>
      unwrap(
        api.POST('/api/staff/{staff_member_id}/hourly-cost', {
          params: { path: { staff_member_id: id } },
          body: { hourly_cost },
        }),
      ),
    onSuccess: refresh,
  })
}

export function useSetPayDay(entity: string) {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: (body: { pay_day: number; pay_month_offset: number }) =>
      unwrap(
        api.PUT('/api/staff/pay-days/{legal_entity_id}', {
          params: { path: { legal_entity_id: entity } },
          body,
        }),
      ),
    onSuccess: refresh,
  })
}
