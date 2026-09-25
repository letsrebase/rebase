FROM node:22-bookworm-slim AS build

# Kept in step with `packageManager` in the repository root's package.json, for the
# same reason PigroCRM's images pin it: corepack cannot read that field before the
# file it is in has been copied, and installing with a different major than the one
# that wrote pnpm-lock.yaml is how a lockfile silently stops being frozen.
RUN corepack enable && corepack prepare pnpm@11.6.0 --activate
WORKDIR /app

# The build context is the repository root, not this directory (see `context:` in
# docker-compose.yml): the pnpm workspace root and the single lockfile live two levels
# above, and `shared/brand` is a workspace dependency of this project.
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY projects/website/package.json projects/website/
COPY shared/brand/package.json shared/brand/
RUN pnpm install --frozen-lockfile --filter website...

COPY shared/brand ./shared/brand
COPY projects/website ./projects/website
RUN pnpm --filter website build

FROM nginx:1.30-alpine
# Docker Hub rebuilds this tag only when nginx or its Alpine base moves, so a package
# Alpine fixes in between (libexpat 2.8.5, missing from the 2026-09-23 build) is
# installed here. The layer is cached like any other: it runs again when the deploy's
# `build --pull` brings a new digest of the tag, or on a cold build cache.
RUN apk upgrade --no-cache
COPY --from=build /app/projects/website/dist /usr/share/nginx/html
COPY projects/website/deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
