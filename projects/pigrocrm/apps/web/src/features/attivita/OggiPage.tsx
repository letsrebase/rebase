import { CalendarCheck, ChevronDown, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { FilterRow } from '@/components/FilterRow'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { Skeleton } from '@rebase/ui/skeleton'
import { useCanWrite } from '@/lib/auth'
import { formatIsoDateItalian } from '@/lib/dates'
import { AssigneeSelect } from './AssigneeSelect'
import { AttivitaDialog } from './AttivitaDialog'
import { AttivitaItem } from './AttivitaItem'
import { EVERYONE, ME } from './filters'
import { useAssignees, useAttivitaOggi, type AttivitaOggi } from './queries'

type Gruppo = AttivitaOggi['in_ritardo']

/**
 * One of the four sections of the panel (spec 2026-09-03 §5): a heading with the real
 * count, the rows the server sent, and a line pointing at the Attività page when the
 * section holds more than it shows. Every row is `AttivitaItem`, the row the record tabs
 * and the Attività page already use, so «Fatto», «Annulla», «Modifica» and the link to
 * the record behave here exactly as there.
 *
 * `showScadenza` is on wherever the section does not already say the day: «Oggi» is
 * today, and repeating it on every row would be noise.
 */
function Sezione({
  id,
  titolo,
  gruppo,
  vuoto,
  showScadenza,
  tone,
  foldable = false,
}: {
  id: string
  titolo: string
  gruppo: Gruppo
  vuoto: string
  showScadenza: boolean
  /** «In ritardo» is the one number of the product that asks for an action (spec §5),
   *  so it is the one heading drawn in the danger tint. */
  tone?: 'danger'
  foldable?: boolean
}) {
  const heading = (
    <span className="flex items-baseline gap-2">
      <span className={tone === 'danger' && gruppo.totale > 0 ? 'text-destructive' : undefined}>
        {titolo}
      </span>
      <span className="text-sm font-normal text-muted-foreground">{gruppo.totale}</span>
    </span>
  )
  const body =
    gruppo.items.length === 0 ? (
      <p className="text-sm text-muted-foreground">{vuoto}</p>
    ) : (
      <>
        <ul className="space-y-3 text-sm">
          {gruppo.items.map((item) => (
            <AttivitaItem key={item.id} attivita={item} showScadenza={showScadenza} />
          ))}
        </ul>
        {gruppo.totale > gruppo.items.length && (
          <p className="text-sm text-muted-foreground">
            Ne sono mostrate {gruppo.items.length} su {gruppo.totale}: le altre sono nella{' '}
            <Link to="/app/activities" className="underline underline-offset-4">
              pagina Attività
            </Link>
            .
          </p>
        )}
      </>
    )

  if (foldable) {
    return (
      <details open className="group space-y-3">
        <summary
          id={id}
          className="flex cursor-pointer list-none items-center gap-2 text-base font-semibold [&::-webkit-details-marker]:hidden"
        >
          <ChevronDown
            className="size-4 shrink-0 -rotate-90 transition-transform group-open:rotate-0"
            aria-hidden="true"
          />
          {heading}
        </summary>
        <div className="space-y-3 pt-3">{body}</div>
      </details>
    )
  }
  return (
    <section aria-labelledby={id} className="space-y-3">
      <h2 id={id} className="text-base font-semibold">
        {heading}
      </h2>
      {body}
    </section>
  )
}

/**
 * `/app/oggi`: what is late, what falls today, what comes in the next seven days and
 * what has no date at all, in that order (spec 2026-09-03 §5; REB-725).
 *
 * Opens on the signed-in person's own attività and says so in the select; «Di chiunque»
 * is one press away. The day is the server's `oggi` and is printed in the header, so
 * the page reads the same for somebody working in another time zone.
 */
export function OggiPage() {
  const canWrite = useCanWrite()
  const assignees = useAssignees()
  const [assignee, setAssignee] = useState(ME)
  const [creating, setCreating] = useState(false)

  // Waits for the session's user id when the choice is «mine»: asking before it is known
  // would send the whole space's list and replace it a moment later.
  const waiting = assignee === ME && assignees.me === null
  const who = assignee === EVERYONE ? undefined : assignee === ME ? (assignees.me ?? undefined) : assignee
  const panel = useAttivitaOggi({ assegnata_a: who, limit: 100 }, { enabled: !waiting })

  return (
    <>
      <PageHeader
        icon={CalendarCheck}
        title="Oggi"
        description={
          panel.data
            ? `Cosa è in ritardo e cosa scade: oggi è il ${formatIsoDateItalian(panel.data.oggi)}.`
            : 'Cosa è in ritardo e cosa scade.'
        }
        actions={
          canWrite && (
            <Button onClick={() => setCreating(true)}>
              <Plus className="mr-2 size-4" />
              Nuova attività
            </Button>
          )
        }
      >
        <FilterRow>
          <AssigneeSelect value={assignee} onChange={setAssignee} />
        </FilterRow>
      </PageHeader>

      <div className="space-y-8 px-8 pb-8">
        {panel.isError ? (
          <QueryErrorBanner error={panel.error} />
        ) : !panel.data ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <>
            <Sezione
              id="oggi-in-ritardo"
              titolo="In ritardo"
              gruppo={panel.data.in_ritardo}
              vuoto="Niente in ritardo."
              showScadenza
              tone="danger"
            />
            <Sezione
              id="oggi-oggi"
              titolo="Oggi"
              gruppo={panel.data.scadono_oggi}
              vuoto="Niente in scadenza oggi."
              showScadenza={false}
            />
            <Sezione
              id="oggi-prossimi"
              titolo="Prossimi sette giorni"
              gruppo={panel.data.prossimi_sette_giorni}
              vuoto="Niente nei prossimi sette giorni."
              showScadenza
            />
            <Sezione
              id="oggi-senza-scadenza"
              titolo="Senza scadenza"
              gruppo={panel.data.senza_scadenza}
              vuoto="Niente senza scadenza."
              showScadenza={false}
              foldable
            />
          </>
        )}
      </div>

      <AttivitaDialog open={creating} onOpenChange={setCreating} />
    </>
  )
}
