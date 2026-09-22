import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { Empty, Figure, Header } from './lists'

/**
 * How the guide is doing (ORB-156): every download the members' route wrote down, the
 * distinct people behind them against everybody on file, the last week, and the latest
 * ones by name. Read-only: the rows are written by `GET /api/hub/me/guide` when a member
 * takes the file, and by nothing else.
 */
export function AdminGuida() {
  const stats = useQuery({ queryKey: ['guide-stats'], queryFn: () => admin.guideStats() })
  return (
    <>
      <Header title="La guida" count={stats.data?.totale} />
      {stats.isError ? (
        <Empty>Non riesco a leggere i download.</Empty>
      ) : stats.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : (
        <>
          <dl className="grid gap-4 border-b px-6 py-5 sm:grid-cols-3">
            <Figure label="Download" value={stats.data.totale} />
            <Figure
              label="Membri che l’hanno scaricata"
              value={stats.data.membri}
              note={`su ${stats.data.membri_totali}`}
            />
            <Figure label="Ultimi 7 giorni" value={stats.data.ultimi_7_giorni} />
          </dl>
          {stats.data.recenti.length === 0 ? (
            <Empty>Nessun download ancora.</Empty>
          ) : (
            <div className="px-6 pb-6">
              <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Chi</TableHead>
                      <TableHead className="text-right">Quando</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.data.recenti.map((download) => (
                      <TableRow key={download.id}>
                        <TableCell>
                          <Link
                            to="/admin/freelance/$id"
                            params={{ id: download.freelancer_id }}
                            className="font-medium hover:underline"
                          >
                            {download.nome} {download.cognome}
                          </Link>
                          <p className="text-xs text-muted-foreground">{download.email}</p>
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground">
                          {formatDateTime(download.downloaded_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </>
      )}
    </>
  )
}
