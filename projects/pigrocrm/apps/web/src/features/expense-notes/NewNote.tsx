import { Camera, FileUp, Route as RouteIcon, Receipt } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { cn } from '@rebase/ui/cn'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useAfterUpload } from '@/features/inbox/queries'
import { useCostCategories } from '@/features/purchases/queries'
import { uploadReceiptPhoto } from '@/features/purchases/receiptPhoto'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useIsAdmin } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import {
  CHOOSE_KIND,
  KIND_HINTS,
  KIND_LABELS,
  MILEAGE_RATE_LINE,
  amountInput,
  kilometresInput,
  rateInput,
} from './labels'
import {
  useExpenseSummary,
  useStaffChoices,
  useSubmitNote,
  type ExpenseNoteBody,
  type NoteKind,
} from './queries'

const KINDS: NoteKind[] = ['expense', 'mileage']
const KIND_ICONS = { expense: Receipt, mileage: RouteIcon } as const

/** The fields the form draws a control for: a refusal naming one is shown on it, any other in the
 *  bar above the buttons, in the server's own words. */
const OWN_CONTROLS = [
  'date',
  'amount',
  'description',
  'category_id',
  'kilometres',
  'rate_per_km',
  'route_from',
  'route_to',
  'staff_member_id',
]

/** The table from `md` up: a dialog; below it the form is a full-screen sheet (the brief: phone first). */
const WIDE = '(min-width: 768px)'

/**
 * «Nuova nota spese» (the brief, «Note spese»): a first choice, «Cosa vuoi farti rimborsare?», and
 * one screen of fields for the kind chosen with a sticky «Invia». «Una spesa» takes the receipt
 * first (the camera or a file, optional), the date, the amount, the category and what it is;
 * «Chilometri» the date, from, to, kilometres and the rate the person types. «Una trasferta» is a
 * later card and is not offered. There is no draft: a note that was not sent does not exist.
 *
 * The photo is stored in the inbox unread (`read=false`, as the phone's «Spesa» does) before the
 * note is written, and the note links it; if the note is refused the photo is not uploaded a second
 * time. A mileage note carries no amount: the server writes kilometres times the rate, so the
 * browser never multiplies and never sends both.
 */
export function NewNote({
  open,
  onOpenChange,
  onSent,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** After «Invia»: the page the person is on refreshes by itself; the phone's «+» goes to the list. */
  onSent?: () => void
}) {
  const wide = useMediaQuery(WIDE)
  const [kind, setKind] = useState<NoteKind | null>(null)
  const close = (next: boolean) => {
    if (!next) setKind(null)
    onOpenChange(next)
  }
  if (!open) return null

  const title = kind === null ? CHOOSE_KIND : KIND_LABELS[kind]
  const description =
    kind === null ? 'Scegli cosa mandare a un amministratore.' : 'Compili i campi e lo mandi: una nota non inviata non esiste.'
  const content =
    kind === null ? (
      <KindChoice onChoose={setKind} />
    ) : (
      <NoteForm
        kind={kind}
        fullScreen={!wide}
        onBack={() => setKind(null)}
        onSent={() => {
          close(false)
          onSent?.()
        }}
      />
    )

  if (wide) {
    return (
      <Dialog open onOpenChange={close}>
        <DialogContent className="max-h-[90dvh] gap-4 overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {content}
        </DialogContent>
      </Dialog>
    )
  }
  return (
    <Sheet open onOpenChange={close}>
      <SheetContent side="right" closeLabel="Chiudi" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none">
        <SheetHeader className="pr-12">
          <SheetTitle>{title}</SheetTitle>
          <SheetDescription>{description}</SheetDescription>
        </SheetHeader>
        {content}
      </SheetContent>
    </Sheet>
  )
}

