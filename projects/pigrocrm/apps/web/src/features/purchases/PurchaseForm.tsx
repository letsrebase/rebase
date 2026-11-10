import { Link } from '@tanstack/react-router'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { Button } from '@rebase/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { useDeal } from '@/features/deals/queries'
import { useAfterUpload } from '@/features/inbox/queries'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { useOrganization } from '@/features/organizations/queries'
import { formatMoneyValue } from '@/features/time/columns'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { formatIsoDateItalian } from '@/lib/dates'
import { useLegalEntity } from '@/lib/legalEntity'
import { useModules } from '@/lib/modules'
import { PHONE_BAR_HEIGHT } from '@/components/PhoneBar'
import type { FieldDefinition } from '@/lib/schema'
import type { NewPurchaseSearch } from './formSearch'
import { usePurchaseCapabilities } from './capabilities'
import { FieldRows, FormGrid, FormRow, WideRow } from './PurchaseFormLayout'
import { VatLinesEditor } from './VatLinesEditor'
import {
  KINDS,
  KIND_LABELS,
  PICKER_KEYS,
  carriesWithholding,
  emptyVatLine,
  fieldsFor,
  isDocumentKind,
  isForeignCurrency,
  newPurchaseFormValues,
  purchaseToFormValues,
  toPurchaseRequestBody,
  type FieldVisibility,
  type PurchaseFormValues,
  type VatLineDraft,
} from './formValues'
import {
  useCreatePurchase,
  usePurchases,
  useUpdatePurchase,
  type CostCategory,
  type Purchase,
  type PurchaseKind,
} from './queries'

export interface PurchaseFormProps {
  /** The purchase being edited. Absent means «Nuovo acquisto»: the mode is derived from this
   *  and from nothing else, so «which mode» and «what is seeded» can never disagree. */
  purchase?: Purchase
  /** What a new purchase opens with; ignored when editing. `deal_id` is not a control: it comes
   *  from here, and a purchase with none is a general one (`null`, never some default deal). */
  defaults?: NewPurchaseSearch
  /** Every category, archived ones included: an archived one is offered only while it is the
   *  purchase's own, so nothing new is filed under it and the edit still names it. */
  categories: CostCategory[]
  customFields: FieldDefinition[]
  /** Called with the purchase the server answered, after the toast. */
  onSaved: (purchase: Purchase) => void
  onCancel: () => void
}

/** The picker's value for a shared purchase: the body carries no `legal_entity_id`, or `null`
 *  when clearing one, and a `Select` needs a non-empty string for the option. */
const SHARED = 'condivisa'
/** Likewise for «no invoice» in the picker of the invoice a credit note corrects. */
const NO_INVOICE = 'nessuna'

/** A key the form draws a control of its own for: a problem on it is shown there and kept out
 *  of `FieldRows`, which would print it a second time as a raw banner. */
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

/** The deal a purchase belongs to, read-only: it is set by where the form was opened from and
 *  is never moved here. Gone with the `deals` module. */
function DealLine({ dealId }: { dealId: string }) {
  const deal = useDeal(dealId)
  return (
    <FormRow label="Deal">
      <p className="border px-3 py-2 text-sm">{deal.data?.name ?? 'Caricamento…'}</p>
    </FormRow>
  )
}

/**
 * The body of the purchase page: the fields, the VAT recap and the action bar, for
 * «Nuovo acquisto», for registering a file from «In arrivo» and for editing. The page
 * (`PurchaseFormPage`) draws the header and the file beside it; nothing here opens a dialog.
 *
 * It owns its mutations rather than taking an `onSubmit`, for the same reason
 * `TimeEntryForm` does: with both, the caller's handler and this component's own `mutate`
 * each fire a request and the same purchase is recorded twice.
 *
 * No fiscal rule is computed here. What the server decides stays the server's: the withholding
 * block is drawn for a supplier whose kind carries one, a foreign supplier gets the sentence
 * that the reverse charge is the person's call (it is never preselected), and a refusal is
 * shown in the server's words, on its field when it names one.
 */
