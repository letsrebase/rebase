import { useEffect, useRef, useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { Button } from '@rebase/ui/button'
import { toProblem, type ProblemDetail } from '@/lib/api'
import {
  imageTooLarge,
  useLegalEntityImage,
  useRemoveLegalEntityImage,
  useUploadLegalEntityImage,
  type LegalEntityImageSlot,
  type LegalEntityRecord,
} from './queries'

/**
 * The two images an azienda owns (REB-629, spec 2026-10-03 §5): the logo every PDF of
 * this azienda draws in its header, the signature the offers draw at their foot. Each
 * block shows the current image, read from the API as the PDF reads it, offers «Carica»
 * and, once there is one, «Rimuovi», and shows the server's refusal under the control.
 * The server decides what a file is from its bytes; the `accept` here is a hint for the
 * picker, the same one the API enforces.
 */
export function LegalEntityImages({ azienda }: { azienda: LegalEntityRecord }) {
  return (
    <div className="grid gap-6 sm:grid-cols-2">
      <ImageBlock
        azienda={azienda}
        slot="logo"
        title="Logo"
        copy="Compare nell'intestazione di ogni PDF di questa azienda. PNG o SVG, fino a 1 MiB; senza logo l'intestazione riporta la ragione sociale."
        empty="Nessun logo"
        accept=".png,.svg,image/png,image/svg+xml"
        present={azienda.logo_key !== null}
      />
      <ImageBlock
        azienda={azienda}
        slot="firma"
        title="Firma"
        copy="Compare in calce alle offerte. Solo PNG, fino a 1 MiB."
        empty="Nessuna firma"
        accept=".png,image/png"
        present={azienda.firma_key !== null}
      />
    </div>
  )
}

function ImageBlock({
  azienda,
  slot,
  title,
  copy,
  empty,
  accept,
  present,
}: {
  azienda: LegalEntityRecord
  slot: LegalEntityImageSlot
  title: string
  copy: string
  empty: string
  accept: string
  present: boolean
}) {
  const image = useLegalEntityImage(azienda.id, slot, present)
  const upload = useUploadLegalEntityImage(azienda.id, slot)
  const remove = useRemoveLegalEntityImage(azienda.id, slot)
  const input = useRef<HTMLInputElement>(null)
  const [problem, setProblem] = useState<ProblemDetail | null>(null)
  const busy = upload.isPending || remove.isPending
  const inputId = `azienda-${slot}-${azienda.id}`
  // A read that failed for a reason other than «none» (storage down) is said, not
  // shown as an empty block with «Rimuovi» beside it.
  const shown = problem ?? (image.isError ? toProblem(image.error) : null)

  function choose(files: FileList | null) {
    const file = files?.[0]
    if (!file) return
    // The one refusal the browser already knows, said here with the service's own
    // sentence: behind nginx the body would otherwise come back as an HTML 413.
    const tooLarge = imageTooLarge(file)
    if (tooLarge) {
      setProblem(tooLarge)
      return
    }
    setProblem(null)
    upload.mutate(file, {
      onSuccess: () => toast.success(slot === 'logo' ? 'Logo caricato' : 'Firma caricata'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  function clear() {
    setProblem(null)
    remove.mutate(undefined, {
      onSuccess: () => toast.success(slot === 'logo' ? 'Logo rimosso' : 'Firma rimossa'),
      onError: (error) => setProblem(toProblem(error)),
    })
  }

  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{title}</p>
      <p className="text-muted-foreground text-xs">{copy}</p>
      {present && image.data ? (
        <Preview blob={image.data} alt={`${title} di ${azienda.nome}`} />
      ) : (
        <div className="text-muted-foreground flex h-20 items-center border border-dashed px-3 text-sm">
          {present && image.isLoading ? 'Caricamento…' : empty}
        </div>
      )}
      <div className="flex items-center gap-2">
        <input
          ref={input}
          id={inputId}
          type="file"
          aria-label={`Carica ${title.toLowerCase()}`}
          accept={accept}
          className="sr-only"
          disabled={busy}
          onChange={(event) => {
            choose(event.target.files)
            // Reset so choosing the same file twice in a row still fires a change.
            event.target.value = ''
          }}
        />
        <Button variant="outline" size="sm" disabled={busy} onClick={() => input.current?.click()}>
          {upload.isPending ? 'Caricamento…' : present ? 'Sostituisci' : 'Carica'}
        </Button>
        {present ? (
          <Button variant="ghost" size="sm" disabled={busy} onClick={clear}>
            Rimuovi
          </Button>
        ) : null}
      </div>
      {shown ? (
        <p className="text-destructive text-sm" role="alert">
          {shown.detail}
        </p>
      ) : null}
    </div>
  )
}

/** A Blob shown through an object URL, revoked when the Blob changes or the block leaves. */
function Preview({ blob, alt }: { blob: Blob; alt: string }) {
  const [url, setUrl] = useState<string | null>(null)
  useEffect(() => {
    const next = URL.createObjectURL(blob)
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the URL exists only once the Blob does, and must be revoked with it
    setUrl(next)
    return () => URL.revokeObjectURL(next)
  }, [blob])
  if (!url) return null
  return (
    <div className="flex h-20 items-center border px-3">
      <img src={url} alt={alt} className="max-h-16 max-w-full object-contain" />
    </div>
  )
}
