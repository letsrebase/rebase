import { describe, expect, it } from 'vitest'
import { LetterTextKeys, type LetterDraft } from './api'
import {
  OtherTermsGroups,
  amountForm,
  TermsFields,
  cancelDescription,
  closeDescription,
  customerComplete,
  customerLine,
  feeHint,
  draftFromFiscal,
  FISCAL_EMPTY,
  fiscalLine,
  fiscalToSave,
  expectedDaysToSend,
  LetterEmpty,
  LetterGroups,
  LetterLabels,
  LetterRequired,
  letterForm,
  olderFiscal,
  letterToSend,
  newerFiscal,
  matchOf,
  objectOf,
  payModeOf,
  previewLabels,
  frameworkHeadingId,
  refillFiscal,
  REGIME_OPTIONS,
  sendLabel,
  sendReportMessage,
  switchPayMode,
  toCustomer,
  toFiscalData,
  toLetter,
  typedAfterSave,
  typedFiscalFields,
  whatOf,
  withPayMode,
} from './contracts'

describe('the letter form (REB-387)', () => {
  it('shows every field of the letter exactly once, in a group', () => {
    const grouped = LetterGroups.flatMap((group) => group.fields)
    expect(new Set(grouped).size).toBe(grouped.length)
    expect(new Set(grouped)).toEqual(new Set(Object.keys(LetterLabels)))
    expect(grouped).toHaveLength(LetterTextKeys.length + 6)
  })

  it('sends an empty box as null, a comma decimal with a dot, and numbers as numbers', () => {
    const letter = toLetter({
      ...LetterEmpty,
      role: ' Backend developer ',
      activities: 'Le API',
      start_date: '2026-10-01',
      fee: '450,50',
      payment_days: '30',
      month_end: true,
    })
    expect(letter.role).toBe('Backend developer')
    expect(letter.results).toBeNull()
    expect(letter.data_fine).toBeNull()
    expect(letter.fee).toBe('450.50')
    expect(letter.payment_days).toBe(30)
    expect(letter.notice_days).toBeNull()
  })

  it('reads a prefill with no fee as an empty box, not the word null', () => {
    const keys = [...LetterTextKeys, 'start_date', 'data_fine', 'fee', 'payment_days', 'month_end', 'notice_days']
    const draft = Object.fromEntries(keys.map((key) => [key, null])) as unknown as LetterDraft
    const form = letterForm(draft)
    expect(form.fee).toBe('')
    expect(form.payment_days).toBe('')
    expect(form.month_end).toBe(false)
  })

  it('sends the expected days as a number beside the letter, and an empty box as null (REB-502)', () => {
    expect(expectedDaysToSend('40')).toBe(40)
    expect(expectedDaysToSend(' 40 ')).toBe(40)
    expect(expectedDaysToSend('')).toBeNull()
    expect(expectedDaysToSend('   ')).toBeNull()
    // What JSON would send for it anyway, so «Controlla e invia» never prints «NaN».
    expect(expectedDaysToSend('quaranta')).toBeNull()
    // The letter's own form has no such field, so the letter the API takes has no such key.
    expect(toLetter(LetterEmpty)).not.toHaveProperty('expected_days')
  })

  it('trims the client data', () => {
    expect(toCustomer({ customer_legal_name: ' ACME S.r.l. ', customer_vat_number: ' 01234567890', customer_registered_office: 'Milano ' })).toEqual({
      customer_legal_name: 'ACME S.r.l.',
      customer_vat_number: '01234567890',
      customer_registered_office: 'Milano',
    })
  })
})

