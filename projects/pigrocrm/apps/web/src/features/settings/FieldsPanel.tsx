import type { ColumnDef } from '@tanstack/react-table'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import { Checkbox } from '@rebase/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import type { EntityType, FieldType } from '@/lib/schema'
import {
  useArchiveFieldDefinition,
  useCreateFieldDefinition,
  useFieldDefinitions,
  useUnarchiveFieldDefinition,
  useUpdateFieldDefinition,
  type FieldDefinitionRecord,
} from './queries'

const ENTITIES: { value: EntityType; label: string }[] = [
  { value: 'customer', label: 'Cliente' },
  { value: 'person', label: 'Persona' },
  { value: 'deal', label: 'Deal' },
]

const TYPES: { value: FieldType; label: string }[] = [
  { value: 'text', label: 'Testo' },
  { value: 'textarea', label: 'Testo lungo' },
  { value: 'number', label: 'Numero' },
  { value: 'currency', label: 'Valuta' },
  { value: 'date', label: 'Data' },
  { value: 'select', label: 'Selezione singola' },
  { value: 'multiselect', label: 'Selezione multipla' },
  { value: 'checkbox', label: 'Sì / No' },
  { value: 'url', label: 'URL' },
]

const NEEDS_OPTIONS = new Set<FieldType>(['select', 'multiselect'])

/** A rough, client-side preview only -- `FieldDefinitionCreate.key` re-runs the
 *  real `slugify_key` (fields/schemas.py: NFKD-normalises accents instead of
 *  dropping them) on whatever ends up in the Chiave field regardless, so this
 *  never has to match it byte for byte. */
function previewSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '_')
}

// Fields `FieldDefinitionCreate`/`FieldDefinitionUpdate` can attribute a
// `validation_failed` 422 to. `options` is listed separately below since it is
// only ever rendered (and therefore only ever attachable) while the field type
// takes options -- see `unattributed`.
const CREATE_FIELDS = ['entity_type', 'key', 'label', 'field_type', 'required', 'position']
const UPDATE_FIELDS = ['label', 'required', 'position']

function unattributed(
  problem: ProblemDetail | null,
  knownFields: string[],
  showsOptions: boolean,
): string | null {
  if (!problem) return null
  const fieldError = fieldErrorFrom(problem)
  if (!fieldError) return problem.detail
  const known = showsOptions ? [...knownFields, 'options'] : knownFields
  return known.includes(fieldError.field) ? null : problem.detail
}