export function PurchaseForm({
  purchase,
  defaults,
  categories,
  customFields,
  onSaved,
  onCancel,
}: PurchaseFormProps) {
  const isCreate = purchase === undefined
  const [initial] = useState<PurchaseFormValues | undefined>(() =>
    purchase === undefined ? undefined : purchaseToFormValues(purchase),
  )
  const [values, setValues] = useState<PurchaseFormValues>(
    () => initial ?? newPurchaseFormValues(defaults),
  )
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const create = useCreatePurchase()
  const update = useUpdatePurchase(purchase?.id ?? '')
  // Registering a file of «In arrivo» moves that item (and the badge that counts it): the inbox's
  // own refresh, the one an upload runs.
  const refreshInbox = useAfterUpload()
  const modules = useModules()
  const legal_entity = useLegalEntity()

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

  const kind = (values.native.kind as PurchaseKind | undefined) ?? 'expense'
  const document = isDocumentKind(kind)
  const categoryId = (values.native.category_id as string | undefined) ?? ''
  const supplierId = (values.native.supplier_id as string | null | undefined) ?? null
  const correctsId = (values.native.corrects_purchase_id as string | null | undefined) ?? null
  const vatLines = (values.native.vat_lines as VatLineDraft[] | undefined) ?? []
  const currency = values.native.currency
  const foreignCurrency = isForeignCurrency(currency)

  // The supplier is read for two facts the server owns and the form only shows: whether its
  // kind carries a withholding, and whether it is a foreign one (the reverse charge is then the
  // person's decision, said in words and never preselected).
  const supplier = useOrganization(supplierId ?? '', { enabled: document && supplierId !== null })
  const country = supplier.data?.country.trim().toUpperCase() ?? ''
  const foreignSupplier = document && country !== '' && country !== 'IT'

  // The rate and its date are the API's, not typed, once a purchase holds them in the currency
  // it still has: shown as a sentence. A new foreign purchase, or one whose currency moved,
  // has nowhere else to say them, so they are typed (the API takes all four or none).
  const heldRate =
    purchase !== undefined &&
    purchase.exchange_rate !== null &&
    purchase.exchange_rate_date !== null &&
    foreignCurrency &&
    purchase.currency === String(currency).trim().toUpperCase()
      ? { rate: purchase.exchange_rate, date: purchase.exchange_rate_date }
      : null

  const dealId = isCreate ? defaults?.deal_id : (purchase.deal_id ?? undefined)
  const dealShown = !modules.loading && modules.on('deals')

  // What the buyer's pack lets a purchase carry. The buyer is the azienda chosen here, else the
  // deal's; until the answer is in, or when it cannot be read, everything is shown. Each part the
  // pack refuses is not drawn and, through `shown`, not sent either.
  const deal = useDeal(dealId ?? '', { enabled: dealId !== undefined && dealShown })
  const chosenEntity = (values.native.legal_entity_id as string | null | undefined) ?? null
  const capabilities = usePurchaseCapabilities(chosenEntity ?? deal.data?.legal_entity_id)
  const shown: FieldVisibility = {
    vatRecap: capabilities.data?.vat_recap ?? true,
    withholding:
      document &&
      (capabilities.data?.withholding ?? true) &&
      carriesWithholding(supplier.data?.supplier_kind),
    reverseCharge: capabilities.data?.reverse_charge ?? true,
    splitPayment: capabilities.data?.split_payment ?? true,
  }
  // «Azienda» only for a purchase with no deal and from the second legal entity on (REB-626,
  // spec §1.7): a purchase on a deal is that deal's azienda's and the server would refuse
  // another, while a general purchase is shared («Condivisa», `null`) unless one legal entity
  // owns it.
  const legalEntityId = (values.native.legal_entity_id as string | null | undefined) ?? SHARED
  const offered = categories.filter((category) => !category.archived || category.id === categoryId)

  const fields = [
    ...fieldsFor(kind, foreignCurrency, shown).filter(
      (field) => !(heldRate !== null && (field.key === 'exchange_rate' || field.key === 'exchange_rate_date')),
    ),
    ...customFields,
  ]
  // Kept out of the flattened values, like the customer form does: a custom field a tenant
  // named `legal_entity_id` would otherwise display the picker's id as its own value.
  const flat = Object.fromEntries(
    Object.entries(values.native).filter(([key]) => !hasOwnControl(key)),
  )

  const fieldError = problem ? fieldErrorFrom(problem) : null
  // Where a refusal is shown: on its own control, on a `FieldRows` field, or, when it names
  // nothing drawn (a closed period, a 409), in the bar beside the buttons so it is on screen
  // when «Registra» was pressed.
  const onOwnControl = fieldError !== null && hasOwnControl(fieldError.field)
  const onDynamicField = fieldError !== null && fields.some((field) => field.key === fieldError.field)
  const errorOn = (key: string) =>
    fieldError?.field === key ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null

  function changeKind(next: PurchaseKind) {
    // A document needs a recap: open it with one row, so the first thing the form shows is
    // where the VAT goes.
    setNative({
      kind: next,
      ...(isDocumentKind(next) && vatLines.length === 0 ? { vat_lines: [emptyVatLine()] } : {}),
    })
  }

  function submit() {
    const body = toPurchaseRequestBody(values, { initial, isRenderedCustomKey, shown })
    setProblem(null)
    if (purchase === undefined) {
      // `deal_id` comes from the route, never from a control. `?? null` rather than an omitted
      // key: `null` is the API's own spelling of «acquisto generale», and leaving it out would
      // be indistinguishable from forgetting it. The file of «In arrivo» rides with it, so the
      // purchase says where it came from.
      create.mutate(
        {
          ...body,
          deal_id: defaults?.deal_id ?? null,
          ...(defaults?.inbox_item_id === undefined ? {} : { inbox_item_id: defaults.inbox_item_id }),
        },
        {
          onSuccess: (saved) => {
            if (defaults?.inbox_item_id !== undefined) refreshInbox()
            toast.success('Acquisto registrato')
            onSaved(saved)
          },
          onError: (error) => setProblem(toProblem(error)),
        },
      )
      return
    }
    update.mutate(body, {
      onSuccess: (saved) => {
        toast.success('Acquisto aggiornato')
        onSaved(saved)
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  const busy = create.isPending || update.isPending

  // The bar's own height, which changes when an error banner joins the buttons: the room left
  // under the last field on a phone is exactly that, so the field can be scrolled clear of the
  // bar instead of ending under it (REB-1057).
  const bar = useRef<HTMLDivElement>(null)
  const [barHeight, setBarHeight] = useState(0)
  useEffect(() => {
    const node = bar.current
    if (node === null || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setBarHeight(node.getBoundingClientRect().height))
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return (
    <div className="space-y-6">
      <FormGrid>
        <FormRow label="Tipo" htmlFor="purchase-kind" error={errorOn('kind')}>
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
        </FormRow>

        {document && (
          <FormRow
            label="Fornitore"
            htmlFor="purchase-supplier"
            required
            error={errorOn('supplier_id')}
            hint={
              <>
                {supplierId === null && (
                  <p className="text-sm text-muted-foreground">
                    Non lo trovi? Aggiungilo tra i fornitori, poi torna qui:{' '}
                    {/* A new tab: this page holds a half-filled form. */}
                    <Link
                      to="/app/organizations"
                      search={{ role: 'supplier' }}
                      target="_blank"
                      rel="noreferrer"
                      className="underline underline-offset-2"
                    >
                      Anagrafiche, Fornitori
                    </Link>
                    .
                  </p>
                )}
                {foreignSupplier && shown.reverseCharge && (
                  <p className="text-sm text-muted-foreground">
                    Lo decidi tu: il fornitore è estero. Spunta «Inversione contabile» solo se vale
                    per questo acquisto.
                  </p>
                )}
              </>
            }
          >
            <OrganizationPicker
              id="purchase-supplier"
              role="supplier"
              value={supplierId}
              onChange={(picked) =>
                setNative({
                  supplier_id: picked?.id ?? null,
                  // The invoice a credit note corrects is the supplier's: another supplier,
                  // another list.
                  ...(picked?.id === supplierId ? {} : { corrects_purchase_id: null }),
                })
              }
              placeholder="Cerca un fornitore per ragione sociale, P.IVA o email…"
              ariaLabel="Cerca un fornitore"
            />
          </FormRow>
        )}

        {kind === 'credit_note' && supplierId !== null && (
          <FormRow
            label="Fattura corretta"
            htmlFor="purchase-corrects"
            error={errorOn('corrects_purchase_id')}
          >
            <CorrectedInvoicePicker
              supplierId={supplierId}
              value={correctsId}
              onChange={(id) => setNative({ corrects_purchase_id: id })}
            />
          </FormRow>
        )}

        <FieldRows
          fields={fields}
          // Flattened for rendering only -- one control per key is all a form can draw. The state
          // behind it stays split, so `toPurchaseRequestBody` still knows which namespace each
          // value came from.
          values={{ ...flat, ...values.custom }}
          onChange={change}
          // Only a problem this form draws on one of its own fields: the rest is shown in the bar
          // below, once, and never as a second banner in the middle of the page.
          problem={onDynamicField && !onOwnControl ? fieldError : null}
          mode={isCreate ? 'create' : 'edit'}
        />

        {heldRate !== null && (
          <WideRow>
            <p className="text-sm text-muted-foreground">
              Cambio {heldRate.rate.replace(/0+$/, '').replace(/\.$/, '').replace('.', ',')} del{' '}
              {formatIsoDateItalian(heldRate.date)}
            </p>
          </WideRow>
        )}

        {document && shown.vatRecap && (
          <WideRow>
            <VatLinesEditor
              lines={vatLines}
              onChange={(lines) => setNative({ vat_lines: lines })}
              error={fieldError?.field === 'vat_lines' ? fieldError.message : undefined}
            />
          </WideRow>
        )}

        <FormRow
          label="Categoria"
          htmlFor="purchase-category"
          required
          error={errorOn('category_id')}
        >
          {/* Its own control rather than a `select` field handed to `DynamicFieldRenderer`: that
              renderer submits the option string itself, and this value has to be the
              category's UUID so a later rename does not orphan the purchase. */}
          <Select value={categoryId} onValueChange={(value) => change('category_id', value)}>
            <SelectTrigger id="purchase-category" className="w-full">
              <SelectValue placeholder="Seleziona…" />
            </SelectTrigger>
            <SelectContent>
              {offered.map((category) => (
                <SelectItem key={category.id} value={category.id}>
                  {category.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </FormRow>

        {dealId === undefined && legal_entity.several ? (
          <FormRow
            label="Azienda"
            htmlFor="purchase-legal-entity"
            error={errorOn('legal_entity_id')}
          >
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
          </FormRow>
        ) : null}

        {dealId !== undefined && dealShown ? <DealLine dealId={dealId} /> : null}
      </FormGrid>

      <div aria-hidden className="sm:hidden" style={{ height: barHeight }} />

      {/* The action bar, bottom right; sticky so «Annulla» and «Registra» stay in reach on a
          phone, where the form is long. */}
      {/* Below `sm` the phone's navigation is fixed over the bottom of the page, so the bar
          sticks above it (its height and the home indicator's inset) and «Registra» is never
          covered. */}
      <div
        ref={bar}
        style={{ '--phone-bar': `calc(${PHONE_BAR_HEIGHT} + env(safe-area-inset-bottom))` } as CSSProperties}
        className="sticky bottom-(--phone-bar) z-10 space-y-2 border-t bg-background py-3 sm:bottom-0"
      >
        {problem !== null && !onOwnControl && !onDynamicField && (
          <p
            role="alert"
            className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {fieldError === null ? problem.detail : fieldError.message}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel} disabled={busy}>
            Annulla
          </Button>
          <Button type="button" onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : isCreate ? 'Registra' : 'Salva'}
          </Button>
        </div>
      </div>
    </div>
  )
}
