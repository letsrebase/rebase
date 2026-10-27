import { describe, expect, it } from 'vitest'
import { elapsed, failureText, jobLine } from './jobs'
import type { ScheduledJob } from './jobsQueries'

const NOW = Date.parse('2026-10-27T09:00:00Z')

function job(patch: Partial<ScheduledJob>): ScheduledJob {
  return {
    key: 'reminders',
    label: 'Passaggio dei promemoria',
    command: 'pigrocrm reminders',
    schedule: 'ogni 15 minuti',
    state: 'ok',
    last_run_at: '2026-10-27T08:55:00Z',
    last_failure_at: null,
    failure: null,
    stale_after_minutes: 30,
    ...patch,
  }
}

describe('elapsed', () => {
  it.each([
    ['2026-10-27T09:00:00Z', 'meno di un minuto'],
    ['2026-10-27T08:59:00Z', '1 minuto'],
    ['2026-10-27T08:55:00Z', '5 minuti'],
    ['2026-10-27T07:01:00Z', '119 minuti'],
    ['2026-10-27T07:00:00Z', '2 ore'],
    ['2026-10-25T10:00:00Z', '47 ore'],
    ['2026-10-25T09:00:00Z', '2 giorni'],
    ['2026-10-19T09:00:00Z', '8 giorni'],
    // A row a second ahead of the browser's clock is «meno di un minuto», never negative.
    ['2026-10-27T09:00:03Z', 'meno di un minuto'],
  ])('%s is %s before the page clock', (at, text) => {
    expect(elapsed(at, NOW)).toBe(text)
  })
})

describe('failureText', () => {
  it('words the machine words of a skipped digest and the class of a refused mailbox', () => {
    expect(failureText('invio_rifiutato')).toBe('il servizio di posta ha rifiutato l’invio')
    expect(failureText('CredentialRevoked')).toBe(
      'il consenso Google è stato revocato: la casella va ricollegata da Impostazioni → Gmail',
    )
  })

  it('shows any other word as the exception it is, and never invents a sentence', () => {
    expect(failureText('OperationalError')).toBe('errore OperationalError')
  })
})

describe('jobLine', () => {
  it('says when an ok job last finished and how long ago', () => {
    expect(jobLine(job({}), NOW)).toMatch(/^Ultimo giro finito il .+, 5 minuti fa\.$/)
  })

  it('says how long a late job has been silent and what to check', () => {
    const line = jobLine(
      job({ state: 'late', last_run_at: '2026-10-25T09:00:00Z', schedule: 'ogni giorno alle 6' }),
      NOW,
    )
    expect(line).toContain('Nessun giro finito da 2 giorni')
    expect(line).toContain('Dovrebbe girare ogni giorno alle 6: controlla la riga del cron sul server.')
  })

  it('gives the reason of a failure, and the last good run when there is one', () => {
    const failed = job({
      state: 'failed',
      last_failure_at: '2026-10-27T08:00:00Z',
      failure: 'invio_rifiutato',
    })

    expect(jobLine(failed, NOW)).toMatch(
      /^Ultimo tentativo fallito il .+: il servizio di posta ha rifiutato l’invio\. L’ultimo giro finito è del .+\.$/,
    )
    expect(jobLine({ ...failed, last_run_at: null }, NOW)).toMatch(
      /Non è ancora finito nessun giro\.$/,
    )
  })

  it('says a job that never ran has not run yet, and a space with nothing to do says so', () => {
    expect(jobLine(job({ state: 'never_run', last_run_at: null }), NOW)).toContain(
      'Non ha ancora girato in questo spazio.',
    )
    expect(
      jobLine(job({ state: 'not_configured', key: 'gmail-sync', last_run_at: null }), NOW),
    ).toBe('Nessuna casella Gmail collegata: qui non c’è niente da sincronizzare.')
  })
})
