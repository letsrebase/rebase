import { describe, expect, it } from 'vitest'
import type { ProblemDetail } from '@/lib/api'
import {
  addPoint,
  changeKind,
  makePrimary,
  movePoint,
  newEmailAddresses,
  pointErrors,
  pointHref,
  pointsAreSendable,
  pointsForRequest,
  pointsToBody,
  pointsToDrafts,
  removePoint,
  validatePoints,
  type ContactPointDraft,
  type PointKind,
} from './contactPoints'

function draft(
  key: string,
  kind: PointKind,
  value: string,
  is_primary = false,
): ContactPointDraft {
  return { key, kind, value, label: 'work', note: '', is_primary }
}

function primaries(points: readonly ContactPointDraft[]): string[] {
  return points.filter((point) => point.is_primary).map((point) => point.key)
}

describe('the primary flag of a kind', () => {
  it('belongs to the first point added of a kind, and not to the second', () => {
    const first = addPoint([])
    const second = addPoint(first)
    expect(first[0]?.is_primary).toBe(true)
    expect(second.map((point) => point.is_primary)).toEqual([true, false])
  })

  it('moves to the ticked row and leaves the other kinds alone', () => {
    const points = [
      draft('a', 'email', 'a@x.it', true),
      draft('b', 'email', 'b@x.it'),
      draft('p', 'phone', '0212345', true),
    ]
    expect(primaries(makePrimary(points, 'b'))).toEqual(['b', 'p'])
  })

  it('cannot be un-ticked by ticking nothing: the kind keeps one', () => {
    const points = [draft('a', 'email', 'a@x.it', true), draft('b', 'email', 'b@x.it')]
    expect(primaries(makePrimary(points, 'a'))).toEqual(['a'])
  })

  it('passes to the first remaining point of the kind when its holder is removed', () => {
    const points = [
      draft('a', 'email', 'a@x.it', true),
      draft('b', 'email', 'b@x.it'),
      draft('c', 'email', 'c@x.it'),
    ]
    expect(primaries(removePoint(points, 'a'))).toEqual(['b'])
  })

  it('stays where it is when another row of the kind is removed', () => {
    const points = [
      draft('a', 'email', 'a@x.it'),
      draft('b', 'email', 'b@x.it', true),
      draft('c', 'email', 'c@x.it'),
    ]
    expect(primaries(removePoint(points, 'a'))).toEqual(['b'])
  })

  it('follows a row that changes kind: the old kind promotes its next, the new one starts with it', () => {
    const points = [
      draft('a', 'email', 'a@x.it', true),
      draft('b', 'email', 'b@x.it'),
      draft('c', 'phone', '0212345', true),
    ]
    const changed = changeKind(points, 'a', 'phone')
    expect(changed.find((point) => point.key === 'a')?.kind).toBe('phone')
    // `b` takes over the emails; `c` keeps the phones, `a` joins them without taking the flag.
    expect(primaries(changed)).toEqual(['b', 'c'])
  })

  it('is the row’s own when it changes into a kind that had no point', () => {
    const points = [draft('a', 'email', 'a@x.it', true), draft('b', 'email', 'b@x.it')]
    expect(primaries(changeKind(points, 'b', 'linkedin'))).toEqual(['a', 'b'])
  })
})

describe('moving a row', () => {
  const points = [draft('a', 'email', 'a@x.it', true), draft('b', 'phone', '0212345', true)]

  it('swaps it with its neighbour and keeps every flag', () => {
    const moved = movePoint(points, 'b', -1)
    expect(moved.map((point) => point.key)).toEqual(['b', 'a'])
    expect(primaries(moved)).toEqual(['b', 'a'])
  })

  it('leaves the ends where they are', () => {
    expect(movePoint(points, 'a', -1).map((point) => point.key)).toEqual(['a', 'b'])
    expect(movePoint(points, 'b', 1).map((point) => point.key)).toEqual(['a', 'b'])
  })
})

