import { describe, expect, it } from 'vitest'
import { LETTERA_TEXT_KEYS, type LetteraDraft } from './api'
import {
  ALTRE_CONDIZIONI_GROUPS,
  amountForm,
  CONDIZIONI_FIELDS,
  cancelDescription,
  closeDescription,
  clienteComplete,
  clienteLine,
  FISCAL_EMPTY,
  fiscalLine,
  fiscalToSave,
  LETTERA_EMPTY,
  LETTERA_GROUPS,
  LETTERA_LABELS,
  LETTERA_REQUIRED,
  letteraForm,
  letteraToSend,
  machineAmount,
  matchOf,
  payModeOf,
  refillFiscal,
  sendLabel,
  sendReportMessage,
  switchPayMode,
  toCliente,
  toLettera,
  typedFiscalFields,
  whatOf,
  withPayMode,
} from './contracts'

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

describe('«Crea match» in three steps (REB-476)', () => {
  it('asks each field of the letter once: on «Condizioni» or in «Altre condizioni»', () => {
    const altre = ALTRE_CONDIZIONI_GROUPS.flatMap((group) => group.fields)
    expect(new Set(altre).size).toBe(altre.length)
    expect(altre.filter((field) => CONDIZIONI_FIELDS.has(field))).toEqual([])
    expect(new Set([...CONDIZIONI_FIELDS, ...altre])).toEqual(new Set(Object.keys(LETTERA_LABELS)))
    expect(altre.filter((field) => LETTERA_REQUIRED.has(field))).toEqual([])
  })

  it('keeps the letter’s sections in «Altre condizioni», with the fields left alone in one «Altro»', () => {
    expect(ALTRE_CONDIZIONI_GROUPS.every((group) => group.fields.length > 1)).toBe(true)
    expect(ALTRE_CONDIZIONI_GROUPS.at(-1)).toEqual({
      title: 'Altro',
      fields: ['periodo_verifica', 'giorni_preavviso', 'rapporti_precedenti'],
    })
    const order = LETTERA_GROUPS.flatMap((group) => group.fields)
    for (const group of ALTRE_CONDIZIONI_GROUPS) {
      expect([...group.fields].sort((a, b) => order.indexOf(a) - order.indexOf(b))).toEqual(group.fields)
    }
  })

  it('shows an amount the way the letter writes it', () => {
    expect(amountForm('480.00')).toBe('480')
    expect(amountForm('480')).toBe('480')
    expect(amountForm('480.50')).toBe('480,50')
    expect(amountForm('480.5')).toBe('480,50')
    expect(amountForm('480.05')).toBe('480,05')
    expect(toLettera({ ...LETTERA_EMPTY, compenso: amountForm('480.50') }).compenso).toBe('480.50')
  })

  it('reads a fee the Italian way: a dot before three digits is a thousands separator, a comma the decimal', () => {
    expect(machineAmount('12.000')).toBe('12000')
    expect(machineAmount('1.500')).toBe('1500')
    expect(machineAmount('1.234,50')).toBe('1234.50')
    expect(machineAmount('480')).toBe('480')
    expect(machineAmount('480,50')).toBe('480.50')
    expect(machineAmount('480.50')).toBe('480.50')
    expect(machineAmount('480.5')).toBe('480.5')
    expect(machineAmount('0,5')).toBe('0.5')
    expect(machineAmount(' 12.000 ')).toBe('12000')
    expect(toLettera({ ...LETTERA_EMPTY, compenso: '12.000' }).compenso).toBe('12000')
    expect(toLettera({ ...LETTERA_EMPTY, compenso: '1.234,50' }).compenso).toBe('1234.50')
  })

  it('compares the fee with the day rate after reading both the Italian way', () => {
    const daily = withPayMode({ ...LETTERA_EMPTY, compenso: '1.500' }, 'a giornata')
    expect(switchPayMode(daily, 'a corpo', '1500').compenso).toBe('')
    expect(switchPayMode({ ...daily, compenso: '1.500,00' }, 'a corpo', '1500').compenso).toBe('')
    expect(switchPayMode({ ...daily, compenso: '1,5' }, 'a corpo', '1500').compenso).toBe('1,5')
  })

  it('does not take the day rate for a lump sum, and puts it back for an empty day-rate fee', () => {
    const daily = withPayMode({ ...LETTERA_EMPTY, compenso: '480' }, 'a giornata')
    expect(switchPayMode(daily, 'a corpo', '480')).toMatchObject({ modalita: 'a corpo', unita: 'a corpo', compenso: '' })
    expect(switchPayMode({ ...daily, compenso: '480,00' }, 'a corpo', '480').compenso).toBe('')
    expect(switchPayMode({ ...daily, compenso: '500' }, 'a corpo', '480').compenso).toBe('500')
    const lump = withPayMode({ ...LETTERA_EMPTY, compenso: '' }, 'a corpo')
    expect(switchPayMode(lump, 'a giornata', '480')).toMatchObject({ modalita: 'a giornata', compenso: '480' })
    expect(switchPayMode({ ...lump, compenso: '12000' }, 'a giornata', '480').compenso).toBe('12000')
    expect(switchPayMode(daily, 'a corpo', '').compenso).toBe('480')
  })

  it('sets modalità and unità together, and reads anything but «a corpo» as a day rate', () => {
    const corpo = withPayMode(LETTERA_EMPTY, 'a corpo')
    expect(corpo).toMatchObject({ modalita: 'a corpo', unita: 'a corpo' })
    expect(payModeOf(corpo)).toBe('a corpo')
    expect(payModeOf(LETTERA_EMPTY)).toBe('a giornata')
  })

  it('leaves out the fixed-price fields of a day-rate letter, and keeps them a corpo', () => {
    const form = {
      ...LETTERA_EMPTY,
      data_inizio: '2026-10-01',
      compenso: '450',
      giorni_pagamento: '30',
      risultati: 'Il modulo',
      accettazione: 'Collaudo',
      scadenze_fatturazione: 'A consegna',
    }
    expect(letteraToSend(withPayMode(form, 'a giornata'))).toMatchObject({
      risultati: null,
      accettazione: null,
      scadenze_fatturazione: null,
    })
    expect(letteraToSend(withPayMode(form, 'a corpo'))).toMatchObject({
      risultati: 'Il modulo',
      accettazione: 'Collaudo',
      scadenze_fatturazione: 'A consegna',
    })
  })

  it('writes a client as one line only when all three are filled', () => {
    const client = { cliente_ragione_sociale: 'ACME S.r.l.', cliente_piva: '01234567890', cliente_sede: 'Milano' }
    expect(clienteComplete(client)).toBe(true)
    expect(clienteLine(client)).toBe('ACME S.r.l. · P.IVA 01234567890 · Milano')
    expect(clienteComplete({ ...client, cliente_sede: '  ' })).toBe(false)
  })

  it('saves tax data only when they were missing, or opened and changed', () => {
    const saved = { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Milano', pec: null }
    const draft = { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Milano', pec: '' }
    expect(fiscalLine(saved)).toBe('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')
    expect(fiscalToSave(saved, draft, false)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicilio: ' Milano ' }, true)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicilio: 'Torino' }, true)).toEqual({ ...saved, domicilio: 'Torino' })
    expect(fiscalToSave(null, { ...FISCAL_EMPTY, codice_fiscale: 'X' }, true)).toEqual({
      codice_fiscale: 'X',
      partita_iva: '',
      domicilio: '',
      pec: null,
    })
  })

  it('refills tax fields from a newer record, except the ones typed in', () => {
    const before = { codice_fiscale: 'LVLDAA85T50H501Z', partita_iva: '01234567890', domicilio: 'Milano', pec: '' }
    const after = { ...before, domicilio: 'Bari' }
    expect(typedFiscalFields(before, after)).toEqual(['domicilio'])
    const newer = {
      freelancer_id: 'f1',
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567899',
      domicilio: 'Torino',
      pec: 'ada@pec.it',
      updated_by: 'a1',
      updated_at: '2026-09-25T09:00:00Z',
    }
    expect(refillFiscal(after, newer, new Set(['domicilio'] as const))).toEqual({
      codice_fiscale: 'LVLDAA85T50H501Z',
      partita_iva: '01234567899',
      domicilio: 'Bari',
      pec: 'ada@pec.it',
    })
    expect(refillFiscal(after, newer, new Set())).toMatchObject({ domicilio: 'Torino' })
  })

  it('names the freelancer on the send button, with «ad» before an a', () => {
    expect(sendLabel('Ada')).toBe('Invia ad Ada per la firma')
    expect(sendLabel('Marco')).toBe('Invia a Marco per la firma')
    expect(sendLabel('')).toBe('Invia per la firma')
  })
})

