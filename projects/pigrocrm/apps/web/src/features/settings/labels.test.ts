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
    expect(ruleLabel('fattura_scaduta')).toBe('Fattura scaduta')
    expect(ruleLabel('deal_fermo')).toBe('Deal fermo')
    expect(ruleLabel('offerta_inviata_richiama')).toBe('Offerta senza risposta')
    expect(ruleLabel('A1')).toBe('Offerta accettata')
    expect(ruleLabel('A2')).toBe('Offerta inviata')
  })

  it('names every template type the backend stores', () => {
    for (const [tipo, label] of [
      ['offerta', 'Offerta'],
      ['contratto', 'Contratto'],
      ['verbale', 'Verbale'],
      ['documento', 'Documento'],
      ['rapporto_ore', 'Rapporto ore'],
      ['email', 'Email'],
      ['sollecito', 'Sollecito'],
    ] as const) {
      expect(templateTypeLabel(tipo)).toBe(label)
    }
  })

  it('names the five seeded cost categories and none contains an underscore', () => {
    expect(costCategoryCodeLabel('consulenza_esterna')).toBe('Consulenza esterna')
    expect(costCategoryCodeLabel('software_licenze')).toBe('Software e licenze')
    expect(costCategoryCodeLabel('viaggi_trasferte')).toBe('Viaggi e trasferte')
    expect(costCategoryCodeLabel('materiali')).toBe('Materiali')
    expect(costCategoryCodeLabel('altro')).toBe('Altro')
    for (const label of Object.values(COST_CATEGORY_CODE_LABELS)) expect(label).not.toContain('_')
  })

  it('humanises a key it has no entry for rather than showing it raw', () => {
    expect(humaniseKey('nuova_regola')).toBe('Nuova regola')
    expect(ruleLabel('nuova_regola')).toBe('Nuova regola')
    expect(templateTypeLabel('contratto_quadro')).toBe('Contratto quadro')
    expect(costCategoryCodeLabel('formazione')).toBe('Formazione')
  })
})
