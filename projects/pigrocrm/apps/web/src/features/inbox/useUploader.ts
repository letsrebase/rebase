import { useCallback, useRef, useState } from 'react'
import { toast } from '@rebase/ui/sonner'
import { documents } from './labels'
import { useAfterUpload } from './queries'
import { sendFiles } from './upload'

/** A file that is on its way, or was refused and stays until a person dismisses it. */
export interface UploadRow {
  key: number
  name: string
  status: 'sending' | 'refused'
  reason: string | null
}

/**
 * The page's uploads (the brief, «Upload in flight» and «Refused»): each file is a row at
 * once, `sending` while the request reads it, and goes when its item is in the list; a file
 * the inbox refused stays as a red row with its reason until dismissed, never only a toast.
 * The lists are read again after every file, so the rows fill in as the files land.
 */
export function useUploader() {
  const [rows, setRows] = useState<UploadRow[]>([])
  const [busy, setBusy] = useState(false)
  const next = useRef(0)
  const afterUpload = useAfterUpload()

  const upload = useCallback(
    async (files: readonly File[]) => {
      if (files.length === 0) return
      setBusy(true)
      const keys = new Map<File, number>()
      const made = await sendFiles(files, {
        start: (file) => {
          next.current += 1
          keys.set(file, next.current)
          setRows((previous) => [
            ...previous,
            { key: next.current, name: file.name, status: 'sending', reason: null },
          ])
        },
        done: (file) => {
          setRows((previous) => previous.filter((row) => row.key !== keys.get(file)))
          afterUpload()
        },
        refused: (file, reason) =>
          setRows((previous) =>
            previous.map((row) =>
              row.key === keys.get(file) ? { ...row, status: 'refused', reason } : row,
            ),
          ),
      })
      setBusy(false)
      const count = made.filter((item) => item.kind !== 'archive').length
      if (count > 0) {
        toast.success(`${documents(count)} ${count === 1 ? 'caricato' : 'caricati'} in In arrivo.`)
      }
    },
    [afterUpload],
  )

  const dismiss = useCallback(
    (key: number) => setRows((previous) => previous.filter((row) => row.key !== key)),
    [],
  )

  return { rows, busy, upload, dismiss }
}
