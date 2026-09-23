import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: { ecmaVersion: 2020, globals: globals.browser },
  },
  {
    // The primitives are shadcn's shapes copied from PigroCRM: a component beside its
    // cva() variants, which the rule flags and which is how the generator ships them.
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
  {
    // The route tree is code, not files: `router.tsx` exports the router and the pages
    // it mounts; `wizard/Wizard.tsx` exports the engine's component beside the step
    // type it takes; the two wizard pages export their step lists beside the page, so
    // the tests can validate a step without mounting the page, and so does
    // `pages/member/Modifica.tsx` with `editSteps`, `pages/member/ModificaAzienda.tsx`
    // with `editCompanyFields` (REB-314) and `pages/member/NuovaRichiestaAzienda.tsx`
    // with `newCompanyFields` (REB-381); `lib/me.tsx` (REB-279, replacing
    // `lib/member.tsx`) is a hook.
    files: [
      'src/router.tsx',
      'src/wizard/Wizard.tsx',
      'src/pages/*Wizard.tsx',
      'src/pages/member/Modifica.tsx',
      'src/pages/member/ModificaAzienda.tsx',
      'src/pages/member/NuovaRichiestaAzienda.tsx',
      'src/lib/me.tsx',
    ],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
])
