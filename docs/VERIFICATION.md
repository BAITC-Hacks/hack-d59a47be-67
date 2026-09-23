# Проверка приложенного case_1 и локального запуска — 2026-09-23

Проверена текущая backend-ветка `codex/backend-ai-handoff`, runtime commit `3b85babc78e6c522b6c5d04d42e5a16a37966570`. Изменений приложения для этой проверки не потребовалось; ниже только фактические результаты. Код AI/frontend, исходные файлы и рабочие записи не менялись.

- В приложенном `case_1/career_quest_dataset/` присутствуют4 источника и3 README. SHA256 источников совпадают с `docs/data-audit.md`.200 сотрудников,40 мероприятий,60 навыков,32 целевых role/grade,2743 записи истории; дата среза2026-10-01. Отдельного ТЗ в папке нет; сохранённый оригинал — `docs/requirements/original-spec.txt`.
- `scripts.audit_dataset.audit()` —64 508 проверок/0 нарушений. `domain.replay()` совпал с независимым аудитом у200/200;159 actionable/10 no_target/31 no_candidates.544 повторных mandatory назначения сохраняются. Отсутствие полезных кандидатов у31 профиля связано с правилами допуска/покрытием каталога, а не отсутствием данных.
- Установленная `var/career_quest.sqlite3` проверена через read-only SQLite: source_json, history и оба каталога полностью равны нормализованным исходникам;4 migration checksums PASS; `quick_check=ok`, FK violations0; revision1.200 профилей/2743 history/0 accounts. БД и данные исключены из Git.
- Во временной SQLite выполнены реальные `ImportService.preview/commit`, повтор импорта и миграций:3075 изменённых записей при первом импорте,0 при повторе, revision1 сохраняется. Через `TestClient(create_app(..., ai_enabled=False))` проверены200 HTTP-профилей, каталог,HR pagination/summary,login/logout/HttpOnly/CSRF,403 для чужого профиля и HR-запросов сотрудника,preview без записи и HTTP dry_run/commit повторного кита. Временные случайные учётки и вся тестовая БД удалены вместе с принадлежащим проверке TemporaryDirectory; рабочие учётки не создавались. Ни записи, ни пароли не выводились.
- `scripts/verify-integration --backend-ref HEAD --ai-ref origin/codex/ai-recommendations`: backend3b85bab + AI f10006a, виртуальное tree30bb15b961516ba45ca2fa8f49de1beb700cec0f без конфликтов; **468 passed**,21 исходный тест,9 schema checks,Ruff и два TCP-запуска PASS. Только синтетический remote selector, без live LLM; одно известное Starlette/httpx warning.
- При начале проверки API не слушал8000 (connection refused). Выполнено `AI_ENABLED=false scripts/dev`. После запуска: health/readiness/version/docs/OpenAPI200; `/api/me` без cookie401. Readiness: dataset loaded1.0:r1, AI not_configured/none. Проверенный локальный адрес — http://127.0.0.1:8000/docs. Процесс работает в текущей сессии; после остановки/reboot нужен `scripts/dev`.

Для полного интерактивного demo в этой рабочей копии ещё нужны серверные учётки (`backend.app.cli create-account`), согласованное подключение PR#3/#4 и frontend. Здесь нет `.env` и AI-модуля, AI отключён; реальные профили не отправлялись провайдеру. Нет Docker CLI; переданный ARM64-отчёт Олега ниже не является новым запуском на этой машине. PR не сливались, публичный deploy не выполнялся.

Ниже сохранены предыдущие проверки.

---

# Русские объяснения и принятие Docker patch — 2026-09-23

Внешний `explanation.text` больше не склеивает английские JSON-facts. Backend строит русский текст из того же snapshot, сохраняет evidence_ids и внутренний AI-контекст. Уровни не округляются скрыто; history/candidate другого события отклоняются. Новые карточки сохраняют исходный текст при restart/stale; старые нужно запросить заново.

