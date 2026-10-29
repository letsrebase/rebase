import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useState } from 'react'
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
import {
  ApiError,
  admin,
  type DuplicatePair,
  type DuplicatePerson,
  type DuplicateSignal,
  type MergeResult,
} from '@/lib/api'
import { formatDate } from '@/lib/format'
import { DUPLICATES_KEY, mergeResultLine, pairCount, useDuplicates } from '@/lib/duplicates'
import { Empty, Header } from './lists'

const SIGNAL_LABELS: Record<DuplicateSignal, string> = {
  linkedin: 'Stesso profilo LinkedIn',
  nome: 'Stesso nome',
  telefono: 'Stesso telefono',
}

/** How a card is called on this page: the name, the address when it has none. */
function personName(person: DuplicatePerson): string {
  return `${person.nome} ${person.cognome}`.trim() || person.email
}

function cvLine(person: DuplicatePerson): string {
  if (!person.ha_cv) return 'Nessun CV'
  return person.cv_uploaded_at ? `CV caricato il ${formatDate(person.cv_uploaded_at)}` : 'CV caricato'
}

/** «Duplicati» (REB-800): the people who are on the list twice, from Talenti. Pairs that
 *  share a LinkedIn profile come first and can be merged; the ones that only share a name
 *  or a number are «Da guardare», with the same merge behind a softer question, since two
 *  people are called «Marco Rossi» and a number can be a studio's. */
export function AdminDuplicati() {
  const duplicates = useDuplicates()
  const [pending, setPending] = useState<{ pair: DuplicatePair; soft: boolean } | null>(null)
  const [done, setDone] = useState<MergeResult | null>(null)
  const list = duplicates.data

  return (
    <>
      <Header title="Duplicati" count={list ? pairCount(list) : undefined} />
      {done && (
        <div role="status" className="mx-6 mt-6 space-y-1 border bg-card px-4 py-3 text-sm">
          <p>{mergeResultLine(done)}</p>
          <p className="text-xs text-muted-foreground">
            Per annullare dalla shell:{' '}
            <code>rebase talent-duplicates --undo {done.action_id} --admin EMAIL</code>
          </p>
        </div>
      )}
      {duplicates.isError ? (
        <Empty>Non riesco a leggere i duplicati.</Empty>
      ) : !list ? (
        <Empty>Caricamento…</Empty>
      ) : pairCount(list) === 0 ? (
        <Empty>Nessun duplicato.</Empty>
      ) : (
        <div className="space-y-8 p-6">
          {list.unibili.length > 0 && (
            <PairSection
              title="Stessa persona"
              note="Hanno lo stesso profilo LinkedIn: la scheda con il CV più recente resta."
              pairs={list.unibili}
              onMerge={(pair) => setPending({ pair, soft: false })}
            />
          )}
          {list.da_rivedere.length > 0 && (
            <PairSection
              title="Da guardare"
              note="Hanno in comune solo il nome o il telefono: guarda le due schede prima di unirle."
              pairs={list.da_rivedere}
              soft
              onMerge={(pair) => setPending({ pair, soft: true })}
            />
          )}
        </div>
      )}
      {list && list.iscrizioni > 0 && (
        <p className="px-6 pb-6 text-sm text-muted-foreground">
          {list.iscrizioni === 1
            ? '1 iscrizione ripete un profilo già in lista'
            : `${list.iscrizioni} iscrizioni ripetono un profilo già in lista`}
          : non ha una scheda da unire.
        </p>
      )}
      <p className="px-6 pb-6">
        <Link to="/admin/talent" className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Tutti i talenti
        </Link>
      </p>
      {pending && (
        <MergeDialog
          key={`${pending.pair.survivor.freelancer_id}:${pending.pair.assorbito.freelancer_id}`}
          pair={pending.pair}
          soft={pending.soft}
          onClose={() => setPending(null)}
          onMerged={(result) => {
            setDone(result)
            setPending(null)
          }}
        />
      )}
    </>
  )
}

