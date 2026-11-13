import { useQuery } from '@tanstack/react-query'
import { Clock, Download, PenLine } from 'lucide-react'
import { Badge } from '@rebase/ui/badge'
import { Button } from '@rebase/ui/button'
import { member, type MemberContract } from '@/lib/api'
import { formatDate, formatDateTime, memberDocumentStateLabel } from '@/lib/format'
import { keys } from '@/lib/query-keys'
import { isHttpUrl } from '@/lib/url'

type Named = Pick<MemberContract, 'kind' | 'regime' | 'number'>

/** «della lettera n. 2026-001», «del contratto quadro»: a contract as a sentence names it. */
function whatOf(document: Named): string {
  if (document.kind === 'letter') return `della lettera n. ${document.number}`
  return document.regime === 'occasional' ? 'del contratto quadro per prestazioni occasionali' : 'del contratto quadro'
}
/** The same with an article in front: «la lettera n. 2026-001», «il contratto quadro». */
function objectOf(document: Named): string {
  if (document.kind === 'letter') return `la lettera n. ${document.number}`
  return document.regime === 'occasional' ? 'il contratto quadro per prestazioni occasionali' : 'il contratto quadro'
}

function Actions({ document }: { document: MemberContract }) {
  // «Firma» wants «il contratto quadro» / «la lettera n. X», the direct object `objectOf`
  // gives (REB-687); the signed copy's label reuses `whatOf`'s own genitive phrasing, the
  // one place both this page and the admin's name a document the same way (REB-392).
  const what = objectOf(document)
  const signingUrl = document.signing_url && isHttpUrl(document.signing_url) ? document.signing_url : null
  if (!signingUrl && !document.has_pdf_signed) return null
  return (
    <div className="flex flex-wrap gap-2">
      {signingUrl && (
        <Button asChild size="sm">
          <a href={signingUrl} target="_blank" rel="noreferrer" aria-label={`Firma ${what}`}>
            <PenLine className="mr-2 size-4" aria-hidden="true" />
            Firma il documento
          </a>
        </Button>
      )}
      {document.has_pdf_signed && (
        <Button asChild variant="outline" size="sm">
          <a
            href={member.contractPdfUrl(document.id)}
            // `whatOf` alone repeats «del contratto quadro» for the current framework
            // agreement and each one under «Contratti quadro precedenti»: the signature
            // time, not only the date, tells the links apart by name -- two can fall on
            // the same day (REB-433). `has_pdf_signed` never holds without `signed_at`
            // set first, whichever step moved the document there.
            aria-label={`Scarica la copia firmata ${whatOf(document)} del ${formatDateTime(document.signed_at!)}`}
          >
            <Download className="mr-2 size-4" aria-hidden="true" />
            Copia firmata
          </a>
        </Button>
      )}
    </div>
  )
}

function Framework({ framework }: { framework: MemberContract }) {
  return (
    <div className="space-y-3 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">{framework.label}</p>
        <Badge variant="pill">{memberDocumentStateLabel('framework', framework.status)}</Badge>
      </div>
      {framework.active && framework.signed_at && framework.renewal && framework.last_notice_day && (
        <p className="text-muted-foreground">
          Firmato il {formatDate(framework.signed_at)}: si rinnova da solo il {formatDate(framework.renewal)}, e per la
          disdetta c’è tempo fino al {formatDate(framework.last_notice_day)}.
        </p>
      )}
      <Actions document={framework} />
    </div>
  )
}

/** «Contratti quadro precedenti» (REB-392): every earlier framework agreement that was
 *  signed, under the current one -- a notice, or a newer framework replacing it, must
 *  not make its own signed copy disappear from the page. */
function PreviousFrameworks({ frameworks }: { frameworks: MemberContract[] }) {
  if (frameworks.length === 0) return null
  return (
    <div className="space-y-2 border bg-card p-4 text-sm">
      <p className="font-medium">Contratti quadro precedenti</p>
      <ul className="space-y-2">
        {frameworks.map((framework) => (
          <li key={framework.id} className="flex flex-wrap items-center gap-2">
            <Badge variant="pill">{memberDocumentStateLabel('framework', framework.status)}</Badge>
            {framework.signed_at && (
              <span className="text-muted-foreground">Firmato il {formatDate(framework.signed_at)}</span>
            )}
            <Actions document={framework} />
          </li>
        ))}
      </ul>
    </div>
  )
}

function Letter({ letter }: { letter: MemberContract }) {
  const period = letter.start
    ? letter.end
      ? `dal ${letter.start} al ${letter.end}`
      : `dal ${letter.start}`
    : null
  return (
    <li className="space-y-2 border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <p className="font-medium">{`${letter.label} n. ${letter.number}`}</p>
        <Badge variant="pill">{memberDocumentStateLabel('letter', letter.status)}</Badge>
      </div>
      <p className="text-muted-foreground">{[letter.customer, period].filter(Boolean).join(', ')}</p>
      <Actions document={letter} />
      {letter.pigro_url && isHttpUrl(letter.pigro_url) && (
        // Only once the letter's match is linked to its deal on Pigro (spec § 3.6).
        <div className="space-y-2">
          <Button asChild variant="outline" size="sm">
            <a
              href={letter.pigro_url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Le tue ore su Pigro per la lettera n. ${letter.number}`}
            >
              <Clock className="mr-2 size-4" aria-hidden="true" />
              Le tue ore su Pigro
            </a>
          </Button>
          <p className="text-muted-foreground">rebase legge le ore di questo progetto per la rendicontazione al cliente.</p>
        </div>
      )}
    </li>
  )
}

/** «Contratti» in the member area (REB-392): each framework agreement's state and dates,
 *  one per regime and each named by its type (REB-687), every earlier signed one
 *  underneath them, then each letter with its client and dates.
 *  A document that waits for the signature has «Firma il documento», which opens the
 *  signing site; a signed one offers its copy. A cancelled one says so here, since the
 *  signing site still opens it and only fails at the click. */
export function MemberContracts() {
  const contracts = useQuery({ queryKey: keys.memberContracts, queryFn: () => member.contracts() })
  const data = contracts.data
  const signed = data
    ? [data.framework, data.occasional_framework, ...data.previous_frameworks, ...data.letters].some(
        (document) => document?.has_pdf_signed,
      )
    : false
  return (
    <section aria-labelledby="me-contracts" className="space-y-3">
      <h2 id="me-contracts" className="text-lg font-semibold tracking-tight">
        Contratti
      </h2>
      {contracts.isError ? (
        <p className="text-sm text-muted-foreground">Non riesco a leggere i tuoi contratti. Riprova tra poco.</p>
      ) : !data ? (
        <p className="text-sm text-muted-foreground">Caricamento…</p>
      ) : data.framework === null && !data.occasional_framework && data.letters.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nessun contratto per ora: quando rebase ti propone un incarico, lo trovi qui da firmare.
        </p>
      ) : (
        <>
          {data.framework && <Framework framework={data.framework} />}
          {data.occasional_framework && <Framework framework={data.occasional_framework} />}
          <PreviousFrameworks frameworks={data.previous_frameworks} />
          {data.letters.length > 0 && (
            <ul className="space-y-3">
              {data.letters.map((letter) => (
                <Letter key={letter.id} letter={letter} />
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
