import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem } from '@/lib/api'
import {
  useCreateEmailTemplate,
  useEmailVariables,
  useUpdateEmailTemplate,
  type AttachmentKind,
  type EmailTemplate,
} from './emailQueries'

const ATTACHMENTS: { kind: AttachmentKind; label: string }[] = [
  { kind: 'fattura_pdf', label: 'PDF della fattura' },
  { kind: 'registro_pdf', label: 'PDF del registro giornate' },
]

const STARTING_BODY =
  'Gentile {{cliente}},\n\nin allegato la fattura {{numero_fattura}} del {{data_fattura}} di {{importo}} ' +
  'e il registro delle giornate di {{periodo}}.\n\nCordiali saluti\n{{emittente.ragione_sociale}}'

interface Values {
  nome: string
  oggetto: string
  corpo: string
  allegati: AttachmentKind[]
}

function startingValues(template: EmailTemplate | undefined): Values {
  return template === undefined
    ? {
        nome: '',
        oggetto: 'Fattura {{numero_fattura}} - {{contratto}}',
        corpo: STARTING_BODY,
        allegati: ['fattura_pdf', 'registro_pdf'],
      }
    : {
        nome: template.nome,
        oggetto: template.oggetto,
        corpo: template.corpo,
        allegati: template.allegati,
      }
}

interface Props {
  contractId: string
  template?: EmailTemplate
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** A saved email of a contract: its name, subject and body with `{{variables}}`, and the
 *  files it carries. An unknown variable is refused by the server when it is saved, and
 *  the refusal is shown on the field it names. */
export function EmailTemplateDialog({ contractId, template, open, onOpenChange }: Props) {
  // Seeded once per opening: the caller mounts this fresh (`key`) for each template.
  const [values, setValues] = useState(() => startingValues(template))
  const variables = useEmailVariables(contractId)
  const create = useCreateEmailTemplate(contractId)
  const update = useUpdateEmailTemplate(contractId, template?.id ?? '')
  const busy = create.isPending || update.isPending
  const error = create.error ?? update.error
  const fieldError = error === null ? null : fieldErrorFrom(toProblem(error))

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((previous) => ({ ...previous, [key]: value }))
  }

  function toggle(kind: AttachmentKind, on: boolean) {
    set('allegati', on ? [...values.allegati, kind] : values.allegati.filter((k) => k !== kind))
  }

  function submit() {
    const done = () => {
      toast.success('Modello salvato')
      onOpenChange(false)
    }
    if (template === undefined) create.mutate(values, { onSuccess: done })
    else update.mutate(values, { onSuccess: done })
  }

  const errorOn = (field: string) =>
    fieldError?.field === field ? (
      <p className="text-sm text-destructive">{fieldError.message}</p>
    ) : null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{template === undefined ? 'Nuovo modello' : 'Modifica il modello'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="email-template-nome">Nome</Label>
          <Input
            id="email-template-nome"
            value={values.nome}
            onChange={(event) => set('nome', event.target.value)}
          />
          {errorOn('nome')}
        </div>
        <div className="space-y-2">
          <Label htmlFor="email-template-oggetto">Oggetto</Label>
          <Input
            id="email-template-oggetto"
            value={values.oggetto}
            onChange={(event) => set('oggetto', event.target.value)}
          />
          {errorOn('oggetto')}
        </div>
        <div className="space-y-2">
          <Label htmlFor="email-template-corpo">Testo</Label>
          <Textarea
            id="email-template-corpo"
            rows={9}
            value={values.corpo}
            onChange={(event) => set('corpo', event.target.value)}
          />
          {errorOn('corpo')}
          {fieldError !== null && fieldError.field !== 'nome' && fieldError.field !== 'oggetto' && fieldError.field !== 'corpo' && (
            <p className="text-sm text-destructive">{fieldError.message}</p>
          )}
          <p className="text-xs text-muted-foreground">
            Variabili:{' '}
            {(variables.data ?? []).map((variable) => `{{${variable.nome}}}`).join(' ')}
          </p>
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Allegati</legend>
          {ATTACHMENTS.map(({ kind, label }) => (
            <div key={kind} className="flex items-center gap-2">
              <Checkbox
                id={`email-template-${kind}`}
                checked={values.allegati.includes(kind)}
                onCheckedChange={(checked) => toggle(kind, checked === true)}
              />
              <Label htmlFor={`email-template-${kind}`} className="font-normal">
                {label}
              </Label>
            </div>
          ))}
        </fieldset>

        {error !== null && fieldError === null && (
          <p className="text-sm text-destructive">{toProblem(error).detail}</p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? 'Salvataggio…' : 'Salva'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
