# The bot for a server (Railway, any Docker host). See deploy/README.md.

# 1. The dashboard (a static build the bot serves).
FROM node:22-slim AS dashboard
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# 2. The bot.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install .
COPY config/ config/
COPY deploy/start.sh deploy/start.sh
COPY --from=dashboard /dashboard/dist dashboard/dist
RUN chmod +x deploy/start.sh
CMD ["deploy/start.sh"]
