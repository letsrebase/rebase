import { useState } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { money } from '@/features/dashboard/format'
import { isZero } from '@/features/cash/schedule/labels'
import { formatIsoDateItalian } from '@/lib/dates'
import type { DueDay } from './dates'
import { F24_CHIP_LABEL, F24_CHIP_TONE, f24Chip, groupableItems, nothingToPay } from './items'
import {
  failureText,
  useAttachReceipt,
  useDetachReceipt,
  useFileF24,
  useGroupF24,
  useReopenF24,
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

/**
 * «Segna come presentato» (REB-1093): an F24 that nets to zero or to a credit moves no money, so no
 * bank movement settles it; it is still presented through the tax agency's telematic services,
 * and a person says so here, with the day and, if there is one, the receipt. The server
 * pays the lines and spends the credits.
 */
function FilingBlock({ group, today }: { group: F24Group; today: string }) {
  const [open, setOpen] = useState(false)
  const [filedOn, setFiledOn] = useState(today)
  const [receipt, setReceipt] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const documents = useTaxDocuments(open)
  const file = useFileF24()
  const attached = new Set(group.receipts.map((one) => one.inbox_item_id))
  const candidates = (documents.data?.items ?? []).filter((item) => !attached.has(item.id))
  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-sm">
        Questo F24 non ha nulla da pagare, quindi nessun movimento in banca lo salda. Va comunque
        presentato con i servizi telematici dell’Agenzia delle entrate: quando l’hai fatto, segnalo qui.
      </p>
      {open ? (
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor={`f24-filed-${group.id}`}>Presentato il</Label>
            <Input
              id={`f24-filed-${group.id}`}
              type="date"
              value={filedOn}
              max={today}
              onChange={(event) => setFiledOn(event.target.value)}
            />
          </div>
          {candidates.length > 0 ? (
            <div className="space-y-1">
              <Label htmlFor={`f24-receipt-${group.id}`}>Ricevuta (facoltativa)</Label>
              <Select value={receipt} onValueChange={setReceipt}>
                <SelectTrigger id={`f24-receipt-${group.id}`} className="w-full">
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
            </div>
          ) : null}
          {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
              Non ancora
            </Button>
            <Button
              size="sm"
              disabled={filedOn === '' || file.isPending}
              onClick={() => {
                setProblem(null)
                file.mutate(
                  { groupId: group.id, filedOn, receiptInboxItemId: receipt === '' ? null : receipt },
                  { onError: (e) => setProblem(failureText(e)) },
                )
              }}
            >
              Conferma
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex justify-end">
          <Button size="sm" onClick={() => setOpen(true)}>
            Segna come presentato
          </Button>
        </div>
      )}
    </div>
  )
}

/** A filed F24 says when, and what is left of its credits; a person can take the filing back. */
function FiledBlock({ group }: { group: F24Group }) {
  const [asking, setAsking] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const reopen = useReopenF24()
  return (
    <div className="space-y-2">
      <p className="text-sm">
        Presentato il {group.settled_on ? formatIsoDateItalian(group.settled_on) : ''}: le voci sono
        pagate e i crediti usati.
      </p>
      {group.credit_left && !isZero(group.credit_left) ? (
        <p className="text-sm">
          Resta un credito di <span className="tabular-nums">{money(group.credit_left)}</span>, ancora
          disponibile per un altro F24.
        </p>
      ) : null}
      {problem ? <p role="alert" className="text-sm text-destructive">{problem}</p> : null}
      <div className="flex justify-end gap-2">
        {asking ? (
          <>
            <Button variant="outline" size="sm" onClick={() => setAsking(false)}>
              Non riaprire
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={reopen.isPending}
              onClick={() =>
                reopen.mutate(group.id, {
                  onError: (e) => {
                    setAsking(false)
                    setProblem(failureText(e))
                  },
                })
              }
            >
              Riapri il gruppo
            </Button>
          </>
        ) : (
          <Button variant="outline" size="sm" onClick={() => setAsking(true)}>
            Riapri l’F24
          </Button>
        )}
      </div>
    </div>
  )
}

function GroupBlock({ group, today }: { group: F24Group; today: string }) {
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
              {line.is_credit && group.state === 'settled' && line.used_amount !== null
                ? ` · usato ${money(line.used_amount)}`
                : ''}
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
      {group.state === 'open' && nothingToPay(group) ? <FilingBlock group={group} today={today} /> : null}
      {group.state === 'settled' && group.settled_by === 'filing' ? <FiledBlock group={group} /> : null}
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
 * «Sciogli l’F24» until a payment is on it; «Segna come presentato» for one with nothing to pay
 * (REB-1093); or «Raggruppa in un F24» for the items of the date that wait. The net, the debits and
 * the credits are the server's.
 */
export function F24Section({ day, today }: { day: DueDay; today: string }) {
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
        <GroupBlock key={one.id} group={one} today={today} />
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
