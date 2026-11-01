# syntax=docker/dockerfile:1.4
#
# Production build — as opposed to Dockerfile.dev, which runs the Vite dev
# server (unbundled ES modules, one HTTP request per source file, no
# minification). That's fine for live-editing against a mounted source tree,
# but this deployment bakes the source into the image either way (no volume
# mount — see frontend.yaml), so every "code changed" cycle already means
# rebuild-and-redeploy, never live HMR against a running pod. Running the dev
# server here bought nothing but a much heavier page load: hundreds of
# unbundled module requests instead of a handful of minified, code-split,
# hashed chunks.
FROM node:20-alpine AS build
WORKDIR /app

RUN npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000

COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:20-alpine
WORKDIR /app

RUN npm config set fetch-retries 5 \
 && npm config set fetch-retry-mintimeout 20000 \
 && npm config set fetch-retry-maxtimeout 120000

# `vite preview` only needs `vite` itself (to serve the already-built dist/)
# plus vite.config.ts for its preview/build options — not the rest of the
# dependency tree or the source. Still installs from the full lockfile
# (--omit=dev drops vitest etc.) rather than hand-picking one package, so this
# stays correct if the preview server's own needs ever change.
COPY package*.json ./
RUN --mount=type=cache,target=/root/.npm npm install --no-audit --no-fund --omit=dev
COPY vite.config.ts ./
COPY scripts/write-runtime-config.mjs ./scripts/
COPY --from=build /app/dist ./dist

EXPOSE 8280
# The install's settings are written at start, not baked in (src/lib/runtimeConfig.ts): the pod's
# root filesystem is read-only, so production mounts an emptyDir at dist/runtime.
CMD ["sh", "-c", "node scripts/write-runtime-config.mjs && exec npm run preview -- --host 0.0.0.0 --port 8280"]
