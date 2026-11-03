import { fetchWithRefresh, toProblem } from './api'

/** The file name a `Content-Disposition` header carries, `filename*=` (RFC 5987) before
 *  the plain `filename=`; `''` when there is none. */
export function filenameFromDisposition(header: string | null): string {
  if (header === null) return ''
  const extended = /filename\*\s*=\s*[^']*'[^']*'([^;]+)/i.exec(header)?.[1]
  if (extended !== undefined) {
    try {
      return decodeURIComponent(extended.trim())
    } catch {
      // A malformed escape: fall through to the plain parameter.
    }
  }
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;]+))/i.exec(header)
  return (plain?.[1] ?? plain?.[2] ?? '').trim()
}

/**
 * Hands an OK response's body to the browser as a file download. A blob: URL carries no
 * `Content-Disposition`, so the server's own file name is read from the header and set
 * as the anchor's `download`.
 */
export async function saveResponse(response: Response): Promise<void> {
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filenameFromDisposition(response.headers.get('Content-Disposition'))
  // Snyk Code javascript/DOMXSS here is a false positive: the href is a blob: URL made
  // just above, never a script URL, and `download` makes the click save the file
  // whatever its type, so nothing it holds is ever rendered.
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  // Not synchronously: some browsers read the URL after `click()` returns, and a
  // revoked one cancels the save.
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

/**
 * Downloads `path` (an API path, `/api/...`) through `fetchWithRefresh`, so the file
 * request gets the space's prefix and the one refresh a plain link would not: opened
 * after the fifteen-minute access cookie expired, a bare anchor answers «Autenticazione
 * richiesta» with a good refresh cookie in the jar. Throws a `ProblemDetail` on a
 * refusal. `openapi-fetch` cannot express a binary response, so this is the one place
 * outside the typed client that reads one.
 */
export async function downloadFromApi(path: string): Promise<void> {
  const response = await fetchWithRefresh(path)
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    throw toProblem(payload, response.status)
  }
  await saveResponse(response)
}
