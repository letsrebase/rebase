# Runbook: the Slack app, and installing its client on a host (REB-739, REB-740)

PigroCRM talks to Slack through **one Slack app per installation, installed once per space**
(`docs/design/DECISIONS.md`, 2026-10-26, REB-730). The app is created on Slack's side by a
person, once; each space's admin then presses «Collega Slack» in Impostazioni → Slack and
installs it in their own workspace. Nothing in this repository creates the app or holds its
credentials: **steps 1 to 4 are Lorenzo's**, and so is putting the three values of step 5 on
each host. The code reads them from settings and never commits them.

## 1. Create the app from the manifest

Slack → <https://api.slack.com/apps> → **Create New App** → **From a manifest** → pick the
workspace that will own the app → paste:

```yaml
display_information:
  name: PigroCRM
  description: I promemoria del tuo CRM, direttamente su Slack.
  long_description: >-
    PigroCRM manda su Slack i promemoria che hai scelto: un messaggio diretto a te
    per ogni attività scaduta, fattura da incassare o contratto in scadenza, e, se
    l'amministratore lo sceglie, un post nel canale dello spazio per quelli che riguardano
    tutti. Cerca ogni persona per email, senza leggere altro del workspace, e non scrive mai
    a nome di nessuno.
  background_color: "#1f1f1f"
features:
  bot_user:
    display_name: PigroCRM
    always_online: false
oauth_config:
  redirect_urls:
    - https://pigro.letsrebase.com/api/slack/oauth/callback
    - https://preview.pigro.letsrebase.com/api/slack/oauth/callback
  scopes:
    bot:
      - chat:write
      - chat:write.public
      - im:write
      - users:read
      - users:read.email
      - channels:read
      - groups:read
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

**The redirect URL is `{PIGROCRM_PUBLIC_URL}/api/slack/oauth/callback`, one per host**, the
root's own, and it is the only one a host ever needs: a space does not register an address of
its own. A space starts the consent with its name in front of the `state` (`<slug>.<jti>`),
Slack sends the browser to the root's callback, and the root hands it to the space's callback
(`apps/api/.../oauth_relay.py`, the same relay Gmail and Drive use). Slack compares the URL with
the list registered on the app character by character, so a host that is not in the list
answers `bad_redirect_uri`. The manifest above lists production and the preview; a third host
is a third line.

What each scope is for (the list is `SLACK_BOT_SCOPES` in `core/slack/schemas.py`, and a test
holds this manifest's list to it):

| Scope | Used for |
|---|---|
| `chat:write` | the direct messages, and the post to a channel the bot is in |
| `chat:write.public` | the post to the chosen public channel without inviting the bot to it |
| `im:write` | opening the direct message with a person (`conversations.open`) |
| `users:read` | reading a member (`users.lookupByEmail` answers with the profile) |
| `users:read.email` | matching a PigroCRM user to a Slack member by email |
| `channels:read` | the channel picker: public channels |
| `groups:read` | the channel picker: private channels the bot was invited to |

No user scopes, no events, no interactivity, no slash commands: nothing in Slack calls
PigroCRM except the redirect after an install. Buttons to complete or snooze from Slack are a
later card and will need an interactivity URL and a signing secret then, not now.

## 2. Allow installation in more than one workspace

A new app installs only in the workspace that owns it. Each space installs it in its own, so
**distribution has to be on** (docs.slack.dev, «App lifecycle & distribution»):

1. <https://api.slack.com/apps> → the app → **Manage Distribution**.
2. Under **Share Your App with Other Workspaces**, complete the checklist (the redirect URLs
   of step 1 are HTTPS, which is what it checks).
3. **Activate Public Distribution**.

This is the unlisted kind: the app is not in the Slack Marketplace, and a workspace whose admins
only allow Marketplace apps, or require approval, will see Slack's own request screen when a
space's admin presses «Collega Slack». That is Slack's, and the person who connects tells their
workspace admin which permissions are asked for (the seven above). Deactivating public
distribution removes the app from every workspace but the owner's, so it is not a switch to
flip back and forth.

## 3. Do not enable PKCE or token rotation

PigroCRM keeps the client secret on the server, so the app is a confidential client and does not
use PKCE; turning it on marks the app a public client and Slack's support is needed to undo it
(docs.slack.dev, «Using PKCE»). Token rotation is off in the manifest because the stored token
is a plain bot token with no refresh token and no expiry to track; with rotation on, tokens
would expire after twelve hours and the connection would silently die.

## 4. Read the client id and secret

**Basic Information** → **App Credentials**: the **Client ID** and **Client Secret**. The
signing secret and the verification token are not used.

## 5. Put them on the host

Three lines in each environment's `${DEPLOY_PATH}/.env` (production and the preview), next to
the Google ones, then let the approved deployment recreate the containers (a push to
`main` that touched PigroCRM for the preview, a `pigrocrm-v<semver>` tag for production,
`docs/adding-a-project.md` §7), never a `docker compose` by hand on the host:

```
PIGROCRM_SLACK_CLIENT_ID=<Client ID>
PIGROCRM_SLACK_CLIENT_SECRET=<Client Secret>
PIGROCRM_SLACK_TOKEN_KEY=<openssl rand -base64 32>
```

`PIGROCRM_PUBLIC_URL` is already set on both hosts, and `PIGROCRM_SLACK_URL` keeps its default
(`https://slack.com`). The three are all or none: with the id and no key the Slack page says
Slack is not configured rather than connecting a workspace it could not store a token for.

