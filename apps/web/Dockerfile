FROM node:22-bookworm-slim AS build
WORKDIR /project
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml tsconfig.base.json ./
COPY packages/shared ./packages/shared
COPY apps/web ./apps/web
RUN pnpm install --filter @superapp/web... --frozen-lockfile
RUN pnpm --filter @superapp/web build

FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build --chown=node:node /project/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /project/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /project/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node","apps/web/server.js"]
