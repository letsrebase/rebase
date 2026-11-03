/**
 * A search parameter by its English name, or by the Italian one links written before the
 * English rename of 2026-11-02 carry (`?apri=`, `?scadute=`, `?mese=`...). Mails, digests,
 * bookmarks and push payloads outlive a release, so both are read; the Italian name is a
 * string here and nowhere an identifier.
 */
export function legacyParam(search: Record<string, unknown>, name: string, legacyName: string): unknown {
  return search[name] ?? search[legacyName]
}
