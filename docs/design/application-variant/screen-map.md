repo: letsrebase/rebase
branch: main

## Last sync

date: 2026-09-17T20:18:10Z

### Updated in this project

- Read the app surfaces for the two application-variant options: PigroCRM Fatture and Nuovo cliente, hub admin lists and the member area.
- Recreated four working screens at 1440×900 in two options, "Ledger" (1px ink, ruled tables, 4px step) and "Paper" (2px ink, zebra, 6px step).
- Copy, columns, states and Italian formats lifted from the repo's own routes, columns and format helpers.
- Icon geometry copied from lucide (the set both apps import) rather than redrawn.

## Screen map

| Screen | Built from |
|---|---|
| Fatture (A/B) | projects/pigrocrm/apps/web/src/routes/app/fatture/index.tsx, features/invoices/columns.tsx |
| Nuovo cliente (A/B) | projects/pigrocrm/apps/web/src/features/customers/CustomerForm.tsx, routes/app/clienti/index.tsx |
| Talenti (A/B) | projects/hub/apps/web/src/pages/admin/talent/, pages/admin/freelancer/, pages/admin/AdminLayout.tsx, lib/format.ts |
| La mia scheda (A/B) | projects/hub/apps/web/src/pages/member/Area.tsx, pages/member/Modifica.tsx, components/Shell.tsx |
| Tokens & signature | shared/brand/palette.css, projects/website/src/landing.css |
