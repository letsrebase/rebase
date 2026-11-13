import { useEffect, useRef, useState } from 'react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { AMOUNT_PROBLEM, amountFilter } from '@/lib/amount'
import { STATE_LABELS, TEAM_BUILDER_ORIGIN_LABEL, VETTED_LABEL } from '@/lib/format'

/** The parts every admin list and detail page is built from. */

const TONE: Record<string, string> = {
  new: 'bg-[var(--color-royal-gold)]',
  lead: 'bg-muted',
  contacted: 'bg-muted-foreground',
  active: 'bg-foreground',
  in_progress: 'bg-foreground',
  discarded: 'bg-[var(--color-watermelon)]',
  closed: 'bg-muted-foreground',
}

export function StatePill({ status }: { status: string }) {
  return (
    <Badge variant="pill" className="gap-1.5">
      <span aria-hidden="true" className={cn('size-1.5 rounded-full', TONE[status] ?? 'bg-muted')} />
      {STATE_LABELS[status] ?? status}
    </Badge>
  )
}

/** Beside the state pill on a talent an admin marked verified (REB-518): the flag the
 *  talent cloud shows as its badge. */
export function VettedPill() {
  return (
    <Badge variant="pill" dot="accent">
      {VETTED_LABEL}
    </Badge>
  )
}

/** On a company request that came from the team builder's beta box (REB-518), whose
 *  referente asked for the talent cloud: the admin knows who to call about it. */
export function TeamBuilderPill() {
  return <Badge variant="pill">{TEAM_BUILDER_ORIGIN_LABEL}</Badge>
}

/** Beside the state pill on a card that is missing the CV, the rate, the position or
 *  the remote preference. Either an admin wrote it from a signup and the person has not
 *  finished yet (ORB-155), or they filled the wizard and skipped the CV, which the
 *  wizard lets them do. */
export function IncompletePill() {
  return <Badge variant="pill">Da completare</Badge>
}

export function Header({ title, count, children }: { title: string; count?: number; children?: React.ReactNode }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-5">
      <h1 className="text-2xl font-semibold tracking-tight">
        {title}
        {count !== undefined && (
          <span className="ml-2 text-base font-normal text-muted-foreground">{count}</span>
        )}
      </h1>
      {children}
    </header>
  )
}

export function StateFilter({
  states,
  value,
  onChange,
  labels = STATE_LABELS,
}: {
  states: readonly string[]
  value: string | undefined
  onChange: (value: string | undefined) => void
  /** The talent and company lists share `STATE_LABELS`; a list over a different vocabulary
   *  («Richieste team», the referral ledger) passes its own. */
  labels?: Record<string, string>
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filtra per stato">
      {[undefined, ...states].map((state) => (
        <Button
          key={state ?? 'all'}
          type="button"
          size="sm"
          variant={value === state ? 'default' : 'outline'}
          onClick={() => onChange(state)}
        >
          {state ? (labels[state] ?? state) : 'Tutti'}
        </Button>
      ))}
    </div>
  )
}

export function FilterField({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

/** A euro filter (REB-485): typed the Italian way, so «1.500» asks for 1500 whatever the
 *  browser's locale, which a number input did not (it read 1.5). The URL keeps what was
 *  typed; the query reads it through `amountFilter`, and a value that is not an amount
 *  narrows nothing and says so under the field. A campaign's filters use it too
 *  (REB-526). */
export function AmountFilter({
  id,
  label,
  value,
  onChange,
}: {
  id: string
  label: string
  value: string | undefined
  onChange: (value: string | undefined) => void
}) {
  const problem = value !== undefined && amountFilter(value) === undefined
  const errorId = `${id}-error`
  return (
    <FilterField label={label} htmlFor={id}>
      <Input
        id={id}
        inputMode="decimal"
        value={value ?? ''}
        onChange={(event) => onChange(event.target.value || undefined)}
        aria-invalid={problem || undefined}
        aria-describedby={problem ? errorId : undefined}
      />
      {problem && (
        <p role="alert" id={errorId} className="text-sm text-destructive">
          {AMOUNT_PROBLEM}
        </p>
      )}
    </FilterField>
  )
}

/** The affordance under a truncated page (REB-286), reimplemented here from the CRM's
 *  `LoadMoreInvoices` (PR #179) since this app may not import PigroCRM: a button that
 *  walks `next_cursor` one page further, a count of what is already on screen, and an
 *  `IntersectionObserver` sentinel so scrolling to the end does what the button does.
 *  Guarded against a missing `IntersectionObserver` (jsdom in tests) rather than
 *  shipping a polyfill for an admin-only page. */
export function LoadMore({
  label,
  isFetchingMore,
  onLoadMore,
}: {
  label: string
  isFetchingMore: boolean
  onLoadMore: () => void
}) {
  const sentinel = useRef<HTMLDivElement>(null)
  const onLoadMoreRef = useRef(onLoadMore)
  useEffect(() => {
    onLoadMoreRef.current = onLoadMore
  })
  useEffect(() => {
    const node = sentinel.current
    if (!node || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver((entries) => {
      if (entries[0]?.isIntersecting) onLoadMoreRef.current()
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])
  return (
    <div ref={sentinel} className="flex flex-wrap items-center gap-3 px-6 py-4">
      <Button type="button" variant="outline" size="sm" onClick={onLoadMore} disabled={isFetchingMore}>
        {isFetchingMore ? (
          <>
            <Loader className="mr-2 size-4" />
            Caricamento…
          </>
        ) : (
          'Mostra altri'
        )}
      </Button>
      <span role="status" className="text-sm text-muted-foreground">
        {label}
      </span>
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-6 py-10 text-center text-sm text-muted-foreground">{children}</p>
}

/** The wait of a list, a record or a panel: the brand `Loader` with a `role="status"`
 *  sentence beside it (`docs/design/DECISIONS.md`, REB-973 and REB-982), never a bare
 *  «Caricamento…» line. `Loader` is decorative, so the sentence is what a screen reader
 *  hears. Centred like `Empty`, which it stands in for; pass a `className` to sit left in
 *  a smaller place. */
export function LoadingState({ label = 'Caricamento…', className }: { label?: string; className?: string }) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn('flex items-center justify-center gap-2 px-6 py-10 text-sm text-muted-foreground', className)}
    >
      <Loader className="size-4" />
      {label}
    </div>
  )
}

/** One number on a stats page («La guida», «Accessi»), inside a `<dl>`. */
export function Figure({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-3xl font-semibold tracking-tight">
        {value}
        {note && <span className="ml-2 text-base font-normal text-muted-foreground">{note}</span>}
      </dd>
    </div>
  )
}

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b pb-3 last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

/** The state of a card or a request. A remark about it is a note (`Notes`, REB-856), never
 *  a field here; the change lands in the record's trail with the admin who made it. A
 *  refused save is said under the button. */
export function StatusEditor({
  states,
  status,
  onSave,
  saving,
  error,
}: {
  states: readonly string[]
  status: string
  onSave: (status: string) => void
  saving: boolean
  error: string | null
}) {
  const [draftState, setDraftState] = useState(status)
  return (
    <div className="space-y-3 border p-4">
      <p className="text-sm font-medium">Stato</p>
      <StateFilter states={states} value={draftState} onChange={(value) => value && setDraftState(value)} />
      <Button size="sm" onClick={() => onSave(draftState)} disabled={saving || draftState === status}>
        {saving ? 'Salvo…' : 'Salva'}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
