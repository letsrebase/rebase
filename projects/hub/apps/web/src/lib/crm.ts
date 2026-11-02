const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i

/** The deal's id out of what an admin pastes: the id itself, or the deal's address. */
export function dealIdOf(text: string): string | null {
  return UUID.exec(text)?.[0].toLowerCase() ?? null
}
