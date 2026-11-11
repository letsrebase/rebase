import { useMemo, useState, type ReactNode } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { toProblem } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useModules } from '@/lib/modules'
import { tenantPrefix } from '@/lib/tenant'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { toast } from '@rebase/ui/sonner'
import {
  CONTROL_TONES,
  LINK_LABELS,
  EXPORT_KIND_LABELS,
  LINK_TONES,
  MISSING_SHOWN,
  STATUS_LABELS,
  STATUS_TONES,
  andOthers,
  controlLabel,
  driveWrittenLine,
  formatDateTime,
  formatDay,
  formatMebibytes,
  linkStateOf,
  openPoints,
  openingsWord,
  partCount,
  partsWord,
  quantity,
  readIndex,
  statusOf,
  type ExportIndex,
  type IndexLine,
  type StoredExport,
} from './labels'
import { periodName } from './periods'
import {
  useBuilderNames,
  useCompanyName,
  useDriveStatus,
  useDriveWrites,
  useExport,
  useExportLinks,
  useRevokeLink,
  useWriteToDrive,
} from './queries'
import { Readiness } from './Readiness'
import { ShareLinkDialog } from './ShareLinkDialog'

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

/** The parts a build wrote, as the index says them: each with its files and their counts, and a
 *  part the company's fiscal pack left out named as such. A part a module left out and one whose
 *  source is not built are not drawn, so the panel never names a module that is off. */
