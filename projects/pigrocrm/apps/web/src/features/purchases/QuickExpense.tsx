import { useRef, useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { useAfterUpload } from '@/features/inbox/queries'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { useCostCategories, useCreatePurchase } from './queries'
import { normaliseAmount, uploadReceiptPhoto } from './receiptPhoto'

/** The fields the sheet draws a control for: a refusal naming one is shown on it, any other in
 *  the bar above the button, in the server's own words. */
const OWN_CONTROLS = ['amount', 'merchant_name', 'description', 'category_id', 'date']

/**
 * «Spesa» of the phone's «+» (the approved brief of 2026-11-10, «Spesa» on the phone): a sheet
 * with amount, esercente, categoria, data and an optional photo, that registers a `receipt` at
 * once. It has no camera-first step: the photo is an input the person may use or skip.
 *
 * The photo is a justification and nothing else. It goes to the inbox unread (`read=false`)
 * before the purchase is written, and the purchase links it; the server files the item as that
 * purchase. If the photo cannot be stored the purchase is not written, so a photo is never
 * lost in silence. A purchase refused after the photo went up keeps the sheet open, and
 * pressing «Registra» again links the same item instead of uploading the file twice.
 *
 * There is no «Già pagata» control: a purchase has no due item yet, so there is nothing to
 * choose. No fiscal rule is decided here; a refusal is shown as the server words it.
 */
export function QuickExpense({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="bottom"
        className="max-h-[90dvh] overflow-y-auto pb-[env(safe-area-inset-bottom)]"
        closeLabel="Chiudi"
      >
        <SheetHeader>
          <SheetTitle>Spesa</SheetTitle>
          <SheetDescription>Uno scontrino o una spesa, in due tocchi.</SheetDescription>
        </SheetHeader>
        {/* Mounted with the sheet, so every opening starts from an empty form and today. */}
        <ExpenseForm onDone={() => onOpenChange(false)} />
      </SheetContent>
    </Sheet>
  )
}

function ExpenseForm({ onDone }: { onDone: () => void }) {
  const categories = useCostCategories()
  const create = useCreatePurchase()
  const afterUpload = useAfterUpload()
  const [amount, setAmount] = useState('')
  const [merchant, setMerchant] = useState('')
  const [chosenCategory, setChosenCategory] = useState<string | null>(null)
  const [date, setDate] = useState(() => toIsoDate(new Date()))
  const [photo, setPhoto] = useState<File | null>(null)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [local, setLocal] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  /** The photo is already in the inbox while the purchase was refused: said, so the item is explained. */
  const [orphan, setOrphan] = useState(false)
  const photoInput = useRef<HTMLInputElement>(null)
  /** The item the photo became, kept with the file so a retry does not upload it again. */
  const stored = useRef<{ file: File; itemId: string } | null>(null)

  const active = (categories.data ?? []).filter((category) => !category.archived)
  const categoryId = chosenCategory ?? active[0]?.id ?? ''

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const errorOn = (key: string) => {
    const message = local[key] ?? (fieldError?.field === key ? fieldError.message : undefined)
    return message === undefined ? null : <p className="text-sm text-destructive">{message}</p>
  }
  // A description the sheet fills from the merchant is shown under the merchant.
  const merchantError = errorOn('merchant_name') ?? errorOn('description')
  const elsewhere =
    problem !== null && !(fieldError !== null && OWN_CONTROLS.includes(fieldError.field))
      ? problem.detail
      : null

  async function submit() {
    const missing: Record<string, string> = {}
    const normalised = normaliseAmount(amount)
    if (normalised === null) missing.amount = 'Scrivi un importo, per esempio 12,50.'
    const where = merchant.trim()
    if (where === '') missing.merchant_name = 'Scrivi dove hai speso.'
    if (categoryId === '') missing.category_id = 'Scegli una categoria.'
    if (date === '') missing.date = 'Scegli la data.'
    setLocal(missing)
    setProblem(null)
    setOrphan(false)
    if (Object.keys(missing).length > 0 || normalised === null) return

    setBusy(true)
    try {
      let itemId: string | undefined
      if (photo !== null) {
        if (stored.current?.file !== photo) {
          try {
            stored.current = { file: photo, itemId: await uploadReceiptPhoto(photo) }
          } catch (error) {
            // Nothing is written: the person keeps the sheet and the photo and tries again.
            const refused = toProblem(error)
            setProblem({
              ...refused,
              detail: `La foto non è stata caricata e la spesa non è stata registrata: ${refused.detail}`,
            })
            return
          }
        }
        itemId = stored.current.itemId
      }
      try {
        await create.mutateAsync({
          kind: 'receipt',
          category_id: categoryId,
          date,
          amount: normalised,
          merchant_name: where,
          description: where,
          deal_id: null,
          ...(itemId === undefined ? {} : { inbox_item_id: itemId }),
        })
      } catch (error) {
        setProblem(toProblem(error))
        setOrphan(itemId !== undefined)
        return
      }
      if (itemId !== undefined) afterUpload()
      toast.success('Spesa registrata')
      onDone()
    } finally {
      setBusy(false)
    }
  }

  if (categories.isPending) return <LoadingState label="Carico le categorie…" className="px-4" />
  if (categories.isError)
    return (
      <div className="px-4 pb-4">
        <QueryErrorBanner error={categories.error} onRetry={() => void categories.refetch()} />
      </div>
    )

  return (
    <form
      className="space-y-4 px-4 pb-4"
      noValidate
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="quick-expense-amount">Importo (€)</Label>
        <Input
          id="quick-expense-amount"
          inputMode="decimal"
          autoComplete="off"
          placeholder="12,50"
          className="tabular-nums"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          aria-invalid={errorOn('amount') !== null}
        />
        {errorOn('amount')}
      </div>

      <div className="space-y-2">
        <Label htmlFor="quick-expense-merchant">Esercente</Label>
        <Input
          id="quick-expense-merchant"
          autoComplete="off"
          value={merchant}
          onChange={(event) => setMerchant(event.target.value)}
          aria-invalid={merchantError !== null}
        />
        {merchantError}
      </div>

      <div className="space-y-2">
        <Label htmlFor="quick-expense-category">Categoria</Label>
        {active.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nessuna categoria di spesa attiva.</p>
        ) : (
          <Select value={categoryId} onValueChange={setChosenCategory}>
            <SelectTrigger id="quick-expense-category" className="w-full">
              <SelectValue placeholder="Seleziona…" />
            </SelectTrigger>
            <SelectContent>
              {active.map((category) => (
                <SelectItem key={category.id} value={category.id}>
                  {category.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {errorOn('category_id')}
      </div>

      <div className="space-y-2">
        <Label htmlFor="quick-expense-date">Data</Label>
        <Input
          id="quick-expense-date"
          type="date"
          value={date}
          onChange={(event) => setDate(event.target.value)}
          aria-invalid={errorOn('date') !== null}
        />
        {errorOn('date')}
      </div>

      <div className="space-y-2">
        <Label htmlFor="quick-expense-photo">Foto (facoltativa)</Label>
        <Input
          ref={photoInput}
          id="quick-expense-photo"
          type="file"
          accept="image/*"
          onChange={(event) => setPhoto(event.target.files?.[0] ?? null)}
        />
        {orphan && elsewhere === null && (
          <p className="text-sm text-muted-foreground">La foto è già in In arrivo.</p>
        )}
        {photo !== null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              setPhoto(null)
              if (photoInput.current) photoInput.current.value = ''
            }}
          >
            Togli la foto
          </Button>
        )}
      </div>

      {elsewhere !== null && (
        <p
          role="alert"
          className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {orphan ? `${elsewhere} La foto è già in In arrivo.` : elsewhere}
        </p>
      )}

      <Button type="submit" className="w-full" disabled={busy || active.length === 0}>
        {busy ? 'Registrazione…' : 'Registra'}
      </Button>
    </form>
  )
}
