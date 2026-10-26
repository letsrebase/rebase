import { describe, expect, it } from 'vitest'
import {
  ACTIONS,
  STATO_LABELS,
  STATO_TONE,
  actorLabel,
  activeRateCard,
  allowsFraction,
  cellDaysLabel,
  quantitaLabel,
  statoLabel,
  statoTone,
  unitaLabel,
} from './labels'
import { rateCard } from './testing'

describe('quantitaLabel', () => {
  it('says a whole day and half a day in words, and any other size in days', () => {
    expect(quantitaLabel('1.00')).toBe('Giornata intera')
    expect(quantitaLabel('0.50')).toBe('Mezza giornata')
    expect(quantitaLabel('1.50')).toBe('1,5 giornate')
    expect(quantitaLabel('2.00')).toBe('2 giornate')
  })
})

describe('the ledger’s states', () => {
  it('has a label and a tone for every state, and tells a worked-without-approval day from a worked one', () => {
    for (const stato of Object.keys(STATO_LABELS)) {
      expect(statoLabel(stato)).toBe(STATO_LABELS[stato as keyof typeof STATO_LABELS])
      expect(statoTone(stato)).toBe(STATO_TONE[stato as keyof typeof STATO_TONE])
    }
    expect(statoLabel('lavorato_senza_approvazione')).toBe('Lavorata senza approvazione')
    expect(statoTone('lavorato_senza_approvazione')).toBe('gold')
    expect(statoTone('lavorato')).toBe('ink')
  })

  it('shows a state it does not know as it came, in a quiet tone', () => {
    expect(statoLabel('sospeso')).toBe('sospeso')
    expect(statoTone('sospeso')).toBe('muted')
  })

  it('asks for a reason on the moves that are a judgement and not on the ones that are the act', () => {
    expect(ACTIONS.contestato?.defaultReason).toBeNull()
    expect(ACTIONS.revocato?.defaultReason).toBeNull()
    expect(ACTIONS.lavorato?.defaultReason).toBe('Giornata svolta')
  })

  it('names who made a move', () => {
    expect(actorLabel({ kind: 'human', id: 'u1' })).toBe('Una persona')
    expect(actorLabel({ kind: 'agent', id: 'u1' })).toBe('Un assistente')
    expect(actorLabel({ kind: 'system' })).toBe('Il sistema')
  })
})

describe('the rate in force', () => {
  const closed = rateCard({ id: 'old', valido_da: '2026-01-01', valido_a: '2026-06-30', importo: '400.00' })
  const open = rateCard({ id: 'new', valido_da: '2026-07-01', valido_a: null, importo: '450.00' })

  it('is the card whose range contains the date, ends included', () => {
    expect(activeRateCard([closed, open], '2026-06-30')?.id).toBe('old')
    expect(activeRateCard([closed, open], '2026-07-01')?.id).toBe('new')
    expect(activeRateCard([closed, open], '2027-01-01')?.id).toBe('new')
    expect(activeRateCard([closed, open], '2025-12-31')).toBeNull()
  })

  it('allows a fraction the card lists, compared as numbers and not as strings', () => {
    expect(allowsFraction(rateCard({ frazioni_ammesse: ['1.00', '0.5'] }), '0.50')).toBe(true)
    expect(allowsFraction(rateCard({ frazioni_ammesse: ['1.00'] }), '0.50')).toBe(false)
    expect(allowsFraction(null, '0.50')).toBe(true)
  })
})

describe('cellDaysLabel', () => {
  const day = (stato: string) => ({
    id: stato,
    contract_id: 'c',
    contract_titolo: 't',
    customer_nome: 'n',
    quantita: '1.00',
    stato,
  })
  it('counts the days and says how many are worked without approval', () => {
    expect(cellDaysLabel([])).toBe('')
    expect(cellDaysLabel([day('lavorato')])).toBe(', 1 giornata')
    expect(cellDaysLabel([day('lavorato'), day('lavorato_senza_approvazione')])).toBe(
      ', 2 giornate, 1 senza approvazione',
    )
  })
})

describe('unitaLabel', () => {
  it('says the unit as the words after a rate’s figure', () => {
    expect(unitaLabel('giorno')).toBe('al giorno')
    expect(unitaLabel('ora')).toBe('all’ora')
    expect(unitaLabel('anno')).toBe('all’anno')
    expect(unitaLabel('forfait')).toBe('a forfait')
    expect(unitaLabel('settimana')).toBe('per settimana')
  })
})
