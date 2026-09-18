import type { ColumnDef } from '@tanstack/react-table'
import { Plus, RotateCcw } from 'lucide-react'
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
import { DEAL_STAGE_TONE, useStages, type Stage } from '@/features/deals/queries'
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { useCreateStage, useDeleteStage, useSeedStages } from './queries'

const TYPES = [
  { value: 'open', label: 'Aperto' },
  { value: 'won', label: 'Vinto' },
  { value: 'lost', label: 'Perso' },
] as const

const KNOWN_FIELDS = ['nome', 'posizione', 'probabilita_default', 'tipo', 'code']

function unattributed(problem: ProblemDetail | null): string | null {
  if (!problem) return null
  const fieldError = fieldErrorFrom(problem)
  if (fieldError && KNOWN_FIELDS.includes(fieldError.field)) return null
  return problem.detail
}

export function PipelinePanel() {
  const [open, setOpen] = useState(false)
  const [nome, setNome] = useState('')
  const [probabilita, setProbabilita] = useState('0')
  const [tipo, setTipo] = useState<'open' | 'won' | 'lost'>('open')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)

  const stages = useStages()
  const create = useCreateStage()
  const deleteStage = useDeleteStage()
  const seed = useSeedStages()

  const fieldError = problem ? fieldErrorFrom(problem) : null
  const banner = unattributed(problem)

  function openDialog() {
    setProblem(null)
    setNome('')
    setProbabilita('0')
    setTipo('open')
    setOpen(true)
  }

  function submit() {
    setProblem(null)
    create.mutate(
      {
        nome,
        posizione: stages.data?.length ?? 0,
        probabilita_default: Number(probabilita),
        tipo,
      },
      {
        onSuccess: () => {
          toast.success('Stato creato')
          setOpen(false)
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  function remove(stage: Stage) {
    // Unlike archiving a field definition, this has no visible way back: `DELETE
    // /api/pipeline-stages/{id}` is a hard delete (`PipelineRepository.delete`
    // calls `session.delete`, not a soft-delete flag), and there is no "undelete"
    // endpoint the way there is `unarchive` for a field. A plain confirm is the
    // same cheap misclick guard `CustomerDetail`/`PersonDetail` already use before
    // their own (reversible) archive -- here it matters even more, since this one
    // truly cannot be undone from the UI.
    const confirmed = window.confirm(
      `Eliminare definitivamente lo stato "${stage.nome}"? L'operazione non è reversibile.`,
    )
    if (!confirmed) return

    deleteStage.mutate(stage.id, {
      onSuccess: () => toast.success('Stato eliminato'),
      onError: (error) => {
        // Refused (409) if any deal, archived included, still points at this
        // stage -- `PipelineService.delete`'s own docstring. The server's
        // sentence already says what to do about it ("spostali in un altro
        // stato prima di eliminarlo"); the count riding along in `deals` is
        // appended the same way `CustomerDetail.archive` appends its own
        // `active_deals`, rather than left to disappear even though the server
        // reported it.
        const failed = toProblem(error)
        const deals = failed.deals
        toast.error(typeof deals === 'number' ? `${failed.detail} (${deals})` : failed.detail)
      },
    })
  }

  const columns: ColumnDef<DataTableFeatures, Stage>[] = [
    { header: 'Posizione', accessorKey: 'posizione' },
    { header: 'Nome', accessorKey: 'nome' },
    { header: 'Probabilità', id: 'probabilita', accessorFn: (row) => `${row.probabilita_default}%` },
    {
      header: 'Tipo',
      id: 'tipo',
      // `DEAL_STAGE_TONE` rather than a tone chosen here: it is keyed on `tipo` -- the
      // closed enum the backend guarantees -- and it is the same map the Kanban and the
      // deal list read, so a stage reads identically wherever it appears.
      cell: (info) => (
        <StatusPill tone={DEAL_STAGE_TONE[info.row.original.tipo]}>
          {TYPES.find((type) => type.value === info.row.original.tipo)?.label}
        </StatusPill>
      ),
    },
    {
      header: '',
      id: 'actions',
      meta: { align: 'right' },
      // Behind the «⋯» like every other row action (§4). Destructive, and it keeps its
      // own `window.confirm` inside `remove`: a stage with deals in it is refused by the
      // server (409), but a stage without them goes for good.
      cell: (info) => (
        <RowActions
          label={`Azioni per ${info.row.original.nome}`}
          items={[
            {
              label: 'Elimina',
              destructive: true,
              disabled: deleteStage.isPending,
              onSelect: () => remove(info.row.original),
            },
          ]}
        />
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="font-semibold">Stati della pipeline</h2>
          <p className="text-sm text-muted-foreground">
            Il tipo dice al sistema cosa significa uno stato: le dashboard riconoscono
            &quot;vinto&quot; dal tipo, non dal nome, così puoi rinominarlo liberamente. Uno stato
            non si può eliminare finché ha deal — anche archiviati — al suo interno.
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() =>
              seed.mutate(undefined, {
                onSuccess: () => toast.success('Stati predefiniti ripristinati'),
                onError: (error) => toast.error(toProblem(error).detail),
              })
            }
          >
            <RotateCcw className="mr-2 size-4" />
            Ripristina predefiniti
          </Button>
          <Button onClick={openDialog}>
            <Plus className="mr-2 size-4" />
            Nuovo stato
          </Button>
        </div>
      </div>

      <DataTable
        columns={columns}
        data={stages.data ?? []}
        isLoading={stages.isLoading}
        isError={stages.isError}
        error={stages.error}
        emptyMessage="Nessuno stato configurato."
      />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nuovo stato</DialogTitle>
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
              <Label htmlFor="stage-nome">Nome</Label>
              <Input
                id="stage-nome"
                aria-invalid={fieldError?.field === 'nome'}
                value={nome}
                onChange={(event) => setNome(event.target.value)}
              />
              {fieldError?.field === 'nome' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="stage-prob">Probabilità predefinita (%)</Label>
              <Input
                id="stage-prob"
                type="number"
                min={0}
                max={100}
                aria-invalid={fieldError?.field === 'probabilita_default'}
                value={probabilita}
                onChange={(event) => setProbabilita(event.target.value)}
              />
              {fieldError?.field === 'probabilita_default' && (
                <p className="text-sm text-destructive">{fieldError.message}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="stage-tipo">Tipo</Label>
              <Select value={tipo} onValueChange={(value) => setTipo(value as typeof tipo)}>
                <SelectTrigger id="stage-tipo" className="w-full">
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
    </div>
  )
}
