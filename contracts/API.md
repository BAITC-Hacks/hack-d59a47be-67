# Career Quest API — контракт v1

Владелец backend, данных, API, доступа, интеграции и деплоя — Алихан. Олег владеет `backend/app/ai/` и собственными тестами; Батыр — `frontend/`. Единственный источник схем — `backend/app/contracts.py`, экспорт OpenAPI — `contracts/openapi.json`. Параллельные копии моделей внутри AI или frontend не поддерживаются. API version — `1.0.0`, URL prefix — `/api`.

## Что работает сейчас

| Метод / путь | Доступ | Ответ | Состояние |
| --- | --- | --- | --- |
| `GET /api/health` | Публичный | `HealthResponse` | Implemented: живой процесс, без проверки БД |
| `GET /api/health/ready` | Публичный | 200 `ReadinessResponse`; 503 `ErrorResponse` | Implemented: БД и версия миграций |
| `GET /api/version` | Публичный | `VersionResponse` | Implemented: `api_version`, `commit_sha`; `unknown` без SHA |
| `POST /api/auth/login` | Публичный, разрешённый Origin | `LoginRequest` → `SessionResponse` | Implemented: demo-сессия, ограничение попыток |
| `POST /api/auth/logout` | Сессия, Origin + CSRF | `LogoutResponse` | Implemented: отзыв сессии |
| `GET /api/me` | Сессия | `UserIdentity` | Implemented: identity текущей сессии |

`capabilities` показывают независимую готовность компонентов:

```json
{"ai":{"status":"not_configured","engine":"none"},"dataset":{"status":"not_loaded","version":null}}
```

Доступный файл на диске не означает загруженный датасет. Живой backend и готовая БД не означают работающее AI. AI `configured/oleg` допустим только при включённом и доступном модуле; dataset `loaded` требует версии. Отсутствие AI не делает инфраструктурную readiness отрицательной. При недоступной БД/миграциях readiness возвращает 503 `ErrorResponse` с code `NOT_READY`, `details.database="unavailable"` и `details.capabilities`.

## Предметные endpoints — planned

Следующие маршруты зарегистрированы и защищены, но бизнес-логика ещё не реализована. После проверки сессии, прав и входной схемы возвращается **501** `NOT_IMPLEMENTED`, а не пустой успешный ответ. Указанные модели — целевой контракт; описание OpenAPI отличает planned от implemented. При отсутствии сессии/прав ответ 401/403 предшествует 501.

| Метод / путь | Доступ | Вход | Будущий успешный ответ |
| --- | --- | --- | --- |
| `GET /api/catalog` | Employee и HR | — | `CatalogResponse` |
| `GET /api/employees` | Только HR | `limit` 1–100 (по умолчанию 20), `offset` ≥0 (по умолчанию 0) | `EmployeeListResponse` |
| `GET /api/employees/{id}` | Свой профиль или HR | Строковый employee id | `EmployeeDetailResponse` |
| `PATCH /api/employees/{id}/goal` | Свой профиль или HR | `GoalUpdateRequest` | `EmployeeDetailResponse` |
| `POST /api/employees/{id}/recommendations` | Свой профиль или HR | `RecommendationRequest` | `RecommendationResponse`, до 3 рекомендаций |
| `POST /api/employees/{id}/preview` | Свой профиль или HR | `PreviewRequest` | `PreviewResponse`, `persisted=false` |
| `POST /api/employees/{id}/completions` | Свой профиль или HR | `CompletionRequest` | `CompletionResponse` |
| `GET /api/hr/summary` | Только HR | — | `HRSummaryResponse` |
| `POST /api/hr/import` | Только HR | `ImportRequest` | `ImportResponse` |

Все POST/PATCH требуют Origin; после login также CSRF. Даже вычислительные POST используют единые правила защиты. Preview — дополнительная функция команды, не требование организаторов. Он не изменяет навыки, историю или версии. `CompletionRequest.mode` обязателен: `completion` или явно выбранный `demo_simulation`; симуляция отображается отдельно в истории и HR-агрегатах.

`state_version` — версия состояния сотрудника (целое ≥0), `data_version` — версия загруженных данных. Записывающие действия принимают `expected_state_version`; устаревшая версия в будущем даёт 409. Вычисления навыков и запись выполняет backend. Уровни навыков **0–5**, как в README данных. Цель имеет ровно `{target_role, target_grade}`; grade — `Junior`, `Middle`, `Senior`, `Lead`.

## Сессии, Origin и ошибки

