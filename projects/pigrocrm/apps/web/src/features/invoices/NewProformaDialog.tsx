import { useNavigate } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { useCustomer, useCustomers } from '@/features/customers/queries'
import { useDeal, useDeals } from '@/features/deals/queries'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { AccrualPeriodFields } from './AccrualPeriodFields'
import {
  emptyAccrualPeriod,
  validateAccrualPeriod,
  type AccrualPeriodDraft,
} from './accrualPeriod'
import { formatMoney, previewImponibile } from './format'
import { useCreateInvoice } from './queries'

/**
 * One line as this form holds it: three strings, because that is what an input gives
 * back, and the conversion happens once on submit.
 *
 * Narrower than `lineDraft.ts`'s `DraftLine` on purpose. That type serves the editor on
 * the detail page, which also edits units and discounts; a new document needs the three
 * fields that decide what it is worth, and the rest is a correction to make on the page
 * the owner lands on -- where `PUT /{id}/lines` can express them and this create call,
 * being a create, has nothing to clear.
 */
interface DraftRow {
  descrizione: string
  quantita: string
  prezzo_unitario: string
}

/** `InvoiceLineIn.quantita` defaults to 1 server-side; the field is pre-filled with it
 *  so the commonest line (one of something) needs no typing at all. */
function newRow(): DraftRow {
  return { descrizione: '', quantita: '1', prezzo_unitario: '' }
}

/**
 * What the dialog starts from when the page that opened it already knows what is being
 * invoiced: a deal's name and its expected value, in the words the deal itself uses.
 *
 * Two strings and not a `DraftRow`, because the caller is not filling in a form -- the
 * quantity is this dialog's business (one of something, as `newRow` explains) and the
 * caller has no opinion about it.
 */
export interface ProformaPrefill {
  descrizione: string
  importo: string
}

/**
 * How much the page behind the dialog has already answered: nothing, the customer, or
 * the customer and the deal.
 *
 * A union and not two independent optionals, because a deal fixed without its customer
 * is not a state this dialog could honour. It would render the customer picker beside a
 * fixed deal's name, and choosing a customer there has to drop that deal (it may belong
 * to someone else -- `_check_owner` answers 409) while the name it was chosen from stays
 * on screen. Nothing ships that pairing today, which is exactly why it would have
 * survived until something did.
 */
export type FixedParties =
  | { customerId?: undefined; dealId?: undefined }
  | { customerId: string; dealId?: string }

/** The first line, from a prefill if there is one. Every field stays editable: a deal's
 *  expected value is the whole of the work and an invoice is usually a part of it, so
 *  this is a starting point and never an answer. */
function firstRow(prefill?: ProformaPrefill): DraftRow {
  if (prefill === undefined) return newRow()
  return { descrizione: prefill.descrizione, quantita: '1', prezzo_unitario: prefill.importo }
}

function isComplete(row: DraftRow): boolean {
  return row.descrizione.trim() !== '' && row.prezzo_unitario.trim() !== ''
}

function isStarted(row: DraftRow): boolean {
  return row.descrizione.trim() !== '' || row.prezzo_unitario.trim() !== ''
}

interface Errors {
  customer?: string
  causale?: string
  data?: string
  competenza?: string
  righe?: string
}

/**
 * Split out so `useCustomers` is mounted -- and therefore requested -- only while the
 * dialog is actually open, the same reasoning `features/deals/DealForm.tsx`'s
 * `CustomerPicker` documents at length. `limit: 200` mirrors it too: a one-shot cap for
 * a dropdown, not a paginated list of its own.
 */
function CustomerPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const customers = useCustomers({ limit: 200 })

  return (
    <div className="space-y-2">
      <Label htmlFor="proforma-cliente">
        Cliente
        <span className="ml-1 text-destructive">*</span>
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id="proforma-cliente" className="w-full">
          <SelectValue placeholder="Seleziona un cliente…" />
        </SelectTrigger>
        <SelectContent>
          {customers.data?.items.map((customer) => (
            <SelectItem key={customer.id} value={customer.id}>
              {customer.ragione_sociale}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

/**
 * What a fixed customer or deal looks like: its name, as text.
 *
 * Not a disabled `Select`. A control nobody can operate still reads as a decision left
 * to make, and the point of opening this dialog from a record is that the decision is
 * already taken -- so the field states the answer instead of offering it. The id travels
 * in the body either way.
 *
 * A `<dl>` and not two `<p>`s: with no control to point an `htmlFor` at, the pairing of
 * the name and what it answers exists only for a sighted reader unless the markup says
 * it. Muted label, `font-medium` value -- the weighting `Row` already uses on both
 * detail pages, where the answer is the thing worth reading.
 */
function FixedField({ label, value }: { label: string; value: string | undefined }) {
  return (
    <dl className="space-y-2">
      <dt className="text-muted-foreground text-sm">{label}</dt>
      {/* `…` while the read resolves, the same placeholder `DealCustomerCard` shows for
          the same fact. In practice neither caller ever renders it: both reads below are
          cache hits (see their own notes). */}
      <dd className="text-sm font-medium">{value ?? '…'}</dd>
    </dl>
  )
}

/**
 * The customer, when only the customer is fixed -- which is the Fatture tab of that
 * customer's own page. `useCustomer` is mounted there at route level, so this is a cache
 * hit and the name is on screen the instant the dialog opens.
 */
function FixedCustomer({ customerId }: { customerId: string }) {
  const customer = useCustomer(customerId)
  return <FixedField label="Cliente" value={customer.data?.ragione_sociale} />
}

/**
 * Both parties at once, when the deal is fixed too -- which is a deal's own header.
 *
 * One read names both, and it is the read the page has already made: `DealRead` carries
 * `customer_ragione_sociale` (denormalised there precisely so a deal can be shown
 * without a second request -- see that field's comment in `deals/schemas.py`), and
 * `useDeal(dealId)` is mounted at the deal route's own top level.
 *
 * This is why the customer is *not* read through `useCustomer` here: on the deal page
 * that would be a cold `GET /api/customers/{id}` on every open. `DealCustomerCard` does
 * hold one, but it lives in the Collegamenti tab, which Radix leaves unmounted until it
 * is selected -- so its cache entry is not there to be hit.
 */
function FixedCustomerAndDeal({ dealId }: { dealId: string }) {
  const deal = useDeal(dealId)
  return (
    <>
      <FixedField label="Cliente" value={deal.data?.customer_ragione_sociale ?? undefined} />
      <FixedField label="Deal" value={deal.data?.nome} />
    </>
  )
}

/** `NO_DEAL` is a UI-only value: "no deal" is the *absence* of `deal_id` in the body,
 *  and a `Select` needs a non-empty string to represent an option. */
const NO_DEAL = 'nessuno'

/**
 * Rendered only once a customer is chosen, which is what makes the list correct rather
 * than merely short: `InvoiceService.create` calls `_check_owner`, so a deal belonging
 * to someone else is a 409, and `DealListQuery.customer_id` lets the repository -- the
 * only thing that can see a deal this page never fetched -- do the filtering.
 */
function DealPicker({
  customerId,
  value,
  onChange,
}: {
  customerId: string
  value: string
  onChange: (value: string) => void
}) {
  const deals = useDeals({ customer_id: customerId })

  return (
    <div className="space-y-2">
      <Label htmlFor="proforma-deal">Deal (facoltativo)</Label>
      <Select value={value === '' ? NO_DEAL : value} onValueChange={onChange}>
        <SelectTrigger id="proforma-deal" className="w-full">
          <SelectValue placeholder="Nessun deal" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_DEAL}>Nessun deal</SelectItem>
          {deals.data?.items.map((deal) => (
            <SelectItem key={deal.id} value={deal.id}>
              {deal.nome}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

/**
 * Creating a proforma.
 *
 * From the Fatture page, where nothing is known and both pickers are asked; from a
 * customer's Fatture tab, where the customer is; and from a deal's header, where the
 * customer, the deal and the first line all are. One dialog for the three, because the
 * document they create is the same one -- what changes is only how many of its questions
 * the page behind it has already answered.
 *
 * A proforma and not a `fattura` bozza, even though `POST /api/invoices` accepts both:
 * what the owner does next is confirm it and issue it (`POST /{id}/confirm`, then
 * `POST /{id}/issue`), which is exactly the proforma's own path -- and neither shape
 * has a number until emission, so nothing here can burn one.
 *
 * `data_scadenza` is deliberately not a field. It is not on `InvoiceCreate` at all
 * (packages/core/src/pigrocrm/core/invoices/schemas.py): the due date is derived at
 * emission from the fiscal profile's `giorni_scadenza` (`InvoiceService.issue`). A
 * control for it here would have been silently dropped -- `InvoiceCreate` does not
 * forbid extra keys -- which is worse than not offering it, so the dialog says where
 * the date comes from instead.
 *
 * `data_emissione` *is* a field, since ORB-63: a proforma is a document the customer
 * receives and answers to, so it carries a date of its own, the sender's and stable,
 * rather than printing the day it was rendered. Today by default, read from local date
 * parts (`toIsoDate`), and editable on the detail page until the proforma is consumed.
 * It is not a register date: the fattura issued from it takes its own at emission.
 *
 * The accrual period (ORB-61) is optional, both ends or neither. It is what the P&L
 * «per competenza» reads and what the XML's `DataInizioPeriodo`/`DataFinePeriodo` say,
 * which is how August work invoiced in September stops landing in September's month.
 */
function NewProformaDialog({
  onClose,
  customerId: fixedCustomerId,
  dealId: fixedDealId,
  prefill,
}: { onClose: () => void; prefill?: ProformaPrefill } & FixedParties) {
  const navigate = useNavigate()
  const create = useCreateInvoice()
  // A fixed id is the initial state and not a separate one: everything downstream --
  // validation, the body, the deal list's `customer_id` -- reads these two and does not
  // need to know whether a human chose them or the page did.
  const [customerId, setCustomerId] = useState(fixedCustomerId ?? '')
  const [dealId, setDealId] = useState(fixedDealId ?? '')
  const [causale, setCausale] = useState('')
  const [dataEmissione, setDataEmissione] = useState(() => toIsoDate(new Date()))
  const [competenza, setCompetenza] = useState<AccrualPeriodDraft>(emptyAccrualPeriod)
  const [note, setNote] = useState('')
  const [rows, setRows] = useState<DraftRow[]>(() => [firstRow(prefill)])
  const [errors, setErrors] = useState<Errors>({})
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  function update(index: number, field: keyof DraftRow, value: string) {
    setRows((previous) =>
      previous.map((row, position) => (position === index ? { ...row, [field]: value } : row)),
    )
  }

  function chooseCustomer(value: string) {
    setCustomerId(value)
    // A deal chosen for the previous customer would be a 409 from `_check_owner`, and
    // the picker below is about to list a different set entirely. Back to the *fixed*
    // deal rather than to none: `FixedParties` makes a fixed deal without its customer
    // unrepresentable, so today this is always `''` -- and it stays correct rather than
    // silently dropping a deal the caller pinned if that ever changes.
    setDealId(fixedDealId ?? '')
  }

  /** What this form checks before asking, and nothing more. Everything else -- the
   *  regime's rules, the widths, whether the deal really is this customer's -- is the
   *  server's answer, rendered as it comes (see `problem` below). */
  function validate(): Errors {
    const found: Errors = {}
    if (customerId === '') found.customer = 'Scegli il cliente da fatturare.'
    if (causale.trim() === '') found.causale = 'La causale è obbligatoria.'
    if (dataEmissione === '') found.data = 'La proforma ha bisogno di una data.'
    const periodError = validateAccrualPeriod(competenza)
    if (periodError !== undefined) found.competenza = periodError
    if (!rows.some(isComplete)) {
      found.righe = 'Serve almeno una riga con descrizione e prezzo unitario.'
    } else if (rows.some((row) => isStarted(row) && !isComplete(row))) {
      found.righe = 'Ogni riga iniziata richiede descrizione e prezzo unitario.'
    }
    return found
  }

  function submit() {
    const found = validate()
    setErrors(found)
    setProblem(null)
    if (Object.keys(found).length > 0) return

    const body: Record<string, unknown> = {
      customer_id: customerId,
      tipo: 'proforma',
      causale: causale.trim(),
      data_emissione: dataEmissione,
      // Only the complete rows, and each one with only the keys the user filled in.
      // An omitted optional key on a *create* is the server's own default, which is
      // what "the user did not say" means here -- unlike `InvoiceLinesEditor`, where a
      // full replacement makes an omitted key indistinguishable from an unchanged one
      // and `null` has to be explicit.
      righe: rows.filter(isComplete).map((row) => ({
        descrizione: row.descrizione.trim(),
        quantita: row.quantita.trim() === '' ? '1' : row.quantita.trim(),
        prezzo_unitario: row.prezzo_unitario.trim(),
      })),
    }
    if (dealId !== '') body.deal_id = dealId
    // After `validate`, a non-empty start means a non-empty end too; an empty pair is
    // the server's own default, per the rule above.
    if (competenza.competenza_da !== '') {
      body.competenza_da = competenza.competenza_da
      body.competenza_a = competenza.competenza_a
    }
    if (note.trim() !== '') body.note_interne = note.trim()

    create.mutate(body, {
      onSuccess: (invoice) => {
        toast.success('Proforma creata')
        onClose()
        // Straight to the document: confirming and issuing live there
        // (`InvoiceActions`), and so does correcting anything this short form does not
        // ask for.
        void navigate({ to: '/app/invoices/$invoiceId', params: { invoiceId: invoice.id } })
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Nuova proforma</DialogTitle>
          <DialogDescription>
            Una proforma non è un documento fiscale: la confermi e la emetti dalla sua
            pagina, ed è l’emissione che assegna il numero e calcola la scadenza dai
            giorni di pagamento del profilo fiscale.
          </DialogDescription>
        </DialogHeader>

        {problem ? <QueryErrorBanner error={problem} /> : null}

        <div className="space-y-4">
          {/* Three shapes, one per level of «already answered»: both pickers, the
              customer stated and its deals still offered, or both parties stated from
              the one read the deal page has already made. */}
          {fixedCustomerId === undefined ? (
            <>
              <CustomerPicker value={customerId} onChange={chooseCustomer} />
              {errors.customer ? (
                <p className="text-sm text-destructive">{errors.customer}</p>
              ) : null}
            </>
          ) : fixedDealId === undefined ? (
            <FixedCustomer customerId={fixedCustomerId} />
          ) : (
            // The state and not `fixedDealId`: what is shown is what the body will carry.
            <FixedCustomerAndDeal dealId={dealId} />
          )}

          {fixedDealId === undefined && customerId !== '' ? (
            <DealPicker
              customerId={customerId}
              value={dealId}
              onChange={(value) => setDealId(value === NO_DEAL ? '' : value)}
            />
          ) : null}

          <div className="space-y-2">
            <Label htmlFor="proforma-causale">
              Causale
              <span className="ml-1 text-destructive">*</span>
            </Label>
            <Input
              id="proforma-causale"
              value={causale}
              onChange={(event) => setCausale(event.target.value)}
              placeholder="Consulenza settembre 2026"
            />
            {errors.causale ? (
              <p className="text-sm text-destructive">{errors.causale}</p>
            ) : null}
          </div>

          <div className="space-y-2">
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="proforma-data">
                  Data
                  <span className="ml-1 text-destructive">*</span>
                </Label>
                <Input
                  id="proforma-data"
                  type="date"
                  required
                  value={dataEmissione}
                  onChange={(event) => setDataEmissione(event.target.value)}
                />
              </div>
              <AccrualPeriodFields idPrefix="proforma" value={competenza} onChange={setCompetenza} />
            </div>
            {errors.data ? <p className="text-sm text-destructive">{errors.data}</p> : null}
            {errors.competenza ? (
              <p className="text-sm text-destructive">{errors.competenza}</p>
            ) : null}
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium">Righe</p>
            {rows.map((row, index) => (
              // The index is the key because a row has no identity yet -- nothing here
              // exists server-side, and rows are only ever added or removed whole.
              <div key={index} className="grid items-end gap-2 sm:grid-cols-12">
                <div className="sm:col-span-6">
                  <Input
                    aria-label={`Descrizione riga ${index + 1}`}
                    placeholder="Descrizione"
                    value={row.descrizione}
                    onChange={(event) => update(index, 'descrizione', event.target.value)}
                  />
                </div>
                <div className="sm:col-span-2">
                  <Input
                    aria-label={`Quantità riga ${index + 1}`}
                    placeholder="Quantità"
                    value={row.quantita}
                    onChange={(event) => update(index, 'quantita', event.target.value)}
                  />
                </div>
                <div className="sm:col-span-3">
                  <Input
                    aria-label={`Prezzo unitario riga ${index + 1}`}
                    placeholder="Prezzo unitario"
                    value={row.prezzo_unitario}
                    onChange={(event) => update(index, 'prezzo_unitario', event.target.value)}
                  />
                </div>
                <div className="sm:col-span-1">
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Rimuovi riga ${index + 1}`}
                    // The last row is never removable: a document with no rows is not
                    // something this form offers (see `validate`), and an empty list
                    // would leave nothing to type into.
                    disabled={rows.length === 1}
                    onClick={() =>
                      setRows((previous) => previous.filter((_, position) => position !== index))
                    }
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            ))}
            {errors.righe ? <p className="text-sm text-destructive">{errors.righe}</p> : null}

            <div className="flex items-center justify-between">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setRows((previous) => [...previous, newRow()])}
              >
                <Plus className="mr-2 size-4" />
                Aggiungi riga
              </Button>
              {/* Explicitly an anteprima, exactly as `InvoiceLinesEditor` labels its
                  own: the imponibile that counts is the one the service computes and
                  stores, with the regime's rules applied. */}
              <span className="text-muted-foreground text-sm">
                Anteprima imponibile: {formatMoney(previewImponibile(rows))}
              </span>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="proforma-note">Note interne (facoltative)</Label>
            <Textarea
              id="proforma-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending ? 'Creazione…' : 'Crea proforma'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * The entry point into creating a document, wherever one is offered: the Fatture page
 * with nothing fixed, a customer's Fatture tab with `customerId`, a deal's header with
 * all three.
 *
 * The dialog is *mounted* only while open rather than kept behind `open={false}`, so
 * the customer list is fetched when someone actually wants to pick from it -- the same
 * fix, for the same reason, that `DealForm`'s `CustomerPicker` documents.
 */
export function NewProformaButton(props: { prefill?: ProformaPrefill } & FixedParties) {
  const [open, setOpen] = useState(false)
  // REB-294: the button knows its own action, so every page that offers it offers it
  // only to a role the service would accept. The customer and deal pages keep their
  // coarser `canWrite` wrappers (they gate the whole header cluster); this is the one
  // that closes the Fatture list, which used to show «Nuova fattura» to a readonly
  // person and answer the press with a 403.
  const canCreate = useCan('create_invoice')
  if (!canCreate) return null
  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus className="mr-2 size-4" />
        Nuova fattura
      </Button>
      {/* Spread whole rather than destructured and re-passed: `FixedParties` is a union,
          and naming its two members one by one is exactly what loses the correlation
          between them -- `customerId` alone would widen back to `string | undefined`. */}
      {open ? <NewProformaDialog onClose={() => setOpen(false)} {...props} /> : null}
    </>
  )
}
