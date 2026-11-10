import { createFileRoute } from '@tanstack/react-router'
import { StaffPage } from '@/features/staff/StaffPage'
import { validateStaffSearch } from '@/features/staff/search'

/**
 * `/app/staff?month=&ended=`, «Persone dell'azienda» (REB-1027). The month worked and the filter
 * for who has left are in the URL; the page is `features/staff/StaffPage.tsx`, so this file
 * exports only `Route`. It belongs to the `staff` module (`ROUTE_MODULES`) and is an
 * administrator's: anyone else reads the sentence the page carries and nothing is asked.
 */
export const Route = createFileRoute('/app/staff')({
  validateSearch: validateStaffSearch,
  component: StaffPage,
})
