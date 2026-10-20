# Cloudflare R2 in the letsrebase.com account: the private bucket Vaultwarden's backup
# service writes to (REB-648), and, under a prefix of its own, the nightly dump of
# Documenso's database (REB-715). Not a record of the zone, so it needs an account-level
# token (see providers.tf and README.md) on top of the DNS one.
#
# The bucket is private by construction: it has no public domain and no `r2.dev`
# access, and what lands in it is already an encrypted envelope
# (projects/vaultwarden/scripts/backup.sh, projects/hub/documenso-backup/scripts/backup.sh)
# whose private key is not on any server. The S3 access key pair a backup service uploads
# with is made in the Cloudflare dashboard (R2, Manage API tokens), scoped to this one
# bucket with Object Read and Write; it is a secret, so it is an Environment secret of
# `vaultwarden-production` (and of `hub-production`, for Documenso's) and not an output of
# this file.

resource "cloudflare_r2_bucket" "rebase_vaultwarden_backup" {
  provider   = cloudflare.rebase
  account_id = var.rebase_account_id
  name       = "rebase-vaultwarden-backup"
  location   = "weur"
}

# One object a day, kept ninety days, then deleted by Cloudflare, so the backup services
# need no delete and the bucket does not grow for ever. A restore wants the newest
# object; an older one is for the day somebody notices a loss late, and ninety days is
# three months of a password manager that changes slowly. The rules are per prefix: an
# object under a prefix no rule names is never deleted, which is why Documenso's has its
# own (the bucket is still named for Vaultwarden, and a bucket of Documenso's own is the
# cleaner end state: a new bucket here, its own key pair, and `DOCUMENSO_BACKUP_BUCKET` in `.env`).
resource "cloudflare_r2_bucket_lifecycle" "rebase_vaultwarden_backup" {
  provider    = cloudflare.rebase
  account_id  = var.rebase_account_id
  bucket_name = cloudflare_r2_bucket.rebase_vaultwarden_backup.name
  rules = [
    {
      id      = "expire-after-90-days"
      enabled = true
      conditions = {
        prefix = "vaultwarden/"
      }
      delete_objects_transition = {
        condition = {
          max_age = 7776000
          type    = "Age"
        }
      }
    },
    {
      id      = "expire-documenso-after-90-days"
      enabled = true
      conditions = {
        prefix = "documenso/"
      }
      delete_objects_transition = {
        condition = {
          max_age = 7776000
          type    = "Age"
        }
      }
    },
  ]
}
