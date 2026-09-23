# Phase 1: prove Documenso in our stack. Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** establish, on a real self-hosted Documenso, every fact the matches design relies on, and write them down so phases 3 and 4 are planned on facts rather than on documentation.

**Architecture:** a throwaway local stack (Documenso, its Postgres, Mailpit as the SMTP sink) started with Docker Compose from a scratch directory, a throwaway Python probe that drives Documenso's API v2 and a tiny webhook receiver, and one committed note with what answered. Nothing here ships: the only committed file is the note.

**Tech Stack:** Docker Compose, the official `documenso/documenso` image (pinned tag), `postgres:17-alpine`, `axllent/mailpit`, Python 3.13 with the standard library only (`urllib`, `http.server`), `openssl`, `pdfsig` (poppler, already on the machine through `pdftoppm`), Playwright from the repository (`pnpm --filter @rebase/brand exec playwright`) to sign in a real browser.

**Spec:** `projects/hub/docs/superpowers/specs/2026-09-23-matches-and-contract-signing-design.md` (§ 1a, § 5 signature positions, § 6, § 7, § 10 phase 1).

## Global Constraints

- The probe runs only on the developer machine and against `localhost`; no request goes to documenso.com's cloud, and no real person's data is used: the PDF is the `--public` build of `lettera-di-incarico` filled from `incarico.esempio.json`, the signer is `probe-freelancer@example.com`.
- Pin the Documenso image to one exact version tag (never `latest`) and record it in the note.
- Every secret in the probe (auth secret, encryption keys, certificate passphrase, API token, webhook secret) is generated fresh with `openssl rand -hex 32` and lives only in the scratch directory; none appears in the note, in a commit or in Linear.
- The note is English, in the repository's voice (no em dashes), and lands in `projects/hub/docs/superpowers/specs/` on the milestone branch.
- No AI co-author trailer on any commit; the body's last line is the card id (`REB-N.`).

## Review Focus

1. `distributionMethod: NONE` really sends nothing: Mailpit must hold zero messages for the signer after distribute; one stray invitation would give a freelancer two mails per document.
2. The webhook secret is checked by header, not by signature: confirm the exact header name and that a request without it can be told apart, since the hub's endpoint has no cookie.
3. The sealed PDF is downloadable by API on a self-hosted instance with a team token (not only through the UI), and `pdfsig` shows a signature over the whole document.
4. Field percentages: a field placed at the Typst-measured position of the signature blank lands on the blank, on the right page, for an A4 page with the template's margins.
5. The same webhook can fire twice (Documenso retries): record whether it retries on a non-2xx answer and how often, since the hub must be idempotent.

---

### Task 1: Start Documenso locally with a self-signed certificate

**Files:**
- Create (scratch, not committed): `~/emdash/scratch/documenso-probe/compose.yml`, `.env`, `cert.p12`

- [ ] **Step 1: Pick the image tag.** Read the tags at `https://hub.docker.com/r/documenso/documenso/tags` (or `gh api repos/documenso/documenso/releases --jq '.[0:5][] | .tag_name'`) and take the newest stable `vX.Y.Z`. Write it down for the note.

- [ ] **Step 2: Make the signing certificate.**

```bash
mkdir -p ~/emdash/scratch/documenso-probe && cd ~/emdash/scratch/documenso-probe
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes \
  -subj "/CN=rebase probe signing/O=rebase/C=IT"
PASS=$(openssl rand -hex 16)
openssl pkcs12 -export -out cert.p12 -inkey key.pem -in cert.pem -passout pass:$PASS
echo "NEXT_PRIVATE_SIGNING_PASSPHRASE=$PASS" > .env
echo "NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS=$(base64 < cert.p12 | tr -d '\n')" >> .env
```

- [ ] **Step 3: Write the rest of `.env`.** Check every variable name against the image's own `.env.example` for the pinned tag (`gh api repos/documenso/documenso/contents/.env.example?ref=<tag> --jq .content | base64 -d`), since the names have changed between releases. At least:

