import { createFileRoute, Link, useParams } from '@tanstack/react-router'
import { FileSignature, Pencil } from 'lucide-react'
import { useState } from 'react'
import { EntityDetailLayout } from '@/components/EntityDetailLayout'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Card, CardContent } from '@rebase/ui/card'
import { Skeleton } from '@rebase/ui/skeleton'
import { ClauseNotesSection } from '@/features/contracts/ClauseNotesSection'
import { ContractForm } from '@/features/contracts/ContractForm'
import { ContractDays } from '@/features/days/ContractDays'
import { ContractEmailSection } from '@/features/contracts/ContractEmailSection'
import { ContractStateActions } from '@/features/contracts/ContractStateActions'
import { DaysToInvoiceSection } from '@/features/contracts/DaysToInvoiceSection'
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
import { RateCardsSection } from '@/features/contracts/RateCardsSection'
import { RegisterSection } from '@/features/contracts/RegisterSection'
import { RenewalSection } from '@/features/contracts/RenewalSection'
import { RenewContractButton } from '@/features/contracts/RenewContractDialog'
import { RenewalChain } from '@/features/contracts/RenewalChain'
import { useContract, type Contract } from '@/features/contracts/queries'
import { useCustomer } from '@/features/customers/queries'
import { formatDate } from '@/features/invoices/format'
import { toProblem } from '@/lib/api'
import { useCan } from '@/lib/auth'

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
  if (contract.giorni_pagamento === null) return 'Come il cliente'
  return `${contract.giorni_pagamento} giorni${contract.pagamento_fine_mese ? ' fine mese' : ''}`
}

function expensePolicy(contract: Contract): string {
  const policy = readExpensePolicy(contract.politica_spese)
  if (policy === null) return JSON.stringify(contract.politica_spese)
  const parts: string[] = [EXPENSE_POLICY_LABELS[policy.kind]]
  if (policy.kind === 'rimborsabile_con_tetto' && policy.importo_tetto !== undefined) {
    parts.push(`tetto ${formatAmount(policy.importo_tetto, contract.divisa)}`)
  }
  if (policy.richiede_preautorizzazione === true) parts.push('autorizzazione preventiva')
  return parts.join(', ')
}

export function ContractDetail() {
  const { contractId } = useParams({ from: '/app/contracts/$contractId' })
  const canEdit = useCan('update_contract')
  const [editing, setEditing] = useState(false)
  const contract = useContract(contractId)
  const customer = useCustomer(contract.data?.customer_id ?? '')

  if (contract.isLoading) return <Skeleton className="m-8 h-96" />
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
        title={row.titolo}
        subtitle={customer.data?.ragione_sociale}
        entityType="contract"
        entityId={row.id}
        actions={
          <>
            <StatusPill tone={stateTone(row.stato)}>{stateLabel(row.stato)}</StatusPill>
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
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Termini</h2>
                <div className="flex justify-between gap-4 border-b py-2 text-sm">
                  <span className="text-muted-foreground">Cliente</span>
                  <Link
                    className="text-right underline underline-offset-4"
                    to="/app/customers/$customerId"
                    params={{ customerId: row.customer_id }}
                  >
                    {customer.data?.ragione_sociale ?? EMPTY}
                  </Link>
                </div>
                <Row label="Inizio" value={formatDate(row.inizio)} />
                <Row label="Fine" value={row.fine === null ? 'Senza scadenza' : formatDate(row.fine)} />
                <Row label="Rinnovo" value={renewalLabel(row.tipo_rinnovo)} />
                <Row
                  label="Preavviso di rinnovo"
                  value={
                    row.preavviso_rinnovo_giorni === null
                      ? EMPTY
                      : `${row.preavviso_rinnovo_giorni} giorni`
                  }
                />
                <Row label="Preavviso di disdetta" value={`${row.preavviso_disdetta_giorni} giorni`} />
                <RenewalChain contract={row} />
              </CardContent>
            </Card>

            <Card>
              <CardContent className="pt-6">
                <h2 className="mb-3 font-semibold">Fatturazione</h2>
                <Row label="Cadenza" value={cadenceLabel(row.cadenza_fatturazione)} />
                <Row label="Termini di pagamento" value={paymentTerms(row)} />
                <Row label="Divisa" value={row.divisa} />
                <Row
                  label="Approvazione preventiva"
                  value={row.requires_prior_approval ? 'Richiesta' : 'Non richiesta'}
                />
                <Row label="Rivalsa INPS" value={row.applies_social_charge ? 'Applicata' : 'No'} />
                <Row label="Spese" value={expensePolicy(row)} />
                <Row
                  label="Tetto sulla quota"
                  value={row.tetto_quota_cliente === null ? 'Nessuno' : formatCapPercent(row.tetto_quota_cliente)}
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
              <RateCardsSection contractId={row.id} divisa={row.divisa} />
            </div>
            <div className="lg:col-span-2">
              <ContractDays
                contractId={row.id}
                customerId={row.customer_id}
                requiresPriorApproval={row.requires_prior_approval}
              />
            </div>
            <div className="lg:col-span-2">
              <ExpensesSection contractId={row.id} divisa={row.divisa} />
            </div>
            <div className="lg:col-span-2">
              <DaysToInvoiceSection contractId={row.id} divisa={row.divisa} />
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

export const Route = createFileRoute('/app/contracts/$contractId')({ component: ContractDetail })
