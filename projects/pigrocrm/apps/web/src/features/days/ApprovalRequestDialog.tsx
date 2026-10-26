import { Link } from '@tanstack/react-router'
import { Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { Textarea } from '@rebase/ui/textarea'
import { useCustomer } from '@/features/customers/queries'
import { useGmailConfigured } from '@/features/gmail/queries'
import { useSendDraft } from '@/features/gmail/draftQueries'
import { toProblem } from '@/lib/api'
import { toIsoDate } from '@/lib/dates'
import { FULL_DAY, HALF_DAY } from './labels'
import {
  usePrepareRequest,
  usePreviewRequest,
  type ApprovalRequestDay,
  type ApprovalRequestPrepared,
} from './requestQueries'

interface Row {
  key: number
  data: string
  quantita: string
  descrizione: string
}

let nextKey = 1

function emptyRow(): Row {
  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)
  return { key: nextKey++, data: toIsoDate(tomorrow), quantita: FULL_DAY, descrizione: '' }
}

function toDays(rows: readonly Row[]): ApprovalRequestDay[] {
  return rows.map((row) => ({ data: row.data, quantita: row.quantita, descrizione: row.descrizione.trim() }))
}

/** Addresses typed in one field, separated by a comma, a semicolon or a space. */
function splitAddresses(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((address) => address.trim())
    .filter((address) => address !== '')
}

/**
 * Asks the client to approve named days before they are worked, from the contract (REB-749):
 * pick the dates, read the request, send it from the person's own Gmail, and the days are
 * recorded as proposed.
 *
 * Three steps and one rule: nothing leaves until the person has read the text they can
 * edit. Sending is two calls on purpose. «Invia» first prepares the request (the days as
 * `proposto` and the message as a draft of the client, one transaction) and then sends that
 * draft through the ordinary send route, so a send that fails leaves the draft, the days and
 * a sentence about why, and pressing «Riprova l’invio» sends the same draft and never a
 * second one. An outcome nobody can know (`incerto`) is not retried here: it is resolved from
 * the client's Email tab by asking Gmail.
 */
