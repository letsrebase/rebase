import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// The privacy page's section on the team builder (REB-515, REB-516, REB-841): what it
// must say about Anthropic and the sources read, and that the data may leave the EU
// (the API runs global inference).
const html = readFileSync(resolve(__dirname, 'privacy.html'), 'utf8')

function section(title: string): string {
  const start = html.indexOf(title)
  expect(start, `the section «${title}» exists`).toBeGreaterThan(-1)
  const rest = html.slice(start + title.length)
  const next = rest.search(/<h2\b/)
  // The page wraps its lines: read the section as one run of words.
  return (next === -1 ? rest : rest.slice(0, next)).replace(/\s+/g, ' ')
}

describe('the privacy page on the team builder', () => {
  const text = section('La tua scheda e il team builder')

  it('names Anthropic as the processor and says the data trains no model', () => {
    expect(text).toContain('Anthropic tratta questi dati per conto nostro')
    expect(text).toContain('non li usa per addestrare i propri modelli')
    expect(text).toContain('https://www.anthropic.com/privacy')
  })

  it("says what is read and sent: the CV, the role, GitHub, the links and the hub's facts, never LinkedIn or the rate", () => {
    expect(text).toContain('il testo del tuo CV')
    expect(text).toContain('il ruolo che hai indicato nel profilo')
    expect(text).toContain('senza il nome del cliente')
    expect(text).toContain('La tariffa e il modo in cui lavori non servono a scriverla')
    expect(text).toContain('i repository pubblici')
    expect(text).toContain('le pagine pubbliche dei link')
    expect(text).toContain('Non leggiamo LinkedIn')
    expect(text).toContain('dietro un accesso')
    expect(text).toContain('mai la tariffa stessa')
  })

  it('says plainly that the data may leave the EU, and the safeguard', () => {
    expect(text).toContain("possono uscire dall'Unione Europea")
    expect(text).toContain('clausole contrattuali standard')
    expect(text).toContain('https://www.anthropic.com/legal/data-processing-addendum')
  })

  it('names no other model provider: OpenAI and Google are off (hub AGENTS.md)', () => {
    expect(text).not.toMatch(/OpenAI|Gemini|Google/)
    expect(text).toContain('Non mandiamo questi dati a nessun altro fornitore di modelli')
  })

  it('says what is stored, for how long, and how to object', () => {
    expect(text).toContain('un\'impronta (hash)')
    expect(text).toContain('una volta a settimana')
    expect(text).toContain('quando lo togli')
    expect(text).toContain('quando cancelli il profilo')
    expect(text).toContain('Ti puoi opporre in ogni momento')
    expect(text).toContain('ciao@letsrebase.com')
  })
})

// The section Google's review asked for on 2026-09-29 (REB-604, REB-453): the
// mechanisms that protect Google user data, each one as the code has it, and the
// commitments named as commitments.
describe('the privacy page on how Google data is protected', () => {
  const text = section('Come proteggiamo i dati di Google')

  it('names the encryption at rest of the token, and that the messages are not encrypted', () => {
    expect(text).toContain('AES-256-GCM')
    expect(text).toContain('chiave che sta fuori dal database')
    expect(text).toContain('non viene mai scritto da nessuna parte')
    expect(text).toContain('Cifriamo il token, non il testo dei messaggi')
  })

  it('names TLS in transit, where the data is, and who can read', () => {
    expect(text).toContain('HTTPS (TLS)')
    expect(text).toContain('database del tuo spazio')
    expect(text).toContain('Helsinki')
    expect(text).toContain('hash (Argon2)')
    expect(text).toContain('token personale che crei tu')
    // An assistant's token reads the conversations without «accesso completo»: that
    // switch gates the attachment and mailbox-query tools only (pigrocrm_mcp, privileged.py).
    expect(text).not.toContain('solo se sei tu a dargli l’accesso completo')
  })

  it('says what stays when bodies are off, and what a Drive read outside the roots does', () => {
    expect(text).toContain('anteprima di poche righe')
    expect(text).toContain('corpi già salvati restano')
    expect(text).toContain('non viene mai scaricato')
  })

  it('says how long the data stays, how it is deleted, and what a breach triggers', () => {
    expect(text).toContain('scolleghi Gmail o Google Drive')
    expect(text).toContain('entro trenta giorni')
    expect(text).toContain('settantadue ore')
    expect(text).toContain('articoli 33 e 34')
    // Article 33 binds the controller: for the space's data that is the customer.
    expect(text).toContain('sei tu il titolare')
    // Article 33 binds the controller only when the breach is likely to pose a risk.
    expect(text).toContain('quando la violazione può comportare un rischio')
    expect(text).toContain('ne teniamo trenta')
  })

  it('tells a commitment from a mechanism', () => {
    expect(text.match(/questo è un impegno/g)?.length).toBe(3)
  })
})

describe('the privacy page on your rights', () => {
  const text = section('I tuoi diritti')

  it('words them for hub members and for PigroCRM spaces separately', () => {
    expect(text).toContain('cancellare il tuo CV, e con lui la descrizione anonima')
    expect(text).toContain('togliere il tuo profilo')
    expect(text).toContain('il resto resta archiviato')
    expect(text).toContain('chiudere lo spazio li cancella')
    expect(text).not.toContain('cancellarli insieme allo spazio')
    expect(text).toContain('Garante')
    expect(text).toContain('rispondiamo entro trenta giorni')
  })
})
