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
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  {
    // shadcn generates these files verbatim; hand-editing them to satisfy lint
    // is pointless since the next `shadcn add --overwrite` puts them back as
    // the CLI's own template produces them. The rule below flags a pattern the
    // generator itself ships on every relevant component, a file exporting its
    // component alongside its cva() variants function, never an actual defect here.
    // Scoped to exactly this file set, not disabled project-wide.
    //
    // `set-state-in-effect` was off here too, for `useIsMobile`, which the sidebar
    // primitive needed; both left with REB-304, and the hook this application did
    // keep (`hooks/use-media-query.ts`) is written on `useSyncExternalStore`
    // precisely so it never trips that rule.
    files: ['src/components/ui/**/*.{ts,tsx}'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
  {
    // `auth.tsx` is a deliberate, permanent context-provider bundle: `AuthProvider`
    // (the component) plus the three hooks that only make sense next to it
    // (`useAuth`, `useCanWrite`, `useIsAdmin`) -- one module, matching this file's
    // place in the plan's own File Structure and what later screens import from
    // `@/lib/auth`. That is exactly the shape `react-refresh/only-export-components`
    // flags (a component file also exporting non-component values), but splitting a
    // ~70-line auth module into two files purely to satisfy this rule would trade a
    // real, permanent indirection for a cost that is real but narrow: editing this
    // file forces a full remount of its subtree on save instead of a hot patch, and
    // this file changes rarely compared to the components actually iterated on.
    // `allowExportNames`, not a blanket `'off'`: the rule stays live for anything
    // added to this file later that is *not* one of these three known, intentional
    // exports -- an accidental new non-component export would still be caught.
    files: ['src/lib/auth.tsx'],
    rules: {
      // Same severity as the base `vite` preset ("error") -- only the options
      // change here, not how strictly the rule is enforced.
      'react-refresh/only-export-components': [
        'error',
        { allowConstantExport: true, allowExportNames: ['useAuth', 'useCanWrite', 'useIsAdmin'] },
      ],
    },
  },
  {
    // Same shape as the `auth.tsx` override above, same reason: `renderFieldValue`
    // is `DynamicFieldRenderer`'s read-only counterpart -- the control and its
    // table/detail-panel rendering for the same nine field types belong in one
    // file, not split across two purely to satisfy this rule. The brief's own
    // `DynamicFieldRenderer.test.tsx` imports both from this one module.
    files: ['src/components/DynamicFieldRenderer.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['renderFieldValue'] }],
    },
  },
  {
    // Same shape again: `customerToFormValues` builds the exact `initial` shape
    // `CustomerForm` expects, walking the same `NATIVE_FIELD_KEYS` this file
    // derives from its own `NATIVE_FIELDS` -- the two are coupled by construction
    // (add a native field here and both need to agree on it), so splitting them
    // across files would not remove the coupling, only hide it. Persons and Deals
    // are expected to need the identical override once their own forms copy this
    // file's shape.
    files: ['src/features/customers/CustomerForm.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['customerToFormValues'] }],
    },
  },
  {
    // Persons' own copy of the override immediately above: `personToFormValues`
    // and `PersonForm` are coupled by construction in exactly the same way.
    files: ['src/features/people/PersonForm.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['personToFormValues'] }],
    },
  },
  {
    // Deals' own copy of the same override: `dealToFormValues` and `DealForm`
    // are coupled by construction in exactly the same way as the two overrides
    // above -- anticipated in this file's own comment on the `CustomerForm.tsx`
    // entry ("Persons and Deals are expected to need the identical override
    // once their own forms copy this file's shape").
    files: ['src/features/deals/DealForm.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['dealToFormValues'] }],
    },
  },
  {
    // `resolveMove` is `KanbanBoard`'s own drag-end decision, pulled out into a
    // plain, exported function so it is directly testable without simulating a
    // real pointer gesture through jsdom (see its own docstring in
    // KanbanBoard.tsx). Coupled to `KanbanBoard` by construction the same way
    // `customerToFormValues`/`personToFormValues`/`dealToFormValues` are coupled
    // to their own forms -- one file, not split purely to satisfy this rule.
    files: ['src/features/deals/KanbanBoard.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['resolveMove'] }],
    },
  },
  {
    // The three top-level detail routes now export their component directly
    // (`CustomerDetail`/`PersonDetail`/`DealDetail`), not only wrapped inside the
    // `{ component: ... }` object `createFileRoute` already received, so their
    // own isError/404 handling is directly testable without rendering a full
    // router (see each file's own `*.test.tsx`). Same shape as the
    // `customerToFormValues`/`personToFormValues`/`dealToFormValues` overrides
    // above, just naming the *non*-component half of the pair: `Route` --
    // `createFileRoute`'s own descriptor object, not a component -- is what this
    // rule would otherwise flag, mixed in the same file as a real component export.
    files: [
      'src/routes/app/clienti/$customerId.tsx',
      'src/routes/app/persone/$personId.tsx',
      'src/routes/app/deal/$dealId.tsx',
    ],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['Route'] }],
    },
  },
  {
    // `variablesToFields` maps `TemplateDescription.variabili` onto the exact
    // `FieldDefinition[]` shape `NewFromTemplateDialog` feeds to `DynamicForm` --
    // coupled to this component by construction the same way
    // `customerToFormValues`/`personToFormValues`/`dealToFormValues` are coupled to
    // their own forms above, and tested directly (`NewFromTemplateDialog.test.tsx`)
    // for the same reason those are: to check the mapping without rendering the
    // whole dialog.
    files: ['src/features/documents/NewFromTemplateDialog.tsx'],
    rules: {
      'react-refresh/only-export-components': ['error', { allowExportNames: ['variablesToFields'] }],
    },
  },
])
