import { Link, useNavigate } from '@tanstack/react-router'
import { StatusPill } from '@/components/StatusPill'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { useMediaQuery } from '@rebase/ui/use-media-query'
import {
  EMPTY,
  datesWords,
  feeWords,
  stateLabel,
  stateTone,
  standingSentence,
  waitingSentence,
  type Engagement,
} from './labels'

/** The table from `md` up, one column of cards below it (the inbox's own breakpoint). */
const TABLE = '(min-width: 768px)'

/** What a hidden client's row reads in the place of a role, a fee or a client (`redacted`). */
const HIDDEN = 'Nascosto'

/** «ruolo · cliente», or «Nascosto» when the reader cannot see the client; a missing client reads as such. */
function roleAndClient(engagement: Engagement): string {
  if (engagement.redacted) return HIDDEN
  return [engagement.role ?? EMPTY, engagement.client_name ?? 'Senza cliente'].join(' · ')
}

/** The sentences a row says it waits for: one per thing a person has to do, else how it stands. */
function sentences(engagement: Engagement): { text: string; waiting: boolean }[] {
  const waiting = (engagement.waiting ?? []).map((entry) => ({
    text: waitingSentence(entry),
    waiting: true,
  }))
  if (waiting.length > 0) return waiting
  const standing = standingSentence(engagement)
  return standing === null ? [] : [{ text: standing, waiting: false }]
}

/** The gold dot of a thing that waits for a person: the brief's marker, a shape and not only a colour. */
function WaitingDot() {
  return <span aria-hidden="true" className="mt-1.5 size-1.5 shrink-0 bg-royal-gold" />
}

function Sentences({ engagement }: { engagement: Engagement }) {
  const lines = sentences(engagement)
  if (lines.length === 0) return null
  return (
    <ul className="space-y-0.5 text-xs text-muted-foreground">
      {lines.map((line) => (
        <li key={line.text} className="flex items-start gap-1.5">
          {line.waiting ? <WaitingDot /> : null}
          <span className="min-w-0 whitespace-normal break-words">{line.text}</span>
        </li>
      ))}
    </ul>
  )
}

function fee(engagement: Engagement): string {
  if (engagement.redacted) return HIDDEN
  return feeWords(engagement.fee, engagement.fee_unit, engagement.days_per_week) ?? EMPTY
}

/** The first cell: the freelancer's name (unless the page is the freelancer's own) over «ruolo · cliente». */
function Name({ engagement, showFreelancer }: { engagement: Engagement; showFreelancer: boolean }) {
  const detail = roleAndClient(engagement)
  return (
    <div className="min-w-0 max-w-96">
      {showFreelancer ? (
        <p className="truncate font-medium" title={engagement.freelancer_name ?? undefined}>
          {engagement.freelancer_name ?? EMPTY}
        </p>
      ) : null}
      <p
        className={
          showFreelancer ? 'truncate text-xs text-muted-foreground' : 'truncate font-medium'
        }
        title={detail}
      >
        {detail}
      </p>
    </div>
  )
}

function Row({ engagement, showFreelancer }: { engagement: Engagement; showFreelancer: boolean }) {
  const navigate = useNavigate()
  const open = () =>
    void navigate({
      to: '/app/engagements/$engagementId',
      params: { engagementId: engagement.id },
    })
  return (
    <TableRow
      tabIndex={0}
      className="cursor-pointer align-top focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset"
      onClick={open}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        open()
      }}
    >
      <TableCell className="py-3 align-top">
        <Link
          to="/app/engagements/$engagementId"
          params={{ engagementId: engagement.id }}
          onClick={(event) => event.stopPropagation()}
          className="block min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Name engagement={engagement} showFreelancer={showFreelancer} />
        </Link>
      </TableCell>
      <TableCell className="py-3 align-top tabular-nums">
        <p>{engagement.letter_number ?? 'Senza numero'}</p>
        <p className="text-xs text-muted-foreground">
          {datesWords(engagement.starts_on, engagement.ends_on)}
        </p>
      </TableCell>
      <TableCell className="max-w-64 whitespace-normal py-3 text-right align-top tabular-nums">
        {fee(engagement)}
      </TableCell>
      <TableCell className="max-w-80 space-y-1 whitespace-normal py-3 align-top">
        <StatusPill tone={stateTone(engagement.state)}>{stateLabel(engagement.state)}</StatusPill>
        <Sentences engagement={engagement} />
      </TableCell>
    </TableRow>
  )
}

function Card({ engagement, showFreelancer }: { engagement: Engagement; showFreelancer: boolean }) {
  const detail = roleAndClient(engagement)
  return (
    <li>
      <Link
        to="/app/engagements/$engagementId"
        params={{ engagementId: engagement.id }}
        className="block space-y-2 border bg-card p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start justify-between gap-3">
          <p className="min-w-0 break-words font-medium">
            {showFreelancer ? (engagement.freelancer_name ?? EMPTY) : detail}
          </p>
          <span className="shrink-0">
            <StatusPill tone={stateTone(engagement.state)}>{stateLabel(engagement.state)}</StatusPill>
          </span>
        </div>
        {showFreelancer ? (
          <p className="break-words text-sm text-muted-foreground">{detail}</p>
        ) : null}
        <p className="text-sm tabular-nums text-muted-foreground">
          {datesWords(engagement.starts_on, engagement.ends_on)}
        </p>
        <p className="text-sm tabular-nums">{fee(engagement)}</p>
        <Sentences engagement={engagement} />
      </Link>
    </li>
  )
}

/**
 * The engagements as the brief draws them (REB-1035): a gridded table from `md`, one column of airy
 * cards below it, and no bulk selection on either. A row says what it waits for in a sentence with a
 * gold dot, not only in a pill; a hidden client reads «Nascosto» for role, fee and client. Every
 * figure is the server's string: nothing is added up or worked out here. `showFreelancer` is false on
 * the freelancer's own tab of the registry, where the name is the page's title.
 */
export function EngagementsTable({
  engagements,
  showFreelancer = true,
}: {
  engagements: readonly Engagement[]
  showFreelancer?: boolean
}) {
  const wide = useMediaQuery(TABLE)
  if (!wide) {
    return (
      <ul aria-label="Incarichi" className="space-y-3">
        {engagements.map((engagement) => (
          <Card key={engagement.id} engagement={engagement} showFreelancer={showFreelancer} />
        ))}
      </ul>
    )
  }
  return (
    <div className="overflow-x-auto overflow-y-hidden border bg-card">
      <Table scrollLabel="Incarichi">
        <TableHeader>
          <TableRow>
            <TableHead>{showFreelancer ? 'Freelance e incarico' : 'Incarico'}</TableHead>
            <TableHead>Lettera</TableHead>
            <TableHead className="text-right">Compenso</TableHead>
            <TableHead>Stato</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {engagements.map((engagement) => (
            <Row key={engagement.id} engagement={engagement} showFreelancer={showFreelancer} />
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
