import { useEffect, useState } from 'react'

/** `value`, once it has stopped changing for `delayMs`: what a search box feeds a query
 *  key so typing "rossi" is one request, not five. */
export function useDebounced<T>(value: T, delayMs: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return settled
}
