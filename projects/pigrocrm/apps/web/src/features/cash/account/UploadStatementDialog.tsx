import { useState } from 'react'
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
import { fieldErrorFrom, toProblem, type ProblemDetail } from '@/lib/api'
import { importSentence } from './labels'
import { useBankAccounts, useUploadStatement, type StatementImportResult } from './queries'

/** What the readers take: a CAMT.053 XML or the Banco BPM «Saldo e movimenti» XLSX. */
const ACCEPTED_FORMATS =
  '.xml,.xlsx,application/xml,text/xml,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/** The server's ceiling for one statement (`STATEMENT_SIZE_MAX`); a larger file never leaves the browser. */
const MAX_STATEMENT_MIB = 10
const MAX_STATEMENT_BYTES = MAX_STATEMENT_MIB * 1024 * 1024

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The account the statement belongs to. Absent, the dialog asks which one. */
  accountId?: string
}

/**
 * «Carica estratto conto»: the file and, only when the server answers that the file does not
 * state the period it covers, that period; afterwards what was read («41 movimenti, 2 già
 * presenti»). The format is told by the server from the file's content, so the person picks none.
 */
export function UploadStatementDialog({ open, onOpenChange, accountId }: Props) {
  const accounts = useBankAccounts(open && accountId === undefined, false)
  const upload = useUploadStatement()
  const [picked, setPicked] = useState<string | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [asksPeriod, setAsksPeriod] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [result, setResult] = useState<StatementImportResult | null>(null)

  const [wasOpen, setWasOpen] = useState(open)
  if (open !== wasOpen) {
    setWasOpen(open)
    if (open) {
      setPicked(null)
      setFile(null)
      setAsksPeriod(false)
      setFrom('')
      setTo('')
      setProblem(null)
      setResult(null)
      upload.reset()
    }
  }

  const choices = accounts.data?.filter((account) => account.archived_at === null) ?? []
  const onlyChoice = choices.length === 1 ? choices[0]!.id : null
  const target = accountId ?? picked ?? onlyChoice

  const tooBig = file !== null && file.size > MAX_STATEMENT_BYTES
  const periodMissing = asksPeriod && (from === '' || to === '')
  const ready = target !== null && file !== null && !tooBig && !periodMissing

  function submit() {
    if (target === null || file === null) return
    setProblem(null)
    upload.mutate(
      { accountId: target, upload: { file, ...(asksPeriod ? { period: { from, to } } : {}) } },
      {
        onSuccess: (done) => setResult(done),
        onError: (error) => {
          const next = toProblem(error)
          // The file's silence about its period is the question the fields below answer; the
          // same refusal after they were sent (an end before the start) is a sentence to read.
          const asked = fieldErrorFrom(next)?.field === 'period' && !asksPeriod
          if (asked) setAsksPeriod(true)
          setProblem(asked ? null : next)
        },
      },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Carica estratto conto</DialogTitle>
          <DialogDescription>
            Un file CAMT.053 (XML) o l’export «Saldo e movimenti» di Banco BPM (XLSX), fino a {MAX_STATEMENT_MIB} MiB.
          </DialogDescription>
        </DialogHeader>

        {result !== null ? (
          <div className="space-y-4">
            <p role="status" className="text-sm">
              {importSentence(result.added, result.duplicates)}
            </p>
            <DialogFooter>
              <Button onClick={() => onOpenChange(false)}>Chiudi</Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-4">
            {accountId === undefined && (
              <div className="space-y-2">
                <Label htmlFor="statement-account">Conto</Label>
                <Select value={target ?? ''} onValueChange={setPicked} disabled={accounts.isLoading}>
                  <SelectTrigger id="statement-account" className="w-full">
                    <SelectValue placeholder={accounts.isLoading ? 'Leggo i conti…' : 'Scegli il conto'} />
                  </SelectTrigger>
                  <SelectContent>
                    {choices.map((account) => (
                      <SelectItem key={account.id} value={account.id}>
                        {account.name} · {account.bank}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {accounts.isError && (
                  <p role="alert" className="text-sm text-destructive">
                    {toProblem(accounts.error).detail}
                  </p>
                )}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="statement-file">File dell’estratto conto</Label>
              <Input
                id="statement-file"
                type="file"
                accept={ACCEPTED_FORMATS}
                onChange={(event) => {
                  setFile(event.target.files?.[0] ?? null)
                  setProblem(null)
                }}
                aria-invalid={tooBig}
              />
              {tooBig && <p className="text-sm text-destructive">Il file supera i {MAX_STATEMENT_MIB} MiB.</p>}
            </div>

            {asksPeriod && (
              <fieldset className="space-y-2">
                <legend className="text-sm text-muted-foreground">
                  Il file non dichiara il periodo: va indicato, non si indovina.
                </legend>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="statement-from">Periodo dal</Label>
                    <Input
                      id="statement-from"
                      type="date"
                      value={from}
                      onChange={(event) => setFrom(event.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="statement-to">Periodo al</Label>
                    <Input
                      id="statement-to"
                      type="date"
                      value={to}
                      onChange={(event) => setTo(event.target.value)}
                    />
                  </div>
                </div>
              </fieldset>
            )}

            {problem !== null && (
              <p role="alert" className="text-sm text-destructive">
                {problem.detail}
              </p>
            )}

            <DialogFooter>
              <Button variant="ghost" onClick={() => onOpenChange(false)}>
                Annulla
              </Button>
              <Button onClick={submit} disabled={!ready || upload.isPending}>
                {upload.isPending ? 'Leggo il file…' : 'Carica'}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
