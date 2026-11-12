import { FileText, Lock } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { DownloadButton } from '@/components/DownloadButton'
import { LoadingState } from '@/components/LoadingState'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toast } from '@rebase/ui/sonner'
import { formatMoneyValue } from '@/features/time/columns'
import { RejectProposalDialog } from '@/features/proposals/RejectProposalDialog'
import { useLegalEntitySites, type LegalEntitySite } from '@/features/settings/queries'
import { siteLabel } from '@/features/settings/sites'
import { formatConfidence } from '@/features/proposals/fields'
import { useAcceptProposal } from '@/features/proposals/queries'
import { toProblem } from '@/lib/api'
import { formatItalianQuantity } from '@/lib/decimal'
import { Absent, Figure } from './Amounts'
import {
  CASH_LABELS,
  STATE_LABELS,
  STATE_TONES,
  euroInput,
  formatDay,
  missingReason,
  monthName,
  reasonOf,
  stateLine,
  stateOf,
  termsLine,
} from './labels'
import {
  useAdoptHourlyCost,
  useCorrectPayslip,
  useHourlyCost,
  useRecordPayslip,
  type MemberMonth,
  type PayrollClosure,
  type PayslipProposal,
  type StaffMember,
} from './queries'

function Section({ title, tag, children }: { title: string; tag?: string; children: ReactNode }) {
  return (
    <section aria-label={title} className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">{title}</h3>
        {tag === undefined ? null : <span className="border px-1.5 text-xs text-muted-foreground">{tag}</span>}
      </div>
      {children}
    </section>
  )
}

