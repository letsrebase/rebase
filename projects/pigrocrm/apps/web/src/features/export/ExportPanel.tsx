import { useMemo, type ReactNode } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { useAuth } from '@/lib/auth'
import { Button } from '@rebase/ui/button'
import {
  CONTROL_TONES,
  MISSING_SHOWN,
  STATUS_LABELS,
  STATUS_TONES,
  andOthers,
  controlLabel,
  formatDateTime,
  formatDay,
  formatMebibytes,
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
import { useBuilderNames, useCompanyName, useExport } from './queries'

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
  if (index.missing.length === 0) {
    return <p className="text-sm text-muted-foreground">Tutti i documenti erano al loro posto.</p>
  }
  const rest = andOthers(index.missing.length)
  return (
    <div className="space-y-2">
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

/** The files of the export, one button each: one ZIP is the strong action, several are quiet. */
function Delivery({ item }: { item: StoredExport }) {
  if (item.parts.length === 0) return null
  const many = item.parts.length > 1
  return (
    <Section title="Consegna">
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
            variant={many ? 'outline' : 'default'}
            className="w-full justify-between sm:w-auto"
          >
            <span>{many ? `Scarica il file ${part.part} di ${item.parts.length}` : 'Scarica lo ZIP'}</span>
            <span className="ml-3 tabular-nums opacity-80">{formatMebibytes(part.size_bytes)}</span>
          </DownloadButton>
        ))}
      </div>
    </Section>
  )
}

/**
 * The open export (the approved brief): its index, the check as it was made, the delivery and
 * the retention line. Only the delivery that exists is drawn: the link and the Drive copy are
 * their own cards, and an export past its 13 months is a trace with nothing to deliver.
 */
export function ExportPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useExport(id)
  const companyName = useCompanyName()
  const builder = useBuilderNames()
  const { user } = useAuth()
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
  const open = index.missing.length
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
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <StatusPill tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</StatusPill>
            {status === 'ready' ? (
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

        <Section title="Controllo alla creazione">
          <Check index={index} />
        </Section>

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
