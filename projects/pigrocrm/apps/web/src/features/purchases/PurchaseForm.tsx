import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { DynamicForm } from '@/components/DynamicForm'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { formatMoneyValue } from '@/features/time/columns'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import type { FieldDefinition } from '@/lib/schema'
import { VatLinesEditor } from './VatLinesEditor'
import {
  KINDS,
  KIND_LABELS,
  PICKER_KEYS,
  defaultPurchaseFormValues,
  emptyVatLine,
  fieldsFor,
  isDocumentKind,
  isForeignCurrency,
  toPurchaseRequestBody,
  type PurchaseFormValues,
  type VatLineDraft,
} from './formValues'
import {
  useCancelPurchase,
  useCreatePurchase,
  useDeletePurchase,
  usePurchases,
  useRestorePurchase,
  useUpdatePurchase,
  type CostCategory,
  type Purchase,
  type PurchaseKind,
} from './queries'

export interface PurchaseFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Absent means a general purchase (§7.4): one that belongs to the business and to
   *  no deal. Never defaulted to some deal to keep the body simple -- that is exactly
   *  how a general purchase would silently become one client's cost. */
  dealId?: string
  categories: CostCategory[]
  customFields: FieldDefinition[]
  /** Absent means "compose a new purchase". Derived rather than a separate boolean prop,
   *  as `DealForm` and `TimeEntryForm` both do it, so "which mode" and "what is seeded"
   *  can never disagree. */
  initial?: PurchaseFormValues
  /** The purchase being edited, for its id and its status. */
  purchase?: Purchase
  title: string
  onSaved?: () => void
}

/** The picker's value for a shared purchase: the body carries no `legal_entity_id`, or `null`
 *  when clearing one, and a `Select` needs a non-empty string for the option. */
const SHARED = 'condivisa'
/** Likewise for «no invoice» in the picker of the invoice a credit note corrects. */
const NO_INVOICE = 'nessuna'

/** A key the form draws a control of its own for: a problem on it is shown there and kept out
 *  of `DynamicForm`, which would print it a second time as a raw banner. */
const hasOwnControl = (key: string) => (PICKER_KEYS as readonly string[]).includes(key)