function Rows({ rows }: { rows: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="border-t">
      {rows.map((row) => (
        <div key={row.label} className="flex justify-between gap-4 border-b py-2 text-sm">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 text-right font-medium">{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

const typed = (value: string | null, suffix = '') => (value === null ? '—' : `${formatItalianQuantity(value)}${suffix}`)

/** The cost of the month, line by line, each as the server estimated it. */
function MonthFigures({ month }: { month: MemberMonth }) {
  const cost = month.cost
  return (
    <Section title="Il mese">
      <Rows
        rows={[
          {
            label: 'Costo',
            value:
              cost === null ? (
                <Absent reason={missingReason(month)} />
              ) : (
                <Figure value={cost.total} from={month.source} tag={month.source === 'payslip' ? 'Lordo da cedolino' : undefined} />
              ),
          },
          ...(cost === null
            ? []
            : [
                { label: 'Lordo', value: <Figure value={cost.gross} from={month.source} /> },
                { label: 'INPS a carico azienda', value: cost.employer_inps === null ? <Absent reason={reasonOf('employer_inps_rate')} /> : <Figure value={cost.employer_inps} from="estimate" /> },
                { label: 'INAIL', value: cost.employer_inail === null ? <Absent reason={reasonOf('employer_inail_rate')} /> : <Figure value={cost.employer_inail} from="estimate" /> },
              ]),
        ]}
      />
      {month.cash.length === 0 ? (
        month.not_estimated.length === 0 ? <p className="text-sm text-muted-foreground">Niente esce questo mese.</p> : null
      ) : (
        <Rows
          rows={month.cash.map((item) => ({
            label: `${CASH_LABELS[item.kind]}, il ${formatDay(item.due_date)}`,
            value: (
              <Figure
                value={item.amount}
                from={month.source === 'payslip' && (item.kind === 'net_pay' || item.kind === 'withholding') ? 'payslip' : 'estimate'}
              />
            ),
          }))}
        />
      )}
      {month.not_estimated.length === 0 ? null : (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {month.not_estimated.map((code) => (
            <li key={code}>{reasonOf(code)}</li>
          ))}
        </ul>
      )}
    </Section>
  )
}

type PayslipField = { id: 'gross' | 'net' | 'withholding'; label: string; value: string; set: (value: string) => void; hint: string }

/** The three figures a payslip prints, each with what stands beside it: the estimate a new payslip
 *  replaces, or the figure a correction replaces. */
function PayslipFields({ fields }: { fields: PayslipField[] }) {
  return (
    <>
      {fields.map((field) => (
        <div key={field.id} className="grid grid-cols-[1fr_auto] items-end gap-3">
          <div className="space-y-1.5">
            <Label htmlFor={`payslip-${field.id}`}>{field.label} (€)</Label>
            <Input id={`payslip-${field.id}`} inputMode="decimal" value={field.value} onChange={(event) => field.set(event.target.value)} />
          </div>
          <p className="pb-2 text-right text-xs text-muted-foreground">{field.hint}</p>
        </div>
      ))}
    </>
  )
}

function FormError({ error }: { error: Error | null }) {
  return error === null ? null : (
    <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      {toProblem(error).detail}
    </p>
  )
}

/** «Accetta il cedolino»: the figures the consultant printed, typed beside the estimate. A person's
 *  act; it replaces the estimate of this month and drives the other months' ratios. */
function PayslipForm({ member, month, monthKey }: { member: StaffMember; month: MemberMonth; monthKey: string }) {
  const [gross, setGross] = useState('')
  const [net, setNet] = useState('')
  const [withholding, setWithholding] = useState('')
  const record = useRecordPayslip(member.id)
  const figures = { gross: euroInput(gross), net: euroInput(net), withholding: euroInput(withholding) }
  const complete = figures.gross !== null && figures.net !== null && figures.withholding !== null
  const estimatedNet = month.cash.find((item) => item.kind === 'net_pay')
  const estimatedWithholding = month.cash.find((item) => item.kind === 'withholding')
  const estimate = (amount: string | undefined) => (amount === undefined ? 'Nessuna stima' : `Stima ${formatMoneyValue(amount)}`)

  return (
    <Section title="Cedolino">
      <p className="text-sm text-muted-foreground">
        Scrivi le cifre del cedolino di {monthName(monthKey)}: prendono il posto della stima di questo mese.
      </p>
      <form
        className="space-y-3"
        onSubmit={(event) => {
          event.preventDefault()
          if (figures.gross === null || figures.net === null || figures.withholding === null) return
          record.mutate(
            { period: `${monthKey}-01`, gross: figures.gross, net: figures.net, withholding: figures.withholding },
            { onSuccess: () => toast.success('Cedolino accettato') },
          )
        }}
      >
        <PayslipFields
          fields={[
            { id: 'gross', label: 'Lordo', value: gross, set: setGross, hint: estimate(month.cost?.gross) },
            { id: 'net', label: 'Netto', value: net, set: setNet, hint: estimate(estimatedNet?.amount) },
            { id: 'withholding', label: 'Ritenute IRPEF', value: withholding, set: setWithholding, hint: estimate(estimatedWithholding?.amount) },
          ]}
        />
        <FormError error={record.error} />
        <Button type="submit" disabled={!complete || record.isPending} className="w-full sm:w-auto">
          {record.isPending ? 'Salvo…' : 'Accetta il cedolino'}
        </Button>
      </form>
    </Section>
  )
}

type Payslip = NonNullable<MemberMonth['payslip']>

/** The form of «Correggi il cedolino»: the recorded figures, to be typed over. It is mounted only
 *  while open, so each opening starts from what is recorded. */
function PayslipCorrectionForm({ member, payslip, monthKey, onDone }: { member: StaffMember; payslip: Payslip; monthKey: string; onDone: () => void }) {
  const [gross, setGross] = useState(payslip.gross)
  const [net, setNet] = useState(payslip.net)
  const [withholding, setWithholding] = useState(payslip.withholding)
  const correct = useCorrectPayslip(member.id, payslip.id)
  const figures = { gross: euroInput(gross), net: euroInput(net), withholding: euroInput(withholding) }
  const complete = figures.gross !== null && figures.net !== null && figures.withholding !== null
  const moved =
    complete && (Number(figures.gross) !== Number(payslip.gross) || Number(figures.net) !== Number(payslip.net) || Number(figures.withholding) !== Number(payslip.withholding))
  const recorded = (amount: string) => `Registrato ${formatMoneyValue(amount)}`

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        if (figures.gross === null || figures.net === null || figures.withholding === null) return
        correct.mutate(
          { gross: figures.gross, net: figures.net, withholding: figures.withholding },
          {
            onSuccess: () => {
              toast.success('Cedolino corretto')
              onDone()
            },
          },
        )
      }}
    >
      <p className="text-sm text-muted-foreground">
        Scrivi le cifre giuste del cedolino di {monthName(monthKey)}: prendono il posto di quelle registrate e le stime degli altri mesi si aggiornano.
      </p>
      <PayslipFields
        fields={[
          { id: 'gross', label: 'Lordo', value: gross, set: setGross, hint: recorded(payslip.gross) },
          { id: 'net', label: 'Netto', value: net, set: setNet, hint: recorded(payslip.net) },
          { id: 'withholding', label: 'Ritenute IRPEF', value: withholding, set: setWithholding, hint: recorded(payslip.withholding) },
        ]}
      />
      <FormError error={correct.error} />
      <div className="flex flex-col gap-2 sm:flex-row">
        <Button type="submit" disabled={!moved || correct.isPending} className="w-full sm:w-auto">
          {correct.isPending ? 'Salvo…' : 'Salva la correzione'}
        </Button>
        <Button type="button" variant="outline" onClick={onDone} disabled={correct.isPending} className="w-full sm:w-auto">
          Annulla
        </Button>
      </div>
    </form>
  )
}

/** The recorded payslip of the month and «Correggi il cedolino», a person's act like recording it:
 *  an agent credential is refused by the server. */
function PayslipRecorded({
  member,
  payslip,
  monthKey,
  closed,
}: {
  member: StaffMember
  payslip: Payslip
  monthKey: string
  closed: boolean
}) {
  const [editing, setEditing] = useState(false)
  return (
    <Section title="Cedolino" tag={closed ? 'Chiuso' : 'Registrato'}>
      <Rows
        rows={[
          { label: 'Lordo', value: formatMoneyValue(payslip.gross) },
          { label: 'Netto', value: formatMoneyValue(payslip.net) },
          { label: 'Ritenute IRPEF', value: formatMoneyValue(payslip.withholding) },
        ]}
      />
      {closed ? (
        <p className="text-sm text-muted-foreground">Il mese è chiuso: il cedolino non si corregge più.</p>
      ) : editing ? (
        <PayslipCorrectionForm member={member} payslip={payslip} monthKey={monthKey} onDone={() => setEditing(false)} />
      ) : (
        <Button variant="outline" onClick={() => setEditing(true)} className="w-full sm:w-auto">
          Correggi il cedolino
        </Button>
      )}
      {payslip.inbox_item_id == null ? null : <OpenPdf itemId={payslip.inbox_item_id} />}
    </Section>
  )
}

/** «Apri il PDF»: the file of the payslip, the inbox item's own, downloaded from there. */
function OpenPdf({ itemId }: { itemId: string }) {
  return (
    <DownloadButton variant="outline" path={`/api/inbox/items/${itemId}/file`} className="w-full sm:w-auto">
      <FileText aria-hidden className="mr-2 size-4" />
      Apri il PDF
    </DownloadButton>
  )
}

/** «Accetta il cedolino»: the proposal's own accept, the same call as in «Proposte», and only a
 *  person's: the server refuses any credential of an agent. What it refuses (a month closed
 *  meanwhile, a person not in service) is shown in its own words. */
export function AcceptPayslipButton({ proposalId, className }: { proposalId: string; className?: string }) {
  const accept = useAcceptProposal(proposalId)
  return (
    <Button
      className={className}
      disabled={accept.isPending}
      onClick={() =>
        accept.mutate(
          {},
          {
            onSuccess: () => toast.success('Cedolino accettato: prende il posto della stima'),
            onError: (error) => toast.error(toProblem(error).detail),
          },
        )
      }
    >
      {accept.isPending ? 'Accetto…' : 'Accetta il cedolino'}
    </Button>
  )
}

/** A payslip the inbox read and nobody accepted: the estimate beside it, line by line, with
 *  «Accetta il cedolino», «Rifiuta…» and «Apri il PDF» (brief REB-1010). Rejecting keeps the
 *  document and the estimate. */
function PayslipReceived({ month, received, monthKey }: { month: MemberMonth; received: PayslipProposal; monthKey: string }) {
  const [rejecting, setRejecting] = useState(false)
  const estimatedNet = month.cash.find((item) => item.kind === 'net_pay')
  const estimatedWithholding = month.cash.find((item) => item.kind === 'withholding')
  const lines: { label: string; estimate: string | undefined; payslip: string }[] = [
    { label: 'Lordo', estimate: month.cost?.gross, payslip: received.gross },
    { label: 'Netto', estimate: estimatedNet?.amount, payslip: received.net },
    { label: 'Ritenute IRPEF', estimate: estimatedWithholding?.amount, payslip: received.withholding },
  ]
  return (
    <Section title="Cedolino ricevuto" tag="Da accettare">
      <p className="text-sm text-muted-foreground">
        È arrivato il cedolino di {monthName(monthKey)}: confrontalo con la stima. Lo accetti tu; finché non lo fai, la stima resta.
      </p>
      <div role="group" aria-label="Stima e cedolino a confronto" className="grid grid-cols-[1fr_auto_auto] gap-x-3 border-t text-sm">
        <span />
        <span className="border-b py-2 text-right text-xs text-muted-foreground underline decoration-dotted underline-offset-2">Stima</span>
        <span className="border-b py-2 text-right text-xs font-medium">Cedolino</span>
        {lines.map((line) => (
          <div key={line.label} className="contents">
            <span className="border-b py-2 text-muted-foreground">{line.label}</span>
            <span className="border-b py-2 text-right tabular-nums">{line.estimate === undefined ? '—' : formatMoneyValue(line.estimate)}</span>
            <span className="border-b py-2 text-right font-semibold tabular-nums">{formatMoneyValue(line.payslip)}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        Lettura: confidenza {formatConfidence(received.confidence)}
        {received.confidence_reason === null ? '' : `, ${received.confidence_reason}`}.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        <AcceptPayslipButton proposalId={received.proposal_id} className="w-full sm:w-auto" />
        <Button variant="outline" className="w-full sm:w-auto" onClick={() => setRejecting(true)}>
          Rifiuta…
        </Button>
        {received.inbox_item_id === null ? null : <OpenPdf itemId={received.inbox_item_id} />}
      </div>
      <RejectProposalDialog
        proposalId={received.proposal_id}
        open={rejecting}
        onOpenChange={setRejecting}
        onRejected={() => toast.success('Cedolino rifiutato: resta la stima')}
        description="Non viene registrato niente: il cedolino resta in «In arrivo» e la stima di questo mese resta quella di adesso. Il motivo resta nella cronologia."
        placeholder="Per esempio: le cifre non sono quelle del cedolino."
      />
    </Section>
  )
}

/** «Sede di lavoro»: the registered office or the site the person works at. Said only where the
 *  azienda has sites, since a company with none has no place to choose (REB-1092). */
function workplaceRow(member: StaffMember, sites: readonly LegalEntitySite[]): { label: string; value: string }[] {
  if (sites.length === 0) return []
  const site = sites.find((candidate) => candidate.id === member.site_id)
  const value = member.at_registered_office ? 'Sede legale' : site === undefined ? 'Non indicata' : siteLabel(site)
  return [{ label: 'Sede di lavoro', value }]
}

function Terms({ member }: { member: StaffMember }) {
  const sites = useLegalEntitySites(member.legal_entity_id).data ?? []
  const day = member.effective_pay_day
  const payDay =
    day === null || day === undefined
      ? '—'
      : `il ${day.pay_day}${day.pay_month_offset === 1 ? ' del mese dopo' : ' del mese lavorato'}, ${day.source === 'staff_member' ? 'solo per questa persona' : 'come l’azienda'}`
  const rows =
    member.kind === 'employee'
      ? [
          { label: 'Retribuzione lorda annua', value: member.gross_annual_pay === null ? '—' : formatMoneyValue(member.gross_annual_pay) },
          { label: 'Mensilità', value: member.monthly_payments ?? '—' },
          { label: 'Part-time', value: typed(member.part_time_percent, '%') },
          { label: 'INPS a carico azienda', value: <Typed>{typed(member.employer_inps_rate, '%')}</Typed> },
          { label: 'INAIL', value: <Typed>{typed(member.employer_inail_rate, '%')}</Typed> },
          { label: 'INPS a carico della persona', value: <Typed>{typed(member.employee_inps_rate, '%')}</Typed> },
        ]
      : [
          { label: 'Compenso annuo', value: member.annual_fee === null ? '—' : formatMoneyValue(member.annual_fee) },
          { label: 'Pagamento', value: member.fee_payment === 'monthly' ? 'Ogni mese' : 'In altro modo' },
          { label: 'Trattamento di fine mandato', value: member.accrues_tfm ? 'Matura' : 'No' },
        ]
  return (
    <Rows
      rows={[
        { label: 'Contratto', value: termsLine(member) },
        ...rows,
        { label: 'Ore al mese', value: member.hours_per_month === null ? '—' : formatItalianQuantity(member.hours_per_month) },
        ...workplaceRow(member, sites),
        { label: 'Giorno di paga', value: payDay },
        { label: 'In servizio', value: `dal ${formatDay(member.start_date)}${member.end_date === null ? '' : ` al ${formatDay(member.end_date)}`}` },
      ]}
    />
  )
}

/** A figure a person typed, not one the product knows. */
function Typed({ children }: { children: ReactNode }) {
  return (
    <span>
      {children} <span className="text-xs font-normal text-muted-foreground">digitata</span>
    </span>
  )
}

/** «Costo orario»: what the hour costs, as the server offers it. Nothing is written until the
 *  person adopts it; a refusal (no user linked, no hours) is shown in the server's words. */
function HourlyCost({ member }: { member: StaffMember }) {
  const proposal = useHourlyCost(member.id)
  const adopt = useAdoptHourlyCost(member.id)
  const data = proposal.data
  return (
    <Section title="Costo orario">
      {data === undefined ? (
        proposal.isError ? (
          <p className="text-sm text-muted-foreground">{toProblem(proposal.error).detail}</p>
        ) : (
          <LoadingState label="Calcolo il costo orario…" className="py-1" />
        )
      ) : (
        <>
          <Rows
            rows={[
              { label: `Costo di ${monthName(data.month.slice(0, 7))}`, value: <Figure value={data.monthly_cost} from="estimate" /> },
              { label: 'Ore al mese', value: formatItalianQuantity(data.hours_per_month) },
              { label: 'Costo orario', value: (
                  <span className="inline-flex flex-col items-end">
                    <span className="tabular-nums">{formatMoneyValue(data.hourly_cost)}/h</span>
                    <span className="text-xs text-muted-foreground underline decoration-dotted underline-offset-2">Stima</span>
                  </span>
                ) },
              {
                label: 'Quello di adesso',
                value: data.current_hourly_cost === null ? '—' : `${formatMoneyValue(data.current_hourly_cost)}/h`,
              },
            ]}
          />
          <p className="text-xs text-muted-foreground">Non viene scritto finché non lo adotti. Le ore già registrate restano come sono.</p>
          {adopt.error === null ? null : (
            <p role="alert" className="text-sm text-destructive">
              {toProblem(adopt.error).detail}
            </p>
          )}
          <Button
            variant="outline"
            disabled={adopt.isPending}
            onClick={() => adopt.mutate(data.hourly_cost, { onSuccess: () => toast.success('Costo orario adottato') })}
          >
            {adopt.isPending ? 'Scrivo…' : 'Adotta il costo orario'}
          </Button>
        </>
      )}
    </Section>
  )
}

/** The panel of one person: the month's figures, the payslip beside the estimate, the terms
 *  (administrators only, which the whole page is), the hourly cost. A closed month shows no
 *  «Accetta» and no edit of the payslip, and says so with a lock. */
export function StaffPanel({
  member,
  month,
  monthKey,
  closed,
  onEdit,
  showEdit,
}: {
  member: StaffMember
  month: MemberMonth | undefined
  monthKey: string
  /** Set when a person closed the month. */
  closed: PayrollClosure | null
  onEdit: () => void
  /** The button sits in the panel on a wide screen; on the phone the sheet's sticky bar carries it. */
  showEdit: boolean
}) {
  const state = stateOf(month, closed !== null)
  const received = month?.received ?? null
  return (
    <div className="space-y-5 p-4">
      <div className="space-y-1">
        <h2 className="break-words text-lg font-semibold">{member.name}</h2>
        <p className="text-sm text-muted-foreground">{termsLine(member)}</p>
        {state === null ? (
          <p className="text-sm text-muted-foreground">Non in servizio in {monthName(monthKey)}.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={STATE_TONES[state]}>
              {state === 'closed' ? <Lock aria-hidden className="mr-1 inline size-3" /> : null}
              {STATE_LABELS[state]}
            </StatusPill>
            <span className="text-sm text-muted-foreground">
              {closed === null
                ? stateLine(month, state)
                : `Chiuso il ${formatDay(closed.closed_at.slice(0, 10))}${closed.closed_by === null ? '' : ` da ${closed.closed_by}`}`}
            </span>
          </div>
        )}
      </div>

      {month === undefined ? null : <MonthFigures month={month} />}
      {month !== undefined && closed === null && month.source === 'estimate' && received !== null ? (
        <PayslipReceived key={`${member.id}:${monthKey}`} month={month} received={received} monthKey={monthKey} />
      ) : null}
      {month !== undefined && closed === null && month.source === 'estimate' && received === null ? (
        <PayslipForm key={`${member.id}:${monthKey}`} member={member} month={month} monthKey={monthKey} />
      ) : null}
      {month !== undefined && month.source === 'payslip' && month.payslip != null ? (
        <PayslipRecorded key={`${member.id}:${monthKey}`} member={member} payslip={month.payslip} monthKey={monthKey} closed={closed !== null} />
      ) : null}
      {month !== undefined && closed !== null && month.source === 'estimate' ? (
        <Section title="Cedolino" tag="Chiuso">
          <p className="text-sm text-muted-foreground">Il mese è chiuso senza un cedolino accettato: le cifre restano la stima.</p>
        </Section>
      ) : null}

      <Section title="Condizioni" tag="Solo amministratori">
        <Terms member={member} />
        {showEdit ? (
          <Button variant="outline" onClick={onEdit}>
            Modifica condizioni
          </Button>
        ) : null}
      </Section>

      <HourlyCost member={member} />
    </div>
  )
}

