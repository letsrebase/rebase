import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useLocation, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
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
import {
  MATCHES_HEADING_ID,
  QUADRO_HEADING_ID,
  cancelDescription,
  closeDescription,
  matchHeadingId,
  matchOf,
  sendReportMessage,
  whatOf,
} from '@/lib/contracts'
import { ACTION_LABELS } from '@/lib/format'
import { FrameworkCard, MatchCards, type ActionHandles } from './contratti/Cards'
import { FiscalSection } from './contratti/Fiscal'
import { Empty, Header } from './lists'

type Section = 'quadro' | 'match'
const SECTION_HEADING: Record<Section, string> = { quadro: QUADRO_HEADING_ID, match: MATCHES_HEADING_ID }

/** Where the focus goes back to after an action: the control that started it while it is
 *  still on the page, else the first of `headings` that is (its card's, then its
 *  section's). Radix, and the browser once a focused control is removed, would leave it
 *  on the body. Answers whether it found a place. */
function focusBack(opener: HTMLElement | null, headings: string[]): boolean {
  const target = opener?.isConnected
    ? opener
    : (headings.map((id) => document.getElementById(id)).find((heading) => heading !== null) ?? null)
  target?.focus()
  return target !== null
}

/** A question before an action that cannot be taken back. */
function Confirm({
  open,
  title,
  description,
  confirm,
  pending,
  onCloseFocus,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  description: string
  confirm: string
  pending: boolean
  /** Puts the focus back on close, answering whether it did: an item of «Altre azioni»
   *  has left the page by then. */
  onCloseFocus: () => boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (onCloseFocus()) event.preventDefault()
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
  const [actionFailure, setActionFailure] = useState<{ section: Section; message: string } | null>(null)
  const [confirming, setConfirming] = useState<Match | null>(null)
  const [closing, setClosing] = useState<Match | null>(null)
  const [confirmingQuadro, setConfirmingQuadro] = useState<'annulla' | 'disdetta' | null>(null)
  // The control the last action started from, and, once its request left, the headings
  // to land on should the refetched page no longer have that control.
  const opener = useRef<HTMLElement | null>(null)
  const landing = useRef<string[]>([])
  useEffect(() => {
    if (landing.current.length === 0) return
    const active = document.activeElement
    // A question still open gives the focus back itself when it closes.
    if (active?.closest('[role="dialog"]')) return
    if (active === null || active === document.body) focusBack(opener.current, landing.current)
    landing.current = []
  }, [contracts.dataUpdatedAt])
  const refresh = () => void client.invalidateQueries({ queryKey: ['contracts', id] })
  const saying = (sentence: string) => () => {
    setMessage(sentence)
    refresh()
  }
  const quiet = () => {
    setMessage(null)
    setActionFailure(null)
  }
  const starting = (section: Section, card: string) => {
    quiet()
    landing.current = [card, SECTION_HEADING[section]]
    return {
      onError: (error: unknown) => {
        landing.current = []
        setActionFailure({
          section,
          message: error instanceof ApiError ? error.message : 'Non riesco a completare l’operazione.',
        })
      },
    }
  }
  // What a card's control runs: a question first, which only remembers the control, or
  // a request, which also starts the action for its card.
  const from = (ask: () => void) => (control: HTMLElement | null) => {
    opener.current = control
    ask()
  }
  const request =
    <T,>(
      mutation: { mutate: (variables: T, options: ReturnType<typeof starting>) => void },
      variables: T,
      section: Section,
      card: string,
    ) =>
    (control: HTMLElement | null) => {
      opener.current = control
      mutation.mutate(variables, starting(section, card))
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
  const focusOnClose = () => focusBack(opener.current, landing.current)

  // The signing actions a document has, shared by the framework agreement's card and a
  // match's, which reaches them on its letter.
  const signing = (document: ContractDocument, section: Section, card: string): ActionHandles => ({
    reinvia_email: {
      label: `${ACTION_LABELS.reinvia_email} ${whatOf(document)}`,
      pendingLabel: `Reinvio l’email ${whatOf(document)}`,
      run: request(resend, document.id, section, card),
      pending: runningFor(resend, document.id),
    },
    aggiorna_stato: {
      label: `${ACTION_LABELS.aggiorna_stato} ${whatOf(document)}`,
      pendingLabel: `Aggiorno lo stato ${whatOf(document)}`,
      run: request(update, document.id, section, card),
      pending: runningFor(update, document.id),
    },
  })
  const quadroHandles = (document: ContractDocument): ActionHandles => ({
    ...signing(document, 'quadro', QUADRO_HEADING_ID),
    annulla: {
      label: 'Annulla il contratto quadro',
      pendingLabel: 'Annullo il contratto quadro',
      run: from(() => setConfirmingQuadro('annulla')),
      pending: runningFor(cancelQuadro, document.id),
    },
    registra_disdetta: {
      label: `${ACTION_LABELS.registra_disdetta} ${whatOf(document)}`,
      pendingLabel: `Registro la disdetta ${whatOf(document)}`,
      run: from(() => setConfirmingQuadro('disdetta')),
      pending: runningFor(recordNotice, document.id),
    },
  })
  const matchHandles = (match: Match): ActionHandles => {
    const which = matchOf(match)
    return {
      ...signing(match.lettera, 'match', matchHeadingId(match.id)),
      invia: {
        label: `${ACTION_LABELS.invia} ${which}`,
        pendingLabel: `Invio per la firma ${which}`,
        run: request(send, match.id, 'match', matchHeadingId(match.id)),
        pending: runningFor(send, match.id),
      },
      annulla: {
        label: `Annulla ${which}`,
        pendingLabel: `Annullo ${which}`,
        run: from(() => setConfirming(match)),
        pending: runningFor(cancel, match.id),
      },
      chiudi: {
        label: `Chiudi ${which}`,
        pendingLabel: `Chiudo ${which}`,
        run: from(() => setClosing(match)),
        pending: runningFor(close, match.id),
      },
    }
  }

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
        <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaving={quiet} onSaved={refresh} />
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
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (confirming)
            cancel.mutate(confirming.id, {
              ...starting('match', matchHeadingId(confirming.id)),
              onSettled: () => setConfirming(null),
            })
        }}
        onClose={() => setConfirming(null)}
      />
      <Confirm
        open={closing !== null}
        title="Chiudere il match?"
        description={closing ? closeDescription(closing) : ''}
        confirm={close.isPending ? 'Chiudo…' : 'Sì, chiudi il match'}
        pending={close.isPending}
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (closing)
            close.mutate(closing.id, {
              ...starting('match', matchHeadingId(closing.id)),
              onSettled: () => setClosing(null),
            })
        }}
        onClose={() => setClosing(null)}
      />
      <Confirm
        open={confirmingQuadro === 'annulla'}
        title="Annullare il contratto quadro?"
        description="Se è già partito, viene annullato anche sul sito di firma e il link ricevuto dal freelance smette di funzionare. Le lettere che lo aspettano restano in attesa: «Invia per la firma» sul loro match ne genera uno nuovo."
        confirm={cancelQuadro.isPending ? 'Annullo…' : 'Sì, annulla il contratto quadro'}
        pending={cancelQuadro.isPending}
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (quadro)
            cancelQuadro.mutate(quadro.id, {
              ...starting('quadro', QUADRO_HEADING_ID),
              onSettled: () => setConfirmingQuadro(null),
            })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
      <Confirm
        open={confirmingQuadro === 'disdetta'}
        title="Registrare la disdetta?"
        description="Da oggi il contratto quadro non è più attivo, e il prossimo match ne genera uno nuovo. Si registra quando il freelance o rebase ha dato disdetta, o uno dei due ha receduto."
        confirm={recordNotice.isPending ? 'Registro…' : 'Sì, registra la disdetta'}
        pending={recordNotice.isPending}
        onCloseFocus={focusOnClose}
        onConfirm={() => {
          if (quadro)
            recordNotice.mutate(quadro.id, {
              ...starting('quadro', QUADRO_HEADING_ID),
              onSettled: () => setConfirmingQuadro(null),
            })
        }}
        onClose={() => setConfirmingQuadro(null)}
      />
    </>
  )
}
