import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import type { CampaignAction, CampaignMeta } from '@/lib/api'
import { ActionLabels, CAMPAIGN_MAX_LENGTH } from '@/lib/campaigns'
import { linkProblem, metaOptions, type CampaignForm } from './form'

/** Messaggio (REB-526): what the mail says and where its button leads. The preview
 *  beside it follows every keystroke. «Dove porta» goes through `onMeta` and the link's
 *  address through `onLink`, not `onChange`: «Un link» brings its own action, and
 *  choosing where the button leads is not writing the mail (REB-530). */
export function Message({
  form,
  onChange,
  onMeta,
  onLink,
  linkRefused = null,
}: {
  form: CampaignForm
  onChange: (patch: Partial<CampaignForm>) => void
  onMeta: (meta: CampaignMeta) => void
  onLink: (url: string) => void
  /** The server's sentence when it refused this very address (a 422 on `button_url`). */
  linkRefused?: string | null
}) {
  const link = form.buttonMeta === 'link'
  // Said while typing, but not before anything is typed: an empty field shows what to
  // write in it, and the header says the draft waits for it.
  const problem = link ? ((form.buttonUrl.trim() !== '' ? linkProblem(form) : null) ?? linkRefused) : null
  return (
    <section aria-labelledby="campagna-messaggio" className="space-y-4">
      <h2 id="campagna-messaggio" className="text-lg font-semibold">
        Messaggio
      </h2>
      <div className="space-y-1.5">
        <Label htmlFor="campagna-oggetto">Oggetto</Label>
        <Input
          id="campagna-oggetto"
          maxLength={CAMPAIGN_MAX_LENGTH.subject}
          value={form.subject}
          onChange={(event) => onChange({ subject: event.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="campagna-testo">Testo</Label>
        <Textarea
          id="campagna-testo"
          rows={10}
          maxLength={CAMPAIGN_MAX_LENGTH.text}
          aria-describedby="campagna-testo-aiuto"
          value={form.text}
          onChange={(event) => onChange({ text: event.target.value })}
        />
        <p id="campagna-testo-aiuto" className="text-xs text-muted-foreground">
          {'{nome}'} diventa il nome della persona. Una riga vuota separa i paragrafi.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-testo">Testo del bottone</Label>
          <Input
            id="campagna-bottone-testo"
            maxLength={CAMPAIGN_MAX_LENGTH.button_text}
            value={form.buttonText}
            onChange={(event) => onChange({ buttonText: event.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-meta">Dove porta</Label>
          <Select value={form.buttonMeta} onValueChange={(value) => onMeta(value as CampaignMeta)}>
            <SelectTrigger id="campagna-bottone-meta" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {metaOptions(form).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {link && (
        <div className="space-y-1.5">
          <Label htmlFor="campagna-bottone-url">Indirizzo del link</Label>
          <Input
            id="campagna-bottone-url"
            type="url"
            placeholder="https://lu.ma/…"
            maxLength={CAMPAIGN_MAX_LENGTH.button_url}
            aria-invalid={problem !== null}
            aria-describedby="campagna-bottone-url-aiuto"
            value={form.buttonUrl}
            onChange={(event) => onLink(event.target.value)}
          />
          <p id="campagna-bottone-url-aiuto" className={problem ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
            {problem ?? 'Una pagina fuori dal hub: un evento su Luma, un gruppo WhatsApp, un modulo. Deve iniziare con https://.'}
          </p>
        </div>
      )}
      {link ? (
        // Of a page the hub does not own, the click is all it sees (REB-530).
        <div className="space-y-1">
          <p className="text-sm">
            <span className="text-muted-foreground">Cosa misuriamo:</span> {ActionLabels.click}
          </p>
          <p className="text-xs text-muted-foreground">Di una pagina fuori dal hub vediamo solo il clic sul bottone.</p>
        </div>
      ) : form.source === 'filters' ? (
        <div className="max-w-sm space-y-1.5">
          <Label htmlFor="campagna-azione">Cosa misuriamo</Label>
          <Select value={form.action} onValueChange={(value) => onChange({ action: value as CampaignAction })}>
            <SelectTrigger id="campagna-azione" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(ActionLabels)
                .filter(([value]) => value !== 'pigro_customer' && value !== 'click')
                .map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">Chi l’ha già fatto quando la mail parte viene saltato.</p>
        </div>
      ) : (
        // A state brings its own action (spec § 1), and a `list` keeps its parent's
        // (Task 6): a sentence, not a menu that cannot be opened, and nothing before a
        // state is picked.
        (form.source === 'list' || form.journeyStatus !== null) && (
          <p className="text-sm">
            <span className="text-muted-foreground">Cosa misuriamo:</span> {ActionLabels[form.action]}
          </p>
        )
      )}
    </section>
  )
}
