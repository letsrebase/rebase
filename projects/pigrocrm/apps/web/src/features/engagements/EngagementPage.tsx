import { Link, useParams } from '@tanstack/react-router'
import { BriefcaseBusiness, ChevronLeft } from 'lucide-react'
import { useState, type CSSProperties, type ReactNode } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { useIsAdmin } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { Button } from '@rebase/ui/button'
import { Loader } from '@rebase/ui/loader'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { CloseEngagementDialog } from './CloseEngagementDialog'
import { EngagementFormDialog } from './EngagementFormDialog'
import { LetterBlock } from './LetterBlock'
import { MonthPanel } from './MonthPanel'
import { HIDDEN } from './words'
import { MonthsTable } from './MonthsTable'
import { RecordArrivalDialog } from './RecordArrivalDialog'
import {
  formatMoney,
  formatQuantity,
  monthLabel,
  monthOnly,
  stateLabel,
  stateTone,
  type Engagement,
} from './labels'
import { awaitsArrival, awaitsDecision, planMonths } from './monthRows'
import {
  useChangeEngagementState,
  useEngagement,
  useEngagementPayments,
  useEngagementReport,
} from './queries'

/** From this width the open month sits beside the page; below it, it opens over it as a sheet. */
const INLINE_PANEL = '(min-width: 1024px)'

function Figure({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="min-w-0 border bg-card p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
      {hint === undefined ? null : <p className="mt-1 text-sm text-muted-foreground tabular-nums">{hint}</p>}
    </div>
  )
}

/** The name and the role of the title; a hidden client's role reads «Nascosto». */
function titleOf(engagement: Engagement): string {
  const name = engagement.freelancer_name ?? 'Freelance'
  const role = engagement.redacted ? HIDDEN : engagement.role ?? 'senza ruolo'
  return `${name} · ${role}`
}

function subtitleOf(engagement: Engagement): string {
  const client = engagement.redacted ? HIDDEN : engagement.client_name ?? 'Cliente non indicato'
  return engagement.letter_number === null ? client : `${client} · lettera n. ${engagement.letter_number}`
}

/**
 * The page of one engagement («Incarichi», REB-1035, the approved brief): the letter and its
 * signature, the three figures, the table of months with the 330 px panel of the month that is open
 * (an inline `aside` from `lg`, a sheet below), and the actions of the state. It is an administrator's
 * page: anyone else who types the address reads one sentence and gets no figure. The days are a read of
 * their own with their own states, so a slow or failed read errors the table and nothing else.
 */