- `make check`: **295 passed, 1 optional AI skip**, 21 исходный тест, 9 schema checks, Ruff PASS. Новые проверки: 12 explanation cases (точность, scopes/provenance, чужие evidence, restart/stale, параллельные запросы), 14 smoke evidence cases.
- `.venv/bin/ruff format --check backend scripts/smoke.py scripts/check_contracts.py scripts/verify_integration.py scripts/check_container.py`: **36 файлов PASS**; `git diff --check` PASS. Промежуточная совместная копия AI29791f1 с изменениями объяснений:453 passed; это не финальный committed snapshot.
- Принят [patch Олега](https://github.com/BAITC-Hacks/hack-d59a47be-67/blob/f10006a/backend/app/ai/verification/container-fixes.patch): исключение лишних AI-файлов из образа, semantic evidence проверки smoke вместо exact4. Последнее сравнение текста адаптировано к новому русскому formatter; возвращать машинные facts в HTTP не требуется.
- [Docker-отчёт Олега](https://github.com/BAITC-Hacks/hack-d59a47be-67/blob/f10006a/backend/app/ai/DOCKER_REPORT.md) подтверждает ARM64 container acceptance, live OpenAI3/3 и frontend/completion/restart на backend a3696ba + AI29791f1 **с patch во временной копии**. Это переданный результат, не наш повторный запуск новой версии. Docker CLI/Desktop/daemon в текущей среде повторно не обнаружены; AMD64, новая container build и публичный deploy не заявляются.

Схемы/OpenAPI, AI-код/тесты Олега и frontend не менялись. CI billing-blocked. Финальный `scripts/verify-integration --backend-ref HEAD --ai-ref origin/codex/ai-recommendations` завершился exit0: backend `1723fb21d008db31e87769d999068d7dbef2204f`, AI `f10006a13c056ae49983c6e8200711cea73afa6a`, виртуальное tree `1e1acb99690b578f39004923152720fa81ed8c3c` без конфликтов. **468 passed**, 21 исходный тест, 9 schema checks, Ruff и два TCP-запуска PASS. Настоящий AI-модуль использовал только синтетический selector; проверены русский текст/evidence, сохранённые карточки, restart, session, completion/idempotency replay после потери ответа и logout. После прерывания команду повторили до полученного полного результата; live-модель не вызывали. Одно известное upstream Starlette/httpx предупреждение.

`scripts/check-container --require-ai` локально завершился exit2 / NOT RUN из-за отсутствия Docker. Изменения передаются через [PR #4](https://github.com/BAITC-Hacks/hack-d59a47be-67/pull/4), без merge. Для текущих refs остаётся контейнерный повтор на подходящем хосте.

Ниже сохранены предыдущие проверки.

---

# Воспроизводимая интеграция и контейнерная приёмка — 2026-09-23

Проверено командой `scripts/verify-integration --backend-ref HEAD --ai-ref origin/codex/ai-recommendations` на backend `960187ef069b552130f5cc51612d3193cfa23a87` и AI `8f0dc053b769a9b965fc648b9b67d3cd01a14981`. Виртуальное объединение без конфликтов: tree `c4fcd058af2986d6b8d2019180bf5d59edeb9b87`. Проверка экспортировала только tracked tree во временную папку, не переключала рабочую ветку и не сливала PR.

- Совместный snapshot: **438 passed**,21 исходный тест,9 contract checks,Ruff PASS; TCP smoke дважды запускает настоящий API/AI-модуль и проверяет cards/evidence → completion → restart → точный idempotency replay/latest stale → logout. Подменён только remote selector; реальных ключей, LLM-вызовов и отправки профилей не было.
- Backend отдельно: `make check` — **269 passed,1 optional AI skip**,21 исходный тест,9 checks,Ruff PASS; формат33 файлов проверен.25 новых тестов проверяют изоляцию окружения, loopback port, image allowlist и unavailable Docker path.
- `scripts/check-container --require-ai` — **exit2 / NOT RUN**, Docker CLI/Desktop/daemon отсутствуют. Это НЕ пройденный container build. Скрипт подготовлен для реальной сборки/проверки non-root/содержимого образа/auth/SQLite и пересоздания контейнера с тем же named volume на машине с Docker; эти действия здесь не выполнялись.
- `git diff --check` и protected-path diff PASS: frontend/, backend/app/ai/, Pydantic/HTTP схемы не менялись. Публичного деплоя и merge PR нет. CI всё ещё billing-blocked.

Команды повторения и границы проверки описаны в docs/HANDOFF.md. На Docker-хосте: `scripts/verify-integration --ai-ref origin/codex/ai-recommendations --container`. Команда использует отдельный случайный Compose project и удаляет только созданные ею тестовые ресурсы; очистка существующих ресурсов/установка Docker не выполняются.

Ниже сохранены предыдущие проверки.

---

# Backend-передача AI PR #3 — 2026-09-23

Ветка `codex/backend-ai-handoff` от `origin/codex/architecture-foundation` (`e3dfc7e`, уже содержит опубликованный ранее PR #2). AI PR #3 (`8f0dc05`) проверен в отдельной detached working copy `/private/tmp/career-quest-pr3-integration`; поверх скопированы только изменения backend/инфраструктуры и новые тесты. AI-код и собственные тесты Олега не менялись, PR не сливались.

Опубликован [PR #4](https://github.com/BAITC-Hacks/hack-d59a47be-67/pull/4). `git merge-tree --write-tree HEAD origin/codex/ai-recommendations` завершился успешно без конфликтов файлов, включая журнал AGENTS; проверка не меняла refs и не выполняла merge PR.

- `make check` в backend-ветке: **244 passed, 1 skipped**, 21 исходный unit test, 9 проверок контрактов, Ruff PASS. Skip — только сквозной тест optional AI, которого в этой ветке пока нет.
- `make check VENV_PYTHON=<absolute path to project .venv/bin/python>` в изолированной копии PR #3 с backend-дополнениями: **413 passed**, 21 исходный unit test, 9 проверок контрактов, Ruff PASS. Сетевой selection подменён; ни ключ, ни OpenAI для этих тестов не нужны.
- `.venv/bin/ruff format --check backend scripts/smoke.py scripts/check_contracts.py`:29 files formatted; `git diff --check`:PASS. Одно известное upstream warning Starlette/httpx.
- Чистый `/private/tmp/career-quest-ai-runtime-venv`: установка **только requirements.lock**, `pip check` без ошибок; импорт настоящего async AI PR #3, capability configured при enabled и not_configured при disabled. Провайдер не вызывался; dev-зависимостей нет.
- Локальный read-only аудит контекстов исходного кита в временной БД:200 профилей,159 actionable контекстов проходят `backend.app.ai.validation.validate_context`; максимальный JSON контекста21089 bytes. Исходные записи и prepared contexts никуда не отправлялись и не печатались.

Новые проверки доказывают выбор critical_skills из целевой роли/грейда (явной или следующей), отсутствие выдуманной критичности, сохранение всех ID большого списка, отдельные history-выборки события и других событий с теми же type+format, counts/даты/provenance/повторы/cutoff/нулевой sample, отсутствие личных идентификаторов. HTTP-тест использует профиль, где самый низкий навык некритичен, а меньший разрыв критичен; настоящий AI-модуль получает этот backend-контекст, затем проверяются resolved evidence, полные карточки, latest после restart и чужой доступ403. Подменённый selector доказывает совместимость и передачу фактов, **не качество live-ранжирования**.

Минимальные общие fixtures сохранены: их структура используется тестами Олега. Расширенные примеры добавлены отдельно и проверяются теми же Pydantic-моделями. `backend/app/contracts.py`, OpenAPI, JSON Schema, frontend/ и backend/app/ai/ не менялись.

Ограничения: Docker отсутствует, поэтому подготовленные allowlist/Compose/runtime pins не выдаются за проверенный container build. AI выключен по умолчанию, ключ задаётся оператором в runtime и не входит в образ/Git. Реальных LLM-вызовов на этой машине не было; сообщённые Олегом3/3 относятся к его прежним синтетическим fixtures. GitHub CI по-прежнему billing-blocked. Для рабочего совместного запуска после review нужны изменения и PR #3, и этой backend-ветки.

Ниже сохранена приёмка предыдущего backend-этапа.

---

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
