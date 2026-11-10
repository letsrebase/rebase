import { useState } from 'react'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@rebase/ui/dialog'
import { Label } from '@rebase/ui/label'
import { Loader } from '@rebase/ui/loader'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@rebase/ui/select'
import { Textarea } from '@rebase/ui/textarea'
import { toast } from '@rebase/ui/sonner'
import { useLegalEntity } from '@/lib/legalEntity'
import {
  KIND_LABELS,
  PICKABLE_KINDS,
  documents,
  type InboxItem,
  type InboxKind,
} from './labels'
import { useMarkDuplicate, useOriginalCandidates, useSetKind } from './queries'

/** The select's value while nothing is chosen: `''` is what Radix reads as «show the placeholder». */
const NONE = ''

const REASON_MAX = 500

/**
 * The reason a person gives for putting documents aside: one sentence, kept on every item
 * with the state «Scartato». `onConfirm` is the caller's write (one item, or a whole selection
 * sent one after the other) and rejects with what it met, which the dialog shows.
 */
export function DiscardDialog({
  count,
  onConfirm,
  onClose,
}: {
  count: number
  onConfirm: (reason: string) => Promise<void>
  onClose: () => void
}) {
  const [reason, setReason] = useState('')
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  return (
    <Dialog open onOpenChange={(next) => !next && !pending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{count === 1 ? 'Scarta il documento' : `Scarta ${documents(count)}`}</DialogTitle>
          <DialogDescription>
            Restano in «Scartati e doppioni», con il motivo che scrivi. Lo stesso file caricato di
            nuovo viene riconosciuto come doppione e resta scartato.
          </DialogDescription>
        </DialogHeader>
        {problem === null ? null : <QueryErrorBanner error={problem} />}
        <div className="space-y-2">
          <Label htmlFor="inbox-discard-reason">Motivo</Label>
          <Textarea
            id="inbox-discard-reason"
            value={reason}
            maxLength={REASON_MAX}
            rows={3}
            placeholder="Per esempio: non è un documento nostro."
            onChange={(event) => setReason(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Annulla
          </Button>
          <Button
            disabled={pending || reason.trim() === ''}
            onClick={() => {
              setPending(true)
              setProblem(null)
              onConfirm(reason.trim()).then(onClose, (error: unknown) => {
                setProblem(error)
                setPending(false)
              })
            }}
          >
            {pending ? <Loader className="mr-2 size-4" /> : null}
            Scarta
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** The legal entities of the space as a select: the company an item is for. */
function CompanySelect({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string
  value: string
  onChange: (next: string) => void
  disabled?: boolean
}) {
  const { legal_entities } = useLegalEntity()
  return (
    <Select value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger id={id} className="w-full" aria-label="Azienda">
        <SelectValue placeholder="Scegli l’azienda" />
      </SelectTrigger>
      <SelectContent>
        {legal_entities.map((entity) => (
          <SelectItem key={entity.id} value={entity.id}>
            {entity.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

/**
 * «Indica il tipo» / «Cambia tipo»: what the document is and, when the space has companies, which
 * one it is for. Nothing is filed: the item waits for the module that registers that kind, and a
 * proposal waiting on it is rejected (the server says so in the sentence a refusal carries). A
 * kind a FatturaPA decided by itself is certain data: it stays, and only the company may change.
 */
export function KindDialog({
  item,
  certain,
  onClose,
}: {
  item: InboxItem
  certain: boolean
  onClose: () => void
}) {
  const { legal_entities } = useLegalEntity()
  const setKind = useSetKind()
  const [kind, setKindValue] = useState<string>(item.kind ?? NONE)
  const [company, setCompany] = useState<string>(item.legal_entity_id ?? NONE)
  const xml = item.file_type === 'xml' || item.file_type === 'p7m'
  const kinds = PICKABLE_KINDS.filter((candidate) => candidate !== 'sales_invoice' || xml)
  const kindChanged = !certain && kind !== NONE && kind !== item.kind
  const companyChanged = company !== NONE && company !== item.legal_entity_id
  return (
    <Dialog open onOpenChange={(next) => !next && !setKind.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{item.kind === null ? 'Indica il tipo' : 'Cambia tipo'}</DialogTitle>
          <DialogDescription>
            Il documento resta in «In arrivo» finché non c’è il modulo che registra quel tipo.
            {certain ? ' Il tipo l’ha letto il file stesso, con dati certi: non si cambia.' : ''}
          </DialogDescription>
        </DialogHeader>
        {setKind.error ? <QueryErrorBanner error={setKind.error} /> : null}
        <div className="space-y-2">
          <Label htmlFor="inbox-kind">Che cos’è</Label>
          <Select value={kind} onValueChange={setKindValue} disabled={certain}>
            <SelectTrigger id="inbox-kind" className="w-full" aria-label="Che cos’è">
              <SelectValue placeholder="Scegli il tipo" />
            </SelectTrigger>
            <SelectContent>
              {kinds.map((candidate) => (
                <SelectItem key={candidate} value={candidate}>
                  {KIND_LABELS[candidate]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {legal_entities.length > 0 ? (
          <div className="space-y-2">
            <Label htmlFor="inbox-company">Per quale azienda</Label>
            <CompanySelect id="inbox-company" value={company} onChange={setCompany} />
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={setKind.isPending}>
            Annulla
          </Button>
          <Button
            disabled={setKind.isPending || (!kindChanged && !companyChanged)}
            onClick={() =>
              setKind.mutate(
                {
                  id: item.id,
                  kind: kindChanged ? (kind as Exclude<InboxKind, 'archive'>) : undefined,
                  legal_entity_id: companyChanged ? company : undefined,
                },
                {
                  onSuccess: () => {
                    toast.success('Fatto.')
                    onClose()
                  },
                },
              )
            }
          >
            {setKind.isPending ? <Loader className="mr-2 size-4" /> : null}
            Salva
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** «Segna come doppione»: the file this one is a copy of, chosen among the others that arrived. */
export function DuplicateDialog({ item, onClose }: { item: InboxItem; onClose: () => void }) {
  const candidates = useOriginalCandidates(item.id, true)
  const mark = useMarkDuplicate()
  const [original, setOriginal] = useState<string>(NONE)
  return (
    <Dialog open onOpenChange={(next) => !next && !mark.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Segna come doppione</DialogTitle>
          <DialogDescription>
            Dice di quale file è una copia. Esce da «Da gestire» e resta in «Scartati e doppioni».
          </DialogDescription>
        </DialogHeader>
        {mark.error ? <QueryErrorBanner error={mark.error} /> : null}
        {candidates.isError ? <QueryErrorBanner error={candidates.error} /> : null}
        <div className="space-y-2">
          <Label htmlFor="inbox-original">Copia di</Label>
          <Select value={original} onValueChange={setOriginal} disabled={candidates.isPending}>
            <SelectTrigger id="inbox-original" className="w-full" aria-label="Copia di">
              <SelectValue placeholder={candidates.isPending ? 'Leggo i file…' : 'Scegli il file'} />
            </SelectTrigger>
            <SelectContent>
              {(candidates.data ?? []).map((candidate) => (
                <SelectItem key={candidate.id} value={candidate.id}>
                  {candidate.original_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={mark.isPending}>
            Annulla
          </Button>
          <Button
            disabled={mark.isPending || original === NONE}
            onClick={() =>
              mark.mutate(
                { id: item.id, originalId: original },
                {
                  onSuccess: () => {
                    toast.success('Segnato come doppione.')
                    onClose()
                  },
                },
              )
            }
          >
            {mark.isPending ? <Loader className="mr-2 size-4" /> : null}
            Segna come doppione
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** «Assegna all'azienda» on a selection: the company the documents are for. */
export function CompanyDialog({
  count,
  onConfirm,
  onClose,
}: {
  count: number
  onConfirm: (legalEntityId: string) => Promise<void>
  onClose: () => void
}) {
  const [company, setCompany] = useState<string>(NONE)
  const [pending, setPending] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  return (
    <Dialog open onOpenChange={(next) => !next && !pending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assegna all’azienda</DialogTitle>
          <DialogDescription>
            {documents(count)} {count === 1 ? 'passa' : 'passano'} all’azienda che scegli, e la
            vedono le persone che lavorano per lei.
          </DialogDescription>
        </DialogHeader>
        {problem === null ? null : <QueryErrorBanner error={problem} />}
        <div className="space-y-2">
          <Label htmlFor="inbox-bulk-company">Azienda</Label>
          <CompanySelect id="inbox-bulk-company" value={company} onChange={setCompany} />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Annulla
          </Button>
          <Button
            disabled={pending || company === NONE}
            onClick={() => {
              setPending(true)
              setProblem(null)
              onConfirm(company).then(onClose, (error: unknown) => {
                setProblem(error)
                setPending(false)
              })
            }}
          >
            {pending ? <Loader className="mr-2 size-4" /> : null}
            Assegna
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
