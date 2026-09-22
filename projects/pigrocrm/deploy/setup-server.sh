#!/usr/bin/env bash
# Needs root (writes /etc/nginx/sites-available/, symlinks into sites-enabled/,
# runs `nginx -t` and `systemctl reload nginx`): run as `sudo bash
# deploy/setup-server.sh`, or as a user with sudo equivalent -- see README.md's
# own "Deploy" section for the full prerequisite and why this is documented
# rather than silently assumed or auto-elevated with a non-interactive `sudo`.
# `ci-deploy.yml` invokes this same script over plain SSH, so the deploy user
# configured there has to already satisfy this, not just whoever runs it by hand.
#
# Idempotent: safe to run on every deploy.
#
# "Idempotent" needs one qualification: after `certbot --nginx -d "$DOMAIN"` has run
# on this server (see the note at the bottom of this file), certbot rewrites this
# same file in place to add the port-443 server block and the certificate paths. A
# script that unconditionally regenerated the file on every deploy would silently
# erase that block the next time CI ran this script -- TLS would vanish on the very
# next push to main, with nothing in the deploy log to say why. So: write the plain
# HTTP vhost only the first time, or any time it has not yet been TLS-enabled; once
# `listen 443 ssl` shows up in it (certbot's own signature), leave it alone.
set -euo pipefail

if [ -z "${PIGROCRM_DOMAIN:-}" ]; then
  echo "PIGROCRM_DOMAIN must be set, e.g. PIGROCRM_DOMAIN=tuodominio.it bash deploy/setup-server.sh -- there is no default, so a fresh install never points at somebody else's domain." >&2
  exit 1
fi
DOMAIN="$PIGROCRM_DOMAIN"
CONF="/etc/nginx/sites-available/${DOMAIN}"

if [ -f "$CONF" ] && grep -q 'listen 443 ssl' "$CONF"; then
  echo "nginx for ${DOMAIN} already has TLS configured by certbot: not overwriting ${CONF}."
else
  cat > "$CONF" <<CONFEOF
server {
    listen 80;
    server_name ${DOMAIN};
    client_max_body_size 25M;

    # REB-271: the same set installed on pigro.letsrebase.com's own vhost
    # (deploy/nginx/security-headers.conf has the reasoning behind each line and why
    # \`Referrer-Policy\` is \`strict-origin\` here, not \`strict-origin-when-cross-
    # origin\`), inlined here rather than \`include\`d: this script has nothing that
    # provisions /etc/nginx/snippets/ on a fresh self-hosted install, and an
    # \`include\` of a path that does not exist fails \`nginx -t\` outright.
    # \`frame-ancestors 'none'\` and nosniff cost nothing and are enforced from day
    # one; the rest of the policy is the same set REB-306 proved against a real
    # browser and flipped to enforcing, merged into the one header below.
    # No \`includeSubDomains\` on the HSTS line,
    # unlike the committed vhost: \$PIGROCRM_DOMAIN here is a domain this script does
    # not own (a self-hoster's own apex), and pinning every sibling subdomain of it to
    # HTTPS for a year from a single CRM visit could brick an unrelated HTTP-only
    # service on the same domain that this installation has no say over.
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin always;
    add_header X-Frame-Options DENY always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://eu-assets.i.posthog.com; style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self'; frame-src 'self' blob:; connect-src 'self' https://eu.i.posthog.com https://eu-assets.i.posthog.com; object-src 'none'; base-uri 'self'; frame-ancestors 'none';" always;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
CONFEOF
  echo "nginx configured for ${DOMAIN} (HTTP only until certbot runs)."
fi

ln -sf "$CONF" "/etc/nginx/sites-enabled/${DOMAIN}"
nginx -t
systemctl reload nginx
echo "nginx active for ${DOMAIN}"
