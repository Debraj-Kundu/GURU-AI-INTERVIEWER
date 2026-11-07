# syntax=docker/dockerfile:1
# Next.js web app for deploy/docker-compose.yml (production build). Build context: web/
FROM node:24.14.0-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
# NEXT_PUBLIC_* values are baked in at build time; the browser calls the API on the host's port.
ARG NEXT_PUBLIC_API_URL=http://localhost:8080
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build
CMD ["npm", "start"]
