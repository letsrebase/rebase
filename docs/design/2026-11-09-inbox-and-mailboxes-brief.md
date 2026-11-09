# Brief: «In arrivo» and «Caselle email» (PigroCRM)

Status: approved by Lorenzo on 2026-11-09, on the artifact `pigrocrm-inbox-and-mailboxes`
(answers A to all six questions, plus the loading rule below). Card: REB-973. Design:
`projects/pigrocrm/docs/superpowers/specs/2026-11-09-incoming-documents-inbox-design.md`
§§ 1 to 3 and 6 (REB-960); frame: `2026-11-09-space-profiles-modules-and-the-money-picture-design.md`
§ A7 (every new surface passes this gate) and § B6 (the phone shell). The rules this brief
creates are the last two rows of `docs/design/DECISIONS.md` dated 2026-11-09; the build cards
read this file and those rows, never the artifact, which is closed.

Everything here is drawn on `shared/ui/tokens.css` and the primitives of `@rebase/ui`:
squared, 1px ink lines, Outfit, Prussian sidebar, Watermelon Deep only on the primary action
and on errors (the application variant, DECISIONS 2026-09-18). This brief uses four of the
five `StatusPill` tones, `ink`, `muted`, `gold` and `danger`, and never `accent`. One light
scheme. Strings are Italian, amounts
`1.464,00 €`, dates `02/11/2026`, tabular numerals in every column of figures, no emoji, no
illustration.

## What Lorenzo decided

| # | Question | Decision |
|---|---|---|
| 1 | Desktop «In arrivo» | A table with the open item in a panel beside it (330 px), not a ruled list that opens a page |
| 2 | A ZIP unpacked into items | Flat rows, one per file, each with a chip «da archivio.zip» and a filter on the origin; the ZIP, like a PEC envelope, is the parent item, listed under «Archiviati» |
| 3 | The «Gmail» settings tab | It becomes «Caselle email», in «Collegamenti»; Gmail is one row in it, its scopes, sync and store-bodies switch inside that row |
| 4 | A mailbox that needs a person | The persistent band on every page, raised only when a person must act (`auth_failed` and today's Gmail banner reasons); a transient failure is only the pill |
| 5 | After an upload, on desktop | Rows appear at once and fill in; a refused file stays as a red row with its reason until dismissed |
| 6 | An admin and a colleague's personal mailbox | Not visible: only the owner sees a personal mailbox |
| 7 | Loading | The app's own `Loader` (`@rebase/ui/loader`, the brand mark animated), never a skeleton and never a new spinner |

## «In arrivo»

```
Subject      The inbox: every file that reached the space (upload, mail, PEC, a ZIP) and is
             not yet a record. It lists what waits for a person and keeps what was filed,
             discarded or seen twice, each with its trace.
Audience     The person who handles the space's paperwork: a founder or an admin with
             supplier invoices, receipts and statements arriving by four roads, and a
             collaborator who photographs a receipt. They know what an invoice is, not SdI
             formats: the surface never says «p7m» or «FPR12» without a plain word beside it.
The job      Clear the queue: see what arrived, check what was read, file it or discard it.
             On the phone: add a document in two taps from the «+».
Palette      Paper ground, white cards, Prussian Blue for ink and every line, Charcoal Blue for
             secondary text, Watermelon Deep for the primary action and errors only, Royal
             Gold as the «waiting» dot. Tokens, never a hex.
Type         Outfit only. 24 px title, 14 px row text, 12.5 px meta, tabular numerals,
             amounts right-aligned.
Density      Middle, as the application variant says (48 px rows, 14 px cells): the `Table`
             primitive's own rows, grown by the two-line cell (name over origin), no custom
             row height. Phone cards are airy, about 96 px. The panel's «what was read» block
             is the one dense block.
Signature    Every item says where it came from and who read it: «Dal file: dato certo» for a
             checked XML, «Letto dal modello» or «Letto dal tuo agente» for a proposal.
States       See the table below.
Constraints  Italian runs about 20% longer: draw and test the long label. `shared/ui`
             primitives and tokens only. Phone first, proven by renders at 390 and 1280 px.
Non-goals    Editing what was read (the module that files it owns that); reading statements
             (C4); the supplier record (C3); bulk actions on the phone; annotating the file.
```

**Desktop.** `PageHeader` with the icon, the title «In arrivo», one sentence, and the one
strong button «Carica documenti» (also: dropping a file anywhere on the page). Tabs, as
«Proposte» does: «Da gestire» (state `new` and `proposed`, with its count), «Archiviati»
(`filed`, and the parent ZIPs and PEC envelopes), «Scartati e doppioni» (`discarded` and `duplicate`, each with
its reason). Filters: «Tipo», «Da dove», «Azienda». The table has a checkbox column and, for the
bulk bar, «Scarta…» and «Assegna all'azienda…». Columns with the panel open: documento (name
and a line «origine, data», plus «Solo tu» where it applies), tipo, importo, stato (the pill
and one line saying why: «Azienda da scegliere», «La conferma è tua», «Letto dal modello»).
The panel: the file, «Da dove arriva», «Cosa è stato letto» with its provenance tag,
«Per quale azienda» (a select only when the entity is not certain), «Arrivata anche», and the
actions «Registra come…» (primary), «Cambia tipo», «Scarta…». 50 rows at a time with «Mostra
altri».

