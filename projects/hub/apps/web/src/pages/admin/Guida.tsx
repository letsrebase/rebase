import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
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
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="px-6 py-2 font-medium">Chi</th>
                  <th className="px-6 py-2 text-right font-medium">Quando</th>
                </tr>
              </thead>
              <tbody>
                {stats.data.recenti.map((download) => (
                  <tr key={download.id} className="border-b last:border-0 hover:bg-muted">
                    <td className="px-6 py-2.5">
                      <Link
                        to="/admin/freelance/$id"
                        params={{ id: download.freelancer_id }}
                        className="font-medium hover:underline"
                      >
                        {download.nome} {download.cognome}
                      </Link>
                      <p className="text-xs text-muted-foreground">{download.email}</p>
                    </td>
                    <td className="px-6 py-2.5 text-right text-muted-foreground">
                      {formatDateTime(download.downloaded_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </>
  )
}
