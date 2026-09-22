# Cloudflare DNS, as code

The DNS of the platform's two zones, `letsrebase.com` and `joinorbiters.com`, described
in Terraform (ORB-196). Until 2026-09-14 every record was typed into Cloudflare's panel
or sent with `curl`; the migration to `letsrebase.com` (ORB-193,
`docs/migrations/2026-09-14-letsrebase.md`) was the last time. From here on a record is
a resource in `rebase.tf` or `orbiters.tf`, and `terraform plan` says whether the panel
and the repository still agree.

## Running it

Two zones in two Cloudflare accounts means two tokens, each scoped to its zone with
*Zone → DNS → Edit*. They are variables read from the environment and never written
here:

```sh
export TF_VAR_rebase_api_token=cfut_...      # letsrebase.com, Lorenzo's account
export TF_VAR_orbiters_api_token=cfat_...    # joinorbiters.com, Ivan's account
cd infra/cloudflare
terraform init
terraform plan        # nothing to do is the expected answer
```

A change is a change to a `.tf` file, a `plan` read twice, an `apply`, and the commit
that ships the file. Never the panel first: a record changed by hand is drift, and the
next `plan` proposes to undo it.

**A record that does not exist yet**: a new `cloudflare_dns_record` block in
`rebase.tf` or `orbiters.tf`, then the sequence above.

**A record that already exists in the panel**: the resource block, an
`import` block in the matching `*-imports.tf` naming its id (`GET
/zones/{zone}/dns_records` against that zone's token returns it), then `plan` — no
changes is the proof the import is correct, before the first `apply`.

**Before committing either**: the resource count and the import count for a zone
must match, once every record in it already exists in the panel —
`grep -c '^resource "cloudflare_dns_record"' rebase.tf` against
`grep -c '^  to ' rebase-imports.tf` (the same pair for `orbiters.tf` /
`orbiters-imports.tf`). `terraform plan` does not fail on a resource with no
import, it just proposes to create it, and that silence is exactly how the two
Google Search Console TXT records (REB-195) shipped with a resource block and no
import block for eight days. The one standing exception is `orbiters.tf` itself,
twelve against eleven until the gap in **The state** below is closed: a mismatch
you introduce on top of that one is still yours to explain before you commit
(REB-259).

## The state

The state is a local `terraform.tfstate`, ignored by git, on the machine that ran the
last `apply`. That is acceptable while one person runs this, because the state holds
nothing that is not in Cloudflare and the `*-imports.tf` files rebuild it from nothing:
`rebase.tf` and `orbiters.tf` declare twenty-four records, and on a fresh clone
`terraform init && terraform apply` imports the twenty-three that carry an import
block into a new state and changes none. The twenty-fourth,
`orbiters_apex_google_site_verification_txt` (the second, change-of-address Search
Console token from ORB-195, not the older `orbiters_apex_txt` imported above),
still has no block: a fresh clone's `apply` plans it as a record to create, which
either fails against Cloudflare's duplicate-record check or writes a second copy of
a token that already verifies the zone, so read the plan and stop before `apply` if
that record still shows up as an addition. That zone is Ivan's Cloudflare account,
and the token to read the record's id is still not on this machine: the Cloudflare
token available here resolves `letsrebase.com` only and returns an authentication
error against `joinorbiters.com`'s zone (checked again for REB-259). When a second
person needs to run it, the decision to take is a remote backend, not a copied file.

## What is not here

The two zones themselves (created once, by hand, in each account), the Resend domain
(one resource, a community provider: not worth the dependency), the host's nginx
(`projects/*/deploy`), and the records Cloudflare manages for itself. The zone of
`joinorbiters.com` also carries GoDaddy's `_domainconnect` and the Google Search
Console verification: imported as they are, so that a `plan` stays quiet, and to be
removed here when they are removed for real (ORB-195).
