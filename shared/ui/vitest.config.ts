import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

/**
 * Two environments, because this package holds two kinds of test. `tokens.test.ts`
 * reads the stylesheet as text and resolves colours the way a browser would, which
 * needs no DOM; the primitives' tests render them, which needs one. Vitest picks per
 * file from the `environmentMatchGlobs` below rather than paying for jsdom in the
 * token suite.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'node',
    setupFiles: ['./test-setup.ts'],
    projects: [
      {
        extends: true,
        test: { name: 'tokens', include: ['tokens.test.ts'], environment: 'node', setupFiles: [] },
      },
      {
        extends: true,
        test: { name: 'primitives', include: ['*.test.tsx'], environment: 'jsdom' },
      },
    ],
  },
})
