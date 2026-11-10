import { useId } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Checkbox } from '@rebase/ui/checkbox'
import { Label } from '@rebase/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@rebase/ui/table'
import { OrganizationPicker } from '@/features/organizations/OrganizationPicker'
import { EMPTY, formatDate, formatMoney } from './format'
import {
  CONFIRM_OUTCOME_LABELS,
  countByOutcome,
  countConfirmed,
  newParties,
  plural,
  REVIEW_OUTCOME_LABELS,
  REVIEW_OUTCOME_NOTES,
  REVIEW_OUTCOME_TONES,
  registerYears,
  sortReviewRows,
  type ChosenOrganization,
  type ConfirmRow,
  type ReviewRow,
} from './importPlan'

/**
 * What an issued-invoice import shows before and after it writes: the review of every
 * invoice (what the import would do with it, and the customers it would create) and the
 * result. Shared by the Fatture page's «Importa» dialog and by the «In arrivo» panel of a
 * sales invoice that waits for a person's confirmation (REB-984): both run the same review
 * and the same write, so both read the same words.
 */

const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  invoice: 'Fattura',
  invoice_advance: 'Acconto su fattura',
  fee_advance: 'Acconto su parcella',
  note_credit: 'Nota di credito',
  note_debit: 'Nota di debito',
  fee_note: 'Parcella',
}

