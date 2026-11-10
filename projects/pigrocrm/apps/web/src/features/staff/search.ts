/** `?month=2026-10&ended=1`: the month worked the page reads and whether people who have left
 *  are listed. Both are in the URL, so a link reopens the same reading. */
export type StaffSearch = { month?: string; ended?: boolean }

const MONTH = /^(\d{4})-(0[1-9]|1[0-2])$/

export function validateStaffSearch(search: Record<string, unknown>): StaffSearch {
  const month = typeof search.month === 'string' && MONTH.test(search.month) ? search.month : undefined
  const ended = search.ended === true || search.ended === 'true' || search.ended === 1 ? true : undefined
  return { month, ended }
}