**The key.** It seals each space's bot token (AES-256-GCM, the same `seal` as the Gmail and
Drive refresh tokens) under a key derived from it and the space's name, so a token copied into
another space's database does not open there. Keep it outside the database and the repository, as
`PIGROCRM_GOOGLE_TOKEN_KEY` is. Rotating it makes every stored token unreadable: the Slack page
of each space says so and the admin connects again; nothing else is lost.

Until the three are installed the feature is absent, not broken: Impostazioni → Slack says
«Slack non è configurato su questa installazione», the `describe_slack` tool is not on the MCP
surface, and the reminder pass sends nothing to Slack.

## 6. Check it

1. Impostazioni → Slack as an admin → **Collega Slack** → Slack's consent screen → the page
   comes back with «Slack collegato», the workspace's name, and the list of users with how each
   was matched.
2. A user whose Slack address is not their CRM address reads «Non trovato»; **Correggi** takes
   the address that user has in Slack. **Cerca di nuovo** asks Slack again for everybody
   without a manual match.
3. **Canale dei promemoria dello spazio** picks one channel, or none.
4. `pigrocrm reminders --dry-run` prints a `slack N` counter beside `push` and `email` for the
   space: the messages the real pass would send. Nothing is sent and nothing is written.

## 7. What the pass sends, and what it never does

The pass (`pigrocrm reminders`, every fifteen minutes) sends **one direct message per person
per pass**, carrying every reminder of theirs that is due, severest first (at most twenty; the
rest go in the next pass), and records each in `reminder_delivery` with channel `slack`, after
Slack accepted the message. It holds a message in a person's quiet hours, sends only what reached
the person's list after they were matched (connecting a workspace does not send the backlog),
and posts the space-wide reminders (the types for admins) to the chosen channel once per occurrence
and severity, recorded in `slack_channel_posts`.

A failed send writes nothing and is tried again in the next pass. A token Slack says is dead
(`invalid_auth`, `token_revoked`, `account_inactive`) is recorded on the workspace
(`status = revoked`, the sentence in `last_error`) and the admin sees «ricollega Slack»; an
outage ends the pass for Slack without recording anything.

The bot token is in no log, no API response and no MCP answer: it exists decrypted for the length
of one call. The pass logs a counter and, on a refusal, Slack's fixed error word
(`channel_not_found`), never a name, an address or a title.

## 8. When something answers an error

| Slack says | Meaning | Do |
|---|---|---|
| `bad_redirect_uri` | the host is not in the app's redirect URLs | add `{public URL}/api/slack/oauth/callback` and install again |
| `invalid_client_id`, `bad_client_secret` | the id or the secret on the host is not the app's | step 4 and 5 |
| `missing_scope:...` on connecting | the app was changed after the manifest | restore the scopes of step 1; the install is refused by name |
| `invalid_auth`, `token_revoked`, `account_inactive` | the workspace uninstalled the app or deactivated it | the admin connects again |
| `channel_not_found`, `is_archived`, `not_in_channel` on a post | the chosen channel went, or is private and the bot is not in it | pick another channel, or invite the bot |
| `users_not_found` | nobody in the workspace has that address | **Correggi** with the address the person has in Slack |
| `ratelimited` | Slack asked to slow down | nothing: a `Retry-After` is honoured, and the pass tries again in fifteen minutes |
