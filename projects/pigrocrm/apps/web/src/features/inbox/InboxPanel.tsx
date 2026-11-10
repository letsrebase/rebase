import { Link } from '@tanstack/react-router'
import { ExternalLink, FileText } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { describeProposal, formatAmount } from '@/features/proposals/fields'
import { useProposal, useProposalTimeline } from '@/features/proposals/queries'
import { fetchWithRefresh, toProblem } from '@/lib/api'
import { useAuth, useCan, useCanWrite, useIsAdmin } from '@/lib/auth'
import { useLegalEntity } from '@/lib/legalEntity'
import { cn } from '@rebase/ui/cn'
import { DiscardDialog, DuplicateDialog, KindDialog } from './InboxDialogs'
import { IssuedReviewDialog } from './IssuedReviewDialog'
import {
  FILE_TYPE_LABELS,
  KIND_LABELS,
  STATE_LABELS,
  STATE_TONES,
  formatDay,
  formatSize,
  isUnread,
  needsCompany,
  visibilityLine,
  whyLine,
  type InboxItem,
  type InboxItemDetail,
} from './labels'
import {
  useClassifyItem,
  useCopiesOf,
  useDiscardItem,
  useInboxItem,
  useModelReads,
  useSetKind,
} from './queries'

type DialogKind = 'kind' | 'duplicate' | 'discard' | 'review' | null

/** The image of an item, fetched with the session (an `<img src>` could not carry it) as an
 *  object URL that is released when the panel moves on. `null` until it is there. */
function useFileUrl(id: string, enabled: boolean): string | null {
  const [loaded, setLoaded] = useState<{ id: string; url: string } | null>(null)
  useEffect(() => {
    if (!enabled) return
    let revoked = false
    let url: string | null = null
    void fetchWithRefresh(`/api/inbox/items/${id}/file`)
      .then((response) => (response.ok ? response.blob() : null))
      .then((blob) => {
        if (blob === null || revoked) return
        url = URL.createObjectURL(blob)
        setLoaded({ id, url })
      })
      .catch(() => {})
    return () => {
      revoked = true
      if (url !== null) URL.revokeObjectURL(url)
    }
  }, [id, enabled])
  return loaded?.id === id ? loaded.url : null
}

