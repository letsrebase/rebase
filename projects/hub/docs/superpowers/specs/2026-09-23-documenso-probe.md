# What a self-hosted Documenso answered: the phase 1 probe for matches and signed contracts

Date: 2026-09-23. Status: probe done, gate for phases 3 and 4 of
`2026-09-23-matches-and-contract-signing-design.md` (REB-387). Tracker: REB-389.

A throwaway stack on a developer Mac (Documenso, its Postgres, Mailpit as the SMTP sink)
ran from a scratch directory outside the repository, driven by a standard-library Python
script against API v2 and by Playwright for the signer. Everything ran against
`localhost`; nothing reached Documenso's cloud. The PDF was the `--public` build of
`lettera-di-incarico` from `incarico.esempio.json`, the signer
`probe-freelancer@example.com`. The stack and its secrets were deleted afterwards.

Every capability the design relies on works on the free self-hosted edition, with two
corrections to the spec: one team per environment does not isolate the preview from
production, a separate Documenso user does (§ 8); and the webhook's retries arrive at
once and can overlap a delivery still in progress, so "acknowledged and ignored" has to
hold under concurrency (§ 5). The full list is in § 11.

## 0. The five review questions

1. **`distributionMethod: NONE` sends nothing.** Confirmed. Eight envelopes were
   distributed with it, four signed to completion, and Mailpit held zero messages for
   the signer at the end. The only Documenso mail left was «Signing Complete!» to the
   account that owns the envelope, and `meta.emailSettings.ownerDocumentCompleted: false`
   switches that off too (§ 4).
2. **The secret is a header, not a signature.** Confirmed. Every call carries
   `X-Documenso-Secret` whose value is the secret typed in the webhook form, verbatim:
   no HMAC, no timestamp, nothing else in the headers or the body that authenticates.
   A request without the header is told apart by its absence. A webhook saved with no
   secret sends the header empty (`execute-webhook-call.ts`), so the hub must refuse an
   empty header as well as a wrong one.
3. **The sealed PDF is downloadable by API with a team token.** Confirmed:
   `GET /api/v2/envelope/item/{envelopeItemId}/download?version=signed`, database storage,
   no S3. `pdfsig` reads one signature by `rebase probe signing` over the total document,
   valid, with the certificate's issuer not trusted (the self-signed certificate, as
   expected) (§ 7).
4. **Field percentages land on the blank.** Confirmed on page 2 of an A4 letter with the
   template's margins: the signature sits on the «firma professionista» rule and the date
   on «data firma» (§ 9). The Typst recipe in the plan needed one change: `here()` inside
   a table cell returns a wrong position.
5. **The same webhook can fire twice.** Confirmed, and more often than "a retry later":
   after a non-2xx answer Documenso retries at once, three times, within about 160 ms in
   all, then gives up. A receiver slower than 10 seconds counts as failed and the retry
   arrives while the first delivery is still running (§ 5).

## 1. The image

`documenso/documenso:v2.18.0`, the newest stable release (published 2026-09-09), digest
`sha256:126976b9e3be54193e1a3be8d22130af1913aaa894c550b98870a2cc4c422650`, amd64 and
arm64. 441 MB on disk; about 630 MiB of memory once warm, Postgres about 80 MiB. It runs
its Prisma migrations at start and answered `GET /api/health` about 15 seconds after the
container started:
`{"status":"ok","checks":{"database":{"status":"ok"},"certificate":{"status":"ok"}}}`.
`GET /api/certificate-status` answers `{"isAvailable":true}`.

With no licence key the licence client logs `Derived Status: NOT_FOUND` and makes no
network call (`license-client.ts` returns before `fetch` when the key is empty). Telemetry
is off with `DOCUMENSO_DISABLE_TELEMETRY=true`.

## 2. Environment variables

