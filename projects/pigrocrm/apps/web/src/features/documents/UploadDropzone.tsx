import { useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { cn } from '@rebase/ui/cn'

interface Props {
  onFiles: (files: File[]) => void
  busy?: boolean
  /** The `accept` attribute, mirroring the backend's own ALLOWED_CONTENT_TYPES. */
  accept: string
}

/**
 * Drag and drop, plus a real `<input type="file">` behind it.
 *
 * The input is not decoration: a dropzone that only accepts a drag is unusable with a
 * keyboard and invisible to a screen reader, and it is the input's `accept` attribute
 * -- not the drag handler -- that tells the file picker what to offer. The drag path
 * does no type filtering of its own: the backend's `ALLOWED_CONTENT_TYPES` is the
 * authority, and a client-side second opinion is how the two start disagreeing.
 */
export function UploadDropzone({ onFiles, busy, accept }: Props) {
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  return (
    <div
      data-testid="upload-dropzone"
      onDragOver={(event) => {
        event.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault()
        setOver(false)
        const files = Array.from(event.dataTransfer?.files ?? [])
        if (files.length > 0) onFiles(files)
      }}
      className={cn(
        'border-2 border-dashed p-6 text-center transition-colors',
        over ? 'border-primary bg-primary/5' : 'border-border',
        busy && 'opacity-60',
      )}
    >
      <Upload aria-hidden className="mx-auto mb-2 h-6 w-6 text-muted-foreground" />
      <p className="text-sm text-muted-foreground">
        {busy ? 'Caricamento…' : 'Trascina qui un file, oppure'}{' '}
        {!busy && (
          <button
            type="button"
            className="underline underline-offset-2"
            onClick={() => inputRef.current?.click()}
          >
            scegline uno
          </button>
        )}
      </p>
      <input
        ref={inputRef}
        type="file"
        aria-label="Carica un documento"
        accept={accept}
        className="sr-only"
        disabled={busy}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? [])
          if (files.length > 0) onFiles(files)
          // Reset so choosing the same file twice in a row still fires a change.
          event.target.value = ''
        }}
      />
    </div>
  )
}
