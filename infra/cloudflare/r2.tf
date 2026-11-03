# Cloudflare R2 in the letsrebase.com account: the private buckets the backup services
# write to, one per project so a project's key pair reaches only its own backups. The
# buckets are Vaultwarden's (REB-648) and the hub's (REB-716); the hub's holds the nightly
# dumps of Documenso's database (REB-715), of the hub's own (REB-717) and of PigroCRM's whole
# server (INFRA-A-07), each under its own prefix. Not a record of the zone, so it needs an account-level token (see
# providers.tf and README.md) on top of the DNS one.
#
# A bucket is private by construction: it has no public domain and no `r2.dev` access,
# and what lands in it is already an encrypted envelope (projects/vaultwarden/scripts/
# backup.sh, projects/hub/db-backup/scripts/backup.sh) whose private key is not on
# any server. The S3 access key pair a backup service uploads with is made in the
# Cloudflare dashboard (R2, Manage API tokens, Create Account API token), scoped to that
# one bucket with Object Read and Write (delete is what the bucket locks below take away); it is a secret, so it is an Environment secret of
# `vaultwarden-production` or `hub-production` and not an output of this file.

resource "cloudflare_r2_bucket" "rebase_vaultwarden_backup" {
  provider   = cloudflare.rebase
  account_id = var.rebase_account_id
  name       = "rebase-vaultwarden-backup"
  location   = "weur"
}

resource "cloudflare_r2_bucket" "rebase_hub_backup" {
  provider   = cloudflare.rebase
  account_id = var.rebase_account_id
  name       = "rebase-hub-backup"
  location   = "weur"
}

# One object a day per prefix, kept ninety days, then deleted by Cloudflare, so the
# backup services need no delete and a bucket does not grow for ever. A restore wants the
# newest object; an older one is for the day somebody notices a loss late. The rules are
# per prefix: an object under a prefix no rule names is never deleted, so a new prefix
# needs its own rule here.
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
  ]
}

resource "cloudflare_r2_bucket_lifecycle" "rebase_hub_backup" {
  provider    = cloudflare.rebase
  account_id  = var.rebase_account_id
  bucket_name = cloudflare_r2_bucket.rebase_hub_backup.name
  rules = [
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
    {
      id      = "expire-hub-after-90-days"
      enabled = true
      conditions = {
        prefix = "hub/"
      }
      delete_objects_transition = {
        condition = {
          max_age = 7776000
          type    = "Age"
        }
      }
    },
    {
      id      = "expire-pigrocrm-after-90-days"
      enabled = true
      conditions = {
        prefix = "pigrocrm/"
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

# Retention: an object cannot be deleted or overwritten for thirty days after it is written,
# whoever asks, the backup services' own key pair included (INFRA-A-13). The key a backup
# service uploads with is Object Read and Write, which includes delete, and sits in a file on
# a host that also runs the application: without this, a compromise of the host can delete
# or overwrite the off-host copies. The lifecycle rules above still expire an object at ninety
# days, after the lock. A lock rule is per prefix, like the lifecycle's.
resource "cloudflare_r2_bucket_lock" "rebase_vaultwarden_backup" {
  provider    = cloudflare.rebase
  account_id  = var.rebase_account_id
  bucket_name = cloudflare_r2_bucket.rebase_vaultwarden_backup.name
  rules = [
    {
      id      = "retain-vaultwarden-30-days"
      enabled = true
      prefix  = "vaultwarden/"
      condition = {
        type            = "Age"
        max_age_seconds = 2592000
      }
    },
  ]
}

resource "cloudflare_r2_bucket_lock" "rebase_hub_backup" {
  provider    = cloudflare.rebase
  account_id  = var.rebase_account_id
  bucket_name = cloudflare_r2_bucket.rebase_hub_backup.name
  rules = [
    {
      id      = "retain-documenso-30-days"
      enabled = true
      prefix  = "documenso/"
      condition = {
        type            = "Age"
        max_age_seconds = 2592000
      }
    },
    {
      id      = "retain-hub-30-days"
      enabled = true
      prefix  = "hub/"
      condition = {
        type            = "Age"
        max_age_seconds = 2592000
      }
    },
    {
      id      = "retain-pigrocrm-30-days"
      enabled = true
      prefix  = "pigrocrm/"
      condition = {
        type            = "Age"
        max_age_seconds = 2592000
      }
    },
  ]
}
