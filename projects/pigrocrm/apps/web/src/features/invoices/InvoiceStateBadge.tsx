import { StatusPill } from '@/components/StatusPill'
import { Badge } from '@rebase/ui/badge'
import {
  INVOICE_STATE_LABELS,
  INVOICE_STATE_TONE,
  type Invoice,
  type InvoiceStato,
} from './queries'

/** Its own file rather than living beside `buildInvoiceColumns`: eslint's
 *  `react-refresh/only-export-components` refuses a module that exports both a
 *  component and a plain function, and it is right -- fast refresh cannot tell which
 *  half changed, so an edit to the column list would remount the badge and lose its
 *  state. The other three entity screens split them the same way.
 *
 *  The tone each state reads as is `INVOICE_STATE_TONE`, next to the labels in
 *  `queries.ts`, not a second table here: the state's label and the state's colour are
 *  one decision and a new state must break the compile in exactly one place. Both maps
 *  are read without a fallback, deliberately: they are total over `InvoiceStato`, so
 *  `?? 'muted'` would be a branch the types make unreachable -- and in the one case it
 *  could fire (a wire string the cast below lies about) it would render a grey pill whose
 *  label is `undefined`, which is worse than nothing at all. If that cast ever stops being
 *  safe, the fix is to narrow it here, not to tint an empty pill. */
/** The two «imported» pills' own labels, keyed by `importata_da`'s two current values
 *  (design 2026-09-23 §5 item 4/§7 item 6). `"esterno"` names no source, on purpose --
 *  see the comment below; `"fatturapa"` names the standard itself, never a product,
 *  because it is the fact that makes the difference actionable: this row's original
 *  transmitted file is on record and `export_xml` can hand it back, which a hand-typed
 *  `"esterno"` row never can. An unrecognised value (there is only ever one column to
 *  misread) falls back to the same generic pill the old single-value check rendered. */
const IMPORTED_BADGE_LABELS: Record<string, string> = {
  esterno: 'importata',
  fatturapa: 'FatturaPA',
}

export function InvoiceStateBadge({
  invoice,
  importata = true,
}: {
  invoice: Invoice
  /** Whether an `"esterno"` row also gets its uninformative «importata» pill. The
   *  detail page wants it, as the reason the XML actions are missing; the list does
   *  not (ORB-130: in a register where most rows came from the previous system the
   *  word explains nothing a reader can act on). A `"fatturapa"` row's own badge
   *  ignores this flag and always shows, everywhere, including the list: unlike a
   *  bulk `"esterno"` migration it is not most of the register, and it names a fact
   *  the list can act on -- the original file is on record and downloadable. */
  importata?: boolean
}) {
  const stato = invoice.stato as InvoiceStato
  const provenienza = invoice.importata_da
  const showImportedBadge =
    provenienza != null && (importata || provenienza === 'fatturapa')
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusPill tone={INVOICE_STATE_TONE[stato]}>
        {INVOICE_STATE_LABELS[stato]}
      </StatusPill>
      {/* The column's value is never printed verbatim -- what a reader of the register
          needs for `"esterno"` is that this invoice was issued elsewhere, so it carries
          no XML and no PDF pigroCRM produced, and not the name of the tool it came out
          of, which is the owner's business and no part of the CRM's copy. `"fatturapa"`
          is different: it is the name of the transmission standard, not a product, and
          the whole reason this second badge exists is to say so.

          A pill with no dot, deliberately: an import badge is not one of the fiscal
          states and must not read as a sixth one sitting in the same row. */}
      {showImportedBadge && provenienza != null ? (
        <Badge variant="pill" className="text-muted-foreground">
          {IMPORTED_BADGE_LABELS[provenienza] ?? 'importata'}
        </Badge>
      ) : null}
    </span>
  )
}
