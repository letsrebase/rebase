/** REB-517: the availability mail's token leaves the URL before anything can see it.
 *  REB-521: the tab keeps it until the answer is posted, so a reload still asks. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ANSWER_LINK_KEY, PREVIOUS_ANSWER_LINK_KEY, forgetAnswerLink, readAnswerLink, stripAnswerLink } from './answer-link'

function setLocation(path: string) {
  window.history.replaceState(null, '', path)
}

afterEach(() => {
  vi.restoreAllMocks()
  setLocation('/hub/')
  forgetAnswerLink()
})

describe('stripAnswerLink', () => {
  it('takes the token and the answer out of the URL', () => {
    setLocation('/hub/team/answer?t=abc_-1&r=no')
    stripAnswerLink()
    expect(window.location.pathname).toBe('/hub/team/answer')
    expect(window.location.search).toBe('')
    expect(readAnswerLink()).toEqual({ t: 'abc_-1', r: 'no' })
  })

  it('keeps any other param', () => {
    setLocation('/hub/team/answer?t=abc&r=si&x=1')
    stripAnswerLink()
    expect(window.location.search).toBe('?x=1')
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'si' })
  })

  it('does nothing off the answer page, or on it with no token', () => {
    setLocation('/hub/team?t=abc&r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?t=abc&r=si')
    expect(readAnswerLink()).toBeNull()

    setLocation('/hub/team/answer?r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?r=si')
    expect(readAnswerLink()).toBeNull()
  })

  it('replaces the pair of an earlier link opened in the same tab', () => {
    setLocation('/hub/team/answer?t=first&r=si')
    stripAnswerLink()
    setLocation('/hub/team/answer?t=second&r=no')
    stripAnswerLink()
    expect(readAnswerLink()).toEqual({ t: 'second', r: 'no' })
  })
})

describe('the pair, held until the answer is posted', () => {
  it('is read as often as asked, and after a reload from the tab’s session storage', async () => {
    setLocation('/hub/team/answer?t=abc&r=si')
    stripAnswerLink()
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'si' })
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'si' })

    // A reload: the module starts again with nothing in memory, at a URL that no
    // longer carries the token, and `main.tsx` strips nothing.
    vi.resetModules()
    const reloaded = await import('./answer-link')
    reloaded.stripAnswerLink()
    expect(reloaded.readAnswerLink()).toEqual({ t: 'abc', r: 'si' })
    reloaded.forgetAnswerLink()
  })

  it('is gone once forgotten, from memory and from the tab', () => {
    setLocation('/hub/team/answer?t=abc&r=si')
    stripAnswerLink()
    forgetAnswerLink()
    expect(readAnswerLink()).toBeNull()
    expect(window.sessionStorage.getItem(ANSWER_LINK_KEY)).toBeNull()
  })

  it('reads nothing from a stored value that is not a pair', () => {
    for (const stored of ['not json', '{"t":1,"r":"si"}', '{"t":"","r":"si"}', 'null']) {
      window.sessionStorage.setItem(ANSWER_LINK_KEY, stored)
      expect(readAnswerLink()).toBeNull()
    }
  })

  it('lives in memory alone when the storage refuses, and throws nothing', () => {
    const refuse = () => {
      throw new DOMException('The operation is insecure.', 'SecurityError')
    }
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(refuse)
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(refuse)

    setLocation('/hub/team/answer?t=abc&r=no')
    stripAnswerLink()
    expect(window.location.search).toBe('')
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'no' })
    forgetAnswerLink()
    expect(readAnswerLink()).toBeNull()
  })
})

describe('the names of the 2026-11 English rename (mails already sent, a tab opened before)', () => {
  afterEach(() => {
    vi.useRealTimers()
    window.sessionStorage.clear()
  })

  it('takes the token out of the old path `/hub/team/risposta` too', () => {
    setLocation('/hub/team/risposta?t=abc_-1&r=si')
    stripAnswerLink()
    expect(window.location.pathname).toBe('/hub/team/risposta')
    expect(window.location.search).toBe('')
    expect(readAnswerLink()).toEqual({ t: 'abc_-1', r: 'si' })
    expect(JSON.parse(window.sessionStorage.getItem(ANSWER_LINK_KEY) ?? 'null')).toEqual({ t: 'abc_-1', r: 'si' })
  })

  it('reads the pair a tab kept under `rebase.team-risposta` once and moves it to `rebase.team-answer`', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-20T10:00:00Z'))
    window.sessionStorage.setItem(PREVIOUS_ANSWER_LINK_KEY, JSON.stringify({ t: 'old', r: 'no' }))
    expect(readAnswerLink()).toEqual({ t: 'old', r: 'no' })
    expect(window.sessionStorage.getItem(PREVIOUS_ANSWER_LINK_KEY)).toBeNull()
    expect(JSON.parse(window.sessionStorage.getItem(ANSWER_LINK_KEY) ?? 'null')).toEqual({ t: 'old', r: 'no' })
  })

  it('forgets the old key as well, and stops reading it after 30 days (2026-12-03)', () => {
    window.sessionStorage.setItem(PREVIOUS_ANSWER_LINK_KEY, JSON.stringify({ t: 'old', r: 'no' }))
    forgetAnswerLink()
    expect(window.sessionStorage.getItem(PREVIOUS_ANSWER_LINK_KEY)).toBeNull()

    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-12-03T00:00:01Z'))
    window.sessionStorage.setItem(PREVIOUS_ANSWER_LINK_KEY, JSON.stringify({ t: 'old', r: 'no' }))
    expect(readAnswerLink()).toBeNull()
    expect(window.sessionStorage.getItem(PREVIOUS_ANSWER_LINK_KEY)).toBeNull()
  })
})
