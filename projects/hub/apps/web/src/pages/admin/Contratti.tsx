import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, Download } from 'lucide-react'
import { useState, type ComponentProps, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type ContractDocument, type Fiscal, type FiscalData, type Match } from '@/lib/api'
import {
  cancelDescription,
  draftFromFiscal,
  sendReportMessage,
  toFiscalData,
  whatOf,
  type FiscalDraft,
} from '@/lib/contracts'
import { DOCUMENT_STATE_LABELS, MATCH_STATE_LABELS, formatDate } from '@/lib/format'
import { Empty, Header, Row } from './lists'

/** The four tax fields, shared by this page and step 2 of «Crea match». */
export function FiscalFields({
  idPrefix,
  draft,
  onChange,
  wrong,
}: {
  idPrefix: string
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  wrong: (field: string) => true | undefined
}) {
  const field = (name: keyof FiscalDraft, label: string, props: ComponentProps<typeof Input> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${idPrefix}-${name}`}>{label}</Label>
      <Input
        id={`${idPrefix}-${name}`}
        value={draft[name]}
        onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        aria-invalid={wrong(name)}
        {...props}
      />
    </div>
  )
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {field('codice_fiscale', 'Codice fiscale', { required: true, maxLength: 40, autoComplete: 'off' })}
      {field('partita_iva', 'Partita IVA', { required: true, maxLength: 40, inputMode: 'numeric' })}
      {field('domicilio', 'Domicilio professionale', { required: true, maxLength: 300 })}
      {field('pec', 'PEC, se ce l’ha', { type: 'email', maxLength: 320 })}
    </div>
  )
}

function DocumentLinks({ document }: { document: ContractDocument }) {
  const what = whatOf(document)
  return (
    <span className="flex flex-wrap gap-2">
      <Button asChild variant="outline" size="sm">
        <a href={admin.contractPdfUrl(document.id)} aria-label={`PDF ${what}`}>
          <Download className="mr-2 size-4" />
          PDF
        </a>
      </Button>
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a href={admin.contractPdfUrl(document.id, true)} aria-label={`PDF firmato ${what}`}>
            <Download className="mr-2 size-4" />
            PDF firmato
          </a>
        </Button>
      )}
    </span>
  )
}

/** The signing actions a document has in its state (REB-407): «Reinvia email» while it
 *  waits for the signature; «Aggiorna stato» while Documenso may know more than the hub
 *  (a lost webhook, a signed copy not downloaded yet, letters a signed framework
 *  agreement has still to release). */
function SigningActions({
  document,
  busy,
  refreshingId,
  resendingId,
  onRefresh,
  onResend,
}: {
  document: ContractDocument
  busy: boolean
  refreshingId: string | null
  resendingId: string | null
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
}) {
  const what = whatOf(document)
  const refreshing = refreshingId === document.id
  const resending = resendingId === document.id
  const refreshable =
    document.stato === 'inviato' ||
    (document.stato === 'firmato' && (document.kind === 'quadro' || !document.ha_pdf_firmato))
  return (
    <>
      {document.stato === 'inviato' && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Reinvia email ${what}`}
          onClick={() => onResend(document)}
        >
          {resending ? 'Reinvio…' : 'Reinvia email'}
        </Button>
      )}
      {refreshable && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Aggiorna stato ${what}`}
          onClick={() => onRefresh(document)}
        >
          {refreshing ? 'Aggiorno…' : 'Aggiorna stato'}
        </Button>
      )}
    </>
  )
}

/** A question before an action that cannot be taken back. */
function Confirm({
  open,
  title,
  description,
  confirm,
  pending,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirm: string
  pending: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Indietro
          </Button>
          <Button type="button" variant="destructive" disabled={pending} onClick={onConfirm}>
            {confirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function FrameworkSection({
  quadro,
  busy,
  refreshingId,
  resendingId,
  onRefresh,
  onResend,
  onCancel,
  onNotice,
  error,
}: {
  quadro: ContractDocument | null
  busy: boolean
  refreshingId: string | null
  resendingId: string | null
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
  onCancel: () => void
  onNotice: () => void
  error: string | null
}) {
  return (
    <section aria-labelledby="contratti-quadro" className="space-y-3 px-6 py-6">
      <h2 id="contratti-quadro" className="text-sm font-medium">
        Contratto quadro
      </h2>
      {quadro === null ? (
        <p className="text-sm text-muted-foreground">Nessun contratto quadro: lo genera il primo match.</p>
      ) : (
        <dl className="space-y-3 text-sm">
          <Row label="Stato">
            <span className="flex flex-wrap items-center gap-2">
              {/* An element of its own, so a test finds the state by its words alone. */}
              <span>{DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</span>
              {quadro.attivo && <Badge variant="pill">Attivo</Badge>}
              {quadro.testo_bozza && <Badge variant="pill">Testo in bozza</Badge>}
              {quadro.stato === 'inviato' && quadro.sent_at && (
                <span className="text-xs text-muted-foreground">Inviato il {formatDate(quadro.sent_at)}</span>
              )}
            </span>
          </Row>
          {quadro.cancel_reason && <Row label="Perché">{quadro.cancel_reason}</Row>}
          {quadro.stato !== 'annullato' && (
            <>
              <Row label="Firmato il">{quadro.signed_at ? formatDate(quadro.signed_at) : 'non ancora'}</Row>
              <Row label="Prossimo rinnovo">{quadro.rinnovo ? formatDate(quadro.rinnovo) : 'dopo la firma'}</Row>
              <Row label="Ultimo giorno per la disdetta">
                {quadro.ultimo_giorno_disdetta ? formatDate(quadro.ultimo_giorno_disdetta) : 'dopo la firma'}
              </Row>
            </>
          )}
          <Row label="Versione del testo">
            <span className="flex flex-wrap items-center gap-2">
              <span>{quadro.text_version}</span>
              {quadro.nuova_versione && <Badge variant="pill">Nuova versione disponibile</Badge>}
            </span>
          </Row>
          <Row label="Documento">
            <DocumentLinks document={quadro} />
          </Row>
          {quadro.stato !== 'annullato' && quadro.stato !== 'disdetto' && (
            <Row label="Azioni">
              <span className="flex flex-wrap gap-2">
                <SigningActions
                  document={quadro}
                  busy={busy}
                  refreshingId={refreshingId}
                  resendingId={resendingId}
                  onRefresh={onRefresh}
                  onResend={onResend}
                />
                {(quadro.stato === 'generato' || quadro.stato === 'inviato') && (
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={busy}
                    aria-label="Annulla il contratto quadro"
                    onClick={onCancel}
                  >
                    Annulla
                  </Button>
                )}
                {quadro.attivo && (
                  <Button type="button" variant="outline" size="sm" disabled={busy} onClick={onNotice}>
                    Registra disdetta
                  </Button>
                )}
              </span>
            </Row>
          )}
        </dl>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

function FiscalSection({ freelancerId, fiscale, onSaved }: { freelancerId: string; fiscale: Fiscal | null; onSaved: () => void }) {
  const [draft, setDraft] = useState<FiscalDraft>(() => draftFromFiscal(fiscale))
  const [saved, setSaved] = useState(false)
  const save = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(freelancerId, data),
    onSuccess: () => {
      setSaved(true)
      onSaved()
    },
  })
  const failure = save.error instanceof ApiError ? save.error : null
  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    save.mutate(toFiscalData(draft))
  }
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Dati fiscali del freelance</h2>
      <form onSubmit={submit} className="max-w-2xl space-y-3 border p-4">
        <FiscalFields
          idPrefix="contratti"
          draft={draft}
          onChange={setDraft}
          wrong={(field) => failure?.fields.includes(field) || undefined}
        />
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva i dati fiscali'}
        </Button>
        {saved && (
          <p role="status" className="text-sm text-muted-foreground">
            Dati fiscali salvati.
          </p>
        )}
        {save.error && (
          <p role="alert" className="text-sm text-destructive">
            {failure ? failure.message : 'Non riesco a salvare i dati fiscali.'}
          </p>
        )}
      </form>
    </section>
  )
}

function MatchesSection({
  matches,
  busy,
  sendingId,
  refreshingId,
  resendingId,
  canSend,
  onSend,
  onRefresh,
  onResend,
  onCancel,
  onClose,
  error,
}: {
  matches: Match[]
  busy: boolean
  sendingId: string | null
  refreshingId: string | null
  resendingId: string | null
  canSend: (match: Match) => boolean
  onSend: (match: Match) => void
  onRefresh: (document: ContractDocument) => void
  onResend: (document: ContractDocument) => void
  onCancel: (match: Match) => void
  onClose: (match: Match) => void
  error: string | null
}) {
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Match</h2>
      {matches.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessun match per questa persona.</p>
      ) : (
        <div className="overflow-x-auto border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Azienda</TableHead>
                <TableHead>Creato</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>Lettera di incarico</TableHead>
                <TableHead>
                  <span className="sr-only">Azioni</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {matches.map((match) => (
                <TableRow key={match.id}>
                  <TableCell>
                    <p className="font-medium">{match.nome_azienda}</p>
                    <p className="text-xs text-muted-foreground">{match.figura_richiesta}</p>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(match.created_at)}</TableCell>
                  <TableCell>
                    <Badge variant="pill">{MATCH_STATE_LABELS[match.stato] ?? match.stato}</Badge>
                  </TableCell>
                  <TableCell className="space-y-1">
                    <p className="text-sm">
                      n. {match.lettera.numero} · {DOCUMENT_STATE_LABELS[match.lettera.stato] ?? match.lettera.stato}
                    </p>
                    {match.lettera.stato === 'inviato' && match.lettera.sent_at && (
                      <p className="text-xs text-muted-foreground">Inviato il {formatDate(match.lettera.sent_at)}</p>
                    )}
                    {match.lettera.cancel_reason && (
                      <p className="text-xs text-muted-foreground">{match.lettera.cancel_reason}</p>
                    )}
                    <DocumentLinks document={match.lettera} />
                  </TableCell>
                  <TableCell>
                    <span className="flex flex-wrap justify-end gap-2">
                      {canSend(match) && (
                        <Button
                          type="button"
                          size="sm"
                          disabled={busy}
                          aria-label={`Invia per la firma il match con ${match.nome_azienda}`}
                          onClick={() => onSend(match)}
                        >
                          {sendingId === match.id ? 'Invio…' : 'Invia per la firma'}
                        </Button>
                      )}
                      <SigningActions
                        document={match.lettera}
                        busy={busy}
                        refreshingId={refreshingId}
                        resendingId={resendingId}
                        onRefresh={onRefresh}
                        onResend={onResend}
                      />
                      {(match.stato === 'bozza' || match.stato === 'in_firma') && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`Annulla il match con ${match.nome_azienda}`}
                          onClick={() => onCancel(match)}
                        >
                          Annulla
                        </Button>
                      )}
                      {match.stato === 'attivo' && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          aria-label={`Chiudi il match con ${match.nome_azienda}`}
                          onClick={() => onClose(match)}
                        >
                          Chiudi match
                        </Button>
                      )}
                    </span>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

/** «Match e contratti» (REB-387): the framework agreement with its dates and its signing
 *  actions, the tax data, and every match with its letter. «Invia per la firma» sends a
 *  match's document (REB-390); «Reinvia email», «Aggiorna stato», «Annulla» and
 *  «Registra disdetta» follow the signature (REB-407). */
export function AdminContratti() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/contracts' })
  const client = useQueryClient()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const contracts = useQuery({ queryKey: ['contracts', id], queryFn: () => admin.contracts(id) })
  const [message, setMessage] = useState<string | null>(null)
  // The last action's own error, and which section it belongs to: an action starting
  // clears it, whether it is the one that failed before or another one, so a stale
  // error never sits next to a later action's success, and the framework agreement's
  // own errors show under the framework section rather than under the matches table
  // (REB-407).
  const [actionFailure, setActionFailure] = useState<{ section: 'quadro' | 'match'; message: string } | null>(
    null,
  )
  const [confirming, setConfirming] = useState<Match | null>(null)
  const [confirmingQuadro, setConfirmingQuadro] = useState<'annulla' | 'disdetta' | null>(null)
  const refresh = () => void client.invalidateQueries({ queryKey: ['contracts', id] })
  const saying = (sentence: string) => () => {
    setMessage(sentence)
    refresh()
  }
  const fail = (section: 'quadro' | 'match') => (error: unknown) =>
    setActionFailure({
      section,
      message: error instanceof ApiError ? error.message : 'Non riesco a completare l’operazione.',
    })
  const starting = (section: 'quadro' | 'match') => {
    setMessage(null)
    setActionFailure(null)
    return { onError: fail(section) }
  }
  const send = useMutation({
    mutationFn: (matchId: string) => admin.sendMatch(matchId),
    onSuccess: (report) => {
      setMessage(sendReportMessage(report))
      refresh()
    },
  })
  const cancel = useMutation({ mutationFn: (matchId: string) => admin.cancelMatch(matchId), onSuccess: refresh })
  const close = useMutation({ mutationFn: (matchId: string) => admin.closeMatch(matchId), onSuccess: refresh })
  const resend = useMutation({
    mutationFn: (documentId: string) => admin.resendDocument(documentId),
    onSuccess: saying('Mail inviata di nuovo.'),
  })
  const update = useMutation({
    mutationFn: (documentId: string) => admin.refreshDocument(documentId),
    onSuccess: saying('Stato letto da Documenso.'),
  })
  const cancelQuadro = useMutation({
    mutationFn: (documentId: string) => admin.cancelDocument(documentId),
    onSuccess: saying('Contratto quadro annullato.'),
  })
  const notice = useMutation({
    mutationFn: (documentId: string) => admin.recordNotice(documentId),
    onSuccess: saying('Disdetta registrata.'),
  })
  const actions = [send, cancel, close, resend, update, cancelQuadro, notice]

  if (contracts.isError) return <Empty>Non riesco a leggere i contratti di questa persona.</Empty>
  if (contracts.isPending) return <Empty>Caricamento…</Empty>
  const data = contracts.data
  const quadro = data.quadro
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const busy = actions.some((action) => action.isPending)
  // A draft leaves on request; a match in signature only when its letter still waits and
  // no framework agreement is out for signature to carry it (a cancelled or refused one,
  // or a signed one whose release failed).
  const canSend = (match: Match) =>
    match.stato === 'bozza' ||
    (match.stato === 'in_firma' && match.lettera.stato === 'in_attesa' && quadro?.stato !== 'inviato')
  const onRefresh = (section: 'quadro' | 'match') => (document: ContractDocument) => {
    update.mutate(document.id, starting(section))
  }
  const onResend = (section: 'quadro' | 'match') => (document: ContractDocument) => {
    resend.mutate(document.id, starting(section))
  }
  // Which row's button shows an in-progress label, next to `busy`'s blanket disable
  // (REB-407): the id of the document or match the pending mutation was called with,
  // or `null` when nothing of that kind is in flight.
  const sendingId = send.isPending ? (send.variables ?? null) : null
  const refreshingId = update.isPending ? (update.variables ?? null) : null
  const resendingId = resend.isPending ? (resend.variables ?? null) : null
  return (
    <>
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'}>
        <Button asChild size="sm">
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </Button>
      </Header>
      <FrameworkSection
        quadro={quadro}
        busy={busy}
        refreshingId={refreshingId}
        resendingId={resendingId}
        onRefresh={onRefresh('quadro')}
        onResend={onResend('quadro')}
        onCancel={() => setConfirmingQuadro('annulla')}
        onNotice={() => setConfirmingQuadro('disdetta')}
        error={actionFailure?.section === 'quadro' ? actionFailure.message : null}
      />
      <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaved={refresh} />
      {message && (
        <p role="status" className="px-6 pb-3 text-sm">
          {message}
        </p>
      )}
      <MatchesSection
        matches={data.matches}
        busy={busy}
        sendingId={sendingId}
        refreshingId={refreshingId}
        resendingId={resendingId}
        canSend={canSend}
        onSend={(match) => send.mutate(match.id, starting('match'))}
        onRefresh={onRefresh('match')}
        onResend={onResend('match')}
        onCancel={setConfirming}
        onClose={(match) => close.mutate(match.id, starting('match'))}
        error={actionFailure?.section === 'match' ? actionFailure.message : null}
      />
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Torna alla scheda
        </Link>
      </p>
      <Confirm
        open={confirming !== null}
        title="Annullare il match?"
        description={confirming ? cancelDescription(confirming) : ''}
        confirm={cancel.isPending ? 'Annullo…' : 'Annulla il match'}
        pending={cancel.isPending}
        onConfirm={() => {
          if (confirming) cancel.mutate(confirming.id, { ...starting('match'), onSettled: () => setConfirming(null) })
        }}
        onClose={() => setConfirming(null)}
      />
      <Confirm
        open={confirmingQuadro === 'annulla'}
        title="Annullare il contratto quadro?"
        description="Se è già partito, viene annullato anche sul sito di firma e il link ricevuto dal freelance smette di funzionare. Le lettere che lo aspettano restano in attesa: «Invia per la firma» sul loro match ne genera uno nuovo."
        confirm={cancelQuadro.isPending ? 'Annullo…' : 'Sì, annulla il contratto quadro'}
        pending={cancelQuadro.isPending}
        onConfirm={() => {
          if (quadro)
            cancelQuadro.mutate(quadro.id, { ...starting('quadro'), onSettled: () => setConfirmingQuadro(null) })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
      <Confirm
        open={confirmingQuadro === 'disdetta'}
        title="Registrare la disdetta?"
        description="Da oggi il contratto quadro non è più attivo, e il prossimo match ne genera uno nuovo. Si registra quando il freelance o rebase ha dato disdetta, o uno dei due ha receduto."
        confirm={notice.isPending ? 'Registro…' : 'Sì, registra la disdetta'}
        pending={notice.isPending}
        onConfirm={() => {
          if (quadro) notice.mutate(quadro.id, { ...starting('quadro'), onSettled: () => setConfirmingQuadro(null) })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
    </>
  )
}
