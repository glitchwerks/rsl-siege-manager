# Build stage
FROM node:20-alpine AS build

# Bake the full version string (e.g. 1.0.1+42.abc1234) into the bundle at
# build time. Vite exposes any VITE_* env var to the app via import.meta.env,
# so SystemPage.tsx can read it as import.meta.env.VITE_APP_VERSION.
# Defaults to "unknown" for local builds that don't pass --build-arg.
ARG VITE_APP_VERSION=unknown
ENV VITE_APP_VERSION=$VITE_APP_VERSION
ARG GIT_SHA=unknown

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

# CHANGELOG.md must be staged from the repo root into this build context before
# `docker build` runs. In CI this is done by the "Stage CHANGELOG.md into frontend
# build context" step in .github/workflows/deploy.yml (see frontend/src/build/changelog-plugin.ts).
COPY . .
RUN npm run build
RUN printf '{"frontend_version":"%s","git_sha":"%s"}\n' \
    "$VITE_APP_VERSION" "$GIT_SHA" > dist/version.json

# Serve stage
FROM nginx:alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/nginx.conf.template

EXPOSE 80

# We run envsubst ourselves (rather than dropping the template into
# /etc/nginx/templates/) so we can restrict substitution to $API_UPSTREAM only.
# The nginx image's built-in envsubst has no variable filter, which means it
# would also replace nginx built-in variables like $uri, $proxy_host, and
# $remote_addr — silently corrupting the config and breaking reverse-proxy.
CMD ["/bin/sh", "-c", "envsubst '$API_UPSTREAM' < /etc/nginx/nginx.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