describe('the request body', () => {
  it('drops blank rows, trims, turns an empty note into null and carries no id or position', () => {
    const body = pointsToBody([
      { ...draft('a', 'email', '  ada@x.it  ', true), note: '  solo lavoro  ' },
      draft('blank', 'phone', '   '),
      draft('p', 'phone', '0212345', true),
    ])
    expect(body).toEqual([
      { kind: 'email', value: 'ada@x.it', label: 'work', note: 'solo lavoro', is_primary: true },
      { kind: 'phone', value: '0212345', label: 'work', note: null, is_primary: true },
    ])
    for (const point of body) {
      expect(point).not.toHaveProperty('id')
      expect(point).not.toHaveProperty('position')
      expect(point).not.toHaveProperty('key')
    }
  })

  it('flags the first point of a kind whose primary row was blank', () => {
    const body = pointsToBody([
      draft('blank', 'email', '', true),
      draft('a', 'email', 'a@x.it'),
      draft('b', 'email', 'b@x.it'),
    ])
    expect(body.map((point) => point.is_primary)).toEqual([true, false])
  })

  it('is read back from a stored list in position order, without its ids', () => {
    const drafts = pointsToDrafts([
      { id: 'u2', kind: 'phone', value: '0212345', label: 'billing', note: null, is_primary: true, position: 1 },
      { id: 'u1', kind: 'email', value: 'a@x.it', label: 'work', note: 'x', is_primary: true, position: 0 },
    ])
    expect(drafts.map((point) => point.kind)).toEqual(['email', 'phone'])
    expect(pointsToBody(drafts)).toEqual([
      { kind: 'email', value: 'a@x.it', label: 'work', note: 'x', is_primary: true },
      { kind: 'phone', value: '0212345', label: 'billing', note: null, is_primary: true },
    ])
  })

  it('says nothing for a new record without points, and the list when it has some', () => {
    expect(pointsForRequest([draft('blank', 'email', '')], undefined)).toBeUndefined()
    expect(pointsForRequest([draft('a', 'email', 'a@x.it')], undefined)).toHaveLength(1)
  })

  it('says nothing for an edit that left the list alone, the whole list when it changed, [] when emptied', () => {
    const opened = [draft('a', 'email', 'a@x.it', true)]
    expect(pointsForRequest(opened, opened)).toBeUndefined()
    expect(pointsForRequest([draft('a', 'email', 'b@x.it', true)], opened)).toHaveLength(1)
    expect(pointsForRequest([], opened)).toEqual([])
  })
})

describe('the browser’s own checks', () => {
  it('leave a blank row alone', () => {
    expect(validatePoints([draft('a', 'email', '  ')])).toEqual({})
  })

  it('refuse an email that is not an address', () => {
    expect(validatePoints([draft('a', 'email', 'non-una-email')])).toEqual({
      a: expect.stringContaining('Indirizzo email non valido'),
    })
  })

  it('refuse a phone with fewer than six digits or more than forty characters, whatever else it holds', () => {
    const problems = validatePoints([
      draft('short', 'phone', '12345'),
      draft('long', 'phone', `1234567 ${'x'.repeat(40)}`),
      draft('ok', 'phone', '+39 02 1234567'),
      draft('office', 'phone', '+39 345 1234567 (ufficio)'),
      draft('ext', 'phone', '345-1234567 int. 2'),
    ])
    expect(Object.keys(problems)).toEqual(['short', 'long'])
  })

  it('refuse a display name or a list where an address is expected', () => {
    const problems = validatePoints([
      draft('angle', 'email', '<a@b.it>'),
      draft('named', 'email', 'Name<a@b.it>'),
      draft('two', 'email', 'a@b.it, c@d.it'),
      draft('ok', 'email', 'a@b.it'),
    ])
    expect(Object.keys(problems)).toEqual(['angle', 'named', 'two'])
  })

  it('take a bare LinkedIn handle', () => {
    expect(validatePoints([draft('a', 'linkedin', 'ada-lovelace')])).toEqual({})
  })

  it('never refuse for its shape a value the record already had', () => {
    const stored = [
      draft('1', 'email', 'ada(at)acme', true),
      draft('2', 'phone', 'int. 2', true),
      draft('3', 'linkedin', 'Ada on LinkedIn', true),
    ]
    const edited = [...stored, draft('4', 'email', 'new@acme.it')]
    expect(validatePoints(edited, stored)).toEqual({})
    // The same values are refused as soon as they are new.
    expect(Object.keys(validatePoints(stored))).toEqual(['1', '2', '3'])
    expect(pointsAreSendable(edited, stored)).toBe(true)
  })

  it('refuse a LinkedIn value that is not on linkedin.com, and take the forms the server takes', () => {
    const problems = validatePoints([
      draft('other', 'linkedin', 'https://example.com/in/ada'),
      draft('bare', 'linkedin', 'linkedin.com/in/ada'),
      draft('country', 'linkedin', 'https://it.linkedin.com/in/bob/'),
    ])
    expect(Object.keys(problems)).toEqual(['other'])
  })

  it('name the later row of the same address, compared lowercased', () => {
    expect(
      validatePoints([draft('a', 'email', 'Ada@Acme.it'), draft('b', 'email', ' ada@acme.IT ')]),
    ).toEqual({ b: expect.stringContaining('già nell’elenco') })
  })

  it('compare phones on their digits, and keep the plus apart', () => {
    expect(
      Object.keys(
        validatePoints([
          draft('a', 'phone', '+39 02 123 4567'),
          draft('b', 'phone', '+39-02-1234567'),
          draft('c', 'phone', '39021234567'),
        ]),
      ),
    ).toEqual(['b'])
  })

  it('compare a profile by its path, whatever the scheme, www or trailing slash', () => {
    expect(
      Object.keys(
        validatePoints([
          draft('a', 'linkedin', 'https://www.linkedin.com/in/Ada'),
          draft('b', 'linkedin', 'linkedin.com/in/ada/?utm_source=share'),
        ]),
      ),
    ).toEqual(['b'])
  })

  it('allow the same value in two kinds', () => {
    expect(validatePoints([draft('a', 'email', 'a@x.it'), draft('b', 'phone', '0212345')])).toEqual({})
  })
})

