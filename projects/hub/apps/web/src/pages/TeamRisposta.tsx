import { useMutation } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { Button } from '@rebase/ui/button'
import { ApiError, team, type TalentAnswer, type TeamAvailabilityAnswer, type TeamAvailabilityOutcome } from '@/lib/api'

/** The question and the three outcomes, spec § 3.2's own sentences. */
const QUESTIONS: Record<TalentAnswer, string> = {
  si: 'Vuoi confermare che sei disponibile?',
  no: 'Vuoi confermare che non sei disponibile?',
}
const OUTCOMES: Record<TeamAvailabilityOutcome, string> = {
  si: 'Grazie, abbiamo registrato la tua disponibilità',
  no: 'Grazie, abbiamo registrato che non sei disponibile',
  invalid: 'Questo link non è più valido',
}

function Outcome({ esito }: { esito: TeamAvailabilityOutcome }) {
  return (
    <div role="status" className="mx-auto max-w-xl text-center">
      <h1 className="text-3xl font-semibold tracking-tight">{OUTCOMES[esito]}</h1>
    </div>
  )
}

/**
 * Where the two buttons of the availability mail land (REB-517, spec § 3.2),
 * `/hub/team/risposta?t=…&r=si|no`: the question and «Conferma», and nothing recorded
 * until the click, since a mail scanner opens every link in a message on its own. The
 * post answers the outcome; a link with no token or no answer, a token the API refuses
 * as a shape, and a token unknown, spent or expired are all the one sentence, never a
 * hint about which.
 */
export function TeamRisposta() {
  const { t, r } = useSearch({ strict: false }) as { t?: unknown; r?: unknown }
  const confirm = useMutation({ mutationFn: (body: TeamAvailabilityAnswer) => team.answer(body) })
  const risposta: TalentAnswer | null = r === 'si' || r === 'no' ? r : null
  if (typeof t !== 'string' || !t || risposta === null) return <Outcome esito="invalid" />
  if (confirm.isSuccess) return <Outcome esito={confirm.data.esito} />
  if (confirm.error instanceof ApiError && confirm.error.status === 422) return <Outcome esito="invalid" />
  return (
    <div className="mx-auto max-w-xl space-y-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">{QUESTIONS[risposta]}</h1>
      <Button type="button" disabled={confirm.isPending} onClick={() => confirm.mutate({ t, risposta })}>
        Conferma
      </Button>
      {confirm.isError && (
        <p role="alert" className="text-sm text-destructive">
          {confirm.error instanceof ApiError ? confirm.error.message : 'Non ci siamo riusciti. Riprova tra un minuto.'}
        </p>
      )}
    </div>
  )
}
