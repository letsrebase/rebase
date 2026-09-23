import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useParams } from '@tanstack/react-router'
import { ArrowLeft, Download } from 'lucide-react'
import { useState, type ComponentProps, type FormEvent } from 'react'
import { Badge } from '@rebase/ui/badge'
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
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@rebase/ui/table'
import { admin, ApiError, type ContractDocument, type Fiscal, type FiscalData, type Match } from '@/lib/api'
import { draftFromFiscal, toFiscalData, type FiscalDraft } from '@/lib/contracts'
import { DOCUMENT_STATE_LABELS, MATCH_STATE_LABELS, formatDate } from '@/lib/format'
import { Empty, Header, Row } from './lists'

/** The four tax fields, shared by this page and step 2 of «Crea match». */
export function FiscalFields({
  idPrefix,
  draft,
  onChange,
  wrong,
}: {
  idPrefix: string
  draft: FiscalDraft
  onChange: (draft: FiscalDraft) => void
  wrong: (field: string) => true | undefined
}) {
  const field = (name: keyof FiscalDraft, label: string, props: ComponentProps<typeof Input> = {}) => (
    <div className="space-y-1.5">
      <Label htmlFor={`${idPrefix}-${name}`}>{label}</Label>
      <Input
        id={`${idPrefix}-${name}`}
        value={draft[name]}
        onChange={(event) => onChange({ ...draft, [name]: event.target.value })}
        aria-invalid={wrong(name)}
        {...props}
      />
    </div>
  )
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {field('codice_fiscale', 'Codice fiscale', { required: true, maxLength: 40, autoComplete: 'off' })}
      {field('partita_iva', 'Partita IVA', { required: true, maxLength: 40, inputMode: 'numeric' })}
      {field('domicilio', 'Domicilio professionale', { required: true, maxLength: 300 })}
      {field('pec', 'PEC, se ce l’ha', { type: 'email', maxLength: 320 })}
    </div>
  )
}

function DocumentLinks({ document }: { document: ContractDocument }) {
  const what = document.kind === 'quadro' ? 'del contratto quadro' : `della lettera n. ${document.numero}`
  return (
    <span className="flex flex-wrap gap-2">
      <Button asChild variant="outline" size="sm">
        <a href={admin.contractPdfUrl(document.id)} aria-label={`PDF ${what}`}>
          <Download className="mr-2 size-4" />
          PDF
        </a>
      </Button>
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a href={admin.contractPdfUrl(document.id, true)} aria-label={`PDF firmato ${what}`}>
            <Download className="mr-2 size-4" />
            PDF firmato
          </a>
        </Button>
      )}
    </span>
  )
}

function FrameworkSection({ quadro }: { quadro: ContractDocument | null }) {
  return (
    <section aria-labelledby="contratti-quadro" className="space-y-3 px-6 py-6">
      <h2 id="contratti-quadro" className="text-sm font-medium">
        Contratto quadro
      </h2>
      {quadro === null ? (
        <p className="text-sm text-muted-foreground">Nessun contratto quadro: lo genera il primo match.</p>
      ) : (
        <dl className="space-y-3 text-sm">
          <Row label="Stato">
            <span className="flex flex-wrap items-center gap-2">
              {/* An element of its own, so a test finds the state by its words alone. */}
              <span>{DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</span>
              {quadro.attivo && <Badge variant="pill">Attivo</Badge>}
              {quadro.testo_bozza && <Badge variant="pill">Testo in bozza</Badge>}
            </span>
          </Row>
          <Row label="Firmato il">{quadro.signed_at ? formatDate(quadro.signed_at) : 'non ancora'}</Row>
          <Row label="Prossimo rinnovo">{quadro.rinnovo ? formatDate(quadro.rinnovo) : 'dopo la firma'}</Row>
          <Row label="Ultimo giorno per la disdetta">
            {quadro.ultimo_giorno_disdetta ? formatDate(quadro.ultimo_giorno_disdetta) : 'dopo la firma'}
          </Row>
          <Row label="Versione del testo">
            <span className="flex flex-wrap items-center gap-2">
              <span>{quadro.text_version}</span>
              {quadro.nuova_versione && <Badge variant="pill">Nuova versione disponibile</Badge>}
            </span>
          </Row>
          <Row label="Documento">
            <DocumentLinks document={quadro} />
          </Row>
        </dl>
      )}
    </section>
  )
}

function FiscalSection({ freelancerId, fiscale, onSaved }: { freelancerId: string; fiscale: Fiscal | null; onSaved: () => void }) {
  const [draft, setDraft] = useState<FiscalDraft>(() => draftFromFiscal(fiscale))
  const [saved, setSaved] = useState(false)
  const save = useMutation({
    mutationFn: (data: FiscalData) => admin.saveFiscal(freelancerId, data),
    onSuccess: () => {
      setSaved(true)
      onSaved()
    },
  })
  const failure = save.error instanceof ApiError ? save.error : null
  function submit(event: FormEvent) {
    event.preventDefault()
    setSaved(false)
    save.mutate(toFiscalData(draft))
  }
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Dati fiscali del freelance</h2>
      <form onSubmit={submit} className="max-w-2xl space-y-3 border p-4">
        <FiscalFields
          idPrefix="contratti"
          draft={draft}
          onChange={setDraft}
          wrong={(field) => failure?.fields.includes(field) || undefined}
        />
        <Button type="submit" size="sm" disabled={save.isPending}>
          {save.isPending ? 'Salvo…' : 'Salva i dati fiscali'}
        </Button>
        {saved && (
          <p role="status" className="text-sm text-muted-foreground">
            Dati fiscali salvati.
          </p>
        )}
        {save.error && (
          <p role="alert" className="text-sm text-destructive">
            {failure ? failure.message : 'Non riesco a salvare i dati fiscali.'}
          </p>
        )}
      </form>
    </section>
  )
}

