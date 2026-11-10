import { describe, expect, it } from 'vitest'

import { Toaster } from './sonner'

describe('Toaster', () => {
  // `richColors` paints success, info, warning and error from sonner's own palette unless
  // every variant names its colours (REB-986: the stock success pair, #008a2e on #ecfdf3,
  // measures 4.25:1). The pairs themselves are measured in `tokens.test.ts`.
  it.each(['success', 'info', 'warning', 'error'])('takes the %s colours from tokens', (kind) => {
    const style = Toaster({ richColors: true }).props.style as Record<string, string>
    for (const part of ['bg', 'text', 'border']) {
      expect(style[`--${kind}-${part}`]).toMatch(/^(var\(--[\w-]+\)|color-mix\(.*\))$/)
    }
  })
})
