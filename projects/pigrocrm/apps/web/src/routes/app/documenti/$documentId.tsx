import { createFileRoute, redirect } from '@tanstack/react-router'

// Old path support (Italian route names retired 2026-09-21, see
// docs/design/DECISIONS.md): `documents/$documentId.tsx` is the real page now.
export const Route = createFileRoute('/app/documenti/$documentId')({
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/app/documents/$documentId', params })
  },
})
