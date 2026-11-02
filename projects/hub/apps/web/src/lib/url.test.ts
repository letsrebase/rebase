import { describe, expect, it } from 'vitest'
import { isHttpUrl } from './url'

describe('isHttpUrl', () => {
  it.each(['https://www.linkedin.com/in/ada', 'http://example.org/a?b=1'])('lets %s open as a link', (value) => {
    expect(isHttpUrl(value)).toBe(true)
  })

  it.each(['javascript:alert(1)', 'data:text/html,x', 'ftp://example.org', 'linkedin.com/in/ada', '', 'ada'])(
    'keeps «%s» from becoming a link',
    (value) => {
      expect(isHttpUrl(value)).toBe(false)
    },
  )
})
