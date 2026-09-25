# Cloudflare DNS for the platform's two zones, as code (ORB-196).
#
# The zones live in two Cloudflare accounts (letsrebase.com in Lorenzo's, joinorbiters.com
# in Ivan's), and a Cloudflare API token is scoped to one account, so there are two
# provider instances and two tokens. Neither token is ever in this tree: pass them as
#
#   export TF_VAR_rebase_api_token=cfut_...     # letsrebase.com, Zone:DNS:Edit
#   export TF_VAR_orbiters_api_token=cfat_...   # joinorbiters.com, Zone:DNS:Edit
#
# The state is local and ignored by git (see .gitignore here). The import blocks in
# `*-imports.tf` make it reproducible from nothing: `rebase.tf` and `orbiters.tf`
# declare twenty-five records, and on a fresh clone `terraform init && terraform
# apply` reads the twenty-four that carry an import block into a new state without
# changing any (the gap is the joinorbiters.com verification TXT; see README.md).

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
  description = "Cloudflare API token for the letsrebase.com zone (Zone:DNS:Edit)."
  type        = string
  sensitive   = true
}

variable "orbiters_api_token" {
  description = "Cloudflare API token for the joinorbiters.com zone (Zone:DNS:Edit)."
  type        = string
  sensitive   = true
}

provider "cloudflare" {
  alias     = "rebase"
  api_token = var.rebase_api_token
}

provider "cloudflare" {
  alias     = "orbiters"
  api_token = var.orbiters_api_token
}
