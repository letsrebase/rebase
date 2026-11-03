import type { ColumnDef } from '@tanstack/react-table'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
import { Checkbox } from '@rebase/ui/checkbox'
import { DataTable, type DataTableFeatures } from '@/components/DataTable'
import {
  Dialog,
  DialogContent,
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
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { fieldErrorFrom } from '@/lib/api'
import {
  useTemplatePreview,
  useTemplates,
  type Template,
  type TemplateVariable,
} from '@/features/documents/queries'
import {
  useActivateTemplate,
  useCreateTemplate,
  useDeactivateTemplate,
  useUpdateTemplate,
} from './queries'
import { templateTypeLabel } from './labels'

const TIPI = [
  { value: 'offer', label: 'Offerta' },
  { value: 'contract', label: 'Contratto' },
  { value: 'minutes', label: 'Verbale' },
  { value: 'document', label: 'Documento' },
] as const

const TIPI_VARIABLE = [
  { value: 'text', label: 'Testo' },
  { value: 'textarea', label: 'Testo lungo' },
  { value: 'number', label: 'Numero' },
  { value: 'currency', label: 'Valuta' },
  { value: 'date', label: 'Data' },
  { value: 'select', label: 'Selezione singola' },
  { value: 'multiselect', label: 'Selezione multipla' },
  { value: 'checkbox', label: 'Casella' },
  { value: 'url', label: 'URL' },
] as const

/** A variable row as the editor holds it: `options` is a comma-separated string
 *  while being typed, because a user editing a list wants to type commas, not
 *  press a button per entry. It becomes an array only on submit. */
interface VariableDraft {
  name: string
  label: string
  type: string
  required: boolean
  options: string
}

function draftFrom(variable: TemplateVariable): VariableDraft {
  return {
    name: variable.name,
    label: variable.label,
    type: variable.type,
    required: variable.required,
    options: (variable.options ?? []).join(', '),
  }
}

function emptyDraft(): VariableDraft {
  return { name: '', label: '', type: 'text', required: false, options: '' }
}

/** Splits on commas and drops blanks, so a trailing comma while typing does not
 *  produce an empty option the backend would then reject. */
function optionsFrom(value: string): string[] {
  return value
    .split(',')
    .map((option) => option.trim())
    .filter((option) => option !== '')
}

export function TemplatesPanel() {
  const templates = useTemplates()
  const create = useCreateTemplate()
  const deactivate = useDeactivateTemplate()
  const activate = useActivateTemplate()
  const preview = useTemplatePreview()

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Template | null>(null)
  const [name, setName] = useState('')
  const [type, setType] = useState<string>('offer')
  const [bodyText, setBody] = useState('')
  const [variables, setVariables] = useState<VariableDraft[]>([])
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [previewText, setPreviewText] = useState<string | null>(null)

  // Declared per render rather than hoisted: `useUpdateTemplate` closes over the
  // id, and the id only exists once a row is being edited.
  const update = useUpdateTemplate(editing?.id ?? '')

  function openCreate() {
    setEditing(null)
    setName('')
    setType('offer')
    setBody('')
    setVariables([])
    setProblem(null)
    setPreviewText(null)
    setOpen(true)
  }

  function openEdit(template: Template) {
    setEditing(template)
    setName(template.name)
    setType(template.type)
    setBody(template.body_markdown)
    setVariables((template.declared_variables ?? []).map(draftFrom))
    setProblem(null)
    setPreviewText(null)
    setOpen(true)
  }

  function body() {
    return {
      name,
      type,
      body_markdown: bodyText,
      declared_variables: variables.map((variable) => ({
        name: variable.name,
        label: variable.label,
        type: variable.type,
        required: variable.required,
        options: optionsFrom(variable.options),
      })),
    }
  }

  function submit() {
    setProblem(null)
    const handlers = {
      onSuccess: () => {
        toast.success(editing ? 'Template aggiornato' : 'Template creato')
        setOpen(false)
      },
      onError: (error: unknown) => setProblem(toProblem(error)),
    }
    if (editing) update.mutate(body(), handlers)
    else create.mutate(body(), handlers)
  }

  /**
   * The preview is rendered by the server, deliberately. Reimplementing the
   * template engine in TypeScript would give two implementations of per-context
   * escaping that drift, and the moment they drift the preview stops predicting
   * the PDF -- which is the only thing a preview is for.
   *
   * It needs a saved template, because the endpoint takes a `template_id`: a body
   * that has never been saved has nothing to preview against. The button says so
   * rather than failing.
   */
  function runPreview() {
    if (!editing) return
    setProblem(null)
    preview.mutate(
      {
        templateId: editing.id,
        variables: Object.fromEntries(
          variables.map((variable) => [variable.name, variable.label || variable.name]),
        ),
      },
      {
        onSuccess: (result) => setPreviewText(result.markdown),
        onError: (error) => {
          setPreviewText(null)
          setProblem(toProblem(error))
        },
      },
    )
  }

  function toggle(template: Template) {
    const mutation = template.active ? deactivate : activate
    mutation.mutate(template.id, {
      onSuccess: () =>
        toast.success(template.active ? 'Template disattivato' : 'Template riattivato'),
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  const columns: ColumnDef<DataTableFeatures, Template>[] = [
    { header: 'Nome', accessorKey: 'name' },
    {
      header: 'Tipo',
      id: 'type',
      accessorFn: (row) => templateTypeLabel(row.type),
    },
    {
      header: 'Variabili',
      id: 'variables',
      // `0` is a value, not a blank: a template with no declared variables shows
      // `0`, never a dash.
      accessorFn: (row) => String((row.declared_variables ?? []).length),
    },
    {
      header: 'Stato',
      id: 'active',
      // The same pill every state in the product goes through (design spec §4). `ink`
      // for the settled state, `muted` for one that claims nothing: a deactivated
      // template is a decision, not a fault, so it is not `danger`.
      cell: ({ row }) => (
        <StatusPill tone={row.original.active ? 'ink' : 'muted'}>
          {row.original.active ? 'Attivo' : 'Disattivato'}
        </StatusPill>
      ),
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      // Two icon buttons until the 2026-09-08 revision, now the «⋯» menu every row in
      // the product uses (§4): the labels say what they do in words, so «Disattiva» no
      // longer has to be inferred from a power symbol. Neither is destructive --
      // deactivating a template has «Riattiva» one click away in the same menu.
      cell: ({ row }) => (
        <RowActions
          label={`Azioni per ${row.original.name}`}
          items={[
            { label: 'Modifica', onSelect: () => openEdit(row.original) },
            {
              label: row.original.active ? 'Disattiva' : 'Riattiva',
              onSelect: () => toggle(row.original),
            },
          ]}
        />
      ),
    },
  ]

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const pending = create.isPending || update.isPending

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-medium">Template</h2>
          <p className="text-muted-foreground text-sm">
            I documenti si generano da questi template. Le variabili sono dichiarate qui: il form di
            compilazione le mostra e il render rifiuta un documento a cui manca una variabile
            obbligatoria.
          </p>
        </div>
        <Button onClick={openCreate}>
          <Plus className="mr-2 size-4" />
          Nuovo template
        </Button>
      </div>

      <DataTable
        label="Template"
        columns={columns}
        data={templates.data?.items ?? []}
        isLoading={templates.isLoading}
        isError={templates.isError}
        error={templates.error}
        emptyMessage="Nessun template definito."
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editing ? 'Modifica template' : 'Nuovo template'}</DialogTitle>
          </DialogHeader>

          {problem && !fieldError ? <QueryErrorBanner error={problem} /> : null}

          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="template-nome">Nome</Label>
                <Input
                  id="template-nome"
                  value={name}
                  aria-invalid={fieldError?.field === 'name' ? true : undefined}
                  onChange={(event) => setName(event.target.value)}
                />
                {fieldError?.field === 'name' ? (
                  <p className="text-destructive text-sm">{fieldError.message}</p>
                ) : null}
              </div>
              <div className="space-y-2">
                <Label htmlFor="template-type">Tipo</Label>
                <Select value={type} onValueChange={setType}>
                  <SelectTrigger id="template-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TIPI.map((entry) => (
                      <SelectItem key={entry.value} value={entry.value}>
                        {entry.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="template-body">Corpo Markdown</Label>
              <Textarea
                id="template-body"
                value={bodyText}
                rows={14}
                className="font-mono text-sm"
                aria-invalid={fieldError?.field === 'body_markdown' ? true : undefined}
                onChange={(event) => setBody(event.target.value)}
              />
              {fieldError?.field === 'body_markdown' ? (
                <p className="text-destructive text-sm">{fieldError.message}</p>
              ) : null}
              <p className="text-muted-foreground text-xs">
                {'Variabili come {{cliente.ragione_sociale}}, condizioni {{#if …}}{{/if}} e cicli '}
                {'{{#each …}}{{/each}}.'}
              </p>
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Label>Variabili dichiarate</Label>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setVariables((previous) => [...previous, emptyDraft()])}
                >
                  Aggiungi variabile
                </Button>
              </div>

              {variables.length === 0 ? (
                <p className="text-muted-foreground text-sm">Nessuna variabile dichiarata.</p>
              ) : null}

              {variables.map((variable, index) => (
                <div key={index} className="grid items-end gap-2 sm:grid-cols-12">
                  <div className="space-y-1 sm:col-span-3">
                    <Label htmlFor={`variabile-nome-${index}`} className="text-xs">
                      Nome
                    </Label>
                    <Input
                      id={`variabile-nome-${index}`}
                      value={variable.name}
                      onChange={(event) =>
                        setVariables((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, name: event.target.value } : entry,
                          ),
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1 sm:col-span-3">
                    <Label htmlFor={`variabile-etichetta-${index}`} className="text-xs">
                      Etichetta
                    </Label>
                    <Input
                      id={`variabile-etichetta-${index}`}
                      value={variable.label}
                      onChange={(event) =>
                        setVariables((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, label: event.target.value } : entry,
                          ),
                        )
                      }
                    />
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor={`variabile-tipo-${index}`} className="text-xs">
                      Tipo
                    </Label>
                    <Select
                      value={variable.type}
                      onValueChange={(value) =>
                        setVariables((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, type: value } : entry,
                          ),
                        )
                      }
                    >
                      <SelectTrigger id={`variabile-tipo-${index}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TIPI_VARIABLE.map((entry) => (
                          <SelectItem key={entry.value} value={entry.value}>
                            {entry.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor={`variabile-options-${index}`} className="text-xs">
                      Opzioni
                    </Label>
                    <Input
                      id={`variabile-options-${index}`}
                      value={variable.options}
                      placeholder="a, b, c"
                      disabled={variable.type !== 'select' && variable.type !== 'multiselect'}
                      onChange={(event) =>
                        setVariables((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, options: event.target.value } : entry,
                          ),
                        )
                      }
                    />
                  </div>
                  <div className="flex items-center gap-2 sm:col-span-2">
                    <label className="flex items-center gap-2 text-xs">
                      <Checkbox
                        aria-label={`Obbligatoria ${variable.name || index + 1}`}
                        checked={variable.required}
                        onCheckedChange={(checked) =>
                          setVariables((previous) =>
                            previous.map((entry, position) =>
                              position === index
                                ? { ...entry, required: checked === true }
                                : entry,
                            ),
                          )
                        }
                      />
                      Obbligatoria
                    </label>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Rimuovi variabile ${variable.name || index + 1}`}
                      onClick={() =>
                        setVariables((previous) =>
                          previous.filter((_, position) => position !== index),
                        )
                      }
                    >
                      ✕
                    </Button>
                  </div>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!editing || preview.isPending}
                  onClick={runPreview}
                >
                  Anteprima
                </Button>
                {!editing ? (
                  <span className="text-muted-foreground text-xs">
                    L'anteprima è calcolata dal server: salva il template una prima volta.
                  </span>
                ) : null}
              </div>
              {previewText !== null ? (
                <pre className="bg-muted max-h-64 overflow-auto p-3 text-xs whitespace-pre-wrap">
                  {previewText}
                </pre>
              ) : null}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Annulla
            </Button>
            <Button onClick={submit} disabled={pending}>
              {editing ? 'Salva' : 'Crea'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
