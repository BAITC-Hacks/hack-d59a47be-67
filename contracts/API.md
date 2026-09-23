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

## Предметные endpoints

Точечное расширение v1 согласовано владельцем задачи с потребителями: `docs/CONTRACT_CHANGE_PROPOSAL.md`. Все маршруты защищены сервером; employee видит только себя, HR определяется по серверной сессии. Фактические команды и результаты локальной приёмки фиксируются в `docs/VERIFICATION.md`. Отсутствие загруженного набора отражается ошибкой 503 и отдельной dataset capability.

| Метод / путь | Доступ | Вход | Успешный ответ |
| --- | --- | --- | --- |
| `GET /api/catalog` | Employee и HR | — | `CatalogResponse` |
| `GET /api/employees` | Только HR | `limit` 1–100 (по умолчанию 20), `offset` ≥0 (по умолчанию 0) | `EmployeeListResponse` |
| `GET /api/employees/{id}` | Свой профиль или HR | Строковый employee id | `EmployeeDetailResponse` |
| `PATCH /api/employees/{id}/goal` | Свой профиль или HR | `GoalUpdateRequest` | `EmployeeDetailResponse` |
| `POST /api/employees/{id}/recommendations` | Свой профиль или HR | `RecommendationRequest` | `RecommendationResponse`, до 3 рекомендаций |
| `GET /api/employees/{id}/recommendations/latest` | Свой профиль или HR | — | Последний полный `RecommendationResponse`; 404, если результата ещё нет |
| `POST /api/employees/{id}/preview` | Свой профиль или HR | `PreviewRequest` | `PreviewResponse`, `persisted=false` |
| `POST /api/employees/{id}/completions` | Свой профиль или HR | `CompletionRequest`, `Idempotency-Key` | `CompletionResponse` |
| `GET /api/hr/summary` | Только HR | — | `HRSummaryResponse` |
| `POST /api/hr/import` | Только HR | `ImportRequest` или `BatchImportRequest` | `ImportResponse` |

Все POST/PATCH требуют Origin; после login также CSRF. Даже вычислительные POST используют единые правила защиты. Preview — дополнительная функция команды, не требование организаторов. Он не изменяет навыки, историю или версии. `CompletionRequest.mode` обязателен: `completion` или явно выбранный `demo_simulation`; симуляция отображается отдельно в истории и HR-агрегатах.

`state_version` / `expected_state_version` — общая монотонная revision демо (целое ≥0), `data_version` — версия кита вместе с revision. Цель, завершение и импорт увеличивают revision; прежние рекомендации становятся `stale=true`. Записывающие действия принимают `expected_state_version`; устаревшая версия даёт 409. Вычисления навыков, прогресса и запись выполняет backend. Уровни навыков **0–5**, как в README данных. Цель имеет ровно `{target_role, target_grade}`; grade — `Junior`, `Middle`, `Senior`, `Lead`.

`EmployeeDetailResponse` возвращает `scenario_date`, `target_status=explicit|next_grade|no_target` и nullable `progress={percent,met_skills,total_skills,critical_met,critical_total}`. Явная `career_goal` приоритетна; без неё используется следующий грейд текущей роли. Lead без цели получает `goal=null`, `target_status=no_target`, `progress=null`. История содержит все исходные статусы, а не только завершения: `record_id,event_id,status,activity_date,date_source,completed_at,mode,effects`. `date_source=historical_proxy` сохраняет исходную приблизительную дату; `completed_at=null`. Новое действие использует `date_source=completed_at` и серверное timezone-aware время demo: дата метаданных `2026-10-01` плюс текущее время суток Asia/Almaty (+05:00 на дату demo). Реальное время принятия хранится отдельно для аудита как `recorded_at`; клиент дату завершения не задаёт. `mode=import|completion|demo_simulation` показывает происхождение действия.

`POST .../completions` обязательно получает `Idempotency-Key`. После проверки прав сервер возвращает ранее сохранённый ответ при повторе ключа с тем же телом **до** проверки устаревшей revision. Тот же ключ с другим телом даёт 409. Необязательный `CompletionRequest.record_id` выбирает конкретное незавершённое назначение, включая повторные mandatory. Запись истории, revision, навыков и идемпотентного ответа происходит транзакционно.

`RecommendationResponse` дополняется `recommendation_id` и `stale`. `no_target/none` означает отсутствие цели, `no_candidates/none` — отсутствие полезных допустимых кандидатов по вычислению backend; это отличается от отключённого AI `not_configured/none`. `ok/oleg` содержит до 3 проверенных полных карточек с эффектами и объяснениями. `unavailable/oleg` означает недоступный или отклонённый результат подключённого модуля. Последний ответ сохраняется полностью; `/recommendations/latest` возвращает объяснения и после перезагрузки. Сохранённая версия остаётся прежней, а `stale=true` показывает изменение revision. Транзакция БД не остаётся открытой во время AI-вызова; перед сохранением проверяется revision.