**Phone.** One column of cards under a segmented «Da gestire / Archiviati / Scartati». A card
opens full screen with a sticky «Registra» and «Scarta…». The centre «+» (frame B6) gains
«Carica un documento», which offers «Scatta una foto» and «Scegli un file»; the upload adds
without navigating and a toast says «2 documenti caricati in In arrivo.» with «Apri». «In arrivo», with its count of waiting items, is also the first entry of «Altro» on the
phone. No bulk selection on the phone.

| State | What is drawn |
|---|---|
| Empty | One sentence on what lives here, «Qui arrivano fatture, ricevute, estratti conto e contratti, da un caricamento o da una casella email.», and the single action «Carica documenti». No illustration. |
| Loading | `Loader` and a `role="status"` sentence («Leggo i nuovi arrivi…»), in the place of the list. |
| Error | `QueryErrorBanner`: what happened and «Riprova». No list under it. |
| Many | 50 at a time with «Mostra altri (212 in tutto)», filters, a checkbox column and a bulk bar (desktop only). A ZIP of hundreds is flat rows, never a nested row. |
| Duplicate | A copy never gets a row in «Da gestire»; it is a line «Arrivata anche via email, 03/11/2026, stesso file» on the original, and a row in «Scartati e doppioni» with its reason. A copy that disagrees (same key, other amount) is a red line on the original with «Confronta», and raises the reminder of design § 5. |
| Owner-only | An item with no company yet shows its owner or uploader «Solo tu» with the reason in one line («Visibile a te e agli amministratori finché non ha un'azienda»); an admin reads «Visibile solo a te e agli amministratori» on the same line, without «Solo tu». Someone who may not see it sees neither the row nor the count. |
| Module off | The item waits and names the module that would file it («Per registrarla serve il modulo Acquisti. Resta qui finché non lo attivi.»); once the module exists, an admin gets the link «Apri Moduli» (frame B4), anyone else the sentence only. |
| Module not built yet | Until the purchases and cash modules exist (C3, C4), a supplier invoice or a statement is classified and waits with its parsed fields shown and no filing action, and no sentence about a module to switch on (design § 3.5). |
| Upload in flight | Each file is a row at once: `Loader` and «Lo leggo…» while it is read, a progress bar while it uploads, then it settles into its state. |
| Refused | A red row with the reason in one line («Non caricato: formato non accettato.», «Non caricato: supera i 100 MiB.») that stays until dismissed. A file that is stored but unreadable is kept as «Altro» with its reason, never dropped. |

## «Caselle email»

```
Subject      The mailboxes PigroCRM reads: a person's own Gmail and IMAP boxes, and the
             space's own (a PEC, fatture@). For each: whose it is, what it reads, whether it
             is working.
Audience     Any person with a mailbox of their own; an admin for the space's. Not technical:
             «IMAP», «porta» and «SSL/TLS» appear only in the connect form, defaults filled.
The job      Connect a mailbox in one go. Afterwards, know at a glance whether it is reading,
             and when it is not, the one thing to do.
Palette      As «In arrivo». Health pills: ink «Attiva», gold «Riprovo» (`failing`), danger
             «Serve te» (`auth_failed`), muted «Scollegata» (`disconnected`).
Type         Outfit only. The connect form is dense two-column, labels beside their field,
             actions bottom-right.
Density      Middle list on the `Table` primitive's rows, dense entry.
Signature    The health pill with the cause on the line under it: one state per mailbox, the
             worst, and what set it. Not a row of indicators.
States       See the table below.
Constraints  The password is typed by a person and never travels over MCP; shown only as dots
             and never again. The connection is tested before saving. Only a space mailbox may
             read «Fatture e documenti»; a personal one reads «Corrispondenza».
Non-goals    The `statements` purpose (C4), Microsoft 365 and space-owned Gmail (later),
             sending mail from a mailbox, provider presets (verified before code).
```

**Desktop.** The tab «Caselle email» is the first of «Collegamenti», before «Google Drive»
and «Slack», and replaces «Gmail». Two groups: «Le tue caselle», then «Caselle dello spazio»
(connected by an admin). Columns: casella (address and a provider badge), di chi, cosa legge
(purpose chips), stato (pill and its sentence). One strong button, «Collega una casella», opens
the connect dialog («Prova la connessione» before «Collega», 90 days backfilled by default).
The row of a Gmail mailbox opens to the panel of today (scopes, sync, store bodies,
re-authorise, disconnect). **Phone:** one card per mailbox, the connect form in one column
with «Prova» and «Collega» in a sticky bar.

| State | What is drawn |
|---|---|
| Empty | «Nessuna casella collegata». One sentence (la tua Gmail per le email dei clienti, o una casella dello spazio per le fatture) and «Collega una casella». |
| Loading | `Loader` and a `role="status"` sentence («Leggo le caselle…»), in the place of the rows. |
| Error | The error band and nothing under it, so no control is offered on state that never arrived (the rule `GmailPanel` already keeps). |
| Attiva | Pill and «Ultimo sync 12:15», with «3 documenti nuovi» on a mailbox that reads documents and «41 messaggi» on one that reads correspondence. |
| Riprovo | Gold pill: «Non riesce a collegarsi dalle 11:30. Riprovo ogni 15 minuti.» Only the pill: no band. |
| Serve te | Danger pill and the cause with the one action: «Il server non accetta la password dalle 08:15. Aggiornala per riprendere a leggere.» The band on every page (below) names it. What was already read stays. |
| Scollegata | Muted pill. |
| Disconnect | «Quello che è già arrivato resta. Da ora non leggo più questa casella.» with the option «Elimina anche i N documenti non ancora archiviati», as the Gmail disconnect does for messages. |
| Many | Sorted by need: `auth_failed`, then `failing`, then the rest by name; no paging under 25. |
| Owner-only | A personal mailbox is listed only to its owner, admins included. |

**The band.** It extends today's `GmailBanner`: one line across the shell, nothing to dismiss
(a close button would be a way to hide a stopped sync from yourself), the worst first, naming
the mailbox and the action, «e 1 altra» when there are more. For a person it covers their own
mailboxes; for an admin it covers the space's mailboxes as well. A transient failure never
raises it.

## What a component can violate

- «In arrivo» is a table with a side panel on desktop and cards on the phone; a ZIP never
  becomes a nested or grouped row.
- The loading state of both surfaces is `Loader` plus a sentence: no `Skeleton`, no new
  spinner. This holds against the skeleton of the application variant and of the owner's
  general taste file; Lorenzo's answer and the row of 2026-09-25 win.
- The band is raised when a person must act: a mailbox in `auth_failed`, and every banner
  reason Gmail has today (`revoked`, `expired`, `expiring`, `scope_missing`, design § 2.2).
  It is never raised for `failing` or `disconnected`, and it has no close control.
- A personal mailbox is invisible to everyone but its owner in «Caselle email».
- A refused upload is a red row with a reason, never only a toast.

## Inferred, not asked

These follow from the design and the code and were not shown to Lorenzo; a reviewer should push
back on any.

- «Caselle email» is open to collaborators and admins for their own mailboxes, as the `drive`
  tab is (a read-only member is refused, as `require_write` does), and shows the space's
  mailboxes to admins only. Today's Gmail tab is admin only (`SETTINGS_TAB_MIN_ROLE.gmail`),
  and `update_gmail_settings` already allows a collaborator.
- The tab's value and route stay `gmail` (`/app/settings/gmail`) and only its label changes,
  because the Google consent callback and `GmailBanner` land there; a rename moves the
  callback's redirect and the banner link in the same change.
- «In arrivo» sits in «Da fare» just before «Proposte» and needs an icon that is not the
  `Inbox` one «Proposte» uses.
- Duplicates and discarded items share one tab, with the reason column.
- The uploader sees their own unclassified item; design § 1 names no rule for an upload.
- The status of an item maps to a tone: `new` muted, `proposed` gold, `filed` ink, `duplicate`
  muted, `discarded` muted; a refused file is `danger`. The build keeps it as a `Record` beside
  the labels, as `StatusPill` asks.
- The words and pieces the design does not name: «Prova la connessione» before saving a
  mailbox, «Confronta» on a disagreeing duplicate, the progress bar of an upload, the one-line
  refusal reasons, and the provenance tags «Dal file: dato certo», «Letto dal modello» and
  «Letto dal tuo agente». The per-file limits of a ZIP (design § 2.1) refuse with their own
  reason in the same red row.

## Open: a contradiction in the design

Design § 1 lets an item come from a personal mailbox, but § 2.2 allows the purpose `billing`
(the only one that stores attachments) on space-owned mailboxes only, and `correspondence`
stores none. This brief draws «Solo tu» on an item that has no company yet, whatever its road.
The sentence of § 1 about a personal mailbox's items is to be fixed in the design by REB-960
when the visibility code is written.
