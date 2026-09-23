# Приёмка предметного backend — 2026-09-23

Командная ветка `origin/codex/architecture-foundation` получена по URL пользователя. Её история присоединена локально; исходное ТЗ, подготовка/аудит данных, frontend и документы сохранены. Работа/PR — отдельная `codex/backend-foundation`; main/base не обновлялись напрямую.

Ветка опубликована обычным push: [PR #2](https://github.com/BAITC-Hacks/hack-d59a47be-67/pull/2) в `codex/architecture-foundation`; GitHub подтвердил `mergeable=true`, `merged=false`. Protected-path diff для frontend/, backend/app/ai/ и исходного ТЗ пустой.

Локальный API запущен на `http://127.0.0.1:8000`: health/readiness/version/OpenAPI — 200, анонимный me — 401. Кит импортирован в игнорируемую локальную БД: 3075 записей, revision1; capability dataset=loaded, AI=not_configured/none. Учётки создаются оператором по HANDOFF, паролей по умолчанию нет. Это локальный backend, публичный деплой не выполнялся.

Фактический финальный локальный `make check`:

- `.venv/bin/python -m unittest discover -s tests -v` — **21 passed**, исходные тесты команды.
- `.venv/bin/python -m pytest backend/tests -q` — **234 passed** (финальный прогон с `PYTHONTZPATH=""`, включая проверку замены каталога).
- `.venv/bin/python scripts/check_contracts.py` — **9 checks passed**; два JSON Schema генерируются из единственных Pydantic-моделей, live AI не вызывается.
- `.venv/bin/python -m ruff check backend scripts/smoke.py scripts/check_contracts.py` — all checks passed.
- `scripts/export-openapi` — экспорт из приложения; предметные endpoints implemented, cookie/CSRF/Origin/Idempotency-Key описаны.
- `scripts/smoke` — два реальных TCP-запуска с временным синтетическим импортом: login, profile, completion, потеря ответа, restart, точный idempotency replay, чужой профиль403, logout/revocation.

Регрессии проверяют: replay строго review<completed<=cutoff, missing=0/unknown skill error; caps никогда не уменьшают навык; точный временной порядок новых completed_at; повторные mandatory; EV_036 с двумя разными сессиями; текущее role/grade/prerequisites/calendar/history; no_target; preview без записи; samekey/body до revision, другое тело409 и запрет второй награды; rollback при внутренней SQL-ошибке; совместный import нового профиля/истории с ошибкой середины партии; source baseline после local goal/completion и no-op reimport; saved full recommendation cards после restart; stale после каждого изменения; write во время await AI доказывает отсутствие удерживаемой DB-транзакции, ответ старой revision отклоняется409; минимум три категории evidence, свободный текст модели не публикуется.

Полный реальный кит проверен локально в отдельной временной SQLite, без копирования исходных данных в Git/образ или их вывода: 60 skills,32 role_profiles,40 events,200 employees,2743 history. Импорт/повторный запуск/no-op reimport сохраняют revision=1; повтор добавляет0 записей. Все200 HTTP profile projections и каталог проходят Pydantic. Доменное распределение совпало с исходным командным аудитом:159 actionable,10 no_target,31 no_candidates. Совместный проход профилей/HR/reimport занял около0.503 s; это локальная проверка функций, не измерение p95 API и не оценка LLM.

Закреплён tzdata==2026.4; Asia/Almaty проверена без системной базы зон через `PYTHONTZPATH=""`. Замена каталога явно предупреждает о пересчёте и отдельно проверена тестом.

Остаётся одно upstream предупреждение Starlette о deprecated httpx TestClient. GitHub Actions заблокирован биллингом организации; CI не объявлен зелёным. Docker отсутствует, container build и Compose не проверены. AI-код/провайдер/frontend не реализовывались этой веткой. HR endpoint пока выдаёт базовые counts; расширенная аналитика gaps/участия и explicit replace существующих employee_id/record_id — отдельный следующий срез.

Ниже сохранён исторический отчёт предыдущего инфраструктурного checkpoint; его planned/501/missing-origin сведения относятся только к тому этапу.

---

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
