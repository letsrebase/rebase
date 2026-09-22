import type { ColumnDef } from '@tanstack/react-table'
import { Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { RowActions } from '@/components/RowActions'
import { StatusPill } from '@/components/StatusPill'
import { Button } from '@rebase/ui/button'
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

const TIPI = [
  { value: 'offerta', label: 'Offerta' },
  { value: 'contratto', label: 'Contratto' },
  { value: 'verbale', label: 'Verbale' },
  { value: 'documento', label: 'Documento' },
] as const

const TIPI_VARIABILE = [
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
  nome: string
  etichetta: string
  tipo: string
  obbligatoria: boolean
  options: string
}

function draftFrom(variable: TemplateVariable): VariableDraft {
  return {
    nome: variable.nome,
    etichetta: variable.etichetta,
    tipo: variable.tipo,
    obbligatoria: variable.obbligatoria,
    options: (variable.options ?? []).join(', '),
  }
}

function emptyDraft(): VariableDraft {
  return { nome: '', etichetta: '', tipo: 'text', obbligatoria: false, options: '' }
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
  const [nome, setNome] = useState('')
  const [tipo, setTipo] = useState<string>('offerta')
  const [corpo, setCorpo] = useState('')
  const [variabili, setVariabili] = useState<VariableDraft[]>([])
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [previewText, setPreviewText] = useState<string | null>(null)

  // Declared per render rather than hoisted: `useUpdateTemplate` closes over the
  // id, and the id only exists once a row is being edited.
  const update = useUpdateTemplate(editing?.id ?? '')

  function openCreate() {
    setEditing(null)
    setNome('')
    setTipo('offerta')
    setCorpo('')
    setVariabili([])
    setProblem(null)
    setPreviewText(null)
    setOpen(true)
  }

  function openEdit(template: Template) {
    setEditing(template)
    setNome(template.nome)
    setTipo(template.tipo)
    setCorpo(template.corpo_markdown)
    setVariabili((template.variabili_dichiarate ?? []).map(draftFrom))
    setProblem(null)
    setPreviewText(null)
    setOpen(true)
  }

  function body() {
    return {
      nome,
      tipo,
      corpo_markdown: corpo,
      variabili_dichiarate: variabili.map((variable) => ({
        nome: variable.nome,
        etichetta: variable.etichetta,
        tipo: variable.tipo,
        obbligatoria: variable.obbligatoria,
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
        variabili: Object.fromEntries(
          variabili.map((variable) => [variable.nome, variable.etichetta || variable.nome]),
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
    const mutation = template.attivo ? deactivate : activate
    mutation.mutate(template.id, {
      onSuccess: () =>
        toast.success(template.attivo ? 'Template disattivato' : 'Template riattivato'),
      onError: (error) => toast.error(toProblem(error).detail),
    })
  }

  const columns: ColumnDef<DataTableFeatures, Template>[] = [
    { header: 'Nome', accessorKey: 'nome' },
    {
      header: 'Tipo',
      id: 'tipo',
      accessorFn: (row) => TIPI.find((entry) => entry.value === row.tipo)?.label ?? row.tipo,
    },
    {
      header: 'Variabili',
      id: 'variabili',
      // `0` is a value, not a blank: a template with no declared variables shows
      // `0`, never a dash.
      accessorFn: (row) => String((row.variabili_dichiarate ?? []).length),
    },
    {
      header: 'Stato',
      id: 'attivo',
      // The same pill every state in the product goes through (design spec §4). `ink`
      // for the settled state, `muted` for one that claims nothing: a deactivated
      // template is a decision, not a fault, so it is not `danger`.
      cell: ({ row }) => (
        <StatusPill tone={row.original.attivo ? 'ink' : 'muted'}>
          {row.original.attivo ? 'Attivo' : 'Disattivato'}
        </StatusPill>
      ),
    },
    {
      header: '',
      id: 'azioni',
      meta: { align: 'right' },
      // Two icon buttons until the 2026-09-08 revision, now the «⋯» menu every row in
      // the product uses (§4): the labels say what they do in words, so «Disattiva» no
      // longer has to be inferred from a power symbol. Neither is destructive --
      // deactivating a template has «Riattiva» one click away in the same menu.
      cell: ({ row }) => (
        <RowActions
          label={`Azioni per ${row.original.nome}`}
          items={[
            { label: 'Modifica', onSelect: () => openEdit(row.original) },
            {
              label: row.original.attivo ? 'Disattiva' : 'Riattiva',
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
                  value={nome}
                  aria-invalid={fieldError?.field === 'nome' ? true : undefined}
                  onChange={(event) => setNome(event.target.value)}
                />
                {fieldError?.field === 'nome' ? (
                  <p className="text-destructive text-sm">{fieldError.message}</p>
                ) : null}
              </div>
              <div className="space-y-2">
                <Label htmlFor="template-tipo">Tipo</Label>
                <Select value={tipo} onValueChange={setTipo}>
                  <SelectTrigger id="template-tipo">
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
              <Label htmlFor="template-corpo">Corpo Markdown</Label>
              <Textarea
                id="template-corpo"
                value={corpo}
                rows={14}
                className="font-mono text-sm"
                aria-invalid={fieldError?.field === 'corpo_markdown' ? true : undefined}
                onChange={(event) => setCorpo(event.target.value)}
              />
              {fieldError?.field === 'corpo_markdown' ? (
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
                  onClick={() => setVariabili((previous) => [...previous, emptyDraft()])}
                >
                  Aggiungi variabile
                </Button>
              </div>

              {variabili.length === 0 ? (
                <p className="text-muted-foreground text-sm">Nessuna variabile dichiarata.</p>
              ) : null}

              {variabili.map((variable, index) => (
                <div key={index} className="grid items-end gap-2 sm:grid-cols-12">
                  <div className="space-y-1 sm:col-span-3">
                    <Label htmlFor={`variabile-nome-${index}`} className="text-xs">
                      Nome
                    </Label>
                    <Input
                      id={`variabile-nome-${index}`}
                      value={variable.nome}
                      onChange={(event) =>
                        setVariabili((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, nome: event.target.value } : entry,
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
                      value={variable.etichetta}
                      onChange={(event) =>
                        setVariabili((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, etichetta: event.target.value } : entry,
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
                      value={variable.tipo}
                      onValueChange={(value) =>
                        setVariabili((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, tipo: value } : entry,
                          ),
                        )
                      }
                    >
                      <SelectTrigger id={`variabile-tipo-${index}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TIPI_VARIABILE.map((entry) => (
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
                      disabled={variable.tipo !== 'select' && variable.tipo !== 'multiselect'}
                      onChange={(event) =>
                        setVariabili((previous) =>
                          previous.map((entry, position) =>
                            position === index ? { ...entry, options: event.target.value } : entry,
                          ),
                        )
                      }
                    />
                  </div>
                  <div className="flex items-center gap-2 sm:col-span-2">
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        aria-label={`Obbligatoria ${variable.nome || index + 1}`}
                        checked={variable.obbligatoria}
                        onChange={(event) =>
                          setVariabili((previous) =>
                            previous.map((entry, position) =>
                              position === index
                                ? { ...entry, obbligatoria: event.target.checked }
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
                      aria-label={`Rimuovi variabile ${variable.nome || index + 1}`}
                      onClick={() =>
                        setVariabili((previous) =>
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
