import { Link } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import type { MatchCheck } from '@/lib/api'
import { sendLabel, type Failure } from '@/lib/contracts'

/** What step 3 shows: the hub's sentences about the match and the two unsaved
 *  previews, blobs in this tab's memory until the page lets them go. */
export interface Review {
  check: MatchCheck
  lettera: string
  quadro: string | null
}

function ToContracts({ freelancerId }: { freelancerId: string }) {
  return (
    <Link to="/admin/freelance/$id/contracts" params={{ id: freelancerId }} className="underline underline-offset-2">
      Vai a Match e contratti
    </Link>
  )
}

/** Step 3: no field, only what is about to happen and the two ways to finish. */
export function ControllaStep({
  review,
  nome,
  onBack,
  onSave,
  onSend,
  saving,
  sending,
  locked,
  failure,
  sentMessage,
  freelancerId,
}: {
  review: Review
  nome: string
  onBack: () => void
  onSave: () => void
  onSend: () => void
  saving: boolean
  sending: boolean
  /** The match is written: what it says can no longer change from here. */
  locked: boolean
  failure: Failure | null
  sentMessage: string | null
  freelancerId: string
}) {
  // A report already back (even a refusal's, `sentMessage`) means this send already
  // happened once: a second click must not send it again, and the match is no longer a
  // draft to save (REB-406).
  const sent = sentMessage !== null
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {review.check.riepilogo.map((sentence, index) => (
          <p key={index}>{sentence}</p>
        ))}
      </div>
      <p>{review.check.cosa_succede}</p>
      <ul className="space-y-2 text-sm">
        <li>
          <a className="underline underline-offset-2" href={review.lettera} target="_blank" rel="noreferrer">
            Apri la lettera (PDF)
          </a>
        </li>
        {review.quadro && (
          <li>
            <a className="underline underline-offset-2" href={review.quadro} target="_blank" rel="noreferrer">
              Apri il contratto quadro (PDF)
            </a>
          </li>
        )}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={onBack} disabled={saving || sending || locked}>
          Indietro
        </Button>
        <Button type="button" variant="outline" onClick={onSave} disabled={saving || sending || sent}>
          {saving ? 'Salvo…' : 'Salva senza inviare'}
        </Button>
        <Button type="button" onClick={onSend} disabled={saving || sending || sent}>
          {sending ? 'Invio…' : sendLabel(nome)}
        </Button>
      </div>
      {failure && (
        <div role="alert" className="space-y-1 text-sm text-destructive">
          <p>{failure.message}</p>
          {/* A 409 names a row already saved differently, never a field on this page
           *  (REB-406): the way onward is the row itself, in «Match e contratti». */}
          {failure.conflict && <ToContracts freelancerId={freelancerId} />}
        </div>
      )}
      {sentMessage && (
        <div role="status" className="space-y-1 text-sm">
          <p>{sentMessage}</p>
          <ToContracts freelancerId={freelancerId} />
        </div>
      )}
    </div>
  )
}
