# Cloudflare DNS, as code

The DNS of the platform's zone, `letsrebase.com`, described in Terraform (ORB-196).
Until 2026-09-14 every record was typed into Cloudflare's panel or sent with `curl`; the
migration to `letsrebase.com` (ORB-193, `docs/migrations/2026-09-14-letsrebase.md`) was
the last time. From here on a record is a resource in `rebase.tf`, and `terraform plan`
says whether the panel and the repository still agree. The platform's first zone,
`joinorbiters.com`, was described here too until 2026-10-20 (REB-670), when its
redirects were retired and its files went; that zone was in Ivan's Cloudflare account,
which no token on the devbox reached.

## Running it

The token is scoped to the zone with *Zone → DNS → Edit*. It is a variable read from the
environment and never written here:

```sh
export TF_VAR_rebase_api_token=cfut_...      # letsrebase.com, Lorenzo's account
export TF_VAR_rebase_account_id=...          # letsrebase.com's account id, for r2.tf (not a secret)
cd infra/cloudflare
terraform init
terraform plan        # nothing to do is the expected answer
```

`r2.tf` is not DNS: the private bucket of Vaultwarden's backup (REB-648) and its ninety-day
expiry. It needs the `letsrebase.com` token to carry *Account → Workers R2 Storage → Edit*
as well as *Zone → DNS → Edit*, and the account id above. The S3 access key the backup
uploads with is made in the dashboard (R2, Manage API tokens, Object Read and Write, that
one bucket) and is a GitHub Environment secret, never a Terraform output.

A change is a change to a `.tf` file, a `plan` read twice, an `apply`, and the commit
that ships the file. Never the panel first: a record changed by hand is drift, and the
next `plan` proposes to undo it.

**A record that does not exist yet**: a new `cloudflare_dns_record` block in
`rebase.tf`, then the sequence above.

**A record that already exists in the panel**: the resource block, an
`import` block in `rebase-imports.tf` naming its id (`GET
/zones/{zone}/dns_records` against the zone's token returns it), then `plan`: no
changes is the proof the import is correct, before the first `apply`.

**Before committing either**: the resource count and the import count must match, once
every record in the zone already exists in the panel:
`grep -c '^resource "cloudflare_dns_record"' rebase.tf` against
`grep -c '^  to ' rebase-imports.tf` (eighteen and eighteen on 2026-10-20).
`terraform plan` does not fail on a resource with no import, it just proposes to create
it, and that silence is exactly how the two Google Search Console TXT records (REB-195)
shipped with a resource block and no import block for eight days.

## The state

The state is a local `terraform.tfstate`, ignored by git, on the machine that ran the
last `apply`. That is acceptable while one person runs this, because the state holds
nothing that is not in Cloudflare and the `*-imports.tf` files rebuild it from nothing:
`rebase.tf` declares eighteen records, and on a fresh clone `terraform init &&
terraform apply` imports them into a new state and changes none (REB-702: 18 to
import, nothing else). A state made before 2026-10-20 may still hold the
`cloudflare_dns_record.orbiters_*` resources of the retired zone: `terraform state rm`
them before the next `plan`, which would otherwise ask for the provider that is gone;
removing them from the state touches no record. When a second person needs to run
this, the decision to take is a remote backend, not a copied file.

## What is not here

The zone itself (created once, by hand), the Resend domain (one resource, a community
provider: not worth the dependency), the host's nginx (`projects/*/deploy`), and the
records Cloudflare manages for itself.
