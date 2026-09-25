# The records that existed before this file did. Once `terraform apply` has taken them
# into the state, this file can be deleted; kept, it is harmless and lets a fresh clone
# rebuild the state without touching a record.

import {
  to       = cloudflare_dns_record.rebase_apex_a
  id       = "904a60b42314c819a66340ae8c1a0e88/4b320df8105069601637ac1582f8269d"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_pigro_a
  id       = "904a60b42314c819a66340ae8c1a0e88/c747632005bacb4d32e71dae99c3fede"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_preview_a
  id       = "904a60b42314c819a66340ae8c1a0e88/b7f63a831ec3e197b2d61393fc39f337"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_preview_pigro_a
  id       = "904a60b42314c819a66340ae8c1a0e88/230b8092cbaec938f6514d6a481d92a5"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_rsend_cname
  id       = "904a60b42314c819a66340ae8c1a0e88/3e7061b1c814fd42b5d7f12f9d247d7f"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_www_cname
  id       = "904a60b42314c819a66340ae8c1a0e88/004ba110f73bfa839c3b4e903142d496"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_send_mx
  id       = "904a60b42314c819a66340ae8c1a0e88/cbf198456059b23105bb4d07b2fd245d"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_dmarc_txt
  id       = "904a60b42314c819a66340ae8c1a0e88/a7614e84179f89407d752925cd954e95"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_resend_domainkey_txt
  id       = "904a60b42314c819a66340ae8c1a0e88/298bb9812b44aa1ddea4a7d5fc7932ee"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_send_txt
  id       = "904a60b42314c819a66340ae8c1a0e88/0e0ea4f5a87c41d611d3a4255b70438e"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_apex_google_site_verification_txt
  id       = "904a60b42314c819a66340ae8c1a0e88/3cfca8fc2e15a4783ca3c3c3064cfa84"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_links_cname
  id       = "904a60b42314c819a66340ae8c1a0e88/cbc5c024006eb542f958e3490cb2c564"
  provider = cloudflare.rebase
}

import {
  to       = cloudflare_dns_record.rebase_firma_a
  id       = "904a60b42314c819a66340ae8c1a0e88/02a38513577f4ed7f867fb6f9b605108"
  provider = cloudflare.rebase
}
