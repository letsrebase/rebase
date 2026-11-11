import { useState } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { moneyIn } from '@/features/cash/money'
import { money } from '@/features/dashboard/format'
import { formatIsoDateItalian } from '@/lib/dates'
import { TAX_ITEM_STATE_LABEL, TAX_ITEM_STATE_TONE, itemTitle, taxItemState } from './items'
import { InstalmentPlanDialog } from './InstalmentPlanDialog'
import { TAX_LABELS, type Tax } from './labels'
import { NoticePlanDialog } from './NoticePlanDialog'
import type { PlanRow } from './plans'
import { failureText, useDeleteNoticePlan, useSetInstalmentPlan, type TaxDueItem } from './queries'

function Line({ children }: { children: React.ReactNode }) {
  return <li className="space-y-1 border-b py-2 text-sm last:border-0">{children}</li>
}

function ItemLine({ item, today }: { item: TaxDueItem; today: string }) {
  const state = taxItemState(item, today)
  return (
    <Line>
      <div className="flex items-baseline justify-between gap-3">
        <span>{item.due_date ? formatIsoDateItalian(item.due_date) : ''}</span>
        <span className="tabular-nums">{moneyIn(item.amount, item.currency)}</span>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">{itemTitle(item)}</span>
        <StatusPill tone={TAX_ITEM_STATE_TONE[state]}>{TAX_ITEM_STATE_LABEL[state]}</StatusPill>
      </div>
    </Line>
  )
}

/**
 * A plan's detail: what it is, each instalment with its day, its amount (the server's principal
 * and interest, and their sum) and its state, and the actions the plan allows. The server refuses
 * a change or a removal once a payment sits on one of the instalments and says so; the panel shows
 * its sentence.
 */
export function PlanPanel({
  row,
  legalEntityId,
  today,
  onClose,
}: {
  row: PlanRow
  legalEntityId: string
  today: string
  onClose: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const remove = useDeleteNoticePlan()
  const clear = useSetInstalmentPlan(legalEntityId)
  const plan = row.plan
  const tax = TAX_LABELS[plan.tax as Tax] ?? plan.tax

  return (
    <div className="space-y-5 overflow-y-auto p-4">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold">{row.title}</h2>
        <p className="text-sm text-muted-foreground">
          {row.kind === 'notice'
            ? `Avviso dell’Agenzia delle Entrate · ${tax}`
            : `Il saldo di giugno e il primo acconto dell’anno dopo, in ${row.plan.instalments} rate mensili · ${tax}`}
        </p>
      </div>
      {row.kind === 'notice' ? (
        <section className="space-y-1">
          <h3 className="text-sm font-semibold">Importo dell’avviso</h3>
          <p className="text-sm tabular-nums">{money(row.plan.total)}</p>
          <p className="text-xs text-muted-foreground">Senza interessi: ogni rata aggiunge i suoi.</p>
        </section>
      ) : null}
      <section className="space-y-1">
        <h3 className="text-sm font-semibold">Rate</h3>
        <ul>
          {row.kind === 'notice'
            ? row.plan.instalments.map((entry) => {
                const item = row.items.find((candidate) =>
                  (candidate.description ?? '').startsWith(`${row.plan.label}, rata ${entry.number} di `),
                )
                const state = item ? taxItemState(item, today) : null
                return (
                  <Line key={entry.number}>
                    <div className="flex items-baseline justify-between gap-3">
                      <span>
                        {entry.number}. {formatIsoDateItalian(entry.due_date)}
                      </span>
                      <span className="tabular-nums">{money(entry.amount)}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-xs text-muted-foreground">
                        Quota {money(entry.principal)}, interessi {money(entry.interest)}
                      </span>
                      {state ? (
                        <StatusPill tone={TAX_ITEM_STATE_TONE[state]}>{TAX_ITEM_STATE_LABEL[state]}</StatusPill>
                      ) : null}
                    </div>
                  </Line>
                )
              })
            : row.items.map((item) => <ItemLine key={item.id} item={item} today={today} />)}
        </ul>
        {row.kind === 'instalment' && row.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Le rate compaiono qui quando le scadenze sono scritte.
          </p>
        ) : null}
      </section>
      {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
      <div className="flex flex-wrap justify-end gap-2">
        {row.kind === 'notice' ? (
          asking ? (
            <>
              <Button variant="outline" size="sm" onClick={() => setAsking(false)}>
                Non eliminare
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(row.plan.id, {
                    onSuccess: onClose,
                    onError: (error) => {
                      setAsking(false)
                      setProblem(failureText(error))
                    },
                  })
                }
              >
                Elimina il piano
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
                Elimina
              </Button>
              <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                Modifica
              </Button>
            </>
          )
        ) : (
          <>
            <Button
              variant="outline"
              size="sm"
              disabled={clear.isPending}
              onClick={() => {
                setProblem(null)
                clear.mutate(
                  {
                    year: row.plan.tax_year,
                    tax: row.plan.tax as 'irpef' | 'substitute_tax' | 'ires' | 'irap',
                    instalments: 1,
                  },
                  { onSuccess: onClose, onError: (error) => setProblem(failureText(error)) },
                )
              }}
            >
              Togli la rateazione
            </Button>
            <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
              Cambia le rate
            </Button>
          </>
        )}
      </div>
      {editing && row.kind === 'notice' ? (
        <NoticePlanDialog legalEntityId={legalEntityId} plan={row.plan} onClose={() => setEditing(false)} />
      ) : null}
      {editing && row.kind === 'instalment' ? (
        <InstalmentPlanDialog
          legalEntityId={legalEntityId}
          year={row.plan.tax_year}
          tax={row.plan.tax as Tax}
          current={row.plan.instalments}
          onClose={() => setEditing(false)}
        />
      ) : null}
    </div>
  )
}
