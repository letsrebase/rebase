FROM node:24-bookworm-slim AS build

# The build context is the repository root, not this directory (see `context:` in
# docker-compose.yml): the pnpm workspace root and the single lockfile live two levels
# above, and `shared/brand` is a workspace dependency of this project. `package.json` is
# copied before `corepack enable`, so corepack reads `packageManager` from it.
WORKDIR /app
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
RUN corepack enable
COPY projects/website/package.json projects/website/
COPY shared/brand/package.json shared/brand/
RUN --mount=type=cache,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir /pnpm/store --filter website...

COPY shared/brand ./shared/brand
COPY projects/website ./projects/website
RUN pnpm --filter website build

FROM nginxinc/nginx-unprivileged:1.31-alpine
# The unprivileged build of the same nginx (user `nginx`, port 8080): the container has no
# root process. `apk upgrade` as root for that one layer, for the reason given in
# projects/pigrocrm/Dockerfile.web.
USER root
RUN apk upgrade --no-cache
USER nginx
COPY --from=build /app/projects/website/dist /usr/share/nginx/html
COPY projects/website/deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
