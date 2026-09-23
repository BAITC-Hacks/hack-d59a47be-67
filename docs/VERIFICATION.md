# Фактические проверки — 2026-09-23

Проверялся локальный каркас Алихана. Каталоги `backend/app/ai/` и `frontend/` не создавались и не изменялись. Исходный параллельный черновик сохранён в игнорируемой `.local-legacy/foundation-draft/`; он не включён в проверяемое приложение и checkpoint.

## Окружение

- macOS, arm64; системный `python3 --version` — 3.9.6.
- Для backend использован найденный Python **3.12.14**, отдельный `.venv`.
- `docker --version`, `docker compose version` — command not found. Сборка образа и запуск Compose **не проверены**.
- `df -h .`: в начале около 2.8 GiB свободно, перед завершением около 3.0 GiB. Очистка компьютера/глобальные установки не выполнялись.
- Текущая папка изначально имела unborn `main`, без коммитов, origin и AGENTS.md. Нового репозитория не создавали, origin не назначали/не заменяли. Создана локальная ветка `codex/backend-foundation`. Подтверждение URL выданного командного remote остаётся за пользователем.

## Команды и результаты

```sh
.venv/bin/python -m venv /private/tmp/career-quest-verify-venv
/private/tmp/career-quest-verify-venv/bin/python -m pip install --no-cache-dir -r requirements-dev.lock
PYTHON=/private/tmp/career-quest-verify-venv/bin/python scripts/test -q
```

Lock-файлы установлены в чистый venv. **112 passed** (4.70 s). Одно предупреждение зависимости Starlette: TestClient с httpx deprecated в пользу httpx2; текущие тесты проходят. Не скрывали warning и не объявляли его ошибкой приложения.

Покрытие сценариев: миграции, checksum и rollback; FK/busy_timeout/WAL; повторный запуск; безопасная конфигурация staging; hash паролей/сессий; login/logout/expiry; Origin/CSRF; запрет чужого и HR-доступа; сохранение rate limit и сессии после рестарта; перебор разных usernames; все 9 planned маршрутов 401/501; единые безопасные 401/403/404/409/422/429/500/503; CORS аварийных ответов; OpenAPI security/planned; валидация синтетических контрактов; AI not_configured, output validation, timeout/cancellation. 409 проверяется тестовым обработчиком, предметная конфликтная логика ещё не реализована.

```sh
.venv/bin/ruff check backend scripts/smoke.py
.venv/bin/ruff format --check backend scripts/smoke.py
/private/tmp/career-quest-verify-venv/bin/python -m pip check
scripts/export-openapi
git diff --check
```

Ruff: all checks passed, 18 файлов отформатированы. Pip: no broken requirements found. OpenAPI экспортирован из приложения в `contracts/openapi.json`; planned операции не объявляют 200. Git whitespace check пройден.

```sh
PYTHON=/private/tmp/career-quest-verify-venv/bin/python scripts/smoke
```

Реальный TCP smoke на отдельном loopback-порту и временной SQLite с синтетическими профилями: два последовательных запуска Uvicorn; health/readiness/version, login, 501 своего профиля, 403 чужого/HR, отклонение logout без CSRF, сохранение сессии через рестарт, успешный logout, затем 401. Профили и единственная версия миграции сохранились. Пароли/токены не печатались.

```sh
scripts/dev
```

Отдельно проверен локальный `http://127.0.0.1:8000`: health/readiness/version/docs/OpenAPI — 200, `/api/me` без сессии — 401. Readiness честно возвращает `ai={status:not_configured,engine:none}`, `dataset={status:not_loaded,version:null}`. До первого checkpoint SHA был `unknown`; `scripts/dev` читает HEAD при запуске после коммита.

Sandbox первоначально запретил скачивание PyPI, запись Git refs и bind loopback. Эти операции выполнены через разрешённые целевые escalations; автоматических отклонений approval не было. Глобальные инструменты и чужие процессы не менялись.

## Ограничения

Доменная логика, импорт и AI-ядро не реализованы; импорт не объявлен совместимым только на основании прочитанной схемы. Нет публичного деплоя, банковского SSO и подтверждённой контейнерной сборки. Демо-учетки создаются оператором интерактивно; общий пароль не коммитился. Тестовые фикстуры не являются данными организаторов.