describe('sendReportMessage (REB-390)', () => {
  const match = { lettera: { numero: '2026-001' } } as never
  it('says which document left and which waits', () => {
    expect(sendReportMessage({ match, inviato: 'quadro', mail_inviata: true })).toBe(
      'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.',
    )
    expect(sendReportMessage({ match, inviato: 'lettera', mail_inviata: true })).toBe(
      'Partita la lettera di incarico n. 2026-001.',
    )
    expect(sendReportMessage({ match, inviato: null, mail_inviata: null })).toBe(
      'La lettera n. 2026-001 aspetta il contratto quadro già in firma e partirà da sola dopo.',
    )
  })
  it('says when the mail did not leave, and what to do', () => {
    expect(sendReportMessage({ match, inviato: 'lettera', mail_inviata: false })).toBe(
      'Partita la lettera di incarico n. 2026-001. La mail però non è partita: usa «Reinvia email».',
    )
  })
})

describe('whatOf (REB-407)', () => {
  it('names a framework agreement and a letter by its own number', () => {
    expect(whatOf({ kind: 'quadro', numero: null } as never)).toBe('del contratto quadro')
    expect(whatOf({ kind: 'lettera', numero: '2026-001' } as never)).toBe('della lettera n. 2026-001')
  })
})

describe('cancelDescription (REB-407)', () => {
  const match = { nome_azienda: 'Rossi Studio', lettera: { numero: '2026-001', stato: 'generato' } } as never
  it('names the match and the letter, and that the framework agreement stays', () => {
    expect(cancelDescription(match)).toBe(
      'Il match con Rossi Studio e la lettera n. 2026-001 diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.',
    )
  })
  it('warns that a letter already out for signature is cancelled on the signing site too', () => {
    const sent = { nome_azienda: 'Rossi Studio', lettera: { numero: '2026-001', stato: 'inviato' } } as never
    expect(cancelDescription(sent)).toContain('il link ricevuto dal freelance smette di funzionare')
  })
})

describe('matchOf', () => {
  it('names a match by its company and its role, which tell two matches with one company apart', () => {
    expect(matchOf({ nome_azienda: 'Rossi Studio', figura_richiesta: 'Designer' })).toBe(
      'il match con Rossi Studio come Designer',
    )
  })
})

describe('closeDescription', () => {
  it('says the match ends as «Concluso» and that its letter and the framework agreement stay', () => {
    const match = {
      nome_azienda: 'Rossi Studio',
      figura_richiesta: 'Designer',
      lettera: { numero: '2026-001' },
    } as never
    expect(closeDescription(match)).toBe(
      'Il match con Rossi Studio come Designer diventa «Concluso»: l’incarico è finito, e dalla pagina non si riapre. La lettera n. 2026-001 e il contratto quadro restano come sono.',
    )
  })
})