Names only, checked against the tag's `.env.example`. Required for this probe:
`NEXTAUTH_SECRET`, `NEXT_PRIVATE_ENCRYPTION_KEY`, `NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY`,
`NEXT_PUBLIC_WEBAPP_URL`, `NEXT_PRIVATE_INTERNAL_WEBAPP_URL` (the container's own
`http://localhost:3000`, used for its background jobs), `NEXT_PRIVATE_DATABASE_URL`,
`NEXT_PRIVATE_DIRECT_DATABASE_URL`, `NEXT_PRIVATE_SIGNING_PASSPHRASE`,
`NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS` (the `.p12` as base64),
`NEXT_PRIVATE_SMTP_TRANSPORT`, `NEXT_PRIVATE_SMTP_HOST`, `NEXT_PRIVATE_SMTP_PORT`,
`NEXT_PRIVATE_SMTP_FROM_NAME`, `NEXT_PRIVATE_SMTP_FROM_ADDRESS`, and
`NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS` whenever the webhook's host resolves to a
private address (§ 5). Set but optional: `NEXT_PRIVATE_SIGNING_TRANSPORT=local` (the
default) and `DOCUMENSO_DISABLE_TELEMETRY`.

The start script prints «Certificate not found or not readable» when the certificate
comes as `NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS`, because it only looks for the file
path. It is harmless: the health check reports the certificate ok and the PDFs are sealed.

`NEXT_PUBLIC_UPLOAD_TRANSPORT` defaults to `database`: the uploaded and the sealed PDFs
live in Postgres. The `documenso` container kept no state the probe relied on.

Not exercised here, for phase 4 to confirm: `NEXT_PRIVATE_SMTP_TRANSPORT=resend` with
`NEXT_PRIVATE_RESEND_API_KEY`, and `NEXT_PUBLIC_DISABLE_SIGNUP=true` so that nobody can
open an account on the public instance.

## 3. Account, team and token

No step asked for a licence key.

- **Sign up** at `/signup`: name, email, password and a signature (Draw, Type or Upload),
  then a confirmation link by mail (it arrived in Mailpit). After confirming, the account
  has a «Personal Organisation» with a «Personal Team».
- **Team**: `/o/<organisation>/settings/teams`, «Create team», with a name and a URL slug.
  `rebase-probe` lives at `/t/rebase-probe`.
- **API token**: `/t/<team>/settings/tokens`, «Create token», a name and an expiry of
  7 days, 1, 3 (the default), 6 or 12 months, or Never. It is shown once, `api_` and
  16 characters. `Authorization: <token>` and `Authorization: Bearer <token>` both work;
  no header answers 401 «Invalid session or API token.».
- **Webhook**: `/t/<team>/settings/webhooks`, «Create Webhook», with the URL, an Enabled
  switch, the triggers and an optional secret.

API v2 has no route for tokens or webhooks: both are made in the UI, once per
environment. Error bodies are
`{"message", "code", "data": {"code", "httpStatus", "stack"}}` and carry a server stack
trace, so the hub should show the admin only `message`.

## 4. The API calls that worked

Base `http://<host>/api/v2`, OpenAPI at `/api/v2/openapi.json`. The shapes documented in
the spec worked as written; nothing had to be renamed.

**Create.** `POST /envelope/create`, `multipart/form-data`, two parts: `payload`, a JSON
string, and `files`, the PDF (filename and `Content-Type: application/pdf`). The payload
below joins two runs that each answered 200: the measured letter sent every key but
`envelopeExpirationPeriod`, which another envelope sent with `distributionMethod` alone:

```json
{
  "type": "DOCUMENT",
  "title": "Lettera di incarico n. 2026-001",
  "externalId": "<the hub's document id>",
  "recipients": [{
    "email": "probe-freelancer@example.com", "name": "Nome Cognome", "role": "SIGNER",
    "fields": [
      {"type": "SIGNATURE", "page": 2, "positionX": 51.008, "positionY": 84.011,
       "width": 26.19, "height": 2.692},
      {"type": "DATE", "page": 2, "positionX": 27.855, "positionY": 76.879,
       "width": 14.286, "height": 0.791,
       "fieldMeta": {"type": "date", "fontSize": 9, "textAlign": "left"}}
    ]
  }],
  "meta": {
    "distributionMethod": "NONE", "language": "it", "timezone": "Europe/Rome",
    "dateFormat": "dd/MM/yyyy", "envelopeExpirationPeriod": {"disabled": true},
    "emailSettings": {"recipientSigningRequest": false, "recipientRemoved": false,
      "recipientSigned": false, "documentPending": false, "documentCompleted": false,
      "documentDeleted": false, "ownerDocumentCompleted": false,
      "ownerRecipientExpired": false, "ownerDocumentCreated": false}
  }
}
```