```bash
cat >> .env <<EOF
NEXTAUTH_SECRET=$(openssl rand -hex 32)
NEXT_PRIVATE_ENCRYPTION_KEY=$(openssl rand -hex 32)
NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY=$(openssl rand -hex 32)
NEXT_PUBLIC_WEBAPP_URL=http://localhost:3300
NEXT_PRIVATE_INTERNAL_WEBAPP_URL=http://localhost:3000
NEXT_PRIVATE_DATABASE_URL=postgres://documenso:documenso@documenso-db:5432/documenso
NEXT_PRIVATE_DIRECT_DATABASE_URL=postgres://documenso:documenso@documenso-db:5432/documenso
NEXT_PRIVATE_SMTP_TRANSPORT=smtp-auth
NEXT_PRIVATE_SMTP_HOST=mailpit
NEXT_PRIVATE_SMTP_PORT=1025
NEXT_PRIVATE_SMTP_FROM_NAME=rebase probe
NEXT_PRIVATE_SMTP_FROM_ADDRESS=probe@example.com
# Documenso refuses to call private addresses unless they are listed (found by the 2026-09-23 run).
NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS=host.docker.internal
EOF
```

- [ ] **Step 4: Write `compose.yml`.**

```yaml
services:
  documenso-db:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: documenso
      POSTGRES_PASSWORD: documenso
      POSTGRES_DB: documenso
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U documenso"]
      interval: 5s
      retries: 20
  mailpit:
    image: axllent/mailpit:latest
    ports: ["127.0.0.1:8025:8025"]
  documenso:
    image: documenso/documenso:<the tag from step 1>
    env_file: .env
    depends_on:
      documenso-db: { condition: service_healthy }
    ports: ["127.0.0.1:3300:3000"]
    # Docker Desktop resolves host.docker.internal by itself; native Linux needs the alias.
    extra_hosts: ["host.docker.internal:host-gateway"]
```

- [ ] **Step 5: Start it and wait for it.**

Run: `docker compose -p documenso-probe up -d && until curl -fsS http://localhost:3300/api/health >/dev/null 2>&1 || curl -fsS http://localhost:3300 >/dev/null; do sleep 3; done; echo up`
Expected: `up` within two minutes. If the health path differs, note which URL answered.

- [ ] **Step 6: Create the account, a team and an API token.** Sign up at `http://localhost:3300` as `probe-admin@example.com`, confirm the address from Mailpit (`http://localhost:8025`), create a team `rebase-probe`, and create an API token for that team in its settings (API tokens). Record in the note whether any of these steps asked for a licence key, and where the token screen lives. Keep the token in `.env` as `PROBE_API_TOKEN=`.

### Task 2: Create, distribute and sign one envelope by API

**Files:**
- Create (scratch): `~/emdash/scratch/documenso-probe/probe.py`, `webhook.py`

- [ ] **Step 1: Build the PDF to sign.**

```bash
cd ~/emdash/repositories/<milestone worktree>
uv run python projects/hub/tools/build_contract_pdf.py lettera-di-incarico --public \
  --data projects/hub/content/contratti/incarico.esempio.json
cp projects/hub/content/contratti/dist/public/lettera-di-incarico-incarico.esempio.pdf \
  ~/emdash/scratch/documenso-probe/letter.pdf
```

- [ ] **Step 2: Start the webhook receiver.** It prints every request's headers and body and answers 200, or 500 when `FAIL_FIRST=1` and it is the first request (to observe retries).

```python
# webhook.py
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

seen = 0

class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        global seen
        seen += 1
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        print("----", seen, self.path, dict(self.headers), body.decode()[:4000], flush=True)
        code = 500 if os.environ.get("FAIL_FIRST") == "1" and seen == 1 else 200
        self.send_response(code)
        self.end_headers()

HTTPServer(("0.0.0.0", 8765), Hook).serve_forever()
```

Run: `python3 webhook.py | tee webhook.log` (in its own terminal or as a background job).
Register it in the team's webhook settings as `http://host.docker.internal:8765/documenso` with a secret from `openssl rand -hex 32`, subscribed to every document event. Record which events the form offers.

