import { Link } from '@tanstack/react-router'
import type { components } from '@/lib/api-types'
import { formatDate, formatPeriod } from '@/features/invoices/format'
import { useContract } from './queries'

type ChainContract = Pick<
  components['schemas']['ContractDetail'],
  'contratto_precedente_id' | 'contratto_successivo_id'
>

interface LinkRowProps {
  label: string
  /** What the link says until the neighbour's own title has loaded, and if it never does. */
  fallback: string
  contractId: string
}

/** One end of the chain: the neighbour's title as the link and its dates under it. The
 *  read is the same query as the neighbour's own page, so following the link is instant;
 *  while it loads, or if it fails, the link still works with its plain name. */
function ChainRow({ label, fallback, contractId }: LinkRowProps) {
  const neighbour = useContract(contractId)
  const row = neighbour.data
  const dates =
    row === undefined
      ? null
      : row.fine === null
        ? `dal ${formatDate(row.inizio)}`
        : formatPeriod(row.inizio, row.fine)

  return (
    <div className="flex justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right">
        <Link
          className="underline underline-offset-4"
          to="/app/contracts/$contractId"
          params={{ contractId }}
        >
          {row?.titolo ?? fallback}
        </Link>
        {dates !== null && <span className="block text-xs text-muted-foreground">{dates}</span>}
      </span>
    </div>
  )
}

/**
 * «Rinnovato da» and «Rinnovato in» in the Termini card: the two ends of the renewal
 * chain this contract sits on (REB-763). Each row appears only when its end exists, and
 * the card shows nothing at all for a contract that was never renewed and is not one.
 * Not the «Rinnovo» row, which is the renewal type the contract was written with.
 */
export function RenewalChain({ contract }: { contract: ChainContract }) {
  const previous = contract.contratto_precedente_id
  const next = contract.contratto_successivo_id
  return (
    <>
      {previous && (
        <ChainRow label="Rinnovato da" fallback="Contratto precedente" contractId={previous} />
      )}
      {next && <ChainRow label="Rinnovato in" fallback="Contratto successivo" contractId={next} />}
    </>
  )
}
