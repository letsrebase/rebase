import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['gallery/dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: { ecmaVersion: 2022, globals: { ...globals.browser, ...globals.node } },
  },
  {
    // The primitives are shadcn's own output, and every one of them that has
    // variants exports the `cva()` function beside its component: `buttonVariants`,
    // `badgeVariants`, `tabsListVariants`, which is what the feature code composes
    // with. That is exactly the shape this rule flags, and it is the generator's
    // template rather than a defect here, so it is off for these files only and
    // live everywhere else, the gallery included.
    files: ['*.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
