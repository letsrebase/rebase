import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, type CampaignListItem } from '@/lib/api'
import { CAMPAIGN_STATE_LABELS } from '@/lib/campaigns'
import { formatDateTime } from '@/lib/format'
import { Empty, Header } from './lists'

function when(item: CampaignListItem): string {
  const moment = item.inviata_at ?? item.programmata_per
  return moment ? formatDateTime(moment) : ''
}

/** «Campagne» (P-REB-41): every campaign, newest first, with what left and what came back. */
export function AdminCampagne() {
  const list = useQuery({ queryKey: ['campaigns'], queryFn: admin.campaigns })
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
                      <Badge variant="pill">{CAMPAIGN_STATE_LABELS[item.stato]}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{when(item)}</TableCell>
                    <TableCell className="text-sm">
                      {item.conteggi.inviate} inviate · {item.conteggi.consegnate} consegnate · {item.conteggi.rimbalzate} rimbalzate ·{' '}
                      {item.conteggi.saltate} saltate · {item.conteggi.fallite} fallite
                    </TableCell>
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
