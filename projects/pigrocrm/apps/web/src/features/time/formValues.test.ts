import { describe, expect, it } from 'vitest'
import { toRequestBody, type TimeEntryFormValues } from './formValues'

const noArchivedKeys = () => true

function values(native: Record<string, unknown>): TimeEntryFormValues {
  return { native, custom: {} }
}

describe('toRequestBody', () => {
  it('clears a rate with null and a description with an empty string', () => {
    // The two spellings task 4B-1 made distinct, on the one form that carries both
    // kinds of column. `applied_rate` is a `numeric` -- `""` is not a decimal, so
    // before this it came back as a 422 and the rate the user was trying to remove
    // stayed in force. `null` clears it, and `TimeEntryService.update` records the
    // clearing as `origine = "absent"` rather than silently re-resolving the deal's
    // rate underneath it.
    const initial = values({ description: 'Analisi', applied_rate: '80.000000' })
    const body = toRequestBody(values({ description: '', applied_rate: '' }), {
      initial,
      locked: false,
      isRenderedCustomKey: noArchivedKeys,
    })

    expect(body).toEqual({ description: '', applied_rate: null, custom_fields: {} })
  })

  it('says nothing about a column that was blank before and is blank now', () => {
    const body = toRequestBody(values({ applied_rate: '' }), {
      initial: values({ applied_rate: null }),
      locked: false,
      isRenderedCustomKey: noArchivedKeys,
    })

    expect(body).toEqual({ custom_fields: {} })
  })
})
