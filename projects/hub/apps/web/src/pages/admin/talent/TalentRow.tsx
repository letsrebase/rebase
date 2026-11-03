import { Link } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { TableCell, TableRow } from '@rebase/ui/table'
import { WebLink } from '@/components/WebLink'
import { admin, type Talent } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { RemoteLabels, formatDate } from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { isHttpUrl } from '@/lib/url'
import { DuplicatePill } from '../DuplicateMarks'
import { StatePill, VettedPill } from '../list-kit'
import { TalentMenu } from './TalentMenu'

export function TalentRow({ item }: { item: Talent }) {
  const name = [item.nome, item.last_name].filter(Boolean).join(' ')
  const to = item.status === 'lead' ? '/admin/talent/$id' : '/admin/freelance/$id'
  const client = useQueryClient()
  // «Segna come verificato» / «Togli la verifica» (REB-518): the list is read again, so
  // the pill follows the server's word, and so does the talent's own page.
  const vet = useMutation({
    mutationFn: (vetted: boolean) => admin.setVetted(item.id, vetted),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.talents() })
      void client.invalidateQueries({ queryKey: keys.freelancer(item.id) })
    },
  })
  const vetFailure = failureMessage(vet.error, 'Non riesco a salvare la verifica.')
  return (
    <TableRow>
      <TableCell>
        <Link to={to} params={{ id: item.id }} className="font-medium hover:underline">
          {name || '—'}
        </Link>
        <p className="text-xs text-muted-foreground">{item.email}</p>
        {vetFailure && (
          <p role="alert" className="text-xs text-destructive">
            {vetFailure}
          </p>
        )}
      </TableCell>
      <TableCell>
        <div className="flex flex-wrap items-center gap-1.5">
          <StatePill status={item.status} />
          {item.vetted_at && <VettedPill />}
          <DuplicatePill others={item.duplicate_possible_of} />
        </div>
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        {item.daily_rate === null ? '—' : formatMoney(item.daily_rate)}
      </TableCell>
      <TableCell className="text-muted-foreground">{remoteLabel(item.remote)}</TableCell>
      <TableCell>
        {item.linkedin_url && isHttpUrl(item.linkedin_url) ? (
          <WebLink
            href={item.linkedin_url}
            aria-label={`LinkedIn di ${name || item.email}`}
            className="text-muted-foreground hover:text-foreground"
          >
            LinkedIn
          </WebLink>
        ) : (
          '—'
        )}
      </TableCell>
      <TableCell className="text-muted-foreground">{item.origin}</TableCell>
      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="w-12 text-right">
        {item.status !== 'lead' && (
          <TalentMenu
            id={item.id}
            label={name || item.email}
            vetted={!!item.vetted_at}
            vetting={vet.isPending}
            onVet={(vetted) => vet.mutate(vetted)}
          />
        )}
      </TableCell>
    </TableRow>
  )
}

/** `TalentRead.remote` is `str | None`, not narrowed to `Remote` (REB-558 review): the
 *  database column allows any string, so a value outside `RemoteLabels`' three keys
 *  reads «—» rather than rendering blank. */
function remoteLabel(value: string | null): string {
  if (value === null) return '—'
  return (RemoteLabels as Record<string, string>)[value] ?? '—'
}
