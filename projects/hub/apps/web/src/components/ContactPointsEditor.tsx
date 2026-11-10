import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@rebase/ui/select'
import {
  CONTACT_KINDS,
  CONTACT_KIND_NAMES,
  CONTACT_LABELS,
  CONTACT_LABEL_NAMES,
  CONTACT_NOTE_MAX,
  blankDraft,
  problemOf,
  type ContactKind,
  type ContactLabel,
  type PointDraft,
} from '@/lib/contactPoints'

/**
 * The list of a person's extra contact points, edited whole (REB-944): a kind, a value, a
 * label (lavoro, personale, amministrazione, altro), a free note, and for a phone or a
 * profile whether it is the primary one. The address the person signs in with is shown on
 * top, read only: it is their identity, so it is neither editable nor removable here, and an
 * extra address is never primary (the sign-in address is).
 *
 * Controlled: the caller keeps the drafts (`draftsFrom`) and sends `toWrite(drafts)`. A row
 * left without a value is dropped on save, so «Aggiungi» costs nothing to undo.
 */
export function ContactPointsEditor({
  idPrefix,
  login,
  drafts,
  onChange,
  disabled = false,
}: {
  idPrefix: string
  /** The sign-in address, `null` when it is not known (never for a person). */
  login: string | null
  drafts: PointDraft[]
  onChange: (drafts: PointDraft[]) => void
  disabled?: boolean
}) {
  function replace(key: string, patch: Partial<PointDraft>) {
    const target = drafts.find((draft) => draft.key === key)
    onChange(
      drafts.map((draft) => {
        if (draft.key === key) return { ...draft, ...patch }
        // One primary per kind: marking this one takes the mark off the others.
        if (patch.isPrimary && target && draft.kind === target.kind) return { ...draft, isPrimary: false }
        return draft
      }),
    )
  }

  return (
    <div className="space-y-3">
      {login !== null && (
        <div className="border bg-muted px-3 py-2 text-sm">
          <p className="font-medium break-all">{login}</p>
          <p className="text-xs text-muted-foreground">
            Indirizzo di accesso, principale. Non si cambia da qui: è l’identità della persona e
            riceve il link per entrare.
          </p>
        </div>
      )}
      {drafts.length === 0 && (
        <p className="text-sm text-muted-foreground">Nessun altro recapito.</p>
      )}
      <ul className="space-y-3">
        {drafts.map((draft, index) => {
          const n = index + 1
          const problem = problemOf(draft)
          const id = `${idPrefix}-${draft.key}`
          return (
            <li key={draft.key} className="space-y-2 border p-3">
              <div className="grid gap-2 sm:grid-cols-[9rem_minmax(0,1fr)_11rem]">
                <div className="space-y-1">
                  <Label htmlFor={`${id}-kind`} className="text-xs text-muted-foreground">
                    Tipo
                  </Label>
                  <Select
                    value={draft.kind}
                    disabled={disabled}
                    onValueChange={(kind) =>
                      // A new kind starts without the primary mark: the first point of a kind
                      // is primary on the server when none is marked.
                      replace(draft.key, { kind: kind as ContactKind, isPrimary: false })
                    }
                  >
                    <SelectTrigger id={`${id}-kind`} aria-label={`Tipo del recapito ${n}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CONTACT_KINDS.map((kind) => (
                        <SelectItem key={kind} value={kind}>
                          {CONTACT_KIND_NAMES[kind]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`${id}-value`} className="text-xs text-muted-foreground">
                    Recapito
                  </Label>
                  <Input
                    id={`${id}-value`}
                    aria-label={`Valore del recapito ${n}`}
                    aria-invalid={problem !== null}
                    type={draft.kind === 'email' ? 'email' : draft.kind === 'phone' ? 'tel' : 'text'}
                    value={draft.value}
                    disabled={disabled}
                    onChange={(event) => replace(draft.key, { value: event.target.value })}
                  />
                </div>
                <div className="space-y-1">
                  <Label htmlFor={`${id}-label`} className="text-xs text-muted-foreground">
                    Etichetta
                  </Label>
                  <Select
                    value={draft.label}
                    disabled={disabled}
                    onValueChange={(label) => replace(draft.key, { label: label as ContactLabel })}
                  >
                    <SelectTrigger id={`${id}-label`} aria-label={`Etichetta del recapito ${n}`}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CONTACT_LABELS.map((label) => (
                        <SelectItem key={label} value={label}>
                          {CONTACT_LABEL_NAMES[label]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="grid items-end gap-2 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
                <div className="space-y-1">
                  <Label htmlFor={`${id}-note`} className="text-xs text-muted-foreground">
                    Nota (facoltativa)
                  </Label>
                  <Input
                    id={`${id}-note`}
                    aria-label={`Nota del recapito ${n}`}
                    value={draft.note}
                    maxLength={CONTACT_NOTE_MAX}
                    placeholder="Per esempio: solo WhatsApp"
                    disabled={disabled}
                    onChange={(event) => replace(draft.key, { note: event.target.value })}
                  />
                </div>
                {draft.kind === 'email' ? (
                  <p className="pb-2 text-xs text-muted-foreground">In più: non è mai quello di accesso.</p>
                ) : (
                  <div className="flex items-center gap-2 pb-2">
                    <Checkbox
                      id={`${id}-primary`}
                      aria-label={`Recapito ${n} principale`}
                      checked={draft.isPrimary}
                      disabled={disabled}
                      onCheckedChange={(checked) => replace(draft.key, { isPrimary: checked === true })}
                    />
                    <Label htmlFor={`${id}-primary`} className="font-normal">
                      Principale
                    </Label>
                  </div>
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={disabled}
                  aria-label={`Togli il recapito ${n}`}
                  onClick={() => onChange(drafts.filter((candidate) => candidate.key !== draft.key))}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
              {problem && (
                <p role="alert" className="text-sm text-destructive">
                  {problem}
                </p>
              )}
            </li>
          )
        })}
      </ul>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={disabled}
        onClick={() => onChange([...drafts, blankDraft()])}
      >
        <Plus className="mr-2 size-4" />
        Aggiungi recapito
      </Button>
    </div>
  )
}