The answer is `200 {"id": "envelope_..."}` and nothing else: the envelope item's id,
needed for the download, comes from the status call. The item's title is the uploaded
file name, and the sealed download is named after it with `_signed`, so the hub should
upload with a meaningful name. The envelope starts `DRAFT`. Each `meta` key above was
exercised: `dateFormat` and `timezone` print «23/09/2026» in the date field;
`envelopeExpirationPeriod: {"disabled": true}` leaves the recipient's `expiresAt` null,
where the default is 90 days after sending; `language: "it"` does not translate the
signing page (§ 6).

**Distribute.** `POST /envelope/distribute`, JSON
`{"envelopeId": "...", "meta": {"distributionMethod": "NONE"}}` (the `meta` is redundant
when the create set it). The answer:

```
{"success": true, "id": "envelope_...", "recipients": [{"id": 1, "name": "Nome Cognome",
 "email": "probe-freelancer@example.com", "token": "<recipient token>", "role": "SIGNER",
 "signingOrder": null, "signingUrl": "<NEXT_PUBLIC_WEBAPP_URL>/sign/<recipient token>"}]}
```

The envelope becomes `PENDING`. `POST /envelope/redistribute` with
`{"envelopeId", "recipients": [<recipient id>]}` exists to renew links; not needed with
expiry off.

**Status.** `GET /envelope/{envelopeId}`. The hub reads `status` (`DRAFT`, `PENDING`,
`COMPLETED`, `REJECTED`, `CANCELLED`), `envelopeItems[0].id`, `externalId`, and per
recipient `signingStatus` (`NOT_SIGNED`, `SIGNED`, `REJECTED`), `signedAt` and
`rejectionReason`. `completedAt` is also set when an envelope is cancelled, so it is not
a signature date: the date of signature is the recipient's `signedAt`, or `completedAt`
only together with `status: COMPLETED`. The answer also carries each recipient's
`token`, which is the secret half of the signing URL.

**Download.** `GET /envelope/item/{envelopeItemId}/download?version=signed` answers
`200 application/pdf` with `Content-Disposition: attachment; filename="<name>_signed.pdf"`.
`version=original` returns the uploaded bytes unchanged. Also tried: the deprecated
`GET /document/{documentId}/download` (the numeric `id` of the webhook payload) returns
the same sealed file; `GET /document/{documentId}/download-beta` answers 400 «Document
downloads are only available when S3 storage is configured.»;
`GET /envelope/{envelopeId}/certificate/download` and `/audit-log/download` return the
certificate page and a three-page audit log as separate PDFs.

**Cancel.** `POST /envelope/cancel`, JSON `{"envelopeId": "...", "reason": "..."}`,
answers `{"success": true}` and fires `DOCUMENT_CANCELLED`. It accepts only `PENDING`
envelopes: a draft answers 400 «Only pending documents can be cancelled». After a cancel
the signing URL still opens the document and the signature pad; inserting the field fails
with «An error occurred while signing the field.», nothing is signed, the status stays
`CANCELLED`, and opening the page still fires `DOCUMENT_OPENED`. `POST /envelope/delete`
exists and was not needed.

## 5. The webhook

**Events offered** by the form, 14: `document.created`, `document.sent`,
`document.opened`, `document.signed`, `document.completed`, `document.rejected`,
`document.cancelled`, `recipient.expired`, `document.recipient.completed`,
`document.reminder.sent`, `template.created`, `template.updated`, `template.deleted`,
`template.used`. In the body they are upper case: `DOCUMENT_COMPLETED` and so on.

