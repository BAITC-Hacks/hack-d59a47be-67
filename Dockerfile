FROM node:24-bookworm-slim AS frontend-build

WORKDIR /build/frontend
RUN npm install --global pnpm@11.25.0
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN pnpm build

FROM python:3.12.14-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/career_quest.sqlite3 \
    STATIC_FILES_PATH=/app/frontend/dist \
    PORT=8000
WORKDIR /app
# The runtime lock includes the optional AI transport; AI itself remains disabled by default.
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home app \
    && mkdir -p /data && chown app:app /data
COPY --chown=app:app backend/__init__.py backend/__init__.py
COPY --chown=app:app backend/app backend/app
COPY --chown=app:app backend/migrations backend/migrations
COPY --from=frontend-build --chown=app:app /build/frontend/dist frontend/dist
ARG COMMIT_SHA=unknown
ENV COMMIT_SHA=$COMMIT_SHA
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/health/ready', timeout=2)"
CMD ["python", "-m", "backend.app.serve"]
