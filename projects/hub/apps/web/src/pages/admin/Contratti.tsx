import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState, type RefObject } from 'react'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { admin, ApiError, type ContractDocument, type Match } from '@/lib/api'
import { cancelDescription, sendReportMessage, whatOf } from '@/lib/contracts'
import { ACTION_LABELS } from '@/lib/format'
import { FrameworkCard, MatchCards, type ActionHandles } from './contratti/Cards'
import { FiscalSection } from './contratti/Fiscal'
import { Empty, Header } from './lists'

/** A question before an action that cannot be taken back. `opener` gets the focus back
 *  on close: an item of «Altre azioni» has left the page by then, and Radix alone would
 *  drop the focus on the body. */
function Confirm({
  open,
  title,
  description,
  confirm,
  pending,
  opener,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirm: string
  pending: boolean
  opener: RefObject<HTMLElement | null>
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        onCloseAutoFocus={(event) => {
          const target = opener.current
          if (!target?.isConnected) return
          event.preventDefault()
          target.focus()
        }}
      >
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

/** Whether `mutation` is running for this very object, so only its own button says so. */
function runningFor<T>(mutation: { isPending: boolean; variables: T | undefined }, id: T): boolean {
  return mutation.isPending && mutation.variables === id
}

/** «Match e contratti» (REB-387): the freelancer's situation top to bottom, the framework
 *  agreement, every match, the tax data. Each card says where it stands and offers the
 *  one next step in the core's words (REB-477): the page maps each of the core's actions
 *  to its API call and decides none of them. */
export function AdminContratti() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/contracts' })
  const navigate = useNavigate()
  const client = useQueryClient()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const contracts = useQuery({ queryKey: ['contracts', id], queryFn: () => admin.contracts(id) })
  // What «Crea match» left for this page in the history entry (REB-476): shown on
  // arrival, as the last action's sentence, so the next action replaces it.
  const notice = useLocation({ select: (location) => location.state.notice })
  const [message, setMessage] = useState<string | null>(notice ?? null)
  useEffect(() => {
    if (notice === undefined) return
    // Taken off the entry once read, so a reload or a return to it does not say it again.
    void navigate({ to: '/admin/freelance/$id/contracts', params: { id }, replace: true, resetScroll: false })
  }, [notice, navigate, id])
  // The last action's own error, and which section it belongs to: an action starting
  // clears it, whether it is the one that failed before or another one, so a stale
  // error never sits next to a later action's success, and the framework agreement's
  // own errors show under its card rather than under the matches (REB-407).
  const [actionFailure, setActionFailure] = useState<{ section: 'quadro' | 'match'; message: string } | null>(
    null,
  )
  const [confirming, setConfirming] = useState<Match | null>(null)
  const [confirmingQuadro, setConfirmingQuadro] = useState<'annulla' | 'disdetta' | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  const asking = (open: () => void) => (from: HTMLElement | null) => {
    opener.current = from
    open()
  }
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
  const recordNotice = useMutation({
    mutationFn: (documentId: string) => admin.recordNotice(documentId),
    onSuccess: saying('Disdetta registrata.'),
  })
  const actions = [send, cancel, close, resend, update, cancelQuadro, recordNotice]

  if (contracts.isError) return <Empty>Non riesco a leggere i contratti di questa persona.</Empty>
  if (contracts.isPending) return <Empty>Caricamento…</Empty>
  const data = contracts.data
  const quadro = data.quadro
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const busy = actions.some((action) => action.isPending)

  // The signing actions a document has, shared by the framework agreement's card and a
  // match's, which reaches them on its letter.
  const signing = (document: ContractDocument, section: 'quadro' | 'match'): ActionHandles => ({
    reinvia_email: {
      label: `${ACTION_LABELS.reinvia_email} ${whatOf(document)}`,
      run: () => resend.mutate(document.id, starting(section)),
      pending: runningFor(resend, document.id),
    },
    aggiorna_stato: {
      label: `${ACTION_LABELS.aggiorna_stato} ${whatOf(document)}`,
      run: () => update.mutate(document.id, starting(section)),
      pending: runningFor(update, document.id),
    },
  })
  const quadroHandles = (document: ContractDocument): ActionHandles => ({
    ...signing(document, 'quadro'),
    annulla: {
      label: 'Annulla il contratto quadro',
      run: asking(() => setConfirmingQuadro('annulla')),
      pending: runningFor(cancelQuadro, document.id),
    },
    registra_disdetta: {
      label: `${ACTION_LABELS.registra_disdetta} ${whatOf(document)}`,
      run: asking(() => setConfirmingQuadro('disdetta')),
      pending: runningFor(recordNotice, document.id),
    },
  })
  const matchHandles = (match: Match): ActionHandles => ({
    ...signing(match.lettera, 'match'),
    invia: {
      label: `${ACTION_LABELS.invia} il match con ${match.nome_azienda}`,
      run: () => send.mutate(match.id, starting('match')),
      pending: runningFor(send, match.id),
    },
    annulla: {
      label: `Annulla il match con ${match.nome_azienda}`,
      run: asking(() => setConfirming(match)),
      pending: runningFor(cancel, match.id),
    },
    chiudi: {
      label: `Chiudi il match con ${match.nome_azienda}`,
      run: () => close.mutate(match.id, starting('match')),
      pending: runningFor(close, match.id),
    },
  })

  return (
    <>
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'}>
        <Button asChild size="sm">
          <Link to="/admin/freelance/$id/match/new" params={{ id }}>
            Crea match
          </Link>
        </Button>
      </Header>
      {message && (
        <p role="status" className="border-b px-6 py-3 text-sm">
          {message}
        </p>
      )}
      <div className="max-w-3xl space-y-8 px-6 py-6">
        <FrameworkCard
          quadro={quadro}
          handles={quadro ? quadroHandles(quadro) : {}}
          busy={busy}
          error={actionFailure?.section === 'quadro' ? actionFailure.message : null}
        />
        <MatchCards
          matches={data.matches}
          handlesFor={matchHandles}
          busy={busy}
          error={actionFailure?.section === 'match' ? actionFailure.message : null}
        />
        <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaved={refresh} />
        <p>
          <Link
            to="/admin/freelance/$id"
            params={{ id }}
            className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline"
          >
            <ArrowLeft className="size-4" /> Torna alla scheda
          </Link>
        </p>
      </div>
      <Confirm
        open={confirming !== null}
        title="Annullare il match?"
        description={confirming ? cancelDescription(confirming) : ''}
        confirm={cancel.isPending ? 'Annullo…' : 'Annulla il match'}
        pending={cancel.isPending}
        opener={opener}
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
        opener={opener}
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
        confirm={recordNotice.isPending ? 'Registro…' : 'Sì, registra la disdetta'}
        pending={recordNotice.isPending}
        opener={opener}
        onConfirm={() => {
          if (quadro) recordNotice.mutate(quadro.id, { ...starting('quadro'), onSettled: () => setConfirmingQuadro(null) })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
    </>
  )
}