**The call.** `POST` to the registered URL, `Content-Type: application/json`,
`User-Agent: node`, `X-Documenso-Secret: <the secret>`. The body is
`{"event", "payload", "createdAt", "webhookEndpoint"}`. The payload is the envelope:
`envelopeId` (the id the create returned, the key the hub should look up), `id` (a
numeric legacy document id), `externalId`, `title`, `status`, `completedAt`, `teamId`,
`userId`, `documentMeta`, and `recipients[]` with `id`, `email`, `name`, `role`,
`signingStatus`, `readStatus`, `sendStatus`, `signedAt`, `expiresAt`,
`rejectionReason` and `token`. A second list, `Recipient`, repeats the recipients.

**Order and timing** for one signature: `DOCUMENT_CREATED` at create, `DOCUMENT_SENT`
at distribute, `DOCUMENT_OPENED` when the page loads, then on «Sign»
`DOCUMENT_RECIPIENT_COMPLETED`, `DOCUMENT_SIGNED` and `DOCUMENT_COMPLETED` within one to
four seconds. `DOCUMENT_COMPLETED` is triggered by the sealing job after the sealed file
is stored (`seal-document.handler.ts`), and the download worked right after it every time.

**Retries.** With the default job provider (`NEXT_PRIVATE_JOBS_PROVIDER=local`), a
delivery answered with a non-2xx status is retried at once, up to three times: four
attempts in about 160 ms, then the job is `FAILED` and nothing retries it later. The
team's webhook log in the UI has a manual resend. Observed:

- first answer 500, the retry 70 ms later answered 200: two deliveries of the same event;
- every answer 500: four deliveries within 160 ms, then none;
- the first `DOCUMENT_COMPLETED` answered after 12 seconds: Documenso gave up at 10
  seconds («Request timed out after 10000ms»), retried at once, and the hub side received
  the second delivery while the first was still being handled.

The bodies of the attempts differ in `createdAt`, so a duplicate is recognised by event
and `envelopeId`, not by the body. The BullMQ provider (Redis) retries with exponential
backoff instead, from the source; it was not run.

**Private addresses.** A URL whose host is literally private (`localhost`, an RFC 1918
address) is refused when the webhook is saved. A host name is accepted when saved and
resolved at each delivery: `http://mailpit:8025/...`, a compose service name, failed every
delivery with «Webhook URL resolves to a private or loopback address» until the host is
listed in `NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS`. The probe reached its receiver on the
Mac through `host.docker.internal`, listed there.

## 6. The signer's journey

No account, no code, no mail check: the signing URL opens the document directly.

1. `/sign/<token>`: the PDF on the right; on the left «Sign Document», the recipient's
   name prefilled, a signature pad, the count of fields remaining, and «Download PDF» and
   «Reject Document». The date field is filled on its own and does not count.
2. A click on the pad opens Draw, Type and Upload; the probe typed the name, «Next».
3. «Next Field» scrolls to the signature field, «Click to insert field», one click places
   it. The button becomes «Complete».
4. «Complete» opens «Are you sure?», a consent paragraph about the electronic signature
   with a link to Documenso's signature disclosure, «Cancel» and «Sign».
5. `/sign/<token>/complete`: «Document Signed», «Everyone has signed! You will receive an
   email copy of the signed document.», «Share», and a «Claim account» form inviting the
   signer to open a Documenso account.

The page's language follows the browser, not the envelope's `language`: an `it-IT`
browser read «Firmatario», «Campo successivo», «Firma documento», an `en-US` one read
English. `meta.redirectUrl` exists and was not tried.

## 7. The sealed PDF

The letter went in with 2 pages and came out with 3: Documenso appends a «Signing
Certificate» page (in English) with the signer's name and email, «Authentication Level:
Email», the signature image, a signature id, the IP address («Unknown» behind Docker's
network), the device, the sent, viewed and signed times in UTC, a QR code and the
envelope id. `pdfsig`:

