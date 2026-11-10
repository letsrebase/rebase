import { toProblem } from '@/lib/api'
import { MAX_FILE_BYTES, type InboxItem } from './labels'
import { uploadInboxFiles } from './queries'

/** What happens to the files of an upload, one by one, so a page can draw a row for each. */
export interface UploadEvents {
  /** A file is about to go: the page draws its row. */
  start: (file: File) => void
  /** It went: the items it made (a ZIP makes its entries too). */
  done: (file: File, items: InboxItem[]) => void
  /** It did not: the sentence that says why, in the brief's shape «Non caricato: ...». */
  refused: (file: File, reason: string) => void
}

/** The refusal's sentence: the server's own when there is one, after «Non caricato:». */
export function refusal(detail: string): string {
  return `Non caricato: ${detail.charAt(0).toLowerCase()}${detail.slice(1)}`
}

/**
 * Sends each file in a request of its own, in order. One file per request is what makes a
 * refusal belong to a file: the inbox refuses a whole request for one loose file it does not
 * take (422, nothing written), and a person who dropped five files needs to know which one.
 * A file over the ceiling never leaves the browser. Resolves with every item made.
 */
export async function sendFiles(files: readonly File[], on: UploadEvents): Promise<InboxItem[]> {
  const made: InboxItem[] = []
  for (const file of files) {
    on.start(file)
    if (file.size > MAX_FILE_BYTES) {
      on.refused(file, 'Non caricato: supera i 100 MiB.')
      continue
    }
    try {
      const items = await uploadInboxFiles([file])
      made.push(...items)
      on.done(file, items)
    } catch (error) {
      on.refused(file, refusal(toProblem(error).detail))
    }
  }
  return made
}
