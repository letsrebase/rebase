import { useMutation } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { takeAnswerLink, type AnswerLink } from '@/lib/answer-link'
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

/** The outcome as the page's heading. After «Conferma» the heading takes the focus
 *  (`announce`), so a screen reader says what happened; a link dead on arrival is
 *  simply the page, read from the top. */
function Outcome({ esito, announce = false }: { esito: TeamAvailabilityOutcome; announce?: boolean }) {
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => {
    if (announce) heading.current?.focus()
  }, [announce])
  return (
    <div className="mx-auto max-w-xl text-center">
      <h1 ref={heading} tabIndex={-1} className="text-3xl font-semibold tracking-tight outline-none">
        {OUTCOMES[esito]}
      </h1>
    </div>
  )
}

function fromSearch(search: { t?: unknown; r?: unknown }): AnswerLink {
  return { t: typeof search.t === 'string' ? search.t : '', r: typeof search.r === 'string' ? search.r : '' }
}

/**
 * Where the two buttons of the availability mail land (REB-517, spec § 3.2),
 * `/hub/team/risposta?t=…&r=si|no`: the question and «Conferma», and nothing recorded
 * until the click, since a mail scanner opens every link in a message on its own. The
 * token and the answer come from `takeAnswerLink`, which `main.tsx` took out of the URL
 * before anything could record it, with the search params as the fallback. The post
 * answers the outcome; a link with no token or no answer, a token the API refuses as a
 * shape, and a token unknown, spent or expired are all the one sentence, never a hint
 * about which.
 */
export function TeamRisposta() {
  const search = useSearch({ strict: false }) as { t?: unknown; r?: unknown }
  // Once per mount: `takeAnswerLink` is a one-shot read (see `Entra`'s own note on
  // StrictMode and this initialiser).
  const [{ t, r }] = useState<AnswerLink>(() => takeAnswerLink() ?? fromSearch(search))
  const confirm = useMutation({ mutationFn: (body: TeamAvailabilityAnswer) => team.answer(body) })
  const risposta: TalentAnswer | null = r === 'si' || r === 'no' ? r : null
  if (!t || risposta === null) return <Outcome esito="invalid" />
  if (confirm.isSuccess) return <Outcome esito={confirm.data.esito} announce />
  if (confirm.error instanceof ApiError && confirm.error.status === 422) return <Outcome esito="invalid" announce />
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
