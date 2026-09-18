import { useState } from 'react'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { toProblem, type ProblemDetail } from '@/lib/api'
import {
  OFFER_STATE_LABELS,
  OFFER_STATE_TONE,
  OFFER_TRANSITIONS,
  useSetOfferState,
  type Document,
  type OfferState,
} from './queries'

/**
 * The buttons come from the backend's own transition table, so the UI never offers a
 * move the server would refuse. It is not a second copy of the rule: the server still
 * checks, and a refusal is shown with the server's own message -- which is what
 * happens when the document changed underneath the page.
 */
export function OfferStatePicker({ document }: { document: Document }) {
  const setState = useSetOfferState(document.id)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  if (document.tipo !== 'offerta' || document.stato === null) return null

  const current = document.stato as OfferState
  const allowed = OFFER_TRANSITIONS[current] ?? []

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Stato:</span>
        <StatusPill tone={OFFER_STATE_TONE[current]}>{OFFER_STATE_LABELS[current]}</StatusPill>
        {allowed.map((next) => (
          <Button
            key={next}
            size="sm"
            variant="secondary"
            disabled={setState.isPending}
            onClick={() => {
              setProblem(null)
              setState.mutate(next, { onError: (error: unknown) => setProblem(toProblem(error)) })
            }}
          >
            Segna come {OFFER_STATE_LABELS[next]}
          </Button>
        ))}
      </div>
      {problem && (
        <p
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
        >
          {problem.detail}
        </p>
      )}
    </div>
  )
}
