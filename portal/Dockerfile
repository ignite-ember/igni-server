# Stage 1: Build the React app
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The check reads what was just built: no CDN host anywhere in dist/. An
# air-gapped deployment cannot reach one, and the URL that broke the
# content editor (B34) lived in a dependency, where no source scan sees it.
RUN npm run build && node scripts/check-no-cdn.mjs

# Stage 2: Serve with nginx
#
# ``nginx-unprivileged`` rather than ``nginx``: the stock image starts its
# master process as root, which the GCP Marketplace container requirements
# forbid and every image scanner an AWS listing passes through flags.
# ``docker run --rm <image> id -u`` printed 0.
#
# This variant runs as uid 101 throughout and has ``/var/cache/nginx``,
# ``/var/run`` and the config directories already owned by it — which is
# the actual work, and why swapping the base image beats adding a `USER`
# line to the stock one.
#
# The port needs no change: ``nginx.conf`` already listens on 3000, and
# 3000 is above 1024 so an unprivileged process can bind it. The stock
# image's default of 80 could not have been.
FROM nginxinc/nginx-unprivileged:alpine
# The base image is rebuilt on nginx's schedule, not Alpine's, so its
# packages trail Alpine's security fixes: the scheduled scan found
# brotli, curl and expat each a patch release behind a fixed one. One
# `apk upgrade` as root brings them level, and the user goes straight
# back to the base image's own 101.
USER root
RUN apk upgrade --no-cache
USER 101
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
# Runs before nginx starts and writes the DNS resolver the /v1 proxy
# needs. See the script for why this is not a line in nginx.conf.
COPY docker-entrypoint.d/19-igni-resolver.sh /docker-entrypoint.d/19-igni-resolver.sh
# The compose service name of the API, used by nginx.conf's /v1 proxy.
#
# Was `ember_api:7777`, which is a name from before the rename — no
# compose file has defined a service by that name for some time. Two
# consequences, and the second only appeared once nginx.conf grew a
# proxy block: the portal's /v1 proxy resolved to nothing in local
# development, and then the container stopped starting at all, because
# nginx resolves proxy_pass upstreams once at startup and exits when one
# does not resolve. Found by rebuilding the local portal for the first
# time in a while.
#
# Both compose files now set this explicitly, so the default is a
# fallback rather than the configuration. It still has to be right.
ENV API_UPSTREAM="igni_api:7777"

LABEL org.opencontainers.image.title="igni portal" \
      org.opencontainers.image.description="igni server — admin portal" \
      org.opencontainers.image.source="https://github.com/ignite-ember/igni-server" \
      # SPDX identifier, which is what a scanner and a marketplace review
      # read. `NOASSERTION` was the placeholder and it is the literal
      # string tooling uses for "nobody said", so it reported as an
      # unlicensed image rather than an unfinished label.
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.vendor="Ignite Ember"

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
