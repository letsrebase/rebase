import { useState } from 'react'
import { DynamicForm } from '@/components/DynamicForm'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Input } from '@rebase/ui/input'
import { Label } from '@rebase/ui/label'
import { toProblem, type ProblemDetail } from '@/lib/api'
import type { FieldDefinition, FieldType } from '@/lib/schema'
import {
  useCreateFromTemplate,
  useTemplateDescription,
  useTemplatePreview,
  useTemplates,
  type DocumentOwner,
  type TemplateVariable,
} from './queries'

/**
 * A declared template variable, as the field renderer already understands it.
 *
 * `TemplateVariable.tipo` is deliberately one of the same nine `FieldType` values
 * custom fields use (see `templates/schemas.py`), so this is a rename, not a
 * translation, and `DynamicFieldRenderer` covers every case with no new code.
 */
export function variablesToFields(variables: TemplateVariable[]): FieldDefinition[] {
  return variables.map((variable) => ({
    key: variable.nome,
    label: variable.etichetta,
    type: variable.tipo as FieldType,
    required: variable.obbligatoria,
    options: [...(variable.options ?? [])],
  }))
}

/**
 * The form's state, in the two namespaces it will be sent in -- and never flattened.
 *
 * `native` holds the document's own fields (only `titolo` here); `custom` holds the
 * template's declared variables, which travel inside `variabili`. The split is
 * decided once, when the dialog seeds itself from the chosen template, and is never
 * re-derived at submit from the currently-described variable list. Re-deriving it is
 * the bug `CustomerFormValues` documents: a variable that disappears from the
 * description between seed and submit would be reclassified as a document field and
 * sent at the top level, where `DocumentFromTemplate` would reject it.
 */
export interface TemplateFormValues {
  native: Record<string, unknown>
  custom: Record<string, unknown>
}

const TITLE_FIELD_KEY = 'titolo'

/** Mirrors `is_blank` in packages/core/src/pigrocrm/core/fields/validator.py:
 *  `null`/`undefined`, a whitespace-only string, or an empty array mean "no value".
 *  `false` and `0` do not -- they are real answers. */
function isBlank(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  return false
}

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  owner: DocumentOwner
}

export function NewFromTemplateDialog({ open, onOpenChange, owner }: Props) {
  const templates = useTemplates()
  const [templateId, setTemplateId] = useState<string | null>(null)
  const description = useTemplateDescription(templateId)
  const preview = useTemplatePreview()
  const create = useCreateFromTemplate(owner)

  const [values, setValues] = useState<TemplateFormValues>({ native: {}, custom: {} })
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [previewText, setPreviewText] = useState<string | null>(null)

  const fields = description.data ? variablesToFields(description.data.variabili) : []

  /** A key present in `custom` stays custom for as long as the dialog is open, even
   *  if the description changes underneath it. Provenance is structural, never
   *  re-decided from a list that can change. */
  function change(key: string, value: unknown) {
    setValues((previous) =>
      key === TITLE_FIELD_KEY && !(key in previous.custom)
        ? { ...previous, native: { ...previous.native, [key]: value } }
        : { ...previous, custom: { ...previous.custom, [key]: value } },
    )
  }

  function chooseTemplate(id: string) {
    setTemplateId(id)
    setProblem(null)
    setPreviewText(null)
    // Seeded once, here. `custom` starts empty and DynamicForm's create-mode effect
    // fills in each checkbox with `false`; every other type stays absent until typed.
    setValues({ native: {}, custom: {} })
  }

  function collectVariables(): Record<string, unknown> {
    const variabili: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.custom)) {
      // `false` and `0` pass this check: only a genuine blank is dropped, and a
      // dropped key is what lets the server report "variabile obbligatoria mancante"
      // by name instead of rendering an empty hole.
      if (!isBlank(value)) variabili[key] = value
    }
    return variabili
  }

  function runPreview() {
    if (!templateId) return
    setProblem(null)
    preview.mutate(
      { templateId, variabili: collectVariables() },
      {
        onSuccess: (result) => setPreviewText(result.markdown),
        onError: (error: unknown) => {
          setPreviewText(null)
          setProblem(toProblem(error))
        },
      },
    )
  }

  function generate() {
    if (!templateId) return
    setProblem(null)
    create.mutate(
      {
        template_id: templateId,
        titolo: String(values.native[TITLE_FIELD_KEY] ?? ''),
        variabili: collectVariables(),
      },
      {
        onSuccess: () => {
          onOpenChange(false)
          setTemplateId(null)
          setValues({ native: {}, custom: {} })
          setPreviewText(null)
        },
        onError: (error: unknown) => setProblem(toProblem(error)),
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Nuovo documento da template</DialogTitle>
        </DialogHeader>

        {templates.isError && <QueryErrorBanner error={templates.error} />}

        {!templateId && !templates.isError && (
          <ul className="divide-y rounded-lg border">
            {(templates.data?.items ?? []).map((template) => (
              <li key={template.id}>
                <button
                  type="button"
                  className="w-full px-4 py-3 text-left hover:bg-muted"
                  onClick={() => chooseTemplate(template.id)}
                >
                  <span className="font-medium">{template.nome}</span>
                  <span className="ml-2 text-sm text-muted-foreground">{template.tipo}</span>
                </button>
              </li>
            ))}
            {templates.data?.items.length === 0 && (
              <li className="px-4 py-3 text-muted-foreground">
                Nessun template. Creane uno in Impostazioni → Template.
              </li>
            )}
          </ul>
        )}

        {templateId && (
          <div className="space-y-5">
            {description.isError && <QueryErrorBanner error={description.error} />}

            <div className="space-y-1">
              <Label htmlFor="document-titolo">Titolo</Label>
              <Input
                id="document-titolo"
                value={String(values.native[TITLE_FIELD_KEY] ?? '')}
                onChange={(event) => change(TITLE_FIELD_KEY, event.target.value)}
              />
            </div>

            <DynamicForm
              fields={fields}
              values={values.custom}
              onChange={change}
              problem={problem}
              // Always "create": this dialog only ever composes a document that does
              // not exist yet, so an untouched checkbox is an honest `false` rather
              // than a value written on a record nobody edited.
              mode="create"
            />

            {previewText !== null && (
              <pre className="max-h-64 overflow-auto rounded-lg border bg-muted p-3 text-sm">
                {previewText}
              </pre>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Annulla
          </Button>
          {templateId && (
            <>
              <Button variant="secondary" onClick={runPreview} disabled={preview.isPending}>
                {preview.isPending ? 'Anteprima…' : 'Anteprima'}
              </Button>
              <Button onClick={generate} disabled={create.isPending}>
                {create.isPending ? 'Generazione…' : 'Genera'}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
