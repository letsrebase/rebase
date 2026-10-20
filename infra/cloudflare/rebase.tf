# letsrebase.com: every DNS record of the zone, imported from what the migration of
# 2026-09-14 (ORB-193) created by hand. `terraform plan` with no change is the proof the
# panel and this file agree.

locals {
  rebase_zone_id = "904a60b42314c819a66340ae8c1a0e88"
}

resource "cloudflare_dns_record" "rebase_apex_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Hetzner origin, same host as joinorbiters.com (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_pigro_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "pigro.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Hetzner origin, same host as joinorbiters.com (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_preview_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "preview.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Hetzner origin, same host as joinorbiters.com (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_preview_pigro_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "preview.pigro.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Hetzner origin, same host as joinorbiters.com (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_rsend_cname" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "rsend.letsrebase.com"
  type     = "CNAME"
  content  = "send.forge.rmta.net"
  ttl      = 1
  proxied  = false
  comment  = "Resend (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_www_cname" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "www.letsrebase.com"
  type     = "CNAME"
  content  = "letsrebase.com"
  ttl      = 1
  proxied  = false
  comment  = "www redirects to the apex in nginx (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_send_mx" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "send.letsrebase.com"
  type     = "MX"
  content  = "feedback-smtp.eu-west-1.amazonses.com"
  ttl      = 1
  priority = 10
  comment  = "Resend (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_dmarc_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "_dmarc.letsrebase.com"
  type     = "TXT"
  content  = "\"v=DMARC1; p=quarantine; adkim=r; aspf=r;\""
  ttl      = 1
  comment  = "mirrors joinorbiters.com without the GoDaddy rua"
}

resource "cloudflare_dns_record" "rebase_resend_domainkey_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "resend._domainkey.letsrebase.com"
  type     = "TXT"
  content  = "p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDltub0CR4qnmowhkbPQgD3cRi9xyAvcqr+MQMxu9Psw9uDjCZcvMwjNaES5Kfxu5LY+bJApUi8IGqfDNXxK/6ad7cucvWNuhTSgobwU1M2kaOaXNjlreA4WHwszzsh8/66cFLomdSvpKbrszEvTnHAJCktj0Mcfh5eqmGwQwImNQIDAQAB"
  ttl      = 1
  comment  = "Resend (migration 2026-09-14)"
}

resource "cloudflare_dns_record" "rebase_send_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "send.letsrebase.com"
  type     = "TXT"
  content  = "v=spf1 include:amazonses.com ~all"
  ttl      = 1
  comment  = "Resend (migration 2026-09-14)"
}

# Google Search Console's proof of ownership for the domain property (ORB-195). The
# token is per Google account and per property; it is not a secret. Ivan's account
# verifies with it; do not remove it.
resource "cloudflare_dns_record" "rebase_apex_google_site_verification_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "TXT"
  content  = "\"google-site-verification=ClhNGnYHY95wDv2fWqKoRz60_7wVXZw7SI0UciLQWsc\""
  ttl      = 1
  comment  = "Search Console domain property (2026-09-14)"
}

# Google Workspace for ciao@letsrebase.com, added in the panel on 2026-09-18 and taken
# into this file on 2026-10-20 (REB-702) exactly as they were, which is why none carries
# a Cloudflare comment: adding one would be a change, and these four are only imported.
# The mailbox (MX), its signature (DKIM) and its sender policy (SPF) make ciao@ work at
# all. The site verification is the Workspace domain's own, and most likely also what
# verified ciao@ as an owner of the Search Console domain property on 2026-10-20
# (REB-700): Search Console says only «domain name provider», not which record. Remove
# none of them.
resource "cloudflare_dns_record" "rebase_google_workspace_mx" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "MX"
  content  = "smtp.google.com"
  ttl      = 300
  priority = 10
}

resource "cloudflare_dns_record" "rebase_google_workspace_domainkey_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "google._domainkey.letsrebase.com"
  type     = "TXT"
  content  = "\"v=DKIM1;k=rsa;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv/jSJRgzZpZmYZ1LCxVna8gayA3OGRmNa3TxUtJvznp8RQVJu7cxy8dy6yec4UW3Av0NEFdgrSxmFh39oTh69WBxw5yirs5LMazqdCuUb88xGkMrwUX5ZlDdHduH4p/amYWOk5kATDXMB7GLZsIjJHixMzeO7KBbhIHjDLE0Eg3fEv5YAskx2fCefIhXwqa6Oy6\" \"L8uZzOn2qxyp8Pp/OAbWU0SyOM4qDacUZdU89yEGbykW84P0DMtWnEeVkYNOklbL520K472mfDD5Q86C0NY8r/Gia192oHH7QwfxuAveVCf7GHaoRxM4wrVs8HtUtIkK8UnRxmEewutvDpiwfewIDAQAB\""
  ttl      = 3600
}

resource "cloudflare_dns_record" "rebase_google_workspace_spf_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "TXT"
  content  = "\"v=spf1 include:_spf.google.com ~all\""
  ttl      = 3600
}

resource "cloudflare_dns_record" "rebase_google_workspace_site_verification_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "TXT"
  content  = "\"google-site-verification=dSF1rAy3hgizON3rYAH29kjA_XBr17gYgOUNaRq2Qus\""
  ttl      = 3600
}

resource "cloudflare_dns_record" "rebase_links_cname" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "links.letsrebase.com"
  type     = "CNAME"
  content  = "links2.resend-dns.com"
  ttl      = 1
  proxied  = false
  comment  = "Resend click tracking subdomain (REB-315)"
}

# Documenso, the contracts' signing site (REB-393): the same Hetzner origin, whose nginx
# proxies it to the hub's production compose project on 127.0.0.1:8090.
resource "cloudflare_dns_record" "rebase_firma_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "firma.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Documenso, the contracts' signing site (REB-393)"
}

# Vaultwarden, the members' password manager (REB-648): the same Hetzner origin, whose
# nginx proxies it to the `vaultwarden` compose project on 127.0.0.1:8091. Moving it to
# another host is this record plus the Environment's DEPLOY_HOST.
resource "cloudflare_dns_record" "rebase_vault_a" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "vault.letsrebase.com"
  type     = "A"
  content  = "204.168.255.175"
  ttl      = 1
  proxied  = false
  comment  = "Vaultwarden, the members' password manager (REB-648)"
}
