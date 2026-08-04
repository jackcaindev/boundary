FROM node:24.15.0-alpine3.23 AS build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.29.6-alpine3.23
COPY docker/frontend-proxy.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 5173

HEALTHCHECK --interval=5s --timeout=3s --retries=12 \
  CMD wget --quiet --tries=1 --timeout=2 --spider http://127.0.0.1:5173/healthz || exit 1
