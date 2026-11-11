import { useState } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { money } from '@/features/dashboard/format'
import type { DueDay } from './dates'
import { F24_CHIP_LABEL, F24_CHIP_TONE, f24Chip, groupableItems } from './items'
import {
  failureText,
  useAttachReceipt,
  useDetachReceipt,
  useGroupF24,
  useTaxDocuments,
  useUngroupF24,
  type F24Group,
} from './queries'

function Receipts({ group }: { group: F24Group }) {
  const [picked, setPicked] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const documents = useTaxDocuments(true)
  const attach = useAttachReceipt(group.id)
  const detach = useDetachReceipt(group.id)
  const attached = new Set(group.receipts.map((receipt) => receipt.inbox_item_id))
  const candidates = (documents.data?.items ?? []).filter((item) => !attached.has(item.id))
  return (
    <div className="space-y-2">
      <h4 className="text-sm font-medium">Ricevuta</h4>
      {group.receipts.length === 0 ? (
        <p className="text-xs text-muted-foreground">Nessuna ricevuta collegata.</p>
      ) : (
        <ul className="space-y-1">
          {group.receipts.map((receipt) => (
            <li key={receipt.id} className="flex items-center justify-between gap-2 text-sm">
              <a
                className="underline"
                href={`/api/inbox/items/${receipt.inbox_item_id}/file`}
                target="_blank"
                rel="noreferrer"
              >
                {receipt.original_name ?? 'Ricevuta'}
              </a>
              <Button
                variant="outline"
                size="sm"
                disabled={detach.isPending}
                onClick={() => detach.mutate(receipt.inbox_item_id, { onError: (e) => setProblem(failureText(e)) })}
              >
                Scollega
              </Button>
            </li>
          ))}
        </ul>
      )}
      {candidates.length > 0 ? (
        <div className="flex items-center gap-2">
          <Select value={picked} onValueChange={setPicked}>
            <SelectTrigger aria-label="Documento fiscale da collegare" className="min-w-0 flex-1">
              <SelectValue placeholder="Scegli un documento" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.original_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            disabled={picked === '' || attach.isPending}
            onClick={() =>
              attach.mutate(picked, {
                onSuccess: () => setPicked(''),
                onError: (e) => setProblem(failureText(e)),
              })
            }
          >
            Collega
          </Button>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          Carica la ricevuta in «In arrivo» come documento fiscale: poi la colleghi qui.
        </p>
      )}
      {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
    </div>
  )
}

function GroupBlock({ group }: { group: F24Group }) {
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const ungroup = useUngroupF24()
  return (
    <div className="space-y-3">
      <ul>
        {group.lines.map((line) => (
          <li key={line.due_item_id} className="space-y-0.5 border-b py-2 text-sm last:border-0">
            <div className="flex items-baseline justify-between gap-3">
              <span>{line.line ?? line.description ?? 'Voce'}</span>
              <span className="tabular-nums">{money(line.amount)}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {line.code_to_verify ? line.code_label : `Codice ${line.code}`}
              {line.reference_year ? ` · anno ${line.reference_year}` : ''}
              {line.is_credit ? ' · credito' : ''}
            </p>
          </li>
        ))}
      </ul>
      <dl className="space-y-1 text-sm">
        <div className="flex justify-between">
          <dt>Da pagare</dt>
          <dd className="tabular-nums">{money(group.debit_total)}</dd>
        </div>
        <div className="flex justify-between">
          <dt>Crediti</dt>
          <dd className="tabular-nums">{money(group.credit_total)}</dd>
        </div>
        <div className="flex justify-between font-semibold">
          <dt>Netto dell’F24</dt>
          <dd className="tabular-nums">{money(group.total)}</dd>
        </div>
      </dl>
      <Receipts group={group} />
      {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
      {group.state === 'open' ? (
        <div className="flex justify-end gap-2">
          {asking ? (
            <>
              <Button variant="outline" size="sm" onClick={() => setAsking(false)}>
                Non sciogliere
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={ungroup.isPending}
                onClick={() =>
                  ungroup.mutate(group.id, {
                    onError: (e) => {
                      setAsking(false)
                      setProblem(failureText(e))
                    },
                  })
                }
              >
                Sciogli il gruppo
              </Button>
            </>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
              Sciogli l’F24
            </Button>
          )}
        </div>
      ) : null}
    </div>
  )
}

/**
 * A date's F24 (brief, question 3 A): the chip, and either the group's lines with the pack's code
 * of each («codice da verificare» when the pack has none), its net and its receipt, with
 * «Sciogli l’F24» until a payment is on it; or «Raggruppa in un F24» for the items of the date that
 * wait. The net, the debits and the credits are the server's.
 */
export function F24Section({ day }: { day: DueDay }) {
  const [problem, setProblem] = useState<string | null>(null)
  const group = useGroupF24()
  const chip = f24Chip(day)
  const open = groupableItems(day)
  const shown = day.groups.filter((candidate) => candidate.state !== 'dissolved')
  if (chip === null) return null
  return (
    <section className="space-y-3 border-b pb-4" aria-label="F24">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">F24</h3>
        <StatusPill tone={F24_CHIP_TONE[chip]}>{F24_CHIP_LABEL[chip]}</StatusPill>
      </div>
      {shown.map((one) => (
        <GroupBlock key={one.id} group={one} />
      ))}
      {open.length > 0 ? (
        <div className="space-y-2">
          <p className="text-xs text-muted-foreground">
            Le voci di questa data si pagano con un solo F24; i crediti si sottraggono.
          </p>
          {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
          <div className="flex justify-end">
            <Button
              variant="outline"
              size="sm"
              disabled={group.isPending}
              onClick={() => {
                setProblem(null)
                group.mutate(
                  open.map((item) => item.id),
                  { onError: (e) => setProblem(failureText(e)) },
                )
              }}
            >
              Raggruppa in un F24
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  )
}
