import type { StatusTone } from '@/components/StatusPill'
import type { JobState, ScheduledJob } from './jobsQueries'

/**
 * How each state of a scheduled job reads on Esecuzioni (REB-764): a word for the pill and
 * a tone from the five a state may have (`StatusPill`). Mastro's `/settings` showed the
 * same four («OK», «Failed», «Stale», «Never run») and «Not configured»; the colour is
 * never the only carrier, the word says it too.
 */
export const JOB_STATES: Record<JobState, { label: string; tone: StatusTone }> = {
  ok: { label: 'In regola', tone: 'ink' },
  failed: { label: 'Non riuscita', tone: 'danger' },
  late: { label: 'In ritardo', tone: 'gold' },
  never_run: { label: 'Mai eseguita', tone: 'muted' },
  not_configured: { label: 'Non configurata', tone: 'muted' },
}

/**
 * What a failure says, by the word the API sends. The API never sends a sentence: a
 * message can quote an address, and the clock is read by every admin and by agents. The
 * words are the machine words of a skipped digest (`digest/run.py`) and the class of the
 * exception that stopped a mailbox's cycle (`gmail/errors.py`); any other word is an
 * exception's type name, shown as such.
 */
const FAILURES: Record<string, string> = {
  titolare_mancante: 'lo spazio non ha un titolare a cui scrivere',
  titolare_disattivato: 'il titolare dello spazio è disattivato',
  invio_rifiutato: 'il servizio di posta ha rifiutato l’invio',
  invio_non_configurato: 'l’invio delle email non è configurato',
  CredentialRevoked: 'il consenso Google è stato revocato: la casella va ricollegata da Impostazioni → Gmail',
  ConsentExpired: 'il consenso Google è scaduto: la casella va ricollegata da Impostazioni → Gmail',
  ScopeMissing: 'manca un’autorizzazione di Google: la casella va ri-autorizzata da Impostazioni → Gmail',
  GmailUnavailable: 'Gmail non ha risposto correttamente: riprova più tardi',
  GoogleCallFailed: 'Gmail non ha risposto correttamente: riprova più tardi',
  Conflict: 'la casella non può essere sincronizzata: il motivo è nel log del server',
}

export function failureText(failure: string): string {
  return FAILURES[failure] ?? `errore ${failure}`
}

/** What a space that has nothing for a job to do says, per job; `none` for the rest. */
const NOT_CONFIGURED: Record<string, string> = {
  'gmail-sync': 'Nessuna casella Gmail collegata: qui non c’è niente da sincronizzare.',
}

const stamp = new Intl.DateTimeFormat('it-IT', { dateStyle: 'short', timeStyle: 'short' })

export function formatStamp(value: string): string {
  return stamp.format(new Date(value))
}

/**
 * «5 minuti», «3 ore», «2 giorni»: how long ago `value` was, in the unit that reads
 * naturally. `now` is a parameter so a test, and the page's own minute tick, decide it.
 */
export function elapsed(value: string, now: number): string {
  const minutes = Math.max(Math.floor((now - new Date(value).getTime()) / 60_000), 0)
  if (minutes < 1) return 'meno di un minuto'
  if (minutes < 120) return minutes === 1 ? '1 minuto' : `${minutes} minuti`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours} ore`
  return `${Math.floor(hours / 24)} giorni`
}

/** The one sentence under a job's name: what it last did and what is wrong, if anything. */
export function jobLine(job: ScheduledJob, now: number): string {
  const lastRun = job.last_run_at
  switch (job.state) {
    case 'ok':
      return `Ultimo giro finito il ${formatStamp(lastRun!)}, ${elapsed(lastRun!, now)} fa.`
    case 'late':
      return `Nessun giro finito da ${elapsed(lastRun!, now)}: l’ultimo è del ${formatStamp(lastRun!)}. Dovrebbe girare ${job.schedule}: controlla la riga del cron sul server.`
    case 'failed': {
      const failed = `Ultimo tentativo fallito il ${formatStamp(job.last_failure_at!)}: ${failureText(job.failure || 'motivo non registrato')}.`
      return lastRun
        ? `${failed} L’ultimo giro finito è del ${formatStamp(lastRun)}.`
        : `${failed} Non è ancora finito nessun giro.`
    }
    case 'never_run':
      return `Non ha ancora girato in questo spazio. Dovrebbe girare ${job.schedule}: controlla la riga del cron sul server.`
    case 'not_configured':
      return NOT_CONFIGURED[job.key] ?? 'Qui non c’è niente da fare.'
  }
}