export function FieldsPanel() {
  const [entityType, setEntityType] = useState<EntityType>('customer')
  const [open, setOpen] = useState(false)
  const [key, setKey] = useState('')
  const [keyEdited, setKeyEdited] = useState(false)
  const [label, setLabel] = useState('')
  const [fieldType, setFieldType] = useState<FieldType>('text')
  const [optionsText, setOptionsText] = useState('')
  const [required, setRequired] = useState(false)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const [editingField, setEditingField] = useState<FieldDefinitionRecord | null>(null)
  const [editLabel, setEditLabel] = useState('')
  const [editOptionsText, setEditOptionsText] = useState('')
  const [editRequired, setEditRequired] = useState(false)
  const [editPosition, setEditPosition] = useState('0')
  const [editProblem, setEditProblem] = useState<ProblemDetail | null>(null)

  const fields = useFieldDefinitions(entityType)
  const create = useCreateFieldDefinition()
  const update = useUpdateFieldDefinition()
  const archive = useArchiveFieldDefinition()
  const unarchive = useUnarchiveFieldDefinition()

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem, CREATE_FIELDS, NEEDS_OPTIONS.has(fieldType))

  const editFieldError = editProblem ? fieldErrorFrom(editProblem) : null
  const editBanner = unattributed(
    editProblem,
    UPDATE_FIELDS,
    editingField ? NEEDS_OPTIONS.has(editingField.field_type) : false,
  )

  function openDialog() {
    setProblem(null)
    setKey('')
    setKeyEdited(false)
    setLabel('')
    setFieldType('text')
    setOptionsText('')
    setRequired(false)
    setOpen(true)
  }

  function submit() {
    setProblem(null)
    create.mutate(
      {
        entity_type: entityType,
        key,
        label,
        field_type: fieldType,
        options: NEEDS_OPTIONS.has(fieldType)
          ? optionsText
              .split('\n')
              .map((line) => line.trim())
              .filter(Boolean)
          : [],
        required,
        position: fields.data?.length ?? 0,
      },
      {
        onSuccess: () => {
          toast.success('Campo creato')
          setOpen(false)
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  function openEditDialog(field: FieldDefinitionRecord) {
    setEditProblem(null)
    setEditingField(field)
    setEditLabel(field.label)
    setEditOptionsText(field.options.join('\n'))
    setEditRequired(field.required)
    setEditPosition(String(field.position))
  }

  function submitEdit() {
    if (!editingField) return
    setEditProblem(null)
    const body: Record<string, unknown> = {
      label: editLabel,
      required: editRequired,
      position: Number(editPosition),
    }
    if (NEEDS_OPTIONS.has(editingField.field_type)) {
      body.options = editOptionsText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
    }
    update.mutate(
      { fieldId: editingField.id, body },
      {
        onSuccess: () => {
          toast.success('Campo aggiornato')
          setEditingField(null)
        },
        onError: (error) => setEditProblem(toProblem(error)),
      },
    )
  }

  const columns: ColumnDef<DataTableFeatures, FieldDefinitionRecord>[] = [
    { header: 'Etichetta', accessorKey: 'label' },
    { header: 'Chiave', id: 'key', cell: (info) => <code>{info.row.original.key}</code> },
    {
      header: 'Tipo',
      id: 'field_type',
      accessorFn: (row) => TYPES.find((type) => type.value === row.field_type)?.label ?? row.field_type,
    },
    {
      header: 'Obbligatorio',
      id: 'required',
      accessorFn: (row) => (row.required ? 'Sì' : 'No'),
    },
    {
      header: 'Stato',
      id: 'archived',
      // The same pill every state in the product goes through (design spec §4). `ink`
      // for a field in use, `muted` for one that claims nothing: archiving is
      // reversible and touches no stored value, so it is not `danger`.
      cell: (info) => (
        <StatusPill tone={info.row.original.archived ? 'muted' : 'ink'}>
          {info.row.original.archived ? 'Archiviato' : 'Attivo'}
        </StatusPill>
      ),
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      // Behind the «⋯» like every other row action (§4), and the labels now say in
      // words what the archive/restore icons only implied.
      //
      // Archiving/restoring stay single-click, with no confirm: unlike an
      // irreversible action elsewhere in Settings (a pipeline-stage delete, a
      // token revoke), the other half of this pair is always one more click
      // away in this same menu. Not marked destructive for the same reason.
      cell: (info) => {
        const field = info.row.original
        return (
          <RowActions
            label={`Azioni per ${field.label}`}
            items={[
              { label: 'Modifica', onSelect: () => openEditDialog(field) },
              field.archived
                ? {
                    label: 'Ripristina',
                    onSelect: () =>
                      unarchive.mutate(field.id, {
                        onSuccess: () => toast.success('Campo ripristinato'),
                        onError: (error) => toast.error(toProblem(error).detail),
                      }),
                  }
                : {
                    label: 'Archivia',
                    onSelect: () =>
                      archive.mutate(field.id, {
                        onSuccess: () => toast.success('Campo archiviato'),
                        onError: (error) => toast.error(toProblem(error).detail),
                      }),
                  },
            ]}
          />
        )
      },
    },
  ]

  return (
    <div className="space-y-4">
      {/* Wrapping: at 390 the select and the button did not fit on one row and the
          button hung outside the panel. */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-2">
          <Label htmlFor="entity">Entità</Label>
          <Select value={entityType} onValueChange={(value) => setEntityType(value as EntityType)}>
            <SelectTrigger id="entity" className="w-56">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {ENTITIES.map((entity) => (
                <SelectItem key={entity.value} value={entity.value}>
                  {entity.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={openDialog}>
          <Plus className="mr-2 size-4" />
          Nuovo campo
        </Button>
      </div>

      <DataTable
        columns={columns}
        data={fields.data ?? []}
        isLoading={fields.isLoading}
        isError={fields.isError}
        error={fields.error}
        emptyMessage="Nessun campo personalizzato per questa entità."
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Nuovo campo</DialogTitle>
            <DialogDescription>
              Il tipo non è modificabile dopo la creazione: per cambiarlo, archivia il campo e
              creane uno nuovo. I valori già inseriti restano leggibili e non vengono toccati.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {banner && (
              <p
                role="alert"
                className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {banner}
              </p>
            )}

            <div className="space-y-2">
              <Label htmlFor="field-label">Etichetta</Label>
              <Input
                id="field-label"
                aria-invalid={fieldError?.field === 'label'}
                value={label}
                onChange={(event) => {
                  const value = event.target.value
                  setLabel(value)
                  // Tracks the full label on every keystroke, not just the
                  // first, until the user edits Chiave directly (`keyEdited`
                  // below) -- a `!key` guard here looks equivalent but isn't:
                  // once the first keystroke sets a non-empty key, `!key` is
                  // false forever after, so "Settore" typed one character at a
                  // time (a real keyboard, not a pasted value) previously
                  // derived the key "s" and stopped there.
                  if (!keyEdited) setKey(previewSlug(value))
                }}
              />
              {fieldError?.field === 'label' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="field-key">Chiave</Label>
              <Input
                id="field-key"
                aria-invalid={fieldError?.field === 'key'}
                value={key}
                onChange={(event) => {
                  setKeyEdited(true)
                  setKey(event.target.value)
                }}
              />
              {fieldError?.field === 'key' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="field-type">Tipo</Label>
              <Select value={fieldType} onValueChange={(value) => setFieldType(value as FieldType)}>
                <SelectTrigger id="field-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {TYPES.map((type) => (
                    <SelectItem key={type.value} value={type.value}>
                      {type.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {NEEDS_OPTIONS.has(fieldType) && (
              <div className="space-y-2">
                <Label htmlFor="field-options">Opzioni (una per riga)</Label>
                <Textarea
                  id="field-options"
                  rows={4}
                  aria-invalid={fieldError?.field === 'options'}
                  value={optionsText}
                  onChange={(event) => setOptionsText(event.target.value)}
                />
                {fieldError?.field === 'options' && (
                  <p className="text-sm text-destructive">{fieldError.message}</p>
                )}
              </div>
            )}

            <div className="flex items-center gap-2">
              <Checkbox
                id="field-required"
                checked={required}
                onCheckedChange={(checked) => setRequired(checked === true)}
              />
              <Label htmlFor="field-required" className="font-normal">
                Obbligatorio
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Annulla
            </Button>
            <Button onClick={submit} disabled={create.isPending}>
              {create.isPending ? 'Creazione…' : 'Crea'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(editingField)} onOpenChange={(next) => !next && setEditingField(null)}>
        <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Modifica campo</DialogTitle>
            {editingField && (
              <DialogDescription>
                Chiave <code>{editingField.key}</code> · Tipo{' '}
                {TYPES.find((type) => type.value === editingField.field_type)?.label} — nessuno dei
                due è modificabile qui (`FieldDefinitionUpdate` non li accetta).
              </DialogDescription>
            )}
          </DialogHeader>

          <div className="space-y-4">
            {editBanner && (
              <p
                role="alert"
                className="rounded-lg border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                {editBanner}
              </p>
            )}

            <div className="space-y-2">
              <Label htmlFor="edit-field-label">Etichetta</Label>
              <Input
                id="edit-field-label"
                aria-invalid={editFieldError?.field === 'label'}
                value={editLabel}
                onChange={(event) => setEditLabel(event.target.value)}
              />
              {editFieldError?.field === 'label' && (
                <p className="text-sm text-destructive">{editFieldError.message}</p>
              )}
            </div>

            {editingField && NEEDS_OPTIONS.has(editingField.field_type) && (
              <div className="space-y-2">
                <Label htmlFor="edit-field-options">Opzioni (una per riga)</Label>
                <Textarea
                  id="edit-field-options"
                  rows={4}
                  aria-invalid={editFieldError?.field === 'options'}
                  value={editOptionsText}
                  onChange={(event) => setEditOptionsText(event.target.value)}
                />
                {editFieldError?.field === 'options' && (
                  <p className="text-sm text-destructive">{editFieldError.message}</p>
                )}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="edit-field-position">Posizione</Label>
              <Input
                id="edit-field-position"
                type="number"
                min={0}
                aria-invalid={editFieldError?.field === 'position'}
                value={editPosition}
                onChange={(event) => setEditPosition(event.target.value)}
              />
              {editFieldError?.field === 'position' && (
                <p className="text-sm text-destructive">{editFieldError.message}</p>
              )}
            </div>

            <div className="flex items-center gap-2">
              <Checkbox
                id="edit-field-required"
                checked={editRequired}
                onCheckedChange={(checked) => setEditRequired(checked === true)}
              />
              <Label htmlFor="edit-field-required" className="font-normal">
                Obbligatorio
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="ghost" onClick={() => setEditingField(null)}>
              Annulla
            </Button>
            <Button onClick={submitEdit} disabled={update.isPending}>
              {update.isPending ? 'Salvataggio…' : 'Salva'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
