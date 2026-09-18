import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { PageHeader } from '@/components/PageHeader'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@rebase/ui/tabs'
import type { TimelineEntityType } from '@/lib/schema'
import { Timeline } from './Timeline'

interface EntityDetailLayoutProps {
  /**
   * The icon in the header's Paper square. Required rather than defaulted: the whole
   * point of the square is that the eye finds the same mark in the same place for the
   * same kind of record, so a detail page silently inheriting some other entity's icon
   * would be worse than one with none. Each route passes the icon its own sidebar entry
   * uses (`AppShell`'s nav), so the page and the menu agree.
   */
  icon: LucideIcon
  title: string
  subtitle?: string
  actions?: ReactNode
  overview: ReactNode
  links?: ReactNode
  /**
   * The Documenti tab's contents. Optional because Person has no documents: a
   * document belongs to a customer or to a deal, never to a contact. When absent the
   * tab is not rendered at all rather than rendered empty -- an empty tab invites the
   * user to look for something that does not exist for this entity.
   */
  documents?: ReactNode
  /** Optional, like `documents`: only customers and deals have invoices, and a person
   *  never will. An absent prop means the tab is not rendered at all, rather than a tab
   *  that opens onto an empty explanation of why it is empty. */
  invoices?: ReactNode
  /**
   * The Ore tab's contents. Optional because only a Deal has hours: an hour is
   * attached to a deal, always and only (`time_entries.deal_id` is required), so
   * neither a Customer nor a Person can have this tab. Absent means the tab is not
   * rendered at all rather than rendered empty -- an empty tab invites the user to look
   * for something that does not exist for this entity.
   */
  hours?: ReactNode
  /**
   * The Economia tab's contents -- a Deal's own profit and loss, or a Customer's as the
   * sum of their deals. Filled by slice 4B; absent for the whole of 4A, which is why it
   * is optional rather than required. A tab showing zeros is worse than no tab: the
   * user understands "not yet", and cannot tell a real zero from a missing feature.
   */
  economics?: ReactNode
  /**
   * The Email tab's contents -- the stored Gmail mirror for this entity. Optional like
   * every slot above it, and for a sharper reason than the others: an installation with
   * no Google client, or an owner who never connected a mailbox, has no correspondence
   * to show and never will until they do. A tab that opens onto "nessuna email" would
   * read as "your mail is not being filed", which is a different and untrue claim.
   * All three entities can have one -- a message is filed against the person, that
   * person's customer and that customer's live deals (`gmail/links.py`).
   */
  emails?: ReactNode
  entityType: TimelineEntityType
  entityId: string
  /**
   * Forwarded to `Timeline` untouched -- see that component's own docstring for
   * why it exists and what the backend bounds it to (1-200, default 50).
   * Optional, because most callers want the default; exposed here rather than
   * only on `Timeline` itself because this layout is what actually mounts the
   * Timeline tab -- no page renders `Timeline` directly, so this is the only
   * place a real caller could ever reach the prop.
   */
  timelineLimit?: number
}

/**
 * One layout for Customer, Person and Deal: the same *Panoramica · Timeline ·
 * Collegamenti* shape for all three, so the product is predictable to learn.
 * Three similar pages drift apart from each other over time; one shared
 * component cannot. Later slices add Documenti, Attività and Fatture as further
 * tabs here, once, for all three entities at once -- never as a fourth near-copy
 * of this file.
 */
export function EntityDetailLayout({
  icon,
  title,
  subtitle,
  actions,
  overview,
  links,
  documents,
  invoices,
  hours,
  economics,
  emails,
  entityType,
  entityId,
  timelineLimit,
}: EntityDetailLayoutProps) {
  // The whole page lives inside `Tabs`, with the tab list in `PageHeader`'s own `tabs`
  // slot: §4 draws the underline tabs as part of a page's intestazione, and Radix needs
  // its list and its panels under one root. Same arrangement as `SettingsLayout`.
  return (
    <Tabs defaultValue="panoramica">
      <PageHeader
        icon={icon}
        title={title}
        description={subtitle}
        actions={actions}
        tabs={
          <TabsList variant="line">
            <TabsTrigger value="panoramica">Panoramica</TabsTrigger>
            {documents && <TabsTrigger value="documenti">Documenti</TabsTrigger>}
            {invoices && <TabsTrigger value="fatture">Fatture</TabsTrigger>}
            {hours && <TabsTrigger value="ore">Ore</TabsTrigger>}
            {economics && <TabsTrigger value="economia">Economia</TabsTrigger>}
            {/* Immediately before Timeline: both are a record of what already happened,
                and everything to the left of them is a thing the user maintains. */}
            {emails && <TabsTrigger value="email">Email</TabsTrigger>}
            <TabsTrigger value="timeline">Timeline</TabsTrigger>
            <TabsTrigger value="collegamenti">Collegamenti</TabsTrigger>
          </TabsList>
        }
      />

      <div className="px-8 pb-8">
        <TabsContent value="panoramica" className="mt-6">
          {overview}
        </TabsContent>
        {documents && (
          <TabsContent value="documenti" className="mt-6">
            {documents}
          </TabsContent>
        )}
        {invoices && (
          <TabsContent value="fatture" className="mt-6">
            {invoices}
          </TabsContent>
        )}
        {hours && (
          <TabsContent value="ore" className="mt-6">
            {hours}
          </TabsContent>
        )}
        {economics && (
          <TabsContent value="economia" className="mt-6">
            {economics}
          </TabsContent>
        )}
        {emails && (
          <TabsContent value="email" className="mt-6">
            {emails}
          </TabsContent>
        )}
        <TabsContent value="timeline" className="mt-6">
          <Timeline entityType={entityType} entityId={entityId} limit={timelineLimit} />
        </TabsContent>
        <TabsContent value="collegamenti" className="mt-6">
          {links ?? <p className="text-muted-foreground">Nessun collegamento.</p>}
        </TabsContent>
      </div>
    </Tabs>
  )
}
