import { Link, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, FileText, ShoppingCart } from 'lucide-react'
import { useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@rebase/ui/sheet'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import { useCan } from '@/lib/auth'
import { useEntitySchema } from '@/lib/schema'
import type { NewPurchaseSearch } from './formSearch'
import { useInboxItem } from '@/features/inbox/queries'
import { PurchaseFile } from './PurchaseFile'
import { PurchaseForm } from './PurchaseForm'
import { useCostCategories, usePurchase } from './queries'

/** From `lg` the file sits beside the form; below it the file is behind «Vedi il file» and opens
 *  over the form (the approved brief of 2026-11-10: phone first). */
const BESIDE = '(min-width: 1024px)'

const DESCRIPTIONS = {
  create: 'Una fattura o una nota di credito di un fornitore, uno scontrino o una spesa.',
  capture: 'Controlla i dati contro il file e registra l’acquisto.',
  edit: 'Correggi i dati dell’acquisto.',
} as const

/**
 * One page for «Nuovo acquisto», for «Registra come acquisto» (the form with the file of an
 * «In arrivo» item beside it) and for editing a purchase: `purchaseId` decides editing,
 * `defaults.inbox_item_id` decides the file, and the form underneath is the same component in
 * every case (`PurchaseForm`).
 *
 * A cancelled purchase is not editable and a read-only member does not register or edit:
 * each gets a sentence and the way back, never a form that could only be refused.
 */
export function PurchaseFormPage({
  purchaseId,
  defaults,
}: {
  purchaseId?: string
  defaults?: NewPurchaseSearch
}) {
  const editing = purchaseId !== undefined
  const allowed = useCan(editing ? 'update_purchase' : 'create_purchase')
  const navigate = useNavigate()
  const purchase = usePurchase(purchaseId ?? '')
  const categories = useCostCategories(true)
  const schema = useEntitySchema('purchase')
  const beside = useMediaQuery(BESIDE)
  const [fileOpen, setFileOpen] = useState(false)

  const inboxItemId = editing ? purchase.data?.inbox_item_id : defaults?.inbox_item_id
  // The file is drawn, and the layout made of two columns for it, only when the item really has
  // one. An item that could not be read keeps the pane, so its error and «Riprova» are seen.
  const item = useInboxItem(inboxItemId ?? null)
  const hasFile = inboxItemId != null && (item.isError || item.data?.has_file === true)
  const title = editing
    ? 'Modifica acquisto'
    : defaults?.inbox_item_id !== undefined
      ? 'Registra come acquisto'
      : 'Nuovo acquisto'
  const description = editing
    ? DESCRIPTIONS.edit
    : defaults?.inbox_item_id !== undefined
      ? DESCRIPTIONS.capture
      : DESCRIPTIONS.create

  const back = editing ? (
    <Button variant="ghost" size="sm" asChild>
      <Link to="/app/purchases/$purchaseId" params={{ purchaseId }}>
        <ArrowLeft className="mr-2 size-4" />
        Torna all’acquisto
      </Link>
    </Button>
  ) : (
    <Button variant="ghost" size="sm" asChild>
      <Link to="/app/purchases">
        <ArrowLeft className="mr-2 size-4" />
        Acquisti
      </Link>
    </Button>
  )

  const header = (
    <PageHeader icon={ShoppingCart} title={title} description={description}>
      <div className="flex flex-wrap items-center gap-2 px-8 pb-4">
        {back}
        {hasFile && !beside ? (
          <Button type="button" variant="outline" size="sm" onClick={() => setFileOpen(true)}>
            <FileText className="mr-2 size-4" />
            Vedi il file
          </Button>
        ) : null}
      </div>
    </PageHeader>
  )

  if (!allowed) {
    return (
      <>
        {header}
        <p role="status" className="px-8 pb-8 text-sm">
          Con il tuo ruolo puoi leggere gli acquisti, ma non registrarli né modificarli.
        </p>
      </>
    )
  }

  if (editing && purchase.isPending) {
    return (
      <>
        {header}
        <LoadingState label="Leggo l’acquisto…" className="px-8" />
      </>
    )
  }
  if (editing && purchase.isError) {
    return (
      <>
        {header}
        <div className="px-8 pb-8">
          <QueryErrorBanner error={purchase.error} onRetry={() => void purchase.refetch()} />
        </div>
      </>
    )
  }
  if (editing && purchase.data?.status === 'cancelled') {
    return (
      <>
        {header}
        <p role="status" className="px-8 pb-8 text-sm">
          Un acquisto annullato non si modifica: resta nel registro com’è.
        </p>
      </>
    )
  }
  if (categories.isError) {
    return (
      <>
        {header}
        <div className="px-8 pb-8">
          <QueryErrorBanner error={categories.error} onRetry={() => void categories.refetch()} />
        </div>
      </>
    )
  }
  if (categories.isPending) {
    return (
      <>
        {header}
        <LoadingState label="Leggo le categorie…" className="px-8" />
      </>
    )
  }

  const form = (
    <PurchaseForm
      // One state per purchase: the form seeds itself once, so reusing an instance across two
      // purchases would show the first one's values on the second.
      key={purchaseId ?? 'new'}
      purchase={purchase.data}
      defaults={defaults}
      categories={categories.data}
      customFields={schema.data?.custom_fields ?? []}
      onCancel={() =>
        void (editing
          ? navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId } })
          : navigate({ to: '/app/purchases' }))
      }
      onSaved={(saved) =>
        void navigate({ to: '/app/purchases/$purchaseId', params: { purchaseId: saved.id } })
      }
    />
  )

  return (
    <>
      {header}
      <div className="px-8 pb-8">
        {hasFile && beside ? (
          // Two columns: the form scrolls with the page, the file stays in view with a scroll of
          // its own, so a long form is checked against the original without losing it.
          <div className="grid grid-cols-2 items-start gap-8">
            <div className="min-w-0">{form}</div>
            <aside
              aria-label="Il file dell’acquisto"
              className="sticky top-4 h-[calc(100dvh-8rem)] min-h-[24rem] overflow-auto"
            >
              <PurchaseFile inboxItemId={inboxItemId} />
            </aside>
          </div>
        ) : (
          form
        )}
      </div>

      {hasFile && !beside ? (
        <Sheet open={fileOpen} onOpenChange={setFileOpen}>
          <SheetContent
            side="right"
            closeLabel="Chiudi"
            className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-none"
          >
            <SheetTitle className="sr-only">Il file</SheetTitle>
            <SheetDescription className="sr-only">
              Il file da cui nasce l’acquisto, da controllare contro i dati del form.
            </SheetDescription>
            <div className="h-[70dvh] overflow-auto p-4 pt-12">
              <PurchaseFile inboxItemId={inboxItemId} />
            </div>
          </SheetContent>
        </Sheet>
      ) : null}
    </>
  )
}
