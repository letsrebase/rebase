import { useQuery } from '@tanstack/react-query'
import { Download, PenLine } from 'lucide-react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { member, type MemberContract } from '@/lib/api'
import { whatOf } from '@/lib/contracts'
import { MEMBER_DOCUMENT_STATE_LABELS, formatDate } from '@/lib/format'

function Actions({ document }: { document: MemberContract }) {
  // «Firma» wants «il contratto quadro» / «la lettera n. X», its direct object; the
  // signed copy's label reuses `whatOf`'s own genitive phrasing, the one place both
  // this page and the admin's name a document the same way (REB-392).
  const what = document.kind === 'quadro' ? 'il contratto quadro' : `la lettera n. ${document.numero}`
  if (!document.signing_url && !document.ha_pdf_firmato) return null
  return (
    <div className="flex flex-wrap gap-2">
      {document.signing_url && (
        <Button asChild size="sm">
          <a href={document.signing_url} target="_blank" rel="noreferrer" aria-label={`Firma ${what}`}>
            <PenLine className="mr-2 size-4" aria-hidden="true" />
            Firma il documento
          </a>
        </Button>
      )}
      {document.ha_pdf_firmato && (
        <Button asChild variant="outline" size="sm">
          <a
            href={member.contractPdfUrl(document.id)}
            aria-label={`Scarica la copia firmata ${whatOf(document)}`}
          >
            <Download className="mr-2 size-4" aria-hidden="true" />
            Copia firmata
          </a>
        </Button>
      )}
    </div>
  )
}

function Framework({ quadro }: { quadro: MemberContract }) {
  return (
    <div className="space-y-3 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">Contratto quadro</p>
        <Badge variant="pill">{MEMBER_DOCUMENT_STATE_LABELS[quadro.stato] ?? quadro.stato}</Badge>
      </div>
      {quadro.attivo && quadro.signed_at && quadro.rinnovo && quadro.ultimo_giorno_disdetta && (
        <p className="text-muted-foreground">
          Firmato il {formatDate(quadro.signed_at)}: si rinnova da solo il {formatDate(quadro.rinnovo)}, e per la
          disdetta c’è tempo fino al {formatDate(quadro.ultimo_giorno_disdetta)}.
        </p>
      )}
      <Actions document={quadro} />
    </div>
  )
}

function Letter({ lettera }: { lettera: MemberContract }) {
  const period = lettera.inizio
    ? lettera.fine
      ? `dal ${lettera.inizio} al ${lettera.fine}`
      : `dal ${lettera.inizio}`
    : null
  return (
    <li className="space-y-2 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">Lettera di incarico n. {lettera.numero}</p>
        <Badge variant="pill">{MEMBER_DOCUMENT_STATE_LABELS[lettera.stato] ?? lettera.stato}</Badge>
      </div>
      <p className="text-muted-foreground">{[lettera.cliente, period].filter(Boolean).join(', ')}</p>
      <Actions document={lettera} />
    </li>
  )
}

/** «Contratti» in the member area (REB-392): the framework agreement's state and dates,
 *  then each letter with its client and dates. A document that waits for the signature
 *  has «Firma il documento», which opens the signing site; a signed one offers its copy.
 *  A cancelled one says so here, since the signing site still opens it and only fails
 *  at the click. */
export function MemberContratti() {
  const contracts = useQuery({ queryKey: ['me', 'contracts'], queryFn: () => member.contracts() })
  const data = contracts.data
  const signed = data ? [data.quadro, ...data.lettere].some((document) => document?.ha_pdf_firmato) : false
  return (
    <section aria-labelledby="me-contratti" className="space-y-3">
      <h2 id="me-contratti" className="text-lg font-semibold tracking-tight">
        Contratti
      </h2>
      {contracts.isError ? (
        <p className="text-sm text-muted-foreground">Non riesco a leggere i tuoi contratti. Riprova tra poco.</p>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">Caricamento…</p>
      ) : data.quadro === null && data.lettere.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.
        </p>
      ) : (
        <>
          {data.quadro && <Framework quadro={data.quadro} />}
          {data.lettere.length > 0 && (
            <ul className="space-y-3">
              {data.lettere.map((lettera) => (
                <Letter key={lettera.id} lettera={lettera} />
              ))}
            </ul>
          )}
          {signed && (
            <p className="text-xs text-muted-foreground">
              La copia firmata ha in fondo una pagina in inglese: è il certificato della firma elettronica.
            </p>
          )}
        </>
      )}
    </section>
  )
}
