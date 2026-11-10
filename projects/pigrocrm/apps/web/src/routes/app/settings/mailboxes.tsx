import { createFileRoute } from '@tanstack/react-router'
import { MailboxesPanel } from '@/features/settings/MailboxesPanel'

export const Route = createFileRoute('/app/settings/mailboxes')({ component: MailboxesPanel })
