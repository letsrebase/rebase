/** The prompts a person copies into the assistant (ORB-182): one per step, honest about
 *  what the MCP can do, which since ORB-188 includes the fiscal identity. */
import { describe, expect, it } from 'vitest'
import { INTRO_PROMPT, STEP_PROMPTS, invoicePrompt } from './prompts'

describe('the ready prompts', () => {
  it('cover every step and leave the values to fill in marked', () => {
    for (const id of ['fiscali', 'cliente', 'lavoro', 'documento'] as const) {
      expect(STEP_PROMPTS[id]).toContain('«')
      expect(STEP_PROMPTS[id].length).toBeGreaterThan(80)
    }
  })

  it('ask the assistant to set both the emitter and the fiscal profile, in one imperative voice', () => {
    // `update_emitter_profile` and `update_fiscal_profile` are on the default MCP surface
    // (ORB-188): the prompt says «imposta» and names both halves, nothing is left to type.
    expect(STEP_PROMPTS.fiscali).toMatch(/^Imposta su PigroCRM/)
    expect(STEP_PROMPTS.fiscali).toContain('Emittente:')
    expect(STEP_PROMPTS.fiscali).toContain('Profilo fiscale:')
    expect(STEP_PROMPTS.fiscali).toContain('partita IVA')
    expect(STEP_PROMPTS.fiscali).toContain('IBAN')
    expect(STEP_PROMPTS.fiscali).not.toMatch(/a mano|non la puoi fare tu|Impostazioni/)
  })

  it('ask for a preview and a confirmation before anything is written or generated', () => {
    expect(INTRO_PROMPT).toMatch(/aspetta il mio ok/)
    expect(STEP_PROMPTS.fiscali).toMatch(/chiedimi conferma/)
    expect(STEP_PROMPTS.fiscali).toMatch(/non inventare/)
    expect(STEP_PROMPTS.documento).toMatch(/anteprima/)
    expect(STEP_PROMPTS.cliente).toMatch(/non inventare/)
  })
})

describe('the invoice handoff prompt (REB-224)', () => {
  const prompt = invoicePrompt({
    documentId: 'doc-1',
    customerId: 'cust-1',
    customerName: 'Officina Verdi S.r.l.',
  })

  it('names the document, the customer and the tool, and links the PDF as the source', () => {
    expect(prompt).toContain('documento doc-1')
    expect(prompt).toContain('«Officina Verdi S.r.l.» (customer_id cust-1)')
    expect(prompt).toContain('import_issued_invoice')
    // What makes the door say «Registrata»: the invoice's `pdf_document_id`.
    expect(prompt).toContain('pdf_sorgente.document_id doc-1')
  })

  it('asks for the summary and the ok before an import that cannot be undone', () => {
    expect(prompt).toMatch(/aspetta il mio ok/)
    expect(prompt).toMatch(/non si disfa/)
    expect(prompt).toMatch(/non inventare/)
  })

  it('covers what the import needs and the PDF does not say', () => {
    // The import reads the regime and refuses without a fiscal profile.
    expect(prompt).toMatch(/profilo fiscale/)
    // Payment and transmission are not printed on the PDF: asked, not defaulted.
    expect(prompt).toMatch(/incassata/)
    expect(prompt).toMatch(/SdI/)
    // The earlier numbers of the year are real invoices, never gaps to declare.
    expect(prompt).toMatch(/numeri precedenti/)
    expect(prompt).toMatch(/non buchi da dichiarare/)
  })
})