describe('«Crea match» in three steps (REB-476)', () => {
  it('asks each field of the letter once: on «Condizioni» or in «Altre condizioni»', () => {
    const other = OtherTermsGroups.flatMap((group) => group.fields)
    expect(new Set(other).size).toBe(other.length)
    expect(other.filter((field) => TermsFields.has(field))).toEqual([])
    expect(new Set([...TermsFields, ...other])).toEqual(new Set(Object.keys(LetterLabels)))
    expect(other.filter((field) => LetterRequired.has(field))).toEqual([])
  })

  it('keeps the letter’s sections in «Altre condizioni», with the fields left alone in one «Altro»', () => {
    expect(OtherTermsGroups.every((group) => group.fields.length > 1)).toBe(true)
    expect(OtherTermsGroups.at(-1)).toEqual({
      title: 'Altro',
      fields: ['review_period', 'notice_days', 'previous_reports'],
    })
    const order = LetterGroups.flatMap((group) => group.fields)
    for (const group of OtherTermsGroups) {
      expect([...group.fields].sort((a, b) => order.indexOf(a) - order.indexOf(b))).toEqual(group.fields)
    }
  })

  it('shows an amount the way the letter writes it', () => {
    expect(amountForm('480.00')).toBe('480')
    expect(amountForm('480')).toBe('480')
    expect(amountForm('480.50')).toBe('480,50')
    expect(amountForm('480.5')).toBe('480,50')
    expect(amountForm('480.05')).toBe('480,05')
    expect(toLetter({ ...LetterEmpty, fee: amountForm('480.50') }).fee).toBe('480.50')
  })

  it('reads a fee the Italian way, through the parser every amount field shares (lib/amount.ts)', () => {
    expect(toLetter({ ...LetterEmpty, fee: '12.000' }).fee).toBe('12000.00')
    expect(toLetter({ ...LetterEmpty, fee: '1.234,50' }).fee).toBe('1234.50')
  })

  it('compares the fee with the day rate after reading both the Italian way', () => {
    const daily = withPayMode({ ...LetterEmpty, fee: '1.500' }, 'to_work_unit')
    expect(switchPayMode(daily, 'to_body', '1500').fee).toBe('')
    expect(switchPayMode({ ...daily, fee: '1.500,00' }, 'to_body', '1500').fee).toBe('')
    expect(switchPayMode({ ...daily, fee: '1,5' }, 'to_body', '1500').fee).toBe('1,5')
  })

  it('does not take the day rate for a lump sum, and puts it back for an empty day-rate fee', () => {
    const daily = withPayMode({ ...LetterEmpty, fee: '480' }, 'to_work_unit')
    expect(switchPayMode(daily, 'to_body', '480')).toMatchObject({ mode: 'to_body', unit: 'to_body', fee: '' })
    expect(switchPayMode({ ...daily, fee: '480,00' }, 'to_body', '480').fee).toBe('')
    expect(switchPayMode({ ...daily, fee: '500' }, 'to_body', '480').fee).toBe('500')
    const lump = withPayMode({ ...LetterEmpty, fee: '' }, 'to_body')
    expect(switchPayMode(lump, 'to_work_unit', '480')).toMatchObject({ mode: 'to_work_unit', fee: '480' })
    expect(switchPayMode({ ...lump, fee: '12000' }, 'to_work_unit', '480').fee).toBe('12000')
    expect(switchPayMode(daily, 'to_body', '').fee).toBe('480')
  })

  it('sets modalità and unità together, and reads anything but «a corpo» as a day rate', () => {
    const body = withPayMode(LetterEmpty, 'to_body')
    expect(body).toMatchObject({ mode: 'to_body', unit: 'to_body' })
    expect(payModeOf(body)).toBe('to_body')
    expect(payModeOf(LetterEmpty)).toBe('to_work_unit')
  })

  it('leaves out the fixed-price fields of a day-rate letter, and keeps them a corpo', () => {
    const form = {
      ...LetterEmpty,
      start_date: '2026-10-01',
      fee: '450',
      payment_days: '30',
      results: 'Il modulo',
      acceptance: 'Collaudo',
      billing_deadlines: 'A consegna',
    }
    expect(letterToSend(withPayMode(form, 'to_work_unit'))).toMatchObject({
      results: null,
      acceptance: null,
      billing_deadlines: null,
    })
    expect(letterToSend(withPayMode(form, 'to_body'))).toMatchObject({
      results: 'Il modulo',
      acceptance: 'Collaudo',
      billing_deadlines: 'A consegna',
    })
  })

  it('writes a client as one line only when all three are filled', () => {
    const client = { customer_legal_name: 'ACME S.r.l.', customer_vat_number: '01234567890', customer_registered_office: 'Milano' }
    expect(customerComplete(client)).toBe(true)
    expect(customerLine(client)).toBe('ACME S.r.l. · P.IVA 01234567890 · Milano')
    expect(customerComplete({ ...client, customer_registered_office: '  ' })).toBe(false)
  })

  it('saves tax data only when they were missing, or opened and changed', () => {
    const saved = { tax_code: 'LVLDAA85T50H501Z', vat_number: '01234567890', domicile: 'Milano', certified_email: null }
    const draft = {
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567890',
      domicile: 'Milano',
      certified_email: '',
      without_vat_number: false,
    }
    expect(fiscalLine(saved)).toBe('Salvati: CF LVLDAA85T50H501Z · P.IVA 01234567890')
    expect(fiscalToSave(saved, draft, false)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicile: ' Milano ' }, true)).toBeNull()
    expect(fiscalToSave(saved, { ...draft, domicile: 'Torino' }, true)).toEqual({ ...saved, domicile: 'Torino' })
    expect(fiscalToSave(null, { ...FISCAL_EMPTY, tax_code: 'X' }, true)).toEqual({
      tax_code: 'X',
      vat_number: '',
      domicile: '',
      certified_email: null,
    })
  })

  it('refills tax fields from a newer record, except the ones typed in', () => {
    const before = {
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567890',
      domicile: 'Milano',
      certified_email: '',
      without_vat_number: false,
    }
    const after = { ...before, domicile: 'Bari' }
    expect(typedFiscalFields(before, after)).toEqual(['domicile'])
    const newer = {
      freelancer_id: 'f1',
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567899',
      domicile: 'Torino',
      certified_email: 'ada@pec.it',
      updated_by: 'a1',
      updated_at: '2026-09-25T09:00:00Z',
    }
    expect(refillFiscal(after, newer, new Set(['domicile'] as const))).toEqual({
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567899',
      domicile: 'Bari',
      certified_email: 'ada@pec.it',
      without_vat_number: false,
    })
    expect(refillFiscal(after, newer, new Set())).toMatchObject({ domicile: 'Torino' })
  })

  it('tells a tax record read before the one the page holds', () => {
    const record = (updated_at: string) => ({
      freelancer_id: 'f1',
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567890',
      domicile: 'Milano',
      certified_email: null,
      updated_by: 'a1',
      updated_at,
    })
    const held = record('2026-09-25T10:00:00Z')
    expect(olderFiscal(record('2026-09-23T10:00:00Z'), held)).toBe(true)
    expect(olderFiscal(record('2026-09-25T10:00:00+00:00'), held)).toBe(false)
    expect(olderFiscal(record('2026-09-25T11:00:00Z'), held)).toBe(false)
    expect(olderFiscal(null, held)).toBe(true)
    expect(olderFiscal(record('2026-09-23T10:00:00Z'), null)).toBe(false)
    expect(olderFiscal(null, null)).toBe(false)
    expect(newerFiscal(record('2026-09-25T11:00:00Z'), held)).toBe(true)
    expect(newerFiscal(record('2026-09-25T10:00:00+00:00'), held)).toBe(false)
    expect(newerFiscal(record('2026-09-23T10:00:00Z'), held)).toBe(false)
    expect(newerFiscal(record('2026-09-23T10:00:00Z'), null)).toBe(true)
    expect(newerFiscal(null, held)).toBe(false)
    expect(newerFiscal(null, null)).toBe(false)
  })

  it('keeps typed only the fields changed after the save was sent', () => {
    const sent = { tax_code: 'LVLDAA85T50H501Z', vat_number: '01234567890', domicile: 'Bari', certified_email: null }
    const draft = {
      tax_code: 'LVLDAA85T50H501Z ',
      vat_number: '01234567890',
      domicile: 'Bari centro',
      certified_email: '',
      without_vat_number: false,
    }
    const typed = new Set(['tax_code', 'domicile', 'certified_email'] as const)
    expect(typedAfterSave(draft, typed, sent)).toEqual(new Set(['domicile']))
  })

  it('names the freelancer on the send button, with «ad» before an a', () => {
    expect(sendLabel('Ada')).toBe('Invia ad Ada per la firma')
    expect(sendLabel('Marco')).toBe('Invia a Marco per la firma')
    expect(sendLabel('')).toBe('Invia per la firma')
  })
})