Каталог содержит `events` с исходными полями событий, включая `develops_skills=[{skill_id,gain,max_level}]`, `prerequisites={skill_id:level}` и `upcoming_sessions=[date]`. Это метаданные для отображения; допуск и эффекты backend повторно проверяет при preview/completion.

## Сессии, Origin и ошибки

Учётные записи, role и employee_id назначает сервер. В `LoginRequest` допустимы только `username` и `password`; публичной регистрации нет. Клиент использует cookie `cq_session` с `credentials: "include"`. Токен CSRF берётся из ответа login `SessionResponse.csrf_token` и передаётся в `X-CSRF-Token`; cookie — HttpOnly, срок ограничен; HTTPS-staging использует Secure. Backend хранит только hash CSRF. Для сохранения CSRF после перезагрузки страницы frontend может использовать sessionStorage; иначе нужен повторный login. `/api/me` возвращает только UserIdentity и не восстанавливает CSRF. Команды создания учётных записей находятся в `docs/HANDOFF.md`.

Origin должен совпадать с явно разрешённым origin целиком (scheme, host, port). Клиент не передаёт роль или идентификатор владельца сессии. Связь сотрудника определяется сохранённой учётной записью. Чужой employee id не даёт сотруднику доступ даже при корректном теле запроса.

Единая ошибка на верхнем уровне:

```json
{"code":"FORBIDDEN","message":"Access denied.","request_id":"synthetic-request-001","details":{}}
```

| HTTP | Значение |
| --- | --- |
| 401 | Нет/истекла сессия или неверные credentials |
| 403 | Недостаточно прав, неверный Origin или CSRF |
| 404 | Неизвестный маршрут или отсутствующий профиль в разрешённой области доступа; чужой профиль сотруднику возвращает 403 до проверки существования |
| 409 | Устаревшая revision, конфликт идемпотентного ключа, preview token или импортной записи |
| 422 | Невалидные параметры или тело; без исходных значений и секретов в details |
| 429 | Превышен лимит попыток login |
| 503 | БД/миграции недоступны или dataset не загружен |

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

Транспорт одиночного файла `ImportRequest` — `{dry_run, source_filename, source_format, content, preview_token?}`. Это HTTP-обёртка команды, не новая версия исходного датасета. `source_filename` принимает только четыре имени из таблицы, `source_format` — `json` или `csv` в соответствии с расширением, `content` — исходный текст. Batch имеет `{dry_run, files:[{source_filename,source_format,content}], preview_token?}`; от 1 до 4 файлов с уникальными именами. Новые профили `{meta,employees}` и их CSV-история валидируются совместно в одной партии.

Сначала `dry_run=true` (значение по умолчанию): проверка всей партии и её связей, затем `ImportResponse.status=validated`, `preview_token`, текущая `revision`, `counts` и `imported_records`. Preview не записывает предметные данные. Для commit передаются те же файлы, `dry_run=false` и выданный `preview_token`; token связан с содержимым партии и revision. Ответ `status=imported` возвращается после атомарной записи. Ошибка любой строки отклоняет всю партию; revision и данные остаются прежними. `record_id` — ключ истории: совпадающее содержимое повторного record_id не дублируется, изменённое конфликтует. Разные record_id не удаляются по совпадению employee/event/date. Повторные mandatory назначения сохраняются. Изменённый существующий employee_id также конфликтует. skills.json/events.json заменяют соответствующий каталог целиком после проверки ссылок всех сохранённых профилей и истории. При замене каталогов preview возвращает предупреждение в существующем поле warnings: профили пересчитываются по baseline/истории и новому каталогу, revision меняется, рекомендации становятся stale.

Правила backend: `employees.skills` — оценка на `last_review_date`; начисляются только `completed` с датой строго после оценки и не позднее `scenario_date`. Неизвестный `skill_id` — ошибка, отсутствующий известный навык — 0. Рост ограничен `gain`, `max_level` и шкалой 0–5; уже высокий уровень не снижается. Mandatory исключены из рекомендаций. Допуск использует текущую роль/грейд, prerequisites, расписание и историю; желаемая роль не расширяет аудиторию. Добровольное завершённое событие не повторяется, кроме регулярного `EV_036`. Пустое множество полезных кандидатов не заменяется выдуманными мероприятиями. AI получает уже допустимых кандидатов и готовые эффекты.

## Примеры

Все JSON в `contracts/examples/` — явно синтетические тестовые fixtures команды. `backend/tests/test_contracts.py` проверяет их по моделям этого контракта, отрицательные случаи и связи AI-примера. Примеры описывают HTTP payloads; отдельные тесты приложения проверяют поведение endpoints. Они не копируют исходные или данные жюри. AI-интерфейс и статусы описаны в `AI_CONTRACT.md`.
