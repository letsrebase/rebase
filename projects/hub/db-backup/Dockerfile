# The database backup services' image (REB-715, REB-717), one for Documenso's database and
# the hub's: the databases' own image, so that `pg_dump` is the same major version as the
# servers it dumps, plus the two tools that image does not carry and the scripts need,
# `openssl` for the CMS envelope and a `curl` new enough to sign S3 requests
# (`--aws-sigv4`, 7.75). `test_documenso_compose.py` fails when the tag here and the tag of
# `documenso-db` in ../docker-compose.documenso.yml or of `db` in ../docker-compose.yml
# drift: a `pg_dump` older than its server refuses to run, and a newer one writes an
# archive an older `pg_restore` cannot read.
#
# The digest is what the origin's `postgres:17-alpine` is today, and it has to be there.
# The deploy runs `docker compose build --pull`, which fetches a build's base image; by
# tag alone that would move the host's `postgres:17-alpine` to whatever Docker Hub has
# built since 2026-08-13, and the next `up -d` would recreate every database container
# that names the tag (the hub's, Documenso's and the CRM's) on a Postgres nobody chose for
# them. Pulled by digest, nothing moves. Bumping it is a
# change to this line, made on purpose, in a pull request.
#
# The scripts are copied in rather than mounted, so a deploy that changes one is a
# changed image and compose recreates the container; a bind-mounted file would be a
# change compose cannot see, and the loop would go on running the old script.
FROM postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73

RUN apk add --no-cache curl openssl

COPY scripts/ /scripts/
