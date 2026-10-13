FROM node:22-alpine AS deps

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.3 --activate

COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

FROM node:22-alpine AS builder

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.3 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY . .

RUN pnpm build

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0

RUN corepack enable && corepack prepare pnpm@9.12.3 --activate \
 && addgroup -S app && adduser -S app -G app

COPY --from=builder /app/build ./build
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules

USER app

EXPOSE 3000

CMD ["node", "build"]