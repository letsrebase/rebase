import { Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toProblem } from '@/lib/api'
import { Button } from '@rebase/ui/button'
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
import { toast } from '@rebase/ui/sonner'
import { DEFAULT_EXPIRY_DAYS, EXPIRY_DAYS, daysWord, formatDay, type CreatedShareLink } from './labels'
import { useCreateLink } from './queries'

/**
 * «Crea un link per il commercialista» (REB-1047, the approved brief): how long the link lives
 * (a week unless the admin says otherwise, thirty days at most) and «Crea il link». Once made, the
 * address is shown here and nowhere else, with «Copia il link»: the server keeps only its hash, so
 * the dialog is the one place it can be copied from and it is gone with the dialog.
 */
export function ShareLinkDialog({
  exportId,
  open,
  onOpenChange,
}: {
  exportId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [pending, setPending] = useState(false)
  return (
    <Dialog open={open} onOpenChange={(next) => (next || !pending) && onOpenChange(next)}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Link per il commercialista</DialogTitle>
          <DialogDescription>
            Chi ha il link scarica l’esportazione senza un account, finché non scade o non lo
            revochi.
          </DialogDescription>
        </DialogHeader>
        {/* Born with each opening, so a closed dialog never offers last time's address. */}
        <LinkForm exportId={exportId} onClose={() => onOpenChange(false)} onPending={setPending} />
      </DialogContent>
    </Dialog>
  )
}

function LinkForm({
  exportId,
  onClose,
  onPending,
}: {
  exportId: string
  onClose: () => void
  onPending: (pending: boolean) => void
}) {
  const [days, setDays] = useState<number>(DEFAULT_EXPIRY_DAYS)
  const create = useCreateLink(exportId)
  const { reset } = create
  // Held in this component's state and nowhere else: not in the URL, and the mutation's own copy
  // of the answer is dropped with the dialog.
  const [created, setCreated] = useState<CreatedShareLink | null>(null)
  useEffect(() => reset, [reset])
  // The dialog may not be closed while the link is being made: its address is only shown here.
  const pending = create.isPending
  useEffect(() => onPending(pending), [pending, onPending])

  if (created !== null) return <CreatedLink link={created} onClose={onClose} />

  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        create.mutate(days, { onSuccess: setCreated })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="share-link-days">Scade tra</Label>
        <Select value={String(days)} onValueChange={(next) => setDays(EXPIRY_DAYS.find((candidate) => String(candidate) === next) ?? DEFAULT_EXPIRY_DAYS)}>
          <SelectTrigger id="share-link-days" className="w-full sm:w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {EXPIRY_DAYS.map((candidate) => (
              <SelectItem key={candidate} value={String(candidate)}>
                {daysWord(candidate)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {create.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {toProblem(create.error).detail}
        </p>
      ) : null}
      <DialogFooter>
        <Button type="submit" disabled={create.isPending}>
          Crea il link
        </Button>
      </DialogFooter>
    </form>
  )
}

function CreatedLink({ link, onClose }: { link: CreatedShareLink; onClose: () => void }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(link.url)
      toast.success('Link copiato')
    } catch {
      toast.error('Non riesco a copiare: seleziona il link e copialo a mano.')
    }
  }
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="share-link-url">Il link</Label>
        <Input
          id="share-link-url"
          readOnly
          value={link.url}
          onFocus={(event) => event.currentTarget.select()}
        />
        <p className="text-xs text-muted-foreground">Scade il {formatDay(link.expires_at)}.</p>
      </div>
      <p className="border bg-background px-3 py-2 text-sm">
        Lo vedi solo ora: non lo conserviamo. Se lo perdi, revocalo e creane un altro.
      </p>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          Chiudi
        </Button>
        <Button onClick={() => void copy()}>
          <Copy aria-hidden className="size-4" />
          Copia il link
        </Button>
      </DialogFooter>
    </div>
  )
}
