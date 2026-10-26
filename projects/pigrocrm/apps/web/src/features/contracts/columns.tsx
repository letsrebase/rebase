import type { ColumnDef } from '@tanstack/react-table'
import { DateCell } from '@/components/cells'
import type { DataTableFeatures } from '@/components/DataTable'
import { StatusPill } from '@/components/StatusPill'
import { cadenceLabel, renewalLabel, stateLabel, stateTone } from './labels'
import type { Contract } from './queries'

export function buildContractColumns(): ColumnDef<DataTableFeatures, Contract>[] {
  return [
    { header: 'Titolo', id: 'titolo', accessorFn: (row) => row.titolo },
    {
      header: 'Stato',
      id: 'stato',
      accessorFn: (row) => row.stato,
      cell: ({ row }) => (
        <StatusPill tone={stateTone(row.original.stato)}>{stateLabel(row.original.stato)}</StatusPill>
      ),
    },
    {
      header: 'Inizio',
      id: 'inizio',
      accessorFn: (row) => row.inizio,
      cell: ({ row }) => <DateCell value={row.original.inizio} />,
    },
    {
      header: 'Fine',
      id: 'fine',
      accessorFn: (row) => row.fine,
      cell: ({ row }) => <DateCell value={row.original.fine} absent="senza scadenza" />,
    },
    { header: 'Rinnovo', id: 'rinnovo', accessorFn: (row) => renewalLabel(row.tipo_rinnovo) },
    {
      header: 'Fatturazione',
      id: 'fatturazione',
      accessorFn: (row) => cadenceLabel(row.cadenza_fatturazione),
    },
  ]
}
