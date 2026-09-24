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
# token is per Google account and per property; it is not a secret.
resource "cloudflare_dns_record" "rebase_apex_google_site_verification_txt" {
  provider = cloudflare.rebase
  zone_id  = local.rebase_zone_id
  name     = "letsrebase.com"
  type     = "TXT"
  content  = "\"google-site-verification=ClhNGnYHY95wDv2fWqKoRz60_7wVXZw7SI0UciLQWsc\""
  ttl      = 1
  comment  = "Search Console domain property (2026-09-14)"
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
