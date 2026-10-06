# The bucket exists since the first apply of r2.tf (REB-648). The lifecycle rule has no
# import in provider 5.25.0, so a checkout without state plans to create it again, which
# writes the same rule onto the bucket.
import {
  to       = cloudflare_r2_bucket.rebase_vaultwarden_backup
  id       = "87930353c186a890e103d671164e376c/rebase-vaultwarden-backup/default"
  provider = cloudflare.rebase
}
