import { describe, expect, it } from 'vitest'
import { LETTERA_TEXT_KEYS, type LetteraDraft } from './api'
import { LETTERA_EMPTY, LETTERA_GROUPS, LETTERA_LABELS, letteraForm, toCliente, toLettera } from './contracts'

describe('the letter form (REB-387)', () => {
  it('shows every field of the letter exactly once, in a group', () => {
    const grouped = LETTERA_GROUPS.flatMap((group) => group.fields)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect(new Set(grouped)).toEqual(new Set(Object.keys(LETTERA_LABELS)))
    expect(grouped).toHaveLength(LETTERA_TEXT_KEYS.length + 6)
  })

  it('sends an empty box as null, a comma decimal with a dot, and numbers as numbers', () => {
    const lettera = toLettera({
      ...LETTERA_EMPTY,
      ruolo: ' Backend developer ',
      attivita: 'Le API',
      data_inizio: '2026-10-01',
      compenso: '450,50',
      giorni_pagamento: '30',
      fine_mese: true,
    })
    expect(lettera.ruolo).toBe('Backend developer')
    expect(lettera.risultati).toBeNull()
    expect(lettera.data_fine).toBeNull()
    expect(lettera.compenso).toBe('450.50')
    expect(lettera.giorni_pagamento).toBe(30)
    expect(lettera.giorni_preavviso).toBeNull()
  })

  it('reads a prefill with no fee as an empty box, not the word null', () => {
    const keys = [...LETTERA_TEXT_KEYS, 'data_inizio', 'data_fine', 'compenso', 'giorni_pagamento', 'fine_mese', 'giorni_preavviso']
    const draft = Object.fromEntries(keys.map((key) => [key, null])) as unknown as LetteraDraft
    const form = letteraForm(draft)
    expect(form.compenso).toBe('')
    expect(form.giorni_pagamento).toBe('')
    expect(form.fine_mese).toBe(false)
  })

  it('trims the client data', () => {
    expect(toCliente({ cliente_ragione_sociale: ' ACME S.r.l. ', cliente_piva: ' 01234567890', cliente_sede: 'Milano ' })).toEqual({
      cliente_ragione_sociale: 'ACME S.r.l.',
      cliente_piva: '01234567890',
      cliente_sede: 'Milano',
    })
  })
})
