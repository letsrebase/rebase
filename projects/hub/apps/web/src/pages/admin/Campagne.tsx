import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type CampaignListItem } from '@/lib/api'
import { campaignStateLabel, isStalled, listRefetchEvery, outcomeLine } from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { ConfirmAction } from './Campagna'
import { Empty, Header } from './lists'

function when(item: CampaignListItem): string {
  const moment = item.inviata_at ?? item.programmata_per
  return moment ? formatDateTime(moment) : ''
}

/** «Elimina» on a draft's row (REB-524), behind the same second click as on its page. */
function DeleteDraft({ id }: { id: string }) {
  const client = useQueryClient()
  const remove = useMutation({
    mutationFn: () => admin.deleteCampaign(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: ['campaigns'] }),
  })
  const failure =
    remove.error instanceof ApiError ? remove.error.message : remove.error ? 'Non riesco a eliminare la bozza.' : null
  return (
    <div className="space-y-1">
      <ConfirmAction
        label="Elimina"
        question="Eliminare la bozza?"
        pendingLabel="Elimino…"
        pending={remove.isPending}
        onConfirm={() => remove.mutate()}
        variant="outline"
      />
      {failure && (
        <p role="alert" className="text-xs text-destructive">
          {failure}
        </p>
      )}
    </div>
  )
}

/** «Campagne» (P-REB-41): every campaign, newest first, with what left and what came
 *  back, «Invio fermo» and its reason on a stopped send, «Elimina» on a draft. It
 *  rereads itself every 30 s while a campaign is scheduled or sending. */
export function AdminCampagne() {
  const list = useQuery({
    queryKey: ['campaigns'],
    queryFn: admin.campaigns,
    refetchInterval: (query) => listRefetchEvery(query.state.data?.items ?? []),
  })
  const items = list.data?.items ?? []
  return (
    <div>
      <Header title="Campagne" count={list.data ? items.length : undefined}>
        <Button asChild>
          <Link to="/admin/campaigns/new">Nuova campagna</Link>
        </Button>
      </Header>
      {list.isError && <Empty>Non riesco a leggere le campagne. Riprova tra poco.</Empty>}
      {list.data && items.length === 0 && <Empty>Ancora nessuna campagna.</Empty>}
      {items.length > 0 && (
        <div className="px-6 pt-6 pb-6">
          <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campagna</TableHead>
                  <TableHead>Stato</TableHead>
                  <TableHead>Quando</TableHead>
                  <TableHead>Esito</TableHead>
                  <TableHead className="w-40">
                    <span className="sr-only">Azioni</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <Link to="/admin/campaigns/$id" params={{ id: item.id }} className="font-medium hover:underline">
                        {item.nome}
                      </Link>
                      <p className="text-xs text-muted-foreground">{item.oggetto}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant="pill" dot={isStalled(item) ? 'danger' : undefined}>
                        {campaignStateLabel(item)}
                      </Badge>
                      {isStalled(item) && <p className="mt-1 text-xs text-destructive">{item.fermo_motivo}</p>}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{when(item)}</TableCell>
                    <TableCell className="text-sm">{outcomeLine(item.conteggi, item.azione)}</TableCell>
                    <TableCell>{item.stato === 'bozza' && <DeleteDraft id={item.id} />}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  )
}
