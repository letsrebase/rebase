import { Link } from '@tanstack/react-router'
import { Bell, ChevronDown, Clock } from 'lucide-react'
import { useState } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@rebase/ui/dropdown-menu'
import { Skeleton } from '@rebase/ui/skeleton'
import { toast } from '@rebase/ui/sonner'
import { toProblem } from '@/lib/api'
import { useLegalEntity } from '@/lib/legalEntity'
import {
  SEVERITIES,
  SNOOZE_OPTIONS,
  handledLine,
  isOpen,
  subjectOf,
  type Reminder,
} from './fields'
import { recordLink } from './links'
import { useAcknowledge, useReminders, useSnooze } from './queries'

/**
 * The Avvisi page (REB-734): what needs the caller's attention, by severity.
 *
 * The list is the API's own, recomputed from the data on every read, so a reminder whose
 * cause was fixed is simply not in it. What the page decides is only what to show of the
 * ones that are: those still `open` sit in a section per severity, severest first, each
 * row opening the record it is about and offering «Visto» and a postponement; those the
 * caller already acknowledged or snoozed leave the list and wait, collapsed, in «Già
 * gestiti», with what was done, so a snoozed reminder is never lost, only out of the way
 * until its day. They come back on their own: by the API's rule (a higher severity) or by
 * the clock, and the next read says so.
 */
export function AvvisiPage() {
  const reminders = useReminders()
  const [showHandled, setShowHandled] = useState(false)
  const list = reminders.data ?? []
  const open = list.filter(isOpen)
  const handled = list.filter((reminder) => !isOpen(reminder))

  return (
    <>
      <PageHeader
        icon={Bell}
        title="Avvisi"
        description="Quello che scade o va storto, dal più urgente."
      />
      <div className="space-y-8 px-8 py-6">
        {reminders.isError ? (
          <QueryErrorBanner error={reminders.error} />
        ) : reminders.isLoading ? (
          <div className="space-y-3" role="status" aria-label="Caricamento avvisi">
            {Array.from({ length: 3 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        ) : (
          <>
            {open.length === 0 ? (
              <div className="border border-dashed px-6 py-12 text-center">
                <h2 className="font-semibold">Nessun avviso</h2>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  Quando qualcosa scade o va storto lo trovi qui, dal più urgente. Quello che
                  hai già visto o rimandato resta fuori dall’elenco.
                </p>
              </div>
            ) : (
              SEVERITIES.map((severity) => {
                const rows = open.filter((reminder) => reminder.severity === severity.id)
                if (rows.length === 0) return null
                return (
                  <section key={severity.id} aria-labelledby={`avvisi-${severity.id}`}>
                    <h2
                      id={`avvisi-${severity.id}`}
                      className="mb-2 flex items-baseline gap-2 text-sm font-semibold"
                    >
                      {severity.label}
                      <span className="tabular-nums font-normal text-muted-foreground">
                        {rows.length}
                      </span>
                    </h2>
                    <ul className="border-t">
                      {rows.map((reminder) => (
                        <ReminderRow key={reminder.key} reminder={reminder} />
                      ))}
                    </ul>
                  </section>
                )
              })
            )}

            {handled.length > 0 && (
              <section aria-labelledby="avvisi-gestiti">
                <h2 id="avvisi-gestiti" className="text-sm font-semibold">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="-ml-2.5"
                    aria-expanded={showHandled}
                    aria-controls="avvisi-gestiti-elenco"
                    onClick={() => setShowHandled((value) => !value)}
                  >
                    <ChevronDown
                      className={showHandled ? 'size-3.5' : 'size-3.5 -rotate-90'}
                      aria-hidden
                    />
                    Già gestiti
                    <span className="tabular-nums font-normal text-muted-foreground">
                      {handled.length}
                    </span>
                  </Button>
                </h2>
                {showHandled && (
                  <ul id="avvisi-gestiti-elenco" className="mt-2 border-t">
                    {handled.map((reminder) => (
                      <ReminderRow key={reminder.key} reminder={reminder} />
                    ))}
                  </ul>
                )}
              </section>
            )}
          </>
        )}
      </div>
    </>
  )
}

function ReminderRow({ reminder }: { reminder: Reminder }) {
  const severity = SEVERITIES.find((candidate) => candidate.id === reminder.severity)
  const link = recordLink(reminder)
  const { select } = useLegalEntity()
  const open = isOpen(reminder)
  const subject = subjectOf(reminder)

  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b px-2 py-4">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {severity && <StatusPill tone={severity.tone}>{severity.single}</StatusPill>}
          <span>{reminder.label}</span>
        </div>
        <p className="font-medium">
          {link ? (
            <Link
              {...link}
              // A fiscal reminder is about one azienda and the dashboard shows the one the
              // sidebar selected: opening it selects the azienda it is about.
              onClick={reminder.entity === 'fiscal' ? () => select(reminder.entity_id) : undefined}
              className="underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              {subject}
            </Link>
          ) : (
            subject
          )}
        </p>
        <p className="text-sm text-muted-foreground">{reminder.detail}</p>
        {!open && <p className="text-xs text-muted-foreground">{handledLine(reminder)}</p>}
      </div>
      {open && <ReminderActions reminder={reminder} />}
    </li>
  )
}

function ReminderActions({ reminder }: { reminder: Reminder }) {
  const acknowledge = useAcknowledge()
  const snooze = useSnooze()
  const busy = acknowledge.isPending || snooze.isPending
  const fail = (error: unknown) => toast.error(toProblem(error).detail)

  return (
    <div className="flex shrink-0 items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() => acknowledge.mutate(reminder, { onError: fail })}
      >
        Visto
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" disabled={busy}>
            <Clock aria-hidden />
            Rimanda
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {SNOOZE_OPTIONS.map((option) => (
            <DropdownMenuItem
              key={option.id}
              onSelect={() => snooze.mutate({ reminder, until: option.id }, { onError: fail })}
            >
              {option.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