describe('sendReportMessage (REB-390)', () => {
  const match = { letter: { number: '2026-001' } } as never
  it('says which document left and which waits', () => {
    expect(sendReportMessage({ match, sent: 'framework', mail_sent: true })).toBe(
      'Partito il contratto quadro: la lettera n. 2026-001 partirà da sola dopo la sua firma.',
    )
    expect(sendReportMessage({ match, sent: 'letter', mail_sent: true })).toBe(
      'Partita la lettera di incarico n. 2026-001.',
    )
    expect(sendReportMessage({ match, sent: null, mail_sent: null })).toBe(
      'La lettera n. 2026-001 aspetta il contratto quadro già in firma e partirà da sola dopo.',
    )
  })
  it('says when the mail did not leave, and what to do', () => {
    expect(sendReportMessage({ match, sent: 'letter', mail_sent: false })).toBe(
      'Partita la lettera di incarico n. 2026-001. La mail però non è partita: usa «Reinvia email».',
    )
  })
})

describe('whatOf (REB-407)', () => {
  it('names a framework agreement and a letter by its own number', () => {
    expect(whatOf({ kind: 'framework', number: null } as never)).toBe('del contratto quadro')
    expect(whatOf({ kind: 'letter', number: '2026-001' } as never)).toBe('della lettera n. 2026-001')
  })
})

