# Career Quest — backend для HackAlem AI / Halyk Bank

Один FastAPI backend, Python 3.12, Pydantic v2, постоянная SQLite и простые версионированные SQL-миграции. Один процесс и одна реплика для первого демо. Нет отдельного AI-сервера, внешней авторизации, Redis или очередей.

Алихан отвечает за backend, данные, API, доступ, интеграцию и деплой. Олег — за `backend/app/ai/` и собственные тесты; Батыр — за `frontend/`. Единственный Python-контракт: [backend/app/contracts.py](backend/app/contracts.py). Старое распределение/предложение отдельного HTTP AI-сервера заменено этим соглашением.

## Что работает

| Маршруты | Состояние |
| --- | --- |
| `GET /api/health` | Liveness процесса; capabilities AI и датасета отдельно |
| `GET /api/health/ready` | БД, таблицы и версии/checksum миграций; 503 при проблеме |
| `GET /api/version` | Версия API `1.0.0`, SHA коммита или `unknown` |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/me` | Demo-авторизация с серверными учетными записями |
| Все предметные маршруты | Защита доступа и схемы готовы; логика **planned / 501** |

`/docs` и `/openapi.json` доступны после запуска; экспорт — [contracts/openapi.json](contracts/openapi.json). В OpenAPI операции имеют `x-implementation-status` и теги `implemented`/`planned`. Preview — дополнительная функция команды. Никакой успешный импорт, выдача рекомендаций или завершение активности пока не имитируются.

## Локальный запуск

Нужен Python 3.12. Глобальные установки не требуются:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
cp .env.example .env
scripts/dev
```

Если Python имеет другой путь, используйте его вместо `python3.12`. На проверенной машине найден Python 3.12.14 в bundled runtime Codex. `scripts/dev` запускает один Uvicorn worker на `127.0.0.1:8000`, без reload, доверия forwarded-заголовкам и access-логов с идентификаторами профилей.

Миграции применяются автоматически при старте и не сбрасывают данные. Их можно запустить отдельно:

```sh
.venv/bin/python -m backend.app.cli migrate
scripts/test -q
scripts/export-openapi
.venv/bin/ruff check backend
```

Runtime-зависимости и их транзитивные версии закреплены в `requirements.lock`, тестовые — в `requirements-dev.lock`. `pyproject.toml` содержит совпадающие прямые версии. Настройки читаются из environment; команды `scripts/dev` и CLI дополнительно читают `.env` без выполнения shell-кода. Environment имеет приоритет. При прямом запуске Uvicorn передайте environment самостоятельно.

## Учетные записи

Публичной регистрации и паролей по умолчанию нет. Оператор задает роль и связь с сотрудником на сервере; пароль вводится скрыто:

```sh
.venv/bin/python -m backend.app.cli create-account --username demo-employee --role employee --employee-id SYNTH_EMP_001 --synthetic
.venv/bin/python -m backend.app.cli create-account --username demo-hr --role hr
```

`--synthetic` создает только явно синтетический профиль, не данные организаторов. Без этого флага employee должен уже существовать в БД. Повторное создание не меняет существующий пароль и не перезаписывает данные. Пароль — 12–256 символов; хранится PBKDF2-SHA256 с индивидуальной случайной солью и 600000 итерациями. Сессия и CSRF хранятся в БД как SHA256-хеши случайных токенов. Реальные профили в этот каркас не загружаются.

Cookie `cq_session`: `HttpOnly`, `SameSite=Lax`, `Path=/api`, срок по умолчанию 3600 секунд; `Secure` автоматически обязателен при `APP_ENV=staging`. Login требует точный разрешенный `Origin`; все остальные изменяющие запросы также требуют `X-CSRF-Token` из login. `/api/me` возвращает только личность, без CSRF. Logout отзывает серверную сессию и удаляет cookie. Ограничение входа: 5 неверных попыток для username либо 20 для IP, блокировка 15 минут, состояние сохраняется при рестарте.

Браузерный frontend на `http://localhost:5173` должен обращаться к `http://localhost:8000` с `credentials: "include"`; не смешивайте `localhost` и `127.0.0.1` из-за SameSite. Для HTTPS-staging используйте один сайт/обратный прокси; банковский SSO не реализован. Подробные fetch-примеры: [docs/HANDOFF.md](docs/HANDOFF.md).