- [ ] **Step 3: Create the envelope.** Write `probe.py` with one function per call, printing the full response. Start from the documented shape and adjust to what the pinned version's OpenAPI says (`http://localhost:3300/api/v2/openapi.json` if served, otherwise `https://openapi.documenso.com`):

```python
# probe.py
import json, os, sys, urllib.request, uuid

BASE = "http://localhost:3300/api/v2"
TOKEN = os.environ["PROBE_API_TOKEN"]

def call(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers={"Authorization": TOKEN, **(headers or {})})
    with urllib.request.urlopen(req) as res:
        raw = res.read()
        print(method, path, res.status, res.headers.get("content-type"))
        return raw

def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = b""
    for name, value in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
    for name, (filename, data) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                "Content-Type: application/pdf\r\n\r\n").encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"

def create():
    payload = {
        "type": "DOCUMENT",
        "title": "Lettera di incarico n. 2026-001 (probe)",
        "recipients": [{
            "email": "probe-freelancer@example.com", "name": "Nome Cognome", "role": "SIGNER",
            "fields": [
                {"type": "SIGNATURE", "page": 2, "positionX": 55, "positionY": 74, "width": 30, "height": 5},
                {"type": "DATE", "page": 2, "positionX": 12, "positionY": 66, "width": 20, "height": 3},
            ],
        }],
    }
    body, ctype = multipart({"payload": json.dumps(payload)},
                            {"files": ("letter.pdf", open("letter.pdf", "rb").read())})
    print(call("POST", "/envelope/create", body, {"Content-Type": ctype}).decode())

if __name__ == "__main__":
    globals()[sys.argv[1]](*sys.argv[2:])
```

Run: `set -a; . ./.env; set +a; python3 probe.py create`
Expected: 200 with an envelope id. If the multipart field names differ (`payload`, `files`), find the right ones in the OpenAPI and record them. Record the exact request that worked.

- [ ] **Step 4: Distribute without mail.** Add `distribute(envelope_id)` posting `{"envelopeId": ..., "meta": {"distributionMethod": "NONE"}}` (adjust to the OpenAPI) to `/envelope/distribute`.

Run: `python3 probe.py distribute <id>`
Expected: the response lists the recipient with a `signingUrl`. Then open Mailpit: **zero** messages to `probe-freelancer@example.com`. Record the response shape (URL redacted to its path pattern) and the Mailpit count.

- [ ] **Step 5: Sign in a real browser.** Open the `signingUrl` with Playwright (headed is fine), type a signature in the signature field, complete. Take one screenshot of the signing page for the note's author only (not committed). Record every screen the signer passes through, and whether an account or a code is asked for.

- [ ] **Step 6: Read the webhook.** `webhook.log` must show `DOCUMENT_SIGNED` / `DOCUMENT_RECIPIENT_COMPLETED` and `DOCUMENT_COMPLETED`. Record: the header carrying the secret and its exact value format, the payload keys that identify the document (`id`, `envelopeId`), the status field, the recipients' fields, and the timing.

- [ ] **Step 7: Retries.** Repeat steps 3-6 with the receiver restarted as `FAIL_FIRST=1 python3 webhook.py`. Record whether Documenso retries after the 500, how many times and how far apart, or that it does not.

### Task 3: Read status, download the sealed PDF, cancel

**Files:**
- Modify (scratch): `~/emdash/scratch/documenso-probe/probe.py`

- [ ] **Step 1: Status by id.** Find in the OpenAPI the call that returns one envelope (for the hub's «Aggiorna stato») and add `status(envelope_id)`.

Run: `python3 probe.py status <completed id>`
Expected: a status meaning completed. Record path and field.

- [ ] **Step 2: Download the sealed PDF.** Find the download call for a completed envelope's document (an envelope item download, or v1's `GET /api/v1/documents/{id}/download` if v2 has none) and add `download(envelope_id)` writing `signed.pdf`.