describe('cancelDescription (REB-407)', () => {
  const match = { company_name: 'Rossi Studio', letter: { number: '2026-001', status: 'generated' } } as never
  it('names the match and the letter, and that the framework agreement stays', () => {
    expect(cancelDescription(match)).toBe(
      'Il match con Rossi Studio e la lettera n. 2026-001 diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.',
    )
  })
  it('warns that a letter already out for signature is cancelled on the signing site too', () => {
    const sent = { company_name: 'Rossi Studio', letter: { number: '2026-001', status: 'sent' } } as never
    expect(cancelDescription(sent)).toContain('il link ricevuto dal freelance smette di funzionare')
  })
})

describe('matchOf', () => {
  it('names a match by its company and its role, which tell two matches with one company apart', () => {
    expect(matchOf({ company_name: 'Rossi Studio', requested_role: 'Designer' })).toBe(
      'il match con Rossi Studio come Designer',
    )
  })
})

describe('closeDescription', () => {
  it('says the match ends as «Concluso» and that its letter and the framework agreement stay', () => {
    const match = {
      company_name: 'Rossi Studio',
      requested_role: 'Designer',
      letter: { number: '2026-001' },
    } as never
    expect(closeDescription(match)).toBe(
      'Il match con Rossi Studio come Designer diventa «Concluso»: l’incarico è finito, e dalla pagina non si riapre. La lettera n. 2026-001 e il contratto quadro restano come sono.',
    )
  })
})

