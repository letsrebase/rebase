import { describe, expect, it } from 'vitest'
import { isWatched, MAIL_HINT, MAIL_LABEL, MAIL_TONE, type InviteMailState } from './inviteMail'

const NOW = new Date('2026-10-07T13:13:00Z').getTime()

describe('isWatched', () => {
  it('watches a mail that is still on its way and was sent a moment ago', () => {
    expect(isWatched({ mail_status: 'sent', mail_sent_at: '2026-10-07T13:12:31Z' }, NOW)).toBe(true)
    expect(isWatched({ mail_status: 'delayed', mail_sent_at: '2026-10-07T13:12:31Z' }, NOW)).toBe(
      true,
    )
  })

  it('stops watching a mail that has ended, whatever way', () => {
    for (const state of ['delivered', 'bounced', 'suppressed', 'complained', 'failed'] as const) {
      expect(isWatched({ mail_status: state, mail_sent_at: '2026-10-07T13:12:31Z' }, NOW)).toBe(
        false,
      )
    }
  })

  it('stops watching a mail the provider has been silent about for two minutes', () => {
    expect(isWatched({ mail_status: 'sent', mail_sent_at: '2026-10-07T13:10:59Z' }, NOW)).toBe(
      false,
    )
  })

  it('has nothing to watch on an invitation that recorded no mail', () => {
    expect(isWatched({ mail_status: null, mail_sent_at: null }, NOW)).toBe(false)
    expect(isWatched({}, NOW)).toBe(false)
  })
})

describe('what the panel says', () => {
  const states: InviteMailState[] = [
    'sent',
    'delayed',
    'delivered',
    'bounced',
    'suppressed',
    'complained',
    'failed',
  ]

  it('has a word and a tone for every state the server can answer', () => {
    for (const state of states) {
      expect(MAIL_LABEL[state]).toBeTruthy()
      expect(MAIL_TONE[state]).toBeTruthy()
    }
  })

  it('asks something of the admin only for the states that did not arrive', () => {
    expect(MAIL_HINT.sent).toBeUndefined()
    expect(MAIL_HINT.delivered).toBeUndefined()
    for (const state of ['bounced', 'suppressed', 'complained', 'failed'] as const) {
      expect(MAIL_HINT[state]).toBeTruthy()
    }
  })
})
