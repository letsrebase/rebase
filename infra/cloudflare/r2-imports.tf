# The buckets exist since the first apply of r2.tf that named them (REB-648 for
# Vaultwarden's, REB-716 for the hub's). The lifecycle rules have no import in provider
# 5.25.0, so a checkout without state plans to create them again, which writes the same
# rules onto the buckets.
import {
  to       = cloudflare_r2_bucket.rebase_vaultwarden_backup
  id       = "87930353c186a890e103d671164e376c/rebase-vaultwarden-backup/default"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_r2_bucket.rebase_hub_backup
  id       = "87930353c186a890e103d671164e376c/rebase-hub-backup/default"
  provider = cloudflare.rebase
}
