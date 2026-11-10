import { Link, useParams } from '@tanstack/react-router'
import { FileSignature, Pencil } from 'lucide-react'
import { useState } from 'react'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { LoadingState } from '@/components/LoadingState'
import { ClauseNotesSection } from '@/features/contracts/ClauseNotesSection'
import { ContractDealLink } from '@/features/contracts/ContractDealLink'
import { ContractForm } from '@/features/contracts/ContractForm'
import { ContractDays } from '@/features/days/ContractDays'
import { ContractEmailSection } from '@/features/contracts/ContractEmailSection'
import { ContractStateActions } from '@/features/contracts/ContractStateActions'
import { DaysToInvoiceSection } from '@/features/contracts/DaysToInvoiceSection'
import { FeeOccurrencesSection } from '@/features/contracts/FeeOccurrencesSection'
import { ExpensesSection } from '@/features/contracts/ExpensesSection'
import {
  EXPENSE_POLICY_LABELS,
  cadenceLabel,
  formatAmount,
  formatCapPercent,
  readExpensePolicy,
  renewalLabel,
  stateLabel,
  stateTone,
} from '@/features/contracts/labels'
import { OwnEngagementCard } from '@/features/contracts/OwnEngagementCard'
import { RateCardsSection } from '@/features/contracts/RateCardsSection'
import { RegisterSection } from '@/features/contracts/RegisterSection'
import { RenewalSection } from '@/features/contracts/RenewalSection'
import { RenewContractButton } from '@/features/contracts/RenewContractDialog'
import { RenewalChain } from '@/features/contracts/RenewalChain'
import { useContract, type Contract } from '@/features/contracts/queries'
import { useOrganization } from '@/features/organizations/queries'
import { formatDate } from '@/features/invoices/format'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { useModules } from '@/lib/modules'

const EMPTY = '—'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b py-2 text-sm last:border-b-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right">{value}</span>
    </div>
  )
}

function paymentTerms(contract: Contract): string {
  if (contract.payment_days === null) return 'Come il cliente'
  return `${contract.payment_days} giorni${contract.payment_end_of_month ? ' fine mese' : ''}`
}

function expensePolicy(contract: Contract): string {
  const policy = readExpensePolicy(contract.expense_policy)
  if (policy === null) return JSON.stringify(contract.expense_policy)
  const parts: string[] = [EXPENSE_POLICY_LABELS[policy.kind]]
  if (policy.kind === 'reimbursable_with_cap' && policy.amount_cap !== undefined) {
    parts.push(`tetto ${formatAmount(policy.amount_cap, contract.currency)}`)
  }
  if (policy.requires_pre_authorization === true) parts.push('autorizzazione preventiva')
  return parts.join(', ')
}

export function ContractDetail() {
  const { contractId } = useParams({ from: '/app/contracts/$contractId' })
  const canEdit = useCan('update_contract')
  const [editing, setEditing] = useState(false)
  const contract = useContract(contractId)
  const customer = useOrganization(contract.data?.customer_id ?? '')
  const modules = useModules()

  if (contract.isLoading) return <LoadingState className="m-8" />
  if (contract.isError) {
    if (toProblem(contract.error).status === 404) return <p className="p-8">Contratto non trovato.</p>
    return (
      <div className="p-8">
        <QueryErrorBanner error={contract.error} />
      </div>
    )
  }
  const row = contract.data
  if (!row) return <p className="p-8">Contratto non trovato.</p>

  return (
    <>
      <EntityDetailLayout
        icon={FileSignature}
        title={row.title}
        subtitle={customer.data?.legal_name}
        entityType="contract"
        entityId={row.id}
        actions={
          <>
            <StatusPill tone={stateTone(row.status)}>{stateLabel(row.status)}</StatusPill>
            <ContractStateActions contract={row} />
            {canEdit && (
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Pencil className="mr-2 size-4" />
                Modifica
              </Button>
            )}
            <RenewContractButton contract={row} />
          </>
        }
        overview={
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="lg:col-span-2 empty:hidden">
              <OwnEngagementCard contractId={row.id} />
            </div>
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Termini</h2>
                <div className="flex justify-between gap-4 border-b py-2 text-sm">
                  <span className="text-muted-foreground">Cliente</span>
                  <Link
                    className="text-right underline underline-offset-4"
                    to="/app/organizations/$organizationId"
                    params={{ organizationId: row.customer_id }}
                  >
                    {customer.data?.legal_name ?? EMPTY}
                  </Link>
                </div>
                <ContractDealLink dealId={row.deal_id} />
                <Row label="Inizio" value={formatDate(row.start)} />
                <Row label="Fine" value={row.end === null ? 'Senza scadenza' : formatDate(row.end)} />
                <Row label="Rinnovo" value={renewalLabel(row.renewal_type)} />
                <Row
                  label="Preavviso di rinnovo"
                  value={
                    row.renewal_notice_days === null
                      ? EMPTY
                      : `${row.renewal_notice_days} giorni`
                  }
                />
                <Row label="Preavviso di disdetta" value={`${row.termination_notice_days} giorni`} />
                <RenewalChain contract={row} />
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Fatturazione</h2>
                <Row label="Cadenza" value={cadenceLabel(row.billing_cadence)} />
                <Row label="Termini di pagamento" value={paymentTerms(row)} />
                <Row label="Divisa" value={row.currency} />
                <Row
                  label="Approvazione preventiva"
                  value={row.requires_prior_approval ? 'Richiesta' : 'Non richiesta'}
                />
                <Row label="Rivalsa INPS" value={row.applies_social_charge ? 'Applicata' : 'No'} />
                <Row label="Spese" value={expensePolicy(row)} />
                <Row
                  label="Tetto sulla quota"
                  value={row.customer_share_cap === null ? 'Nessuno' : formatCapPercent(row.customer_share_cap)}
                />
              </CardContent>
            </Card>

            {row.note && (
              <Card className="lg:col-span-2">
                <CardContent className="pt-6">
                  <h2 className="mb-3 font-semibold">Note</h2>
                  <p className="whitespace-pre-wrap text-sm">{row.note}</p>
                </CardContent>
              </Card>
            )}

            <div className="lg:col-span-2">
              <RateCardsSection contractId={row.id} currency={row.currency} />
            </div>
            {/* A card of its own in the grid, not a wrapper around one: a contract with no
                recurring fee draws nothing, and an empty grid item would still take its gap. */}
            <FeeOccurrencesSection contractId={row.id} currency={row.currency} />
            <div className="lg:col-span-2">
              <ContractDays
                contractId={row.id}
                customerId={row.customer_id}
                requiresPriorApproval={row.requires_prior_approval}
              />
            </div>
            {/* The expenses pick their category from the cost categories: a surface of the
                contracts and of the purchases, drawn only when both are on (REB-953). */}
            {modules.on('purchases') && (
              <div className="lg:col-span-2">
                <ExpensesSection contractId={row.id} currency={row.currency} />
              </div>
            )}
            <div className="lg:col-span-2">
              <DaysToInvoiceSection contractId={row.id} currency={row.currency} />
            </div>
            <div className="lg:col-span-2">
              <RegisterSection contractId={row.id} />
            </div>
            <div className="lg:col-span-2">
              <ContractEmailSection contractId={row.id} customerId={row.customer_id} />
            </div>
            <div className="lg:col-span-2">
              <ClauseNotesSection contractId={row.id} />
            </div>
            <div className="lg:col-span-2">
              <RenewalSection contract={row} />
            </div>
          </div>
        }
      />

      <ContractForm open={editing} onOpenChange={setEditing} contract={row} />
    </>
  )
}
