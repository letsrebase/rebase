import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { StatusPill } from '@/components/StatusPill'
import { Skeleton } from '@rebase/ui/skeleton'
import { JOB_STATES, jobLine } from './jobs'
import { useScheduledJobs } from './jobsQueries'

/**
 * Esecuzioni (REB-764): «is my instance healthy» for the four cron commands, in one screen.
 *
 * PigroCRM has no daemon: `gmail-sync`, `reminders`, `automations` and `digest` are lines
 * in the host's crontab, and until this page nobody could see whether they ran. Each
 * writes the space's row at the end of a run (`reminder_job_run`) and this lists the rows,
 * one job per card with its last finished run, its last failed attempt and whether it is
 * late. A late one is also raised as a reminder (`job_late`), which opens here.
 *
 * Read-only: a job cannot be started from the browser, because the host runs it and the
 * CRM does not (the runbooks in `docs/superpowers/notes/` say how).
 */
export function JobsPanel() {
  const jobs = useScheduledJobs()
  // The sentences say «3 minuti fa» from the moment the data was read, not from the
  // browser's clock at some other time: a tab that sat in the background comes back with a
  // refetch under way, and the old data is then read as of when it was fetched.
  const now = jobs.dataUpdatedAt

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <h2 className="font-semibold">Esecuzioni</h2>
        <p className="text-sm text-muted-foreground">
          Le operazioni che il server lancia da solo, ognuna con l’ultima volta che è andata a
          buon fine. Se una resta indietro compare anche tra gli avvisi.
        </p>
      </div>
      {/* The banner above the list, never instead of it: a refetch that fails once in the
          minute's polling must not blank what the page already knows. */}
      {jobs.isError && <QueryErrorBanner error={jobs.error} />}
      {jobs.data === undefined ? (
        jobs.isError ? null : (
          <div className="space-y-3" role="status" aria-label="Caricamento esecuzioni">
            {Array.from({ length: 4 }, (_, index) => (
              <Skeleton key={index} className="h-20 w-full" />
            ))}
          </div>
        )
      ) : (
        <ul className="space-y-3">
          {jobs.data.jobs.map((job) => {
            const state = JOB_STATES[job.state]
            return (
              <li key={job.key} className="space-y-1 border p-4" data-job={job.key}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">{job.label}</h3>
                  <StatusPill tone={state.tone}>{state.label}</StatusPill>
                </div>
                <p className="text-sm">{jobLine(job, now)}</p>
                <p className="text-xs text-muted-foreground">
                  <code>{job.command}</code> · {job.schedule}
                </p>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
