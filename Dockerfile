FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# package-lock.json in older checkouts may not yet include BFF runtime deps.
# npm install refreshes the dependency tree so the image remains buildable until
# the lockfile is regenerated and committed by maintainers.
RUN npm install

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build:bff

FROM node:22-alpine AS runner
WORKDIR /app
RUN addgroup --system --gid 1001 bff && adduser --system --uid 1001 bff
ENV NODE_ENV=production
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/apps/marketing ./apps/marketing
USER bff
EXPOSE 3001
CMD ["node", "--experimental-sqlite", "dist/server.js"]