function PairSection({
  title,
  note,
  pairs,
  soft = false,
  onMerge,
}: {
  title: string
  note: string
  pairs: DuplicatePair[]
  soft?: boolean
  onMerge: (pair: DuplicatePair) => void
}) {
  return (
    <section aria-label={title} className="space-y-3">
      <div>
        <h2 className="text-sm font-medium">
          {title}
          <span className="ml-2 font-normal text-muted-foreground">{pairs.length}</span>
        </h2>
        <p className="text-sm text-muted-foreground">{note}</p>
      </div>
      <ol className="space-y-3">
        {pairs.map((pair) => (
          <li
            key={`${pair.survivor.freelancer_id}:${pair.assorbito.freelancer_id}`}
            className="space-y-3 border bg-card p-4"
          >
            <div className="flex flex-wrap items-center gap-1.5">
              {pair.segnali.map((signal) => (
                <Badge key={signal} variant="pill">
                  {SIGNAL_LABELS[signal]}
                </Badge>
              ))}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <PersonCard person={pair.survivor} role="Resta" />
              <PersonCard person={pair.assorbito} role="Viene nascosta" />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-3">
              {soft && <span className="text-sm text-muted-foreground">Sono la stessa persona?</span>}
              <Button
                type="button"
                size="sm"
                variant={soft ? 'outline' : 'default'}
                aria-label={`Unisci ${personName(pair.survivor)} e ${personName(pair.assorbito)}`}
                onClick={() => onMerge(pair)}
              >
                Unisci
              </Button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}

function PersonCard({ person, role }: { person: DuplicatePerson; role: string }) {
  return (
    <div className="min-w-0 space-y-1 text-sm">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{role}</p>
      <p>
        <Link
          to="/admin/freelance/$id"
          params={{ id: person.freelancer_id }}
          className="font-medium underline-offset-2 hover:underline"
        >
          {personName(person)}
        </Link>
      </p>
      <p className="break-words text-muted-foreground">{person.email}</p>
      <p>{cvLine(person)}</p>
      <p className="text-muted-foreground">Scheda creata il {formatDate(person.created_at)}</p>
    </div>
  )
}

/** The confirmation before a merge: what moves, that the other card is hidden and not
 *  deleted, and that putting it back is a command in the shell. The keeper is the pair's
 *  default, the card with the newest CV, unless the admin turns the pair round. */
function MergeDialog({
  pair,
  soft,
  onClose,
  onMerged,
}: {
  pair: DuplicatePair
  soft: boolean
  onClose: () => void
  onMerged: (result: MergeResult) => void
}) {
  const client = useQueryClient()
  const [swapped, setSwapped] = useState(false)
  const keep = swapped ? pair.assorbito : pair.survivor
  const hide = swapped ? pair.survivor : pair.assorbito
  const merge = useMutation({
    mutationFn: () => admin.mergeFreelancer(keep.freelancer_id, hide.freelancer_id),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: DUPLICATES_KEY })
      void client.invalidateQueries({ queryKey: ['talenti'] })
      void client.invalidateQueries({ queryKey: ['freelancer'] })
      void client.invalidateQueries({ queryKey: ['audit'] })
      onMerged(result)
    },
  })
  const failure =
    merge.error instanceof ApiError ? merge.error.message : merge.error ? 'Non riesco a unire le schede.' : null

  return (
    <Dialog open onOpenChange={(open) => !open && !merge.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{soft ? 'Sono la stessa persona?' : 'Unire le due schede?'}</DialogTitle>
          <DialogDescription>
            {soft
              ? 'Hanno in comune solo il nome o il telefono. Unisci solo se hai letto le due schede e sono la stessa persona.'
              : 'Hanno lo stesso profilo LinkedIn: è la stessa persona.'}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p>
            Resta <strong>{personName(keep)}</strong> ({keep.email}). Viene nascosta{' '}
            <strong>{personName(hide)}</strong> ({hide.email}).
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Passano alla scheda che resta gli accessi, le note, i match e i contratti, le campagne, i
              referral, le richieste e il registro delle modifiche.
            </li>
            <li>Resta il CV più recente; se serve, la scheda anonima va riscritta.</li>
            <li>L’altra scheda è nascosta, non cancellata.</li>
            <li>
              Da qui non si annulla: si annulla solo dalla shell, con{' '}
              <code className="border bg-muted px-1 py-0.5">rebase talent-duplicates --undo</code>.
            </li>
          </ul>
          <Button type="button" variant="ghost" size="sm" onClick={() => setSwapped((value) => !value)}>
            Tieni l’altra scheda
          </Button>
          {failure && (
            <p role="alert" className="text-destructive">
              {failure}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={merge.isPending}>
            Annulla
          </Button>
          <Button type="button" onClick={() => merge.mutate()} disabled={merge.isPending}>
            {merge.isPending ? 'Unisco…' : 'Conferma e unisci'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
