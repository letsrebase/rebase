# shellcheck shell=sh
# Sourced, not run: the image's /start.sh sources every /etc/vaultwarden.d/*.sh just before it
# execs the server, which is upstream's own hook for this (REB-699). The compose file mounts
# this file there, read-only, and branding/images beside it.
#
# It builds a copy of the web vault that says the product's name where upstream says
# «Vaultwarden Web», and points the server at the copy with WEB_VAULT_FOLDER. The image is
# not touched and nothing lands in the data directory, so the backup carries none of it and a
# restore onto a fresh host gets the same pages from the repository. Every replacement is a
# literal looked up at start, not a patch against a version: an image bump that moves one only
# makes this print a warning and leaves that string as upstream wrote it. The server comes up
# either way, because a password manager that will not start over its branding is the worse
# fault; tests/backup-restore.sh is what fails a bump that lets one through.
#
# What it rewrites, in the copy:
#   - the tab title, the footer, the splash's alt text and the authenticator's issuer (they
#     are literals in app/main.<hash>.js, whose name is upstream's hash of the unbranded file);
#   - the web manifest's name and colours, `theme-color`, the pinned tab's tint and the
#     browserconfig's tile;
#   - the titles inside the three logo SVGs and the Duo page's alt text;
#   - the favicons, from branding/images.
# The favicons, the manifest and main.js are renamed with a hash of their new content and
# index.html is pointed at the new names: upstream serves every one of them as
# `cache-control: public, immutable, max-age=604800`, so a browser that opened the vault before
# the look existed would otherwise keep the old file for a week whatever the server sends now.
# index.html itself is served `public, max-age=600`, so a browser finds the new names within
# ten minutes of a deploy.
#
# POSIX sh, because /start.sh is: it runs in the shell that then execs the server. Names it
# defines are prefixed so they cannot collide with the image's.

