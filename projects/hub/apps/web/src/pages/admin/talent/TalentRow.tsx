import { Link } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { TableCell, TableRow } from '@rebase/ui/table'
import { WebLink } from '@/components/WebLink'
import { admin, type Talento } from '@/lib/api'
import { failureMessage } from '@/lib/adminList'
import { REMOTO_LABELS, formatDate } from '@/lib/format'
import { formatMoney } from '@rebase/ui/format'
import { keys } from '@/lib/query-keys'
import { isHttpUrl } from '@/lib/url'
import { DuplicatePill } from '../DuplicateMarks'
import { StatePill, VettedPill } from '../list-kit'
import { TalentoMenu } from './TalentMenu'

export function TalentoRow({ item }: { item: Talento }) {
  const name = [item.nome, item.cognome].filter(Boolean).join(' ')
  const to = item.stato === 'lead' ? '/admin/talent/$id' : '/admin/freelance/$id'
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
          <StatePill stato={item.stato} />
          {item.vetted_at && <VettedPill />}
          <DuplicatePill others={item.possibile_duplicato_di} />
        </div>
      </TableCell>
      <TableCell className="text-right text-muted-foreground">
        {item.tariffa_giornaliera === null ? '—' : formatMoney(item.tariffa_giornaliera)}
      </TableCell>
      <TableCell className="text-muted-foreground">{remotoLabel(item.remoto)}</TableCell>
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
      <TableCell className="text-muted-foreground">{item.origine}</TableCell>
      <TableCell className="text-right text-muted-foreground">{formatDate(item.created_at)}</TableCell>
      <TableCell className="w-12 text-right">
        {item.stato !== 'lead' && (
          <TalentoMenu
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

/** `TalentoRead.remoto` is `str | None`, not narrowed to `Remoto` (REB-558 review): the
 *  database column allows any string, so a value outside `REMOTO_LABELS`' three keys
 *  reads «—» rather than rendering blank. */
function remotoLabel(value: string | null): string {
  if (value === null) return '—'
  return (REMOTO_LABELS as Record<string, string>)[value] ?? '—'
}
