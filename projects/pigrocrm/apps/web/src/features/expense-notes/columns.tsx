import type { ColumnDef } from '@tanstack/react-table'
import type { DataTableFeatures } from '@/components/DataTable'
import { MoneyCell } from '@/components/cells'
import { formatMoneyValue } from '@/features/time/columns'
import { NoteCell, StateCell } from './NoteCells'
import type { ExpenseNote } from './queries'

/** «Nota», «Categoria», «Importo», «Stato» (the brief): «Persona» is the second line of the first
 *  column and not a column, and a staff member's table drops it altogether. */
export function noteColumns({
  withPerson,
  forStaffMember,
}: {
  withPerson: boolean
  forStaffMember: boolean
}): ColumnDef<DataTableFeatures, ExpenseNote>[] {
  return [
    { id: 'note', header: 'Nota', cell: ({ row }) => <NoteCell note={row.original} withPerson={withPerson} /> },
    {
      id: 'category',
      header: 'Categoria',
      cell: ({ row }) => <span className="whitespace-normal">{row.original.category_name}</span>,
    },
    {
      id: 'amount',
      header: 'Importo',
      meta: { align: 'right' },
      cell: ({ row }) => <MoneyCell>{formatMoneyValue(row.original.amount)}</MoneyCell>,
    },
    {
      id: 'state',
      header: 'Stato',
      cell: ({ row }) => <StateCell note={row.original} forStaffMember={forStaffMember} />,
    },
  ]
}