function KindChoice({ onChoose }: { onChoose: (kind: NoteKind) => void }) {
  return (
    <ul className="divide-y border-y">
      {KINDS.map((kind) => {
        const Icon = KIND_ICONS[kind]
        return (
          <li key={kind}>
            <button
              type="button"
              className="flex min-h-14 w-full items-center gap-4 px-4 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
              onClick={() => onChoose(kind)}
            >
              <Icon className="size-5 shrink-0" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{KIND_LABELS[kind]}</span>
                <span className="block text-sm text-muted-foreground">{KIND_HINTS[kind]}</span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function Field({
  id,
  label,
  error,
  className,
  children,
}: {
  id: string
  label: string
  error: string | null
  className?: string
  children: ReactNode
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error === null ? null : <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}

function NoteForm({
  kind,
  fullScreen,
  onBack,
  onSent,
}: {
  kind: NoteKind
  fullScreen: boolean
  onBack: () => void
  onSent: () => void
}) {
  const admin = useIsAdmin()
  const summary = useExpenseSummary()
  const categories = useCostCategories()
  const staff = useStaffChoices(admin)
  const submit = useSubmitNote()
  const afterUpload = useAfterUpload()

  const [date, setDate] = useState(() => toIsoDate(new Date()))
  const [amount, setAmount] = useState('')
  const [description, setDescription] = useState('')
  const [chosenCategory, setChosenCategory] = useState<string | null>(null)
  const [who, setWho] = useState<string | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [kilometres, setKilometres] = useState('')
  const [rate, setRate] = useState('')
  const [local, setLocal] = useState<Record<string, string>>({})
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const camera = useRef<HTMLInputElement>(null)
  const files = useRef<HTMLInputElement>(null)
  /** The item the photo became, kept with the file so a retry does not upload it again. */
  const stored = useRef<{ file: File; itemId: string } | null>(null)

  const active = (categories.data ?? []).filter((category) => !category.archived)
  const categoryId = chosenCategory ?? active[0]?.id ?? ''
  // An administrator's own note is the default «Per chi?»; with no staff member of their own they choose.
  const own = summary.data?.staff_member_id ?? ''
  const person = who ?? own

  const fieldError = problem === null ? null : fieldErrorFrom(problem)
  const errorOn = (key: string): string | null =>
    local[key] ?? (fieldError?.field === key ? fieldError.message : null)
  const elsewhere =
    problem !== null && !(fieldError !== null && OWN_CONTROLS.includes(fieldError.field)) ? problem.detail : null

  async function send() {
    const missing: Record<string, string> = {}
    if (date === '') missing.date = 'Scegli la data.'
    if (categoryId === '') missing.category_id = 'Scegli una categoria.'
    if (admin && person === '') missing.staff_member_id = 'Scegli per chi è la nota.'
    const typed = description.trim()
    const route = { from: from.trim(), to: to.trim() }
    const sum = amountInput(amount)
    const distance = kilometresInput(kilometres)
    const perKm = rateInput(rate)
    if (kind === 'expense') {
      if (sum === null) missing.amount = 'Scrivi un importo, per esempio 12,50.'
      if (typed === '') missing.description = 'Scrivi di cosa si tratta.'
    } else {
      if (route.from === '') missing.route_from = 'Scrivi da dove sei partito.'
      if (route.to === '') missing.route_to = 'Scrivi dove sei arrivato.'
      if (distance === null) missing.kilometres = 'Scrivi i chilometri, per esempio 120.'
      if (perKm === null) missing.rate_per_km = 'Scrivi la tariffa al chilometro, per esempio 0,45.'
    }
    setLocal(missing)
    setProblem(null)
    if (Object.keys(missing).length > 0) return

    const base = {
      date,
      category_id: categoryId,
      // Left out for the caller's own staff member; an administrator names another's.
      ...(admin && person !== own ? { staff_member_id: person } : {}),
    }
    setBusy(true)
    try {
      let body: ExpenseNoteBody
      let itemId: string | undefined
      if (kind === 'expense') {
        if (photo !== null) {
          if (stored.current?.file !== photo) {
            try {
              stored.current = { file: photo, itemId: await uploadReceiptPhoto(photo) }
            } catch (error) {
              // Nothing is written: the person keeps the form and the photo and tries again.
              const refused = toProblem(error)
              setProblem({
                ...refused,
                detail: `La foto non è stata caricata e la nota non è stata inviata: ${refused.detail}`,
              })
              return
            }
          }
          itemId = stored.current.itemId
        }
        body = {
          ...base,
          kind: 'expense',
          description: typed,
          amount: sum,
          ...(itemId === undefined ? {} : { inbox_item_id: itemId }),
        }
      } else {
        body = {
          ...base,
          kind: 'mileage',
          description: typed === '' ? `Da ${route.from} a ${route.to}` : typed,
          kilometres: distance,
          rate_per_km: perKm,
          route_from: route.from,
          route_to: route.to,
        }
      }
      try {
        await submit.mutateAsync(body)
      } catch (error) {
        setProblem(toProblem(error))
        return
      }
      if (itemId !== undefined) afterUpload()
      toast.success('Nota spese inviata')
      onSent()
    } finally {
      setBusy(false)
    }
  }

  const sendBar = (
    <>
      <Button type="button" variant="outline" disabled={busy} onClick={onBack}>
        Indietro
      </Button>
      <Button type="submit" className={cn(fullScreen && 'flex-1')} disabled={busy || active.length === 0}>
        {busy ? <Loader className="mr-2 size-4" /> : null}
        Invia
      </Button>
    </>
  )

  if (categories.isPending || (admin && staff.isPending)) {
    return <LoadingState label="Carico i dati della nota…" className="px-4" />
  }
  if (categories.isError || staff.isError) {
    return (
      <div className="px-4 pb-4">
        <QueryErrorBanner
          error={categories.error ?? staff.error}
          onRetry={() => {
            void categories.refetch()
            void staff.refetch()
          }}
        />
      </div>
    )
  }

  const people = staff.data?.items ?? []
  return (
    <form
      noValidate
      className={cn(fullScreen ? 'flex min-h-0 flex-1 flex-col' : 'space-y-4')}
      onSubmit={(event) => {
        event.preventDefault()
        void send()
      }}
    >
      <div className={cn('grid gap-4 sm:grid-cols-2', fullScreen && 'min-h-0 flex-1 content-start overflow-y-auto px-4 pb-4')}>
        {kind === 'expense' ? (
          <div className="space-y-2 sm:col-span-2">
            <Label>Ricevuta</Label>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="outline" onClick={() => camera.current?.click()}>
                <Camera className="mr-2 size-4" aria-hidden />
                Scatta una foto
              </Button>
              <Button type="button" variant="outline" onClick={() => files.current?.click()}>
                <FileUp className="mr-2 size-4" aria-hidden />
                Scegli un file
              </Button>
            </div>
            {photo === null ? null : (
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <span className="break-all font-medium">{photo.name}</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => setPhoto(null)}>
                  Togli la foto
                </Button>
              </p>
            )}
            <input
              ref={camera}
              type="file"
              accept="image/*"
              capture="environment"
              aria-label="Scatta una foto"
              className="sr-only"
              onChange={(event) => {
                setPhoto(event.target.files?.[0] ?? photo)
                event.target.value = ''
              }}
            />
            <input
              ref={files}
              type="file"
              accept="image/*,application/pdf"
              aria-label="Scegli un file"
              className="sr-only"
              onChange={(event) => {
                setPhoto(event.target.files?.[0] ?? photo)
                event.target.value = ''
              }}
            />
          </div>
        ) : null}

        <Field id="expense-note-date" label="Data" error={errorOn('date')}>
          <Input
            id="expense-note-date"
            type="date"
            value={date}
            max={toIsoDate(new Date())}
            onChange={(event) => setDate(event.target.value)}
            aria-invalid={errorOn('date') !== null}
          />
        </Field>

        {kind === 'expense' ? (
          <Field id="expense-note-amount" label="Importo (€)" error={errorOn('amount')}>
            <Input
              id="expense-note-amount"
              inputMode="decimal"
              autoComplete="off"
              placeholder="12,50"
              className="tabular-nums"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              aria-invalid={errorOn('amount') !== null}
            />
          </Field>
        ) : null}

        <Field id="expense-note-category" label="Categoria" error={errorOn('category_id')}>
          <CategorySelect id="expense-note-category" categories={active} value={categoryId} onChange={setChosenCategory} />
        </Field>

        {kind === 'mileage' ? (
          <>
            <Field id="expense-note-from" label="Da" error={errorOn('route_from')}>
              <Input
                id="expense-note-from"
                autoComplete="off"
                value={from}
                onChange={(event) => setFrom(event.target.value)}
                aria-invalid={errorOn('route_from') !== null}
              />
            </Field>
            <Field id="expense-note-to" label="A" error={errorOn('route_to')}>
              <Input
                id="expense-note-to"
                autoComplete="off"
                value={to}
                onChange={(event) => setTo(event.target.value)}
                aria-invalid={errorOn('route_to') !== null}
              />
            </Field>
            <Field id="expense-note-kilometres" label="Chilometri" error={errorOn('kilometres')}>
              <Input
                id="expense-note-kilometres"
                inputMode="decimal"
                autoComplete="off"
                placeholder="120"
                className="tabular-nums"
                value={kilometres}
                onChange={(event) => setKilometres(event.target.value)}
                aria-invalid={errorOn('kilometres') !== null}
              />
            </Field>
            <Field id="expense-note-rate" label="Tariffa (€ al km)" error={errorOn('rate_per_km')}>
              <Input
                id="expense-note-rate"
                inputMode="decimal"
                autoComplete="off"
                placeholder="0,45"
                className="tabular-nums"
                value={rate}
                onChange={(event) => setRate(event.target.value)}
                aria-invalid={errorOn('rate_per_km') !== null}
              />
            </Field>
            <p className="text-sm text-muted-foreground sm:col-span-2">{MILEAGE_RATE_LINE}</p>
          </>
        ) : null}

        {admin ? (
          <Field id="expense-note-person" label="Per chi?" error={errorOn('staff_member_id')}>
            <Select value={person} onValueChange={setWho}>
              <SelectTrigger id="expense-note-person" className="w-full">
                <SelectValue placeholder="Scegli la persona…" />
              </SelectTrigger>
              <SelectContent>
                {people.map((member) => (
                  <SelectItem key={member.id} value={member.id}>
                    {member.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        ) : null}

        <Field
          id="expense-note-description"
          label={kind === 'expense' ? 'Di cosa si tratta' : 'Motivo (facoltativo)'}
          error={errorOn('description')}
          className="sm:col-span-2"
        >
          <Input
            id="expense-note-description"
            autoComplete="off"
            placeholder={kind === 'expense' ? 'Pranzo con il cliente' : 'Visita dal cliente'}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            aria-invalid={errorOn('description') !== null}
          />
        </Field>

        {active.length === 0 ? (
          <p className="text-sm text-muted-foreground sm:col-span-2">Nessuna categoria di spesa attiva.</p>
        ) : null}
        {elsewhere === null ? null : (
          <p
            role="alert"
            className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:col-span-2"
          >
            {elsewhere}
          </p>
        )}
      </div>

      {fullScreen ? (
        <div className="flex gap-2 border-t bg-background p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">{sendBar}</div>
      ) : (
        <DialogFooter>{sendBar}</DialogFooter>
      )}
    </form>
  )
}

function CategorySelect({
  id,
  categories,
  value,
  onChange,
}: {
  id: string
  categories: { id: string; name: string }[]
  value: string
  onChange: (id: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue placeholder="Seleziona…" />
      </SelectTrigger>
      <SelectContent>
        {categories.map((category) => (
          <SelectItem key={category.id} value={category.id}>
            {category.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