describe('tax data without a partita IVA (REB-687)', () => {
  const record = {
    freelancer_id: 'f1',
    tax_code: 'LVLDAA85T50H501Z',
    vat_number: null,
    domicile: 'Milano',
    certified_email: null,
    updated_by: 'a1',
    updated_at: '2026-10-12T09:00:00Z',
  }

  it('reads a saved NULL as the ticked box, and sends the ticked box as NULL', () => {
    const draft = draftFromFiscal(record)
    expect(draft).toMatchObject({ vat_number: '', without_vat_number: true })
    expect(toFiscalData({ ...draft, vat_number: '0123' })).toMatchObject({ vat_number: null })
    expect(toFiscalData({ ...draft, without_vat_number: false, vat_number: ' 01234567890 ' })).toMatchObject({
      vat_number: '01234567890',
    })
  })

  it('says «senza partita IVA» on the saved line', () => {
    expect(fiscalLine(record)).toBe('Salvati: CF LVLDAA85T50H501Z · senza partita IVA')
  })

  it('saves nothing again for a NULL shown as the ticked box, and treats an empty field as NULL', () => {
    const draft = draftFromFiscal(record)
    expect(fiscalToSave(record, draft, true)).toBeNull()
    expect(fiscalToSave(record, { ...draft, without_vat_number: false }, true)).toBeNull()
    expect(fiscalToSave(record, { ...draft, without_vat_number: false, vat_number: '01234567890' }, true)).toEqual({
      tax_code: 'LVLDAA85T50H501Z',
      vat_number: '01234567890',
      domicile: 'Milano',
      certified_email: null,
    })
  })

  it('counts the box as typed only while the save carried another partita IVA', () => {
    const sent = { tax_code: 'X', vat_number: null, domicile: 'Bari', certified_email: null }
    const draft = { tax_code: 'X', vat_number: '', domicile: 'Bari', certified_email: '', without_vat_number: true }
    const typed = new Set(['without_vat_number'] as const)
    expect(typedAfterSave(draft, typed, sent)).toEqual(new Set())
    expect(typedAfterSave({ ...draft, without_vat_number: false }, typed, sent)).toEqual(new Set(['without_vat_number']))
  })
})

describe('the letter type (REB-687)', () => {
  it('offers the two types in the order step 1 shows them', () => {
    expect(REGIME_OPTIONS).toEqual([
      { regime: 'partita_iva', label: 'Incarico con partita IVA' },
      { regime: 'occasional', label: 'Prestazione occasionale' },
    ])
  })

  it('hints the fee as net of VAT or gross, by type', () => {
    expect(feeHint('partita_iva')).toBe('IVA esclusa')
    expect(feeHint('occasional')).toBe('lordo, senza IVA')
  })

  it('names the previews by type', () => {
    expect(previewLabels('partita_iva')).toEqual({ letter: 'Apri la lettera (PDF)', framework: 'Apri il contratto quadro (PDF)' })
    expect(previewLabels('occasional')).toEqual({
      letter: 'Apri la lettera occasionale (PDF)',
      framework: 'Apri il contratto quadro per prestazioni occasionali (PDF)',
    })
  })

  it('names each framework agreement by its type, as complement and as object', () => {
    const occasional = { kind: 'framework', number: null, regime: 'occasional' } as const
    const ordinary = { kind: 'framework', number: null, regime: 'partita_iva' } as const
    const letter = { kind: 'letter', number: '2026-001', regime: 'occasional' } as const
    expect(whatOf(occasional)).toBe('del contratto quadro per prestazioni occasionali')
    expect(whatOf(ordinary)).toBe('del contratto quadro')
    expect(objectOf(occasional)).toBe('il contratto quadro per prestazioni occasionali')
    expect(objectOf(ordinary)).toBe('il contratto quadro')
    expect(objectOf(letter)).toBe('la lettera n. 2026-001')
    expect(frameworkHeadingId('partita_iva')).toBe('contratti-quadro')
    expect(frameworkHeadingId('occasional')).toBe('contratti-quadro-occasionale')
  })
})
