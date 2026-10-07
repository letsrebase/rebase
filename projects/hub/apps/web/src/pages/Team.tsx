import { Link, useSearch } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { Button } from '@rebase/ui/button'
import { TeamBuilder } from '@/components/TeamBuilder'
import { ApiError, team, type TeamProposal } from '@/lib/api'

/** Over an empty builder when `?proposta=` names nothing the API can open (REB-675): a
 *  proposal older than a day, never made, or not a public one; one sentence for all. */
export const STALE_SENTENCE =
  'Questa proposta non c’è più: descrivi di nuovo il progetto e te ne proponiamo un altro.'
/** When the read failed for any other reason (the network, a 5xx, the speed bump): the
 *  proposal may well be there, so the page says so and offers to read it again rather
 *  than telling a visitor who clicked a second ago that it is gone. */
export const UNREACHABLE_SENTENCE = 'Non riusciamo ad aprire la proposta: riprova.'
const OPENING = 'Apro la proposta…'

/** The public team builder, `/hub/team` (P-REB-43, spec § 3.1, § 4.3): no login, the
 *  wizards' chrome, the builder, and the beta box that points a company wanting the
 *  whole cloud at the company wizard, with `da=team-builder` as its origin. Since
 *  REB-675 it also opens on a proposal made elsewhere: `?proposta=<id>`, the link the
 *  landing's hero gives under its minimal result, reads the proposal back and hands it
 *  to the builder as if just proposed; `?descrizione=` only fills the box. */
export function Team() {
  const search = useSearch({ strict: false }) as { proposta?: string; descrizione?: string }
  const proposta = search.proposta
  // The read of `proposta`, keyed by it: a `null` proposal with `gone` is the API's
  // 404, without it a failure the next attempt may not repeat.
  const [opened, setOpened] = useState<{
    id: string
    proposal: TeamProposal | null
    gone: boolean
  } | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!proposta) return
    let current = true
    team.get(proposta).then(
      (proposal) => {
        if (current) setOpened({ id: proposta, proposal, gone: false })
      },
      (error: unknown) => {
        if (!current) return
        const gone = error instanceof ApiError && error.status === 404
        setOpened({ id: proposta, proposal: null, gone })
      },
    )
    return () => {
      current = false
    }
  }, [proposta, attempt])

  const read = proposta && opened?.id === proposta ? opened : null
  const opening = Boolean(proposta) && read === null
  const proposal = read?.proposal ?? undefined

  return (
    <div className="mx-auto w-full max-w-3xl space-y-10">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight">
          Descrivi il progetto, ti proponiamo il team
        </h1>
        <p className="mt-2 text-muted-foreground">
          Scrivi cosa va fatto: leggiamo i profili dei talenti di rebase e di solito in pochi secondi ti
          proponiamo un team, senza nomi, con una fascia di prezzo. Se ti convince, lo assumi da qui.
        </p>
      </div>
      {opening ? (
        <p role="status" className="text-muted-foreground">
          {OPENING}
        </p>
      ) : (
        <>
          {read && proposal === undefined && (
            <div
              role="alert"
              className="flex flex-wrap items-center gap-3 border-l-4 border-(--landing-ink) bg-card py-2 pl-4 pr-2"
            >
              <p>{read.gone ? STALE_SENTENCE : UNREACHABLE_SENTENCE}</p>
              {!read.gone && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setOpened(null)
                    setAttempt((count) => count + 1)
                  }}
                >
                  Riprova
                </Button>
              )}
            </div>
          )}
          {/* Keyed by the proposal: the builder seeds its state once, on mount. */}
          <TeamBuilder
            key={proposal?.id ?? 'empty'}
            mode="public"
            proposal={proposal}
            descrizione={search.descrizione}
          />
        </>
      )}
      <aside
        aria-label="Talent cloud"
        className="space-y-3 border-l-4 border-(--landing-ink) bg-card py-3 pl-4 pr-2"
      >
        <p>
          Il team builder è in beta e senza limiti. Le aziende che entrano nel talent cloud vedono i
          profili per nome, sfogliano tutto il cloud e chiedono i talenti direttamente.
        </p>
        <Button asChild variant="outline">
          <Link to="/companies" search={{ da: 'team-builder' }}>
            Chiedi l’accesso al talent cloud
          </Link>
        </Button>
      </aside>
    </div>
  )
}
