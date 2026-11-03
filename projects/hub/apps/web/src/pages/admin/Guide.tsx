import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { Empty, Figure, Header } from './list-kit'

/**
 * How the guide is doing (ORB-156): every download the members' route wrote down, the
 * distinct people behind them against everybody on file, the last week, and the latest
 * ones by name. Read-only: the rows are written by `GET /api/hub/me/guide` when a member
 * takes the file, and by nothing else.
 */
export function AdminGuide() {
  const stats = useQuery({ queryKey: keys.guideStats, queryFn: () => admin.guideStats() })
  return (
    <>
      <Header title="La guida" count={stats.data?.total} />
      {stats.isError ? (
        <Empty>Non riesco a leggere i download.</Empty>
      ) : stats.isPending ? (
        <Empty>Caricamento…</Empty>
      ) : (
        <>
          <dl className="grid gap-4 border-b px-6 py-5 sm:grid-cols-3">
            <Figure label="Download" value={stats.data.total} />
            <Figure
              label="Membri che l’hanno scaricata"
              value={stats.data.members}
              note={`su ${stats.data.total_members}`}
            />
            <Figure label="Ultimi 7 giorni" value={stats.data.last_7_days} />
          </dl>
          {stats.data.recent.length === 0 ? (
            <Empty>Nessun download ancora.</Empty>
          ) : (
            <div className="px-6 pt-6 pb-6">
              <div className="overflow-x-auto overflow-y-hidden border border-border bg-card">
                <Table scrollLabel="Download della guida">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Chi</TableHead>
                      <TableHead className="text-right">Quando</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {stats.data.recent.map((download) => (
                      <TableRow key={download.id}>
                        <TableCell>
                          {download.freelancer_id ? (
                            <Link
                              to="/admin/freelance/$id"
                              params={{ id: download.freelancer_id }}
                              className="font-medium hover:underline"
                            >
                              {download.first_name} {download.last_name}
                            </Link>
                          ) : (
                            <span className="font-medium">
                              {download.first_name} {download.last_name}
                            </span>
                          )}
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
