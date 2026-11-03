/**
 * The `?from=` attribution parameter: where a visit came from, for PostHog (`from=digest`
 * on every link of the weekly mail). Mails sent before the English rename of 2026-11-02
 * carry the Italian `?da=` instead, and a mail stays in an inbox for good, so both are read.
 */
export function attributionOf(search: Record<string, unknown>): string | undefined {
  const value = search.from ?? search.da
  return typeof value === 'string' ? value : undefined
}
