import { useNavigate } from '@tanstack/react-router'
import { Camera, FileUp } from 'lucide-react'
import { useRef } from 'react'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@rebase/ui/sheet'
import { toast } from '@rebase/ui/sonner'
import { ACCEPT, documents } from './labels'
import { useAfterUpload } from './queries'
import { sendFiles } from './upload'

/**
 * «Carica un documento» of the phone's «+» (the brief, REB-984): two choices, «Scatta una foto» (the
 * camera, where the device has one) and «Scegli un file». The upload adds the document to «In arrivo»
 * without leaving the page, and a toast says so with «Apri» (the brief: «2 documenti caricati in
 * In arrivo.»). A file the inbox refuses has no row to stay as here, so each refusal is its own toast
 * with the reason; the page keeps the red row for what is dropped on it.
 */
export function QuickUpload({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const navigate = useNavigate()
  const afterUpload = useAfterUpload()
  const camera = useRef<HTMLInputElement>(null)
  const files = useRef<HTMLInputElement>(null)

  async function upload(chosen: File[]) {
    if (chosen.length === 0) return
    onOpenChange(false)
    const refusals: string[] = []
    const made = await sendFiles(chosen, {
      start: () => {},
      done: () => afterUpload(),
      refused: (file, reason) => refusals.push(`${file.name}: ${reason}`),
    })
    const count = made.filter((item) => item.kind !== 'archive').length
    if (count > 0) {
      toast.success(`${documents(count)} ${count === 1 ? 'caricato' : 'caricati'} in In arrivo.`, {
        action: {
          label: 'Apri',
          onClick: () => void navigate({ to: '/app/inbox', search: { tab: 'pending' } }),
        },
      })
    }
    for (const refusal of refusals) toast.error(refusal)
  }

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="bottom" className="pb-[env(safe-area-inset-bottom)]" closeLabel="Chiudi">
          <SheetHeader>
            <SheetTitle>Carica un documento</SheetTitle>
            <SheetDescription>Va in «In arrivo», dove lo guardi con calma.</SheetDescription>
          </SheetHeader>
          <ul className="divide-y border-y">
            <li>
              <button
                type="button"
                className="flex min-h-14 w-full items-center gap-4 px-4 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                onClick={() => camera.current?.click()}
              >
                <Camera className="size-5 shrink-0" aria-hidden="true" />
                <span className="text-sm font-medium">Scatta una foto</span>
              </button>
            </li>
            <li>
              <button
                type="button"
                className="flex min-h-14 w-full items-center gap-4 px-4 py-2 text-left hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                onClick={() => files.current?.click()}
              >
                <FileUp className="size-5 shrink-0" aria-hidden="true" />
                <span className="text-sm font-medium">Scegli un file</span>
              </button>
            </li>
          </ul>
        </SheetContent>
      </Sheet>
      {/* Outside the sheet: it unmounts the moment it closes, and a picker that is still open
          would answer into nothing. */}
      <input
        ref={camera}
        type="file"
        accept="image/*"
        capture="environment"
        aria-label="Scatta una foto"
        className="sr-only"
        onChange={(event) => {
          void upload(Array.from(event.target.files ?? []))
          event.target.value = ''
        }}
      />
      <input
        ref={files}
        type="file"
        multiple
        accept={ACCEPT}
        aria-label="Scegli un file"
        className="sr-only"
        onChange={(event) => {
          void upload(Array.from(event.target.files ?? []))
          event.target.value = ''
        }}
      />
    </>
  )
}