```
Signature #1:
  - Signer Certificate Common Name: rebase probe signing
  - Signer full Distinguished Name: C=IT,O=rebase,CN=rebase probe signing
  - Signing Hash Algorithm: SHA-256
  - Signature Type: ETSI.CAdES.detached
  - Total document signed
  - Signature Validation: Signature is Valid.
  - Certificate Validation: Certificate issuer isn't Trusted.
```

The certificate was self-signed with
`openssl req -x509 -newkey rsa:2048 ... -subj "/CN=rebase probe signing/O=rebase/C=IT"`
and exported with `openssl pkcs12 -export` (OpenSSL 3.6); Documenso read the modern
PKCS#12 encryption without trouble.

## 8. One team per environment is not enough

The spec (§ 7) gives the preview its own Documenso team, API token and webhook on the
same instance. With both teams belonging to one user, that isolates nothing that matters:

- the production team's token read the preview team's envelope by id (200);
- it cancelled that envelope (`POST /envelope/cancel`, 200, now `CANCELLED`);
- and the `DOCUMENT_CANCELLED` of the preview's envelope went to the production team's
  webhook, not the preview's;
- only the listing (`GET /envelope`) and the item download (404) stayed per team.

The reason is in `getEnvelopeWhereInput`: an envelope is reachable when it belongs to the
token's team or when its `userId` is the token's user, whatever the team.

A second user, `probe-preview-admin@example.com`, with its own organisation, team
`rebase-probe-preview2`, token and webhook, did isolate: in both directions the other
environment's envelope answered 404 to `GET /envelope/{id}`, to the item download and to
the cancel, and the envelope stayed `PENDING`. So one Documenso user per environment,
each owning its environment's team and token, with separate organisations as tested.
Two users inside one organisation should also work by the same rule, but it was not run.

## 9. Signature positions from Typst

The plan's first recipe, a `metadata` carrying `here().position()` next to the blank,
gave the right position for the inline «data firma» blank and a wrong one, about 330 pt
too high, for the blanks inside the signatures table. What worked: tag the blank's top
left with a placed element, and read every tag's position from one document-level query.
In a scratch copy of `contract.typ.template`:

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
  let size = measure(blank)
  box({
    place(top + left, [#metadata((name: name, width: size.width.pt(), height: size.height.pt()))<blank>])
    blank
  })
}
```

and after `$body$`:

```typst
#context [#metadata(query(<blank>).map(it => {
  let at = it.location().position()
  (name: it.value.name, page: at.page, x: at.x.pt(), y: at.y.pt(),
   width: it.value.width, height: it.value.height)
}))<blanks>]
```

Then, on the intermediate `.typ` that `build_contract_pdf.py` writes, with the same
`--root` and `--font-path` as the compile:

```
typst query --root <workdir> --font-path <workdir>/fonts --ignore-system-fonts \
  <workdir>/lettera-di-incarico.typ "<blanks>" --field value --one --format json
