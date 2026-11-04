import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import {
  KIND_TEXT,
  LABEL_TEXT,
  MAX_CONTACT_POINTS,
  NOTE_MAX_LENGTH,
  POINT_KINDS,
  POINT_LABELS,
  addPoint,
  changeKind,
  editPoint,
  makePrimary,
  movePoint,
  removePoint,
  type ContactPointDraft,
  type ContactPointErrors,
  type PointKind,
  type PointLabel,
} from './contactPoints'

const VALUE_PLACEHOLDER: Record<PointKind, string> = {
  email: 'nome@dominio.it',
  phone: '+39 02 1234567',
  linkedin: 'https://www.linkedin.com/in/nome',
}

const VALUE_INPUT_MODE: Record<PointKind, 'email' | 'tel' | 'url'> = {
  email: 'email',
  phone: 'tel',
  linkedin: 'url',
}

interface Props {
  points: ContactPointDraft[]
  onChange: (points: ContactPointDraft[]) => void
  errors: ContactPointErrors
}

/**
 * The list of ways to reach a person or a customer: one row per point with its kind, value,
 * label, note and a primary flag, in the order they are kept. A new row is the primary of its
 * kind when it is the first one; ticking another row moves the flag; removing the primary
 * hands it to the next point of that kind. Rows left without a value are dropped on submit,
 * so nobody has to delete a blank one by hand.
 */
export function ContactPointsEditor({ points, onChange, errors }: Props) {
  return (
    <section className="space-y-3" aria-labelledby="contact-points-heading">
      <div className="flex items-center justify-between">
        <h3 id="contact-points-heading" className="text-sm font-medium">
          Recapiti
        </h3>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={points.length >= MAX_CONTACT_POINTS}
          onClick={() => onChange(addPoint(points))}
        >
          <Plus className="mr-2 size-4" />
          Aggiungi recapito
        </Button>
      </div>

      {points.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nessun recapito: aggiungi un’email, un telefono o un profilo LinkedIn.
        </p>
      )}

      {points.map((point, index) => {
        const n = index + 1
        const error = errors.rows[point.key] ?? null
        return (
          <div
            key={point.key}
            className="space-y-3 rounded-md border p-3"
            role="group"
            aria-label={`Recapito ${n}`}
          >
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label htmlFor={`${point.key}-kind`}>
                  Tipo<span className="sr-only"> recapito {n}</span>
                </Label>
                <Select
                  value={point.kind}
                  onValueChange={(value) => onChange(changeKind(points, point.key, value as PointKind))}
                >
                  <SelectTrigger id={`${point.key}-kind`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {POINT_KINDS.map((kind) => (
                      <SelectItem key={kind} value={kind}>
                        {KIND_TEXT[kind]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${point.key}-label`}>
                  Etichetta<span className="sr-only"> recapito {n}</span>
                </Label>
                <Select
                  value={point.label}
                  onValueChange={(value) =>
                    onChange(editPoint(points, point.key, { label: value as PointLabel }))
                  }
                >
                  <SelectTrigger id={`${point.key}-label`} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {POINT_LABELS.map((label) => (
                      <SelectItem key={label} value={label}>
                        {LABEL_TEXT[label]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="col-span-2 space-y-1">
                <Label htmlFor={`${point.key}-value`}>
                  Valore<span className="sr-only"> recapito {n}</span>
                </Label>
                <Input
                  id={`${point.key}-value`}
                  value={point.value}
                  inputMode={VALUE_INPUT_MODE[point.kind]}
                  autoComplete="off"
                  placeholder={VALUE_PLACEHOLDER[point.kind]}
                  aria-invalid={error !== null}
                  aria-describedby={error === null ? undefined : `${point.key}-error`}
                  onChange={(event) => onChange(editPoint(points, point.key, { value: event.target.value }))}
                />
              </div>
              <div className="col-span-2 space-y-1">
                <Label htmlFor={`${point.key}-note`}>
                  Nota<span className="sr-only"> recapito {n}</span>
                </Label>
                <Input
                  id={`${point.key}-note`}
                  value={point.note}
                  maxLength={NOTE_MAX_LENGTH}
                  placeholder="Es. solo WhatsApp"
                  onChange={(event) => onChange(editPoint(points, point.key, { note: event.target.value }))}
                />
              </div>
            </div>

            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id={`${point.key}-primary`}
                    checked={point.is_primary}
                    onCheckedChange={(checked) => {
                      if (checked === true) onChange(makePrimary(points, point.key))
                    }}
                  />
                  <Label htmlFor={`${point.key}-primary`} className="font-normal">
                    Principale<span className="sr-only"> recapito {n}</span>
                  </Label>
                </div>
                <p
                  id={`${point.key}-error`}
                  className="text-sm text-destructive"
                  role={error ? 'alert' : undefined}
                >
                  {error}
                </p>
              </div>
              <div className="flex shrink-0 items-center">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Sposta su recapito ${n}`}
                  disabled={index === 0}
                  onClick={() => onChange(movePoint(points, point.key, -1))}
                >
                  <ArrowUp className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Sposta giù recapito ${n}`}
                  disabled={index === points.length - 1}
                  onClick={() => onChange(movePoint(points, point.key, 1))}
                >
                  <ArrowDown className="size-4" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`Rimuovi recapito ${n}`}
                  onClick={() => onChange(removePoint(points, point.key))}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          </div>
        )
      })}

      {errors.list !== null && (
        <p className="text-sm text-destructive" role="alert">
          {errors.list}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        Il principale di ogni tipo è quello che il CRM usa: l’email principale è l’indirizzo a cui
        scrive.
      </p>
    </section>
  )
}