export function ReviewPanel({
  rows,
  declined,
  chosen,
  onChoose,
  onToggleParty,
}: {
  rows: ReviewRow[]
  declined: ReadonlySet<string>
  chosen: ReadonlyMap<string, ChosenOrganization>
  onChoose: (key: string, customer: ChosenOrganization | undefined) => void
  onToggleParty: (key: string) => void
}) {
  const counts = countByOutcome(rows)
  const parties = newParties(rows)
  const sorted = sortReviewRows(rows)
  return (
    <div className="space-y-4">
      <ul aria-label="Esito della lettura" className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {(Object.keys(REVIEW_OUTCOME_LABELS) as (keyof typeof REVIEW_OUTCOME_LABELS)[])
          .filter((outcome) => counts[outcome])
          .map((outcome) => (
            <li key={outcome}>
              <span className="text-muted-foreground">{REVIEW_OUTCOME_LABELS[outcome]}</span>{' '}
              <span className="font-medium">{counts[outcome]}</span>
            </li>
          ))}
      </ul>

      {parties.length > 0 ? (
        <section aria-labelledby="import-new-organizations" className="space-y-2 border p-3">
          <h3 id="import-new-organizations" className="font-medium">
            Clienti da creare
          </h3>
          <p className="text-sm text-muted-foreground">
            Questi clienti non sono in anagrafica. Lasciali spuntati per crearli insieme alle
            loro fatture, oppure scegli un cliente che hai già: le fatture vanno a lui e non ne
            nasce un altro. Se ne togli uno, le sue fatture non vengono importate.
          </p>
          {parties.map((party) => (
            <div key={party.key} className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <Checkbox
                  checked={!declined.has(party.key)}
                  onCheckedChange={() => onToggleParty(party.key)}
                />
                <span className="font-medium">{party.name}</span>
                <span className="text-muted-foreground">
                  {party.taxId !== '' ? `${party.taxId} · ` : ''}
                  {plural(party.invoices, 'fattura', 'fatture')}
                </span>
              </label>
              <ExistingOrganizationPicker
                party={party.taxId !== '' ? `${party.name} (${party.taxId})` : party.name}
                legalEntityId={party.legalEntityId}
                mixedLegalEntities={party.mixedLegalEntities}
                value={chosen.get(party.key)}
                disabled={declined.has(party.key) || party.mixedLegalEntities}
                onChange={(customer) => onChoose(party.key, customer)}
              />
            </div>
          ))}
        </section>
      ) : null}

      <div className="max-h-[40vh] overflow-y-auto border">
        <Table scrollLabel="Fatture del file">
          <TableHeader className="sticky top-0 bg-popover">
            <TableRow>
              <TableHead>Fattura</TableHead>
              <TableHead>Cliente</TableHead>
              <TableHead className="text-right">Totale</TableHead>
              <TableHead>File</TableHead>
              <TableHead>Esito</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((row, index) => (
              <ReviewTableRow
                key={`${row.sha256}-${index}`}
                row={row}
                declined={declined}
                chosen={chosen}
              />
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

/**
 * Attach a new party to a customer already on file (REB-777). `match_customer` finds one by
 * an Italian VAT number or tax code, so a customer without one (a foreign customer, a
 * private person with no tax code stated) is offered for creation on every import; this is
 * the way to say it is somebody already known. It is the app's customer search
 * (`OrganizationPicker`), limited to the legal entity the invoice is written on: the server takes
 * any `customer_id` it is sent, and an invoice on one legal entity's register for another's
 * customer is a state nothing else in the product can produce. Left empty, the party is
 * created as a new customer, which is the default and the old behaviour.
 */
function ExistingOrganizationPicker({
  party,
  legalEntityId,
  mixedLegalEntities,
  value,
  disabled,
  onChange,
}: {
  party: string
  legalEntityId: string | null
  mixedLegalEntities: boolean
  value: ChosenOrganization | undefined
  disabled: boolean
  onChange: (customer: ChosenOrganization | undefined) => void
}) {
  const id = useId()
  return (
    <div className="flex flex-wrap items-start gap-2">
      <Label htmlFor={id} className="pt-2 text-sm text-muted-foreground">
        Cliente
      </Label>
      <div className="w-72">
        <OrganizationPicker
          id={id}
          value={value?.id ?? null}
          selectedName={value?.name}
          legalEntityId={legalEntityId}
          ariaLabel={`Cliente di ${party}`}
          placeholder="Crea un nuovo cliente, o cerca uno già in anagrafica…"
          disabled={disabled}
          onChange={(customer) =>
            onChange(
              customer === null ? undefined : { id: customer.id, name: customer.legal_name },
            )
          }
        />
      </div>
      {mixedLegalEntities ? (
        <span className="pt-2 text-xs text-muted-foreground">
          Le sue fatture sono di più aziende: non si può collegare a un cliente che hai già.
        </span>
      ) : null}
    </div>
  )
}

function ReviewTableRow({
  row,
  declined,
  chosen,
}: {
  row: ReviewRow
  declined: ReadonlySet<string>
  chosen: ReadonlyMap<string, ChosenOrganization>
}) {
  const { invoice } = row
  const skipped =
    row.outcome === 'needs_customer_confirmation' &&
    row.party_key != null &&
    declined.has(row.party_key ?? '')
  const attached =
    !skipped && row.outcome === 'needs_customer_confirmation' && row.party_key != null
      ? chosen.get(row.party_key)
      : undefined
  const note = skipped
    ? 'Il cliente non viene creato: questa fattura non viene importata.'
    : attached !== undefined
      ? `Va al cliente «${attached.name}», già in anagrafica: non ne viene creato un altro.`
      : row.outcome === 'needs_customer_confirmation' && row.existing_organization_id != null
        ? "Il cliente è già in anagrafica con un'altra azienda: viene condiviso fra le aziende, non ne viene creato un altro."
        : row.outcome === 'unclaimed' && row.reason
      ? `Sembra una fattura FatturaPA ma non si legge: ${row.reason}`
      : REVIEW_OUTCOME_NOTES[row.outcome]
  return (
    <TableRow>
      <TableCell className="whitespace-nowrap">
        {invoice ? (
          <>
            <span className="font-medium">{invoice.number}</span>
            <span className="block text-xs text-muted-foreground">
              {formatDate(invoice.issue_date)}
              {invoice.sdi_document_type !== 'invoice'
                ? ` · ${DOCUMENT_TYPE_LABELS[invoice.sdi_document_type] ?? invoice.sdi_document_type}`
                : ''}
            </span>
          </>
        ) : (
          EMPTY
        )}
      </TableCell>
      <TableCell>{invoice ? invoice.customer.legal_name : EMPTY}</TableCell>
      <TableCell className="text-right whitespace-nowrap">
        {invoice ? formatMoney(invoice.total) : EMPTY}
      </TableCell>
      <TableCell className="max-w-48 truncate text-muted-foreground" title={row.file}>
        {row.file}
      </TableCell>
      <TableCell>
        <StatusPill
          tone={skipped ? 'muted' : attached !== undefined ? 'ink' : REVIEW_OUTCOME_TONES[row.outcome]}
        >
          {attached !== undefined ? 'Cliente scelto' : REVIEW_OUTCOME_LABELS[row.outcome]}
        </StatusPill>
        {note !== '' ? <p className="mt-1 text-xs text-muted-foreground">{note}</p> : null}
      </TableCell>
    </TableRow>
  )
}

/** The outcomes of a confirmation a person has to follow up, with the file each came from. */
const FOLLOW_UP: ConfirmRow['outcome'][] = [
  'conflict',
  'unclaimed',
  'needs_customer_confirmation',
]

export function ResultPanel({
  rows,
  interrupted,
  legalEntityName,
}: {
  rows: ConfirmRow[]
  interrupted: boolean
  legalEntityName: (id: string | null) => string | undefined
}) {
  const counts = countConfirmed(rows)
  const years = registerYears(rows)
  const outcomes = Object.keys(CONFIRM_OUTCOME_LABELS) as (keyof typeof CONFIRM_OUTCOME_LABELS)[]
  const followUp = rows.filter((row) => FOLLOW_UP.includes(row.outcome))
  return (
    <div className="space-y-4" role="status">
      <ul className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {outcomes
          .filter((outcome) => counts[outcome])
          .map((outcome) => (
            <li key={outcome}>
              <span className="text-muted-foreground">{CONFIRM_OUTCOME_LABELS[outcome]}</span>{' '}
              <span className="font-medium">{counts[outcome]}</span>
            </li>
          ))}
        {rows.length === 0 && !interrupted ? <li>Nessuna fattura è stata importata.</li> : null}
        {interrupted ? (
          <li>
            L’importazione si è interrotta: quello che è già stato scritto resta nel registro.
          </li>
        ) : null}
      </ul>
      {followUp.length > 0 ? (
        <section aria-labelledby="import-follow-up" className="space-y-1 border p-3 text-sm">
          <h3 id="import-follow-up" className="font-medium">
            Da guardare
          </h3>
          <ul className="space-y-1">
            {followUp.map((row, index) => (
              <li key={`${row.sha256}-${index}`}>
                <span className="font-medium">{row.file}</span>{' '}
                <span className="text-muted-foreground">
                  {CONFIRM_OUTCOME_LABELS[row.outcome]}
                  {row.reason ? `: ${row.reason}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {years.map((year) => {
        const name = legalEntityName(year.legalEntityId)
        const label = `Registro ${year.year}${name ? ` di ${name}` : ''}`
        return (
          <p key={`${year.legalEntityId}/${year.year}`} className="border p-3 text-sm">
            <span className="font-medium">{label}: </span>
            {year.missing.length === 0
              ? 'non manca nessun numero.'
              : `mancano ancora i numeri ${year.missing.join(', ')}.`}
          </p>
        )
      })}
    </div>
  )
}
