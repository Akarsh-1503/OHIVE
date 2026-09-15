# Driftless web (`slam-web`).
#
#   docker build -t slam-web assignment2/frontend/
#   docker run -p 3000:3000 slam-web
#
# `NEXT_PUBLIC_*` values are inlined into the client bundle at build time, so they are build
# args rather than runtime env. The default API base is relative, which is what keeps the
# image independent of the hostname it ends up behind.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci


FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG NEXT_PUBLIC_API_BASE=/api/v1
ARG NEXT_PUBLIC_MOCK=0
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_MOCK=$NEXT_PUBLIC_MOCK

RUN npm run build


FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup -S -g 10001 driftless \
 && adduser -S -u 10001 -G driftless -h /app driftless

# `output: 'standalone'` emits a self-contained server plus only the node_modules it traced.
# Static assets are deliberately not part of that tree and have to be copied alongside it.
COPY --from=builder --chown=driftless:driftless /app/.next/standalone ./
COPY --from=builder --chown=driftless:driftless /app/.next/static ./.next/static

USER driftless
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
