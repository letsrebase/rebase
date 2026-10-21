# Cloudflare for the platform's zone, as code (ORB-196).
#
# The zone is letsrebase.com, in Lorenzo's Cloudflare account. The token is never in
# this tree: pass it as
#
#   export TF_VAR_rebase_api_token=cfut_...     # letsrebase.com, Zone:DNS:Edit
#
# The state is local and ignored by git (see .gitignore here). The import blocks in
# `*-imports.tf` make it reproducible from nothing: on a fresh clone `terraform init &&
# terraform apply` reads every record and both buckets into a new state and writes the
# expiry rules again, which cannot be imported, without changing anything (see README.md).

terraform {
  required_version = ">= 1.5"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }
}

variable "rebase_api_token" {
  description = "Cloudflare API token for the letsrebase.com account: Zone:DNS:Edit on the zone, and Workers R2 Storage:Edit on the account for r2.tf."
  type        = string
  sensitive   = true
}

variable "rebase_account_id" {
  description = "Cloudflare account id of the letsrebase.com account (R2, r2.tf). Not a secret."
  type        = string
}

provider "cloudflare" {
  alias     = "rebase"
  api_token = var.rebase_api_token
}