Учётные записи, role и employee_id назначает сервер. В `LoginRequest` допустимы только `username` и `password`; публичной регистрации нет. Клиент использует cookie `cq_session` с `credentials: "include"`. Токен CSRF берётся из ответа login `SessionResponse.csrf_token` и передаётся в `X-CSRF-Token`; cookie — HttpOnly, срок ограничен; HTTPS-staging использует Secure. Backend хранит только hash CSRF. Для сохранения CSRF после перезагрузки страницы frontend может использовать sessionStorage; иначе нужен повторный login. `/api/me` возвращает только UserIdentity и не восстанавливает CSRF. Команды создания учётных записей находятся в `docs/HANDOFF.md`.

Origin должен совпадать с явно разрешённым origin целиком (scheme, host, port). Клиент не передаёт роль или идентификатор владельца сессии. Связь сотрудника определяется сохранённой учётной записью. Чужой employee id не даёт сотруднику доступ даже при корректном теле запроса.

Единая ошибка на верхнем уровне:

```json
{"code":"NOT_IMPLEMENTED","message":"This domain endpoint is planned for a later step.","request_id":"synthetic-request-001","details":{"capabilities":{"ai":{"status":"not_configured","engine":"none"},"dataset":{"status":"not_loaded","version":null}}}}
```

| HTTP | Значение |
| --- | --- |
| 401 | Нет/истекла сессия или неверные credentials |
| 403 | Недостаточно прав, неверный Origin или CSRF |
| 404 | Неизвестный маршрут или отсутствующий профиль в разрешённой области доступа; чужой профиль сотруднику возвращает 403 до проверки существования |
| 409 | Конфликт состояния, например устаревшая версия (предметная логика planned) |
| 422 | Невалидные параметры или тело; без исходных значений и секретов в details |
| 429 | Превышен лимит попыток login |
| 501 | Предметный маршрут ещё не реализован |
| 503 | БД/миграции недоступны; будущая зависимость временно недоступна |

`details` содержит только безопасные метаданные. Пароли, cookie, CSRF, профили и исходный импорт не включаются в ошибки или логи. Непредвиденная ошибка — 500 с общим сообщением и request_id.

## Исходные файлы и импорт

Проверены README стартовых данных и структура локальных файлов. Они не копируются в репозиторий/образ. README называет стартовые данные синтетическими; наши маленькие fixtures созданы командой отдельно и не выдаются за данные организаторов. Дата сценария исходного набора — **2026-10-01**, история — 2024-10-01…2026-09-30.

| Точное имя | Проверенная JSON-обёртка / CSV-заголовок |
| --- | --- |
| `skills.json` | Объект `meta`, `proficiency_scale`, `skills`, `role_profiles` |
| `employees.json` | Объект `meta`, `employees` |
| `events.json` | Объект `meta`, `events` |
| `activity_history.csv` | `record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by` |

У каждого JSON `meta` содержит `dataset`, `version`, `as_of_date`. Каталог навыков использует `skill_id,name,type,category,description`; профиль роли — `role,grade,required_skills,critical_skills`. Профили сотрудников: `employee_id,full_name,department,role,grade,manager_id,hire_date,tenure_months,work_format,preferred_language,career_goal,skills,last_review_date`. Мероприятия: `event_id,title,description,type,format,duration_hours,mandatory,target_roles,target_grades,develops_skills,prerequisites,upcoming_sessions`.

Наш транспорт `ImportRequest` — `{dry_run, source_filename, source_format, content}`. Это HTTP-обёртка команды, не новая версия исходного датасета. `source_filename` принимает только четыре имени из таблицы, `source_format` — `json` или `csv` в соответствии с расширением, `content` — непрозрачный исходный текст; `dry_run` по умолчанию true. Внутреннее содержимое сейчас не разбирается и не записывается: endpoint возвращает 501 для обоих значений dry_run. Совместимость импорта ещё **не проверена**. Будущий импорт проверяет весь набор связей и выполняется атомарно; порядок частичных загрузок и лимиты согласуются до реализации.

Правила будущего backend: отсутствующий навык — 0; учесть только завершения после `last_review_date`, чтобы не начислять эффект повторно; `gain` ограничивается `max_level`; исключить mandatory из рекомендаций; учитывать prerequisites, роли/грейды и даты; после completed не повторять событие, кроме `EV_036`. AI получает уже допустимых кандидатов и готовые эффекты.

## Примеры

Все JSON в `contracts/examples/` — явно синтетические тестовые fixtures команды. `backend/tests/test_contracts.py` проверяет их по моделям этого контракта, отрицательные случаи и связи AI-примера. Они описывают будущие payloads и не означают, что planned endpoints вернули успех. AI-интерфейс и статусы описаны в `AI_CONTRACT.md`.
