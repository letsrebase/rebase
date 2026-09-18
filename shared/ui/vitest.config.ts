import { defineConfig } from 'vitest/config'

// `node`, not jsdom: this package ships one stylesheet and the test reads it as text,
// resolving every colour by evaluating the declarations the way a browser would. The
// two applications test their own DOM.
export default defineConfig({ test: { environment: 'node', globals: true } })
