import type { ColumnDef } from '@tanstack/react-table'
import { DateCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { StatusPill } from '@/components/StatusPill'
import { cadenceLabel, renewalLabel, stateLabel, stateTone } from './labels'
import type { Contract } from './queries'

export function buildContractColumns(): ColumnDef<DataTableFeatures, Contract>[] {
  return [
    { header: 'Titolo', id: 'title', accessorFn: (row) => row.title },
    {
      header: 'Stato',
      id: 'status',
      accessorFn: (row) => row.status,
      cell: ({ row }) => (
        <StatusPill tone={stateTone(row.original.status)}>{stateLabel(row.original.status)}</StatusPill>
      ),
    },
    {
      header: 'Inizio',
      id: 'start',
      accessorFn: (row) => row.start,
      cell: ({ row }) => <DateCell value={row.original.start} />,
    },
    {
      header: 'Fine',
      id: 'end',
      accessorFn: (row) => row.end,
      cell: ({ row }) => <DateCell value={row.original.end} absent="senza scadenza" />,
    },
    { header: 'Rinnovo', id: 'rinnovo', accessorFn: (row) => renewalLabel(row.renewal_type) },
    {
      header: 'Fatturazione',
      id: 'fatturazione',
      accessorFn: (row) => cadenceLabel(row.billing_cadence),
    },
  ]
}
