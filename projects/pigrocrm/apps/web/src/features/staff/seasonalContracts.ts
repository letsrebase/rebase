import type { StaffMember } from './queries'

/**
 * The contract history of a seasonal worker (REB-1090), as the form edits it: dates and days as
 * the text in their inputs. The server decides what the history is worth (art. 11 c. 4-octies);
 * nothing here computes a deduction.
 */
export interface ContractRow {
  start: string
  end: string
  /** The days of effective work by tax year; a year left empty is «not stated», never zero. */
  days: Record<string, string>
}

export type ContractWire = NonNullable<StaffMember['seasonal_contracts']>[number]

const MOST_YEARS = 12

export function emptyRow(): ContractRow {
  return { start: '', end: '', days: {} }
}

export function rowsFrom(contracts: StaffMember['seasonal_contracts'] | undefined): ContractRow[] {
  return (contracts ?? []).map((contract) => ({
    start: contract.start_date,
    end: contract.end_date ?? '',
    days: Object.fromEntries(Object.entries(contract.worked_days ?? {}).map(([year, days]) => [year, String(days)])),
  }))
}

/** The tax years a row spans: from the start to the end, or to the current year while it runs. */
export function yearsOf(row: ContractRow, thisYear: number): number[] {
  const first = Number(row.start.slice(0, 4))
  if (!/^\d{4}-\d{2}-\d{2}$/.test(row.start)) return []
  const last = /^\d{4}-\d{2}-\d{2}$/.test(row.end) ? Number(row.end.slice(0, 4)) : thisYear
  if (last < first) return []
  const years: number[] = []
  for (let year = first; year <= Math.min(last, first + MOST_YEARS - 1); year += 1) years.push(year)
  return years
}

export type Wired = { ok: true; value: ContractWire[] } | { ok: false; error: string }

/** The rows as the body carries them. A row with neither date is a blank one and is dropped. */
export function wireContracts(rows: ContractRow[], thisYear: number): Wired {
  const value: ContractWire[] = []
  for (const row of rows) {
    if (row.start === '' && row.end === '') continue
    if (row.start === '') return { ok: false, error: 'Ogni contratto ha una data di inizio.' }
    if (row.end !== '' && row.end < row.start) return { ok: false, error: 'La fine del contratto non può precedere l’inizio.' }
    const worked: Record<string, number> = {}
    for (const year of yearsOf(row, thisYear)) {
      const text = (row.days[String(year)] ?? '').trim()
      if (text === '') continue
      if (!/^\d{1,3}$/.test(text) || Number(text) > 366) return { ok: false, error: `I giorni del ${year} sono un numero da 0 a 366.` }
      worked[String(year)] = Number(text)
    }
    value.push({ start_date: row.start, end_date: row.end === '' ? null : row.end, worked_days: worked })
  }
  return { ok: true, value }
}