rebase_brand_web_vault() {
  src=/web-vault
  dst=/web-vault-rebase
  images=/branding/images

  # The body runs in a subshell: its `set -e` and its `exit` are its own, never start.sh's.
  (
    set -eu
    [ -d "$src" ] || exit 1
    [ -d "$images" ] || exit 1
    : "${BRAND_NAME:?}" "${BRAND_THEME_COLOR:?}" "${BRAND_BACKGROUND_COLOR:?}"
    theme="#$BRAND_THEME_COLOR"
    background="#$BRAND_BACKGROUND_COLOR"
    # An otpauth:// label and issuer are URL components.
    encoded=$(printf '%s' "$BRAND_NAME" | sed 's/ /%20/g')

    # Replaces a literal, anywhere in a file. A literal that is not there is a warning.
    replace() {
      if [ ! -f "$1" ]; then
        echo "brand-web-vault: $1 is missing, left as upstream wrote it" >&2
        return 0
      fi
      count=$(OLD=$2 perl -0777 -ne 'print scalar(() = /\Q$ENV{OLD}\E/g)' "$1")
      if [ "$count" -eq 0 ]; then
        echo "brand-web-vault: '$2' is not in $1, left as upstream wrote it" >&2
        return 0
      fi
      OLD=$2 NEW=$3 perl -0777 -pi -e 's/\Q$ENV{OLD}\E/$ENV{NEW}/g' "$1"
    }

    # Prints the name a file takes when it carries a hash of its content: a.png, a.<hash>.png.
    hashed_name() {
      hash=$(sha256sum "$1" | cut -c1-12)
      printf '%s' "${1%.*}.$hash.${1##*.}"
    }

    rm -rf "$dst"
    cp -a "$src" "$dst"
    cd "$dst"
    # Source maps are the largest part of the image's web vault and nothing a browser asks for.
    find . -name '*.map' -type f -delete

    # The favicons: ours over upstream's. favicon.ico stays where browsers look for it by
    # default.
    cp "$images/favicon.ico" favicon.ico
    for file in favicon-16x16.png favicon-32x32.png apple-touch-icon.png safari-pinned-tab.svg; do
      cp "$images/$file" "images/$file"
    done
    cp "$images/icons/android-chrome-192x192.png" images/icons/android-chrome-192x192.png
    cp "$images/icons/android-chrome-512x512.png" images/icons/android-chrome-512x512.png

    # The icons the page names, under their hashed names; the plain names stay as well, for a
    # page a browser already holds.
    for file in images/favicon-16x16.png images/favicon-32x32.png images/apple-touch-icon.png images/safari-pinned-tab.svg; do
      new=$(hashed_name "$file")
      cp "$file" "$new"
      replace index.html "$file" "$new"
    done

    # The manifest, which index.html names by a hash of upstream's content.
    manifest=$(sed -n 's/.*rel="manifest" href="\([^"]*\)".*/\1/p' index.html)
    for file in manifest.json "$manifest"; do
      if [ -z "$file" ] || [ ! -f "$file" ]; then
        echo "brand-web-vault: no manifest '$file'" >&2
        continue
      fi
      replace "$file" 'Vaultwarden Web' "$BRAND_NAME"
      replace "$file" '"theme_color": "#FFFFFF"' "\"theme_color\": \"$theme\""
      replace "$file" '"background_color": "#FFFFFF"' "\"background_color\": \"$background\""
    done
    if [ -n "$manifest" ] && [ -f "$manifest" ]; then
      for file in images/icons/android-chrome-192x192.png images/icons/android-chrome-512x512.png; do
        new=$(hashed_name "$file")
        cp "$file" "$new"
        replace "$manifest" "$file" "$new"
      done
      new=$(hashed_name "$manifest")
      mv "$manifest" "$new"
      replace index.html "$manifest" "$new"
    fi

    # The tab: the document's own title until the app boots, the app's after.
    replace index.html 'Vaultwarden Web' "$BRAND_NAME"
    replace index.html '#175DDC' "$theme"
    replace index.html 'alt="Vaultwarden"' "alt=\"$BRAND_NAME\""
    replace browserconfig.xml '#175DDC' "$theme"
    replace browserconfig.xml 'images/icons/mstile-150x150.png' 'images/icons/android-chrome-192x192.png'

    # The app: the title of every route, the footer, the splash, the authenticator entry.
    for main in app/main.*.js; do
      [ -f "$main" ] || { echo "brand-web-vault: no app/main.*.js" >&2; break; }
      replace "$main" 'Vaultwarden Web' "$BRAND_NAME"
      replace "$main" 'Web Vault for Vaultwarden (an unofficial rewrite of the Bitwarden® server).' 'Web Vault.'
      replace "$main" 'Vaultwarden is not associated with' "$BRAND_NAME is not associated with"
      replace "$main" '["alt","Vaultwarden"' "[\"alt\",\"$BRAND_NAME\""
      replace "$main" 'otpauth://totp/Vaultwarden:' "otpauth://totp/$encoded:"
      replace "$main" '&issuer=Vaultwarden' "&issuer=$encoded"
      replace "$main" '<title>Vaultwarden</title>' "<title>$BRAND_NAME</title>"
      new=$(hashed_name "$main")
      mv "$main" "$new"
      replace index.html "$main" "$new"
    done

    # Titles and alt texts nobody reads on purpose.
    replace images/logo.svg '<title>Vaultwarden</title>' "<title>$BRAND_NAME</title>"
    replace images/logo-white.svg '<title>Vaultwarden</title>' "<title>$BRAND_NAME</title>"
    replace images/icon-white.svg '<title>Vaultwarden Icon - White</title>' "<title>$BRAND_NAME</title>"
    replace duo-redirect-connector.html 'alt="Vaultwarden"' "alt=\"$BRAND_NAME\""
  )
}

# Called as a plain statement, not as the condition of an `if` or an `||`: `set -e` does nothing
# inside anything run as a condition, so the subshell's would not stop it at a failed `cp`.
rebase_brand_web_vault
rebase_status=$?
if [ "$rebase_status" -eq 0 ]; then
  export WEB_VAULT_FOLDER=/web-vault-rebase
else
  echo "brand-web-vault: could not build the branded web vault, serving upstream's as it is" >&2
fi