export function ApprovalRequestDialog({
  open,
  onOpenChange,
  contractId,
  customerId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  contractId: string
  customerId: string
}) {
  const gmail = useGmailConfigured()
  const customer = useCustomer(customerId, { enabled: open })
  const preview = usePreviewRequest(contractId)
  const prepare = usePrepareRequest(contractId)
  const send = useSendDraft()

  const [step, setStep] = useState<'dates' | 'text' | 'done'>('dates')
  const [rows, setRows] = useState<Row[]>(() => [emptyRow()])
  const [to, setTo] = useState<string | null>(null)
  const [oggetto, setOggetto] = useState('')
  const [corpo, setCorpo] = useState('')
  const [prepared, setPrepared] = useState<ApprovalRequestPrepared | null>(null)
  const [sendFailure, setSendFailure] = useState<string | null>(null)

  const recipients = to ?? customer.data?.email ?? ''
  const datesReady = rows.every((row) => row.data !== '' && row.descrizione.trim() !== '')
  const duplicated = new Set(rows.map((row) => row.data)).size !== rows.length

  function reset() {
    setStep('dates')
    setRows([emptyRow()])
    setTo(null)
    setPrepared(null)
    setSendFailure(null)
    preview.reset()
    prepare.reset()
    send.reset()
  }

  function close(next: boolean) {
    if (prepare.isPending || send.isPending) return
    if (!next) reset()
    onOpenChange(next)
  }

  function update(key: number, patch: Partial<Row>) {
    setRows((previous) => previous.map((row) => (row.key === key ? { ...row, ...patch } : row)))
  }

  function read() {
    preview.mutate(toDays(rows), {
      onSuccess: (message) => {
        setOggetto(message.oggetto)
        setCorpo(message.corpo)
        setStep('text')
      },
    })
  }

  async function deliver(draft: ApprovalRequestPrepared['draft']) {
    setSendFailure(null)
    // The days are recorded and the draft exists whatever happens next, so the outcome, good
    // or bad, is the last step: a failure is told there, beside the way to send again.
    setStep('done')
    try {
      const sent = await send.mutateAsync(draft)
      if (sent.send_state === 'inviato') {
        toast.success('Richiesta inviata')
        return
      }
      setSendFailure(
        sent.send_state === 'incerto'
          ? 'Non sappiamo se la richiesta è partita. Controlla dall’Email del cliente prima di rimandarla.'
          : (sent.last_error ?? 'La richiesta non è partita.'),
      )
    } catch (error) {
      setSendFailure(toProblem(error).detail)
    }
  }

  async function submit() {
    try {
      const result = await prepare.mutateAsync({
        days: toDays(rows),
        to_addresses: splitAddresses(recipients),
        cc_addresses: [],
        oggetto: oggetto.trim(),
        corpo,
      })
      setPrepared(result)
      if (gmail) await deliver(result.draft)
      else setStep('done')
    } catch {
      // Shown below from the mutation's own error: nothing was recorded.
    }
  }

  const days = prepared?.work_units.length ?? 0

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Chiedi l’approvazione al cliente</DialogTitle>
          <DialogDescription>
            {step === 'dates'
              ? 'Indica le date per cui chiedi l’approvazione preventiva.'
              : step === 'text'
                ? 'Leggi la richiesta: parte esattamente quello che vedi.'
                : sendFailure === null && gmail
                  ? 'La richiesta è partita dal tuo indirizzo.'
                  : 'La richiesta è pronta.'}
          </DialogDescription>
        </DialogHeader>

        {step === 'dates' ? (
          <div className="space-y-4">
            <ul className="space-y-3">
              {rows.map((row, index) => (
                <li key={row.key} className="grid gap-2 border p-3 sm:grid-cols-[10rem_9rem_1fr_auto] sm:items-end">
                  <div className="space-y-1">
                    <Label htmlFor={`req-date-${row.key}`}>Data {index + 1}</Label>
                    <Input
                      id={`req-date-${row.key}`}
                      type="date"
                      value={row.data}
                      onChange={(event) => update(row.key, { data: event.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`req-part-${row.key}`}>Quanto</Label>
                    <Select value={row.quantita} onValueChange={(value) => update(row.key, { quantita: value })}>
                      <SelectTrigger id={`req-part-${row.key}`} className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={FULL_DAY}>Giornata intera</SelectItem>
                        <SelectItem value={HALF_DAY}>Mezza giornata</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`req-task-${row.key}`}>Cosa farai</Label>
                    <Input
                      id={`req-task-${row.key}`}
                      value={row.descrizione}
                      onChange={(event) => update(row.key, { descrizione: event.target.value })}
                      placeholder="Workshop con il cliente"
                    />
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Togli la data ${index + 1}`}
                    disabled={rows.length === 1}
                    onClick={() => setRows((previous) => previous.filter((candidate) => candidate.key !== row.key))}
                  >
                    <Trash2 className="size-4" aria-hidden />
                  </Button>
                </li>
              ))}
            </ul>
            <Button type="button" variant="outline" size="sm" onClick={() => setRows((previous) => [...previous, emptyRow()])}>
              <Plus className="mr-2 size-4" aria-hidden />
              Aggiungi una data
            </Button>
            {duplicated ? <p className="text-sm text-destructive">Una data è indicata due volte.</p> : null}
            {preview.isError ? <QueryErrorBanner error={preview.error} /> : null}
          </div>
        ) : null}

        {step === 'text' ? (
          <div className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="req-to">A</Label>
              <Input
                id="req-to"
                value={recipients}
                onChange={(event) => setTo(event.target.value)}
                placeholder="amministrazione@cliente.it"
              />
              {customer.isSuccess && !customer.data.email ? (
                <p className="text-sm text-muted-foreground">Il cliente non ha un indirizzo: scrivilo qui.</p>
              ) : null}
            </div>
            <div className="space-y-1">
              <Label htmlFor="req-subject">Oggetto</Label>
              <Input id="req-subject" value={oggetto} onChange={(event) => setOggetto(event.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="req-body">Messaggio</Label>
              <Textarea id="req-body" rows={12} value={corpo} onChange={(event) => setCorpo(event.target.value)} />
            </div>
            {gmail ? (
              <p className="text-sm text-muted-foreground">
                Parte dal tuo Gmail e le {rows.length === 1 ? 'giornata chiesta si registra' : `${rows.length} giornate chieste si registrano`}{' '}
                come proposte sul contratto.
              </p>
            ) : (
              <p className="border border-muted bg-muted/40 px-3 py-2 text-sm">
                La posta non è collegata (Impostazioni, Gmail): la richiesta si prepara come bozza del cliente e le
                giornate si registrano come proposte. La invii dopo, dall’Email del cliente.
              </p>
            )}
            {prepare.isError ? <QueryErrorBanner error={prepare.error} /> : null}
          </div>
        ) : null}

        {step === 'done' ? (
          <div className="space-y-3 text-sm">
            <p>
              {days === 1 ? 'La giornata è registrata come proposta' : `Le ${days} giornate sono registrate come proposte`} sul
              contratto
              {gmail && sendFailure === null ? ', in attesa della risposta del cliente.' : '.'}
            </p>
            {sendFailure !== null ? (
              <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-destructive">
                {sendFailure}
              </p>
            ) : null}
            {gmail && sendFailure !== null && prepared !== null && !sendFailure.startsWith('Non sappiamo') ? (
              <Button type="button" onClick={() => void deliver(prepared.draft)} disabled={send.isPending}>
                {send.isPending ? <Loader className="mr-2 size-4" /> : null}
                Riprova l’invio
              </Button>
            ) : null}
            <p>
              <Link
                to="/app/customers/$customerId"
                params={{ customerId }}
                className="underline underline-offset-4"
              >
                Apri il cliente
              </Link>{' '}
              per vedere la richiesta nella sua Email.
            </p>
          </div>
        ) : null}

        <DialogFooter>
          {step === 'dates' ? (
            <>
              <Button variant="outline" onClick={() => close(false)}>
                Annulla
              </Button>
              <Button onClick={read} disabled={!datesReady || duplicated || preview.isPending}>
                {preview.isPending ? <Loader className="mr-2 size-4" /> : null}
                Leggi la richiesta
              </Button>
            </>
          ) : null}
          {step === 'text' ? (
            <>
              <Button variant="outline" onClick={() => setStep('dates')} disabled={prepare.isPending || send.isPending}>
                Indietro
              </Button>
              <Button
                onClick={() => void submit()}
                disabled={
                  prepare.isPending || send.isPending || splitAddresses(recipients).length === 0 || oggetto.trim() === '' || corpo.trim() === ''
                }
              >
                {prepare.isPending || send.isPending ? <Loader className="mr-2 size-4" /> : null}
                {gmail ? 'Invia la richiesta' : 'Prepara la bozza'}
              </Button>
            </>
          ) : null}
          {step === 'done' ? <Button onClick={() => close(false)}>Chiudi</Button> : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