export function EngagementPage() {
  const { engagementId } = useParams({ from: '/app/engagements/$engagementId' })
  const admin = useIsAdmin()
  const engagement = useEngagement(engagementId)
  const started =
    engagement.data !== undefined &&
    engagement.data.state !== 'draft' &&
    engagement.data.state !== 'awaiting_signature'
  const report = useEngagementReport(engagementId, started)
  const payments = useEngagementPayments(engagementId, started)
  const change = useChangeEngagementState(engagementId)
  const inline = useMediaQuery(INLINE_PANEL)
  const [selected, setSelected] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [closing, setClosing] = useState<'conclude' | 'cancel' | null>(null)
  const [arrival, setArrival] = useState<string | null>(null)

  if (!admin) {
    return (
      <>
        <PageHeader icon={BriefcaseBusiness} title="Incarichi" />
        <p className="px-8 pb-8 text-sm text-muted-foreground">
          Gli incarichi dei freelance li vedono solo gli amministratori.
        </p>
      </>
    )
  }
  if (engagement.isPending) return <LoadingState label="Leggo l'incarico…" className="p-8" />
  if (engagement.isError) {
    return (
      <div className="p-8">
        <QueryErrorBanner error={engagement.error} onRetry={() => void engagement.refetch()} />
      </div>
    )
  }

  const data = engagement.data
  // The CRM's own day, from the server: the browser's clock would disagree near midnight.
  const today = data.today ?? toIsoDate(new Date())
  const plan = planMonths(report.data, payments.data?.items ?? [], today)
  const open = selected === null ? undefined : plan.rows.find((row) => row.key === selected)
  const live = data.state === 'draft' || data.state === 'awaiting_signature' || data.state === 'active'
  const cancelling = data.starts_on > today
  const figures = report.data?.available ? report.data : null

  // «Da approvare»: the months received and not decided, a count. Until the report is read, the
  // server's own list of what the engagement waits for says the same.
  const undecided =
    report.data === undefined
      ? (data.waiting ?? []).filter((waiting) => waiting.code === 'report_undecided').length
      : plan.rows.filter((row) => awaitsDecision(row)).length

  const toDecide = plan.rows.find((row) => awaitsDecision(row))
  const toReceive = plan.rows.find((row) => awaitsArrival(row))
  const hasLetter = data.letter_document_id !== null && data.letter_document_id !== undefined

  // The strong action of the state on the phone's sticky bar.
  let barAction: ReactNode = null
  if (data.state === 'draft') {
    barAction = hasLetter ? (
      <Button
        className="w-full"
        disabled={change.isPending}
        onClick={() =>
          change.mutate(
            { state: 'awaiting_signature' },
            { onSuccess: () => toast.success('Lettera inviata per la firma.') },
          )
        }
      >
        {change.isPending ? <Loader className="mr-2 size-4" /> : null}
        Invia per la firma
      </Button>
    ) : (
      <Button
        className="w-full"
        onClick={() => document.getElementById('lettera')?.scrollIntoView?.({ behavior: 'smooth' })}
      >
        Crea la lettera
      </Button>
    )
  } else if (data.state === 'active' || data.state === 'concluded') {
    if (toDecide !== undefined) {
      barAction = (
        <Button className="w-full" onClick={() => setSelected(toDecide.key)}>
          Decidi il rendiconto di {monthOnly(toDecide.month)}
        </Button>
      )
    } else if (toReceive !== undefined) {
      barAction = (
        <Button className="w-full" onClick={() => setArrival(toReceive.key)}>
          Registra l'arrivo
        </Button>
      )
    }
  }

  const panel =
    open === undefined ? null : (
      <MonthPanel
        engagement={data}
        row={open}
        days={report.data?.days ?? []}
        daysAvailable={figures !== null}
        today={today}
      />
    )

  return (
    <>
      <PageHeader
        icon={BriefcaseBusiness}
        title={titleOf(data)}
        description={subtitleOf(data)}
        titleAddon={<StatusPill tone={stateTone(data.state)}>{stateLabel(data.state)}</StatusPill>}
        actions={
          live ? (
            <>
              <Button variant="outline" onClick={() => setEditing(true)}>
                Modifica
              </Button>
              <Button variant="outline" onClick={() => setClosing(cancelling ? 'cancel' : 'conclude')}>
                {cancelling ? "Annulla l'incarico…" : "Chiudi l'incarico…"}
              </Button>
            </>
          ) : null
        }
      />

      <div className="flex items-start gap-6 px-8 pb-6">
        <div className="min-w-0 flex-1 space-y-6">
          <Link
            to="/app/engagements"
            search={{ tab: 'todo' }}
            className="inline-flex items-center gap-1 text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            <ChevronLeft aria-hidden="true" className="size-4" />
            Tutti gli incarichi
          </Link>

          {started ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <div className="max-md:hidden">
                <Figure
                  label={report.data?.unit === 'hour' ? 'Ore lavorate' : 'Giorni lavorati'}
                  value={report.isPending ? <Loader className="size-5" /> : figures === null ? '-' : formatQuantity(figures.total_quantity)}
                />
              </div>
              <Figure
                label="Pagabili"
                value={report.isPending ? <Loader className="size-5" /> : figures === null ? '-' : formatQuantity(figures.payable_quantity)}
                hint={
                  data.redacted
                    ? HIDDEN
                    : figures === null || figures.payable_amount === null
                      ? undefined
                      : formatMoney(figures.payable_amount)
                }
              />
              <Figure label="Da approvare" value={undecided} />
            </div>
          ) : null}

          <LetterBlock engagement={data} onComplete={() => setEditing(true)} />

          <MonthsTable
            engagement={data}
            report={report}
            payments={payments}
            plan={plan}
            selected={selected}
            onSelect={setSelected}
            today={today}
          />
        </div>

        {panel !== null && inline ? (
          <aside
            aria-label="Mese aperto"
            className="sticky top-4 w-82.5 shrink-0 self-start overflow-y-auto border bg-card max-h-[calc(100dvh-2rem)]"
          >
            <div className="flex justify-end border-b px-2 py-1">
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
                Chiudi
              </Button>
            </div>
            {panel}
          </aside>
        ) : null}
      </div>

      {barAction === null ? null : (
        <div
          style={{ '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties}
          className="sticky bottom-(--phone-bar) z-10 border-t bg-background px-4 py-3 sm:hidden"
        >
          {barAction}
        </div>
      )}

      {panel !== null && !inline ? (
        <Sheet open onOpenChange={(next) => !next && setSelected(null)}>
          <SheetContent
            side="right"
            closeLabel="Chiudi"
            className="gap-0 overflow-y-auto data-[side=right]:w-full data-[side=right]:sm:max-w-none"
          >
            <SheetTitle className="sr-only">Mese di {open === undefined ? '' : monthLabel(open.month)}</SheetTitle>
            <SheetDescription className="sr-only">
              Il rendiconto, i giorni e i pagamenti del mese.
            </SheetDescription>
            <div className="pt-8">{panel}</div>
          </SheetContent>
        </Sheet>
      ) : null}

      <EngagementFormDialog open={editing} onOpenChange={setEditing} engagement={data} />
      {closing === null ? null : (
        <CloseEngagementDialog engagementId={data.id} mode={closing} today={today} onClose={() => setClosing(null)} />
      )}
      {arrival === null ? null : (
        <RecordArrivalDialog engagementId={data.id} month={arrival} onClose={() => setArrival(null)} />
      )}
    </>
  )
}