```

The wrapping box changes nothing on the page: the word boxes of `pdftotext -bbox` are
identical with and without it. For the letter, filled from `incarico.esempio.json`
without `data-firma` so that the date is a blank:

| Blank | Page | x, y, w, h (pt) | Documenso % (X, Y, width, height) |
|---|---|---|---|
| firma professionista | 2 | 303.638, 707.281, 155.906, 22.660 | 51.008, 84.011, 26.19, 2.692 |
| data firma | 2 | 165.811, 647.241, 85.039, 6.660 | 27.855, 76.879, 14.286, 0.791 |

The conversion is the plan's, with the page as `pdfinfo` reports it, 595.276 × 841.89
pt: `positionX = x / 595.276 * 100`, `positionY = y / 841.89 * 100`,
`width = w / 595.276 * 100`, `height = h / 841.89 * 100`, measured from the top left.
In the signed PDF the typed signature sits on the «firma professionista» rule, scaled to
the box's height and centred in it, and «23/09/2026» sits on the «data firma» rule. The
grey labels of both blanks stay visible under the signature and the date.

## 10. Beyond the brief

- **One-time code** (spec § 11). Recipient `actionAuth` (`TWO_FACTOR_AUTH`, `PASSWORD`,
  `PASSKEY`) is refused on this edition: create answers 401 «You do not have permission
  to set the action auth», gated by the organisation claim `cfr21`, which a self-hosted
  organisation does not have (its claim flags are `{}`). `accessAuth: ["ACCOUNT"]` is
  accepted; by its name it wants the signer signed in to a Documenso account (not tried
  in the browser). There is no mailed one-time code
  in 2.18.0. So no stronger signature for the onerous clauses without a paid plan.
- **Reminders** are gated the same way (`signingReminders` claim), as the spec says.

## 11. What phases 3 and 4 must do differently from the spec

1. **One Documenso user per environment** (spec § 7), not only one team: production and
   preview each get their own user and organisation, and each creates its own team's
   token. A token of a user who owns envelopes in both teams can read and cancel either,
   and events can reach the other environment's webhook (§ 8).
2. **The webhook's idempotency must hold under concurrency** (spec § 6). Retries come at
   once and a slow answer produces a second delivery while the first runs: claim the
   transition in the database (for example an update from `inviato` to `firmato` that
   only one request can win, or a row lock) before downloading and mailing, and keep the
   handler well under 10 seconds or acknowledge before the slow work. An event for an
   unknown `envelopeId`, or for a document already `annullato`, is answered 200 and
   ignored.
3. **Lost events stay lost.** Four attempts in 160 ms, then nothing: a hub restarting at
   that moment never hears of the signature. «Aggiorna stato» is the only net in the
   spec; phase 3 should say who presses it, or add a periodic check of the documents
   still `inviato`.
4. **The hub refuses a missing or empty `X-Documenso-Secret`**, since Documenso sends the
   header empty when a webhook has no secret, and the secret travels in clear: the
   webhook URL must be HTTPS or stay on the host's network.
5. **The webhook URL and the SSRF guard** (spec § 7). If Documenso calls the hub by its
   compose service name, that host goes into `NEXT_PRIVATE_WEBHOOK_SSRF_BYPASS_HOSTS`;
   otherwise the hub's public URL works without it.
6. **Create with the full `meta`** of § 4: `distributionMethod: NONE`, every
   `emailSettings` flag false (or the Documenso account receives «Signing Complete!» for
   every contract), `dateFormat: dd/MM/yyyy`, `timezone: Europe/Rome`, and
   `envelopeExpirationPeriod: {"disabled": true}` unless the hub wants links to die after
   90 days and to handle `RECIPIENT_EXPIRED`. `externalId` carries the hub's document id
   and comes back in every webhook.
7. **Store the envelope item id**, read once from `GET /envelope/{id}` after the create,
   since the create answers only the envelope id and the download is by item. Upload with
   the file name the signed copy should carry.
8. **The date of signature is the recipient's `signedAt`**, never `completedAt` alone,
   which a cancel also sets.
9. **The signature positions come from a placed tag and a document query** (§ 9), not
   from `here()` next to the blank. And the template should stop printing the grey label
   inside a blank that Documenso fills (`firma-professionista`, `data-firma`) when the
   document goes out for signature: it shows through the signature and the date.
10. **A cancelled document needs the hub to say so.** Documenso's page still opens and
    only fails at the click with a generic error, so the member area and any mail must
    show `annullato` themselves.
11. **The signed copy has one more page**, Documenso's «Signing Certificate» in English.
    The mail and the member area can say so; nothing to change in the texts.
12. **Tokens and webhooks are made by hand** in the UI for each environment (no API),
    and a token expires in 3 months unless «Never» is chosen: phase 4's deploy notes
    record which, and when to rotate.
13. **Phase 4 still has to confirm** Resend as Documenso's SMTP transport and
    `NEXT_PUBLIC_DISABLE_SIGNUP=true`, which this probe did not run, and budget about
    630 MiB of memory for the container. The data to back up is Documenso's Postgres:
    with the default database transport the PDFs live there.