## Конфигурация и хранение

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `APP_ENV` | `development` | `development`, `test`, `staging` |
| `DATABASE_PATH` | `var/career_quest.sqlite3` | Постоянный SQLite-файл |
| `ALLOWED_ORIGINS` | localhost frontend + локальный API | JSON-массив точных origins, без wildcard |
| `SESSION_TTL_SECONDS` | `3600` | От 60 до 86400 секунд |
| `COOKIE_SECURE` | автоматически для staging | В staging false запрещен |
| `SQLITE_WAL` | `false` | WAL только при `SQLITE_LOCAL_DISK=true` |
| `SQLITE_LOCAL_DISK` | `false` | Явное подтверждение локального диска одного хоста |
| `AI_ENABLED` | `false` | По умолчанию адаптер отключен |
| `COMMIT_SHA` | `unknown` | `scripts/dev` подставляет текущий Git HEAD |

Каждое SQLite-соединение включает `foreign_keys=ON`, `busy_timeout=5000`. Миграции применяются транзакционно под `BEGIN IMMEDIATE`, их имена и SHA256 сохраняются в `schema_migrations`. Уже примененные SQL-файлы не редактировать: добавляйте следующий номер. Ошибка миграции оставляет liveness доступным, readiness возвращает 503. До подключения AI/датасета readiness может быть 200 с `ai.not_configured`, `engine=none`, `dataset.not_loaded`.

## Ошибки

Единый JSON: `{code, message, request_id, details}`; идентификатор совпадает с `X-Request-ID`. 401 — нет/истекла сессия; 403 — чужой доступ, Origin или CSRF; 404 — неизвестный разрешенный профиль/маршрут; 409 — контракт будущего конфликта версий/идемпотентности; 422 — неверный запрос; 429 — перебор; 501 — planned; 503 — недоступно хранилище/миграции. Доменный 409 не выдается фиктивно до реализации логики.

В details валидации попадают только пути полей и типы ошибок. Пароли, токены, значения входных профилей и текст неожиданных исключений не выводятся. Политика не отправляет реальные данные внешним сервисам. Общая ошибка 500 скрывает внутренние сведения.

## Docker Compose

```sh
COMMIT_SHA=$(git rev-parse HEAD) docker compose -f compose.yaml up --build
docker compose -f compose.yaml down
```

Только сервис `backend`, один worker, непривилегированный UID/GID 10001, порт внутри 8000, публикация `127.0.0.1:8000:8000`. Именованный volume `sqlite_data` сохраняет БД между запусками. Не используйте `down -v` для обычной остановки. SQLite volume должен лежать на локальном диске одного хоста; WAL в Compose выключен.

Образ — официальный `python:3.12.14-slim-bookworm`. Docker context построен по allowlist кода и миграций, без `.env`, ключей, БД, датасета, тестовых аккаунтов и архивного черновика. **Docker отсутствовал на проверенной машине: container build и Compose execution не проверены.** Публичного деплоя нет.

## Структура и интеграция

```text
backend/app/       конфигурация, SQLite, auth, routes, contracts, ai_adapter
backend/migrations/   версионированные SQL
backend/tests/     инфраструктура, доступ, контракты, AI boundary
contracts/         API.md, AI_CONTRACT.md, openapi.json, examples/*.synthetic.json
docs/              SOURCES.md, HANDOFF.md, WORK_PLAN.md, VERIFICATION.md
scripts/           dev, test, export-openapi, smoke
```

Олег экспортирует `async recommend(context: RecommendationContext) -> RecommendationResult` из `backend.app.ai`. Адаптер вне AI-модуля допускает отсутствие реализации, проверяет результат, кандидатов и evidence, ограничивает время. Арифметика, доступность событий, обогащение HTTP-ответа и запись — backend. При отключении результат `not_configured/none`; это не `no_candidates`.

Реальный стартовый README и структура исходных файлов проверены локально; содержимое профилей не включено в Git. Статус первичного ТЗ и прежнего плана: [docs/SOURCES.md](docs/SOURCES.md). Дальнейшие шаги — [docs/WORK_PLAN.md](docs/WORK_PLAN.md).

После разрешенной координации неизмененный параллельный черновик сохранен локально в `.local-legacy/foundation-draft/`, включая прежний README. Он не входит в приложение, тестовый набор, образ и checkpoint: активный backend и контракт единственные.