/** The invoices of one supplier, for the credit note that corrects one of them. */
function CorrectedInvoicePicker({
  supplierId,
  value,
  onChange,
}: {
  supplierId: string
  value: string | null
  onChange: (id: string | null) => void
}) {
  const invoices = usePurchases({ kind: 'invoice', supplier_id: supplierId })
  return (
    <Select
      value={value ?? NO_INVOICE}
      onValueChange={(next) => onChange(next === NO_INVOICE ? null : next)}
    >
      <SelectTrigger id="purchase-corrects" className="w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NO_INVOICE}>Nessuna</SelectItem>
        {(invoices.data?.items ?? []).map((invoice) => (
          <SelectItem key={invoice.id} value={invoice.id}>
            {`${invoice.number ?? 'senza numero'} · ${formatIsoDateItalian(invoice.date)} · ${formatMoneyValue(invoice.amount)}`}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * The one dialog behind both «Registra acquisto» and «Modifica acquisto».
 *
 * It owns its mutations rather than taking an `onSubmit`, for the same reason
 * `TimeEntryForm` does: with both, the caller's handler and this component's own
 * `mutate` each fire a POST and the same purchase is recorded twice.
 */
export function PurchaseForm({
  open,
  onOpenChange,
  dealId,
  categories,
  customFields,
  initial,
  purchase,
  title,
  onSaved,
}: PurchaseFormProps) {
  const [values, setValues] = useState<PurchaseFormValues>(initial ?? defaultPurchaseFormValues())
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const isCreate = initial === undefined
  const create = useCreatePurchase()
  const update = useUpdatePurchase(purchase?.id ?? '')
  const remove = useDeletePurchase()
  const restore = useRestorePurchase()
  const cancel = useCancelPurchase()

  // Same "reset synchronously when `open` toggles" pattern as OrganizationForm/DealForm/
  // TimeEntryForm: this component stays mounted across dialog opens, so a plain
  // useState initializer would only ever run once.
  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setValues(initial ?? defaultPurchaseFormValues())
      setProblem(null)
    }
  }

  const isRenderedCustomKey = (key: string) => customFields.some((field) => field.key === key)

  function change(key: string, value: unknown) {
    setValues((previous) =>
      isRenderedCustomKey(key) || key in previous.custom
        ? { ...previous, custom: { ...previous.custom, [key]: value } }
        : { ...previous, native: { ...previous.native, [key]: value } },
    )
  }

  /** Straight into `native`, not through `change`, which would route a key a tenant
   *  happened to define as a custom field into `custom`. */
  function setNative(patch: Record<string, unknown>) {
    setValues((previous) => ({ ...previous, native: { ...previous.native, ...patch } }))
  }

  function changeKind(next: PurchaseKind) {
    const lines = (values.native.vat_lines as VatLineDraft[] | undefined) ?? []
    // A document needs a recap: open it with one row, so the first thing the form shows is
    // where the VAT goes.
    setNative({
      kind: next,
      ...(isDocumentKind(next) && lines.length === 0 ? { vat_lines: [emptyVatLine()] } : {}),
    })
  }

  function submit() {
    const body = toPurchaseRequestBody(values, { initial, isRenderedCustomKey })
    setProblem(null)
    if (isCreate) {
      // `deal_id` comes from the route, never from a control. `?? null` rather than an
      // omitted key: `null` is the API's own spelling of "acquisto generale", and leaving
      // it out would be indistinguishable from forgetting it.
      create.mutate(
        { ...body, deal_id: dealId ?? null },
        {
          onSuccess: () => {
            onOpenChange(false)
            onSaved?.()
            toast.success('Acquisto registrato')
          },
          onError: (error) => setProblem(toProblem(error)),
        },
      )
      return
    }
    update.mutate(body, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Acquisto aggiornato')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function archive() {
    // The same cheap misclick guard the other forms use, and the wording says what
    // actually happens: `soft_delete` sets `deleted_at`, so nothing is lost.
    const confirmed = window.confirm(
      'Eliminare questo acquisto? Sparisce dagli elenchi e dai conti del deal, ma il dato resta e l’operazione è reversibile.',
    )
    if (!confirmed || purchase === undefined) return
    const id = purchase.id
    remove.mutate(id, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Acquisto eliminato', {
          action: { label: 'Ripristina', onClick: () => restore.mutate(id) },
        })
      },
      // A closed period refuses the delete with its own sentence: shown here rather
      // than swallowed, for the same reason a failed save is.
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function annul() {
    const confirmed = window.confirm(
      'Annullare questo acquisto? Resta nel registro come annullato e non conta più nei costi.',
    )
    if (!confirmed || purchase === undefined) return
    cancel.mutate(purchase.id, {
      onSuccess: () => {
        onSaved?.()
        toast.success('Acquisto annullato')
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy =
    create.isPending || update.isPending || remove.isPending || restore.isPending || cancel.isPending
  const fieldError = problem ? fieldErrorFrom(problem) : null
  const kind = (values.native.kind as PurchaseKind | undefined) ?? 'expense'
  const document = isDocumentKind(kind)
  const categoryId = (values.native.category_id as string | undefined) ?? ''
  const supplierId = (values.native.supplier_id as string | null | undefined) ?? null
  const correctsId = (values.native.corrects_purchase_id as string | null | undefined) ?? null
  const vatLines = (values.native.vat_lines as VatLineDraft[] | undefined) ?? []
  // «Azienda» only for a purchase with no deal and from the second legal entity on (REB-626,
  // spec §1.7): a purchase on a deal is that deal's azienda's and the server would refuse
  // another, while a general purchase is shared («Condivisa», `null`) unless one legal entity
  // owns it.
  const legal_entity = useLegalEntity()
  const legalEntityId = (values.native.legal_entity_id as string | null | undefined) ?? SHARED
  // Kept out of the flattened values, like the customer form does: a custom field a
  // tenant named `legal_entity_id` would otherwise display the picker's id as its own value.
  const flat = Object.fromEntries(
    Object.entries(values.native).filter(([key]) => !hasOwnControl(key)),
  )
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="purchase-kind">Tipo</Label>
          {/* Fixed once registered: a receipt does not become an invoice by editing, it is
              registered again. */}
          <Select
            value={kind}
            disabled={!isCreate}
            onValueChange={(value) => changeKind(value as PurchaseKind)}
          >
            <SelectTrigger id="purchase-kind" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((item) => (
                <SelectItem key={item} value={item}>
                  {KIND_LABELS[item]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {kind === 'credit_note' && (
            <p className="text-sm text-muted-foreground">
              Il totale di una nota di credito è negativo.
            </p>
          )}
          {errorOn('kind')}
        </div>

        {document && (
          <div className="space-y-2">
            <Label htmlFor="purchase-supplier">Fornitore</Label>
            <OrganizationPicker
              id="purchase-supplier"
              role="supplier"
              value={supplierId}
              onChange={(supplier) =>
                setNative({
                  supplier_id: supplier?.id ?? null,
                  // The invoice a credit note corrects is the supplier's: another supplier,
                  // another list.
                  ...(supplier?.id === supplierId ? {} : { corrects_purchase_id: null }),
                })
              }
              placeholder="Cerca un fornitore per ragione sociale, P.IVA o email…"
              ariaLabel="Cerca un fornitore"
            />
            {errorOn('supplier_id')}
          </div>
        )}

        {kind === 'credit_note' && supplierId !== null && (
          <div className="space-y-2">
            <Label htmlFor="purchase-corrects">Fattura corretta</Label>
            <CorrectedInvoicePicker
              supplierId={supplierId}
              value={correctsId}
              onChange={(id) => setNative({ corrects_purchase_id: id })}
            />
            {errorOn('corrects_purchase_id')}
          </div>
        )}

        <div className="space-y-2">
          <Label htmlFor="purchase-category">Categoria</Label>
          {/* Its own control rather than a `select` field handed to `DynamicForm`: that
              renderer submits the option string itself, and this value has to be the
              category's UUID so a later rename does not orphan the purchase. Only the
              active categories are offered -- an archived one is still shown on rows
              that already reference it, but nothing new should be filed under it. */}
          <Select value={categoryId} onValueChange={(value) => change('category_id', value)}>
            <SelectTrigger id="purchase-category" className="w-full">
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
          {errorOn('category_id')}
        </div>

        {dealId === undefined && legal_entity.several ? (
          <div className="space-y-2">
            <Label htmlFor="purchase-legal-entity">Azienda</Label>
            <Select
              value={legalEntityId}
              onValueChange={(value) => setNative({ legal_entity_id: value === SHARED ? null : value })}
            >
              <SelectTrigger id="purchase-legal-entity" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={SHARED}>Condivisa</SelectItem>
                {legal_entity.legal_entities.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {errorOn('legal_entity_id')}
          </div>
        ) : null}

        <DynamicForm
          fields={[...fieldsFor(kind, isForeignCurrency(values.native.currency)), ...customFields]}
          // Flattened for rendering only -- one control per key is all a form can draw.
          // The state behind it stays split, so `toPurchaseRequestBody` still knows which
          // namespace each value came from.
          values={{ ...flat, ...values.custom }}
          onChange={change}
          // Withheld when the server blamed a key that has no control inside
          // `DynamicForm`: it would render the message a second time as a raw
          // `key: ...` banner next to the line already shown on its own control.
          problem={fieldError !== null && hasOwnControl(fieldError.field) ? null : problem}
          mode={isCreate ? 'create' : 'edit'}
        />

        {document && (
          <VatLinesEditor
            lines={vatLines}
            onChange={(lines) => setNative({ vat_lines: lines })}
            error={fieldError?.field === 'vat_lines' ? fieldError.message : undefined}
          />
        )}

        <DialogFooter>
          {!isCreate && (
            <Button variant="destructive" className="mr-auto" onClick={archive} disabled={busy}>
              Elimina
            </Button>
          )}
          {purchase !== undefined && purchase.status === 'registered' && (
            <Button variant="outline" onClick={annul} disabled={busy}>
              Annulla acquisto
            </Button>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Chiudi
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
