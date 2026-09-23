import {
  BadgeEuro,
  Ban,
  Download,
  FileCheck2,
  FileText,
  RefreshCw,
  Send,
  Trash2,
  Undo2,
} from 'lucide-react'
import { useState } from 'react'
import { toast } from '@rebase/ui/sonner'
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
import { Textarea } from '@rebase/ui/textarea'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { toProblem, type ProblemDetail } from '@/lib/api'
import { useCan } from '@/lib/auth'
import { toIsoDate } from '@/lib/dates'
import { formatInvoiceNumber } from './format'
import {
  downloadInvoiceArtifact,
  useAnnulInvoice,
  useConfirmProforma,
  useDeleteInvoice,
  useIssueInvoice,
  useMarkTransmitted,
  useProduceArtifacts,
  useSetPaymentState,
  type Invoice,
} from './queries'

export function InvoiceActions({
  invoice,
  onDeleted,
  onIssued,
}: {
  invoice: Invoice
  /** Called once the server has accepted the delete: the page this bar sits on no
   *  longer exists, so the caller decides where to go (the list, in practice). */
  onDeleted?: () => void
  /** Called with the row that now carries the number. For a draft fattura it is this
   *  same row; for a proforma it is a *new* row (spec 5), and the page this bar sits on
   *  has become a consumed proforma with nothing left to do on it, so the caller goes
   *  to the fattura, where «XML FatturaPA» is (ORB-134). */
  onIssued?: (issued: Invoice) => void
}) {
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const [annulOpen, setAnnulOpen] = useState(false)
  const [motivo, setMotivo] = useState('')
  // The two dates default to today, read from local parts (`toIsoDate`): late in the
  // evening the UTC route would propose tomorrow, which the server refuses.
  const [collectOpen, setCollectOpen] = useState(false)
  const [dataIncasso, setDataIncasso] = useState(() => toIsoDate(new Date()))
  const [transmitOpen, setTransmitOpen] = useState(false)
  const [dataTrasmissione, setDataTrasmissione] = useState(() => toIsoDate(new Date()))
  // REB-294: one check per button, each against the action its own service passes to
  // `require_write`/`require_admin`. This bar used to read the role only for
  // «Segna trasmessa», so a readonly person stood in front of «Emetti», «Annulla» and
  // «Elimina» on every draft and every issued invoice, and the press answered a 403.
  // The `may` prefix because the document's state already owns `canIssue`, `canDelete`
  // and the visibility flags below: a button exists only when its role check and its
  // state check are both true, and the two reads say which is which.
  const mayConfirm = useCan('confirm_proforma')
  const mayIssue = useCan('issue_invoice')
  const mayAnnul = useCan('annul_invoice')
  const mayDelete = useCan('delete_invoice')
  const maySetPayment = useCan('set_payment_state')
  const mayProduce = useCan('produce_invoice_artifacts')
  const mayMarkTransmitted = useCan('mark_transmitted_externally')

  const issue = useIssueInvoice(invoice.id)
  const confirm = useConfirmProforma(invoice.id)
  const annul = useAnnulInvoice(invoice.id)
  const artifacts = useProduceArtifacts(invoice.id)
  const payment = useSetPaymentState(invoice.id)
  const transmitted = useMarkTransmitted(invoice.id)
  const remove = useDeleteInvoice()

  const isDraftFattura = invoice.tipo === 'fattura' && invoice.stato === 'bozza'
  const isProformaReady = invoice.tipo === 'proforma' && invoice.stato === 'confermata'
  // The step before «Emetti» on a proforma (ORB-132): until it existed a proforma
  // created from the web could never be issued from the web, since `issue` wants a
  // confirmed one and nothing here confirmed it.
  const isDraftProforma = invoice.tipo === 'proforma' && invoice.stato === 'bozza'
  const canIssue = isDraftFattura || isProformaReady
  const isIssued = invoice.tipo === 'fattura' && invoice.stato === 'emessa'
  // What never consumed a number can go (slice 3 §4: a draft is «cancellabile»): a
  // draft fattura, and a proforma until it is consumed by the emission it precedes.
  // The server (`soft_delete`, and the CHECK behind it) refuses everything else with a
  // 409, so this mirrors the rule rather than owning it. An issued invoice is annulled.
  const canDelete = isDraftFattura || (invoice.tipo === 'proforma' && invoice.stato !== 'consumata')
  // An invoice pigroCRM imported never had its own XML rendered here: the one on file is
  // whatever the system that issued it transmitted at the time, so offering to
  // regenerate it would silently replace a legally-filed document with a
  // reconstruction. The flag is the column's presence, never its value.
  const isImported = invoice.importata_da != null
  // Collection is a fact about an issued invoice and nothing else (`set_payment_state`
  // answers 409 for every other state), so the two payment buttons only exist there.
  // A proforma is never collected and a draft is not yet a document.
  const collected = invoice.stato_pagamento === 'incassato'
  // Settable once, admin-only on the server (`mark_transmitted_externally`, and the
  // `mayMarkTransmitted` check above is the UI half of that gate), and meaningless for
  // an imported invoice: the system that issued it is the one that transmitted it, and
  // the column already says so. The button follows all four.
  const showMarkTransmitted =
    isIssued && mayMarkTransmitted && !isImported && invoice.trasmessa_esternamente_il === null
  // REB-168: a download button exists only when its file does, read from the row the way
  // the proforma branch below already reads `pdf_document_id`. «XML FatturaPA» used to
  // show on every issued invoice, and on one whose render had failed the press answered
  // the raw `invoice_artifact …#xml not found`. A render can fail whole or half-way
  // (`produce_artifacts` commits the PDF before it exports the XML), and since REB-143
  // the emission still succeeds either way, so an issued row with an empty id is a
  // state this bar meets, not an accident.
  const hasPdf = invoice.pdf_document_id != null
  const hasXml = invoice.xml_document_id != null
  // Said only where «Rigenera documenti» is the way out: an imported invoice never
  // renders its own files (`produce_artifacts` refuses it), so pointing there would be
  // pointing at a button that is not on its page.
  const missingFiles =
    isIssued && !isImported ? missingFilesSentence(hasPdf, hasXml, mayProduce) : null

  /**
   * No `window.confirm` here, unlike «Emetti» and «Elimina»: confirming consumes no
   * number, and a confirmed proforma is still editable (`_is_editable` in
   * `InvoiceService`: header, lines) and still deletable, so a misclick forecloses
   * nothing. There is no way back to «Bozza», and none is needed: the next step, «Emetti»,
   * is the one that asks. The server's refusals -- a fattura, a proforma that is not a
   * draft, a proforma without lines -- land in the same banner every other action uses.
   */
  function onConfirm() {
    setProblem(null)
    confirm.mutate(undefined, {
      onSuccess: () => toast.success('Proforma confermata'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  /**
   * Emission and the render are two steps, deliberately.
   *
   * `issue()` is one transaction and does not produce the artefacts; the endpoint renders
   * them right after its commit, in a second one. That boundary exists because calling
   * the render from inside emission made emission stop being one transaction, and an
   * artefact commit then survived a rollback.
   *
   * So a failed render is **not** a failed emission. The invoice has its number and is
   * fiscally complete; it is merely unprinted, and `produce_artifacts` regenerates
   * deterministically from the frozen snapshot whenever it is called again. Since
   * REB-143 the endpoint says so itself: it answers the issued row whatever the render
   * did, read back after it, so a missing `pdf_document_id` or `xml_document_id` is the
   * render that failed. Only then does this bar try once more, and only if that also
   * fails does it warn. Saying "emission failed" here would be the more dangerous lie,
   * so the message says exactly what happened and what to press.
   *
   * The retry targets `issued.id`, not `invoice.id`: from a proforma the two differ,
   * and rendering the proforma would print the wrong document (ORB-134). The toast
   * names the number, since it is the one fact the person cannot see on the page they
   * pressed the button on.
   *
   * `mutateAsync` and not `mutate` with callbacks: `onIssued` navigates away, this bar
   * unmounts, and TanStack Query drops the callbacks handed to `mutate()` once the
   * observer has no listeners. The warning would be lost on exactly the flow this
   * exists for. A promise's `catch` does not care whether anything is still mounted,
   * and the toast is global.
   */
  function onIssue() {
    if (!window.confirm(`Emettere questo documento? Il numero assegnato non è più modificabile.`))
      return
    setProblem(null)
    issue.mutate(
      {},
      {
        onSuccess: (issued) => {
          toast.success(`Fattura ${formatInvoiceNumber(issued)} emessa`)
          if (issued.pdf_document_id == null || issued.xml_document_id == null) {
            void artifacts.mutateAsync(issued.id).catch(() =>
              toast.warning(
                'Documento emesso correttamente, ma PDF e XML non sono stati generati. ' +
                  'Riprova con «Rigenera documenti»: il numero resta quello.',
              ),
            )
          }
          onIssued?.(issued)
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  /**
   * The same soft delete `discard_proforma` performs from the MCP (ORB-37). Here the
   * confirm is against a misclick, the way «Emetti» has one. Unlike «Annulla» there is
   * no motivo to ask for: nothing fiscal happened yet, and the list is where you land.
   */
  function onDelete() {
    const what = invoice.tipo === 'proforma' ? 'questa proforma' : 'questa bozza'
    if (!window.confirm(`Eliminare ${what}? Non ha un numero, quindi non resta traccia nel registro.`))
      return
    setProblem(null)
    remove.mutate(invoice.id, {
      onSuccess: () => {
        toast.success(invoice.tipo === 'proforma' ? 'Proforma eliminata' : 'Bozza eliminata')
        onDeleted?.()
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function onAnnul() {
    setProblem(null)
    annul.mutate(motivo, {
      onSuccess: () => {
        toast.success('Documento annullato. Il numero resta nel registro.')
        setAnnulOpen(false)
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function onCollect() {
    setProblem(null)
    payment.mutate(
      { stato_pagamento: 'incassato', data_incasso: dataIncasso },
      {
        onSuccess: () => {
          toast.success('Incasso registrato')
          setCollectOpen(false)
        },
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  /**
   * The way back. A collection recorded on the wrong invoice is an ordinary mistake,
   * and the server clears the date with the state (`set_payment_state`), so undoing it
   * is one call with no dialog -- the confirm is against a misclick, nothing more.
   */
  function onUncollect() {
    if (!window.confirm('Segnare questa fattura come ancora da incassare? La data di incasso viene tolta.'))
      return
    setProblem(null)
    payment.mutate(
      { stato_pagamento: 'da_incassare', data_incasso: null },
      {
        onSuccess: () => toast.success('Fattura segnata da incassare'),
        onError: (error) => setProblem(toProblem(error)),
      },
    )
  }

  function onTransmit() {
    setProblem(null)
    transmitted.mutate(dataTrasmissione, {
      onSuccess: () => {
        toast.success('Trasmissione registrata')
        setTransmitOpen(false)
      },
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  async function onDownload(kind: 'pdf' | 'xml') {
    try {
      await downloadInvoiceArtifact(invoice.id, kind)
    } catch (error) {
      toast.error(
        downloadErrorSentence(
          toProblem(error),
          kind,
          invoice.tipo === 'proforma' ? 'proforma' : 'fattura',
          isIssued && !isImported && mayProduce,
        ),
      )
    }
  }

  return (
    <div className="space-y-3">
      {problem ? <QueryErrorBanner error={problem} /> : null}

      <div className="flex flex-wrap gap-2">
        {isDraftProforma && mayConfirm ? (
          <Button onClick={onConfirm} disabled={confirm.isPending}>
            <FileCheck2 className="mr-2 size-4" />
            Conferma
          </Button>
        ) : null}

        {canIssue && mayIssue ? (
          <Button onClick={onIssue} disabled={issue.isPending}>
            <FileCheck2 className="mr-2 size-4" />
            Emetti
          </Button>
        ) : null}

        {isIssued ? (
          <>
            {/* The state of the money comes first among an issued invoice's actions:
                marking a collection is the thing done most often to an invoice after
                it leaves, and the one the list's «Pagamento» pill is waiting for. */}
            {maySetPayment &&
              (collected ? (
                <Button variant="outline" onClick={onUncollect} disabled={payment.isPending}>
                  <Undo2 className="mr-2 size-4" />
                  Segna da incassare
                </Button>
              ) : (
                <Button onClick={() => setCollectOpen(true)} disabled={payment.isPending}>
                  <BadgeEuro className="mr-2 size-4" />
                  Segna incassata
                </Button>
              ))}
            {showMarkTransmitted ? (
              <Button
                variant="outline"
                onClick={() => setTransmitOpen(true)}
                disabled={transmitted.isPending}
              >
                <Send className="mr-2 size-4" />
                Segna trasmessa
              </Button>
            ) : null}
            {hasPdf ? (
              <Button variant="outline" onClick={() => void onDownload('pdf')}>
                <Download className="mr-2 size-4" />
                PDF
              </Button>
            ) : null}
            {isImported || !hasXml ? null : (
              <Button variant="outline" onClick={() => void onDownload('xml')}>
                <Download className="mr-2 size-4" />
                XML FatturaPA
              </Button>
            )}
            {!isImported && mayProduce ? (
              <Button
                variant="outline"
                onClick={() =>
                  artifacts.mutate(undefined, {
                    onSuccess: () => toast.success('Documenti rigenerati'),
                    onError: (error) => toast.error(toProblem(error).detail),
                  })
                }
                disabled={artifacts.isPending}
              >
                <RefreshCw className="mr-2 size-4" />
                Rigenera documenti
              </Button>
            ) : null}
            {mayAnnul ? (
              <Button variant="destructive" onClick={() => setAnnulOpen(true)}>
                <Ban className="mr-2 size-4" />
                Annulla
              </Button>
            ) : null}
          </>
        ) : null}

        {invoice.tipo === 'proforma' && invoice.stato !== 'consumata' ? (
          invoice.pdf_document_id === null ? (
            /* Until now the web never produced a proforma's PDF: the download button
               asked for a file that did not exist and got a 404 (ORB-30). The same
               endpoint emission uses renders it; the preview beside shows it at once.
               Rendering it is `produce_invoice_artifacts` on the server, so the button
               follows that role too. */
            mayProduce ? (
              <Button
                variant="outline"
                onClick={() =>
                  artifacts.mutate(undefined, {
                    onSuccess: () => toast.success('PDF proforma generato'),
                    onError: (error) => toast.error(toProblem(error).detail),
                  })
                }
                disabled={artifacts.isPending}
              >
                <FileText className="mr-2 size-4" />
                Genera PDF proforma
              </Button>
            ) : null
          ) : (
            <Button variant="outline" onClick={() => void onDownload('pdf')}>
              <Download className="mr-2 size-4" />
              PDF proforma
            </Button>
          )
        ) : null}

        {canDelete && mayDelete ? (
          <Button variant="destructive" onClick={onDelete} disabled={remove.isPending}>
            <Trash2 className="mr-2 size-4" />
            {invoice.tipo === 'proforma' ? 'Elimina proforma' : 'Elimina bozza'}
          </Button>
        ) : null}
      </div>

      {missingFiles ? (
        <p className="text-muted-foreground text-sm" data-testid="missing-invoice-files">
          {missingFiles}
        </p>
      ) : null}

      <Dialog open={collectOpen} onOpenChange={setCollectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Registra l&apos;incasso</DialogTitle>
          </DialogHeader>
          {/* The date is required by the server («un incasso senza data non è un
              incasso»): the day the money arrived is what the cash view and the fiscal
              estimate read, so it is asked for here rather than assumed to be today. */}
          <p className="text-muted-foreground text-sm">
            La fattura passa a «Incassato» e la data entra nella vista economica dell&apos;anno
            in cui cade. Si può tornare indietro.
          </p>
          <div className="space-y-2">
            <Label htmlFor="data-incasso">Data incasso</Label>
            <Input
              id="data-incasso"
              type="date"
              required
              value={dataIncasso}
              onChange={(event) => setDataIncasso(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCollectOpen(false)}>
              Chiudi
            </Button>
            <Button onClick={onCollect} disabled={payment.isPending || dataIncasso === ''}>
              Registra incasso
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={transmitOpen} onOpenChange={setTransmitOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Segna come trasmessa</DialogTitle>
          </DialogHeader>
          {/* Once. This is the column that tells an invoice that never left -- still
              annullable -- from one already deposited with the Agenzia delle Entrate,
              and it stays frozen for exactly that reason (`mark_transmitted_externally`). */}
          <p className="text-muted-foreground text-sm">
            Registra che l&apos;XML è stato consegnato all&apos;intermediario o allo SDI fuori da
            PigroCRM. Non si può annullare.
          </p>
          <div className="space-y-2">
            <Label htmlFor="data-trasmissione">Data di trasmissione</Label>
            <Input
              id="data-trasmissione"
              type="date"
              required
              value={dataTrasmissione}
              onChange={(event) => setDataTrasmissione(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTransmitOpen(false)}>
              Chiudi
            </Button>
            <Button
              onClick={onTransmit}
              disabled={transmitted.isPending || dataTrasmissione === ''}
            >
              Segna trasmessa
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={annulOpen} onOpenChange={setAnnulOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Annulla documento</DialogTitle>
          </DialogHeader>
          {/* Annulment is not deletion: the number stays in the register and the row
              stays readable, because a fiscal register with a hole in it is a problem
              with the tax authority. The reason is required for the same purpose --
              it is what the document's own history will say later. */}
          <p className="text-muted-foreground text-sm">
            Il numero resta nel registro e il documento resta consultabile. Per correggere
            un errore si emette un nuovo documento, non si modifica questo.
          </p>
          <div className="space-y-2">
            <Label htmlFor="motivo-annullamento">Motivo</Label>
            <Textarea
              id="motivo-annullamento"
              value={motivo}
              rows={3}
              onChange={(event) => setMotivo(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAnnulOpen(false)}>
              Chiudi
            </Button>
            <Button variant="destructive" onClick={onAnnul} disabled={annul.isPending}>
              Annulla documento
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

/**
 * What the bar says under an issued invoice whose PDF or XML does not exist, or `null`
 * when both do. The pointer at «Rigenera documenti» follows the role: a person who cannot
 * press it is told where the file comes from rather than to press a button they do not
 * have (REB-294 hides it from a readonly role).
 */
function missingFilesSentence(
  hasPdf: boolean,
  hasXml: boolean,
  mayProduce: boolean,
): string | null {
  if (hasPdf && hasXml) return null
  const both = !hasPdf && !hasXml
  const what = both
    ? 'Il PDF e l’XML FatturaPA di questa fattura non sono stati generati.'
    : hasPdf
      ? 'L’XML FatturaPA di questa fattura non è stato generato.'
      : 'Il PDF di questa fattura non è stato generato.'
  const how = mayProduce
    ? `Premi «Rigenera documenti» per ${both ? 'generarli' : 'generarlo'}: il numero resta quello.`
    : `${both ? 'Si generano' : 'Si genera'} con «Rigenera documenti», che il tuo ruolo non può usare.`
  return `${what} ${how}`
}

/**
 * The toast for a failed download. A 404 here means the row points at no file, at a
 * document with no current version, or at a stored file that is gone, and the server's
 * detail for each is written for a log: `invoice_artifact <uuid>#xml not found` (REB-168,
 * seen on production 2026-09-11), `document_blob <key> not found`. It becomes a sentence
 * naming the file, and «Rigenera documenti» when that button is on the page: it produces a
 * missing file and repairs a lost one with identical bytes (`produce_artifacts`). Keyed on
 * the status, which `toProblem` always takes from the response, rather than on the
 * entity, so a lost `document_blob` gets the same sentence. Any other failure keeps the
 * server's `detail` as every other action on this bar does.
 */
function downloadErrorSentence(
  problem: ProblemDetail,
  kind: 'pdf' | 'xml',
  noun: 'fattura' | 'proforma',
  canRegenerate: boolean,
): string {
  if (problem.status !== 404) return problem.detail
  const file = kind === 'xml' ? 'L’XML FatturaPA' : 'Il PDF'
  const retry = canRegenerate ? ' Premi «Rigenera documenti» per generarlo di nuovo.' : ''
  return `${file} di questa ${noun} non è disponibile.${retry}`
}