function Contents({ index }: { index: ExportIndex }) {
  const lines = index.lines.filter(
    (line) => line.status === 'included' || (line.status === 'omitted' && line.cause === 'pack'),
  )
  const sections = [...new Set(lines.map((line) => line.section))]
  if (lines.length === 0) {
    return <p className="text-sm text-muted-foreground">L’indice non elenca parti.</p>
  }
  return (
    <div className="space-y-3">
      {sections.map((section) => (
        <div key={section} className="space-y-1">
          {sections.length > 1 ? (
            <h4 className="text-xs font-medium text-muted-foreground">{section}</h4>
          ) : null}
          <ul className="divide-y border">
            {partsOf(lines.filter((line) => line.section === section)).map((part) => (
              <li key={part.key} className="space-y-0.5 px-3 py-2 text-sm">
                <p className="font-medium">{part.label}</p>
                {part.omitted ? (
                  <p className="text-xs text-muted-foreground">
                    Non si applica al regime fiscale.
                  </p>
                ) : (
                  part.entries.map((entry) => (
                    <p
                      key={`${entry.path}`}
                      className="flex justify-between gap-3 text-xs text-muted-foreground"
                    >
                      <span className="min-w-0 break-words">{entry.path || 'file originali'}</span>
                      <span className="shrink-0 tabular-nums">
                        {entry.count === null || entry.unit === null
                          ? ''
                          : quantity(entry.count, entry.unit)}
                      </span>
                    </p>
                  ))
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

interface PartGroup {
  key: string
  label: string
  omitted: boolean
  entries: { path: string; count: number | null; unit: 'rows' | 'files' | null }[]
}

/** The lines of one section grouped by part, in the order the index wrote them. */
function partsOf(lines: readonly IndexLine[]): PartGroup[] {
  const groups = new Map<string, PartGroup>()
  for (const line of lines) {
    const group = groups.get(line.key) ?? {
      key: line.key,
      label: line.label,
      omitted: line.status === 'omitted',
      entries: [],
    }
    group.entries.push({ path: line.path ?? '', count: line.count, unit: line.unit })
    groups.set(line.key, group)
  }
  return [...groups.values()]
}

/** The check as it was when the export was made: the documents the build could not attach, the
 *  first few by name and the rest counted. It is never recomputed after the build. */
function Check({ index }: { index: ExportIndex }) {
  const readiness = index.readiness
  const list =
    readiness === null || readiness.checks.length === 0 ? null : (
      <Readiness checks={readiness.checks} fix={false} label="Il periodo com’era alla creazione" />
    )
  if (index.missing.length === 0) {
    return (
      <div className="space-y-2">
        {list}
        <p className="text-sm text-muted-foreground">Tutti i documenti erano al loro posto.</p>
      </div>
    )
  }
  const rest = andOthers(index.missing.length)
  return (
    <div className="space-y-2">
      {list}
      <p className="text-sm">
        {index.missing.length === 1
          ? 'Un documento non è stato allegato.'
          : `${index.missing.length} documenti non sono stati allegati.`}
      </p>
      <ul className="divide-y border">
        {index.missing.slice(0, MISSING_SHOWN).map((item, position) => (
          <li key={`${item.document}-${item.kind}-${position}`} className="px-3 py-2 text-sm">
            <p className="break-words font-medium">
              {item.document}
              {item.kind === '' ? '' : ` · ${item.kind}`}
            </p>
            <p className="text-xs text-muted-foreground">{item.reason}</p>
          </li>
        ))}
      </ul>
      {rest === null ? null : (
        <p className="text-xs text-muted-foreground">
          {rest}: l’elenco completo è nell’indice dentro lo ZIP.
        </p>
      )}
    </div>
  )
}

/** The links of this export: state, expiry, count of openings and «Revoca», newest first. The
 *  address is never here: it was shown once, when the link was made. */
function Links({ exportId, onOpenLink }: { exportId: string; onOpenLink: (id: string) => void }) {
  const links = useExportLinks(exportId)
  const revoke = useRevokeLink()
  if (links.isError) return <QueryErrorBanner error={links.error} onRetry={() => void links.refetch()} />
  if (links.isPending) return <LoadingState label="Leggo i link…" />
  if (links.data.length === 0) {
    return <p className="text-sm text-muted-foreground">Nessun link per questa esportazione.</p>
  }
  return (
    <ul className="divide-y border" aria-label="Link dell’esportazione">
      {links.data.map((link) => {
        const state = linkStateOf(link)
        return (
          <li
            key={link.id}
            className={cn('space-y-1 px-3 py-2 text-sm', state !== 'active' && 'text-muted-foreground')}
          >
            <div className="flex items-center justify-between gap-2">
              <StatusPill tone={LINK_TONES[state]}>{LINK_LABELS[state]}</StatusPill>
              {state === 'active' ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={revoke.isPending}
                  onClick={() =>
                    revoke.mutate(link.id, {
                      onSuccess: () => toast.success('Link revocato: chi lo ha non scarica più'),
                      onError: (error) => toast.error(toProblem(error).detail),
                    })
                  }
                >
                  Revoca
                </Button>
              ) : null}
            </div>
            <p className="text-xs tabular-nums">
              Creato il {formatDateTime(link.created_at)} · scade il {formatDay(link.expires_at)}
            </p>
            {link.recipient_email === null ? null : (
              <p className="break-all text-xs">Con codice, a {link.recipient_email}</p>
            )}
            <p className="text-xs">
              {link.download_count === 0 ? (
                openingsWord(0)
              ) : (
                <button
                  type="button"
                  className="underline underline-offset-2"
                  onClick={() => onOpenLink(link.id)}
                >
                  {openingsWord(link.download_count)}
                </button>
              )}
            </p>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * Writing the export to the space's Drive: the button only when the storage is Drive (a person's
 * act, no agent has it), the sentence that says why not otherwise, and where the copies went. A
 * Drive that cannot be reached is a red band with the way to Drive's own page.
 */
function DriveDelivery({ exportId }: { exportId: string }) {
  const status = useDriveStatus()
  const writes = useDriveWrites(exportId)
  const write = useWriteToDrive(exportId)
  const latest = writes.data?.[0]
  if (status.isError) return <QueryErrorBanner error={status.error} onRetry={() => void status.refetch()} />
  if (status.isPending) return null
  const drive = status.data
  const opener = (
    <a className="underline underline-offset-2" href={`${tenantPrefix}/app/settings/drive`}>
      Apri Google Drive
    </a>
  )
  if (!drive.available) {
    return (
      <p className="text-sm text-muted-foreground">
        {drive.reason === 'not_connected' ? (
          <>
            Google Drive è la cartella dei file di questo spazio, ma non è ancora collegato. {opener}
          </>
        ) : (
          'Questo spazio salva i file sul server, non su Google Drive: la consegna su Drive non è disponibile.'
        )}
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <Button
        variant="outline"
        className="w-full sm:w-auto"
        disabled={write.isPending}
        onClick={() => write.mutate(undefined, { onSuccess: () => toast.success('Scritta su Drive') })}
      >
        {write.isPending ? 'Scrivo su Drive…' : 'Scrivi su Drive'}
      </Button>
      {write.isError ? (
        <p role="alert" className="border border-destructive px-3 py-2 text-sm text-destructive">
          {toProblem(write.error).detail} {opener}
        </p>
      ) : null}
      {latest === undefined ? null : (
        <p className="break-all text-xs text-muted-foreground">{driveWrittenLine(latest)}</p>
      )}
    </div>
  )
}

/** The delivery of the export, the panel's foot: the link is how the file reaches the accountant,
 *  so it is the one strong button; the download and the Drive copy are quiet. */
function Delivery({ item }: { item: StoredExport }) {
  const [linkOpen, setLinkOpen] = useState(false)
  if (item.parts.length === 0) return null
  const many = item.parts.length > 1
  return (
    <Section title="Consegna">
      <Button className="w-full sm:w-auto" onClick={() => setLinkOpen(true)}>
        {item.payroll ? 'Crea un link con codice' : 'Crea un link per il commercialista'}
      </Button>
      <ShareLinkDialog
        exportId={item.id}
        payroll={item.payroll}
        open={linkOpen}
        onOpenChange={setLinkOpen}
      />
      {many ? (
        <p className="text-sm text-muted-foreground">
          {item.parts.length} file ZIP: ognuno si apre da solo, e l’indice dentro il primo dice
          quale contiene cosa.
        </p>
      ) : null}
      <div className="flex flex-col gap-2">
        {item.parts.map((part) => (
          <DownloadButton
            key={part.part}
            path={part.download_url}
            variant="outline"
            className="w-full justify-between sm:w-auto"
          >
            <span>{many ? `Scarica il file ${part.part} di ${item.parts.length}` : 'Scarica lo ZIP'}</span>
            <span className="ml-3 tabular-nums opacity-80">{formatMebibytes(part.size_bytes)}</span>
          </DownloadButton>
        ))}
      </div>
      <DriveDelivery exportId={item.id} />
    </Section>
  )
}

/**
 * The open export (the approved brief): its index, the check as it was made, its links, the
 * delivery and the retention line. An export past its 13 months is a trace with nothing to
 * deliver, and its links are over with it.
 */
export function ExportPanel({
  id,
  onClose,
  onOpenLink,
}: {
  id: string
  onClose: () => void
  onOpenLink: (linkId: string) => void
}) {
  const detail = useExport(id)
  const companyName = useCompanyName()
  const builder = useBuilderNames()
  const { user } = useAuth()
  const modules = useModules()
  const index = useMemo(
    () => (detail.data ? readIndex(detail.data.checklist) : null),
    [detail.data],
  )

  if (detail.isError) {
    return (
      <div className="space-y-3 p-4">
        <QueryErrorBanner error={detail.error} />
        <Button variant="outline" size="sm" onClick={onClose}>
          Chiudi
        </Button>
      </div>
    )
  }
  if (!detail.data || index === null) {
    return <LoadingState label="Leggo l’esportazione…" className="p-4" />
  }

  const item = detail.data
  const status = statusOf(item)
  const open = openPoints(index)
  // «Variazioni paghe» has no checklist, so it has no check to wear.
  const checked = item.kind === 'accountant'
  const when = formatDateTime(item.built_at)
  const who = builder(item.built_by, user?.id ?? null).by

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
        <header className="space-y-2">
          <h2 className="break-words text-lg font-semibold">{companyName(item)}</h2>
          <p className="text-sm">
            {periodName(item.period_from, item.period_to)} · {partsWord(partCount(index))}
          </p>
          {modules.on('staff') ? (
            <p className="text-xs text-muted-foreground">Tipo: {EXPORT_KIND_LABELS[item.kind]}</p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <StatusPill tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</StatusPill>
            {status === 'ready' && checked ? (
              <StatusPill tone={CONTROL_TONES[open === 0 ? 'complete' : 'open']}>
                {controlLabel(open === 0 ? 'complete' : 'open', open)}
              </StatusPill>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">
            Creata il {when} da {who}
          </p>
        </header>

        {status === 'deleted' ? (
          <p className="border bg-background px-3 py-2 text-sm text-muted-foreground">
            Eliminata il {formatDay(item.deleted_at ?? item.expires_at)}: oltre i 13 mesi. Se
            serve, si prepara di nuovo.
          </p>
        ) : null}

        <Section title="Cosa c’è nello ZIP">
          <Contents index={index} />
        </Section>

        {checked ? (
          <Section title="Controllo alla creazione">
            <Check index={index} />
          </Section>
        ) : null}

        {status === 'ready' ? (
          <Section title="Link per il commercialista">
            <Links exportId={item.id} onOpenLink={onOpenLink} />
          </Section>
        ) : null}
      </div>

      {/* The delivery is the panel's foot and stays in view while the index scrolls: the download is
          the act this panel is for. */}
      {status === 'ready' ? (
        <footer className="space-y-3 border-t bg-card p-4">
          <Delivery item={item} />
          <p className="text-xs text-muted-foreground">
            Si conserva fino al {formatDay(item.expires_at)}, poi viene eliminata e ne resta
            traccia.
          </p>
        </footer>
      ) : null}
    </div>
  )
}
