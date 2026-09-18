import { Link, createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { Handshake, Pencil, ThumbsDown, Trophy } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from '@rebase/ui/sonner'
import { renderFieldValue } from '@/components/DynamicFieldRenderer'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { RowActions } from '@/components/RowActions'
import { Timeline } from '@/components/Timeline'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { EconomicsTab } from '@/features/analytics/EconomicsTab'
import { useCustomer } from '@/features/customers/queries'
import { DealForm, dealToFormValues } from '@/features/deals/DealForm'
import { DealStageBar } from '@/features/deals/DealStageBar'
import { displayNative, formatDate, formatHours, formatMoney } from '@/features/deals/columns'
import {
  useDeal,
  useDeleteDeal,
  useMoveDeal,
  useStages,
  useUpdateDeal,
  type Stage,
} from '@/features/deals/queries'
import { DocumentsTab } from '@/features/documents/DocumentsTab'
import { EmailTab } from '@/features/gmail/EmailTab'
import { useGmailConfigured } from '@/features/gmail/queries'
import { InvoicesTab } from '@/features/invoices/InvoicesTab'
import { NewProformaButton } from '@/features/invoices/NewProformaDialog'
import { TimeEntriesTab } from '@/features/time/TimeEntriesTab'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'

function Row({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  )
}

/** A heading inside the summary card: one card, several short sections, instead of the
 *  four boxes this page used to be. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="text-sm">
      <h3 className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * Split into its own component so `useCustomer` is only ever mounted -- and
 * therefore only ever called -- with a real id. `deal.customer_id` is required
 * and non-null on every real `Deal` (`DealCreate`'s own docstring: "a deal
 * without a customer has no economic meaning"), but `deal` itself is `undefined`
 * for the render or two before `useDeal` resolves -- during which a naive
 * `useCustomer(deal?.customer_id ?? '')` at the top of `DealDetail` would call
 * `useCustomer('')`. `features/people`'s own `$personId.tsx` hit exactly this
 * danger first: `/api/customers/{customer_id}` with an empty path segment does
 * not match that route, Starlette's default trailing-slash redirect lands the
 * request on plain `/api/customers` instead -- the *list* endpoint, with an
 * absolute URL that escapes the Vite dev proxy entirely -- and it still resolves
 * 200 with nothing to show for it. Mounting this only from inside the `if
 * (!deal) return ...` guard below, where `deal.customer_id` is guaranteed a real
 * id, is the same fix `LinkedCustomerRow` (people/$personId.tsx) already applies.
 */
function DealCustomerCard({ customerId }: { customerId: string }) {
  const customer = useCustomer(customerId)
  return (
    <Card>
      <CardContent className="pt-6">
        <h2 className="mb-3 font-semibold">Cliente</h2>
        <Row
          label="Ragione sociale"
          value={
            customer.data ? (
              <Link
                to="/app/clienti/$customerId"
                params={{ customerId }}
                className="underline underline-offset-2"
              >
                {customer.data.ragione_sociale}
              </Link>
            ) : (
              '…'
            )
          }
        />
        <Row label="P.IVA" value={customer.data ? displayNative(customer.data.partita_iva) : '…'} />
      </CardContent>
    </Card>
  )
}

/** The first stage of a kind, by pipeline position: what «Vinto» and «Perso» move to.
 *  A tenant may define more than one of either; the first is the conventional one. */
function firstOfKind(stages: Stage[], tipo: Stage['tipo']): Stage | undefined {
  return stages
    .filter((stage) => stage.tipo === tipo)
    .sort((a, b) => a.posizione - b.posizione)[0]
}

/**
 * The deal page, in the shape a Pipedrive deal has since 2026-09-09: the pipeline as a
 * bar across the top, one summary column on the left with everything the deal *is*,
 * and the recent activity on the right -- what happened to it, latest first. Won and
 * lost are two buttons in the header, and archiving moved behind the «⋯» menu, where
 * every list already keeps it (design spec §4).
 *
 * What left: the separate «Stato» and «Preventivo» cards and the paragraph pointing at
 * the Ore and Economia tabs. The figures are still here, in the summary, and the tabs
 * are one row above -- a note telling the reader where the tabs are was the page
 * admitting it had too many boxes.
 */
export function DealDetail() {
  const { dealId } = useParams({ from: '/app/deal/$dealId' })
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const [editing, setEditing] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const schema = useEntitySchema('deal')
  const { data: deal, isLoading, isError, error } = useDeal(dealId)
  const stages = useStages()
  const update = useUpdateDeal(dealId)
  const remove = useDeleteDeal()
  const move = useMoveDeal()
  // Shares the shell banner's cached health row -- see `useGmailConfigured`.
  const gmailConfigured = useGmailConfigured()

  if (isLoading) return <Skeleton className="m-8 h-96" />
  // See `routes/app/clienti/$customerId.tsx`'s identical guard for the full
  // reasoning: without this, a 500/502/dropped connection reads as "Deal non
  // trovato." exactly like a genuine 404 does, since both leave `deal` undefined
  // once loading ends. `toProblem(error).status` is the one place that is never
  // ambiguous; only a real 404 keeps this screen's own wording.
  if (isError) {
    if (toProblem(error).status === 404) return <p className="p-8">Deal non trovato.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={error} />
      </div>
    )
  }
  if (!deal) return <p className="p-8">Deal non trovato.</p>

  const custom = schema.data?.custom_fields ?? []
  const stageList = stages.data ?? []
  const stage = stageList.find((item) => item.id === deal.pipeline_stage_id)
  const isOpen = stage?.tipo === 'open'
  const won = firstOfKind(stageList, 'won')
  const lost = firstOfKind(stageList, 'lost')

  function archive() {
    if (!deal) return
    // Soft delete or not, the UI itself offers no visible undo (restoring today
    // means calling `POST /api/deals/{id}/restore` directly, not a button here)
    // -- a plain confirm is a cheap guard against a misclick, not a client-side
    // re-implementation of any server rule. Mirrors `CustomerDetail`/
    // `PersonDetail`'s own `archive`.
    const confirmed = window.confirm(
      `Archiviare ${deal.nome}? Il deal non comparirà più negli elenchi, ma i dati restano e l'operazione è reversibile.`,
    )
    if (!confirmed) return

    remove.mutate(deal.id, {
      onSuccess: () => {
        toast.success('Deal archiviato')
        void navigate({ to: '/app/deal' })
      },
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  // No per-call `onError`: `useMoveDeal` toasts the server's message itself, for the
  // reason its own docstring gives. Success needs no toast either -- the bar moves.
  function moveTo(stageId: string) {
    move.mutate({ dealId, stageId })
  }

  // The header's second line: whose deal, and for how much. `customer_ragione_sociale`
  // rides on `DealRead` so this costs no request; the Collegamenti tab keeps the full
  // customer card.
  const subtitle = [deal.customer_ragione_sociale, formatMoney(deal.valore_previsto)]
    .filter((part): part is string => typeof part === 'string' && part !== '' && part !== '—')
    .join(' · ')

  return (
    <>
      <EntityDetailLayout
        icon={Handshake}
        title={deal.nome}
        subtitle={subtitle === '' ? 'Deal' : subtitle}
        entityType="deal"
        entityId={dealId}
        documents={<DocumentsTab owner={{ dealId }} />}
        invoices={<InvoicesTab owner={{ dealId }} />}
        hours={<TimeEntriesTab dealId={dealId} />}
        economics={<EconomicsTab dealId={dealId} />}
        emails={gmailConfigured ? <EmailTab entityType="deal" entityId={dealId} /> : undefined}
        actions={
          canWrite && (
            <>
              {/* First, and the only filled button here -- everything after it, the two
                  outcomes included, is an outline: a deal's header is opened to invoice
                  it far more often than to rename it, or even to close it. The dialog it
                  opens has nothing left to ask but the causale -- the customer, the deal
                  and the first line are all on this page already. `valore_previsto` is
                  nullable (`DealRead`), and an absent one starts the line at an empty
                  price rather than at a zero nobody typed. */}
              <NewProformaButton
                customerId={deal.customer_id}
                dealId={deal.id}
                prefill={{ descrizione: deal.nome, importo: deal.valore_previsto ?? '' }}
              />
              {/* Two outcomes, top right, only while the deal is still open: closing it
                  is the decision this page exists for, but «una sola cosa forte per
                  schermata» (UI revision spec §92) means the strong slot above is
                  already taken -- so both are outlines, and they are told apart by
                  their icon and their word, not by their weight. Reopening goes through
                  the bar, which is also where the person sees which stage it goes back
                  to. */}
              {isOpen && won && (
                <Button variant="outline" onClick={() => moveTo(won.id)} disabled={move.isPending}>
                  <Trophy className="mr-2 size-4" />
                  Vinto
                </Button>
              )}
              {isOpen && lost && (
                <Button
                  variant="outline"
                  onClick={() => moveTo(lost.id)}
                  disabled={move.isPending}
                >
                  <ThumbsDown className="mr-2 size-4" />
                  Perso
                </Button>
              )}
              <Button
                variant="outline"
                onClick={() => {
                  setProblem(null)
                  setEditing(true)
                }}
              >
                <Pencil className="mr-2 size-4" />
                Modifica
              </Button>
              <RowActions
                items={[
                  {
                    label: 'Archivia',
                    destructive: true,
                    disabled: remove.isPending,
                    onSelect: archive,
                  },
                ]}
              />
            </>
          )
        }
        overview={
          <div className="space-y-6">
            <DealStageBar
              deal={deal}
              stages={stageList}
              canMove={canWrite}
              busy={move.isPending}
              onMove={moveTo}
            />

            <div className="grid gap-6 lg:grid-cols-3">
              <Card className="lg:col-span-1">
                <CardContent className="space-y-5 pt-6">
                  <Section title="Riepilogo">
                    <Row label="Valore previsto" value={formatMoney(deal.valore_previsto)} />
                    <Row label="Probabilità" value={`${deal.probabilita}%`} />
                    <Row label="Chiusura prevista" value={formatDate(deal.data_chiusura_prevista)} />
                    {deal.chiuso_il !== null && (
                      <Row label="Chiuso il" value={formatDate(deal.chiuso_il)} />
                    )}
                    <Row
                      label="Cliente"
                      value={
                        <Link
                          to="/app/clienti/$customerId"
                          params={{ customerId: deal.customer_id }}
                          className="underline underline-offset-2"
                        >
                          {deal.customer_ragione_sociale ?? 'Apri il cliente'}
                        </Link>
                      }
                    />
                  </Section>

                  <Section title="Preventivo">
                    <Row label="Ore preventivate" value={formatHours(deal.ore_preventivate)} />
                    <Row label="Valore preventivato" value={formatMoney(deal.valore_preventivato)} />
                    <Row label="Tariffa oraria" value={formatMoney(deal.tariffa_oraria)} />
                  </Section>

                  {custom.length > 0 && (
                    <Section title="Campi personalizzati">
                      {custom.map((field) => (
                        <Row
                          key={field.key}
                          label={field.label}
                          value={renderFieldValue(field, deal.custom_fields[field.key])}
                        />
                      ))}
                    </Section>
                  )}
                </CardContent>
              </Card>

              <div className="space-y-6 lg:col-span-2">
                {deal.note && (
                  <Card>
                    <CardContent className="pt-6">
                      <h2 className="mb-3 font-semibold">Note</h2>
                      <p className="text-sm whitespace-pre-wrap">{deal.note}</p>
                    </CardContent>
                  </Card>
                )}

                {/* The latest ten, next to the deal rather than behind the Timeline tab:
                    a stage moved, a field changed, an hour logged -- on Pipedrive this
                    column is the page. The tab still has the whole history. */}
                <Card>
                  <CardContent className="pt-6">
                    <h2 className="mb-3 font-semibold">Attività recenti</h2>
                    <Timeline entityType="deal" entityId={dealId} limit={10} />
                  </CardContent>
                </Card>
              </div>
            </div>
          </div>
        }
        links={<DealCustomerCard customerId={deal.customer_id} />}
      />

      <DealForm
        title="Modifica deal"
        open={editing}
        onOpenChange={setEditing}
        customFields={custom}
        initial={dealToFormValues(deal)}
        problem={problem}
        busy={update.isPending}
        onSubmit={(values) => {
          setProblem(null)
          update.mutate(values, {
            onSuccess: () => {
              setEditing(false)
              toast.success('Deal aggiornato')
            },
            onError: (error) => setProblem(toProblem(error)),
          })
        }}
      />
    </>
  )
}

export const Route = createFileRoute('/app/deal/$dealId')({ component: DealDetail })
