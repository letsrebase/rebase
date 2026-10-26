import { Link, useNavigate } from '@tanstack/react-router'
import { FileCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { toast } from '@rebase/ui/sonner'
import { Textarea } from '@rebase/ui/textarea'
import { formatOccurredAt } from '@/components/activityLabels'
import { useGmailConfigured, useGmailMessages } from '@/features/gmail/queries'
import { toProblem } from '@/lib/api'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { cn } from '@rebase/ui/cn'
import {
  CANALE_IDS,
  CANALI,
  canReceiveApproval,
  excerptIsInBody,
  toDateTimeLocal,
  type ApprovalCanale,
  type GmailMessage,
} from './approvals'
import { useFileProof, useRecordApproval } from './approvalQueries'
import { quantitaLabel, statoLabel } from './labels'
import { useDayContracts, useWorkUnits } from './queries'

type ProofKind = 'messaggio' | 'file' | 'testo'

const PROOFS: { id: ProofKind; label: string }[] = [
  { id: 'messaggio', label: 'Un messaggio sincronizzato' },
  { id: 'file', label: 'Un file' },
  { id: 'testo', label: 'Testo incollato' },
]

/**
 * `/app/approvals/new`: record a client's approval with its proof, and the days it covers
 * (REB-747). The same form mastro had: the channel, who sent it, when it arrived, the
 * sentence that approves, verbatim, and the proof as a message already synced from the
 * client's mailbox, a file, or pasted text.
 *
 * Recording moves the days the approval names: a proposed day becomes approved, a day
 * worked without the approval its contract requires is recovered to worked. Only days that
 * can use an approval are offered, and the API refuses the whole recording if one cannot,
 * naming it. An approval is immutable evidence: a correction is a second approval.
 */
export function ApprovalForm({
  initialContractId,
  initialDayIds,
}: {
  initialContractId?: string
  initialDayIds: readonly string[]
}) {
  const navigate = useNavigate()
  const canWrite = useCanWrite()
  const contracts = useDayContracts()
  const gmail = useGmailConfigured()
  const fileProof = useFileProof()
  const record = useRecordApproval()

  const [contractId, setContractId] = useState(initialContractId ?? '')
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set(initialDayIds))
  const [canale, setCanale] = useState<ApprovalCanale>('email')
  const [mittente, setMittente] = useState('')
  const [ricevutoIl, setRicevutoIl] = useState(toDateTimeLocal(new Date()))
  const [estratto, setEstratto] = useState('')
  // Until the person picks one, the proof is the first the installation can offer: the mailbox's
  // health is read after the first render, so the default is derived and never frozen at it.
  const [chosenProof, setProof] = useState<ProofKind | null>(null)
  const proof: ProofKind = chosenProof ?? (gmail ? 'messaggio' : 'file')
  const [messageId, setMessageId] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [testo, setTesto] = useState('')

  const options = contracts.data ?? []
  const contract = options.find((candidate) => candidate.id === contractId)
  const candidates = useWorkUnits({ contractId }, { enabled: contractId !== '' })
  const days = (candidates.data?.items ?? []).filter(canReceiveApproval)
  const messages = useGmailMessages({ entityType: 'customer', entityId: contract?.customer_id ?? '' })
  const inbound = (messages.data ?? []).filter((message) => message.direction === 'inbound')
  const chosenMessage = inbound.find((message) => message.id === messageId)

  if (!canWrite) {
    return (
      <>
        <PageHeader icon={FileCheck} title="Registra un’approvazione" />
        <p className="px-8 py-6 text-sm text-muted-foreground">
          Con il tuo ruolo puoi leggere le approvazioni ma non registrarne.
        </p>
      </>
    )
  }

  function chooseMessage(message: GmailMessage) {
    setMessageId(message.id)
    // The message knows who sent it and when: offered, and still the person's to change.
    setMittente(message.from_address)
    setRicevutoIl(toDateTimeLocal(new Date(message.internal_date)))
    setCanale('email')
  }

  function toggle(id: string, on: boolean) {
    setPicked((previous) => {
      const next = new Set(previous)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const chosenDays = days.filter((day) => picked.has(day.id))
  const proofReady =
    (proof === 'messaggio' && chosenMessage !== undefined) ||
    (proof === 'file' && file !== null) ||
    (proof === 'testo' && testo.trim() !== '')
  const ready =
    contractId !== '' &&
    chosenDays.length > 0 &&
    mittente.trim() !== '' &&
    ricevutoIl !== '' &&
    estratto.trim() !== '' &&
    proofReady
  const busy = fileProof.isPending || record.isPending
  const mismatch =
    proof === 'messaggio' && chosenMessage !== undefined && !excerptIsInBody(estratto, chosenMessage.body_text)
  const failure = fileProof.error ?? record.error

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!ready || busy) return
    fileProof.reset()
    record.reset()
    const base = {
      contract_id: contractId,
      canale,
      mittente: mittente.trim(),
      ricevuto_il: new Date(ricevutoIl).toISOString(),
      estratto: estratto.trim(),
      work_unit_ids: chosenDays.map((day) => day.id),
    }
    try {
      let body
      if (proof === 'messaggio') {
        body = { ...base, gmail_message_id: messageId }
      } else {
        const upload =
          proof === 'file' && file !== null
            ? file
            : new File([testo], 'approvazione.txt', { type: 'text/plain' })
        const document = await fileProof.mutateAsync({
          contractId,
          titolo: proof === 'file' && file !== null ? file.name : `Approvazione di ${mittente.trim()}`,
          file: upload,
        })
        body = { ...base, document_id: document.id }
      }
      const recorded = await record.mutateAsync(body)
      toast.success(
        recorded.work_units.length === 1
          ? 'Approvazione registrata: 1 giornata'
          : `Approvazione registrata: ${recorded.work_units.length} giornate`,
      )
      const first = recorded.work_units[0]
      if (recorded.work_units.length === 1 && first !== undefined) {
        void navigate({ to: '/app/days/$workUnitId', params: { workUnitId: first.id } })
      } else {
        void navigate({ to: '/app/days', search: { contract: contractId } })
      }
    } catch {
      // Shown by the banner below from the mutation's own error; nothing was recorded.
    }
  }

  return (
    <>
      <PageHeader
        icon={FileCheck}
        title="Registra un’approvazione"
        description="Quello che il cliente ha approvato, con la prova, e le giornate che copre."
      />
      <form onSubmit={(event) => void submit(event)} className="max-w-2xl space-y-6 px-4 py-6 sm:px-8" noValidate>
        {contracts.isError ? <QueryErrorBanner error={contracts.error} /> : null}

        <div className="space-y-2">
          <Label htmlFor="approval-contract">Contratto</Label>
          <Select
            value={contractId}
            onValueChange={(next) => {
              setContractId(next)
              setPicked(new Set())
              setMessageId('')
            }}
            disabled={contracts.isPending}
          >
            <SelectTrigger id="approval-contract" className="w-full">
              <SelectValue placeholder={contracts.isPending ? 'Caricamento…' : 'Scegli il contratto'} />
            </SelectTrigger>
            <SelectContent>
              {options.map((candidate) => (
                <SelectItem key={candidate.id} value={candidate.id}>
                  {candidate.titolo}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <fieldset className="space-y-2" disabled={contractId === ''}>
          <legend className="text-sm font-medium">Giornate che copre</legend>
          {contractId === '' ? (
            <p className="text-sm text-muted-foreground">Scegli prima il contratto.</p>
          ) : candidates.isError ? (
            <QueryErrorBanner error={candidates.error} />
          ) : candidates.isPending ? (
            <p className="text-sm text-muted-foreground">Cerco le giornate…</p>
          ) : days.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nessuna giornata di questo contratto può ricevere un’approvazione: ci vogliono giornate proposte
              o lavorate senza approvazione.{' '}
              <Link to="/app/days/new" search={{ contract: contractId }} className="underline underline-offset-4">
                Registra una giornata
              </Link>
            </p>
          ) : (
            <ul className="border-t">
              {days.map((day) => (
                <li key={day.id} className="border-b">
                  <label className="flex cursor-pointer items-center gap-3 px-2 py-3 text-sm">
                    <Checkbox
                      checked={picked.has(day.id)}
                      onCheckedChange={(on) => toggle(day.id, on === true)}
                      aria-label={`${formatIsoDateItalian(day.data)}, ${day.descrizione}`}
                    />
                    <span className="w-24 shrink-0 font-medium tabular-nums">{formatIsoDateItalian(day.data)}</span>
                    <span className="min-w-0 flex-1 truncate">{day.descrizione}</span>
                    <span className="shrink-0 text-muted-foreground">
                      {quantitaLabel(day.quantita)} · {statoLabel(day.stato).toLowerCase()}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </fieldset>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="approval-channel">Come è arrivata</Label>
            <Select value={canale} onValueChange={(next) => setCanale(next as ApprovalCanale)}>
              <SelectTrigger id="approval-channel" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CANALE_IDS.map((id) => (
                  <SelectItem key={id} value={id}>
                    {CANALI[id]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="approval-received">Ricevuta il</Label>
            <Input
              id="approval-received"
              type="datetime-local"
              value={ricevutoIl}
              onChange={(event) => setRicevutoIl(event.target.value)}
              required
            />
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="approval-sender">Da chi</Label>
          <Input
            id="approval-sender"
            value={mittente}
            onChange={(event) => setMittente(event.target.value)}
            placeholder="giulia.bianchi@cliente.it"
            required
          />
        </div>

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">La prova</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {PROOFS.map((option) => (
              <label
                key={option.id}
                className={cn(
                  'flex min-h-11 cursor-pointer items-center justify-center border px-3 text-center text-sm font-medium transition-colors',
                  'has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50',
                  proof === option.id ? 'border-primary bg-primary text-primary-foreground' : 'bg-background hover:bg-muted/50',
                )}
              >
                <input
                  type="radio"
                  name="approval-proof"
                  value={option.id}
                  checked={proof === option.id}
                  onChange={() => setProof(option.id)}
                  className="sr-only"
                />
                {option.label}
              </label>
            ))}
          </div>

          {proof === 'messaggio' ? (
            <MessagePicker
              configured={gmail}
              contractChosen={contractId !== ''}
              loading={messages.isPending && contract !== undefined}
              error={messages.isError ? messages.error : null}
              messages={inbound}
              chosenId={messageId}
              onChoose={chooseMessage}
            />
          ) : null}
          {proof === 'file' ? (
            <div className="space-y-2">
              <Label htmlFor="approval-file">File</Label>
              <Input
                id="approval-file"
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.txt,.md,.docx"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <p className="text-sm text-muted-foreground">
                La lettera, la scansione della raccomandata o la stampa della mail: resta archiviata sul contratto.
              </p>
            </div>
          ) : null}
          {proof === 'testo' ? (
            <div className="space-y-2">
              <Label htmlFor="approval-text">Testo dell’approvazione</Label>
              <Textarea id="approval-text" rows={6} value={testo} onChange={(event) => setTesto(event.target.value)} />
              <p className="text-sm text-muted-foreground">Viene archiviato com’è, come documento del contratto.</p>
            </div>
          ) : null}
        </fieldset>

        <div className="space-y-2">
          <Label htmlFor="approval-excerpt">La frase che approva, com’è scritta</Label>
          <Textarea
            id="approval-excerpt"
            rows={3}
            value={estratto}
            onChange={(event) => setEstratto(event.target.value)}
            placeholder="Confermo le giornate del 2 e del 3 marzo."
            required
          />
          {mismatch ? (
            <p role="status" className="text-sm text-muted-foreground">
              Questa frase non compare nel messaggio scelto: controlla di averla copiata tal quale.
            </p>
          ) : null}
        </div>

        {failure ? (
          <p role="alert" className="border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {toProblem(failure).detail}
          </p>
        ) : null}

        <Button type="submit" size="lg" className="w-full sm:w-auto" disabled={!ready || busy}>
          {busy ? <Loader className="mr-2 size-4" /> : null}
          {chosenDays.length <= 1 ? 'Registra approvazione' : `Registra approvazione per ${chosenDays.length} giornate`}
        </Button>
      </form>
    </>
  )
}

/**
 * The messages already synced for the client, newest first as the API lists them, inbound
 * only: an approval is something the client sent. Choosing one shows its text, because the
 * excerpt is a passage of it and the person copies it from here.
 */
function MessagePicker({
  configured,
  contractChosen,
  loading,
  error,
  messages,
  chosenId,
  onChoose,
}: {
  configured: boolean
  contractChosen: boolean
  loading: boolean
  error: unknown
  messages: readonly GmailMessage[]
  chosenId: string
  onChoose: (message: GmailMessage) => void
}) {
  if (!configured) {
    return (
      <p className="text-sm text-muted-foreground">
        La posta non è collegata: archivia la prova come file o come testo incollato.
      </p>
    )
  }
  if (!contractChosen) return <p className="text-sm text-muted-foreground">Scegli prima il contratto.</p>
  if (error) return <QueryErrorBanner error={error} />
  if (loading) return <p className="text-sm text-muted-foreground">Cerco i messaggi del cliente…</p>
  if (messages.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nessun messaggio ricevuto da questo cliente è stato sincronizzato. Archivia la prova come file o come
        testo incollato.
      </p>
    )
  }
  const chosen = messages.find((message) => message.id === chosenId)
  return (
    <div className="space-y-3">
      <ul className="max-h-64 overflow-y-auto border-t" aria-label="Messaggi del cliente">
        {messages.map((message) => (
          <li key={message.id} className="border-b">
            <label
              className={cn(
                'flex cursor-pointer items-start gap-3 px-2 py-3 text-sm',
                message.id === chosenId ? 'bg-muted/60' : 'hover:bg-muted/40',
              )}
            >
              <input
                type="radio"
                name="approval-message"
                value={message.id}
                checked={message.id === chosenId}
                onChange={() => onChoose(message)}
                className="mt-1"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{message.subject || '(senza oggetto)'}</span>
                <span className="block truncate text-muted-foreground">
                  {message.from_address} · {formatOccurredAt(message.internal_date)}
                </span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      {chosen ? (
        chosen.body_text.trim() === '' ? (
          <p className="text-sm text-destructive">
            Il testo di questo messaggio non è archiviato: non può fare da prova. Scegli un altro messaggio o
            carica il file.
          </p>
        ) : (
          <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap border bg-muted/30 p-3 font-sans text-sm">
            {chosen.body_text}
          </pre>
        )
      ) : null}
    </div>
  )
}
