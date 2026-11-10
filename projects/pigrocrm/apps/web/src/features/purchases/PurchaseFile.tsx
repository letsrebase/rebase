import { ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import { LoadingState } from '@/components/LoadingState'
import { QueryErrorBanner } from '@/components/QueryErrorBanner'
import { Button } from '@rebase/ui/button'
import { useInboxItem } from '@/features/inbox/queries'
import { fetchWithRefresh, toProblem } from '@/lib/api'

type FileState =
  | { id: string; attempt: number; url: string; error?: undefined }
  | { id: string; attempt: number; url?: undefined; error: unknown }

/**
 * The bytes of an «In arrivo» item, fetched with the session (an `<img src>` or an `<object
 * data>` could not carry it) as an object URL released when the item changes or the page
 * goes. `loading` until the answer for this item and this attempt is in; `retry` asks again.
 */
function useItemFile(id: string, enabled: boolean) {
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<FileState | null>(null)
  useEffect(() => {
    if (!enabled) return
    let released = false
    let url: string | null = null
    void (async () => {
      try {
        const response = await fetchWithRefresh(`/api/inbox/items/${id}/file`)
        if (!response.ok) {
          throw toProblem(await response.json().catch(() => null), response.status)
        }
        const blob = await response.blob()
        if (released) return
        url = URL.createObjectURL(blob)
        setState({ id, attempt, url })
      } catch (error) {
        if (!released) setState({ id, attempt, error })
      }
    })()
    return () => {
      released = true
      if (url !== null) URL.revokeObjectURL(url)
    }
  }, [id, enabled, attempt])
  const current = state?.id === id && state.attempt === attempt ? state : null
  return {
    url: current?.url ?? null,
    error: current?.error,
    failed: current !== null && current.url === undefined,
    loading: enabled && current === null,
    retry: () => setAttempt((previous) => previous + 1),
  }
}

/**
 * The file of an «In arrivo» item, to check the form against (the approved brief of
 * 2026-11-10, «Capture»): a photo as an image, a PDF in the browser's own viewer, anything
 * else (a FatturaPA's XML) only behind «Apri il file». Nothing at all for an item that has
 * no file. It reads; the item is never changed from here.
 */
export function PurchaseFile({ inboxItemId }: { inboxItemId: string }) {
  const item = useInboxItem(inboxItemId)
  const hasFile = item.data?.has_file === true
  const file = useItemFile(inboxItemId, hasFile)

  if (item.isError) return <QueryErrorBanner error={item.error} onRetry={() => void item.refetch()} />
  if (item.isPending) return <LoadingState label="Leggo il file…" />
  if (!hasFile) return null

  const name = item.data.original_name ?? 'File'
  const type = item.data.file_type
  const picture = type === 'png' || type === 'jpeg'
  const pdf = type === 'pdf'

  return (
    <section aria-label="Il file" className="flex h-full min-h-[24rem] flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 flex-1 truncate text-sm font-medium" title={name}>
          {name}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={file.url === null}
          onClick={() => {
            if (file.url !== null) window.open(file.url, '_blank')
          }}
        >
          <ExternalLink className="mr-2 size-4" />
          Apri il file
        </Button>
      </div>
      {file.loading ? <LoadingState label="Leggo il file…" /> : null}
      {file.failed ? (
        <QueryErrorBanner error={file.error} onRetry={file.retry} />
      ) : file.url !== null && picture ? (
        <div className="min-h-0 flex-1 overflow-auto border bg-muted/30">
          <img src={file.url} alt={name} className="mx-auto h-auto max-w-full object-contain" />
        </div>
      ) : file.url !== null && pdf ? (
        <object
          data={file.url}
          type="application/pdf"
          aria-label={name}
          className="min-h-0 w-full flex-1 border"
        >
          <p className="p-3 text-sm text-muted-foreground">
            Il browser non mostra il PDF qui: usa «Apri il file».
          </p>
        </object>
      ) : null}
    </section>
  )
}