function MatchesSection({
  matches,
  busy,
  onCancel,
  onClose,
  error,
}: {
  matches: Match[]
  busy: boolean
  onCancel: (match: Match) => void
  onClose: (match: Match) => void
  error: string | null
}) {
  return (
    <section className="space-y-3 px-6 pb-6">
      <h2 className="text-sm font-medium">Match</h2>
      {matches.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nessun match per questa persona.</p>
      ) : (
        <div className="overflow-x-auto border border-border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Azienda</TableHead>
                <TableHead>Creato</TableHead>
                <TableHead>Stato</TableHead>
                <TableHead>Lettera di incarico</TableHead>
                <TableHead>
                  <span className="sr-only">Azioni</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {matches.map((match) => (
                <TableRow key={match.id}>
                  <TableCell>
                    <p className="font-medium">{match.nome_azienda}</p>
                    <p className="text-xs text-muted-foreground">{match.figura_richiesta}</p>
                  </TableCell>
                  <TableCell className="text-muted-foreground">{formatDate(match.created_at)}</TableCell>
                  <TableCell>
                    <Badge variant="pill">{MATCH_STATE_LABELS[match.stato] ?? match.stato}</Badge>
                  </TableCell>
                  <TableCell className="space-y-1">
                    <p className="text-sm">
                      n. {match.lettera.numero} · {DOCUMENT_STATE_LABELS[match.lettera.stato] ?? match.lettera.stato}
                    </p>
                    <DocumentLinks document={match.lettera} />
                  </TableCell>
                  <TableCell className="text-right">
                    {match.stato === 'bozza' && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        aria-label={`Annulla il match con ${match.nome_azienda}`}
                        onClick={() => onCancel(match)}
                      >
                        Annulla
                      </Button>
                    )}
                    {match.stato === 'attivo' && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        aria-label={`Chiudi il match con ${match.nome_azienda}`}
                        onClick={() => onClose(match)}
                      >
                        Chiudi match
                      </Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </section>
  )
}

/** «Match e contratti» (REB-387, phase 2): the framework agreement with its dates, the
 *  tax data, and every match with its letter. No signing action yet: sending, resending,
 *  refreshing and recording a notice arrive with the electronic signature (phase 3). */
export function AdminContratti() {
  const { id } = useParams({ from: '/signedIn/admin/freelance/$id/contracts' })
  const client = useQueryClient()
  const person = useQuery({ queryKey: ['freelancer', id], queryFn: () => admin.freelancer(id) })
  const contracts = useQuery({ queryKey: ['contracts', id], queryFn: () => admin.contracts(id) })
  const refresh = () => void client.invalidateQueries({ queryKey: ['contracts', id] })
  const cancel = useMutation({ mutationFn: (matchId: string) => admin.cancelMatch(matchId), onSuccess: refresh })
  const close = useMutation({ mutationFn: (matchId: string) => admin.closeMatch(matchId), onSuccess: refresh })
  const [confirming, setConfirming] = useState<Match | null>(null)

  if (contracts.isError) return <Empty>Non riesco a leggere i contratti di questa persona.</Empty>
  if (contracts.isPending) return <Empty>Caricamento…</Empty>
  const data = contracts.data
  const name = person.data ? `${person.data.nome} ${person.data.cognome}` : ''
  const actionError = cancel.error ?? close.error
  const actionFailure =
    actionError instanceof ApiError
      ? actionError.message
      : actionError
        ? 'Non riesco a completare l’operazione.'
        : null
  return (
    <>
      <Header title={name ? `Match e contratti · ${name}` : 'Match e contratti'} />
      <FrameworkSection quadro={data.quadro} />
      <FiscalSection freelancerId={id} fiscale={data.fiscale} onSaved={refresh} />
      <MatchesSection
        matches={data.matches}
        busy={cancel.isPending || close.isPending}
        onCancel={setConfirming}
        onClose={(match) => close.mutate(match.id)}
        error={actionFailure}
      />
      <p className="px-6 pb-6">
        <Link to="/admin/freelance/$id" params={{ id }} className="inline-flex items-center gap-1 text-sm underline-offset-2 hover:underline">
          <ArrowLeft className="size-4" /> Torna alla scheda
        </Link>
      </p>
      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Annullare il match?</DialogTitle>
            <DialogDescription>
              {confirming &&
                `Il match con ${confirming.nome_azienda} e la lettera n. ${confirming.lettera.numero} diventano annullati, e il numero non si riusa. Il contratto quadro resta com’è.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirming(null)}>
              Indietro
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={cancel.isPending}
              onClick={() => {
                if (confirming) cancel.mutate(confirming.id, { onSettled: () => setConfirming(null) })
              }}
            >
              {cancel.isPending ? 'Annullo…' : 'Annulla il match'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
