# AGENTS.md: working on Vaultwarden

The root [`AGENTS.md`](../../AGENTS.md) covers the monorepo. This file is only about this
project. [`README.md`](README.md) is how it runs and how it is provisioned; read it first.

## What it is

The rebase members' password manager (REB-644, REB-648): the upstream `vaultwarden/server`
image, pinned by tag and digest, one SQLite file on the production host, a vhost, an
encrypted daily backup to R2 and the runbooks. **There is no application code here**, only
configuration and bash, and that is the point: the less of it, the less to audit in a
system that holds other people's secrets.

## Rules that are easy to break

- **The signup door is shut in the file, not in `.env`.** `SIGNUPS_ALLOWED` is a literal
  `'false'` in `docker-compose.yml`. Never turn it into a variable; the drill's seed
  phase opens it with an override file (`tests/seed.yml`) for exactly that reason.
- **The admin page is off by default and stays that way.** `ADMIN_TOKEN` is the hash or
  nothing, never a plain token. A setting saved from the page lands in `config.json` and
  overrides the compose file silently: configuration changes are a PR here, not a click.
- **The image changes with a tag and a digest together, in a PR, read against the
  release notes.** Never `latest`, never a tag alone. 1.37.0 is the floor
  (DECISIONS.md, 2026-10-05). `docker buildx imagetools inspect vaultwarden/server:<tag>`
  prints the index digest to pin.
- **Published on 127.0.0.1 only**, port 8091 (`docs/adding-a-project.md` §7 allocates it).
- **Nothing is released by hand**, and that includes the admin page's opening and closing
  and the first deploy: a `.env` line, then `gh run rerun` of the deploy run
  (`README.md`). A secret the compose file requires goes in `env-secrets` of
  `deploy-vaultwarden.yml` and in the Environment, never in a hand-edited `.env`.
- **The backup's private key never touches a server.** The host holds the certificate
  only. A change that puts the key, or a passphrase that decrypts, on the host, in the
  bucket or in a log defeats the backup's whole threat model.
- **The restore is the proof.** A change to `scripts/` or to the image is done when
  `tests/backup-restore.sh` passes, and the real-bucket drill of `docs/restore.md` is run
  after anything that changes the bundle's shape.
- **The member guide is Italian**, because it is read by members; everything else here is
  English. When the clients or the policies change, change the guide in the same PR.

## Changing the backup bundle

`scripts/backup.sh` and `scripts/restore.sh` are a pair: the bundle's layout (`db.sqlite3`,
`attachments/`, `sends/`, `config.json`, `rsa_key*`, `MANIFEST`) is the contract between
them, and `docs/restore.md` documents it. Old objects stay in the bucket for ninety days
under the old layout, so a new `restore.sh` has to open the layout of the last three months
or say clearly that it cannot.

## Verifying a change

From the repository root (Docker, node 22, openssl, shellcheck):

```
shellcheck projects/vaultwarden/scripts/*.sh projects/vaultwarden/tests/*.sh
projects/vaultwarden/tests/backup-restore.sh
```

The second starts the real compose file on throwaway servers and takes about two minutes.
`ci.yml`'s `vaultwarden` job and preflight's `vaultwarden-drill` run the same thing.
