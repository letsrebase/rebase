import { ChevronDown } from 'lucide-react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { nextStates, stateLabel } from './labels'
import { useSetContractState, type Contract, type ContractState } from './queries'

/**
 * Moves a contract between its states (REB-741). A `bozza` has one obvious next step and
 * gets it as a button, «Attiva»; every other move, from any state to any other, is in the
 * «Stato» menu. Each is one named act on the API (`POST /api/contracts/{id}/state`) and
 * one row in the contract's timeline, never a field an edit could flip by accident.
 * Ending a contract asks first: it is the one move that reads as a decision about the
 * engagement and not as bookkeeping, and the dialog costs less than finding out later.
 */
export function ContractStateActions({ contract }: { contract: Contract }) {
  const canChange = useCan('set_contract_state')
  const change = useSetContractState(contract.id)

  if (!canChange) return null

  function move(stato: ContractState) {
    if (
      stato === 'terminato' &&
      !window.confirm(`Terminare il contratto «${contract.titolo}»? Potrai riattivarlo in seguito.`)
    ) {
      return
    }
    change.mutate(stato, {
      onSuccess: () => toast.success(`Contratto: ${stateLabel(stato).toLowerCase()}`),
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  return (
    <>
      {contract.stato === 'bozza' && (
        <Button onClick={() => move('attivo')} disabled={change.isPending}>
          Attiva
        </Button>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" disabled={change.isPending}>
            Stato
            <ChevronDown className="ml-2 size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {nextStates(contract.stato).map((stato) => (
            <DropdownMenuItem key={stato} onSelect={() => move(stato)}>
              Segna come «{stateLabel(stato).toLowerCase()}»
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
