# Imagen del frontend Next.js de HJ Campaigns
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# API_INTERNAL_URL es la URL interna que usan los rewrites en build/runtime
ARG API_INTERNAL_URL=http://api:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10001 hj && adduser --system --uid 10001 -G hj hj

COPY --from=builder /app/public ./public
COPY --from=builder --chown=hj:hj /app/.next/standalone ./
COPY --from=builder --chown=hj:hj /app/.next/static ./.next/static

USER hj
EXPOSE 3000

# Healthcheck con Node estándar (sin depender de wget/curl de la imagen base)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:3000/api/v1/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
