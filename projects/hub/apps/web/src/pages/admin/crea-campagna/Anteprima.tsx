import { Button } from '@rebase/ui/button'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import type { AudienceRow, Campaign } from '@/lib/api'
import { personalise, romeTime } from '@/lib/campaigns'

/** The server's paragraphs (`campaigns/render.py:_paragraphs`): a blank line splits
 *  them, a single newline stays a line break inside one. */
function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((part) => part.trim())
    .filter(Boolean)
}

/** The mail as a person gets it (`campaigns/render.py:render`), redrawn on every
 *  keystroke: the text with their name in it, the button, the signature and the
 *  unsubscribe line, word for word. */
export function Anteprima({
  oggetto,
  testo,
  bottoneTesto,
  righe,
  persona,
  onPersona,
}: {
  oggetto: string
  testo: string
  bottoneTesto: string
  /** The rows that will get the mail, to choose whose name the preview uses. */
  righe: AudienceRow[]
  persona: AudienceRow | null
  onPersona: (email: string) => void
}) {
  const body = paragraphs(personalise(testo, persona?.nome ?? null))
  return (
    <section aria-labelledby="campagna-anteprima" className="space-y-3">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <h2 id="campagna-anteprima" className="text-lg font-semibold">
          Anteprima
        </h2>
        {righe.length > 0 && (
          <div className="flex items-center gap-2">
            <Label htmlFor="campagna-persona" className="whitespace-nowrap text-muted-foreground">
              Vedi come la riceve
            </Label>
            <Select value={persona?.email ?? ''} onValueChange={onPersona}>
              <SelectTrigger id="campagna-persona" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {righe.map((row) => (
                  <SelectItem key={row.email} value={row.email}>
                    {row.nome ?? row.email}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <div className="bg-muted p-3 sm:p-4" data-testid="anteprima-mail">
        <p className="mb-3 text-sm">
          <span className="text-muted-foreground">Oggetto:</span>{' '}
          {oggetto.trim() ? oggetto : <span className="text-muted-foreground">nessun oggetto</span>}
        </p>
        <div className="space-y-4 border-2 border-foreground bg-card p-5 text-sm leading-relaxed">
          {body.length > 0 ? (
            body.map((paragraph, index) => (
              <p key={index} className="whitespace-pre-line">
                {paragraph}
              </p>
            ))
          ) : (
            <p className="text-muted-foreground">Il testo della mail compare qui mentre lo scrivi.</p>
          )}
          {bottoneTesto.trim() && (
            <span aria-hidden="true" className="inline-block bg-primary px-5 py-2.5 font-medium text-primary-foreground">
              {bottoneTesto}
            </span>
          )}
          <p className="whitespace-pre-line">{'Ivan\nrebase'}</p>
          {/* The mail's own footer, word for word (`campaigns/render.py`'s `UNSUBSCRIBE_LINE`). */}
          <p className="text-xs text-muted-foreground">
            Non vuoi più ricevere queste mail? <span className="underline">Disiscriviti</span>
          </p>
        </div>
      </div>
    </section>
  )
}

/** «Mandami una prova», and what the last one means for «Invia»: sent and still
 *  current, sent before the last change, or never sent. `dirty` counts a change the
 *  server has not stored yet, so the verdict never lags the typing. */
export function Prova({
  campaign,
  dirty,
  email,
  ready,
  pending,
  failure,
  onTest,
}: {
  campaign: Campaign | null
  dirty: boolean
  email: string | undefined
  /** Subject, text and button are all there: the server refuses a test without them. */
  ready: boolean
  pending: boolean
  failure: string | null
  onTest: () => void
}) {
  const sent = campaign?.prova_inviata_at ?? null
  const current = campaign !== null && campaign.pronta && !dirty
  const status =
    sent === null
      ? 'Prima di inviare, mandati una prova: arriva a te, con «[prova]» nell’oggetto.'
      : current
        ? `Prova inviata alle ${romeTime(sent)}${email ? ` a ${email}` : ''}. Puoi inviare.`
        : `Hai cambiato la campagna dopo la prova delle ${romeTime(sent)}: mandane un’altra.`
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant={current ? 'outline' : 'default'} onClick={onTest} disabled={!ready || pending}>
          {pending ? 'Mando la prova…' : current ? 'Mandamene un’altra' : 'Mandami una prova'}
        </Button>
      </div>
      <p className="text-sm" aria-live="polite">
        {ready ? status : 'Scrivi oggetto, testo e bottone per mandarti una prova.'}
      </p>
      {failure && (
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
    </div>
  )
}