describe('the errors the editor shows', () => {
  const drafts = [
    draft('blank', 'email', ''),
    draft('a', 'email', 'a@x.it', true),
    draft('b', 'phone', '0212345', true),
  ]

  function refusal(field: string, reason: string, expected?: string): ProblemDetail {
    return {
      type: 'about:blank',
      title: 'Dati non validi',
      status: 422,
      code: 'validation_failed',
      detail: `customer.${field}: ${reason}`,
      entity: 'organization',
      field,
      reason,
      ...(expected ? { expected } : {}),
    }
  }

  it('maps the server’s row index onto the rows that were sent, not onto the blank one', () => {
    // `contact_points[1]` is the second row sent: the phone, since the blank row was dropped.
    const { rows, list } = pointErrors(drafts, refusal('contact_points[1]', 'numero di telefono non valido', '+39 02 1234567'), false)
    expect(rows).toEqual({ b: 'numero di telefono non valido (atteso: +39 02 1234567)' })
    expect(list).toBeNull()
  })

  it('keeps a sentence about the whole list off every row', () => {
    const { rows, list } = pointErrors(drafts, refusal('contact_points', 'al massimo 30 recapiti'), false)
    expect(rows).toEqual({})
    expect(list).toBe('al massimo 30 recapiti')
  })

  it('ignores a refusal about some other field', () => {
    expect(pointErrors(drafts, refusal('legal_name', 'manca'), false)).toEqual({ rows: {}, list: null })
  })

  it('shows its own checks only once Salva was pressed', () => {
    const bad = [draft('a', 'email', 'nope')]
    expect(pointErrors(bad, null, false).rows).toEqual({})
    expect(Object.keys(pointErrors(bad, null, true).rows)).toEqual(['a'])
  })
})

describe('the Gmail notice’s input', () => {
  it('lists only the addresses the record did not have, compared lowercased', () => {
    const opened = [draft('a', 'email', 'Ada@acme.it', true)]
    const now = [
      draft('a', 'email', 'ada@ACME.it', true),
      draft('b', 'email', 'ada@casa.it'),
      draft('c', 'phone', '0212345', true),
    ]
    expect(newEmailAddresses(now, opened)).toEqual(['ada@casa.it'])
    expect(newEmailAddresses(now, undefined)).toEqual(['ada@ACME.it', 'ada@casa.it'])
  })
})

describe('links', () => {
  it('opens an email in the mail client', () => {
    expect(pointHref({ kind: 'email', value: ' ada@acme.it ' })).toBe('mailto:ada@acme.it')
  })

  it('dials the digits and a leading plus only', () => {
    expect(pointHref({ kind: 'phone', value: '+39 (02) 123-4567' })).toBe('tel:+39021234567')
    expect(pointHref({ kind: 'phone', value: '02 1234567' })).toBe('tel:021234567')
  })

  it('links a profile as it is stored, and a value that is not an address not at all', () => {
    expect(pointHref({ kind: 'linkedin', value: 'https://www.linkedin.com/in/ada' })).toBe(
      'https://www.linkedin.com/in/ada',
    )
    expect(pointHref({ kind: 'linkedin', value: 'javascript:alert(1)' })).toBeNull()
    expect(pointHref({ kind: 'linkedin', value: 'ada' })).toBeNull()
  })
})
