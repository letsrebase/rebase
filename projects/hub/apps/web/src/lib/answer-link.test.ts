/** REB-517: the availability mail's token leaves the URL before anything can see it.
 *  REB-521: the tab keeps it until the answer is posted, so a reload still asks. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ANSWER_LINK_KEY, forgetAnswerLink, readAnswerLink, stripAnswerLink } from './answer-link'

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
    setLocation('/hub/team/risposta?t=abc_-1&r=no')
    stripAnswerLink()
    expect(window.location.pathname).toBe('/hub/team/risposta')
    expect(window.location.search).toBe('')
    expect(readAnswerLink()).toEqual({ t: 'abc_-1', r: 'no' })
  })

  it('keeps any other param', () => {
    setLocation('/hub/team/risposta?t=abc&r=si&x=1')
    stripAnswerLink()
    expect(window.location.search).toBe('?x=1')
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'si' })
  })

  it('does nothing off the answer page, or on it with no token', () => {
    setLocation('/hub/team?t=abc&r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?t=abc&r=si')
    expect(readAnswerLink()).toBeNull()

    setLocation('/hub/team/risposta?r=si')
    stripAnswerLink()
    expect(window.location.search).toBe('?r=si')
    expect(readAnswerLink()).toBeNull()
  })

  it('replaces the pair of an earlier link opened in the same tab', () => {
    setLocation('/hub/team/risposta?t=first&r=si')
    stripAnswerLink()
    setLocation('/hub/team/risposta?t=second&r=no')
    stripAnswerLink()
    expect(readAnswerLink()).toEqual({ t: 'second', r: 'no' })
  })
})

describe('the pair, held until the answer is posted', () => {
  it('is read as often as asked, and after a reload from the tab’s session storage', async () => {
    setLocation('/hub/team/risposta?t=abc&r=si')
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
    setLocation('/hub/team/risposta?t=abc&r=si')
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

    setLocation('/hub/team/risposta?t=abc&r=no')
    stripAnswerLink()
    expect(window.location.search).toBe('')
    expect(readAnswerLink()).toEqual({ t: 'abc', r: 'no' })
    forgetAnswerLink()
    expect(readAnswerLink()).toBeNull()
  })
})
