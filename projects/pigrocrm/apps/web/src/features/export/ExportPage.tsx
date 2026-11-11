import { PackageOpen } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { toProblem } from '@/lib/api'
import { cn } from '@rebase/ui/cn'
import { Tabs, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { ExportsTab } from './ExportsTab'
import type { Attempt, BuildTarget } from './labels'
import { LinksTab } from './LinksTab'
import { PrepareTab } from './PrepareTab'
import { useBuildExport, useExportTotal } from './queries'
import { TABS, type ExportTab } from './search'

const INLINE_PANEL = '(min-width: 1024px)'

/**
 * «Esporta per il commercialista» (REB-1028, the approved brief of 2026-11-10): where an admin
 * prepares the archive of one company and one period and finds the ones already made. The tab and
 * the open export are in the URL; what the page shows is the API's to decide, an admin's alone.
 *
 * Built here: «Prepara», the form with the readiness checklist («Il periodo è pronto?») and, where
 * the space has staff, the «Tipo» «Variazioni paghe» and the pay parts to tick (REB-1048);
 * «Esportazioni», the stored exports with their panel, the download, the share link (with a code
 * for an export that carries pay) and the copy in Drive (REB-1047); and «Link condivisi», every
 * link of the space with its openings.
 */
export function ExportPage({
  tab,
  item,
  link = null,
  onTabChange,
  onItemChange,
  onLinkChange = () => {},
  onOpenLink = () => {},
  onOpenExport = onItemChange,
}: {
  tab: ExportTab
  item: string | null
  /** The open link of «Link condivisi». */
  link?: string | null
  onTabChange: (next: ExportTab) => void
  onItemChange: (next: string | null) => void
  onLinkChange?: (next: string | null) => void
  /** Goes to a link of an export, from the export's panel, in «Link condivisi». */
  onOpenLink?: (linkId: string) => void
  /** Goes to an export, from a link's panel, in «Esportazioni». */
  onOpenExport?: (exportId: string) => void
}) {
  const inline = useMediaQuery(INLINE_PANEL)
  const total = useExportTotal()
  const build = useBuildExport()
  const [attempts, setAttempts] = useState<Attempt[]>([])
  const counter = useRef(0)
  // A build outlives the page: the admin may leave while it runs, and finding the export done is
  // the list's job. Opening its panel is not: it only happens while this page is still the place.
  const here = useRef(true)
  // Where the admin is when a build ends: a finished build opens its panel only for someone looking
  // at the list with nothing open, never over the form or another export.
  const where = useRef({ tab, item })
  useEffect(() => {
    where.current = { tab, item }
  }, [tab, item])
  useEffect(() => {
    here.current = true
    return () => {
      here.current = false
    }
  }, [])

  /** Starts a build and puts its row in the list at once. A refusal stays as a row with its
   *  reason; a retry is a new export asked from the same target. */
  function start(target: BuildTarget) {
    counter.current += 1
    const key = `attempt-${counter.current}`
    setAttempts((previous) => [{ key, target, status: 'building', reason: null }, ...previous])
    if (where.current.tab !== 'exports') onTabChange('exports')
    build.mutateAsync(target.request).then(
      (created) => {
        setAttempts((previous) => previous.filter((other) => other.key !== key))
        if (here.current && where.current.tab === 'exports' && where.current.item === null) {
          onItemChange(created.id)
        }
      },
      (error: unknown) =>
        setAttempts((previous) =>
          previous.map((other) =>
            other.key === key ? { ...other, status: 'failed', reason: toProblem(error).detail } : other,
          ),
        ),
    )
  }

  function retry(attempt: Attempt) {
    setAttempts((previous) => previous.filter((other) => other.key !== attempt.key))
    start(attempt.target)
  }

  function dismiss(attempt: Attempt) {
    setAttempts((previous) => previous.filter((other) => other.key !== attempt.key))
  }

  return (
    <Tabs
      value={tab}
      onValueChange={(next) => onTabChange(next as ExportTab)}
      // From `lg` the exports and links tabs are as tall as the window and its list and panel scroll inside
      // it, so the panel's download is always in view; below it the page flows.
      className={cn('flex min-h-full flex-col', inline && tab !== 'prepare' && 'h-full')}
    >
      <PageHeader
        icon={PackageOpen}
        title="Esporta per il commercialista"
        description="Un archivio ZIP per azienda e periodo: registri, documenti e scadenze, con l’indice di ciò che contiene."
        tabs={
          <TabsList variant="line">
            {TABS.map((candidate) => (
              <TabsTrigger key={candidate.id} value={candidate.id}>
                {candidate.label}
                {candidate.id === 'exports' && (total.data ?? 0) > 0 ? (
                  <span className="ml-2 tabular-nums text-muted-foreground">{total.data}</span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        }
      />
      {tab === 'prepare' ? (
        <PrepareTab onCreate={start} />
      ) : tab === 'exports' ? (
        <ExportsTab
          item={item}
          attempts={attempts}
          onItemChange={onItemChange}
          onRetry={retry}
          onDismiss={dismiss}
          onPrepare={() => onTabChange('prepare')}
          onOpenLink={onOpenLink}
        />
      ) : (
        <LinksTab
          link={link}
          onLinkChange={onLinkChange}
          onOpenExport={onOpenExport}
          onPrepareExports={() => onTabChange('exports')}
        />
      )}
    </Tabs>
  )
}