Run: `python3 probe.py download <completed id> && pdfsig signed.pdf`
Expected: `pdfsig` lists one signature, signer `rebase probe signing`, and says it covers the whole document (validation fails on trust, which is expected with a self-signed certificate). Record whether an audit certificate page is appended, and the file's page count against the original.

- [ ] **Step 3: Cancel.** Create and distribute a second envelope, then find and call the cancel or delete call. Expected: the signing URL no longer lets anyone sign, and the webhook receives `DOCUMENT_CANCELLED` (or record which event, if any). Record the call.

- [ ] **Step 4: Isolation between environments.** Create a team `rebase-probe-preview` under the same user, with its own token and webhook, and try the first team's token on the second team's envelope. Then do the same with a second Documenso user and organisation. Record both results: the spec lets the preview share the production instance only through whichever arrangement refuses the other environment's token (401/403/404). (Run on 2026-09-23: two teams under one user did not isolate, separate users did; spec § 7 follows that.)

### Task 4: Measure the signature positions from Typst

**Files:**
- Create (scratch): `~/emdash/scratch/documenso-probe/positions.typ` notes only

- [ ] **Step 1: Mark one blank.** In a scratch copy of `projects/hub/tools/contract.typ.template`, wrap the signature blank of `field()` in a labelled `metadata` so it can be queried:

```typst
#let field(name) = context {
  let signature = name.starts-with("firma")
  let label = text(size: 0.75em, fill: inkquiet, name)
  let blank = box(
    width: calc.max(measure(label).width + 6pt, if signature { 55mm } else { 30mm }),
    stroke: (bottom: 0.6pt + inkquiet),
    inset: (x: 2pt, top: if signature { 16pt } else { 0pt }, bottom: 1.5pt),
    label,
  )
  if signature {
    let size = measure(blank)
    [#metadata((name: name, width: size.width.pt(), height: size.height.pt()))<signature>]
  }
  blank
}
```

- [ ] **Step 2: Query it.** Build the intermediate `.typ` once with the scratch template (pandoc as in `build_contract_pdf.py`), then:

Run: `typst query letter.typ "<signature>" --field value --format json` and `typst query letter.typ "<signature>" --format json` (the second gives the location; if it does not, use `#context { let l = here(); [#metadata((page: l.page(), x: l.position().x.pt(), y: l.position().y.pt()))<signature-pos>] }` inside the same function).
Expected: page, x, y in points for each signature blank. Convert to Documenso percentages with A4 = 595.28 × 841.89 pt: `positionX = x / 595.28 * 100`, `positionY = y / 841.89 * 100`, `width = w / 595.28 * 100`, `height = h / 841.89 * 100`.

- [ ] **Step 3: Check it lands.** Re-run Task 2 steps 3-5 with those numbers instead of the hand-picked ones. Expected: in the signed PDF the signature sits on the «firma professionista» rule. Record the exact query command and the conversion.

### Task 5: Write the note and open the milestone's draft PR

**Files:**
- Create: `projects/hub/docs/superpowers/specs/2026-09-23-documenso-probe.md`

- [ ] **Step 1: Write the note.** Sections: the pinned tag; the environment variables that were required (names only); account, team and token creation (and any licence prompt); each API call that worked (method, path, request shape, the response fields the hub will read), for create, distribute, status, download, cancel; the webhook (events offered, header, payload keys, retries observed); the signer's journey; the sealed PDF (`pdfsig` output summary, audit page); the per-team isolation; the Typst position query and the conversion; and a closing list **What phase 3 and 4 must do differently from the spec**, empty if nothing. No secret, no token, no signing URL token.

- [ ] **Step 2: Commit on the milestone branch.**

```bash
git add projects/hub/docs/superpowers/specs/2026-09-23-documenso-probe.md
git commit -m "docs(hub): what a self-hosted Documenso answered to the probe" -m "<why, what was confirmed, what differs from the spec>" -m "REB-N."
git log -1 --format=%B | grep -ci co-authored   # must print 0
git push origin HEAD:<milestone branch>
```

- [ ] **Step 3: Tear down.** `docker compose -p documenso-probe down -v` and delete `~/emdash/scratch/documenso-probe` (it holds the secrets).
