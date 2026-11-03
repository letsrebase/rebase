import { describe, expect, it } from 'vitest'
import {
  COST_CATEGORY_CODE_LABELS,
  costCategoryCodeLabel,
  humaniseKey,
  ruleLabel,
  templateTypeLabel,
} from './labels'

describe('the settings label maps', () => {
  it('names every rule the daily pass and the two event rules write', () => {
    expect(ruleLabel('overdue_invoice')).toBe('Fattura scaduta')
    expect(ruleLabel('stalled_deal')).toBe('Deal fermo')
    expect(ruleLabel('sent_offer_follow_up')).toBe('Offerta senza risposta')
    expect(ruleLabel('A1')).toBe('Offerta accettata')
    expect(ruleLabel('A2')).toBe('Offerta inviata')
  })

  it('names every template type the backend stores', () => {
    for (const [type, label] of [
      ['offer', 'Offerta'],
      ['contract', 'Contratto'],
      ['minutes', 'Verbale'],
      ['document', 'Documento'],
      ['hours_report', 'Rapporto ore'],
      ['email', 'Email'],
      ['payment_reminder', 'Sollecito'],
    ] as const) {
      expect(templateTypeLabel(type)).toBe(label)
    }
  })

  it('names the five seeded cost categories and none contains an underscore', () => {
    expect(costCategoryCodeLabel('external_consulting')).toBe('Consulenza esterna')
    expect(costCategoryCodeLabel('software_licenses')).toBe('Software e licenze')
    expect(costCategoryCodeLabel('travel')).toBe('Viaggi e trasferte')
    expect(costCategoryCodeLabel('materials')).toBe('Materiali')
    expect(costCategoryCodeLabel('other')).toBe('Altro')
    for (const label of Object.values(COST_CATEGORY_CODE_LABELS)) expect(label).not.toContain('_')
  })

  it('humanises a key it has no entry for rather than showing it raw', () => {
    expect(humaniseKey('nuova_regola')).toBe('Nuova regola')
    expect(ruleLabel('nuova_regola')).toBe('Nuova regola')
    expect(templateTypeLabel('contratto_quadro')).toBe('Contratto quadro')
    expect(costCategoryCodeLabel('formazione')).toBe('Formazione')
  })

  it('does not read a key named like an object property as a label', () => {
    expect(ruleLabel('function Object() { [native code] }')).toBe(humaniseKey('function Object() { [native code] }'))
    expect(templateTypeLabel('[object Object]')).toBe(humaniseKey('[object Object]'))
    expect(costCategoryCodeLabel('function toString() { [native code] }')).toBe(humaniseKey('function toString() { [native code] }'))
  })
})
