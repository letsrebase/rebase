import { useNavigate } from '@tanstack/react-router'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { DataTable } from '@/components/DataTable'
import { Button } from '@rebase/ui/button'
import { useCan } from '@/lib/auth'
import { buildContractColumns } from './columns'
import { ContractForm } from './ContractForm'
import { useCustomerContracts } from './queries'

/**
 * The Contratti tab on a customer's page (REB-741): the customer's contracts, newest
 * first, each opening its own page, and «Nuovo contratto» for the customer the page is
 * about, which is never asked for again. A contract is created as a bozza and the page
 * it opens is where it is edited and activated.
 */
export function ContractsTab({ customerId }: { customerId: string }) {
  const navigate = useNavigate()
  const canCreate = useCan('create_contract')
  const contracts = useCustomerContracts(customerId)
  const [creating, setCreating] = useState(false)

  return (
    <div className="space-y-4">
      {canCreate && (
        <div className="flex justify-end">
          <Button onClick={() => setCreating(true)}>
            <Plus className="mr-2 size-4" />
            Nuovo contratto
          </Button>
        </div>
      )}
      <DataTable
        columns={buildContractColumns()}
        data={contracts.data?.items ?? []}
        isLoading={contracts.isLoading}
        isError={contracts.isError}
        error={contracts.error}
        onRowClick={(row) =>
          void navigate({ to: '/app/contracts/$contractId', params: { contractId: row.id } })
        }
        emptyMessage="Nessun contratto per questo cliente."
      />
      <ContractForm
        open={creating}
        onOpenChange={setCreating}
        customerId={customerId}
        onSaved={(created) =>
          void navigate({ to: '/app/contracts/$contractId', params: { contractId: created.id } })
        }
      />
    </div>
  )
}