/** Opens a stored file in a new tab: the blob of the response, which carries the file's own type. */
async function openFile(id: string): Promise<void> {
  // The tab is opened inside the click, before the request: a browser blocks one opened later.
  const tab = window.open('', '_blank')
  try {
    const response = await fetchWithRefresh(`/api/inbox/items/${id}/file`)
    if (!response.ok) throw toProblem(await response.json().catch(() => null), response.status)
    const url = URL.createObjectURL(await response.blob())
    if (tab !== null) tab.location.href = url
    // Left to the tab: a revoked URL would cancel its load.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (error) {
    tab?.close()
    toast.error(toProblem(error).detail)
  }
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

function Rows({ rows }: { rows: readonly { label: string; value: ReactNode }[] }) {
  return (
    <dl className="border-t">
      {rows.map((row) => (
        <div key={row.label} className="flex justify-between gap-4 border-b py-2 text-sm">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 text-right font-medium">{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Who put the item there, in the words the person asking would use. */
function uploader(item: InboxItem, me: string | null): string {
  if (item.uploaded_by_id === null) return 'Lo spazio'
  return item.uploaded_by_id === me ? 'Tu' : 'Un collega'
}

/** What the item says it is: the FatturaPA's own figures, certain, one block per invoice. */
function ParsedInvoices({ fields }: { fields: Record<string, unknown> }) {
  const invoices = Array.isArray(fields.invoices) ? (fields.invoices as Record<string, unknown>[]) : []
  return (
    <div className="space-y-3">
      {invoices.map((invoice, index) => {
        const supplier = (invoice.supplier ?? {}) as Record<string, unknown>
        const customer = (invoice.customer ?? {}) as Record<string, unknown>
        return (
          <Rows
            key={`${String(invoice.number)}-${index}`}
            rows={[
              { label: 'Numero', value: String(invoice.number ?? '') },
              { label: 'Data', value: String(invoice.issue_date ?? '') },
              { label: 'Da', value: String(supplier.name ?? '') },
              { label: 'A', value: String(customer.name ?? '') },
              {
                label: 'Totale',
                value: (
                  <span className="tabular-nums">
                    {formatAmount(invoice.total, invoice.currency) ?? ''}
                  </span>
                ),
              },
            ]}
          />
        )
      })}
    </div>
  )
}

/**
 * The item's panel (the brief): the file, where it came from, what was read and by whom, the
 * company it is for, the other times it arrived, and the acts on it. Nothing here edits what was
 * read: the module that files a kind owns that, and a proposal is decided in «Proposte».
 *
 * `onOpen` moves to another item (the original of a copy, the ZIP an item came from); `onClose` is the
 * way out of a panel that could not read its item. A discarded or filed item stays open, with its new
 * state, until the person closes it.
 */
export function InboxPanel({
  id,
  onOpen,
  onClose,
}: {
  id: string
  onOpen: (id: string) => void
  onClose: () => void
}) {
  const detail = useInboxItem(id)
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
  if (!detail.data) return <LoadingState label="Leggo il documento…" className="p-4" />
  return <Loaded item={detail.data} onOpen={onOpen} />
}

function Loaded({ item, onOpen }: { item: InboxItemDetail; onOpen: (id: string) => void }) {
  const { user } = useAuth()
  const me = user?.id ?? null
  const admin = useIsAdmin()
  const canWrite = useCanWrite()
  const mayImport = useCan('confirm_invoice_import')
  const modelReads = useModelReads()
  const { legal_entities } = useLegalEntity()
  const [dialog, setDialog] = useState<DialogKind>(null)
  const discard = useDiscardItem()
  const classify = useClassifyItem()
  const setKind = useSetKind()
  const proposalId = item.proposal_id ?? null
  const proposal = useProposal(proposalId ?? '')
  const timeline = useProposalTimeline(proposalId ?? '', proposalId !== null)
  const copies = useCopiesOf(item.id, item.state !== 'duplicate')
  const preview = useFileUrl(item.id, item.has_file && (item.file_type === 'png' || item.file_type === 'jpeg'))

  const waiting = item.state === 'new' || item.state === 'proposed'
  const certain = item.kind !== null && item.parsed_fields !== null
  const read = proposal.data ? describeProposal(proposal.data) : null
  const createdBy = timeline.data?.find((entry) => entry.kind === 'created')?.actor_type
  const company = legal_entities.find((entity) => entity.id === item.legal_entity_id)
  const visibility = visibilityLine(item, me, admin)
  const sales = item.kind === 'sales_invoice' && item.state === 'new'

  const provenance = certain
    ? 'Dal file: dato certo'
    : proposalId !== null && createdBy !== undefined
      ? createdBy === 'mcp'
        ? 'Letto dal tuo agente'
        : 'Letto dal modello'
      : item.reason === 'Tipo indicato da una persona'
        ? 'Indicato da una persona'
        : null

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
        <header className="space-y-2">
          <h2 className="break-words text-lg font-semibold">{item.original_name}</h2>
          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
            <StatusPill tone={STATE_TONES[item.state]}>{STATE_LABELS[item.state]}</StatusPill>
            <span>
              {item.file_type !== null ? FILE_TYPE_LABELS[item.file_type] : 'Tipo non accettato'} ·{' '}
              {formatSize(item.size_bytes)}
            </span>
          </div>
          <p className="text-sm">{whyLine(item)}</p>
          {visibility === null ? null : (
            <p className="text-xs text-muted-foreground">{visibility}</p>
          )}
        </header>

        <Section title="Il file">
          {preview === null ? null : (
            <img
              src={preview}
              alt={`Anteprima di ${item.original_name}`}
              className="max-h-64 w-full border object-contain"
            />
          )}
          {item.has_file ? (
            <div className="flex flex-wrap gap-2">
              {item.file_type === 'pdf' || item.file_type === 'png' || item.file_type === 'jpeg' ? (
                <Button variant="outline" size="sm" onClick={() => void openFile(item.id)}>
                  <ExternalLink className="mr-2 size-4" aria-hidden />
                  Apri il file
                </Button>
              ) : null}
              <DownloadButton variant="outline" size="sm" path={`/api/inbox/items/${item.id}/file`}>
                <FileText className="mr-2 size-4" aria-hidden />
                Scarica il file
              </DownloadButton>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {item.state === 'filed'
                ? 'Il file è nel registro, dove è stato archiviato.'
                : 'Il file di questo documento è quello dell’originale.'}
            </p>
          )}
        </Section>

        <Section title="Da dove arriva">
          <Rows
            rows={[
              { label: 'Caricato da', value: uploader(item, me) },
              { label: 'Il', value: formatDay(item.created_at) },
              ...(item.parent_id === null
                ? []
                : [
                    {
                      label: 'Dentro',
                      value: (
                        <button
                          type="button"
                          className="underline underline-offset-2"
                          onClick={() => onOpen(item.parent_id ?? '')}
                        >
                          l’archivio
                        </button>
                      ),
                    },
                  ]),
              ...(item.duplicate_of_id === null
                ? []
                : [
                    {
                      label: 'Doppione di',
                      value: (
                        <button
                          type="button"
                          className="underline underline-offset-2"
                          onClick={() => onOpen(item.duplicate_of_id ?? '')}
                        >
                          il file arrivato prima
                        </button>
                      ),
                    },
                  ]),
            ]}
          />
        </Section>

        <Section title="Cosa è stato letto">
          {provenance === null ? null : (
            <p className="text-xs font-medium text-muted-foreground">{provenance}</p>
          )}
          {item.parsed_fields !== null ? (
            <ParsedInvoices fields={item.parsed_fields} />
          ) : read !== null ? (
            read.groups.map((group) => (
              <div key={group.title} className="space-y-1">
                <h4 className="text-xs font-medium text-muted-foreground">{group.title}</h4>
                <Rows rows={group.rows} />
              </div>
            ))
          ) : proposal.isError ? (
            <QueryErrorBanner error={proposal.error} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {isUnread(item) ? 'Non è stato ancora letto.' : 'Non c’è niente di letto.'}
            </p>
          )}
          {proposalId === null ? null : (
            <Button asChild variant={item.state === 'proposed' ? 'default' : 'outline'} size="sm">
              <Link to="/app/proposals/$proposalId" params={{ proposalId }}>
                Apri la proposta
              </Link>
            </Button>
          )}
        </Section>

        <Section title="Per quale azienda">
          {company !== undefined ? (
            <p className="text-sm font-medium">{company.name}</p>
          ) : needsCompany(item) || item.legal_entity_id === null ? (
            legal_entities.length > 0 && canWrite && waiting ? (
              <Select
                value=""
                onValueChange={(value) =>
                  setKind.mutate(
                    { id: item.id, legal_entity_id: value },
                    {
                      onSuccess: () => toast.success('Azienda assegnata.'),
                      onError: (error) => toast.error(toProblem(error).detail),
                    },
                  )
                }
              >
                <SelectTrigger className="w-full" aria-label="Per quale azienda">
                  <SelectValue placeholder="Scegli l’azienda" />
                </SelectTrigger>
                <SelectContent>
                  {legal_entities.map((entity) => (
                    <SelectItem key={entity.id} value={entity.id}>
                      {entity.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <p className="text-sm text-muted-foreground">Ancora nessuna.</p>
            )
          ) : (
            <p className="text-sm text-muted-foreground">Un’azienda che non vedi.</p>
          )}
        </Section>

        {(copies.data ?? []).length === 0 ? null : (
          <Section title="Arrivata anche">
            <ul className="space-y-1 text-sm">
              {(copies.data ?? []).map((copy) => (
                <li key={copy.id}>
                  <button
                    type="button"
                    className="text-left underline underline-offset-2"
                    onClick={() => onOpen(copy.id)}
                  >
                    {copy.original_name}
                  </button>
                  <span className="text-muted-foreground"> · {formatDay(copy.created_at)}, stesso file</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        {item.kind === null ? null : (
          <p className="text-xs text-muted-foreground">Tipo: {KIND_LABELS[item.kind]}</p>
        )}
      </div>

      {canWrite && waiting ? (
        <div
          className={cn(
            'flex shrink-0 flex-wrap gap-2 border-t bg-card p-4 pb-[max(1rem,env(safe-area-inset-bottom))]',
          )}
        >
          {sales && mayImport ? (
            <Button onClick={() => setDialog('review')}>Rivedi e importa…</Button>
          ) : null}
          {isUnread(item) && modelReads ? (
            <Button
              variant="outline"
              disabled={classify.isPending}
              onClick={() =>
                classify.mutate(item.id, {
                  onError: (error) => toast.error(toProblem(error).detail),
                })
              }
            >
              Leggi con il modello
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => setDialog('kind')}>
            {item.kind === null ? 'Indica il tipo…' : 'Cambia tipo…'}
          </Button>
          <Button variant="outline" onClick={() => setDialog('duplicate')}>
            Segna come doppione…
          </Button>
          <Button variant="outline" onClick={() => setDialog('discard')}>
            Scarta…
          </Button>
        </div>
      ) : null}

      {dialog === 'review' ? <IssuedReviewDialog item={item} onClose={() => setDialog(null)} /> : null}
      {dialog === 'kind' ? (
        <KindDialog item={item} certain={certain} onClose={() => setDialog(null)} />
      ) : null}
      {dialog === 'duplicate' ? <DuplicateDialog item={item} onClose={() => setDialog(null)} /> : null}
      {dialog === 'discard' ? (
        <DiscardDialog
          count={1}
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await discard.mutateAsync({ id: item.id, reason })
            toast.success('Documento scartato.')
          }}
        />
      ) : null}
    </div>
  )
}
