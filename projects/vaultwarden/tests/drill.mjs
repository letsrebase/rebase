// A small Bitwarden client written with Node's own crypto, so the backup drill
// (backup-restore.sh) can put a real, client-side encrypted item into a server and read
// it back from another one, without a browser. Same scheme the official clients use:
// PBKDF2-SHA256 master key, HKDF-stretched, a random 64-byte user key wrapped with it,
// every field an `2.iv|data|mac` EncString (AES-256-CBC, HMAC-SHA256).
//
//   node drill.mjs register <base-url> <email> <password>
//   node drill.mjs create   <base-url> <email> <password> <item-name>
//   node drill.mjs read     <base-url> <email> <password>      # prints each item's name
//   node drill.mjs refuse   <base-url> <email>                 # exits 0 if signup is refused
import crypto from 'node:crypto';
import { promisify } from 'node:util';

const pbkdf2 = promisify(crypto.pbkdf2);
const ITERATIONS = 600000;

const b64 = (buf) => Buffer.from(buf).toString('base64');

function hkdfExpand(key, info) {
  return Buffer.from(crypto.hkdfSync('sha256', key, Buffer.alloc(0), info, 32));
}

function encrypt(plain, key) {
  const encKey = key.subarray(0, 32);
  const macKey = key.subarray(32, 64);
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv('aes-256-cbc', encKey, iv);
  const data = Buffer.concat([cipher.update(plain), cipher.final()]);
  const mac = crypto.createHmac('sha256', macKey).update(iv).update(data).digest();
  return `2.${b64(iv)}|${b64(data)}|${b64(mac)}`;
}

function decrypt(encString, key) {
  const [, rest] = encString.split('.');
  const [iv, data, mac] = rest.split('|').map((p) => Buffer.from(p, 'base64'));
  const expected = crypto.createHmac('sha256', key.subarray(32, 64)).update(iv).update(data).digest();
  if (!crypto.timingSafeEqual(expected, mac)) throw new Error('MAC mismatch');
  const decipher = crypto.createDecipheriv('aes-256-cbc', key.subarray(0, 32), iv);
  return Buffer.concat([decipher.update(data), decipher.final()]);
}

async function masterKeys(email, password) {
  const master = await pbkdf2(password, email.trim().toLowerCase(), ITERATIONS, 32, 'sha256');
  const hash = await pbkdf2(master, password, 1, 32, 'sha256');
  const stretched = Buffer.concat([hkdfExpand(master, 'enc'), hkdfExpand(master, 'mac')]);
  return { hash: b64(hash), stretched };
}

async function call(base, path, init = {}) {
  const res = await fetch(`${base}${path}`, init);
  const text = await res.text();
  return { status: res.status, text, json: () => JSON.parse(text) };
}

const json = (body, token) => ({
  method: 'POST',
  headers: { 'content-type': 'application/json', accept: 'application/json', ...(token && { authorization: `Bearer ${token}` }) },
  body: JSON.stringify(body),
});

async function login(base, email, password) {
  const { hash, stretched } = await masterKeys(email, password);
  const form = new URLSearchParams({
    grant_type: 'password',
    username: email,
    password: hash,
    scope: 'api offline_access',
    client_id: 'web',
    deviceType: '9',
    deviceIdentifier: crypto.randomUUID(),
    deviceName: 'backup-drill',
  });
  const res = await call(base, '/identity/connect/token', {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: form,
  });
  if (res.status !== 200) throw new Error(`login ${res.status}: ${res.text}`);
  const body = res.json();
  return { token: body.access_token, userKey: decrypt(body.Key, stretched) };
}

async function register(base, email, password) {
  const { hash, stretched } = await masterKeys(email, password);
  const userKey = crypto.randomBytes(64);
  const { publicKey, privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  const sent = await call(base, '/identity/accounts/register/send-verification-email', json({ email, name: 'drill' }));
  if (sent.status !== 200) throw new Error(`send-verification-email ${sent.status}: ${sent.text}`);
  const done = await call(
    base,
    '/identity/accounts/register/finish',
    json({
      email,
      emailVerificationToken: sent.json(),
      masterPasswordHash: hash,
      kdf: 0,
      kdfIterations: ITERATIONS,
      userSymmetricKey: encrypt(userKey, stretched),
      userAsymmetricKeys: {
        publicKey: b64(publicKey.export({ type: 'spki', format: 'der' })),
        encryptedPrivateKey: encrypt(privateKey.export({ type: 'pkcs8', format: 'der' }), userKey),
      },
    }),
  );
  if (done.status !== 200) throw new Error(`register/finish ${done.status}: ${done.text}`);
}

const [command, base, email, password, name] = process.argv.slice(2);

if (command === 'register') {
  await register(base, email, password);
  console.log(`registered ${email}`);
} else if (command === 'refuse') {
  // The same first call every client makes; a server with signups closed answers 400.
  const sent = await call(base, '/identity/accounts/register/send-verification-email', json({ email, name: 'nobody' }));
  console.log(`signup without an invitation: HTTP ${sent.status} ${sent.text.slice(0, 120)}`);
  // The refusal itself, not any error: a 404 or a validation failure proves nothing.
  process.exit(sent.status === 400 && sent.text.includes('Registration not allowed') ? 0 : 1);
} else if (command === 'create') {
  const { token, userKey } = await login(base, email, password);
  // The server insists the item says whose it is: `encryptedFor` is the account's id.
  const me = await call(base, '/api/accounts/profile', { headers: { authorization: `Bearer ${token}`, accept: 'application/json' } });
  if (me.status !== 200) throw new Error(`profile ${me.status}: ${me.text}`);
  const made = await call(
    base,
    '/api/ciphers',
    json(
      {
        type: 1,
        encryptedFor: me.json().id,
        name: encrypt(Buffer.from(name), userKey),
        notes: null,
        favorite: false,
        login: {
          username: encrypt(Buffer.from('drill-user'), userKey),
          password: encrypt(Buffer.from('drill-secret'), userKey),
          uris: [],
        },
      },
      token,
    ),
  );
  if (made.status !== 200) throw new Error(`create ${made.status}: ${made.text}`);
  console.log(`created item ${name}`);
} else if (command === 'read') {
  const { token, userKey } = await login(base, email, password);
  const sync = await call(base, '/api/sync', { headers: { authorization: `Bearer ${token}`, accept: 'application/json' } });
  if (sync.status !== 200) throw new Error(`sync ${sync.status}: ${sync.text}`);
  for (const cipher of sync.json().ciphers) {
    console.log(decrypt(cipher.name, userKey).toString());
  }
} else {
  console.error('usage: drill.mjs register|create|read|refuse <base-url> <email> [<password> [<item-name>]]');
  process.exit(2);
}
