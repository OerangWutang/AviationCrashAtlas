FROM node:22-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npx tsc -b

FROM node:22-alpine AS runner
WORKDIR /app
RUN addgroup --system --gid 1001 bff && adduser --system --uid 1001 bff
COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
USER bff
EXPOSE 3001
ENV NODE_ENV=production
CMD ["node", "--experimental-sqlite", "dist/server.js"]